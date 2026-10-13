import os
import sys
from unittest.mock import AsyncMock

import pytest

from flocks.cli.mcp_server import create_server


async def test_mcp_tool_calls_shared_engine(monkeypatch, tmp_path):
    import flocks.cli.mcp_server as module
    run = AsyncMock(return_value={"type": "result", "success": True, "session_id": "s", "result": "answer", "error": None})
    monkeypatch.setattr(module, "run_headless", run)
    server = create_server(tmp_path, allowed_tools=("read",), disallowed_tools=("bash",), max_budget=1)
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == ["flocks_exec"]
    await server.call_tool("flocks_exec", {"prompt": "hello", "session_id": "s"})
    options = run.await_args.args[0]
    assert options.session_id == "s"
    assert options.directory == tmp_path
    assert options.controls.allowed_tools == ("read",)
    assert options.controls.max_budget == 1


async def test_real_stdio_initialize_list_call_and_eof(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Zero budget makes tools/call exercise the real invocation engine without
    # provider access, session creation, or loading user plugins.
    env = {**os.environ, "FLOCKS_ROOT": str(tmp_path / "state")}
    env.pop("VIRTUAL_ENV", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "flocks.cli.main", "mcp", "serve", "-C", str(tmp_path), "--max-budget", "0"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            info = await client.initialize()
            assert info.serverInfo.name == "Flocks"
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ["flocks_exec"]
            result = await client.call_tool("flocks_exec", {"prompt": "hello"})
            assert result.isError
            assert "budget" in result.content[0].text


@pytest.mark.skipif(sys.platform == "win32", reason="sends a POSIX SIGINT")
def test_interrupt_stops_a_server_still_waiting_for_stdin(tmp_path):
    import json
    import select
    import signal
    import subprocess
    import time

    env = {**os.environ, "FLOCKS_ROOT": str(tmp_path / "state")}
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "flocks.cli.main", "mcp", "serve", "-C", str(tmp_path), "--max-budget", "0"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
    )
    try:
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        proc.stdin.write((json.dumps(initialize) + "\n").encode())
        proc.stdin.flush()
        response = b""
        deadline = time.monotonic() + 60
        while b"\n" not in response and time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.5)[0]:
                response += os.read(proc.stdout.fileno(), 65536)
        assert b'"result"' in response
        proc.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
        proc.stdin.flush()
        time.sleep(0.5)
        # stdin stays open, so the server is blocked waiting for the next line.
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


class _ChunkedStdin:
    """Unbuffered stdin stand-in: returns the given chunks, then EOF."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.reads = 0

    def read(self, size):
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""


async def test_stdin_lines_split_across_reads_and_end_at_eof():
    import asyncio
    from flocks.cli.mcp_server import _StdinLines

    # "你" is split between two reads; a line is decoded only once it is whole.
    you = "你".encode()
    reader = _StdinLines(_ChunkedStdin([b'{"a": 1}\n{"b": "' + you[:2], you[2:] + b'"}\n{"c"', b": 3}"]))
    lines = await asyncio.wait_for(_collect(reader), timeout=5)
    assert lines == ['{"a": 1}\n', '{"b": "你"}\n', '{"c": 3}']


async def test_stdin_reader_waits_for_a_slow_server():
    import asyncio
    from flocks.cli.mcp_server import _StdinLines

    lines = [f'{{"n": {i}}}\n' for i in range(500)]
    stdin = _ChunkedStdin(["".join(lines).encode()])
    reader = _StdinLines(stdin)
    await asyncio.sleep(0.3)
    # Nobody has taken a line yet: only the 64-line backlog is held, not the burst.
    assert reader._queue.qsize() <= 64
    assert await asyncio.wait_for(_collect(reader), timeout=5) == lines


async def _collect(reader):
    return [line async for line in reader]
