"""Official MCP SDK stdio server exposing the same bounded agent invocation."""
import asyncio
from contextlib import chdir, redirect_stdout
from pathlib import Path
import sys

from flocks.cli.headless import ExecOptions, run_headless
from flocks.session.runtime_controls import RuntimeControls


def create_server(directory: Path, permission_mode: str = "default", allowed_tools: tuple[str, ...] = (),
                  disallowed_tools: tuple[str, ...] = (), sandbox: str | None = None, max_budget: float | None = None):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("Flocks", instructions="Run Flocks agent turns. Permissions are fixed by the server operator.")
    lock = asyncio.Lock()

    @server.tool()
    async def flocks_exec(prompt: str, session_id: str | None = None, model: str | None = None) -> dict:
        """Run one agent turn to completion, optionally resuming an existing session."""
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        async with lock:
            options = ExecOptions(
                directory=directory, prompt=prompt, session_id=session_id, model=model, sandbox=sandbox,
                controls=RuntimeControls(permission_mode=permission_mode, allowed_tools=allowed_tools,
                                         disallowed_tools=disallowed_tools, max_budget=max_budget),
            )
            with chdir(directory), redirect_stdout(sys.stderr):
                result = await run_headless(options)
            if not result["success"]:
                raise RuntimeError(result["error"])
            return result
    return server


async def serve_stdio(server):
    import anyio
    from mcp.server.stdio import stdio_server

    # Bind protocol streams before redirecting plugin/library diagnostics.
    protocol_stdout = anyio.wrap_file(sys.stdout)
    protocol_stdin = anyio.wrap_file(sys.stdin)
    with redirect_stdout(sys.stderr):
        try:
            async with stdio_server(stdin=protocol_stdin, stdout=protocol_stdout) as (reader, writer):
                await server._mcp_server.run(reader, writer, server._mcp_server.create_initialization_options())
        finally:
            from flocks.cli.runtime import close_cli_resources
            await close_cli_resources()
