import os
import sys
from unittest.mock import AsyncMock

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
