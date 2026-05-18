import asyncio
import threading
from contextlib import asynccontextmanager
from types import MethodType
from unittest.mock import AsyncMock

import pytest

import flocks.mcp.client as mcp_client_module
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

    @pytest.mark.asyncio
    async def test_connect_local_closes_stderr_file_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        class _FakeTempFile:
            def __init__(self) -> None:
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                self.close()
                return False

            def seek(self, _offset: int) -> None:
                return None

            def read(self, _size: int = -1) -> str:
                return "stdio stderr"

            def close(self) -> None:
                self.closed = True

        fake_stderr = _FakeTempFile()
        client = McpClient(
            name="demo",
            server_type="stdio",
            command=["python", "-m", "demo"],
        )

        @asynccontextmanager
        async def broken_stdio(self, _server_params, stderr_file):
            assert stderr_file is fake_stderr
            raise RuntimeError("spawn failed")
            yield

        monkeypatch.setattr(
            mcp_client_module.tempfile,
            "TemporaryFile",
            lambda mode="w+": fake_stderr,
        )
        monkeypatch.setattr(
            client,
            "_create_stdio_streams",
            MethodType(broken_stdio, client),
        )

        startup_future = asyncio.get_running_loop().create_future()
        with pytest.raises(RuntimeError, match="Stdio connection failed"):
            await client._connect_local(startup_future)

        assert fake_stderr.closed is True


class TestMcpClientCrossLoopSubmission:
    @pytest.mark.asyncio
    async def test_call_tool_from_another_loop_reuses_owner_loop(self):
        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
        )
        owner_loop = asyncio.get_running_loop()
        client._connected = True
        client._owner_loop = owner_loop
        client._command_queue = asyncio.Queue()

        observed: dict[str, object] = {}

        async def owner() -> None:
            while True:
                command = await client._command_queue.get()
                observed["owner_loop"] = asyncio.get_running_loop()
                observed["action"] = command.action
                observed["payload"] = dict(command.payload)
                if command.action == "disconnect":
                    if command.response is not None and not command.response.done():
                        command.response.set_result(None)
                    return
                if command.response is not None and not command.response.done():
                    command.response.set_result(
                        {
                            "ok": True,
                            "loop_matches": asyncio.get_running_loop() is owner_loop,
                            "payload": dict(command.payload),
                        }
                    )

        client._owner_task = asyncio.create_task(owner())

        result = await asyncio.to_thread(
            lambda: asyncio.run(client.call_tool("demo_tool", {"value": "x"}))
        )

        assert result == {
            "ok": True,
            "loop_matches": True,
            "payload": {"name": "demo_tool", "arguments": {"value": "x"}},
        }
        assert observed["owner_loop"] is owner_loop
        assert observed["action"] == "call_tool"

        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_from_another_loop_finishes_owner_task(self):
        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
        )
        client._connected = True
        client._owner_loop = asyncio.get_running_loop()
        client._command_queue = asyncio.Queue()
        disconnect_seen = threading.Event()

        async def owner() -> None:
            while True:
                command = await client._command_queue.get()
                if command.action == "disconnect":
                    disconnect_seen.set()
                    if command.response is not None and not command.response.done():
                        command.response.set_result(None)
                    return

        owner_task = asyncio.create_task(owner())
        client._owner_task = owner_task

        await asyncio.to_thread(lambda: asyncio.run(client.disconnect()))

        assert disconnect_seen.is_set() is True
        assert owner_task.done() is True
        assert client._owner_loop is None
        assert client._command_queue is None
