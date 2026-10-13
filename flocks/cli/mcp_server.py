"""Official MCP SDK stdio server exposing the same bounded agent invocation."""
import asyncio
from contextlib import chdir, redirect_stdout
from pathlib import Path
import sys
import threading

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


class _StdinLines:
    """Protocol lines from stdin, read by a daemon thread.

    anyio.wrap_file reads in a worker thread that cancellation cannot stop and
    interpreter exit waits for, so after Ctrl+C the server hung until one more
    line or EOF arrived. This thread reads the unbuffered stream, so it holds no
    buffer lock at shutdown and the process may exit while it is still blocked.
    """

    # Lines read ahead of the protocol reader; past this the thread waits.
    BACKLOG = 64

    def __init__(self, raw):
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(self.BACKLOG)
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._pump, args=(raw,), name="flocks-mcp-stdin", daemon=True).start()

    def _put(self, item: str | None) -> None:
        put = self._queue.put(item)
        try:
            future = asyncio.run_coroutine_threadsafe(put, self._loop)
        except RuntimeError:  # the loop is already closed
            put.close()
            raise
        future.result()

    def _pump(self, raw) -> None:
        pending = b""
        try:
            while chunk := raw.read(65536):
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    self._put(line.decode("utf-8", "replace") + "\n")
            if pending:
                self._put(pending.decode("utf-8", "replace"))
        except Exception:  # stdin failed or closed, or the loop is gone
            pass
        try:
            self._put(None)
        except Exception:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        line = await self._queue.get()
        if line is None:
            self._queue.put_nowait(None)
            raise StopAsyncIteration
        return line


async def serve_stdio(server):
    import anyio
    from mcp.server.stdio import stdio_server

    # Bind protocol streams before redirecting plugin/library diagnostics.
    protocol_stdout = anyio.wrap_file(sys.stdout)
    raw_stdin = getattr(getattr(sys.stdin, "buffer", None), "raw", None)
    # Without an unbuffered stream behind sys.stdin, keep the plain wrapper.
    protocol_stdin = _StdinLines(raw_stdin) if raw_stdin is not None else anyio.wrap_file(sys.stdin)
    with redirect_stdout(sys.stderr):
        try:
            async with stdio_server(stdin=protocol_stdin, stdout=protocol_stdout) as (reader, writer):
                await server._mcp_server.run(reader, writer, server._mcp_server.create_initialization_options())
        finally:
            from flocks.cli.runtime import close_cli_resources
            await close_cli_resources()
