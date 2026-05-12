from unittest.mock import AsyncMock

import pytest

from flocks.mcp.client import McpClient


class TestMcpClientTransportSelection:
    @pytest.mark.asyncio
    async def test_connect_routes_remote_servers_to_remote_owner(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls: list[str] = []

        async def fake_remote(startup_future):
            calls.append("remote")
            startup_future.set_result(None)

        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
        )
        monkeypatch.setattr(client, "_connect_remote", fake_remote)

        await client.connect()

        assert calls == ["remote"]

    @pytest.mark.asyncio
    async def test_connect_routes_stdio_servers_to_local_owner(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls: list[str] = []

        async def fake_local(startup_future):
            calls.append("local")
            startup_future.set_result(None)

        client = McpClient(
            name="demo",
            server_type="stdio",
            command=["python", "-m", "demo"],
        )
        monkeypatch.setattr(client, "_connect_local", fake_local)

        await client.connect()

        assert calls == ["local"]

    @pytest.mark.asyncio
    async def test_timeout_none_defaults_to_safe_float(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        observed: list[float] = []

        async def fake_remote(startup_future):
            observed.append(client.timeout)
            startup_future.set_result(None)

        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
            timeout=None,
        )
        monkeypatch.setattr(client, "_connect_remote", fake_remote)

        await client.connect()

        assert observed == [30.0]

    @pytest.mark.asyncio
    async def test_unknown_type_raises_value_error(self):
        client = McpClient(
            name="demo",
            server_type="websocket",
            url="wss://example.com",
        )

        with pytest.raises(ValueError, match="Unknown server type: websocket"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_failed_connect_cleans_up_owner_runtime_state(self):
        client = McpClient(
            name="demo",
            server_type="websocket",
            url="wss://example.com",
        )

        with pytest.raises(ValueError, match="Unknown server type: websocket"):
            await client.connect()

        assert client._connected is False
        assert client._command_queue is None
        assert client._owner_task is None
        assert isinstance(client._owner_error, ValueError)

    @pytest.mark.asyncio
    async def test_already_connected_skips_new_owner_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
        )
        client._connected = True
        fake_owner = AsyncMock()
        monkeypatch.setattr(client, "_run_connection_owner", fake_owner)

        await client.connect()

        fake_owner.assert_not_called()
