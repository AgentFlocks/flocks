"""
ACP CLI command

Starts ACP (Agent Client Protocol) server for editor integration (e.g., Zed).
Ported from original cli/cmd/acp.ts
"""

import asyncio
import json
import sys
import os
from typing import Optional, Dict, Any

import typer
from rich.console import Console

from flocks.utils.log import Log


acp_app = typer.Typer(
    name="acp",
    help="Start ACP (Agent Client Protocol) server",
)

console = Console(stderr=True)
log = Log.create(service="acp.command")


class NDJsonStream:
    """
    Newline-delimited JSON stream handler
    
    Handles JSON-RPC communication over stdin/stdout.
    Matches TypeScript ndJsonStream function.
    """
    
    def __init__(self):
        self._buffer = ""
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._read_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start reading from stdin"""
        self._read_task = asyncio.create_task(self._read_loop())
    
    async def _read_loop(self) -> None:
        """Read lines from stdin"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                
                line_str = line.decode("utf-8").strip()
                if line_str:
                    try:
                        message = json.loads(line_str)
                        await self._message_queue.put(message)
                    except json.JSONDecodeError as e:
                        log.error("json.parse.error", {"error": str(e)})
            except Exception as e:
                log.error("read.error", {"error": str(e)})
                break
    
    async def read(self) -> Dict[str, Any]:
        """Read next message"""
        return await self._message_queue.get()
    
    async def write(self, message: Dict[str, Any]) -> None:
        """Write message to stdout"""
        line = json.dumps(message, separators=(",", ":")) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()
    
    def close(self) -> None:
        """Close the stream"""
        if self._read_task:
            self._read_task.cancel()


class ACPServer:
    """
    ACP server implementation
    
    Handles JSON-RPC over stdio for ACP protocol.
    """
    
    def __init__(self, cwd: str, host: str = "127.0.0.1", port: int = 0, server_url: Optional[str] = None):
        self._cwd = cwd
        self._host = host
        self._port = port
        # A running Flocks service to drive (``--server``): nothing is started
        # here, every call goes to it with the user's own login.
        self._server_url = server_url
        self._stream = NDJsonStream()
        self._agent = None
        self._sdk = None
        self._server = None
        self._shutdown = False
    
    async def start(self) -> None:
        """Start the ACP server"""
        if self._server_url:
            await self._start_against(self._server_url)
            return
        await self._start_with_local_service()

    async def _start_against(self, server_url: str) -> None:
        """Bridge to a running service: no runtime here, the service's own identity checks apply."""
        from flocks.server.client import FlocksClient, ServiceUnavailable

        sdk = FlocksClient(
            base_url=server_url,
            session_id=os.environ.get("FLOCKS_SESSION") or None,
            username=os.environ.get("FLOCKS_USERNAME") or None,
            password=os.environ.get("FLOCKS_PASSWORD") or None,
            api_token=os.environ.get("FLOCKS_API_TOKEN") or None,
        )
        try:
            await sdk.check()
        except ServiceUnavailable as exc:
            # Said once, plainly, and nothing else is started in its place.
            sys.stderr.write(f"flocks acp: {exc}\n")
            await sdk.close()
            raise SystemExit(1) from exc
        self._sdk = sdk
        try:
            await self._serve_stdio()
        finally:
            await sdk.close()

    async def _serve_stdio(self, on_exit=None) -> None:
        from flocks.acp.agent import ACP, ACPConnection, ACPConfig

        acp_factory = await ACP.init(self._sdk)
        await self._stream.start()
        connection = ACPConnection(send_message=self._stream.write)
        config = ACPConfig(sdk=self._sdk)
        self._agent = acp_factory["create"](connection, config)
        self._agent.start_event_subscription()
        log.info("acp.started", {"server": self._server_url or "local"})
        # A prompt is answered when its turn ends; meanwhile the editor sends a
        # cancel, or answers a permission the turn is waiting on.  So requests
        # run as tasks of their own and the loop keeps reading; replies to what
        # the agent asked are handed over at once.
        running: set[asyncio.Task] = set()
        try:
            while not self._shutdown:
                try:
                    message = await asyncio.wait_for(self._stream.read(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if "result" in message or "error" in message:
                    await self._handle_message(message, connection)
                    continue
                task = asyncio.create_task(self._handle_message(message, connection))
                running.add(task)
                task.add_done_callback(running.discard)
        except Exception as e:
            log.error("acp.error", {"error": str(e)})
        finally:
            for task in list(running):
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            self._stream.close()
            if self._agent:
                self._agent.shutdown()
            if on_exit is not None:
                await on_exit()

    async def _start_with_local_service(self) -> None:
        """No service named: run one in this process, for this editor only (not in a managed install)."""
        from flocks.server.app import app
        import uvicorn
        
        # Initialize built-in hooks
        try:
            from flocks.hooks.builtin import register_builtin_hooks

            register_builtin_hooks()
            log.info("acp.hooks.registered")
        except Exception as e:
            # Hook registration failure should not stop server startup
            log.warn("acp.hooks.register_failed", {"error": str(e)})
        
        # Start internal HTTP server for SDK
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        
        # Start server in background
        server_task = asyncio.create_task(server.serve())
        
        # Wait for server to be ready
        while not server.started:
            await asyncio.sleep(0.1)
        
        # Get actual port
        actual_port = server.servers[0].sockets[0].getsockname()[1] if server.servers else self._port
        
        # The local service is driven like any other: over HTTP, with this
        # machine's API token (minted on first use, as the TUI does).
        from flocks.cli.main import _ensure_server_api_token
        from flocks.security import get_secret_manager
        from flocks.server.auth import API_TOKEN_SECRET_ID
        from flocks.server.client import FlocksClient

        _ensure_server_api_token()
        self._sdk = FlocksClient(
            base_url=f"http://{self._host}:{actual_port}",
            api_token=get_secret_manager().get(API_TOKEN_SECRET_ID),  # secret-guard: allow 误报：参数名，值来自密钥管理
        )

        async def stop_local_service() -> None:
            await self._sdk.close()
            server.should_exit = True
            await server_task

        await self._serve_stdio(on_exit=stop_local_service)

    async def _handle_message(
        self,
        message: Dict[str, Any],
        connection: "ACPConnection"
    ) -> None:
        """Handle incoming JSON-RPC message"""
        from flocks.acp.session import RequestError
        
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        
        # Handle response
        if "result" in message or "error" in message:
            if msg_id is not None:
                connection.handle_response(
                    msg_id,
                    result=message.get("result"),
                    error=message.get("error"),
                )
            return
        
        # Handle request
        try:
            result = await self._dispatch_method(method, params)
            
            if msg_id is not None:
                await self._stream.write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                })
        except RequestError as e:
            if msg_id is not None:
                await self._stream.write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": e.code,
                        "message": e.message,
                        "data": e.data,
                    },
                })
        except Exception as e:
            log.error("dispatch.error", {"method": method, "error": str(e)})
            if msg_id is not None:
                await self._stream.write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32603,
                        "message": str(e),
                    },
                })
    
    async def _dispatch_method(self, method: str, params: Dict[str, Any]) -> Any:
        """Dispatch method to agent"""
        if not self._agent:
            raise Exception("Agent not initialized")
        
        if method == "initialize":
            return await self._agent.initialize(params)
        elif method == "authenticate":
            return await self._agent.authenticate(params)
        elif method == "session/new":
            return await self._agent.new_session(params)
        elif method == "session/load":
            return await self._agent.load_session(params)
        elif method == "session/setModel":
            return await self._agent.set_session_model(params)
        elif method == "session/setMode":
            return await self._agent.set_session_mode(params)
        elif method == "session/prompt":
            return await self._agent.prompt(params)
        elif method in {"cancel", "session/cancel"}:
            await self._agent.cancel(params)
            return None
        else:
            raise Exception(f"Unknown method: {method}")
    
    def shutdown(self) -> None:
        """Shutdown the server"""
        self._shutdown = True


@acp_app.callback(invoke_without_command=True)
def acp_command(
    cwd: Optional[str] = typer.Option(
        None, "--cwd",
        help="Working directory"
    ),
    host: str = typer.Option(
        "127.0.0.1", "--host", "-h",
        help="Server host"
    ),
    port: int = typer.Option(
        0, "--port", "-p",
        help="Server port (0 for auto)"
    ),
    server: Optional[str] = typer.Option(
        None, "--server",
        help=(
            "URL of a running Flocks service to drive (e.g. http://127.0.0.1:8000); nothing is "
            "started here. Credentials: FLOCKS_SESSION (a login session), FLOCKS_USERNAME and "
            "FLOCKS_PASSWORD, or FLOCKS_API_TOKEN."
        ),
    ),
):
    """
    Start ACP (Agent Client Protocol) server
    
    The ACP server enables integration with editors like Zed through
    the Agent Client Protocol. It communicates via JSON-RPC over stdio.
    
    Usage with Zed:
    
    Add to ~/.config/zed/settings.json:
    
        {
          "agent_servers": {
            "Flocks": {
              "command": "flocks",
              "args": ["acp"]
            }
          }
        }
    """
    from flocks.cli.managed import refuse_local_runtime_when_managed

    # Without --server, ACP runs a service of its own in this process; in a
    # managed install the runtime is the supervised service and nothing starts
    # beside it, so there it only drives that service.
    if not server:
        refuse_local_runtime_when_managed("flocks acp without --server")

    if cwd is None:
        cwd = os.getcwd()
    
    # Run ACP server
    acp_server = ACPServer(cwd=cwd, host=host, port=port, server_url=server)
    
    try:
        asyncio.run(acp_server.start())
    except KeyboardInterrupt:
        acp_server.shutdown()
