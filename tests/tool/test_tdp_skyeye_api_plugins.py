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


def _built_json_payload(mock_run):
    api_name, path, body_builder, body = mock_run.await_args.args
    return api_name, path, body_builder(body)


async def test_tdp_incident_timeline_requires_incident_id():
    module = _load_module("test_tdp_handler_incident", _TDP_HANDLER)

    result = await module.incident_list(_ctx(), action="timeline")

    assert result.success is False
    assert "incident_id" in result.error


async def test_tdp_incident_timeline_defaults_show_attack_true():
    module = _load_module("test_tdp_handler_incident_timeline_show_attack", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.incident_list(
            _ctx(),
            action="timeline",
            incident_id="incident-1",
            time_from=1700000000,
            time_to=1700003600,
        )

    assert result.success is True
    body = mock_run.await_args.kwargs["body"]
    assert body["incident_id"] == "incident-1"
    assert body["show_attack"] is True


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


async def test_tdp_alert_host_summary_uses_all_threat_types_defaults():
    module = _load_module("test_tdp_handler_alert_host_summary_defaults", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.threat_host_list(_ctx(), action="summary")

    assert result.success is True
    mock_run.assert_awaited_once()
    api_name, path, payload = _built_json_payload(mock_run)
    assert api_name == "host_get_fall_host_sum_list"
    assert path == "/api/v1/host/getFallHostSumList"
    assert payload["condition"]["threat_type"] == module.DEFAULT_HOST_THREAT_TYPES
    assert payload["condition"]["threat_characters"] == []
    assert "time_from" in payload["condition"]
    assert "time_to" in payload["condition"]
    assert payload["page"] == {"cur_page": 1, "page_size": 20, "sort_by": "severity", "sort_flag": "desc"}


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


async def test_tdp_platform_config_exposes_disposal_log_list():
    module = _load_module("test_tdp_handler_platform_disposal_log", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.platform_config(_ctx(), action="disposal_log_list")

    assert result.success is True
    assert mock_run.await_args.kwargs["action"] == "disposal_log_list"


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


async def test_tdp_log_terms_allows_missing_sql():
    module = _load_module("test_tdp_handler_log_terms_no_sql", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.log_search(
            _ctx(),
            action="terms",
            time_from=1777266076,
            time_to=1777352476,
            term="alert_type",
        )

    assert result.success is True
    body = mock_run.await_args.kwargs["body"]
    assert body["time_from"] == 1777266076
    assert body["time_to"] == 1777352476
    assert body["term"] == "alert_type"
    assert "sql" not in body


async def test_tdp_log_search_rejects_full_sql_statement():
    module = _load_module("test_tdp_handler_log_search_full_sql", _TDP_HANDLER)

    result = await module.log_search(
        _ctx(),
        action="search",
        time_from=1777266076,
        time_to=1777352476,
        sql="select * from alert",
        size=5,
    )

    assert result.success is False
    assert "filter expression" in result.error
    assert "SELECT/FROM" in result.error


async def test_tdp_high_priority_query_tools_map_semantic_filters():
    module = _load_module("test_tdp_handler_high_priority_mappings", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.vulnerability_list(
            _ctx(),
            assets_group=[0, 237],
            severity=[2, 4],
            status=1,
            keyword="traversal",
            cur_page=3,
            page_size=5,
            sort_by="last_occ_time",
            sort_order="asc",
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "vulnerability_list"
        assert path == "/api/v1/vulnerability/vulnerabilityList"
        assert payload["condition"]["assets_group"] == [0, 237]
        assert payload["condition"]["severity"] == [2, 4]
        assert payload["condition"]["status"] == 1
        assert payload["condition"]["fuzzy"] == {"keyword": "traversal", "fieldlist": ["vulnerability_name", "ip"]}
        assert payload["page"]["cur_page"] == 3
        assert payload["page"]["page_size"] == 5
        assert payload["page"]["sort"] == [{"sort_by": "last_occ_time", "sort_order": "asc"}]

        mock_run.reset_mock()
        result = await module.api_list(
            _ctx(),
            host="example.com",
            methods=["POST"],
            privacy_tags=["leak_phone"],
            tags=["上传接口"],
            is_public=True,
            has_interface=1,
            is_encrypted=False,
            keyword="login",
            cur_page=2,
            page_size=10,
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "interface_list"
        assert path == "/api/v1/interface/list"
        assert payload["condition"]["host"] == "example.com"
        assert payload["condition"]["methods"] == ["POST"]
        assert payload["condition"]["privacy_tags"] == ["leak_phone"]
        assert payload["condition"]["tags"] == ["上传接口"]
        assert payload["condition"]["is_public"] is True
        assert payload["condition"]["has_interface"] == 1
        assert payload["condition"]["is_encrypted"] is False
        assert payload["condition"]["fuzzy"] == {"keyword": "login", "fieldlist": ["url_pattern", "title"]}
        assert payload["page"]["cur_page"] == 2
        assert payload["page"]["page_size"] == 10

        mock_run.reset_mock()
        result = await module.api_risk_list(
            _ctx(),
            assets_group=[1],
            api_risk_type="注入漏洞",
            keyword="graphql",
            sort_by="last_occ_time",
            sort_order="asc",
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "interface_risk_list"
        assert path == "/api/v1/interface/risk/getApiList"
        assert payload["condition"]["assets_group"] == [1]
        assert payload["condition"]["api_risk_type"] == "注入漏洞"
        assert payload["condition"]["fuzzy"] == {"keyword": "graphql", "fieldlist": ["threat.name", "url_pattern"]}
        assert payload["page"]["sort"] == [{"sort_by": "last_occ_time", "sort_order": "asc"}]

        mock_run.reset_mock()
        result = await module.api_risk_list(
            _ctx(),
            time_from=1700000000,
            time_to=1700003600,
            page_size=10,
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "interface_risk_list"
        assert path == "/api/v1/interface/risk/getApiList"
        assert payload["condition"]["time_from"] == 1700000000
        assert payload["condition"]["time_to"] == 1700003600
        assert payload["condition"]["api_risk_type"] == ""
        assert payload["condition"]["assets_group"] == []
        assert payload["condition"]["fuzzy"] == {"keyword": "", "fieldlist": ["threat.name", "url_pattern"]}
        assert payload["page"]["page_size"] == 10

        mock_run.reset_mock()
        result = await module.weak_password_list(
            _ctx(),
            assets_group=[2],
            data="10.0.0.1/wp-login.php",
            weakpwd_source="智能识别规则",
            result="success",
            app_class=["OA"],
            is_plaintext=True,
            keyword="admin",
            cur_page=4,
            page_size=8,
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "weak_password_list"
        assert path == "/api/v1/login/weakpwd/list"
        assert payload["condition"]["assets_group"] == [2]
        assert payload["condition"]["data"] == "10.0.0.1/wp-login.php"
        assert payload["condition"]["weakpwd_source"] == "智能识别规则"
        assert payload["condition"]["result"] == "success"
        assert payload["condition"]["app_class"] == ["OA"]
        assert payload["condition"]["is_plaintext"] is True
        assert payload["condition"]["fuzzy"] == {
            "keyword": "admin",
            "fieldlist": ["threat.params.username", "threat.params.weakpwd", "data", "net.src_ip"],
        }
        assert payload["page"]["cur_page"] == 4
        assert payload["page"]["page_size"] == 8

        mock_run.reset_mock()
        result = await module.domain_asset_list(
            _ctx(),
            created_in_3_days=True,
            has_login_api=False,
            domain_name_or_ip="attack.com",
            has_privacy=True,
            has_upload_api=False,
            is_active=True,
            is_public=False,
            second_level_domain="attack.com",
            cur_page=5,
            page_size=50,
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "domain_asset_search"
        assert path == "/api/v1/assets/domainName/search"
        assert payload["condition"]["created_in_3_days"] is True
        assert payload["condition"]["has_login_api"] is False
        assert payload["condition"]["domain_name_or_ip"] == "attack.com"
        assert payload["condition"]["has_privacy"] is True
        assert payload["condition"]["has_upload_api"] is False
        assert payload["condition"]["is_active"] is True
        assert payload["condition"]["is_public"] is False
        assert payload["condition"]["second_level_domain"] == "attack.com"
        assert payload["page"]["cur_page"] == 5
        assert payload["page"]["page_size"] == 50

        mock_run.reset_mock()
        result = await module.privacy_overview(
            _ctx(),
            assets_group=[3],
            itag=["phone", "email"],
            methods=["POST"],
            fuzzy_url_path="/submit",
            fuzzy_url_host="example.com",
            fuzzy_src_ip="1.1.1.1",
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "privacy_diagram"
        assert path == "/api/v1/privacy/diagram"
        assert payload["condition"]["assets_group"] == [3]
        assert payload["condition"]["itag"] == ["phone", "email"]
        assert payload["condition"]["methods"] == ["POST"]
        assert payload["condition"]["fuzzy_url_path"] == "/submit"
        assert payload["condition"]["fuzzy_url_host"] == "example.com"
        assert payload["condition"]["fuzzy_src_ip"] == "1.1.1.1"

        mock_run.reset_mock()
        result = await module.inbound_attack(
            _ctx(),
            severity=[3, 4],
            result_list=["success"],
            cascade_asset_group={"device-1": [-1]},
            keyword="sqlmap",
        )
        assert result.success is True
        api_name, path, payload = _built_json_payload(mock_run)
        assert api_name == "inbound_attack_severity_distribution"
        assert path == "/api/v1/threat/inbound-attack/severity-distribution"
        assert payload["condition"]["severity"] == [3, 4]
        assert payload["condition"]["result_list"] == ["success"]
        assert payload["condition"]["cascade_asset_group"] == {"device-1": [-1]}
        assert payload["condition"]["fuzzy"] == {
            "keyword": "sqlmap",
            "fieldlist": ["threat.name", "external_ip", "machine", "assets.name", "data"],
        }


async def test_tdp_medium_query_tools_map_semantic_filters():
    module = _load_module("test_tdp_handler_medium_mappings", _TDP_HANDLER)
    mock_result = ToolResult(success=True, output={"status": 200})

    with patch.object(module, "_run_action_json_tool", AsyncMock(return_value=mock_result)) as mock_run:
        result = await module.service_asset_list(
            _ctx(),
            action="web_app_framework_list",
            time_from=1777266076,
            time_to=1777352476,
            assets_group=[0],
            host_type=["终端"],
            application="泛微e-cology",
            sub_class="OA",
            is_active=True,
            keyword="10.0.0.5",
            cur_page=2,
            page_size=15,
            sort_by="last_occ_time",
            sort_order="asc",
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert mock_run.await_args.kwargs["action"] == "web_app_framework_list"
        assert "time_from" not in body["condition"]
        assert "time_to" not in body["condition"]
        assert body["condition"]["assets_group"] == [0]
        assert body["condition"]["host_type"] == ["终端"]
        assert body["condition"]["application"] == "泛微e-cology"
        assert body["condition"]["sub_class"] == "OA"
        assert body["condition"]["is_active"] is True
        assert body["condition"]["fuzzy"] == {"keyword": "10.0.0.5", "fieldlist": ["machine", "assets.name"]}
        assert body["page"]["cur_page"] == 2
        assert body["page"]["page_size"] == 15
        assert body["page"]["sort"] == [{"sort_by": "last_occ_time", "sort_order": "asc"}]

        mock_run.reset_mock()
        result = await module.login_entry_list(
            _ctx(),
            action="list",
            assets_group=[237],
            app_class="CMS",
            category="web",
            threat_tag=["弱口令"],
            keyword="wp-login",
            is_public=1,
            is_new_online=1,
            is_active=1,
            result="success",
            vulnerable=1,
            cur_page=3,
            page_size=12,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["assets_group"] == [237]
        assert body["condition"]["app_class"] == "CMS"
        assert body["condition"]["category"] == "web"
        assert body["condition"]["threat_tag"] == ["弱口令"]
        assert body["condition"]["fuzzy"] == {"keyword": "wp-login", "fieldlist": ["data", "net.http.reqs_referer"]}
        assert body["condition"]["is_public"] == 1
        assert body["condition"]["is_new_online"] == 1
        assert body["condition"]["is_active"] == 1
        assert body["condition"]["result"] == "success"
        assert body["condition"]["vulnerable"] == 1
        assert body["page"]["cur_page"] == 3
        assert body["page"]["page_size"] == 12

        mock_run.reset_mock()
        result = await module.cloud_service(
            _ctx(),
            action="instance_access_list",
            cloud_instance="i-zadG8d4l",
            assets_group=[0],
            keyword="10.10.10.1",
            cur_page=2,
            page_size=6,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["cloud_instance"] == "i-zadG8d4l"
        assert body["condition"]["assets_group"] == [0]
        assert body["condition"]["fuzzy"] == {
            "keyword": "10.10.10.1",
            "fieldlist": ["cloud_instance", "external_ip", "machine"],
        }
        assert body["page"]["cur_page"] == 2
        assert body["page"]["page_size"] == 6

        mock_run.reset_mock()
        result = await module.mdr_alert_list(
            _ctx(),
            section_list=["终端"],
            threat_severity=[4],
            judge_result_status=[2],
            keyword="10.10.10.1",
            cur_page=5,
            page_size=9,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["section_list"] == ["终端"]
        assert body["condition"]["threat_severity"] == [4]
        assert body["condition"]["judge_result_status"] == [2]
        assert body["condition"]["fuzzy"] == {
            "keyword": "10.10.10.1",
            "fieldlist": ["task_id", "machine", "asset_info", "threat_name"],
        }
        assert body["page"]["cur_page"] == 5
        assert body["page"]["page_size"] == 9

        mock_run.reset_mock()
        result = await module.dashboard_status(
            _ctx(),
            action="alert_sum",
            time_from=1700000000,
            time_to=1700600000,
            assets_group=[1],
            machine_type="server",
            severity=[3, 4],
            cur_page=2,
            page_size=4,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["time_from"] == 1700000000
        assert body["condition"]["time_to"] == 1700600000
        assert body["condition"]["assets_group"] == [1]
        assert body["condition"]["machine_type"] == "server"
        assert body["condition"]["severity"] == [3, 4]
        assert body["page"]["cur_page"] == 2
        assert body["page"]["page_size"] == 4

        mock_run.reset_mock()
        result = await module.upload_api(
            _ctx(),
            action="interface_list",
            host="example.com",
            search_for_upload=True,
            keyword="upload",
            cur_page=2,
            page_size=7,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["host"] == "example.com"
        assert body["condition"]["search_for_upload"] is True
        assert body["condition"]["fuzzy"] == {"keyword": "upload", "fieldlist": ["url_pattern", "title"]}
        assert body["page"]["cur_page"] == 2
        assert body["page"]["page_size"] == 7

        mock_run.reset_mock()
        result = await module.incident_list(
            _ctx(),
            action="search",
            severity=[4],
            phase=["exploit"],
            result=["success"],
            is_target_attack=True,
            begin_duration=1,
            end_duration=6,
            keyword="sql",
            cur_page=2,
            page_size=11,
        )
        assert result.success is True
        body = mock_run.await_args.kwargs["body"]
        assert body["condition"]["severity"] == [4]
        assert body["condition"]["phase"] == ["exploit"]
        assert body["condition"]["result"] == ["success"]
        assert body["condition"]["is_target_attack"] is True
        assert body["condition"]["duration"] == {"begin_duration": 1, "end_duration": 6}
        assert body["condition"]["fuzzy"] == {
            "keyword": "sql",
            "fieldlist": ["attacker_ip", "host_ip", "attack_name", "attack_tool", "incident_id"],
        }
        assert body["page"]["cur_page"] == 2
        assert body["page"]["page_size"] == 11


async def test_tdp_cloud_instance_access_requires_cloud_instance():
    module = _load_module("test_tdp_handler_cloud_instance_required", _TDP_HANDLER)

    result = await module.cloud_service(_ctx(), action="instance_access_list")

    assert result.success is False
    assert "condition.cloud_instance" in result.error


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
    assert "severity" in raw["inputSchema"]["properties"]
    assert "begin_duration" in raw["inputSchema"]["properties"]
    assert "keyword" in raw["inputSchema"]["properties"]


def test_tdp_query_yaml_promotes_semantic_top_level_fields():
    expected_fields = {
        "tdp_host_threat_list.yaml": {"severity", "threat_characters", "keyword", "cur_page"},
        "tdp_vulnerability_list.yaml": {"severity", "status", "keyword", "sort_by"},
        "tdp_interface_list.yaml": {"host", "methods", "privacy_tags", "keyword"},
        "tdp_interface_risk_list.yaml": {"api_risk_type", "keyword", "sort_by"},
        "tdp_login_weakpwd_list.yaml": {"data", "result", "app_class", "keyword"},
        "tdp_assets_domain_list.yaml": {"domain_name_or_ip", "has_login_api", "second_level_domain"},
        "tdp_privacy_diagram.yaml": {"itag", "methods", "fuzzy_url_host"},
        "tdp_threat_inbound_attack.yaml": {"severity", "result_list", "keyword"},
        "tdp_machine_asset_list.yaml": {"time_from", "time_to", "service", "service_class", "application", "keyword"},
        "tdp_mdr_alert_list.yaml": {"section_list", "threat_severity", "keyword"},
        "tdp_cloud_facilities.yaml": {"cloud_vendor", "cloud_instance", "keyword"},
        "tdp_login_api_list.yaml": {"threat_tag", "keyword", "is_public"},
        "tdp_dashboard_status.yaml": {"machine_type", "severity", "cur_page"},
        "tdp_asset_upload_api.yaml": {"keyword", "sort_by", "page_size"},
    }

    for filename, fields in expected_fields.items():
        yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api" / filename
        raw = _read_yaml_raw(yaml_path)
        properties = raw["inputSchema"]["properties"]
        for field in fields:
            assert field in properties


def test_tdp_platform_yaml_uses_keyword_and_requires_confirmation():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/tdp_api/tdp_platform_config.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "tdp_platform_config"
    assert tool.info.provider == "tdp_api"
    assert raw["requires_confirmation"] is True
    assert "keyword" in raw["inputSchema"]["properties"]
    assert "device_id" not in raw["inputSchema"]["properties"]
    assert "disposal_log_list" in raw["inputSchema"]["properties"]["action"]["enum"]


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
    assert raw["inputSchema"].get("required") == []
    assert raw["inputSchema"]["properties"]["columns"]["items"]["type"] == "object"
    assert "cascade_asset_group" in raw["inputSchema"]["properties"]


def test_skyeye_alarm_list_yaml_loads_with_provider():
    yaml_path = _WORKSPACE_ROOT / ".flocks/plugins/tools/api/skyeye_api/skyeye_alarm_list.yaml"
    raw = _read_yaml_raw(yaml_path)
    tool = yaml_to_tool(raw, yaml_path)

    assert tool.info.name == "skyeye_alarm_list"
    assert tool.info.provider == "skyeye_api"


def test_skyeye_verify_ssl_defaults_false_when_unset():
    module = _load_module("test_skyeye_handler_verify_ssl", _SKYEYE_HANDLER)
    assert module._verify_ssl({}) is False
    assert module._verify_ssl({"custom_settings": {}}) is False
    assert module._verify_ssl({"verify_ssl": True}) is True
    assert module._verify_ssl({"verify_ssl": False}) is False


def test_tdp_resolve_verify_ssl_defaults_false_when_unset():
    module = _load_module("test_tdp_handler_verify_ssl", _TDP_HANDLER)
    assert module._resolve_verify_ssl({}) is False
    assert module._resolve_verify_ssl({"custom_settings": {}}) is False
    assert module._resolve_verify_ssl({"verify_ssl": True}) is True
    assert module._resolve_verify_ssl({"verify_ssl": False}) is False
