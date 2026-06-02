from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from flocks.config.api_versioning import derive_storage_key
from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.tool_loader import yaml_to_tool


_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _ROOT / ".flocks" / "flockshub" / "plugins" / "tools" / "device" / "360_waf_v5_5"
_HANDLER_PATH = _PLUGIN_DIR / "360_waf.handler.py"


def _installed_plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project_root = tmp_path / "project"
    install_dir = project_root / ".flocks" / "plugins" / "tools" / "device" / "360_waf_v5_5"
    shutil.copytree(_PLUGIN_DIR, install_dir)
    monkeypatch.chdir(project_root)
    return install_dir


def _load_handler():
    spec = importlib.util.spec_from_file_location("_test_360_waf_handler", _HANDLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_metadata_declares_360_waf_v5_5_device_plugin():
    raw = yaml.safe_load((_PLUGIN_DIR / "_provider.yaml").read_text(encoding="utf-8"))

    assert raw["name"] == "360_waf"
    assert raw["service_id"] == "360_waf"
    assert raw["version"] == "5.5"
    assert raw["integration_type"] == "device"
    assert raw["description_cn"]
    assert derive_storage_key(raw["service_id"], raw["version"]) == "360_waf_v5_5"
    assert "allow_mutation" not in raw["defaults"]
    assert "allow_dangerous_ops" not in raw["defaults"]

    credential_keys = {field["key"] for field in raw["credential_fields"]}
    assert {"base_url", "username", "password"} <= credential_keys


def test_probe_manifest_declares_connectivity_and_fixtures():
    raw = yaml.safe_load((_PLUGIN_DIR / "_test.yaml").read_text(encoding="utf-8"))

    assert raw["connectivity"]["tool"] == "360_waf_system"
    assert raw["connectivity"]["params"] == {"action": "waf_check_login"}

    expected_tools = {
        "360_waf_system",
        "360_waf_site",
        "360_waf_policy_ops",
        "360_waf_observability",
        "360_waf_api_readonly",
        "360_waf_api_mutation",
        "360_waf_file",
    }
    for tool_name in expected_tools:
        assert raw["fixtures"][tool_name], tool_name


def test_mutation_manifest_uses_json_string_body_without_framework_schema_extensions():
    raw = yaml.safe_load(
        (_PLUGIN_DIR / "360_waf_api_mutation.yaml").read_text(encoding="utf-8")
    )

    body_schema = raw["inputSchema"]["properties"]["body"]

    assert raw["requires_confirmation"] is True
    assert body_schema["type"] == "string"
    assert "oneOf" not in body_schema
    assert "confirm" not in raw["inputSchema"]["properties"]


def test_json_payload_strings_are_parsed_by_handler():
    handler = _load_handler()
    payload = [
        {
            "siteId": 2147483647,
            "type": 1,
            "content": "192.0.2.236",
            "is_permanent": "1",
        }
    ]
    object_payload = {"conditions": []}

    assert handler.require_payload(json.dumps(payload)) == payload
    assert handler.require_payload(json.dumps(object_payload)) == object_payload
    assert handler.optional_payload("") is None


def test_policy_ops_manifest_exposes_business_mutation_actions():
    raw = yaml.safe_load((_PLUGIN_DIR / "360_waf_policy_ops.yaml").read_text(encoding="utf-8"))

    action_enum = set(raw["inputSchema"]["properties"]["action"]["enum"])
    body_schema = raw["inputSchema"]["properties"]["body"]

    assert raw["requires_confirmation"] is True
    assert {
        "waf_blacklist_create",
        "waf_blacklist_delete",
        "waf_site_global_blacklist_create",
        "waf_site_global_blacklist_delete",
        "waf_whitelist_create",
        "waf_whitelist_delete",
        "waf_site_global_whitelist_create",
        "waf_site_global_whitelist_delete",
        "waf_exception_rule_create",
        "waf_exception_rule_update",
        "waf_exception_rule_delete",
    } <= action_enum
    assert body_schema["type"] == "string"
    assert "oneOf" not in body_schema
    assert "confirm" not in raw["inputSchema"]["properties"]


def test_observability_manifest_exposes_log_query_filters():
    raw = yaml.safe_load((_PLUGIN_DIR / "360_waf_observability.yaml").read_text(encoding="utf-8"))

    action_enum = set(raw["inputSchema"]["properties"]["action"]["enum"])
    properties = raw["inputSchema"]["properties"]

    assert "waf_configuration_log_search" in action_enum
    assert {"time_start", "time_end", "http_url", "action_filter", "msg"} <= set(properties)


@pytest.mark.parametrize(
    ("yaml_name", "function_name"),
    [
        ("360_waf_system.yaml", "system"),
        ("360_waf_site.yaml", "site"),
        ("360_waf_policy_ops.yaml", "policy_ops"),
        ("360_waf_observability.yaml", "observability"),
        ("360_waf_api_readonly.yaml", "api_readonly"),
        ("360_waf_api_mutation.yaml", "api_mutation"),
        ("360_waf_file.yaml", "file_ops"),
    ],
)
def test_group_manifest_loads_as_device_tool(
    yaml_name: str,
    function_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    install_dir = _installed_plugin_dir(tmp_path, monkeypatch)
    yaml_path = install_dir / yaml_name
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.provider == "360_waf_v5_5"
    assert tool.info.source == "device"
    assert tool.info.provider_version == "5.5"
    assert raw["provider"] == "360_waf"
    assert raw["handler"]["script_file"] == "360_waf.handler.py"
    assert raw["handler"]["function"] == function_name
    assert "action" in raw["inputSchema"]["required"]
    assert raw["inputSchema"]["properties"]["action"]["enum"]


def test_group_manifests_use_official_confirmation_flags():
    expected = {
        "360_waf_system.yaml": False,
        "360_waf_site.yaml": False,
        "360_waf_policy_ops.yaml": True,
        "360_waf_observability.yaml": False,
        "360_waf_api_readonly.yaml": False,
        "360_waf_api_mutation.yaml": True,
        "360_waf_file.yaml": True,
    }

    for yaml_name, requires_confirmation in expected.items():
        raw = yaml.safe_load((_PLUGIN_DIR / yaml_name).read_text(encoding="utf-8"))
        assert raw["requires_confirmation"] is requires_confirmation


def test_runtime_config_resolves_configwriter_and_secret_refs(monkeypatch):
    handler = _load_handler()
    raw_service = {
        "base_url": "https://waf.example.com/",
        "username": "{secret:360_waf_v5_5_username}",
        "password": "{secret:360_waf_v5_5_password}",
        "timeout": "12",
        "verify_ssl": "true",
    }
    secrets = {
        "360_waf_v5_5_username": "admin",
        "360_waf_v5_5_password": "pass",
    }

    monkeypatch.setattr(
        handler.ConfigWriter,
        "get_api_service_raw",
        staticmethod(lambda service_id: raw_service if service_id == "360_waf" else None),
    )
    monkeypatch.setattr(handler, "get_secret_manager", lambda: SimpleNamespace(get=secrets.get))

    config = handler._load_runtime_config()

    assert config.base_url == "https://waf.example.com"
    assert config.username == "admin"
    assert config.password == "pass"
    assert config.timeout == 12
    assert config.verify_ssl is True


def test_client_cache_key_does_not_store_plaintext_password():
    handler = _load_handler()
    config = handler.RuntimeConfig(
        base_url="https://waf.example.com",
        username="admin",
        password="secret-password",
        verify_ssl=False,
        timeout=30,
    )

    key = handler._client_cache_key(config)

    assert "secret-password" not in key
    assert key == ("https://waf.example.com", "admin", False)


def test_ssl_cipher_downgrade_only_for_unverified_connections(monkeypatch):
    handler = _load_handler()

    class _FakeContext:
        def __init__(self) -> None:
            self.ciphers: list[str] = []

        def set_ciphers(self, value: str) -> None:
            self.ciphers.append(value)

    verified = _FakeContext()
    unverified = _FakeContext()
    monkeypatch.setattr(handler.ssl, "create_default_context", lambda: verified)
    monkeypatch.setattr(handler.ssl, "_create_unverified_context", lambda: unverified)

    base = {
        "base_url": "https://waf.example.com",
        "username": "admin",
        "password": "secret",
        "timeout": 30,
    }

    handler.WafClient(handler.RuntimeConfig(**base, verify_ssl=True))
    handler.WafClient(handler.RuntimeConfig(**base, verify_ssl=False))

    assert verified.ciphers == []
    assert unverified.ciphers == ["DEFAULT:@SECLEVEL=0"]


@pytest.mark.asyncio
async def test_unified_ops_runs_sync_handlers_in_worker_thread(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    async def fake_to_thread(func: Any, *args: Any) -> ToolResult:
        calls.append((func, args))
        return func(*args)

    def fake_handler(params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=params)

    monkeypatch.setattr(handler.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setitem(handler._ACTION_MAP, "fake_thread_action", fake_handler)

    result = await handler.unified_ops(
        ToolContext(session_id="s", message_id="m"),
        action="fake_thread_action",
        value=1,
    )

    assert result.success is True
    assert result.output == {"value": 1}
    assert calls == [(fake_handler, ({"value": 1},))]


@pytest.mark.asyncio
async def test_api_readonly_group_dispatches_to_original_waf_action(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeClient:
        def call_readonly(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
            calls.append((path, query))
            return {"success": True, "result": [{"hostname": "waf01"}]}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    result: ToolResult = await handler.api_readonly(
        ToolContext(session_id="s", message_id="m"),
        action="waf_call_raw_readonly",
        path="rest/api/sysinfo",
        query={"conditions": []},
    )

    assert result.success is True
    assert result.output == {"success": True, "result": [{"hostname": "waf01"}]}
    assert calls == [("/rest/api/sysinfo", {"conditions": []})]


@pytest.mark.asyncio
async def test_policy_ops_builds_blacklist_and_whitelist_payloads(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, str, dict[str, Any] | None, Any]] = []

    class _FakeClient:
        def request(
            self,
            method: str,
            path: str,
            query: dict[str, Any] | None = None,
            body: Any = None,
        ) -> dict[str, Any]:
            calls.append((method, path, query, body))
            return {"success": True, "result": []}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    ctx = ToolContext(session_id="s", message_id="m")
    create_blacklist = await handler.policy_ops(
        ctx,
        action="waf_blacklist_create",
        siteId=2147483647,
        content="192.0.2.10",
    )
    delete_blacklist = await handler.policy_ops(
        ctx,
        action="waf_blacklist_delete",
        siteId=2147483647,
        content="192.0.2.10",
    )
    create_whitelist = await handler.policy_ops(
        ctx,
        action="waf_whitelist_create",
        id=2147483647,
        ip_start="192.0.2.11",
        desc="allow scanner",
    )
    delete_whitelist = await handler.policy_ops(
        ctx,
        action="waf_whitelist_delete",
        id=2147483647,
        ip_start="192.0.2.11",
    )

    assert create_blacklist.success is True
    assert delete_blacklist.success is True
    assert create_whitelist.success is True
    assert delete_whitelist.success is True
    assert calls == [
        (
            "POST",
            "/rest/api/blacklist",
            None,
            [{"siteId": 2147483647, "type": 1, "content": "192.0.2.10", "is_permanent": "1"}],
        ),
        (
            "DELETE",
            "/rest/api/blacklist",
            None,
            [{"siteId": 2147483647, "type": 1, "content": "192.0.2.10"}],
        ),
        (
            "POST",
            "/rest/api/whitelist",
            None,
            {
                "id": 2147483647,
                "ip_whitelist": {
                    "ip_ver": "0",
                    "type": "0",
                    "ip_start": "192.0.2.11",
                    "desc": "allow scanner",
                },
            },
        ),
        (
            "DELETE",
            "/rest/api/whitelist",
            None,
            {
                "id": 2147483647,
                "ip_whitelist": {
                    "ip_ver": "0",
                    "type": "0",
                    "ip_start": "192.0.2.11",
                    "ip_end": "0",
                    "netmask": 32,
                },
            },
        ),
    ]


@pytest.mark.asyncio
async def test_policy_ops_builds_global_list_and_exception_payloads(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, str, dict[str, Any] | None, Any]] = []

    class _FakeClient:
        def request(
            self,
            method: str,
            path: str,
            query: dict[str, Any] | None = None,
            body: Any = None,
        ) -> dict[str, Any]:
            calls.append((method, path, query, body))
            return {"success": True, "result": []}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    ctx = ToolContext(session_id="s", message_id="m")
    await handler.policy_ops(
        ctx,
        action="waf_site_global_blacklist_create",
        content="192.0.2.12",
        is_permanent=0,
        block_time=120,
    )
    await handler.policy_ops(
        ctx,
        action="waf_site_global_blacklist_delete",
        content="192.0.2.12",
        is_permanent=0,
    )
    await handler.policy_ops(
        ctx,
        action="waf_site_global_whitelist_create",
        ip_start="192.0.2.13",
    )
    await handler.policy_ops(
        ctx,
        action="waf_site_global_whitelist_delete",
        ip_start="192.0.2.13",
    )
    payload = {"rule_id": "1000000015", "protection_sub_type": "10000"}
    await handler.policy_ops(ctx, action="waf_exception_rule_create", body=payload)
    await handler.policy_ops(ctx, action="waf_exception_rule_update", body=payload)
    await handler.policy_ops(ctx, action="waf_exception_rule_delete", body=payload)

    assert calls == [
        (
            "POST",
            "/rest/api/site_global_blacklist",
            None,
            [{"type": 1, "content": "192.0.2.12", "is_permanent": "0", "block_time": 120}],
        ),
        (
            "DELETE",
            "/rest/api/site_global_blacklist",
            None,
            [{"type": 1, "content": "192.0.2.12"}],
        ),
        (
            "POST",
            "/rest/api/site_global_whitelist",
            None,
            [{"type": "0", "ip_ver": "0", "ip_start": "192.0.2.13"}],
        ),
        (
            "DELETE",
            "/rest/api/site_global_whitelist",
            None,
            [{"type": "0", "ip_ver": "0", "ip_start": "192.0.2.13", "ip_end": "0", "netmask": 32}],
        ),
        ("POST", "/rest/api/exceptionlist", None, payload),
        ("PUT", "/rest/api/exceptionlist", None, payload),
        ("DELETE", "/rest/api/exceptionlist", None, payload),
    ]


@pytest.mark.asyncio
async def test_observability_filters_security_and_configuration_logs(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeClient:
        def get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
            calls.append((path, query))
            return {"success": True, "result": []}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    ctx = ToolContext(session_id="s", message_id="m")
    security_result = await handler.observability(
        ctx,
        action="waf_security_log_search",
        time_start="2026/05/29 08:00:00",
        time_end="2026/05/29 09:00:00",
        http_url="/login",
        action_filter="deny",
        start=5,
        limit=10,
    )
    config_result = await handler.observability(
        ctx,
        action="waf_configuration_log_search",
        time_start="2026/05/29 08:00:00",
        time_end="2026/05/29 09:00:00",
        msg="blacklist",
        start=0,
        limit=20,
    )

    assert security_result.success is True
    assert config_result.success is True
    assert calls == [
        (
            "/rest/api/websecuritylog",
            {
                "conditions": [
                    {"field": "time_start", "operator": 0, "value": "2026/05/29 08:00:00"},
                    {"field": "time_end", "operator": 0, "value": "2026/05/29 09:00:00"},
                    {"field": "http_url", "operator": 0, "value": "/login"},
                    {"field": "action", "operator": 0, "value": "deny"},
                ],
                "start": 5,
                "limit": 10,
            },
        ),
        (
            "/rest/api/configurationlog",
            {
                "lifeTime": {
                    "interval": "custom",
                    "start": "2026/05/29 08:00:00",
                    "end": "2026/05/29 09:00:00",
                },
                "conditions": [{"field": "msg", "operator": 0, "value": "blacklist"}],
                "start": 0,
                "limit": 20,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/rest/api/reboot_system"),
        ("POST", "/rest/api/mgmt_image"),
        ("DELETE", "/rest/api/mgmt_image"),
        ("POST", "/rest/api/signature"),
        ("PUT", "/rest/api/waf_deploy_mode"),
        ("PUT", "/rest/api/licenseManagementAgent"),
        ("POST", "/rest/api/interface"),
        ("DELETE", "/rest/api/zone"),
    ],
)
def test_raw_mutation_rejects_waf_device_state_changes(monkeypatch, method: str, path: str):
    handler = _load_handler()
    monkeypatch.setattr(handler, "get_client", lambda: pytest.fail("blocked raw mutation must not call WAF"))

    with pytest.raises(handler.WafApiError, match="does not support modifying WAF device state"):
        handler.waf_call_mutation({"method": method, "path": path, "body": []})


@pytest.mark.parametrize(
    ("action", "params"),
    [
        (
            "waf_file_upload",
            {
                "path": "/rest/file/signature_import",
                "file_path": "signature.dat",
            },
        ),
        (
            "waf_file_request",
            {"method": "DELETE", "path": "/rest/file?fileName=tmp"},
        ),
    ],
)
def test_file_ops_reject_upgrade_and_import_helpers(monkeypatch, action: str, params: dict[str, Any]):
    handler = _load_handler()
    monkeypatch.setattr(handler, "get_client", lambda: pytest.fail("blocked file helper must not call WAF"))

    with pytest.raises(handler.WafApiError, match="does not support WAF upgrade or import file operations"):
        handler._ACTION_MAP[action](params)


@pytest.mark.asyncio
async def test_observability_test_action_uses_readonly_security_log_probe(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeClient:
        def get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
            calls.append((path, query))
            return {"success": True, "result": []}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    result: ToolResult = await handler.observability(
        ToolContext(session_id="s", message_id="m"),
        action="test",
    )

    assert result.success is True
    assert calls == [
        (
            "/rest/api/websecuritylog",
            {"conditions": [{"field": "interval", "operator": 0, "value": "hour"}], "start": 0, "limit": 50},
        )
    ]


@pytest.mark.asyncio
async def test_file_ops_test_action_returns_clear_no_probe_error(monkeypatch):
    handler = _load_handler()
    monkeypatch.setattr(
        handler,
        "get_client",
        lambda: pytest.fail("file_ops action=test must not touch the WAF"),
    )

    result: ToolResult = await handler.file_ops(
        ToolContext(session_id="s", message_id="m"),
        action="test",
    )

    assert result.success is False
    assert "does not define a zero-argument connectivity probe" in result.error
