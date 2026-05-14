"""
MCP Client Wrapper

Client implementation based on official MCP SDK, supporting Streamable HTTP and SSE transports.
"""

import asyncio
import contextlib
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from flocks.mcp.types import McpResource, McpToolDef
from flocks.mcp.utils import build_mcp_headers, build_mcp_url
from flocks.utils.log import Log

log = Log.create(service="mcp.client")


def _extract_root_cause(exc: BaseException) -> str:
    """Extract the root cause message from potentially nested ExceptionGroups.
    
    MCP SDK uses anyio TaskGroups which wrap errors in ExceptionGroup.
    This helper unwraps them to get a human-readable error message.
    """
    # Handle ExceptionGroup (Python 3.11+) and BaseExceptionGroup
    if isinstance(exc, BaseExceptionGroup):
        # Get the first sub-exception and recurse
        if exc.exceptions:
            return _extract_root_cause(exc.exceptions[0])
    # For httpx HTTPStatusError, extract status code and URL
    if hasattr(exc, 'response') and hasattr(exc.response, 'status_code'):
        status = exc.response.status_code
        url = str(exc.request.url) if hasattr(exc, 'request') else 'unknown'
        # Mask sensitive parts of URL
        if '?' in url:
            url = url.split('?')[0] + '?...'
        return f"HTTP {status} from {url}"
    return str(exc)


def _normalize_timeout(timeout: object) -> float:
    """Normalize optional timeout inputs to a positive float."""
    try:
        value = float(timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


@dataclass(slots=True)
class _ClientCommand:
    """A serialized request executed by the MCP owner task."""

    action: Literal["list_tools", "call_tool", "list_resources", "read_resource", "disconnect"]
    payload: Dict[str, Any] = field(default_factory=dict)
    response: asyncio.Future[Any] | None = None


class McpClient:
    """
    MCP Client - Wraps official SDK
    
    Supports remote servers (Streamable HTTP / SSE) and local servers (Stdio)
    
    Server types:
    - "remote" / "sse": Try Streamable HTTP first, fall back to SSE
    - "local" / "stdio": Connect via Stdio subprocess
    """
    
    def __init__(
        self, 
        name: str, 
        server_type: str,
        url: Optional[str] = None,
        command: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        env: Optional[Dict[str, str]] = None,
        auth_config: Optional[Dict[str, Any]] = None,
        transport: Literal["auto", "sse", "http"] = "auto",
        timeout: float = 30.0
    ):
        """
        Initialize MCP client
        
        Args:
            name: Server name
            server_type: Server type (remote | sse | local)
            url: Server URL (required for remote/sse type)
            command: Startup command (required for local type), first element is the executable
            headers: Extra HTTP headers for remote MCP connections
            env: Extra environment variables for local server subprocess
            auth_config: Authentication configuration
            transport: Preferred remote transport (auto | sse | http)
            timeout: Timeout in seconds
        """
        self.name = name
        self.server_type = server_type
        self.url = url
        self.command = command
        self.headers = headers
        self.env = env
        self.auth_config = auth_config
        self.transport = transport
        self.timeout = _normalize_timeout(timeout)
        
        self.session: Optional[ClientSession] = None
        self._streams = None
        self._connected = False
        self._transport_type: Optional[str] = None
        self._command_queue: asyncio.Queue[_ClientCommand] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._owner_error: BaseException | None = None
    
    async def connect(self) -> None:
        """
        Connect to MCP server
        
        Raises:
            ValueError: If server type is unknown
            RuntimeError: If connection fails
        """
        if self._connected:
            log.warn("mcp.client.already_connected", {"server": self.name})
            return

        if self._owner_task and not self._owner_task.done():
            log.warn("mcp.client.connect_in_progress", {"server": self.name})
            return

        loop = asyncio.get_running_loop()
        startup_future: asyncio.Future[None] = loop.create_future()
        self._owner_error = None
        self._command_queue = asyncio.Queue()

        owner_task = asyncio.create_task(
            self._run_connection_owner(startup_future),
            name=f"mcp-owner:{self.name}",
        )
        owner_task.add_done_callback(self._handle_owner_task_done)
        self._owner_task = owner_task

        try:
            await asyncio.wait_for(startup_future, timeout=self.timeout + 1.0)
        except Exception:
            await self._cancel_owner_task()
            self._reset_runtime_state()
            raise

    async def _run_connection_owner(self, startup_future: asyncio.Future[None]) -> None:
        """Own the entire MCP session lifecycle inside one asyncio task."""
        try:
            if self.server_type in ("remote", "sse"):
                await self._connect_remote(startup_future)
            elif self.server_type in ("local", "stdio"):
                await self._connect_local(startup_future)
            else:
                raise ValueError(f"Unknown server type: {self.server_type}")
        except Exception as exc:
            if not startup_future.done():
                startup_future.set_exception(exc)
            else:
                await self._fail_pending_commands(
                    RuntimeError(f"Connection lost: {self.name}: {_extract_root_cause(exc)}")
                )
            raise
        finally:
            if not startup_future.done():
                startup_future.set_exception(
                    RuntimeError(f"Connection closed before initialization: {self.name}")
                )
            self._connected = False
            self.session = None
            self._streams = None
            self._transport_type = None
            await self._fail_pending_commands(RuntimeError(f"Client not connected: {self.name}"))

    def _handle_owner_task_done(self, task: asyncio.Task[None]) -> None:
        """Retrieve background task exceptions so asyncio does not emit warnings."""
        try:
            owner_error = task.exception()
        except asyncio.CancelledError:
            owner_error = None

        self._owner_error = owner_error
        if self._owner_task is task:
            self._owner_task = None

        if owner_error is not None:
            log.error("mcp.client.owner_task_error", {
                "server": self.name,
                "error": _extract_root_cause(owner_error),
            })

    async def _cancel_owner_task(self) -> None:
        """Cancel and await the owner task if it is still running."""
        owner_task = self._owner_task
        if owner_task is None:
            return
        if not owner_task.done():
            owner_task.cancel()
        try:
            await owner_task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            # Connection startup may already have failed; preserve the error so
            # callers can finish cleanup and still surface the root cause.
            if self._owner_error is None:
                self._owner_error = exc

    def _reset_runtime_state(self, clear_owner_error: bool = False) -> None:
        """Reset local state after disconnects or failed startups."""
        self.session = None
        self._streams = None
        self._connected = False
        self._transport_type = None
        self._command_queue = None
        if self._owner_task is not None and self._owner_task.done():
            self._owner_task = None
        if clear_owner_error:
            self._owner_error = None

    async def _connect_remote(self, startup_future: asyncio.Future[None]) -> None:
        """Connect to a remote server using the configured transport strategy."""
        full_url = build_mcp_url(self.url, self.auth_config)
        request_headers = build_mcp_headers(self.headers, self.auth_config)

        if self.transport == "http":
            log.info("mcp.client.connecting", {
                "server": self.name,
                "type": "remote",
                "strategy": "streamable_http_only",
            })
            await self._connect_streamable_http_only(full_url, request_headers, startup_future)
            return

        if self.transport == "sse":
            log.info("mcp.client.connecting", {
                "server": self.name,
                "type": "remote",
                "strategy": "sse_only",
            })
            await self._connect_sse_only(full_url, request_headers, startup_future)
            return

        log.info("mcp.client.connecting", {
            "server": self.name,
            "type": "remote",
            "strategy": "streamable_http_then_sse",
        })
        await self._connect_auto(full_url, request_headers, startup_future)

    async def _connect_streamable_http_only(
        self,
        full_url: str,
        headers: Optional[Dict[str, str]],
        startup_future: asyncio.Future[None],
    ) -> None:
        """Connect using only Streamable HTTP."""
        try:
            await self._run_remote_transport(
                transport_name="streamable_http",
                full_url=full_url,
                headers=headers,
                startup_future=startup_future,
                transport_factory=self._create_streamable_http_streams,
            )
        except asyncio.TimeoutError as exc:
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "streamable_http",
            })
            raise RuntimeError(f"Connection timeout: {self.name}") from exc
        except Exception as exc:
            raise RuntimeError(f"Connection failed: {self.name}: {_extract_root_cause(exc)}") from exc

    async def _connect_sse_only(
        self,
        full_url: str,
        headers: Optional[Dict[str, str]],
        startup_future: asyncio.Future[None],
    ) -> None:
        """Connect using only SSE."""
        try:
            await self._run_remote_transport(
                transport_name="sse",
                full_url=full_url,
                headers=headers,
                startup_future=startup_future,
                transport_factory=self._create_sse_streams,
            )
        except asyncio.TimeoutError as exc:
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "sse",
            })
            raise RuntimeError(f"Connection timeout: {self.name}") from exc
        except Exception as exc:
            raise RuntimeError(f"Connection failed: {self.name}: {_extract_root_cause(exc)}") from exc

    async def _connect_auto(
        self,
        full_url: str,
        headers: Optional[Dict[str, str]],
        startup_future: asyncio.Future[None],
    ) -> None:
        """Connect using auto-detection: HTTP first, then SSE."""
        try:
            await self._run_remote_transport(
                transport_name="streamable_http",
                full_url=full_url,
                headers=headers,
                startup_future=startup_future,
                transport_factory=self._create_streamable_http_streams,
            )
            return
        except asyncio.TimeoutError as exc:
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "streamable_http",
            })
            raise RuntimeError(f"Connection timeout: {self.name}") from exc
        except Exception as exc:
            if startup_future.done():
                raise
            log.info("mcp.client.streamable_http_failed", {
                "server": self.name,
                "error": _extract_root_cause(exc),
                "fallback": "sse",
            })

        try:
            await self._run_remote_transport(
                transport_name="sse",
                full_url=full_url,
                headers=headers,
                startup_future=startup_future,
                transport_factory=self._create_sse_streams,
            )
        except Exception as exc:
            root_cause = _extract_root_cause(exc)
            log.error("mcp.client.all_transports_failed", {
                "server": self.name,
                "error": root_cause,
            })
            raise RuntimeError(f"Connection failed: {self.name}: {root_cause}") from exc

    async def _run_remote_transport(
        self,
        transport_name: Literal["streamable_http", "sse"],
        full_url: str,
        headers: Optional[Dict[str, str]],
        startup_future: asyncio.Future[None],
        transport_factory,
    ) -> None:
        """Run one remote transport from startup until disconnect."""
        async with transport_factory(full_url, headers) as streams:
            self._streams = streams
            await self._run_connected_session(transport_name, streams, startup_future)

    @asynccontextmanager
    async def _create_streamable_http_streams(
        self,
        full_url: str,
        headers: Optional[Dict[str, str]],
    ):
        """Create a modern Streamable HTTP transport context."""
        timeout = httpx.Timeout(self.timeout, read=60 * 5)
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
            async with streamable_http_client(full_url, http_client=http_client) as streams:
                yield streams

    @asynccontextmanager
    async def _create_sse_streams(
        self,
        full_url: str,
        headers: Optional[Dict[str, str]],
    ):
        """Create an SSE transport context."""
        async with sse_client(full_url, headers=headers, timeout=self.timeout) as streams:
            yield streams

    async def _run_connected_session(
        self,
        transport_name: Literal["streamable_http", "sse", "stdio"],
        streams,
        startup_future: asyncio.Future[None],
    ) -> None:
        """Initialize the MCP session and then serve queued commands."""
        if len(streams) == 3:
            read_stream, write_stream, _ = streams
        else:
            read_stream, write_stream = streams

        async with ClientSession(read_stream, write_stream) as session:
            self.session = session
            init_result = await asyncio.wait_for(session.initialize(), timeout=self.timeout)
            self._connected = True
            self._transport_type = transport_name
            if not startup_future.done():
                startup_future.set_result(None)
            log.info("mcp.client.connected", {
                "server": self.name,
                "transport": transport_name,
                "protocol_version": getattr(init_result, "protocolVersion", "unknown"),
                "server_info": getattr(init_result, "serverInfo", {}),
            })
            await self._serve_commands(session)

    async def _serve_commands(self, session: ClientSession) -> None:
        """Process serialized commands until disconnect is requested."""
        if self._command_queue is None:
            raise RuntimeError(f"Command queue not initialized: {self.name}")

        while True:
            command = await self._command_queue.get()
            if command.action == "disconnect":
                if command.response is not None and not command.response.done():
                    command.response.set_result(None)
                return

            try:
                result = await self._execute_command(session, command)
            except Exception as exc:
                if command.response is not None and not command.response.done():
                    command.response.set_exception(exc)
            else:
                if command.response is not None and not command.response.done():
                    command.response.set_result(result)

    async def _execute_command(self, session: ClientSession, command: _ClientCommand) -> Any:
        """Execute one queued MCP command inside the owner task."""
        if command.action == "list_tools":
            result = await asyncio.wait_for(session.list_tools(), timeout=self.timeout)
            tools = [McpToolDef.from_sdk(tool) for tool in result.tools]
            log.debug("mcp.client.tools_listed", {
                "server": self.name,
                "count": len(tools),
            })
            return tools

        if command.action == "call_tool":
            tool_name = command.payload["name"]
            try:
                result = await asyncio.wait_for(
                    session.call_tool(name=tool_name, arguments=command.payload["arguments"]),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError as exc:
                log.error("mcp.client.call_timeout", {
                    "server": self.name,
                    "tool": tool_name,
                })
                from concurrent.futures import TimeoutError as _FuturesTimeoutError

                raise _FuturesTimeoutError(f"MCP工具调用超时 ({self.timeout}s): {tool_name}") from exc

            log.debug("mcp.client.tool_called", {
                "server": self.name,
                "tool": tool_name,
            })
            return result

        if command.action == "list_resources":
            result = await asyncio.wait_for(session.list_resources(), timeout=self.timeout)
            resources = [
                McpResource(
                    name=resource.name,
                    uri=resource.uri,
                    description=getattr(resource, "description", None),
                    mime_type=getattr(resource, "mimeType", None),
                    server=self.name,
                )
                for resource in result.resources
            ]
            log.debug("mcp.client.resources_listed", {
                "server": self.name,
                "count": len(resources),
            })
            return resources

        if command.action == "read_resource":
            uri = command.payload["uri"]
            result = await asyncio.wait_for(session.read_resource(uri=uri), timeout=self.timeout)
            log.debug("mcp.client.resource_read", {
                "server": self.name,
                "uri": uri,
            })
            return result

        raise ValueError(f"Unknown MCP command: {command.action}")

    async def _fail_pending_commands(self, error: Exception) -> None:
        """Fail any queued commands when the owner task exits."""
        if self._command_queue is None:
            return

        while True:
            try:
                command = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            if command.response is not None and not command.response.done():
                command.response.set_exception(error)
    
    @staticmethod
    def _read_stderr(stderr_file) -> str:
        """Read captured stderr from a temporary file, truncating if too long."""
        try:
            stderr_file.seek(0)
            output = stderr_file.read(4096).strip()
            return output
        except Exception:
            return ""

    @staticmethod
    def _flocks_mcp_prefix() -> str:
        """Return the npm prefix directory used for MCP package installs.

        All npx-based MCP servers share a single prefix so packages are
        cached on first use and reused offline afterwards.
        """
        prefix = Path.home() / ".flocks" / "plugins" / "mcp"
        prefix.mkdir(parents=True, exist_ok=True)
        return str(prefix)

    @asynccontextmanager
    async def _create_stdio_streams(self, server_params: StdioServerParameters, stderr_file):
        """Create stdio transport streams."""
        async with stdio_client(server_params, errlog=stderr_file) as streams:
            yield streams

    async def _connect_local(self, startup_future: asyncio.Future[None]) -> None:
        """Connect to local server via Stdio transport."""
        if not self.command:
            raise ValueError(f"No command specified for local server: {self.name}")

        executable = self.command[0]
        args = self.command[1:] if len(self.command) > 1 else []

        # Always start from the full system environment so PATH etc. are intact.
        merged_env: Dict[str, str] = {k: v for k, v in os.environ.items()}

        # For npx-based commands, set CWD to ~/.flocks/plugins/mcp so that
        # npx finds packages installed by preflight_install in ./node_modules/.bin/.
        # Also redirect npm_config_prefix for any global fallback lookups.
        cwd: Optional[str] = None
        if executable == "npx":
            mcp_prefix = self._flocks_mcp_prefix()
            cwd = mcp_prefix
            merged_env["npm_config_prefix"] = mcp_prefix
            local_bin = str(Path(mcp_prefix) / "node_modules" / ".bin")
            global_bin = str(Path(mcp_prefix) / "bin")
            existing_path = merged_env.get("PATH", "")
            for bin_dir in (local_bin, global_bin):
                if bin_dir not in existing_path:
                    existing_path = f"{bin_dir}{os.pathsep}{existing_path}"
            merged_env["PATH"] = existing_path
            log.info("mcp.client.npm_prefix", {
                "server": self.name,
                "prefix": mcp_prefix,
                "cwd": cwd,
            })

        if self.env:
            merged_env.update(self.env)

        server_params = StdioServerParameters(
            command=executable,
            args=args,
            env=merged_env,
            cwd=cwd,
        )

        log.info("mcp.client.connecting", {
            "server": self.name,
            "type": "local",
            "command": executable,
            "args": args,
        })

        # Keep stderr capture lifetime explicit: the file must outlive the stdio
        # transport context, but should close immediately once the attempt ends.
        with tempfile.TemporaryFile(mode="w+") as stderr_file:
            try:
                async with self._create_stdio_streams(server_params, stderr_file) as streams:
                    self._streams = streams
                    await self._run_connected_session("stdio", streams, startup_future)
            except asyncio.TimeoutError as exc:
                stderr_output = self._read_stderr(stderr_file)
                log.error("mcp.client.timeout", {
                    "server": self.name,
                    "transport": "stdio",
                    "stderr": stderr_output,
                })
                detail = f"Connection timeout (stdio): {self.name}"
                if stderr_output:
                    detail += f"\nServer stderr:\n{stderr_output}"
                raise RuntimeError(detail) from exc
            except Exception as exc:
                stderr_output = self._read_stderr(stderr_file)
                root_cause = _extract_root_cause(exc)
                log.error("mcp.client.stdio_failed", {
                    "server": self.name,
                    "error": root_cause,
                    "stderr": stderr_output,
                })
                detail = f"Stdio connection failed: {self.name}: {root_cause}"
                if stderr_output:
                    detail += f"\nServer stderr:\n{stderr_output}"
                raise RuntimeError(detail)
    
    async def disconnect(self) -> None:
        """Disconnect from server"""
        owner_task = self._owner_task
        if owner_task is None and not self._connected:
            return

        try:
            if owner_task is not None and not owner_task.done() and self._command_queue is not None:
                response = asyncio.get_running_loop().create_future()
                await self._command_queue.put(_ClientCommand(action="disconnect", response=response))
                await response
            elif owner_task is not None and not owner_task.done():
                owner_task.cancel()

            if owner_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await owner_task
        except Exception as exc:
            log.error("mcp.client.disconnect_error", {
                "server": self.name,
                "error": str(exc),
            })
        finally:
            self._reset_runtime_state(clear_owner_error=True)
            if self._owner_task is owner_task:
                self._owner_task = None
            log.info("mcp.client.disconnected", {"server": self.name})
    
    async def list_tools(self) -> List[McpToolDef]:
        """
        List available tools
        
        Returns:
            List of tool definitions
            
        Raises:
            RuntimeError: If not connected
        """
        try:
            result = await self._submit_command("list_tools")
            return result
        except Exception as exc:
            log.error("mcp.client.list_tools_error", {
                "server": self.name,
                "error": str(exc),
            })
            raise
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call a tool
        
        Args:
            name: Tool name
            arguments: Tool arguments
            
        Returns:
            Tool execution result
            
        Raises:
            RuntimeError: If not connected
        """
        try:
            return await self._submit_command("call_tool", name=name, arguments=arguments)
        except Exception as exc:
            log.error("mcp.client.call_error", {
                "server": self.name,
                "tool": name,
                "error": str(exc),
            })
            raise
    
    async def list_resources(self) -> List[McpResource]:
        """
        List available resources
        
        Returns:
            List of resources
            
        Raises:
            RuntimeError: If not connected
        """
        try:
            result = await self._submit_command("list_resources")
            return result
        except Exception as exc:
            log.error("mcp.client.list_resources_error", {
                "server": self.name,
                "error": str(exc),
            })
            raise
    
    async def read_resource(self, uri: str) -> Any:
        """
        Read a resource
        
        Args:
            uri: Resource URI
            
        Returns:
            Resource content
            
        Raises:
            RuntimeError: If not connected
        """
        try:
            return await self._submit_command("read_resource", uri=uri)
        except Exception as exc:
            log.error("mcp.client.read_resource_error", {
                "server": self.name,
                "uri": uri,
                "error": str(exc),
            })
            raise

    async def _submit_command(self, action: str, **payload: Any) -> Any:
        """Send a serialized command to the owner task."""
        if not self._connected or self._command_queue is None:
            if self._owner_error is not None:
                raise RuntimeError(
                    f"Client not connected: {self.name}: {_extract_root_cause(self._owner_error)}"
                ) from self._owner_error
            raise RuntimeError(f"Client not connected: {self.name}")

        owner_task = self._owner_task
        if owner_task is None:
            if self._owner_error is not None:
                raise RuntimeError(
                    f"Client not connected: {self.name}: {_extract_root_cause(self._owner_error)}"
                ) from self._owner_error
            raise RuntimeError(f"Client not connected: {self.name}")

        response = asyncio.get_running_loop().create_future()
        command = _ClientCommand(action=action, payload=payload, response=response)
        await self._command_queue.put(command)

        if owner_task.done() and not response.done():
            owner_error = self._owner_error or RuntimeError(f"Client not connected: {self.name}")
            response.set_exception(owner_error)

        return await response
    
    @property
    def is_connected(self) -> bool:
        """Whether connected to server"""
        return self._connected


__all__ = ['McpClient']
