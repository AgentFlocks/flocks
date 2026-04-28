from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool

BASE = Path.cwd() / ".flocks" / "plugins" / "tools" / "api" / "tdp_api"


def _load_tool(yaml_name: str):
    yaml_path = BASE / yaml_name
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return yaml_to_tool(raw, yaml_path)


class _FakeResponse:
    def __init__(self, *, status=200, json_payload=None, text_payload="", bytes_payload=b"", headers=None):
        self.status = status
        self._json_payload = json_payload
        self._text_payload = text_payload
        self._bytes_payload = bytes_payload
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        del content_type
        if self._json_payload is None:
            raise ValueError("no json payload")
        return self._json_payload

    async def text(self):
        return self._text_payload

    async def read(self):
        return self._bytes_payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_tdp_dashboard_status_uses_combined_credentials_and_signs_request():
    tool = _load_tool("tdp_dashboard_status.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"agent_count": 6}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert result.output == {"agent_count": 6}
    assert result.metadata["api"] == "dashboard_status"

    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/dashboard/status"
    assert request_kwargs["params"]["api_key"] == "demo-api"
    assert request_kwargs["params"]["auth_timestamp"].isdigit()
    assert request_kwargs["params"]["sign"]


@pytest.mark.asyncio
async def test_tdp_dashboard_status_can_switch_to_dashboard_block_action():
    tool = _load_tool("tdp_dashboard_status.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [1, 2]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="block")

    assert result.success is True
    assert result.metadata["api"] == "dashboard_block"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/dashboard/block"


@pytest.mark.asyncio
async def test_tdp_dashboard_status_uses_non_deprecated_threat_topic_path():
    tool = _load_tool("tdp_dashboard_status.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": []}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="threat_topic")

    assert result.success is True
    assert result.metadata["api"] == "dashboard_threat_topic"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/dashboard/threat-topic"


@pytest.mark.asyncio
async def test_tdp_incident_list_can_use_secret_manager_credentials_and_default_filters():
    tool = _load_tool("tdp_incident_list.yaml")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "tdp_api_key": "secret-api",
        "tdp_secret": "secret-value",
        "tdp_host": "tdp.internal",
    }.get(key)
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"incident_id": "1"}]}})]
    )

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={}),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert result.output["items"][0]["incident_id"] == "1"

    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.internal/api/v1/incident/search"
    condition = request_kwargs["json"]["condition"]
    assert condition["duration"] == {"begin_duration": 0, "end_duration": 24}
    assert condition["time_from"] < condition["time_to"]


@pytest.mark.asyncio
async def test_tdp_machine_asset_list_can_switch_to_web_app_framework_action():
    tool = _load_tool("tdp_machine_asset_list.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"service": "Apache"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="web_app_framework_list",
        )

    assert result.success is True
    assert result.metadata["api"] == "machine_app_frame_detail_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/machine/appFrame/detailList"
    assert request_kwargs["json"]["condition"]["af_class"] == "web_application"


@pytest.mark.asyncio
async def test_tdp_threat_inbound_attack_uses_severity_distribution_endpoint():
    tool = _load_tool("tdp_threat_inbound_attack.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": [{"key": "4", "value": 2}]})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert result.metadata["api"] == "inbound_attack_severity_distribution"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/threat/inbound-attack/severity-distribution"
    assert request_kwargs["json"]["condition"]["time_from"] < request_kwargs["json"]["condition"]["time_to"]


@pytest.mark.asyncio
async def test_tdp_login_api_list_can_switch_to_summary_action():
    tool = _load_tool("tdp_login_api_list.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"all_login_api": {"count": 35}}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="summary")

    assert result.success is True
    assert result.metadata["api"] == "login_api_count"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/loginApi/countOfAppClass"
    assert request_kwargs["json"]["time_from"] < request_kwargs["json"]["time_to"]


@pytest.mark.asyncio
async def test_tdp_asset_upload_api_can_switch_to_interface_list_action():
    tool = _load_tool("tdp_asset_upload_api.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"url_path": "/upload"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="interface_list")

    assert result.success is True
    assert result.metadata["api"] == "upload_api_interface_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/asset/uploadApi/interface/list"
    assert request_kwargs["json"]["page"]["sort"][0]["sort_by"] == "last_upload_time"


@pytest.mark.asyncio
async def test_tdp_log_search_can_switch_to_terms_action():
    tool = _load_tool("tdp_log_search.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"data": [{"key": "SQL注入"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="terms")

    assert result.success is True
    assert result.metadata["api"] == "log_terms"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/log/terms"
    assert request_kwargs["json"]["term"] == "threat.name"


@pytest.mark.asyncio
async def test_tdp_log_search_search_uses_default_sql_and_size():
    tool = _load_tool("tdp_log_search.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"data": [{"id": "log-1"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="search")

    assert result.success is True
    assert result.metadata["api"] == "log_search_by_sql"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/log/searchBySql"
    assert request_kwargs["json"]["sql"] == "threat.level = 'attack'"
    assert request_kwargs["json"]["size"] == 10


def test_tdp_log_search_schema_marks_sql_as_required():
    tool = _load_tool("tdp_log_search.yaml")

    assert "sql" in tool.info.get_schema().required
    assert tool.info.get_schema().properties["sql"]["default"] == "threat.level = 'attack'"


@pytest.mark.asyncio
async def test_tdp_vulnerability_list_uses_vulnerability_endpoint():
    tool = _load_tool("tdp_vulnerability_list.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"status": 0}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert result.metadata["api"] == "vulnerability_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/vulnerability/vulnerabilityList"
    assert request_kwargs["json"]["page"]["sort"][0]["sort_by"] == "severity"


@pytest.mark.asyncio
async def test_tdp_interface_risk_list_invokes_handler_via_yaml_tool_loader():
    """Loads YAML → yaml_to_tool → handler(context, **kwargs), same path as Flocks runtime."""
    tool = _load_tool("tdp_interface_risk_list.yaml")

    assert tool.info.name == "tdp_interface_risk_list"
    assert tool.info.provider == "tdp_api"

    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": []}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            time_from=1700000000,
            time_to=1700003600,
            api_risk_type="注入漏洞",
            keyword="graphql",
            cur_page=2,
            page_size=15,
            sort_by="last_occ_time",
            sort_order="asc",
        )

    assert result.success is True
    assert result.metadata["api"] == "interface_risk_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/interface/risk/getApiList"
    req_json = request_kwargs["json"]
    assert req_json["condition"]["time_from"] == 1700000000
    assert req_json["condition"]["time_to"] == 1700003600
    assert req_json["condition"]["api_risk_type"] == "注入漏洞"
    assert req_json["condition"]["fuzzy"] == {
        "keyword": "graphql",
        "fieldlist": ["threat.name", "url_pattern"],
    }
    assert req_json["page"]["cur_page"] == 2
    assert req_json["page"]["page_size"] == 15
    assert req_json["page"]["sort"] == [{"sort_by": "last_occ_time", "sort_order": "asc"}]


@pytest.mark.asyncio
async def test_tdp_cloud_facilities_can_switch_to_instance_access_list_action():
    tool = _load_tool("tdp_cloud_facilities.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"cloud_instance": "i-zadG8d4l"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="instance_access_list",
            cloud_instance="i-zadG8d4l",
        )

    assert result.success is True
    assert result.metadata["api"] == "cloud_facilities_instance_access_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/cloud-facilities/instance-access-list"
    assert request_kwargs["json"]["page"]["sort"][0]["sort_by"] == "connect_times"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("yaml_name", "call_kwargs", "expected_url", "expected_api", "expected_filename"),
    [
        (
            "tdp_pcap_download.yaml",
            {"alert_id": "alert-1", "occ_time": 1700000000},
            "https://tdp.local/api/v1/pcap/download",
            "pcap_download",
            "capture.pcap",
        ),
        (
            "tdp_file_download.yaml",
            {"hash": "abcd"},
            "https://tdp.local/api/v1/file/download/abcd",
            "file_download",
            "sample.bin",
        ),
    ],
)
async def test_tdp_download_tools_return_binary_payload(
    yaml_name, call_kwargs, expected_url, expected_api, expected_filename
):
    tool = _load_tool(yaml_name)
    fake_session = _FakeSession(
        [
            _FakeResponse(
                bytes_payload=b"\x89PNG\r\n",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": f'attachment; filename="{expected_filename}"',
                },
            )
        ]
    )

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), **call_kwargs)

    assert result.success is True
    assert result.metadata["api"] == expected_api
    assert result.output["filename"] == expected_filename
    assert result.output["encoding"] == "base64"
    assert result.output["content_base64"] == "iVBORw0K"

    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "GET"
    assert request_url == expected_url
    assert request_kwargs["params"]["api_key"] == "demo-api"
    if yaml_name == "tdp_pcap_download.yaml":
        assert "body" in request_kwargs["params"]


@pytest.mark.asyncio
async def test_tdp_mdr_alert_list_can_switch_to_indicator_action():
    tool = _load_tool("tdp_mdr_alert_list.yaml")
    fake_session = _FakeSession(
        [_FakeResponse(json_payload={"response_code": 0, "data": {"cloud_inspection_alert": 10}})]
    )

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="indicator")

    assert result.success is True
    assert result.metadata["api"] == "mdr_alert_indicator"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/mdr/alertExpert/indicator"


@pytest.mark.asyncio
async def test_tdp_system_status_aggregates_multiple_status_endpoints():
    tool = _load_tool("tdp_system_status.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(json_payload={"response_code": 0, "data": {"result": 0}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"message": "ok"}]}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"items": []}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"items": []}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"items": []}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"result": 0}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"items": []}}),
            _FakeResponse(json_payload={"response_code": 0, "data": {"result": 0}}),
        ]
    )

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert set(result.output.keys()) == {
        "core",
        "ioc_update",
        "hardware",
        "input",
        "database",
        "timezone",
        "service",
        "cloud_connectivity",
    }
    assert len(fake_session.calls) == 8


@pytest.mark.asyncio
async def test_tdp_system_status_can_request_single_status_action():
    tool = _load_tool("tdp_system_status.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"result": 0}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="service")

    assert result.success is True
    assert result.metadata["api"] == "system_status_service"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/service-status"


@pytest.mark.asyncio
async def test_tdp_platform_config_asset_list_uses_asset_config_endpoint():
    tool = _load_tool("tdp_platform_config.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "data": {"items": [{"ip": "1.1.1.1"}]}})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), action="asset_list")

    assert result.success is True
    assert result.metadata["api"] == "assets_get_list"
    method, request_url, request_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert request_url == "https://tdp.local/api/v1/assets/getList"
    assert request_kwargs["json"]["page"]["sort"][0]["sort_by"] == "updated_time"


@pytest.mark.asyncio
async def test_tdp_platform_config_asset_delete_supports_ips_wrapper_payload():
    tool = _load_tool("tdp_platform_config.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "verbose_msg": "OK"})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="asset_delete",
            ips=["1.1.1.1", "2.2.2.2"],
        )

    assert result.success is True
    assert result.metadata["api"] == "assets_delete"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/assets/delete"
    assert fake_session.calls[0][2]["json"] == ["1.1.1.1", "2.2.2.2"]


@pytest.mark.asyncio
async def test_tdp_policy_settings_custom_intel_edit_injects_edit_action():
    tool = _load_tool("tdp_policy_settings.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "verbose_msg": "OK"})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="custom_intel_edit",
            data={
                "severity": 2,
                "main_tag": "INFO-IOC",
                "ioc": "fget-career.com",
                "ioc_type": "DOMAIN",
                "intel_uuid": "intel-1",
            },
        )

    assert result.success is True
    assert result.metadata["api"] == "intel_action_edit"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/intel/action"
    assert fake_session.calls[0][2]["json"]["action"] == "edit"
    assert fake_session.calls[0][2]["json"]["data"]["intel_uuid"] == "intel-1"


@pytest.mark.asyncio
async def test_tdp_policy_settings_ip_reputation_delete_supports_ids_wrapper():
    tool = _load_tool("tdp_policy_settings.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 0, "verbose_msg": "OK"})])

    with (
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.local",
            },
        ),
        patch(
            "flocks.security.get_secret_manager",
            return_value=MagicMock(
                get=MagicMock(side_effect=lambda key: {"tdp_api_key": "demo-api", "tdp_secret": "demo-secret"}.get(key))
            ),
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="ip_reputation_delete",
            ids=[302773, 302774],
        )

    assert result.success is True
    assert result.metadata["api"] == "ip_reputation_delete"
    assert fake_session.calls[0][1] == "https://tdp.local/api/v1/ipReputation/delete"
    assert fake_session.calls[0][2]["json"] == [302773, 302774]
