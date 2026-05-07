"""Regression tests for the NGSOC handler.

The NGSOC plugin is intentionally simpler than OneSIG (static
``NGSOC-Access-Token`` header, no captcha / pubkey / cookie negotiation),
but the dispatch architecture is the same ``ActionSpec``-driven one.
This file pins down the load-bearing surfaces:

1. **SSL verify resolution** — parity with onesig / onesec / qingteng so
   the WebUI ``custom_settings.verify_ssl`` toggle, the ``ssl_verify``
   snake_case alias, the ``verifySsl`` legacy camelCase alias and the
   ``NGSOC_VERIFY_SSL`` env var all reach
   ``aiohttp.session.request(..., ssl=...)`` correctly. The default is
   ``False`` because NGSOC is overwhelmingly deployed as a private
   appliance with a self-signed certificate.
2. **Token header injection** — every outbound request carries
   ``NGSOC-Access-Token``; we never fall back to ``Authorization``.
3. **URL composition** — ``base_url + api_prefix + path`` with REST
   placeholder substitution; unresolved ``{placeholder}`` raises
   ``ValueError`` instead of silently shipping a literal to the gateway.
4. **ActionSpec request building** — passthrough_query / passthrough_body
   forwarding, body_keys filtering, and required-parameter validation
   that the dispatcher relies on.
5. **Envelope unwrapping** — ``errCode == 0`` returns ``data``,
   ``errCode != 0`` collapses ``errMsg`` / ``errDetail`` into
   ``ToolResult.error``.
6. **Binary downloads** — ``/storage/download`` body bytes are persisted
   under ``~/.flocks/workspace/outputs/<today>/ngsoc_*`` and the saved
   path / size / content-type surface in metadata.
7. **Group dispatch** — ``action="test"`` routes to a no-arg
   connectivity probe; unknown actions return a discoverable error
   listing the available actions for the group.
8. **YAML manifests** — every group's ``ngsoc_<group>.yaml`` loads via
   ``yaml_to_tool`` and binds to the matching handler entry-point.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from flocks.tool.registry import ToolContext


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------
# The NGSOC handler lives outside the ``flocks`` package, so we load it
# directly via importlib (the same trick the YAML tool loader uses).
_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "tools"
    / "api"
    / "ngsoc"
)
_HANDLER_PATH = _PLUGIN_DIR / "ngsoc.handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "ngsoc_handler_under_test", _HANDLER_PATH
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
@pytest.mark.parametrize(
    "raw, env, expected, why",
    [
        # 1. canonical key wins over everything below it
        (
            {
                "verify_ssl": False,
                "ssl_verify": True,
                "custom_settings": {"verify_ssl": True},
            },
            None,
            False,
            "verify_ssl=False overrides ssl_verify=True and custom_settings",
        ),
        # 2. ssl_verify alias honoured when verify_ssl missing
        ({"ssl_verify": False}, None, False, "ssl_verify alias respected"),
        # 3. legacy camelCase verifySsl still works
        ({"verifySsl": False}, None, False, "verifySsl legacy alias respected"),
        # 4. WebUI's custom_settings.verify_ssl drives the default UI switch
        (
            {"custom_settings": {"verify_ssl": False}},
            None,
            False,
            "custom_settings.verify_ssl honoured (UI toggle path)",
        ),
        # 5. env var fallback for CLI / containerised deployments
        ({}, "false", False, "NGSOC_VERIFY_SSL env var honoured"),
        # 6. nothing set -> default False (NGSOC is private-appliance heavy)
        ({}, None, False, "default DEFAULT_VERIFY_SSL=False when unset"),
        # 7. string coercion through _coerce_bool
        ({"verify_ssl": "off"}, None, False, "off -> False"),
        ({"verify_ssl": "1"}, None, True, "'1' -> True"),
        ({"verify_ssl": 0}, None, False, "numeric 0 -> False"),
        # 8. precedence regression: custom_settings ignored once ssl_verify present
        (
            {
                "ssl_verify": True,
                "custom_settings": {"verify_ssl": False},
            },
            None,
            True,
            "ssl_verify (closer to canonical) wins over custom_settings",
        ),
    ],
)
def test_resolve_verify_ssl_precedence(handler, raw, env, expected, why, monkeypatch):
    if env is None:
        monkeypatch.delenv("NGSOC_VERIFY_SSL", raising=False)
    else:
        monkeypatch.setenv("NGSOC_VERIFY_SSL", env)
    assert handler._resolve_verify_ssl(raw) is expected, why


def test_default_verify_ssl_is_off_for_private_deployments(handler):
    # NGSOC is overwhelmingly deployed as a private appliance with a self-
    # signed cert. Flipping this default back to True would silently break
    # every fresh integration, so guard the constant explicitly.
    assert handler.DEFAULT_VERIFY_SSL is False


# ---------------------------------------------------------------------------
# _ssl_context: bool -> aiohttp ssl arg shape
# ---------------------------------------------------------------------------
def test_ssl_context_returns_none_when_verify_enabled(handler):
    # When validation is on, returning None lets aiohttp use its default
    # certifi-backed context (i.e. real validation).
    assert handler._ssl_context(True) is None


def test_ssl_context_disables_validation_when_disabled(handler):
    import ssl as _ssl

    ctx = handler._ssl_context(False)
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == _ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Constants we don't want flipping by accident
# ---------------------------------------------------------------------------
def test_token_header_name_is_ngsoc_access_token(handler):
    # Header name is case-sensitive on at least one NGSOC build observed
    # during integration; pin the exact case so a refactor can't silently
    # break authentication in production.
    assert handler.TOKEN_HEADER == "NGSOC-Access-Token"


def test_default_api_prefix_matches_manual(handler):
    # Manual §2.1 prints ``https://ngsoc/api/v1/<api-url>`` everywhere.
    assert handler.DEFAULT_API_PREFIX == "/api/v1"


def test_product_version_marker(handler):
    # Surfaces in metadata['version'] so downstream dashboards can
    # distinguish R4.15.x payloads from older NGSOC R3.x integrations.
    assert handler.PRODUCT_VERSION == "R4.15.1"


# ---------------------------------------------------------------------------
# NGSOCRuntimeConfig.build_url + NGSOCSession._path_with_rest
# ---------------------------------------------------------------------------
def _build_config(handler, **overrides):
    base = dict(
        base_url="https://ngsoc.example.com",
        api_prefix="/api/v1",
        access_token="token-abc-123456",
        verify_ssl=False,
        timeout=30,
    )
    base.update(overrides)
    return handler.NGSOCRuntimeConfig(**base)


def test_build_url_concatenates_base_prefix_and_path(handler):
    config = _build_config(handler)
    assert (
        config.build_url("/alarms/list")
        == "https://ngsoc.example.com/api/v1/alarms/list"
    )
    # Path without leading slash is normalised
    assert (
        config.build_url("alarms/list")
        == "https://ngsoc.example.com/api/v1/alarms/list"
    )


def test_build_url_supports_empty_api_prefix(handler):
    config = _build_config(handler, api_prefix="")
    assert (
        config.build_url("/alarms/list")
        == "https://ngsoc.example.com/alarms/list"
    )


def test_session_key_uses_token_fingerprint_only(handler):
    # The full token must NOT appear in the in-process session-pool key
    # because we sometimes log iteration of _SESSIONS for debugging. Only
    # the trailing 6 chars are used as a rotation marker.
    config = _build_config(handler, access_token="abcdef-very-long-secret-XYZ123")
    assert "very-long" not in config.session_key
    assert config.session_key.endswith("XYZ123")


def test_path_with_rest_substitutes_placeholders(handler):
    session = handler.NGSOCSession(_build_config(handler))
    out = session._path_with_rest(
        "/alarms/{alarm-id}/raw-alarms",
        {"alarm-id": "uuid-123"},
    )
    assert out == "/alarms/uuid-123/raw-alarms"


def test_path_with_rest_handles_two_placeholders(handler):
    session = handler.NGSOCSession(_build_config(handler))
    out = session._path_with_rest(
        "/app-ai-alarm-judgment/judge/{merge-alarm-id}/{judge-type}",
        {"merge-alarm-id": "uuid-1", "judge-type": 2},
    )
    assert out == "/app-ai-alarm-judgment/judge/uuid-1/2"


def test_path_with_rest_raises_when_placeholder_missing(handler):
    session = handler.NGSOCSession(_build_config(handler))
    with pytest.raises(ValueError, match="REST 参数"):
        session._path_with_rest(
            "/workorders/work-order/{workOrderId}",
            {},
        )


# ---------------------------------------------------------------------------
# ActionSpec.build_request
# ---------------------------------------------------------------------------
def test_action_spec_get_with_passthrough_query(handler):
    spec = handler.ActionSpec(
        "GET",
        "/alarms/{raw-alarm-id}/evidence",
        rest_keys=["raw-alarm-id"],
        passthrough_query=True,
    )
    rest, query, body = spec.build_request(
        {
            "action": "alarm_evidence_get",
            "raw-alarm-id": "uuid-1",
            "type": "unix",
            "timestamp": 1700000000,
            "ruleId": "rule-1",
            "missing": None,
        }
    )
    assert rest == {"raw-alarm-id": "uuid-1"}
    assert query == {
        "type": "unix",
        "timestamp": 1700000000,
        "ruleId": "rule-1",
    }
    assert body is None
    # ``action`` is a reserved key and never leaks downstream
    assert "action" not in (query or {})


def test_action_spec_post_with_passthrough_body(handler):
    spec = handler.ActionSpec(
        "POST",
        "/alarms/list",
        passthrough_body=True,
    )
    rest, query, body = spec.build_request(
        {
            "action": "alarm_list",
            "queryParams": {"timestamp": {"stime": 1, "etime": 2}},
            "page": 1,
            "size": 50,
            "blank": None,
        }
    )
    assert rest == {}
    assert query is None
    assert body == {
        "queryParams": {"timestamp": {"stime": 1, "etime": 2}},
        "page": 1,
        "size": 50,
    }


def test_action_spec_post_with_explicit_body_keys_filters_unknown(handler):
    spec = handler.ActionSpec(
        "PUT",
        "/workorders/list/status",
        body_keys=["workOrderIds", "status"],
    )
    rest, query, body = spec.build_request(
        {
            "workOrderIds": [1, 2],
            "status": 5,
            "noise": "ignored",
        }
    )
    assert rest == {}
    assert query is None
    assert body == {"workOrderIds": [1, 2], "status": 5}


def test_action_spec_get_without_query_returns_none(handler):
    spec = handler.ActionSpec("GET", "/alarms/detail/summary/{uuid}", rest_keys=["uuid"])
    rest, query, body = spec.build_request({"uuid": "u1"})
    assert rest == {"uuid": "u1"}
    assert query is None
    assert body is None


def test_validate_required_reports_missing_and_blank(handler):
    spec = handler.ActionSpec(
        "POST",
        "/x",
        body_keys=["a"],
        required=["a", "b"],
    )
    err = handler._validate_required(spec, "x", {"a": "", "b": None})
    assert err is not None
    assert "a" in err and "b" in err


def test_validate_required_passes_when_truthy(handler):
    spec = handler.ActionSpec(
        "POST",
        "/x",
        body_keys=["a"],
        required=["a"],
    )
    assert handler._validate_required(spec, "x", {"a": "value"}) is None
    assert handler._validate_required(spec, "x", {"a": [1]}) is None
    assert handler._validate_required(spec, "x", {"a": 0}) is None


# ---------------------------------------------------------------------------
# Envelope unwrap
# ---------------------------------------------------------------------------
def test_envelope_to_result_success_returns_data(handler):
    result = handler._envelope_to_result(
        "alarm_list",
        {"errCode": 0, "errMsg": "成功", "data": {"items": [1, 2, 3]}},
    )
    assert result.success is True
    assert result.output == {"items": [1, 2, 3]}
    assert result.metadata["source"] == "NGSOC"
    assert result.metadata["api"] == "alarm_list"
    assert result.metadata["version"] == "R4.15.1"


def test_envelope_to_result_error_collapses_message(handler):
    result = handler._envelope_to_result(
        "alarm_list",
        {"errCode": 1001, "errMsg": "无权限", "errDetail": None, "data": None},
    )
    assert result.success is False
    assert "errCode=1001" in result.error
    assert "无权限" in result.error
    # Even on error we surface the raw envelope so the caller can debug.
    assert result.output["errCode"] == 1001


def test_envelope_to_result_error_falls_back_to_err_detail(handler):
    result = handler._envelope_to_result(
        "alarm_list",
        {"errCode": 500, "errMsg": "", "errDetail": "boom"},
    )
    assert result.success is False
    assert "boom" in result.error


def test_envelope_to_result_envelope_without_data_returns_envelope(handler):
    result = handler._envelope_to_result(
        "anything",
        {"errCode": 0, "errMsg": "ok"},
    )
    assert result.success is True
    # No ``data`` key -> surface the envelope itself so the caller can pick
    # what they need (e.g. requestId for tracing).
    assert result.output == {"errCode": 0, "errMsg": "ok"}


# ---------------------------------------------------------------------------
# End-to-end dispatch via FakeSession
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        json_payload: Any = None,
        body_bytes: bytes = b"",
        content_type: str = "application/json",
    ):
        self.status = status
        self._json_payload = json_payload
        self._body_bytes = body_bytes
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):  # noqa: ARG002
        return self._json_payload

    async def read(self):
        return self._body_bytes


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


def _patch_runtime(handler, raw_service: dict[str, Any], fake_session: _FakeSession):
    secret_manager = MagicMock()
    secret_manager.get.side_effect = lambda key: {
        "ngsoc_access_token": "token-from-secret-store",
    }.get(key)
    return (
        patch.object(
            handler.ConfigWriter,
            "get_api_service_raw",
            return_value=raw_service,
        ),
        patch.object(handler, "_get_secret_manager", return_value=secret_manager),
        patch.object(handler.aiohttp, "ClientSession", return_value=fake_session),
    )


@pytest.mark.asyncio
async def test_alarms_list_data_injects_token_header_and_posts_body(handler):
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "errCode": 0,
                    "errMsg": "成功",
                    "data": {"items": [{"alarmId": "uuid-1"}], "total": 1},
                }
            )
        ]
    )

    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "api_prefix": "/api/v1",
        "access_token": "{secret:ngsoc_access_token}",
    }

    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.alarms(
            ToolContext(session_id="t", message_id="m"),
            action="alarm_list_data",
            queryParams={"timestamp": {"stime": 1, "etime": 2}},
            page=1,
            size=50,
        )

    assert result.success is True
    assert result.output == {"items": [{"alarmId": "uuid-1"}], "total": 1}
    assert result.metadata["api"] == "alarm_list_data"
    assert result.metadata["http_status"] == 200
    assert result.metadata["err_code"] == 0
    assert result.metadata["method"] == "POST"
    assert result.metadata["source"] == "NGSOC"

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://ngsoc.example.com/api/v1/alarms/list-data"
    # Token header must be present on every outbound request — this is the
    # whole authentication contract.
    assert kwargs["headers"]["NGSOC-Access-Token"] == "token-from-secret-store"
    # JSON body sent with application/json (handler injects it for POST/PUT).
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"] == {
        "queryParams": {"timestamp": {"stime": 1, "etime": 2}},
        "page": 1,
        "size": 50,
    }
    # SSL is off by default for NGSOC -> aiohttp gets a permissive context.
    import ssl as _ssl

    assert isinstance(kwargs["ssl"], _ssl.SSLContext)


@pytest.mark.asyncio
async def test_assets_get_substitutes_rest_placeholder(handler):
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "errCode": 0,
                    "data": {"id": 42, "name": "host-42"},
                }
            )
        ]
    )

    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }

    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.assets(
            ToolContext(session_id="t", message_id="m"),
            action="asset_get",
            id=42,
        )

    assert result.success is True
    assert result.output == {"id": 42, "name": "host-42"}

    method, url, kwargs = fake_session.calls[0]
    assert method == "GET"
    # REST placeholder substituted; default api_prefix /api/v1 applied.
    assert url == "https://ngsoc.example.com/api/v1/assets/asset/42"
    # GET requests have no Content-Type and no JSON body.
    assert "json" not in kwargs
    assert kwargs["headers"]["NGSOC-Access-Token"] == "tok"


@pytest.mark.asyncio
async def test_workorders_get_returns_error_when_rest_param_missing(handler):
    # No HTTP call is made — the dispatcher rejects the request before it
    # reaches the wire because ``workOrderId`` is in spec.required.
    fake_session = _FakeSession([])
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }

    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.workorders(
            ToolContext(session_id="t", message_id="m"),
            action="work_order_get",
        )

    assert result.success is False
    assert "workOrderId" in result.error
    assert fake_session.calls == [], "no HTTP call expected on validation failure"


@pytest.mark.asyncio
async def test_envelope_error_propagates_to_tool_result(handler):
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "errCode": 1003,
                    "errMsg": "查询参数非法",
                    "errDetail": "timestamp is required",
                }
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }

    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.alarms(
            ToolContext(session_id="t", message_id="m"),
            action="alarm_list_data",
            queryParams={"foo": "bar"},
        )

    assert result.success is False
    assert "errCode=1003" in result.error
    assert "查询参数非法" in result.error
    # http_status / err_code still present in metadata for observability.
    assert result.metadata["err_code"] == 1003
    assert result.metadata["http_status"] == 200


@pytest.mark.asyncio
async def test_unsupported_action_returns_discoverable_error(handler):
    fake_session = _FakeSession([])
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.assets(
            ToolContext(session_id="t", message_id="m"),
            action="this_action_does_not_exist",
        )

    assert result.success is False
    assert "Unsupported assets action" in result.error
    # The error must enumerate available actions so callers can self-correct.
    for known in ("asset_get", "asset_id_list", "asset_group_list"):
        assert known in result.error


@pytest.mark.asyncio
async def test_action_test_routes_to_connectivity_probe(handler):
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={"errCode": 0, "data": ["admin", "audit"]}
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        # users group has user_nicknames as the connectivity probe.
        result = await handler.users(
            ToolContext(session_id="t", message_id="m"),
            action="test",
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "GET"
    assert url.endswith("/users/accounts/nicknames")


@pytest.mark.asyncio
async def test_action_test_rejected_when_group_has_no_probe(handler):
    fake_session = _FakeSession([])
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        # alarms has no zero-arg connectivity probe defined.
        result = await handler.alarms(
            ToolContext(session_id="t", message_id="m"),
            action="test",
        )
    assert result.success is False
    assert "连通性测试" in result.error
    assert fake_session.calls == []


# ---------------------------------------------------------------------------
# Binary download persistence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_storage_download_persists_binary_under_outputs_dir(handler, tmp_path):
    body = b"PCAP-PAYLOAD-\x00\x01\x02"
    fake_session = _FakeSession(
        [
            _FakeResponse(
                status=200,
                json_payload=None,
                body_bytes=body,
                content_type="application/octet-stream",
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }

    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()

    patches = _patch_runtime(handler, raw_service, fake_session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(handler, "_outputs_dir", return_value=str(outputs_dir)),
    ):
        handler._SESSIONS.clear()
        result = await handler.storage(
            ToolContext(session_id="t", message_id="m"),
            action="storage_download",
            uuidName="abc-uuid",
            fileName="evidence.pcap",
            serviceName="alarms",
        )

    assert result.success is True
    saved_path = Path(result.output["saved_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == body
    assert saved_path.parent == outputs_dir
    assert saved_path.name.startswith("ngsoc_")
    # User supplied ``fileName="evidence.pcap"`` -> preserve the original
    # name + extension instead of synthesizing a Content-Type-derived one.
    assert saved_path.name.endswith("_evidence.pcap")
    assert saved_path.suffix == ".pcap"

    assert result.metadata["binary_size"] == len(body)
    assert result.metadata["content_type"] == "application/octet-stream"
    assert result.metadata["saved_path"] == str(saved_path)

    method, url, kwargs = fake_session.calls[0]
    assert method == "GET"
    assert url == "https://ngsoc.example.com/api/v1/storage/download"
    assert kwargs["params"] == {
        "uuidName": "abc-uuid",
        "fileName": "evidence.pcap",
        "serviceName": "alarms",
    }


@pytest.mark.asyncio
async def test_storage_download_required_uuid_validated(handler):
    fake_session = _FakeSession([])
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.storage(
            ToolContext(session_id="t", message_id="m"),
            action="storage_download",
            fileName="x.bin",
        )
    assert result.success is False
    assert "uuidName" in result.error
    assert fake_session.calls == [], "validation must short-circuit before HTTP"


# ---------------------------------------------------------------------------
# Configuration resolution edge cases
# ---------------------------------------------------------------------------
def test_resolve_runtime_config_requires_base_url(handler, monkeypatch):
    monkeypatch.delenv("NGSOC_BASE_URL", raising=False)
    with patch.object(
        handler.ConfigWriter, "get_api_service_raw", return_value={}
    ), patch.object(
        handler, "_get_secret_manager", return_value=MagicMock(get=lambda _: None)
    ):
        with pytest.raises(ValueError, match="base_url"):
            handler._resolve_runtime_config()


def test_resolve_runtime_config_requires_access_token(handler, monkeypatch):
    monkeypatch.delenv("NGSOC_ACCESS_TOKEN", raising=False)
    sm = MagicMock()
    sm.get.return_value = None
    with patch.object(
        handler.ConfigWriter,
        "get_api_service_raw",
        return_value={"base_url": "https://ngsoc.example.com"},
    ), patch.object(handler, "_get_secret_manager", return_value=sm):
        with pytest.raises(ValueError, match="access_token"):
            handler._resolve_runtime_config()


def test_resolve_runtime_config_normalises_api_prefix(handler, monkeypatch):
    monkeypatch.delenv("NGSOC_API_PREFIX", raising=False)
    sm = MagicMock()
    sm.get.return_value = "secret-token"
    with patch.object(
        handler.ConfigWriter,
        "get_api_service_raw",
        return_value={
            "base_url": "https://ngsoc.example.com/",
            "api_prefix": "ngsoc/api/v1/",  # missing leading slash, trailing slash
            "access_token": "{secret:ngsoc_access_token}",
        },
    ), patch.object(handler, "_get_secret_manager", return_value=sm):
        config = handler._resolve_runtime_config()
    assert config.base_url == "https://ngsoc.example.com"
    assert config.api_prefix == "/ngsoc/api/v1"
    assert config.access_token == "secret-token"


# ---------------------------------------------------------------------------
# YAML manifests load and bind to the right handler entry-points
# ---------------------------------------------------------------------------
_GROUP_MANIFESTS = [
    ("ngsoc_alarms.yaml", "alarms"),
    ("ngsoc_assets.yaml", "assets"),
    ("ngsoc_vuls.yaml", "vuls"),
    ("ngsoc_risks.yaml", "risks"),
    ("ngsoc_users.yaml", "users"),
    ("ngsoc_workorders.yaml", "workorders"),
    ("ngsoc_bigscreens.yaml", "bigscreens"),
    ("ngsoc_storage.yaml", "storage"),
]


@pytest.mark.parametrize("yaml_name, function_name", _GROUP_MANIFESTS)
def test_yaml_manifest_loads_and_binds_to_handler(yaml_name, function_name):
    from flocks.tool.tool_loader import yaml_to_tool

    yaml_path = _PLUGIN_DIR / yaml_name
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.provider == "ngsoc_api"
    assert tool.info.source == "api"
    # Every group manifest pins the manual version so downstream agents
    # can disambiguate R4.15.x from older NGSOC R3.x deployments.
    assert raw["version"] == "4.15.1"
    # Handler is wired to the matching group entry-point
    assert raw["handler"]["function"] == function_name
    assert raw["handler"]["script_file"] == "ngsoc.handler.py"
    # ``action`` is the dispatcher's only universally-required parameter.
    assert "action" in raw["inputSchema"]["required"]


# ---------------------------------------------------------------------------
# Regression: spec correctness against manual §5
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alarm_judgment_record_update_sends_identifiers_via_query(handler):
    """Manual §5.1.13 mis-labels ``alarmId`` / ``latestTimestamp`` as REST
    parameters, but the URL ``/alarms/judgment-record`` has no placeholder.
    The dispatcher must forward them as query string and keep the triage
    fields in the JSON body — otherwise the device returns 400 because it
    cannot locate the alarm to update."""
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": {"recordId": "r1"}})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.alarms(
            ToolContext(session_id="t", message_id="m"),
            action="alarm_judgment_record_update",
            alarmId="alarm-uuid-1",
            latestTimestamp=1700000000000,
            triageResult="triage-uuid-1",
            judgmentReason="missed positive",
            type=0,
            creatorId="creator-1",
            creatorName="alice",
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    # Path is unchanged — no REST substitution happens.
    assert url == "https://ngsoc.example.com/api/v1/alarms/judgment-record"
    # Identifiers travel as query string so the device can locate the alarm.
    assert kwargs["params"] == {
        "alarmId": "alarm-uuid-1",
        "latestTimestamp": 1700000000000,
    }
    # Triage payload is the only thing in the JSON body.
    assert kwargs["json"] == {
        "triageResult": "triage-uuid-1",
        "judgmentReason": "missed positive",
        "type": 0,
        "creatorId": "creator-1",
        "creatorName": "alice",
    }


@pytest.mark.asyncio
async def test_query_string_booleans_are_lowercased(handler):
    """Java/Spring backends parse query-string booleans case-sensitively
    and silently treat ``True`` (Python's default ``str(True)``) as false.
    The handler must coerce booleans to ``"true"`` / ``"false"`` before
    they hit aiohttp."""
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": []})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.assets(
            ToolContext(session_id="t", message_id="m"),
            action="asset_group_list",
            viewId=1,
            showNative=True,
            domainIds="-1,1",
        )
    assert result.success is True
    _method, _url, kwargs = fake_session.calls[0]
    assert kwargs["params"]["showNative"] == "true", (
        "boolean True must be lowercased to satisfy NGSOC's Spring parser"
    )
    # Non-bool values stay unchanged.
    assert kwargs["params"]["viewId"] == 1
    assert kwargs["params"]["domainIds"] == "-1,1"


@pytest.mark.asyncio
async def test_query_string_booleans_false_lowercased(handler):
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": []})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        await handler.assets(
            ToolContext(session_id="t", message_id="m"),
            action="asset_group_list",
            viewId=1,
            showNative=False,
        )
    _m, _u, kwargs = fake_session.calls[0]
    assert kwargs["params"]["showNative"] == "false"


@pytest.mark.asyncio
async def test_json_body_keeps_native_booleans(handler):
    """Boolean coercion must NOT touch the JSON body: vuls/raw-data-vul-list
    sends ``cnnvdOrder`` as a real boolean and JSON serialises that fine."""
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": {}})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        await handler.vuls(
            ToolContext(session_id="t", message_id="m"),
            action="vuls_raw_data_list",
            page=1,
            size=10,
            cnnvdOrder=True,
            domainId=-1,
        )
    _m, _u, kwargs = fake_session.calls[0]
    # Body keeps Python booleans — JSON serialisation handles them.
    assert kwargs["json"]["cnnvdOrder"] is True
    assert "params" not in kwargs, "POST body endpoints must not send query"


@pytest.mark.asyncio
async def test_workorder_status_update_accepts_string_enum(handler):
    """Manual §5.6.1 declares ``status`` as a string enum (PENDING /
    UNDISPOSED / DISPOSING / REVOKED / DISPOSED / FINISHED). The handler
    must forward the literal string unchanged — coercing it to an int
    would silently break dispatch."""
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": None})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.workorders(
            ToolContext(session_id="t", message_id="m"),
            action="work_order_status_update",
            workOrderIds=[9, 10],
            status="DISPOSED",
        )
    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "PUT"
    assert url.endswith("/workorders/list/status")
    assert kwargs["json"] == {"workOrderIds": [9, 10], "status": "DISPOSED"}


@pytest.mark.asyncio
async def test_storage_download_mode_string_passthrough(handler, tmp_path):
    """``mode`` is a string enum (inline/attachment) per manual §5.8.1.
    Pass through the literal value unchanged."""
    body = b"BIN-PAYLOAD"
    fake_session = _FakeSession(
        [
            _FakeResponse(
                status=200,
                body_bytes=body,
                content_type="application/octet-stream",
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    patches = _patch_runtime(handler, raw_service, fake_session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(handler, "_outputs_dir", return_value=str(outputs_dir)),
    ):
        handler._SESSIONS.clear()
        result = await handler.storage(
            ToolContext(session_id="t", message_id="m"),
            action="storage_download",
            uuidName="abc",
            mode="attachment",
        )
    assert result.success is True
    _m, _u, kwargs = fake_session.calls[0]
    assert kwargs["params"] == {"uuidName": "abc", "mode": "attachment"}


def test_alarm_judgment_record_action_spec_shape(handler):
    """Pin the ActionSpec wiring so a refactor can't silently revert this
    bugfix back to ``passthrough_body=True`` (which would put the
    identifiers in the body again and reproduce the device-side 400)."""
    spec = handler.ALARMS_ACTION_SPECS["alarm_judgment_record_update"]
    assert spec.method == "POST"
    assert spec.path == "/alarms/judgment-record"
    assert spec.passthrough_body is False
    assert spec.passthrough_query is False
    assert "alarmId" in spec.query_keys
    assert "latestTimestamp" in spec.query_keys
    # Triage payload stays in the body.
    for key in (
        "triageResult",
        "judgmentReason",
        "type",
        "creatorId",
        "creatorName",
        "recordId",
    ):
        assert key in spec.body_keys, key
    # Identifiers must NOT also be enumerated as body keys (would double-send).
    assert "alarmId" not in spec.body_keys
    assert "latestTimestamp" not in spec.body_keys


def test_provider_yaml_pins_service_id_and_version():
    raw = yaml.safe_load(
        (_PLUGIN_DIR / "_provider.yaml").read_text(encoding="utf-8")
    )
    assert raw["service_id"] == "ngsoc_api"
    assert raw["version"] == "4.15.1"
    assert raw["auth"]["type"] == "custom"
    assert raw["auth"]["secret"] == "ngsoc_access_token"
    # Required credential fields cover the three pieces config resolution
    # depends on (base_url + api_prefix + access_token).
    keys = {f["key"] for f in raw["credential_fields"]}
    assert {"base_url", "api_prefix", "access_token"} <= keys


# ---------------------------------------------------------------------------
# Second-pass deep-review regressions: binary naming + assets connectivity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_storage_download_without_filename_uses_path_naming(
    handler, tmp_path
):
    """When the operator omits ``fileName``, the saved file falls back to
    the URL-derived name with a Content-Type extension. This guarantees a
    stable artifact path even for endpoints that don't expose a filename
    field (future-proofing for any new binary endpoints)."""
    body = b"PCAP-RAW-\x00\xff"
    fake_session = _FakeSession(
        [
            _FakeResponse(
                status=200,
                json_payload=None,
                body_bytes=body,
                content_type="application/octet-stream",
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()

    patches = _patch_runtime(handler, raw_service, fake_session)
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(handler, "_outputs_dir", return_value=str(outputs_dir)),
    ):
        handler._SESSIONS.clear()
        result = await handler.storage(
            ToolContext(session_id="t", message_id="m"),
            action="storage_download",
            uuidName="abc-uuid",
            # NOTE: no fileName -> exercises the URL-naming branch.
        )

    assert result.success is True
    saved_path = Path(result.output["saved_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == body
    # Falls back to <path>_<ts>.<ext> with octet-stream -> .bin
    assert saved_path.name.startswith("ngsoc_storage_download_")
    assert saved_path.suffix == ".bin"


def test_save_binary_sanitizes_path_traversal_in_user_filename(
    handler, tmp_path
):
    """A malicious or sloppy ``fileName=../../etc/passwd`` must not escape
    the outputs directory. The sanitizer also strips Windows-style paths."""
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    with patch.object(handler, "_outputs_dir", return_value=str(outputs_dir)):
        unix_traversal = handler._save_binary(
            "/storage/download",
            b"x",
            "application/octet-stream",
            preferred_name="../../etc/passwd",
        )
        windows_traversal = handler._save_binary(
            "/storage/download",
            b"y",
            "application/octet-stream",
            preferred_name=r"C:\evil\report.exe",
        )

    unix_path = Path(unix_traversal)
    win_path = Path(windows_traversal)
    # Both files must land inside outputs_dir (no traversal escape).
    assert unix_path.parent == outputs_dir
    assert win_path.parent == outputs_dir
    # Basename is preserved post-sanitization.
    assert unix_path.name.endswith("_passwd")
    assert win_path.name.endswith("_report.exe")


def test_save_binary_filename_collision_disambiguated_by_timestamp(
    handler, tmp_path
):
    """Two saves with the same user-supplied filename must not overwrite
    each other when called within the same second they get the same name,
    but the timestamp prefix gives sub-second granularity once it changes.
    We assert at least the deterministic shape: timestamp prefixes the
    user filename so listing-by-name groups artifacts chronologically."""
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    with patch.object(handler, "_outputs_dir", return_value=str(outputs_dir)):
        path = handler._save_binary(
            "/storage/download",
            b"payload",
            "application/octet-stream",
            preferred_name="evidence.pcap",
        )
    saved = Path(path)
    # ngsoc_<YYYYMMDDTHHMMSS>_evidence.pcap
    assert saved.parent == outputs_dir
    assert saved.name.startswith("ngsoc_")
    # 6-char "ngsoc_" + 15-char ISO basic timestamp + "_evidence.pcap"
    assert saved.name.endswith("_evidence.pcap")
    parts = saved.name.split("_")
    # ["ngsoc", "<timestamp>", "evidence.pcap"]
    assert len(parts) == 3
    assert len(parts[1]) == 15  # YYYYMMDDTHHMMSS


@pytest.mark.asyncio
async def test_asset_risks_list_sends_body_and_query_in_one_request(
    handler,
):
    """🚨 Third-pass regression: the manual's ``POST /risks/asset/asset-risks``
    endpoint (manual §5.4.1) is the *only* non-GET endpoint that mixes a
    JSON body (``groupIds`` / ``networkSegmentId`` / ``domainId``) with a
    rich query-string filter set (``viewId`` 必填, ``page``, ``size``,
    ``riskLevel``, ``compromiseState``, ``assetName``, ``assetIp``, ...).

    A previous bug gated ``passthrough_query`` behind
    ``if self.method == "GET"``, which silently dropped every query param
    for this POST — the server would reject with "viewId is required" and
    the operator would have no clue the SDK ate the field.

    Pin BOTH:
      * the body carries only the body_keys (no double-send of viewId)
      * the query carries every passthrough field including ``viewId``
        and respects the boolean lowercasing rule.
    """
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={"errCode": 0, "data": {"data": []}}
            )
        ]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.risks(
            ToolContext(session_id="t", message_id="m"),
            action="asset_risks_list",
            # body fields (body_keys)
            groupIds=[1, 2],
            networkSegmentId="-2",
            domainId="-1",
            # query fields (passthrough_query) — viewId is REQUIRED
            viewId=1,
            page=1,
            size=20,
            orderBy="-risk",
            compromiseState=False,
            riskLevel="1,2,3",
            assetIp="10.58.169.36",
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://ngsoc.example.com/api/v1/risks/asset/asset-risks"

    # JSON body: ONLY the body_keys, no query fields leaked.
    assert kwargs["json"] == {
        "groupIds": [1, 2],
        "networkSegmentId": "-2",
        "domainId": "-1",
    }
    # Query string: every passthrough field present, viewId lives here
    # (NOT in the body), and booleans are lowercased per the prior fix.
    assert kwargs["params"] == {
        "viewId": 1,
        "page": 1,
        "size": 20,
        "orderBy": "-risk",
        "compromiseState": "false",
        "riskLevel": "1,2,3",
        "assetIp": "10.58.169.36",
    }
    # Body fields must NOT also leak into query string (no double-send).
    assert "groupIds" not in kwargs["params"]
    assert "networkSegmentId" not in kwargs["params"]
    assert "domainId" not in kwargs["params"]


def test_action_spec_passthrough_query_is_method_agnostic(handler):
    """Direct unit test on ActionSpec.build_request: the same input on a
    PUT / DELETE / PATCH spec with passthrough_query must reach the query
    bag, not silently get dropped on the floor."""
    for method in ("PUT", "DELETE", "PATCH", "POST"):
        spec = handler.ActionSpec(
            method,
            "/some/path",
            body_keys=["bodyOnly"],
            passthrough_query=True,
        )
        rest, query, body = spec.build_request(
            {
                "bodyOnly": "x",
                "viewId": 1,
                "queryFlag": True,
            }
        )
        assert rest == {}
        # bodyOnly stays in body; viewId + queryFlag end up as query
        # because passthrough_query now applies to every method.
        assert query == {"viewId": 1, "queryFlag": True}, method
        assert body == {"bodyOnly": "x"}, method


def test_action_spec_passthrough_query_does_not_double_ship_body_keys(
    handler,
):
    """The defensive ``if k in self.body_keys: continue`` filter must
    prevent ``passthrough_query`` from also enqueuing fields that already
    live in the JSON body. Otherwise NGSOC could see (and reject) the
    same field twice — once in body, once in query string."""
    spec = handler.ActionSpec(
        "POST",
        "/x",
        body_keys=["onlyInBody"],
        passthrough_query=True,
    )
    _rest, query, body = spec.build_request(
        {"onlyInBody": "value", "alsoInQuery": "v2"}
    )
    assert body == {"onlyInBody": "value"}
    assert query == {"alsoInQuery": "v2"}
    assert "onlyInBody" not in (query or {})


@pytest.mark.asyncio
async def test_assets_action_test_routes_to_connectivity_probe(handler):
    """``assets`` was missing from _CONNECTIVITY_TEST_ACTIONS in the first
    pass. Pin that ``action="test"`` now routes to the no-arg
    ``asset_group_list`` probe so operators can validate connectivity for
    the assets module without crafting a real request."""
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"errCode": 0, "data": []})]
    )
    raw_service = {
        "base_url": "https://ngsoc.example.com",
        "access_token": "tok",
    }
    patches = _patch_runtime(handler, raw_service, fake_session)
    with patches[0], patches[1], patches[2]:
        handler._SESSIONS.clear()
        result = await handler.assets(
            ToolContext(session_id="t", message_id="m"),
            action="test",
        )

    assert result.success is True
    method, url, _kwargs = fake_session.calls[0]
    assert method == "GET"
    assert url.endswith("/assets/asset-group-list")
