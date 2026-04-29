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
        / "ngtip_v5_1_5"
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

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_ngtip_query_uses_ssl_false_by_default():
    tool = _load_tool("ngtip_query.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 0,
                    "data": {"resource": "8.8.8.8", "intelligence": []},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "apikey-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:ngtip_apikey}",
                "query_base_url": "https://ngtip-query.local:8090",
            },
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="query_ip",
            resource="8.8.8.8",
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "GET"
    assert url == "https://ngtip-query.local:8090/tip_api/v5/ip"
    assert kwargs["params"] == {"apikey": "apikey-1", "resource": "8.8.8.8"}
    assert kwargs["ssl"] is False


@pytest.mark.asyncio
async def test_ngtip_platform_honors_verify_ssl_true():
    tool = _load_tool("ngtip_platform.yaml")
    fake_session = _FakeSession(
        [
            _FakeResponse(
                json_payload={
                    "response_code": 0,
                    "verbose_msg": "ok",
                    "data": {"user_id": 1},
                }
            )
        ]
    )
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = "apikey-1"

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={
                "apiKey": "{secret:ngtip_apikey}",
                "base_url": "https://ngtip.local",
                "verify_ssl": True,
            },
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="platform_add_user",
            username="alice",
            roles=["admin"],
        )

    assert result.success is True
    method, url, kwargs = fake_session.calls[0]
    assert method == "POST"
    assert url == "https://ngtip.local/tip/v5/add_user"
    assert kwargs["json"] == {
        "apikey": "apikey-1",
        "username": "alice",
        "roles": ["admin"],
    }
    assert kwargs["ssl"] is True
