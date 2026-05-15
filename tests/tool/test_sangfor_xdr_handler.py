"""Targeted tests for the Sangfor XDR plugin handler.

The handler lives under ``.flocks/plugins/tools/api/sangfor_xdr_v2_2/`` and is
loaded dynamically at runtime, so we import it via a path-based loader to
exercise the helpers we just hardened:

* ``_resolve_runtime_config`` strips protocol prefixes / inline ports from
  the user-supplied ``host`` so the WebUI ``host=https://10.0.0.1`` value
  stops producing ``https://https://10.0.0.1``.
* ``_decode_auth_code`` raises a friendly error instead of a cryptic
  ``binascii.Error`` when the user pastes a non-hex secret.
* ``_parse_response_body`` falls back through UTF-8 / GBK so the test-
  credentials probe no longer fails with
  ``'utf-8' codec can't decode byte 0x8d in position 0``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "tools"
    / "api"
    / "sangfor_xdr_v2_2"
    / "sangfor_xdr.handler.py"
)


def _load_handler_module():
    if not _HANDLER_PATH.exists():
        pytest.skip(f"Sangfor XDR handler not present at {_HANDLER_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_sangfor_xdr_handler_under_test",
        str(_HANDLER_PATH),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def handler():
    return _load_handler_module()


# ---------------------------------------------------------------------------
# Host normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_host, expected_base_url",
    [
        ("10.0.0.1", "https://10.0.0.1"),
        ("https://10.0.0.1", "https://10.0.0.1"),
        ("https://10.0.0.1/", "https://10.0.0.1"),
        ("http://10.0.0.1", "https://10.0.0.1"),
        ("HTTPS://example.test", "https://example.test"),
        ("10.0.0.1:8443", "https://10.0.0.1:8443"),
        ("https://10.0.0.1:8443/", "https://10.0.0.1:8443"),
        # Path / query / fragment must not leak into base_url; otherwise the
        # final URL becomes ``https://10.0.0.1/api/api/xdr/v1/...`` which
        # silently fails signing.
        ("https://10.0.0.1/api", "https://10.0.0.1"),
        ("https://10.0.0.1/api/", "https://10.0.0.1"),
        ("https://10.0.0.1:8443/some/sub/path", "https://10.0.0.1:8443"),
        ("https://10.0.0.1?x=1", "https://10.0.0.1"),
        ("https://10.0.0.1#frag", "https://10.0.0.1"),
        # IPv6 literal must keep its bracketed form so urls remain parseable.
        ("https://[::1]:8443", "https://[::1]:8443"),
        # Surrounding whitespace.
        (" https://10.0.0.1 ", "https://10.0.0.1"),
    ],
)
def test_resolve_runtime_config_normalises_host(handler, raw_host, expected_base_url):
    fake_secret_manager = type(
        "_SM",
        (),
        {"get": staticmethod(lambda key: "deadbeef" if "auth_code" in key else None)},
    )()

    raw_cfg: dict[str, Any] = {
        "host": raw_host,
        "auth_code": "deadbeef",
        "verify_ssl": False,
    }

    with (
        patch.object(handler.ConfigWriter, "get_api_service_raw", return_value=raw_cfg),
        patch.object(handler, "_get_secret_manager", return_value=fake_secret_manager),
    ):
        cfg = handler._resolve_runtime_config()

    assert cfg.base_url == expected_base_url
    assert cfg.verify_ssl is False
    assert cfg.auth_code == "deadbeef"


# ---------------------------------------------------------------------------
# auth_code decoding
# ---------------------------------------------------------------------------

def test_decode_auth_code_rejects_non_hex(handler):
    handler._AK_SK_CACHE.clear()
    with pytest.raises(ValueError) as exc:
        handler._decode_auth_code("lxy/FS$)K10R822_v1WRt)$n")
    assert "联动码" in str(exc.value) or "hex" in str(exc.value).lower()


def test_decode_auth_code_rejects_empty(handler):
    handler._AK_SK_CACHE.clear()
    with pytest.raises(ValueError):
        handler._decode_auth_code("")


# ---------------------------------------------------------------------------
# Response body parsing
# ---------------------------------------------------------------------------

def test_parse_response_body_utf8(handler):
    body = json.dumps({"code": "Success", "data": {"hello": "世界"}}).encode("utf-8")
    parsed = handler._parse_response_body(body, 200)
    assert parsed["code"] == "Success"
    assert parsed["data"]["hello"] == "世界"


def test_parse_response_body_gbk_fallback(handler):
    body = json.dumps({"code": "Success", "msg": "成功"}, ensure_ascii=False).encode("gbk")
    # The first byte of "成" in GBK is 0xB3 — not the canonical 0x8d that
    # broke the user's setup, but the same code path handles every leading
    # byte that fails strict UTF-8 validation.
    parsed = handler._parse_response_body(body, 200)
    assert parsed["msg"] == "成功"


def test_parse_response_body_does_not_leak_unicode_decode_error(handler):
    """Reproduces the user's symptom: a body that fails strict UTF-8 must
    surface as a deterministic ``RuntimeError`` rather than the raw
    ``'utf-8' codec can't decode byte 0x8d in position 0`` ``UnicodeError``
    bubbling out of ``aiohttp``."""

    body = bytes([0x8D, 0xFF, 0xFE, 0xC0])  # not a valid prefix in any encoding+JSON
    with pytest.raises(UnicodeDecodeError):
        body.decode("utf-8")

    with pytest.raises(RuntimeError) as exc:
        handler._parse_response_body(body, 200)

    # Crucially: it is *not* a UnicodeDecodeError — operators see a clear
    # XDR-specific message instead of an opaque codec failure.
    assert not isinstance(exc.value, UnicodeDecodeError)


# ---------------------------------------------------------------------------
# AES-CBC decryption (regression: must use decryptor, not encryptor)
# ---------------------------------------------------------------------------

def test_aes_cbc_decrypt_round_trips_against_reference_encryption(handler):
    """Guard against the historical regression where ``_aes_cbc_decrypt`` was
    implemented with ``cipher.encryptor()`` instead of ``cipher.decryptor()``.

    The bug silently returned a re-encrypted blob in place of the AK/SK,
    which the XDR server then rejected with ``access key not exist`` /
    ``Full ak/sk authentication is required``.  We assert that the helper
    matches the canonical AES-CBC behaviour from the official Sangfor demo
    (``aksk_py3.Signature.__aes_cbc_decrypt``): zero IV, NUL padding, and a
    real *decrypt* operation.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = b"0123456789abcdef"  # 16-byte AES key
    plaintext = b"AKSK_TEST_VALUE\x00"  # 16 bytes, NUL-padded like the SDK
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytearray(16)), backend=default_backend())
    encryptor = cipher.encryptor()
    cipher_bytes = encryptor.update(plaintext) + encryptor.finalize()
    cipher_hex = cipher_bytes.hex()

    decoded = handler._aes_cbc_decrypt(cipher_hex, key)

    assert decoded == "AKSK_TEST_VALUE", (
        "AES decrypt regressed: handler is no longer reversing the SDK's "
        "AES-CBC encryption (likely encryptor() was reintroduced)."
    )


def test_sign_request_sorts_query_params(handler, monkeypatch):
    """Demo (``aksk_py3.__query_str_transform``) sorts query params by key
    before signing.  Two requests with the same params in different dict
    orders must therefore produce identical signatures."""
    headers_a = {handler.CONTENT_TYPE_KEY: handler.DEFAULT_CONTENT_TYPE}
    headers_b = {handler.CONTENT_TYPE_KEY: handler.DEFAULT_CONTENT_TYPE}

    fixed = "20260101T000000Z"

    class _FixedDT:
        @staticmethod
        def now(tz=None):  # noqa: ARG004 - signature compatibility
            class _D:
                @staticmethod
                def strftime(_fmt):
                    return fixed

            return _D()

    monkeypatch.setattr(handler, "datetime", _FixedDT)

    signed_a = handler._sign_request(
        ak="ak",
        sk="sk",
        method="GET",
        url="https://10.0.0.1/api/v1/alerts",
        headers=headers_a,
        params={"b": "2", "a": "1", "c": "3"},
    )
    signed_b = handler._sign_request(
        ak="ak",
        sk="sk",
        method="GET",
        url="https://10.0.0.1/api/v1/alerts",
        headers=headers_b,
        params={"c": "3", "a": "1", "b": "2"},
    )
    assert signed_a[handler.AUTH_HEADER_KEY] == signed_b[handler.AUTH_HEADER_KEY]


def test_parse_response_body_empty_raises(handler):
    with pytest.raises(RuntimeError) as exc:
        handler._parse_response_body(b"", 502)
    assert "empty body" in str(exc.value)
    assert "502" in str(exc.value)


def test_parse_response_body_undecodable_raises(handler):
    raw = bytes([0x8D, 0xFF, 0xFE, 0xC0])
    with pytest.raises(RuntimeError) as exc:
        handler._parse_response_body(raw, 200)
    assert "could not decode" in str(exc.value).lower() or "parse" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# _request body serialisation
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body


class _FakeSession:
    """Minimal stand-in for ``aiohttp.ClientSession`` that captures the
    keyword arguments passed to ``session.request`` so tests can assert on
    the wire-level body the handler actually transmits."""

    def __init__(self, response_body: bytes = b'{"code":"Success","data":null}'):
        self.calls: list[dict[str, Any]] = []
        self._response_body = response_body

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(self._response_body)


@pytest.mark.parametrize(
    "data, expected_body",
    [
        # The historical bug: ``if data`` treats {} as falsy and therefore
        # transmits an empty string body — the XDR appliance then rejects
        # it with "参数解析异常".  After the fix, an empty dict serialises
        # to the canonical JSON literal ``{}``.
        ({}, "{}"),
        ({"foo": "bar"}, '{"foo": "bar"}'),
        # ``alerts/dealstatus/list`` and ``incidents/dealstatus/list``
        # require a JSON *array* body; an empty list must NOT degrade to
        # an empty string either.
        ([], "[]"),
        (["alert-uuid-1"], '["alert-uuid-1"]'),
        # ``data=None`` is the only case allowed to send a truly empty
        # body (e.g. for plain GET endpoints with no payload).
        (None, ""),
    ],
)
def test_request_serialises_body_for_empty_containers(handler, data, expected_body):
    """Regression for the wire-level body sent to the XDR appliance.

    Empty ``{}`` / ``[]`` containers must serialise to the canonical
    JSON literals so that:

      1.  The signed payload hash matches what the server computes
          (otherwise we get signature mismatches even when the body is
          accepted), and
      2.  The server's strict JSON parser does not reject the request
          with "参数解析异常 / 请求参数校验失败 / 参数不合法".
    """
    import asyncio

    cfg = handler.RuntimeConfig(
        base_url="https://10.0.0.1",
        timeout=5,
        auth_code="deadbeef",
        verify_ssl=False,
    )

    session = _FakeSession()

    with (
        patch.object(handler, "_decode_auth_code", return_value=("AK", "SK")),
        # Don't actually compute HMAC headers — we only care about body.
        patch.object(handler, "_sign_request", side_effect=lambda *a, **kw: kw.get("headers") or a[4]),
    ):
        result = asyncio.run(
            handler._request(cfg, session, "POST", "/api/xdr/v1/whitelists/list", data=data)
        )

    assert result == {"code": "Success", "data": None}
    assert len(session.calls) == 1
    sent_body = session.calls[0].get("data")
    assert sent_body == expected_body, (
        f"_request transmitted {sent_body!r} for data={data!r}; "
        f"expected {expected_body!r}.  Empty containers must round-trip "
        "as JSON literals so the XDR signature & body parser both succeed."
    )


# ---------------------------------------------------------------------------
# Action-specific body shape (regression for "param page cannot be null"
# and "请求参数校验失败" surfaced by the second WebUI test report)
# ---------------------------------------------------------------------------

def _run_action(handler, run_fn, params: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool handler with patched IO and return the body that
    ``_run_request`` would have transmitted, plus the method/path."""
    import asyncio

    captured: dict[str, Any] = {}

    async def _fake_run_request(method, path, data=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = data
        captured["params"] = params

        class _R:
            success = True
            error = None
            data = {"code": "Success"}

        return _R()

    ctx = type("_Ctx", (), {"params": params})()

    with patch.object(handler, "_run_request", side_effect=_fake_run_request):
        asyncio.run(run_fn(ctx))

    return captured


def test_whitelists_list_uses_page_not_pageNum(handler):
    """Spec defines the paging key as ``page``; the appliance rejects
    ``pageNum`` with ``param page cannot be null``."""
    captured = _run_action(handler, handler.run_whitelists, {"action": "list"})
    assert captured["path"] == "/api/xdr/v1/whitelists/list"
    body = captured["data"]
    assert "page" in body, f"expected 'page' key, got {body!r}"
    assert "pageNum" not in body, f"'pageNum' must not appear, got {body!r}"
    assert body["page"] == 1
    assert body["pageSize"] == 20


def test_whitelists_list_honours_explicit_paging(handler):
    captured = _run_action(
        handler,
        handler.run_whitelists,
        {"action": "list", "page_num": 3, "page_size": 50},
    )
    assert captured["data"] == {"page": 3, "pageSize": 50}


def test_vuln_list_includes_required_dataType(handler):
    """Spec marks ``dataType`` as the only paramNotNull=0 field on
    /vuls/risk/list; missing it produces ``请求参数校验失败``."""
    captured = _run_action(handler, handler.run_vulns, {"action": "vuln_list"})
    assert captured["path"] == "/api/xdr/v1/vuls/risk/list"
    body = captured["data"]
    assert body["dataType"] == "loophole"
    assert body["page"] == 1
    assert body["pageSize"] == 20


def test_vuln_list_allows_weakpwd_dataType(handler):
    captured = _run_action(
        handler,
        handler.run_vulns,
        {"action": "vuln_list", "data_type": "weakpwd"},
    )
    assert captured["data"]["dataType"] == "weakpwd"


def test_baseline_list_uses_page_not_pageNum(handler):
    captured = _run_action(handler, handler.run_vulns, {"action": "baseline"})
    body = captured["data"]
    assert "page" in body and "pageNum" not in body, body


def test_request_get_does_not_transmit_body_but_signs_canonical_payload(handler):
    """GET requests must not put a body on the wire, but the signed payload
    hash should still reflect ``""`` (not ``{}``) for null ``data``."""
    import asyncio

    cfg = handler.RuntimeConfig(
        base_url="https://10.0.0.1",
        timeout=5,
        auth_code="deadbeef",
        verify_ssl=False,
    )
    session = _FakeSession()

    with (
        patch.object(handler, "_decode_auth_code", return_value=("AK", "SK")),
        patch.object(handler, "_sign_request", side_effect=lambda *a, **kw: kw.get("headers") or a[4]),
    ):
        asyncio.run(
            handler._request(cfg, session, "GET", "/api/xdr/v1/alerts/uuid/proof")
        )

    assert "data" not in session.calls[0], "GET requests must omit body kwarg"


# ---------------------------------------------------------------------------
# Regression tests for the 各工具功能测试 session
# ---------------------------------------------------------------------------

def test_run_request_returns_payload_via_output_field(handler):
    """The handler historically called ``ToolResult(success=True, data=...)``.

    ``ToolResult`` declares ``output`` (not ``data``) as its payload
    field; pydantic silently dropped the unknown kwarg, so every call —
    even successful ones — surfaced ``output=None`` to the LLM agent
    which then reported "认证后请求恢复内容都是空的".  This test pins
    the field name so the regression cannot recur.
    """
    import asyncio

    captured = {"called": False}

    async def _fake_request(cfg, session, method, path, data=None, params=None):
        captured["called"] = True
        return {"code": "Success", "data": {"item": [{"uuId": "abc"}], "total": 1}}

    cfg = handler.RuntimeConfig(
        base_url="https://10.0.0.1",
        timeout=5,
        auth_code="deadbeef",
        verify_ssl=False,
    )
    with (
        patch.object(handler, "_resolve_runtime_config", return_value=cfg),
        patch.object(handler, "_request", side_effect=_fake_request),
    ):
        result = asyncio.run(handler._run_request("POST", "/api/xdr/v1/alerts/list"))

    assert captured["called"]
    assert result.success is True
    assert result.error is None
    # MUST be on .output (not .data) — see ToolResult class definition.
    assert result.output == {
        "code": "Success",
        "data": {"item": [{"uuId": "abc"}], "total": 1},
    }, (
        "ToolResult payload must arrive on the `output` attribute. "
        "If this assertion fails, the handler is constructing "
        "ToolResult(data=...) again — pydantic silently drops the kwarg "
        "and downstream consumers see output=None."
    )


@pytest.mark.parametrize(
    # 2026-04-21 00:00:00 UTC == 1776729600 (verified via:
    #   datetime(2026,4,21,tzinfo=timezone.utc).timestamp())
    "raw, expected_seconds_utc",
    [
        ("2026-04-21T00:00:00Z", 1776729600),
        ("2026-04-21T00:00:00.000Z", 1776729600),
        ("2026-04-21T00:00:00", 1776729600),
        ("2026-04-21 00:00:00", 1776729600),
        ("2026-04-21", 1776729600),
        # 2026-04-21 08:30:00 +08:00 == 2026-04-21 00:30:00 UTC
        ("2026-04-21T08:30:00+08:00", 1776729600 + 1800),
        ("1776729600", 1776729600),
        (1776729600, 1776729600),
    ],
)
def test_to_ts_handles_iso8601_with_z_suffix(handler, raw, expected_seconds_utc):
    """The first 各工具功能测试 attempt failed with
    ``Cannot parse time value: '2026-04-21T00:00:00Z'`` because the
    trailing ``Z`` was unsupported.  Accept ISO-8601 in all the shapes
    a JS ``Date.toISOString()`` (and reasonable LLM guesses) can emit.
    """
    assert handler._to_ts(raw) == expected_seconds_utc


def test_assets_list_uses_page_not_pageNum(handler):
    """Spec rejects ``{'pageSize': 5, 'pageNum': 1}`` with
    ``参数: ... 不合法``; the assets list endpoint is a sibling of
    whitelists/list and uses the same ``page`` / ``pageSize`` keys."""
    captured = _run_action(handler, handler.run_assets, {"action": "list"})
    assert captured["path"] == "/api/xdr/v1/assets/list"
    body = captured["data"]
    assert "page" in body and "pageSize" in body, body
    assert "pageNum" not in body, f"pageNum must not appear, got {body!r}"
    assert body["page"] == 1
    assert body["pageSize"] == 20


def test_assets_list_honours_explicit_paging(handler):
    captured = _run_action(
        handler,
        handler.run_assets,
        {"action": "list", "page_num": 2, "page_size": 30},
    )
    assert captured["data"] == {"page": 2, "pageSize": 30}


@pytest.mark.parametrize(
    "alias, canonical, run_fn_name, expected_path",
    [
        # alerts: query → list (1st 各工具功能测试 round had every tool
        # default to action="query" and got "Unknown ... action")
        ("query", "list", "run_alerts", "/api/xdr/v1/alerts/list"),
        ("search", "list", "run_alerts", "/api/xdr/v1/alerts/list"),
        ("get", "list", "run_incidents", "/api/xdr/v1/incidents/list"),
        ("fetch", "list", "run_whitelists", "/api/xdr/v1/whitelists/list"),
        # vulns: agent guessed "query_baseline" then "query_vuln"
        ("query_baseline", "baseline", "run_vulns", "/api/xdr/v1/vuls/baseline/list"),
        ("query_vuln", "vuln_list", "run_vulns", "/api/xdr/v1/vuls/risk/list"),
        ("query", "list", "run_assets", "/api/xdr/v1/assets/list"),
        # responses default
        ("isolate", "isolate_list", "run_responses",
         "/api/xdr/v1/responses/host/isolate/list"),
    ],
)
def test_action_alias_normalisation(handler, alias, canonical, run_fn_name, expected_path):
    """LLM agents observed to emit synonyms instead of canonical action
    names.  The handler now maps the most common ones back so the
    request still goes through (vs. surfacing ``Unknown ... action``)."""
    run_fn = getattr(handler, run_fn_name)
    captured = _run_action(handler, run_fn, {"action": alias})
    assert captured["path"] == expected_path, (
        f"alias {alias!r} should route to {canonical!r} "
        f"({expected_path}) but went to {captured['path']!r}"
    )


def test_normalise_action_default_when_missing(handler):
    """``None`` / empty / unknown all fall through gracefully."""
    assert handler._normalise_action(None, "list") == "list"
    assert handler._normalise_action("", "baseline") == "baseline"
    assert handler._normalise_action("   ", "list") == "list"
    # Unknown but non-empty values pass through unchanged so the per-action
    # ``Unknown ... action`` error message can still guide the caller.
    assert handler._normalise_action("unknown_op", "list") == "unknown_op"
    # Case folding: agents sometimes capitalise.
    assert handler._normalise_action("LIST", "list") == "list"
    assert handler._normalise_action("Query", "list") == "list"
