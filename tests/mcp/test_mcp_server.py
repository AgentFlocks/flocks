from __future__ import annotations

import pytest

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
