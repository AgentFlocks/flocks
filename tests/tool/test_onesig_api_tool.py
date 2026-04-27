"""Regression tests for the OneSIG handler.

Two thematically distinct surfaces are covered here:

1. **SSL verify resolution** (PR #193 follow-up). Confirms that the WebUI's
   ``custom_settings.verify_ssl`` toggle, the ``ssl_verify`` snake-case alias,
   and the ``verifySsl`` legacy camelCase alias all reach
   ``aiohttp.session.request(..., ssl=...)`` with the right shape.

2. **Cookie persistence to ``.secret.json``**. OneSIG sessions are cookie-
   based, so the handler now serialises the jar after every successful
   login under ``onesig_session_cookie__<sha1[:12]>`` and re-hydrates it on
   construction. The tests cover the helper purity (snapshot round-trip,
   expired filtering), the precedence of the ``persist_cookies`` toggle,
   and the integration points: ``__init__`` loads + trusts, ``login()``
   saves, ``logout()`` deletes, and the persisted-cookie path skips the
   captcha → pubkey → /v3/login → /v3/account chain entirely.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import types
from email.utils import formatdate
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------
# The OneSIG handler lives outside the ``flocks`` package, so we load it
# directly via importlib (the same trick the YAML tool loader uses).
_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "tools"
    / "api"
    / "onesig"
    / "onesig.handler.py"
)


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "onesig_handler_under_test", _HANDLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def handler():
    return _load_handler()


# ---------------------------------------------------------------------------
# _resolve_verify_ssl: precedence
# ---------------------------------------------------------------------------
# Each row is `(env, raw_dict, expected)`. The env key is unset for rows that
# do not set it explicitly so we don't leak across tests.
@pytest.mark.parametrize(
    "raw, env, expected, why",
    [
        # 1. canonical key wins over everything below it
        ({"verify_ssl": False, "ssl_verify": True, "custom_settings": {"verify_ssl": True}},
         None, False, "verify_ssl=False overrides ssl_verify=True and custom_settings"),
        # 2. ssl_verify is honoured when verify_ssl missing (PR #193 alias)
        ({"ssl_verify": False}, None, False, "ssl_verify alias respected"),
        # 3. legacy camelCase verifySsl still works after canonical/ssl_verify both missing
        ({"verifySsl": False}, None, False, "verifySsl legacy alias respected"),
        # 4. WebUI's custom_settings.verify_ssl drives the default UI switch
        ({"custom_settings": {"verify_ssl": False}}, None, False,
         "custom_settings.verify_ssl honoured (UI toggle path)"),
        # 5. env var fallback for CLI / containerised deployments
        ({}, "false", False, "ONESIG_VERIFY_SSL env var honoured"),
        # 6. nothing set → default False (parity with onesec/ngtip/qingteng).
        # OneSIG is overwhelmingly deployed as a private gateway with self-
        # signed certs, so the open-box default is to *not* validate. Users
        # opt in to strict validation by toggling the UI switch.
        ({}, None, False, "default DEFAULT_VERIFY_SSL=False when unset"),
        # 7. string coercion through _coerce_bool
        ({"verify_ssl": "off"}, None, False, "off → False"),
        ({"verify_ssl": "1"}, None, True, "'1' → True"),
        ({"verify_ssl": 0}, None, False, "numeric 0 → False"),
        # 8. precedence regression: custom_settings ignored once ssl_verify present
        ({"ssl_verify": True, "custom_settings": {"verify_ssl": False}},
         None, True, "ssl_verify (closer to canonical) wins over custom_settings"),
    ],
)
def test_resolve_verify_ssl_precedence(handler, raw, env, expected, why, monkeypatch):
    if env is None:
        monkeypatch.delenv("ONESIG_VERIFY_SSL", raising=False)
    else:
        monkeypatch.setenv("ONESIG_VERIFY_SSL", env)
    assert handler._resolve_verify_ssl(raw) is expected, why


def test_default_verify_ssl_is_off_for_private_deployments(handler):
    # OneSIG defaults to *not* validating certificates so private-deployment
    # users (the overwhelmingly common case) work out of the box. This is the
    # same default onesec / ngtip / qingteng adopted in PR #193. Flipping the
    # constant back to True would silently break every self-signed deployment,
    # so guard it explicitly.
    assert handler.DEFAULT_VERIFY_SSL is False


# ---------------------------------------------------------------------------
# _ssl_context: bool -> aiohttp ssl arg shape
# ---------------------------------------------------------------------------
def test_ssl_context_returns_none_when_verify_enabled(handler):
    # When verification is on, returning None lets aiohttp use its default
    # certifi-backed context (i.e. real validation).
    assert handler._ssl_context(True) is None


def test_ssl_context_disables_validation_when_disabled(handler):
    import ssl as _ssl

    ctx = handler._ssl_context(False)
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == _ssl.CERT_NONE


# ---------------------------------------------------------------------------
# End-to-end: WebUI toggle (custom_settings.verify_ssl=False) propagates all
# the way to aiohttp.session.request(..., ssl=<SSLContext with CERT_NONE>).
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, *, status: int = 200, json_payload: Any = None,
                 text_payload: str = "", content_type: str = "application/json"):
        self.status = status
        self._json_payload = json_payload
        self._text_payload = text_payload
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):  # noqa: ARG002 - signature parity
        return self._json_payload

    async def text(self):
        return self._text_payload

    async def read(self):
        return self._text_payload.encode("utf-8") if self._text_payload else b""


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession`` with a scripted response queue."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)

    async def close(self):
        self.closed = True


def _captcha_then_pubkey_then_login_then_business():
    return [
        # GET /v3/captcha
        _FakeResponse(json_payload={
            "responseCode": 0,
            "verboseMsg": "成功",
            "data": {"enableCaptcha": False, "enableTotp": False},
        }),
        # GET /v3/pubkey
        _FakeResponse(json_payload={
            "responseCode": 0,
            "verboseMsg": "成功",
            "data": {"pubkey": "FAKE-PUBKEY-PEM"},
        }),
        # POST /v3/login
        _FakeResponse(json_payload={"responseCode": 0, "verboseMsg": "成功"}),
        # GET /v3/account (post-login probe)
        _FakeResponse(json_payload={
            "responseCode": 0,
            "verboseMsg": "成功",
            "data": {"username": "admin"},
        }),
        # business request (basic_version → GET /v3/basic/version)
        _FakeResponse(json_payload={
            "responseCode": 0,
            "verboseMsg": "成功",
            "data": {"version": "v2.5.3"},
        }),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_config, expect_ssl_validation",
    [
        # WebUI "SSL verify" switch OFF → custom_settings.verify_ssl=False.
        # Before this fix the OneSIG handler ignored that field entirely and
        # kept verify_ssl at its default (True), so private-deployment users
        # who toggled the UI saw no behaviour change. The fix makes the
        # toggle drive aiohttp's ssl= argument.
        ({"custom_settings": {"verify_ssl": False}}, False),
        # Canonical verify_ssl still wins.
        ({"verify_ssl": True, "custom_settings": {"verify_ssl": False}}, True),
        # ssl_verify alias also honoured (parity with PR #193).
        ({"ssl_verify": False}, False),
    ],
    ids=["custom_settings_off", "canonical_overrides_ui", "ssl_verify_alias_off"],
)
async def test_onesig_request_honours_verify_ssl_from_config(
    handler, service_config, expect_ssl_validation
):
    fake_session = _FakeSession(_captcha_then_pubkey_then_login_then_business())

    raw_service: dict[str, Any] = {
        "base_url": f"https://onesig-{id(service_config)}.example.local",
        "username": "admin",
        "password": "{secret:onesig_password}",
        "oaep_hash": "sha1",
    }
    raw_service.update(service_config)

    secret_manager = MagicMock()
    secret_manager.get.side_effect = lambda key: {
        "onesig_password": "supersecret",
    }.get(key)

    fake_flocks_security = types.ModuleType("flocks.security")
    fake_flocks_security.get_secret_manager = lambda: secret_manager
    sys.modules["flocks.security"] = fake_flocks_security

    with (
        patch.object(
            handler.ConfigWriter, "get_api_service_raw", return_value=raw_service
        ),
        patch.object(
            handler, "_rsa_oaep_encrypt", return_value="ENCRYPTED-PASSWORD"
        ),
        patch.object(
            handler.aiohttp, "ClientSession", return_value=fake_session
        ),
    ):
        # Flush the per-base-url session pool so each parametrised case gets a
        # fresh OneSIGSession instance bound to the parametrised config.
        handler._SESSIONS.clear()

        config = handler._resolve_runtime_config()
        session = handler.OneSIGSession(config)
        status, envelope, _body, _ct = await session.request(
            "GET", "/v3/basic/version"
        )

    # All five recorded requests (4 auth + 1 business) must use the resolved
    # ssl context. We assert on the business request (last call) which is the
    # one users actually care about.
    assert envelope.get("responseCode") == 0
    assert status == 200
    assert len(fake_session.calls) == 5

    business_method, business_url, business_kwargs = fake_session.calls[-1]
    assert business_method == "GET"
    assert business_url.endswith("/v3/basic/version")

    ssl_arg = business_kwargs["ssl"]
    if expect_ssl_validation:
        # When validation is on, the handler passes ``ssl=None`` so aiohttp
        # falls back to its default certifi context.
        assert ssl_arg is None
    else:
        # When validation is off, the handler passes a permissive SSLContext
        # so private deployments with self-signed certs work.
        import ssl as _ssl
        assert isinstance(ssl_arg, _ssl.SSLContext)
        assert ssl_arg.check_hostname is False
        assert ssl_arg.verify_mode == _ssl.CERT_NONE

    # All other requests in the auth chain should use the same ssl arg.
    for _method, _url, kwargs in fake_session.calls:
        if expect_ssl_validation:
            assert kwargs["ssl"] is None
        else:
            assert kwargs["ssl"] is not None


# ===========================================================================
# Cookie persistence: snapshot helpers (pure functions)
# ===========================================================================
def _future_rfc1123(seconds_from_now: int = 3600) -> str:
    """Return an RFC 1123 string ``seconds_from_now`` seconds in the future."""
    return formatdate(time.time() + seconds_from_now, usegmt=True)


def _past_rfc1123(seconds_ago: int = 3600) -> str:
    return formatdate(time.time() - seconds_ago, usegmt=True)


def test_cookie_secret_id_is_stable_and_unique(handler):
    a1 = handler._cookie_secret_id("https://1.2.3.4", "admin")
    a2 = handler._cookie_secret_id("https://1.2.3.4", "admin")
    b = handler._cookie_secret_id("https://1.2.3.4", "audit")
    c = handler._cookie_secret_id("https://1.2.3.5", "admin")

    assert a1 == a2, "stable across calls (same input → same secret_id)"
    assert a1 != b, "different username → different secret_id"
    assert a1 != c, "different base_url → different secret_id"
    # Filesystem-/JSON-safe characters only (avoids leaking IP / port / scheme).
    assert re.fullmatch(r"onesig_session_cookie__[0-9a-f]{12}", a1)


@pytest.mark.asyncio
async def test_cookies_to_snapshot_round_trip(handler):
    # Seed a real CookieJar with two cookies so we exercise the actual
    # aiohttp ↔ http.cookies.Morsel pathway (not a hand-rolled fake).
    # ``aiohttp.CookieJar.__init__`` calls ``asyncio.get_running_loop()``
    # so this needs to run inside an event loop.
    jar = aiohttp.CookieJar(unsafe=True)
    from http.cookies import SimpleCookie
    from yarl import URL

    sc: SimpleCookie = SimpleCookie()
    sc["onesig_session"] = "abc123"
    sc["onesig_session"]["domain"] = "1.2.3.4"
    sc["onesig_session"]["path"] = "/"
    sc["onesig_session"]["expires"] = _future_rfc1123(3600)
    sc["onesig_session"]["httponly"] = True
    sc["onesig_session"]["secure"] = True
    sc["lang"] = "zh"
    sc["lang"]["domain"] = "1.2.3.4"
    sc["lang"]["path"] = "/"
    jar.update_cookies(sc, response_url=URL("https://1.2.3.4/api"))

    rows = handler._cookies_to_snapshot(jar)
    assert {r["name"] for r in rows} == {"onesig_session", "lang"}

    session_row = next(r for r in rows if r["name"] == "onesig_session")
    assert session_row["value"] == "abc123"
    assert session_row["domain"] == "1.2.3.4"
    assert session_row["path"] == "/"
    assert session_row["secure"] is True
    assert session_row["httponly"] is True

    # Round-trip back into a fresh jar.
    fresh_jar = aiohttp.CookieJar(unsafe=True)
    injected = handler._snapshot_into_jar(fresh_jar, rows, "https://1.2.3.4/api")
    assert injected == 2
    rehydrated = {r["name"]: r for r in handler._cookies_to_snapshot(fresh_jar)}
    assert rehydrated["onesig_session"]["value"] == "abc123"
    assert rehydrated["onesig_session"]["secure"] is True
    assert rehydrated["lang"]["value"] == "zh"


@pytest.mark.asyncio
async def test_snapshot_into_jar_drops_already_expired_cookies(handler):
    rows = [
        {
            "name": "stale",
            "value": "x",
            "domain": "1.2.3.4",
            "path": "/",
            "expires": _past_rfc1123(60),
            "secure": False,
            "httponly": False,
        },
        {
            "name": "fresh",
            "value": "y",
            "domain": "1.2.3.4",
            "path": "/",
            "expires": _future_rfc1123(3600),
            "secure": False,
            "httponly": False,
        },
    ]
    jar = aiohttp.CookieJar(unsafe=True)
    injected = handler._snapshot_into_jar(jar, rows, "https://1.2.3.4")
    assert injected == 1
    names = {m.key for m in jar}
    assert names == {"fresh"}, "expired cookie must not poison the jar"


def test_load_cookie_snapshot_rejects_corrupt_payloads(handler):
    sm = MagicMock()
    cases = [
        None,                              # secret missing
        "",                                # empty string
        "{not json",                       # malformed
        '{"version": 999, "cookies": []}', # version mismatch
        '{"version": 1, "cookies": "nope"}',  # cookies field wrong type
        '{"version": 1, "cookies": []}',   # empty cookies
        json.dumps({                        # all entries already expired
            "version": handler._COOKIE_SNAPSHOT_VERSION,
            "cookies": [{"name": "x", "value": "y",
                         "expires": _past_rfc1123(60)}],
        }),
    ]
    for raw in cases:
        sm.get.return_value = raw
        with patch.object(handler, "_get_secret_manager", return_value=sm):
            assert handler._load_cookie_snapshot("any-id") is None, raw


def test_load_cookie_snapshot_keeps_unexpired_cookies(handler):
    sm = MagicMock()
    payload = json.dumps({
        "version": handler._COOKIE_SNAPSHOT_VERSION,
        "session_key": "https://1.2.3.4|admin",
        "saved_at": int(time.time()),
        "cookies": [
            {"name": "a", "value": "1",
             "expires": _future_rfc1123(3600)},
            {"name": "b", "value": "2",
             "expires": _past_rfc1123(60)},   # filtered out
            {"name": "c", "value": "3", "expires": ""},  # no expiry → kept
        ],
    })
    sm.get.return_value = payload
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        snap = handler._load_cookie_snapshot("any-id")
    assert snap is not None
    names = {c["name"] for c in snap["cookies"]}
    assert names == {"a", "c"}


# ===========================================================================
# Cookie persistence: persist_cookies precedence
# ===========================================================================
@pytest.mark.parametrize(
    "raw, env, expected, why",
    [
        ({"persist_cookies": False}, None, False, "canonical key honoured"),
        ({"persistCookies": False}, None, False, "camelCase alias honoured"),
        ({"custom_settings": {"persist_cookies": False}}, None, False,
         "WebUI custom_settings path honoured"),
        ({}, "false", False, "ONESIG_PERSIST_COOKIES env var honoured"),
        ({}, None, True, "default DEFAULT_PERSIST_COOKIES=True when unset"),
        ({"persist_cookies": True,
          "custom_settings": {"persist_cookies": False}},
         None, True, "canonical wins over custom_settings"),
        ({"persist_cookies": "off"}, None, False, "string 'off' coerced"),
        ({"persist_cookies": 1}, None, True, "integer 1 coerced"),
    ],
)
def test_resolve_persist_cookies_precedence(handler, raw, env, expected, why,
                                            monkeypatch):
    if env is None:
        monkeypatch.delenv("ONESIG_PERSIST_COOKIES", raising=False)
    else:
        monkeypatch.setenv("ONESIG_PERSIST_COOKIES", env)
    assert handler._resolve_persist_cookies(raw) is expected, why


def test_default_persist_cookies_is_on(handler):
    # Persistence is the open-box default. Flipping this back to False would
    # silently make every flocks restart cost an extra 4-RTT login dance.
    assert handler.DEFAULT_PERSIST_COOKIES is True


# ===========================================================================
# Cookie persistence: OneSIGSession integration
# ===========================================================================
def _build_config(handler, *, persist_cookies: bool = True,
                  base_url: str = "https://1.2.3.4",
                  username: str = "admin") -> Any:
    return handler.OneSIGRuntimeConfig(
        base_url=base_url,
        api_prefix="/api",
        username=username,
        password="supersecret",
        oaep_hash="sha1",
        verify_ssl=False,
        timeout=30,
        persist_cookies=persist_cookies,
    )


def _persisted_payload(name: str = "onesig_session", value: str = "live") -> str:
    return json.dumps({
        "version": 1,
        "session_key": "https://1.2.3.4|admin",
        "saved_at": int(time.time()),
        "cookies": [
            {"name": name, "value": value,
             "domain": "1.2.3.4", "path": "/",
             "expires": _future_rfc1123(3600),
             "secure": False, "httponly": True},
        ],
    })


def test_session_init_loads_persisted_cookie_and_marks_logged_in(handler):
    sm = MagicMock()
    sm.get.return_value = _persisted_payload()
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(_build_config(handler))

    assert session._logged_in is True, (
        "persisted cookie present + non-expired → trust it; let request() "
        "fall back to auto-relogin if device has rotated it"
    )
    assert session._pending_cookies and \
           session._pending_cookies[0]["name"] == "onesig_session"
    sm.get.assert_called_once()
    assert sm.get.call_args[0][0].startswith("onesig_session_cookie__")


def test_session_init_skips_load_when_persist_cookies_disabled(handler):
    sm = MagicMock()
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(
            _build_config(handler, persist_cookies=False)
        )
    assert session._logged_in is False
    assert session._pending_cookies is None
    sm.get.assert_not_called(), "no .secret.json read when toggle off"


def test_session_init_does_not_trust_only_expired_cookie(handler):
    sm = MagicMock()
    sm.get.return_value = json.dumps({
        "version": 1,
        "cookies": [{"name": "stale", "value": "x",
                     "expires": _past_rfc1123(60)}],
    })
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(_build_config(handler))
    assert session._logged_in is False
    assert session._pending_cookies is None


@pytest.mark.asyncio
async def test_ensure_session_injects_pending_cookies_into_jar(handler):
    sm = MagicMock()
    sm.get.return_value = _persisted_payload(name="JSESSIONID", value="hot")
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(_build_config(handler))
        client = await session._ensure_session()
        try:
            cookies_in_jar = {m.key: m.value for m in client.cookie_jar}
            assert cookies_in_jar.get("JSESSIONID") == "hot"
            assert session._cookies_loaded is True
            assert session._pending_cookies is None, (
                "pending list cleared once installed into the live jar"
            )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_login_persists_cookie_snapshot(handler):
    sm = MagicMock()
    sm.get.return_value = None  # no prior snapshot
    captured: dict[str, Any] = {}

    def _set(secret_id, payload):
        captured["secret_id"] = secret_id
        captured["payload"] = payload

    sm.set.side_effect = _set

    # Real CookieJar so _persist_cookies has actual content to serialise.
    jar = aiohttp.CookieJar(unsafe=True)
    from http.cookies import SimpleCookie
    from yarl import URL
    sc: SimpleCookie = SimpleCookie()
    sc["onesig_session"] = "freshly-issued"
    sc["onesig_session"]["domain"] = "1.2.3.4"
    sc["onesig_session"]["path"] = "/"
    sc["onesig_session"]["expires"] = _future_rfc1123(3600)
    jar.update_cookies(sc, response_url=URL("https://1.2.3.4/api"))

    fake_session = MagicMock()
    fake_session.closed = False
    fake_session.cookie_jar = jar

    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(_build_config(handler))
        session._session = fake_session  # bypass _ensure_session
        session._persist_cookies()

    assert captured["secret_id"].startswith("onesig_session_cookie__")
    body = json.loads(captured["payload"])
    assert body["version"] == 1
    names = {c["name"] for c in body["cookies"]}
    assert "onesig_session" in names


@pytest.mark.asyncio
async def test_logout_drops_persisted_cookie(handler):
    sm = MagicMock()
    sm.get.return_value = None  # no prior snapshot for __init__
    deleted: list[str] = []
    sm.delete.side_effect = lambda sid: deleted.append(sid) or True

    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(_build_config(handler))
        session._drop_persisted_cookies()

    assert deleted, "_drop_persisted_cookies must call SecretManager.delete"
    assert deleted[0].startswith("onesig_session_cookie__")


@pytest.mark.asyncio
async def test_persist_cookies_is_noop_when_toggle_off(handler):
    sm = MagicMock()
    sm.get.return_value = None
    with patch.object(handler, "_get_secret_manager", return_value=sm):
        session = handler.OneSIGSession(
            _build_config(handler, persist_cookies=False)
        )
        # Even with a populated jar, set() must not be called.
        session._session = MagicMock(closed=False,
                                     cookie_jar=aiohttp.CookieJar(unsafe=True))
        session._persist_cookies()
        session._drop_persisted_cookies()
    sm.set.assert_not_called()
    sm.delete.assert_not_called()


@pytest.mark.asyncio
async def test_request_with_persisted_cookie_skips_full_login_chain(handler):
    """End-to-end: a fresh process whose ``.secret.json`` already has a
    cookie should fire **only** the business request — no captcha, no
    pubkey, no /v3/login, no /v3/account."""
    fake_session = _FakeSession([
        # Just the business call. If the handler accidentally triggers the
        # login chain there will be missing responses and the test fails.
        _FakeResponse(json_payload={
            "responseCode": 0, "verboseMsg": "成功",
            "data": {"version": "v2.5.3"},
        }),
    ])

    sm = MagicMock()
    sm.get.return_value = _persisted_payload()
    sm.set.return_value = None
    sm.delete.return_value = None

    fake_flocks_security = types.ModuleType("flocks.security")
    fake_flocks_security.get_secret_manager = lambda: sm
    sys.modules["flocks.security"] = fake_flocks_security

    raw_service = {
        "base_url": "https://1.2.3.4",
        "username": "admin",
        "password": "{secret:onesig_password}",
        "oaep_hash": "sha1",
        "verify_ssl": False,
    }

    with (
        patch.object(handler.ConfigWriter, "get_api_service_raw",
                     return_value=raw_service),
        patch.object(handler.aiohttp, "ClientSession",
                     return_value=fake_session),
    ):
        handler._SESSIONS.clear()
        config = handler._resolve_runtime_config()
        session = handler.OneSIGSession(config)
        assert session._logged_in is True, (
            "persisted cookie should make the session believe it's already in"
        )
        status, envelope, _, _ = await session.request(
            "GET", "/v3/basic/version"
        )

    assert status == 200
    assert envelope.get("responseCode") == 0
    assert len(fake_session.calls) == 1, (
        "exactly one HTTP call (the business request) — login chain skipped"
    )
    method, url, _kwargs = fake_session.calls[0]
    assert method == "GET"
    assert url.endswith("/v3/basic/version")
