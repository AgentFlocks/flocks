from __future__ import annotations

import importlib.util
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
_PLUGIN_DIR = _ROOT / ".flocks" / "flockshub" / "plugins" / "tools" / "device" / "360_fw_v5_5"
_HANDLER_PATH = _PLUGIN_DIR / "360_fw.handler.py"


def _installed_plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project_root = tmp_path / "project"
    install_dir = project_root / ".flocks" / "plugins" / "tools" / "device" / "360_fw_v5_5"
    shutil.copytree(_PLUGIN_DIR, install_dir)
    monkeypatch.chdir(project_root)
    return install_dir


def _load_handler():
    spec = importlib.util.spec_from_file_location("_test_360_fw_handler", _HANDLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_metadata_declares_360_fw_v5_5_device_plugin():
    raw = yaml.safe_load((_PLUGIN_DIR / "_provider.yaml").read_text(encoding="utf-8"))

    assert raw["name"] == "360_fw"
    assert raw["service_id"] == "360_fw"
    assert raw["version"] == "5.5"
    assert raw["integration_type"] == "device"
    assert raw["description_cn"]
    assert derive_storage_key(raw["service_id"], raw["version"]) == "360_fw_v5_5"
    assert raw["defaults"]["product_version"] == "5.5"
    assert raw["defaults"]["fw_software_version"] == "V5.5"
    assert raw["defaults"]["version_software"] == "V5.5R605P000B20240625"
    assert "allow_mutation" not in raw["defaults"]
    assert "allow_dangerous_ops" not in raw["defaults"]

    credential_keys = {field["key"] for field in raw["credential_fields"]}
    assert {"base_url", "username", "password"} <= credential_keys
    secret_ids = {field.get("secret_id") for field in raw["credential_fields"]}
    assert {"360_fw_v5_5_username", "360_fw_v5_5_password"} <= secret_ids


def test_probe_manifest_declares_connectivity_and_fixtures():
    raw = yaml.safe_load((_PLUGIN_DIR / "_test.yaml").read_text(encoding="utf-8"))

    assert raw["connectivity"]["tool"] == "360_fw_system"
    assert raw["connectivity"]["params"] == {"action": "fw_check_login"}

    expected_tools = {
        "360_fw_system",
        "360_fw_objects",
        "360_fw_policy",
        "360_fw_network",
        "360_fw_vpn_bgp",
        "360_fw_auth_security",
        "360_fw_observability",
        "360_fw_api_readonly",
        "360_fw_api_mutation",
    }
    for tool_name in expected_tools:
        assert raw["fixtures"][tool_name], tool_name


@pytest.mark.parametrize(
    ("yaml_name", "function_name", "requires_confirmation"),
    [
        ("360_fw_system.yaml", "system", False),
        ("360_fw_objects.yaml", "objects", True),
        ("360_fw_policy.yaml", "policy", True),
        ("360_fw_network.yaml", "network", True),
        ("360_fw_vpn_bgp.yaml", "vpn_bgp", True),
        ("360_fw_auth_security.yaml", "auth_security", True),
        ("360_fw_observability.yaml", "observability", False),
        ("360_fw_api_readonly.yaml", "api_readonly", False),
        ("360_fw_api_mutation.yaml", "api_mutation", True),
    ],
)
def test_group_manifest_loads_as_device_tool(
    yaml_name: str,
    function_name: str,
    requires_confirmation: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    install_dir = _installed_plugin_dir(tmp_path, monkeypatch)
    yaml_path = install_dir / yaml_name
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.provider == "360_fw_v5_5"
    assert tool.info.source == "device"
    assert tool.info.provider_version == "5.5"
    assert raw["provider"] == "360_fw"
    assert raw["handler"]["script_file"] == "360_fw.handler.py"
    assert raw["handler"]["function"] == function_name
    assert raw["requires_confirmation"] is requires_confirmation
    assert "action" in raw["inputSchema"]["required"]
    assert raw["inputSchema"]["properties"]["action"]["enum"]


def test_runtime_config_resolves_configwriter_and_secret_refs(monkeypatch):
    handler = _load_handler()
    raw_service = {
        "base_url": "https://fw.example.com/API/",
        "username": "{secret:360_fw_v5_5_username}",
        "password": "{secret:360_fw_v5_5_password}",
        "timeout": "12",
        "verify_ssl": "true",
    }
    secrets = {
        "360_fw_v5_5_username": "admin",
        "360_fw_v5_5_password": "pass",
    }

    monkeypatch.setattr(
        handler.ConfigWriter,
        "get_api_service_raw",
        staticmethod(lambda service_id: raw_service if service_id == "360_fw" else None),
    )
    monkeypatch.setattr(handler, "get_secret_manager", lambda: SimpleNamespace(get=secrets.get))

    config = handler._load_runtime_config()

    assert config.base_url == "https://fw.example.com/API"
    assert config.username == "admin"
    assert config.password == "pass"
    assert config.timeout == 12
    assert config.verify_ssl is True


def test_client_cache_key_does_not_store_plaintext_password():
    handler = _load_handler()
    config = handler.RuntimeConfig(
        base_url="https://fw.example.com/API",
        username="admin",
        password="secret-password",
        verify_ssl=False,
        timeout=30,
    )

    key = handler._client_cache_key(config)

    assert "secret-password" not in key
    assert key == ("https://fw.example.com/API", "admin", False)


def test_login_uses_user_pwd_and_raw_authorization_header():
    handler = _load_handler()
    config = handler.RuntimeConfig(
        base_url="https://fw.example.com/API",
        username="admin",
        password="secret",
        verify_ssl=False,
        timeout=30,
    )
    client = handler.FwClient(config)
    calls: list[tuple[str, str, dict[str, Any] | None, Any]] = []

    class _Response:
        status_code = 200
        text = '{"result": true, "authorization": "raw-token"}'

        def json(self):
            return {"result": True, "authorization": "raw-token"}

    def fake_request(method: str, url: str, **kwargs: Any):
        calls.append((method, url, kwargs.get("json"), kwargs.get("headers")))
        return _Response()

    client.session.request = fake_request
    result = client.login()

    assert result == {"result": True, "authorization": "***"}
    assert calls == [("POST", "https://fw.example.com/API/login", {"user": "admin", "pwd": "secret"}, None)]
    assert client.session.headers["Authorization"] == "raw-token"
    assert "Bearer" not in client.session.headers["Authorization"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/save_config"),
        ("POST", "/change_password"),
        ("POST", "/config_clear_common"),
        ("POST", "/restart"),
        ("POST", "/restore"),
        ("POST", "/library_upgrade"),
        ("PUT", "/license_config"),
        ("PUT", "/ha_config"),
        ("PUT", "/global_domain_block_switch"),
        ("DELETE", "/session_monitor"),
        ("POST", "/bgp_clear_bgp_route"),
        ("POST", "/user_obj"),
        ("POST", "/signature_event"),
    ],
)
def test_raw_mutation_rejects_high_risk_fw_device_state_changes(
    monkeypatch, method: str, path: str
):
    handler = _load_handler()
    monkeypatch.setattr(handler, "get_client", lambda: pytest.fail("blocked raw mutation must not call FW"))

    with pytest.raises(handler.FwApiError, match="does not support high-risk FW operations"):
        handler.fw_call_mutation({"method": method, "path": path, "body": "{}"})


def test_api_catalog_contains_full_fw_surface_and_known_problem_metadata():
    handler = _load_handler()

    catalog = handler.fw_api_catalog({}).output

    resources = catalog["documented_rest_api_resources"]
    assert resources["/sys_info"] == ["GET"]
    assert {"GET", "POST", "PUT", "DELETE"} <= set(resources["/addressobj"])
    assert {"GET", "POST", "PUT", "DELETE"} <= set(resources["/fwpolicy"])
    assert {"GET", "POST", "DELETE"} <= set(resources["/bgp_info"])
    assert "/save_config" in catalog["blocked_high_risk_resources"]
    assert catalog["known_problem_resources"]["/domainBlackList"]["GET"]["http_status"] == 404


@pytest.mark.asyncio
async def test_api_readonly_group_dispatches_to_fw_get(monkeypatch):
    handler = _load_handler()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeClient:
        def get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
            calls.append((path, query))
            return {"result": True, "data": {"host_name": "FW-1"}}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    result: ToolResult = await handler.api_readonly(
        ToolContext(session_id="s", message_id="m"),
        action="fw_call_raw_readonly",
        path="/sys_info",
    )

    assert result.success is True
    assert result.output == {"result": True, "data": {"host_name": "FW-1"}}
    assert calls == [("/sys_info", None)]


@pytest.mark.asyncio
async def test_specialized_actions_build_expected_fw_payloads(monkeypatch):
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
            return {"result": True, "data": [{"id": 101, "name": body.get("name") if isinstance(body, dict) else None}]}

    monkeypatch.setattr(handler, "get_client", lambda: _FakeClient())

    ctx = ToolContext(session_id="s", message_id="m")
    address = await handler.objects(
        ctx,
        action="fw_addressobj_create",
        name="tmp_addr",
        addr="198.18.0.10",
        desc="temp",
    )
    service = await handler.objects(
        ctx,
        action="fw_serviceobj_create",
        name="tmp_svc",
        sev_str="TCP/1-65535:65000-65001",
    )
    route = await handler.network(
        ctx,
        action="fw_static_route_create",
        dst_ip="198.51.100.252/32",
        nh_ip="198.18.10.2",
    )

    assert address.success is True
    assert service.success is True
    assert route.success is True
    assert calls == [
        ("POST", "/addressobj", None, {"name": "tmp_addr", "type": 0, "desc": "temp", "item": [{"addr": "0:198.18.0.10"}]}),
        ("POST", "/serviceobj", None, {"name": "tmp_svc", "desc": "", "item": [{"sev_str": "TCP/1-65535:65000-65001"}]}),
        ("POST", "/static_route?protocol=1", None, {"ip_vrf_name": "default", "dst_ip": "198.51.100.252/32", "nh_type": "0", "nh_ip": "198.18.10.2", "oif": "", "weigh": "1", "distance": "255", "monitor_name": ""}),
    ]
