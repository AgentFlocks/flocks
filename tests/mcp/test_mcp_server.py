from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, METHOD_NOT_FOUND

from flocks.mcp import server as mcp_server_module
from flocks.mcp.server import McpServerManager
from flocks.mcp.types import McpStatus


class _FakeMcpClient:
    def __init__(
        self,
        *,
        name: str,
        server_type: str,
        url=None,
        command=None,
        headers=None,
        env=None,
        auth_config=None,
        transport: str = "auto",
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.server_type = server_type
        self.url = url
        self.command = command
        self.headers = headers
        self.env = env
        self.auth_config = auth_config
        self.transport = transport
        self.timeout = timeout

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> list:
        return []

    async def list_resources(self) -> list:
        return []


def test_status_snapshot_is_read_only_and_does_not_initialize():
    manager = McpServerManager()
    status = SimpleNamespace(status=McpStatus.DISCONNECTED)
    manager._status["demo"] = status

    snapshot, initialized = manager.status_snapshot()

    assert initialized is False
    assert snapshot == {"demo": status}
    snapshot.clear()
    assert manager._status == {"demo": status}


@pytest.mark.asyncio
async def test_connect_and_register_accepts_legacy_env_alias(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class CapturingClient(_FakeMcpClient):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            captured["env"] = self.env

    monkeypatch.setattr("flocks.mcp.server.McpClient", CapturingClient)

    manager = McpServerManager()
    await manager._connect_and_register(
        "legacy-demo",
        {
            "type": "local",
            "command": ["python", "-m", "demo"],
            "env": {"DEMO_TOKEN": "secret"},
        },
    )

    assert captured["env"] == {"DEMO_TOKEN": "secret"}
    assert manager._status["legacy-demo"].status == McpStatus.CONNECTED


@pytest.mark.asyncio
async def test_connect_treats_missing_resources_method_as_optional(monkeypatch: pytest.MonkeyPatch):
    class ToolsOnlyClient(_FakeMcpClient):
        async def list_resources(self) -> list:
            raise McpError(
                ErrorData(code=METHOD_NOT_FOUND, message="Unsupported operation")
            )

    info_events: list[str] = []
    warn_events: list[str] = []
    monkeypatch.setattr(mcp_server_module, "McpClient", ToolsOnlyClient)
    monkeypatch.setattr(
        mcp_server_module.log,
        "info",
        lambda event, _data=None: info_events.append(event),
    )
    monkeypatch.setattr(
        mcp_server_module.log,
        "warn",
        lambda event, _data=None: warn_events.append(event),
    )

    manager = McpServerManager()
    await manager._connect_and_register(
        "tools-only",
        {"type": "local", "command": ["python", "-m", "demo"]},
    )

    assert manager._resources_cache["tools-only"] == []
    assert manager._status["tools-only"].status == McpStatus.CONNECTED
    assert "mcp.resources_unsupported" in info_events
    assert "mcp.resources_unavailable" not in warn_events


@pytest.mark.asyncio
async def test_connect_logs_resource_discovery_failure_once(monkeypatch: pytest.MonkeyPatch):
    class BrokenResourcesClient(_FakeMcpClient):
        async def list_resources(self) -> list:
            raise RuntimeError("resource endpoint unavailable")

    warn_events: list[str] = []
    error_events: list[str] = []
    monkeypatch.setattr(mcp_server_module, "McpClient", BrokenResourcesClient)
    monkeypatch.setattr(
        mcp_server_module.log,
        "warn",
        lambda event, _data=None: warn_events.append(event),
    )
    monkeypatch.setattr(
        mcp_server_module.log,
        "error",
        lambda event, _data=None: error_events.append(event),
    )

    manager = McpServerManager()
    await manager._connect_and_register(
        "broken-resources",
        {"type": "local", "command": ["python", "-m", "demo"]},
    )

    assert warn_events == ["mcp.resources_unavailable"]
    assert error_events == []


@pytest.mark.asyncio
async def test_init_is_serialized_for_concurrent_callers(monkeypatch: pytest.MonkeyPatch):
    manager = McpServerManager()
    config = SimpleNamespace(
        mcp={
            "demo": {
                "type": "remote",
                "url": "https://example.invalid/mcp",
                "enabled": True,
            }
        }
    )

    get_calls = 0
    connect_calls = 0
    retry_started = 0

    async def fake_get_config():
        nonlocal get_calls
        get_calls += 1
        await asyncio.sleep(0)
        return config

    async def fake_connect_and_register(name, server_config):
        nonlocal connect_calls
        connect_calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("connect boom")

    async def fake_retry_failed_servers():
        nonlocal retry_started
        retry_started += 1
        await asyncio.sleep(60)

    monkeypatch.setattr("flocks.mcp.server.Config.get", fake_get_config)
    monkeypatch.setattr(manager, "_connect_and_register", fake_connect_and_register)
    monkeypatch.setattr(manager, "_retry_failed_servers", fake_retry_failed_servers)

    try:
        await asyncio.gather(manager.init(), manager.init())
        await asyncio.sleep(0)

        assert get_calls == 1
        assert connect_calls == 1
        assert retry_started == 1
    finally:
        await manager.shutdown()
