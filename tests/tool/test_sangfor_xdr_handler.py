"""Targeted tests for the Sangfor XDR plugin handler.

The handler lives under ``.flocks/plugins/tools/api/sangfor_xdr/`` and is
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
    / "sangfor_xdr"
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
