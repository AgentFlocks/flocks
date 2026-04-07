import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.tool_loader import _read_yaml_raw, yaml_to_tool

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_TDP_HANDLER = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp.handler.py"
_SKYEYE_HANDLER = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/skyeye_api/skyeye.handler.py"


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="test")


async def test_tdp_incident_timeline_requires_incident_id():
    module = _load_module("test_tdp_handler_incident", _TDP_HANDLER)

    result = await module.incident_list(_ctx(), action="timeline")

    assert result.success is False
    assert "incident_id" in result.error


async def test_tdp_incident_alert_search_requires_page():
    module = _load_module("test_tdp_handler_incident_alert_page", _TDP_HANDLER)

    result = await module.incident_list(_ctx(), action="alert_search", alert_ids=["alert-1"])

    assert result.success is False
    assert "page" in result.error


async def test_tdp_alert_host_events_requires_asset_machine():
    module = _load_module("test_tdp_handler_alert_host", _TDP_HANDLER)

    result = await module.threat_host_list(_ctx(), action="events", condition={})

    assert result.success is False
    assert "condition.asset_machine" in result.error


async def test_tdp_platform_asset_delete_requires_non_empty_list():
    module = _load_module("test_tdp_handler_platform", _TDP_HANDLER)

    result = await module.platform_config(_ctx(), action="asset_delete")

    assert result.success is False
    assert "asset IP list" in result.error


async def test_tdp_platform_white_rule_delete_requires_id():
    module = _load_module("test_tdp_handler_platform_white_rule_delete", _TDP_HANDLER)

    result = await module.platform_config(_ctx(), action="white_rule_delete", rule={})

    assert result.success is False
    assert "id" in result.error


async def test_tdp_platform_cascade_children_maps_keyword_to_root_payload():
    module = _load_module("test_tdp_handler_platform_cascade", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.platform_config(_ctx(), action="cascade_children", keyword="node-001")

    assert result.success is True
    mock_run.assert_awaited_once()
    default_action = mock_run.await_args.kwargs["default_action"]
    action = mock_run.await_args.kwargs["action"]
    body = mock_run.await_args.kwargs["body"]
    assert default_action == "asset_list"
    assert action == "cascade_children"
    assert body == {"keyword": "node-001"}


async def test_tdp_policy_ip_reputation_delete_requires_non_empty_ids():
    module = _load_module("test_tdp_handler_policy", _TDP_HANDLER)

    result = await module.policy_settings(_ctx(), action="ip_reputation_delete")

    assert result.success is False
    assert "ID list" in result.error


async def test_tdp_policy_custom_intel_add_requires_required_fields():
    module = _load_module("test_tdp_handler_policy_custom_intel_required", _TDP_HANDLER)

    result = await module.policy_settings(_ctx(), action="custom_intel_add", main_tag="auto_domain", severity=4)

    assert result.success is False
    assert "ioc_type" in result.error
    assert "ioc_list" in result.error


async def test_tdp_policy_custom_intel_add_maps_object_ioc_list():
    module = _load_module("test_tdp_handler_policy_custom_intel_mapping", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})
    ioc_list = [{"ioc": "aaa.com"}, {"ioc": "bbb.com"}]

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.policy_settings(
            _ctx(),
            action="custom_intel_add",
            ioc_type="DOMAIN",
            ioc_list=ioc_list,
            main_tag="auto_domain",
            severity=4,
            overwrite=True,
        )

    assert result.success is True
    mock_run.assert_awaited_once()
    action = mock_run.await_args.kwargs["action"]
    body = mock_run.await_args.kwargs["body"]
    assert action == "custom_intel_add"
    assert body["ioc_type"] == "DOMAIN"
    assert body["ioc_list"] == ioc_list
    assert body["main_tag"] == "auto_domain"
    assert body["severity"] == 4
    assert body["overwrite"] is True


async def test_tdp_policy_ip_reputation_add_requires_non_empty_ip_list():
    module = _load_module("test_tdp_handler_policy_ip_add", _TDP_HANDLER)

    result = await module.policy_settings(_ctx(), action="ip_reputation_add")

    assert result.success is False
    assert "IP list" in result.error


async def test_tdp_policy_bypass_block_delete_requires_block_ip():
    module = _load_module("test_tdp_handler_policy_bypass_block_delete", _TDP_HANDLER)

    result = await module.policy_settings(_ctx(), action="bypass_block_delete", entry={})

    assert result.success is False
    assert "block IP list" in result.error


async def test_tdp_policy_resolve_host_requires_assets_machine_status_and_sub_status():
    module = _load_module("test_tdp_handler_policy_resolve_host", _TDP_HANDLER)

    result_missing_fields = await module.policy_settings(_ctx(), action="resolve_host", entry={})
    result_missing_sub_status = await module.policy_settings(
        _ctx(),
        action="resolve_host",
        entry={"assets_machine": ["default__10.0.0.1"], "status": 3},
    )

    assert result_missing_fields.success is False
    assert "assets_machine" in result_missing_fields.error
    assert "status" in result_missing_fields.error
    assert result_missing_sub_status.success is False
    assert "sub_status" in result_missing_sub_status.error


async def test_tdp_incident_alert_search_maps_explicit_params_to_condition():
    module = _load_module("test_tdp_handler_incident_mapping", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.incident_list(
            _ctx(),
            action="alert_search",
            alert_ids=["alert-1"],
            include_risk=True,
            include_action=False,
            time_from=1700000000,
            time_to=1700600000,
            page={"cur_page": 2, "page_size": 5},
        )

    assert result.success is True
    mock_run.assert_awaited_once()
    default_action = mock_run.await_args.kwargs["default_action"]
    action = mock_run.await_args.kwargs["action"]
    body = mock_run.await_args.kwargs["body"]
    assert default_action == "search"
    assert action == "alert_search"
    assert body["condition"]["id"] == ["alert-1"]
    assert body["condition"]["include_risk"] is True
    assert body["condition"]["include_action"] is False
    assert body["condition"]["time_from"] == 1700000000
    assert body["condition"]["time_to"] == 1700600000
    assert body["page"] == {"cur_page": 2, "page_size": 5}


async def test_tdp_log_terms_maps_explicit_params_to_root_payload():
    module = _load_module("test_tdp_handler_log_mapping", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.log_search(
            _ctx(),
            action="terms",
            term="src_ip",
            size=25,
            sql="status = 500",
            log_ip="10.0.0.8",
            net_data_type=["http"],
            cascade_asset_group={"device-1": [0, 237]},
        )

    assert result.success is True
    mock_run.assert_awaited_once()
    default_action = mock_run.await_args.kwargs["default_action"]
    action = mock_run.await_args.kwargs["action"]
    body = mock_run.await_args.kwargs["body"]
    assert default_action == "search"
    assert action == "terms"
    assert body["term"] == "src_ip"
    assert body["size"] == 25
    assert body["sql"] == "status = 500"
    assert body["log_ip"] == "10.0.0.8"
    assert body["net_data_type"] == ["http"]
    assert body["cascade_asset_group"] == {"device-1": [0, 237]}


async def test_skyeye_alarm_list_forwards_extended_filters():
    module = _load_module("test_skyeye_handler_alarm_list", _SKYEYE_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_request_json", AsyncMock(return_value=mock_result)) as mock_request:
        result = await module.alarm_list(
            _ctx(),
            threat_type="web_attack",
            serial_num="sensor-1",
            alarm_sip="10.0.0.1",
            attack_sip="1.1.1.1",
            attack_stage="recon",
            asset_group="237",
            is_alarm_black_ip=1,
            limit=10,
        )

    assert result.success is True
    mock_request.assert_awaited_once()
    endpoint, params, api_name = mock_request.await_args.args
    assert endpoint == "alarm/alarm/list"
    assert api_name == "alarm_alarm_list"
    assert params["threat_type"] == "web_attack"
    assert params["serial_num"] == "sensor-1"
    assert params["alarm_sip"] == "10.0.0.1"
    assert params["attack_sip"] == "1.1.1.1"
    assert params["attack_stage"] == "recon"
    assert params["asset_group"] == "237"
    assert params["is_alarm_black_ip"] == 1
    assert params["limit"] == 10


def test_tdp_incident_yaml_loads_with_provider():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp_incident_list.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "tdp_incident_list"
    assert tool.info.provider == "tdp_api"
    assert "body" not in raw["inputSchema"]["properties"]
    assert "condition" in raw["inputSchema"]["properties"]


def test_tdp_platform_yaml_uses_keyword_and_requires_confirmation():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp_platform_config.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "tdp_platform_config"
    assert tool.info.provider == "tdp_api"
    assert raw["requires_confirmation"] is True
    assert "keyword" in raw["inputSchema"]["properties"]
    assert "device_id" not in raw["inputSchema"]["properties"]


def test_tdp_policy_yaml_requires_confirmation_and_uses_object_ioc_list():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp_policy_settings.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "tdp_policy_settings"
    assert tool.info.provider == "tdp_api"
    assert raw["requires_confirmation"] is True
    assert raw["inputSchema"]["properties"]["ioc_list"]["items"]["type"] == "object"
    assert raw["inputSchema"]["properties"]["severity"]["type"] == "integer"


def test_tdp_log_yaml_uses_object_columns_and_supports_cascade_asset_group():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp_log_search.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "tdp_log_search"
    assert tool.info.provider == "tdp_api"
    assert raw["inputSchema"]["properties"]["columns"]["items"]["type"] == "object"
    assert "cascade_asset_group" in raw["inputSchema"]["properties"]


def test_skyeye_alarm_list_yaml_loads_with_provider():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/skyeye_api/skyeye_alarm_list.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "skyeye_alarm_list"
    assert tool.info.provider == "skyeye_api"


def test_skyeye_verify_ssl_defaults_true_when_unset():
    module = _load_module("test_skyeye_handler_verify_ssl", _SKYEYE_HANDLER)
    assert module._verify_ssl({}) is True
    assert module._verify_ssl({"custom_settings": {}}) is True
    assert module._verify_ssl({"verify_ssl": False}) is False


def test_tdp_resolve_verify_ssl_defaults_true_when_unset():
    module = _load_module("test_tdp_handler_verify_ssl", _TDP_HANDLER)
    assert module._resolve_verify_ssl({}) is True
    assert module._resolve_verify_ssl({"custom_settings": {}}) is True
    assert module._resolve_verify_ssl({"verify_ssl": False}) is False
