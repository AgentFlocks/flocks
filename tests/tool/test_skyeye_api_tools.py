from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool


def _load_tool(yaml_name: str):
    yaml_path = Path.cwd() / ".flocks" / "plugins" / "tools" / "api" / "skyeye_v4_0_14_0_SP2" / yaml_name
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
async def test_skyeye_dashboard_view_tool_uses_custom_login_flow():
    tool = _load_tool("skyeye_dashboard_view.yaml")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "skyeye_api_key": "login-key-123",
    }.get(key)
    fake_session = _FakeSession([
        _FakeResponse(json_payload={"access_token": "token-123", "status": 200}),
        _FakeResponse(text_payload='<html><meta name="csrf-token" content="abcdef1234567890"></html>'),
        _FakeResponse(json_payload={"data": {"items": {"value": 42}, "status": 1000, "message": "ok"}}),
    ])

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:skyeye_api_key}",
                "base_url": "https://skyeye.local",
                "custom_settings": {
                    "api_prefix": "api",
                    "api_version": "v1",
                    "username": "tapadmin",
                },
            },
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert tool.info.source == "api"
    assert tool.info.provider == "skyeye_api_v4_0_14_0_SP2"
    assert result.success is True
    assert result.output["data"]["items"] == {"value": 42}
    assert result.metadata["api"] == "monitor_center_dashboard_view"

    method, auth_url, auth_kwargs = fake_session.calls[0]
    assert method == "POST"
    assert auth_url == "https://skyeye.local/api/v1/admin/auth"
    assert auth_kwargs["data"] == {
        "client_id": auth_kwargs["data"]["client_id"],
        "username": "tapadmin",
    }
    assert auth_kwargs["headers"]["X-Authorization"]
    assert auth_kwargs["headers"]["X-Timestamp"]

    method, request_url, request_kwargs = fake_session.calls[2]
    assert method == "GET"
    assert request_url == "https://skyeye.local/api/v1/monitor-center/dashboard/view"
    assert request_kwargs["params"]["name"] == "overall_view"
    assert request_kwargs["params"]["interval_time"] == 7
    assert request_kwargs["params"]["csrf_token"] == "abcdef1234567890"
    assert "r" in request_kwargs["params"]


@pytest.mark.asyncio
async def test_skyeye_alarm_params_tool_can_use_secret_host_and_login_key():
    tool = _load_tool("skyeye_alarm_params.yaml")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "skyeye_login_key": "login-key-xyz",
        "skyeye_host": "skyeye.internal",
    }.get(key)
    fake_session = _FakeSession([
        _FakeResponse(json_payload={"access_token": "token-456", "status": 200}),
        _FakeResponse(text_payload='<meta name="csrf-token" content="1234567890abcdef" />'),
        _FakeResponse(json_payload={"data": {"items": {"attack_chain": {"1": "侦察"}}, "status": 1000}}),
    ])

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={}),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is True
    assert result.output["data"]["items"]["attack_chain"] == {"1": "侦察"}
    assert result.metadata["api"] == "alarm_alarm_params"

    method, auth_url, _ = fake_session.calls[0]
    assert method == "POST"
    assert auth_url == "https://skyeye.internal:443/v1/admin/auth"

    method, request_url, request_kwargs = fake_session.calls[2]
    assert method == "GET"
    assert request_url == "https://skyeye.internal:443/v1/alarm/alarm/alarm-params"
    assert request_kwargs["params"]["data_source"] == 0
    assert request_kwargs["params"]["csrf_token"] == "1234567890abcdef"


@pytest.mark.asyncio
async def test_skyeye_alarm_list_returns_clear_error_when_not_configured():
    tool = _load_tool("skyeye_alarm_list.yaml")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = None

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={}),
        patch("aiohttp.ClientSession", return_value=_FakeSession([])),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"))

    assert result.success is False
    assert "base URL" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("yaml_name", "call_kwargs", "expected_url", "expected_api"),
    [
        (
            "skyeye_download_uploadfile.yaml",
            {"alarm_id": "alarm-1", "start_time": 1700000000000, "end_time": 1700003600000},
            "https://skyeye.local/skyeye/v1/alarm/alarm/info/uploadfile/download",
            "alarm_alarm_info_uploadfile_download",
        ),
        (
            "skyeye_download_pcap.yaml",
            {"alarm_id": "alarm-1", "start_time": 1700000000000, "end_time": 1700003600000},
            "https://skyeye.local/skyeye/v1/alarm/alarm/info/pcap/download",
            "alarm_alarm_info_pcap_download",
        ),
        (
            "skyeye_download_alarm_report.yaml",
            {
                "alarm_id": "alarm-1",
                "export_type": "pdf",
                "start_time": 1700000000000,
                "end_time": 1700003600000,
            },
            "https://skyeye.local/skyeye/v1/alarm/alarm/info/download",
            "alarm_alarm_info_download",
        ),
    ],
)
async def test_skyeye_download_tools_return_binary_payload(yaml_name, call_kwargs, expected_url, expected_api):
    tool = _load_tool(yaml_name)
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "skyeye_api_key": "login-key-123",
    }.get(key)
    fake_session = _FakeSession([
        _FakeResponse(json_payload={"access_token": "token-123", "status": 200}),
        _FakeResponse(text_payload='<html><meta name="csrf-token" content="abcdef1234567890"></html>'),
        _FakeResponse(
            bytes_payload=b"\x89PNG\r\n",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="evidence.bin"',
            },
        ),
    ])

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:skyeye_api_key}",
                "base_url": "https://skyeye.local/skyeye",
                "username": "skyeye",
            },
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(ToolContext(session_id="test", message_id="test"), **call_kwargs)

    assert result.success is True
    assert result.metadata["api"] == expected_api
    assert result.output["content_type"] == "application/octet-stream"
    assert result.output["encoding"] == "base64"
    assert result.output["filename"] == "evidence.bin"
    assert result.output["content_base64"] == "iVBORw0K"

    method, request_url, request_kwargs = fake_session.calls[2]
    assert method == "GET"
    assert request_url == expected_url
    assert request_kwargs["params"]["csrf_token"] == "abcdef1234567890"
    assert request_kwargs["params"]["alarm_id"] == "alarm-1"
