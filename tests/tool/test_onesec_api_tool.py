import base64
import hmac
from hashlib import sha1
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool


def _load_tool(yaml_name: str):
    yaml_path = (
        Path.cwd()
        / ".flocks"
        / "plugins"
        / "tools"
        / "api"
        / "onesec"
        / yaml_name
    )
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return yaml_to_tool(raw, yaml_path)


class _FakeResponse:
    def __init__(self, *, status=200, json_payload=None, text_payload=""):
        self.status = status
        self._json_payload = json_payload
        self._text_payload = text_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        del content_type
        return self._json_payload

    async def text(self):
        return self._text_payload


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


def _expected_sign(api_key: str, secret: str, timestamp: int) -> str:
    raw = f"{api_key}{timestamp}".encode()
    return base64.urlsafe_b64encode(
        hmac.new(secret.encode(), raw, sha1).digest()
    ).decode().rstrip("=")


@pytest.mark.asyncio
async def test_onesec_dns_search_queries_uses_signed_query_params_and_doc_payload():
    tool = _load_tool("onesec_dns.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 0,
                    "verbose_msg": "SUCCESS",
                    "data": {
                        "cur_page": 1,
                        "page_items_num": 50,
                        "items": [{"domain": "example.com"}],
                    },
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "onesec_credentials": "api-key-1|secret-1",
    }.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:onesec_credentials}",
                "base_url": "https://console.onesec.local",
                "timeout": 45,
            },
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="dns_search_queries",
            time_from=1699990000,
            time_to=1700000000,
            domain="example.com",
            qType="A",
            rcode="NOERROR",
            cur_page=1,
            pageitemsnum=50,
        )

    assert tool.info.source == "api"
    assert tool.info.provider == "onesec_api"
    assert result.success is True
    assert result.output["items"] == [{"domain": "example.com"}]
    assert result.metadata["api"] == "dns_search_queries"

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.local/open/api/client/searchQueries"
    assert kwargs["params"] == {
        "api_key": "api-key-1",
        "auth_timestamp": "1700000000",
        "sign": _expected_sign("api-key-1", "secret-1", 1700000000),
    }
    assert kwargs["json"] == {
        "condition": {
            "time_from": 1699990000,
            "time_to": 1700000000,
            "domain": "example.com",
            "qType": "A",
            "rcode": "NOERROR",
        },
        "page": {
            "cur_page": 1,
            "page_items_num": 50,
        },
    }


@pytest.mark.asyncio
async def test_onesec_edr_get_threat_files_uses_doc_page_structure():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 0,
                    "verbose_msg": "SUCCESS",
                    "data": {
                        "total": 1,
                        "cur_page": 2,
                        "malwareFileItemList": [{"threat_name": "DemoTrojan"}],
                    },
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_threat_files",
            time_from=1698000000,
            time_to=1700000000,
            group_list=[1001],
            umid_list=["umid-1"],
            threat_severity=[3, 4],
            process_result=[1200],
            search_field={"threat_name": "trojan"},
            cur_page=2,
            page_size=100,
        )

    assert result.success is True
    assert result.output["malwareFileItemList"] == [{"threat_name": "DemoTrojan"}]

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/getThreatFiles"
    assert kwargs["json"] == {
        "time_from": 1698000000,
        "time_to": 1700000000,
        "group_list": [1001],
        "umid_list": ["umid-1"],
        "threat_severity": [3, 4],
        "process_result": [1200],
        "search_field": {"threat_name": "trojan"},
        "page": {"cur_page": 2, "page_size": 100},
    }


@pytest.mark.asyncio
async def test_onesec_threat_virus_scan_returns_integer_task_id():
    tool = _load_tool("onesec_threat.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload=98765)])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="threat_virus_scan",
            agent_list=["umid-1", "umid-2"],
            task_type=10110,
            scanmode=2,
        )

    assert result.success is True
    assert result.output == 98765
    assert result.metadata["api"] == "threat_virus_scan"

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/virusScan"
    assert kwargs["json"] == {
        "task_scope": {"agent_list": ["umid-1", "umid-2"]},
        "task_type": 10110,
        "task_content": {"scanmode": 2},
    }


@pytest.mark.asyncio
async def test_onesec_ops_query_agent_page_list_uses_sort_object_payload():
    tool = _load_tool("onesec_ops.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"total": 1, "cur_page": 1, "items": [{"host_name": "host-1"}]},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="ops_query_agent_page_list",
            cur_page=1,
            page_size=10,
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/QueryAgentPageList"
    assert kwargs["json"] == {
        "page": {"cur_page": 1, "page_size": 10},
        "sort": {"sort_by": "create_time", "sort_order": "desc"},
    }


@pytest.mark.asyncio
async def test_onesec_ops_edit_agent_info_includes_extended_doc_fields():
    tool = _load_tool("onesec_ops.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200, "verbose_msg": "success"})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="ops_edit_agent_info",
            mac="AA-BB-CC-00-00-00",
            update_type=1,
            name="测试姓名",
            department="财务组A",
            job_number="U-000001",
            phone_number="18200000000",
            mail="test@example.com",
            organization_user_id="third-user-id",
            is_virtual=2,
            pc_id="PC-000001",
            group_path=["华东", "主公司", "财务部"],
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/EditAgentInfo"
    assert kwargs["json"] == {
        "mac": "AA-BB-CC-00-00-00",
        "update_type": 1,
        "name": "测试姓名",
        "department": "财务组A",
        "job_number": "U-000001",
        "phone_number": "18200000000",
        "mail": "test@example.com",
        "organization_user_id": "third-user-id",
        "is_virtual": 2,
        "pc_id": "PC-000001",
        "group_path": ["华东", "主公司", "财务部"],
    }


@pytest.mark.asyncio
async def test_onesec_ops_query_task_page_list_uses_sort_object_payload():
    tool = _load_tool("onesec_ops.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"total": 1, "cur_page": 1, "items": [{"task_id": 1}]},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="ops_query_task_page_list",
            time_type="create_time",
            begin_time=1699990000,
            end_time=1700000000,
            auto=0,
            cur_page=1,
            page_size=10,
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/QueryTaskPageList"
    assert kwargs["json"] == {
        "time_type": "create_time",
        "begin_time": 1699990000,
        "end_time": 1700000000,
        "auto": 0,
        "page": {"cur_page": 1, "page_size": 10},
        "sort": {"sort_by": "create_time", "sort_order": "desc"},
    }


@pytest.mark.asyncio
async def test_onesec_edr_get_ioc_list_uses_doc_payload():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"total": 1, "cur_page": 1, "items": [{"ioc": "hash1"}]},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_ioc_list",
            fuzzy="ioc-keyword",
            ioc_severity_list=[1, 4],
            cur_page=1,
            page_size=50,
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/getIOCList"
    assert kwargs["json"] == {
        "fuzzy": "ioc-keyword",
        "severity": [1, 4],
        "page": {"cur_page": 1, "page_size": 50},
        "sort": {"sort_by": "updateTime", "sort_order": "desc"},
    }


@pytest.mark.asyncio
async def test_onesec_edr_get_threat_disposals_uses_incident_and_sort_payload():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"total": 1, "cur_page": 1, "list": [{"id": "disp-1"}]},
                }
            ),
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"list": [{"id": "disp-1"}]},
                }
            ),
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        paged_result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_threat_disposals",
            incident_id="incident-1",
            umid="umid-1",
            cur_page=1,
            page_size=20,
        )
        recent_result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_recent_threat_disposals",
            incident_id="incident-1",
            umid="umid-1",
        )

    assert paged_result.success is True
    assert recent_result.success is True

    paged_call = fake_session.calls[0]
    assert paged_call[1] == "https://console.onesec.net/api/saasedr/api/client/v1/getThreatDisposals"
    assert paged_call[2]["json"] == {
        "incident_id": "incident-1",
        "umid": "umid-1",
        "page": {"cur_page": 1, "page_size": 20},
        "sort": [{"sort_by": "update_time", "sort_order": "desc"}],
    }

    recent_call = fake_session.calls[1]
    assert recent_call[1] == "https://console.onesec.net/api/saasedr/api/client/v1/getRecentThreatDisposals"
    assert recent_call[2]["json"] == {
        "incident_id": "incident-1",
        "umid": "umid-1",
        "sort": [{"sort_by": "update_time", "sort_order": "desc"}],
    }


@pytest.mark.asyncio
async def test_onesec_edr_recent_incidents_rejects_window_over_24_hours():
    tool = _load_tool("onesec_edr.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="edr_get_recent_incidents",
        time_from=1699395200,
        time_to=1700000000,
    )

    assert result.success is False
    assert "仅支持最近 24 小时的数据" in result.error
    assert "`edr_get_incidents`" in result.error


@pytest.mark.asyncio
async def test_onesec_edr_threat_timeline_requires_incident_id():
    tool = _load_tool("onesec_edr.yaml")

    paged_result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="edr_get_threat_timeline",
        time_from=1699990000,
        time_to=1700000000,
        cur_page=1,
        page_size=20,
    )
    recent_result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="edr_get_recent_threat_timeline",
    )

    assert paged_result.success is False
    assert paged_result.error == (
        "Missing required parameters for edr_get_threat_timeline: incident_id"
    )
    assert recent_result.success is False
    assert recent_result.error == (
        "Missing required parameters for edr_get_recent_threat_timeline: incident_id"
    )


@pytest.mark.asyncio
async def test_onesec_edr_threat_timeline_uses_incident_id_payload():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"total": 1, "cur_page": 1, "tbBaseLogList": [{"event_time": "1"}]},
                }
            ),
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"tbBaseLogList": [{"event_time": "1"}]},
                }
            ),
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        paged_result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_threat_timeline",
            incident_id="incident-1",
            time_from=1699990000,
            time_to=1700000000,
            cur_page=1,
            page_size=20,
        )
        recent_result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_get_recent_threat_timeline",
            incident_id="incident-1",
            time_from=1699990000,
            time_to=1700000000,
        )

    assert paged_result.success is True
    assert recent_result.success is True

    paged_call = fake_session.calls[0]
    assert paged_call[1] == "https://console.onesec.net/api/saasedr/api/client/v1/getThreatTimeline"
    assert paged_call[2]["json"] == {
        "incident_id": "incident-1",
        "time_from": 1699990000,
        "time_to": 1700000000,
        "page": {"cur_page": 1, "page_size": 20},
    }

    recent_call = fake_session.calls[1]
    assert recent_call[1] == "https://console.onesec.net/api/saasedr/api/client/v1/getRecentThreatTimeline"
    assert recent_call[2]["json"] == {
        "incident_id": "incident-1",
        "time_from": 1699990000,
        "time_to": 1700000000,
    }


@pytest.mark.asyncio
async def test_onesec_software_query_page_list_uses_sort_object_payload():
    tool = _load_tool("onesec_software.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {
                        "cur_page": 1,
                        "page_size": 1,
                        "total": 1,
                        "items": [{"name": "Google Chrome"}],
                    },
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="software_query_page_list",
            group_list=[1001],
            fuzzy="chrome",
            cur_page=1,
            page_size=1,
        )

    assert result.success is True
    assert result.output["items"] == [{"name": "Google Chrome"}]

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/querySoftwarePageList"
    assert kwargs["json"] == {
        "agent_group_list": [1001],
        "fuzzy": "chrome",
        "page": {"cur_page": 1, "page_size": 1},
        "sort": {"sort_by": "install_time", "sort_order": "desc"},
    }


@pytest.mark.asyncio
async def test_onesec_software_query_agent_list_uses_doc_fields():
    tool = _load_tool("onesec_software.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {
                        "cur_page": 1,
                        "page_size": 1,
                        "total": 1,
                        "items": [{"host_name": "host-1"}],
                    },
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="software_query_agent_list",
            name="Google Chrome",
            publisher="Google LLC",
            version_min="120.0",
            version_max="122.0",
            include_empty_version=False,
            fuzzy="host-1",
            os_version=["Windows 11"],
            agent_version=["1.2.3"],
            software_version=["121.0"],
            install_begin=1698000000,
            install_end=1700000000,
            cur_page=1,
            page_size=1,
        )

    assert result.success is True
    assert result.output["items"] == [{"host_name": "host-1"}]

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/querySoftwareAgentList"
    assert kwargs["json"] == {
        "name": "Google Chrome",
        "publisher": "Google LLC",
        "version_min": "120.0",
        "version_max": "122.0",
        "include_empty_version": False,
        "fuzzy": "host-1",
        "os_version": ["Windows 11"],
        "agent_version": ["1.2.3"],
        "software_version": ["121.0"],
        "install_begin": 1698000000,
        "install_end": 1700000000,
        "page": {"cur_page": 1, "page_size": 1},
    }


@pytest.mark.asyncio
async def test_onesec_returns_clear_error_when_credentials_missing():
    tool = _load_tool("onesec_dns.yaml")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = None

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={}),
        patch("aiohttp.ClientSession", return_value=_FakeSession([])),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="dns_get_public_ip_list",
        )

    assert result.success is False
    assert "credentials" in result.error.lower()


@pytest.mark.asyncio
async def test_onesec_dns_search_blocked_queries_validates_action_required_fields():
    tool = _load_tool("onesec_dns.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="dns_search_blocked_queries",
        time_from=1699990000,
        time_to=1700000000,
    )

    assert result.success is False
    assert result.error == (
        "Missing required parameters for dns_search_blocked_queries: domain, keyword"
    )


@pytest.mark.asyncio
async def test_onesec_ops_edit_agent_info_validates_umid_or_mac():
    tool = _load_tool("onesec_ops.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="ops_edit_agent_info",
        name="测试姓名",
    )

    assert result.success is False
    assert result.error == "Missing required parameters for ops_edit_agent_info: umid/mac"


@pytest.mark.asyncio
async def test_onesec_threat_virus_scan_validates_custom_scan_paths():
    tool = _load_tool("onesec_threat.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="threat_virus_scan",
        agent_list=["umid-1"],
        task_type=10130,
        scanmode=1,
    )

    assert result.success is False
    assert result.error == "Missing required parameters for threat_virus_scan: scan_paths"


@pytest.mark.asyncio
async def test_onesec_edr_get_threat_disposals_validates_incident_id_and_umid():
    tool = _load_tool("onesec_edr.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="edr_get_threat_disposals",
        incident_id="incident-1",
    )

    assert result.success is False
    assert result.error == "Missing required parameters for edr_get_threat_disposals: umid"


@pytest.mark.asyncio
async def test_onesec_software_query_agent_list_validates_name_and_publisher():
    tool = _load_tool("onesec_software.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="software_query_agent_list",
        cur_page=1,
        page_size=1,
    )

    assert result.success is False
    assert result.error == (
        "Missing required parameters for software_query_agent_list: name, publisher"
    )


@pytest.mark.asyncio
async def test_onesec_edr_delete_registry_startup_validates_registry_type():
    tool = _load_tool("onesec_edr.yaml")

    result = await tool.handler(
        ToolContext(session_id="test", message_id="test"),
        action="edr_delete_registry_startup",
        agent_list=["umid-1"],
        registry_path=1,
    )

    assert result.success is False
    assert result.error == (
        "Missing required parameters for edr_delete_registry_startup: registry_type"
    )


@pytest.mark.asyncio
async def test_onesec_edr_delete_registry_startup_uses_doc_field_names():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 200,
                    "verbose_msg": "success",
                    "data": {"items": [{"task_id": 1}]},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "api-key-1|secret-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_delete_registry_startup",
            agent_list=["umid-1"],
            registry_path=1,
            registry_type=r"HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Demo",
        )

    assert result.success is True

    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://console.onesec.net/api/saasedr/api/client/v1/actions/deleteRegistryStartup"
    assert kwargs["json"] == {
        "task_scope": {"agent_list": ["umid-1"]},
        "task_content_req": [
            {
                "registry_path": 1,
                "registry_type": r"HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Demo",
            }
        ],
    }
