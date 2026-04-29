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
    
    def __init__(self, cwd: str, host: str = "127.0.0.1", port: int = 0):
        self._cwd = cwd
        self._host = host
        self._port = port
        self._stream = NDJsonStream()
        self._agent = None
        self._sdk = None
        self._server = None
        self._shutdown = False
    
    async def start(self) -> None:
        """Start the ACP server"""
        from flocks.acp.agent import ACP, ACPConnection, ACPConfig
        from flocks.server.app import app
        import uvicorn
        
        # Initialize built-in hooks
        try:
            from flocks.config import Config
            config = await Config.get()
            
            # Register built-in hooks if memory is enabled
            if config.memory.enabled:
                from flocks.hooks.builtin import register_builtin_hooks
                register_builtin_hooks()
                log.info("acp.hooks.registered")
        except Exception as e:
            # Hook registration failure should not stop server startup
            log.warn("acp.hooks.register_failed", {"error": str(e)})

        # Bootstrap pluggable evolution module (parallels server.app).
        # Defaults to NoOp when evolution.enabled is false; ExtensionPoints
        # are still registered so PluginLoader.load_all() can discover them.
        try:
            from flocks.evolution import EvolutionEngine
            config = await Config.get()
            evo_cfg = getattr(config, "evolution", None)
            if evo_cfg is not None:
                evo_dict = evo_cfg.model_dump(exclude_none=False) if hasattr(evo_cfg, "model_dump") else dict(evo_cfg)
            else:
                evo_dict = {}
            EvolutionEngine.get().bootstrap(evo_dict)
            log.info("acp.evolution.bootstrapped", {"status": EvolutionEngine.get().status()["active"]})
        except Exception as e:
            log.warn("acp.evolution.bootstrap_failed", {"error": str(e)})
        
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
        
        # Create SDK client
        from flocks.server.client import FlocksClient
        self._sdk = FlocksClient(base_url=f"http://{self._host}:{actual_port}")
        
        # Initialize ACP
        acp_factory = await ACP.init(self._sdk)
        
        # Start stream
        await self._stream.start()
        
        # Create connection
        connection = ACPConnection(send_message=self._stream.write)
        
        # Create agent
        config = ACPConfig(sdk=self._sdk)
        self._agent = acp_factory["create"](connection, config)
        
        # Start event subscription
        self._agent.start_event_subscription()
        
        log.info("acp.started", {"port": actual_port})
        
        # Main message loop
        try:
            while not self._shutdown:
                try:
                    message = await asyncio.wait_for(
                        self._stream.read(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                await self._handle_message(message, connection)
        except Exception as e:
            log.error("acp.error", {"error": str(e)})
        finally:
            self._stream.close()
            if self._agent:
                self._agent.shutdown()
            server.should_exit = True
            await server_task
    
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
        elif method == "cancel":
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
    if cwd is None:
        cwd = os.getcwd()
    
    # Run ACP server
    server = ACPServer(cwd=cwd, host=host, port=port)
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        server.shutdown()
