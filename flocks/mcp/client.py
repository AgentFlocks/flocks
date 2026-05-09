"""
MCP Client Wrapper

Client implementation based on official MCP SDK, supporting Streamable HTTP and SSE transports
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters

from flocks.mcp.types import McpToolDef, McpResource
from flocks.mcp.utils import build_mcp_headers, build_mcp_url, resolve_env_var
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
        self.timeout = timeout
        
        self.session: Optional[ClientSession] = None
        self._streams = None
        self._streams_context = None
        self._connected = False
        self._transport_type: Optional[str] = None
    
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
        
        if self.server_type in ("remote", "sse"):
            # Both "remote" and "sse" use auto-detection:
            # try Streamable HTTP first, fall back to SSE.
            # This handles servers that only support one transport.
            await self._connect_remote()
        elif self.server_type in ("local", "stdio"):
            await self._connect_local()
        else:
            raise ValueError(f"Unknown server type: {self.server_type}")
    
    async def _connect_remote(self) -> None:
        """Connect to a remote server using the configured transport strategy."""
        full_url = build_mcp_url(self.url, self.auth_config)
        request_headers = build_mcp_headers(self.headers, self.auth_config)

        if self.transport == "http":
            log.info("mcp.client.connecting", {
                "server": self.name,
                "type": "remote",
                "strategy": "streamable_http_only",
            })
            await self._connect_streamable_http_only(full_url, request_headers)
            return

        if self.transport == "sse":
            log.info("mcp.client.connecting", {
                "server": self.name,
                "type": "remote",
                "strategy": "sse_only",
            })
            await self._connect_sse_only(full_url, request_headers)
            return

        log.info("mcp.client.connecting", {
            "server": self.name,
            "type": "remote",
            "strategy": "streamable_http_then_sse",
        })
        await self._connect_auto(full_url, request_headers)

    async def _connect_streamable_http_only(
        self, full_url: str, headers: Optional[Dict[str, str]]
    ) -> None:
        """Connect using only Streamable HTTP."""
        try:
            await self._do_connect_streamable_http(full_url, headers)
            self._transport_type = "streamable_http"
        except asyncio.TimeoutError:
            await self._cleanup_connection()
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "streamable_http",
            })
            raise RuntimeError(f"Connection timeout: {self.name}")
        except Exception as e:
            root_cause = _extract_root_cause(e)
            await self._cleanup_connection()
            raise RuntimeError(f"Connection failed: {self.name}: {root_cause}")

    async def _connect_sse_only(
        self, full_url: str, headers: Optional[Dict[str, str]]
    ) -> None:
        """Connect using only SSE."""
        try:
            await self._do_connect_sse(full_url, headers)
            self._transport_type = "sse"
        except asyncio.TimeoutError:
            await self._cleanup_connection()
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "sse",
            })
            raise RuntimeError(f"Connection timeout: {self.name}")
        except Exception as e:
            root_cause = _extract_root_cause(e)
            await self._cleanup_connection()
            raise RuntimeError(f"Connection failed: {self.name}: {root_cause}")

    async def _connect_auto(
        self, full_url: str, headers: Optional[Dict[str, str]]
    ) -> None:
        """Connect using auto-detection: HTTP first, then SSE."""
        try:
            await self._do_connect_streamable_http(full_url, headers)
            self._transport_type = "streamable_http"
            return
        except asyncio.TimeoutError:
            await self._cleanup_connection()
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "streamable_http",
            })
            raise RuntimeError(f"Connection timeout: {self.name}")
        except Exception as e:
            log.info("mcp.client.streamable_http_failed", {
                "server": self.name,
                "error": str(e),
                "fallback": "sse",
            })
            await self._cleanup_connection()

        try:
            await self._do_connect_sse(full_url, headers)
            self._transport_type = "sse"
            return
        except Exception as e:
            root_cause = _extract_root_cause(e)
            log.error("mcp.client.all_transports_failed", {
                "server": self.name,
                "error": root_cause,
            })
            await self._cleanup_connection()
            raise RuntimeError(f"Connection failed: {self.name}: {root_cause}")
    
    async def _do_connect_streamable_http(
        self, full_url: str, headers: Optional[Dict[str, str]] = None
    ) -> None:
        """Perform Streamable HTTP connection.
        
        Raises:
            asyncio.TimeoutError: If connection or initialization times out
            Exception: Any other connection error
        """
        self._streams_context = streamablehttp_client(
            full_url,
            headers=headers,
            timeout=self.timeout,
        )
        self._streams = await self._streams_context.__aenter__()
        read_stream, write_stream, _ = self._streams
        
        self.session = ClientSession(read_stream, write_stream)
        await self.session.__aenter__()
        init_result = await asyncio.wait_for(
            self.session.initialize(),
            timeout=self.timeout
        )
        
        self._connected = True
        log.info("mcp.client.connected", {
            "server": self.name,
            "transport": "streamable_http",
            "protocol_version": getattr(init_result, 'protocolVersion', 'unknown'),
            "server_info": getattr(init_result, 'serverInfo', {})
        })
    
    async def _do_connect_sse(
        self, full_url: str, headers: Optional[Dict[str, str]] = None
    ) -> None:
        """Perform SSE connection.
        
        Raises:
            asyncio.TimeoutError: If connection or initialization times out
            Exception: Any other connection error
        """
        self._streams_context = sse_client(
            full_url,
            headers=headers,
            timeout=self.timeout,
        )
        self._streams = await self._streams_context.__aenter__()
        read_stream, write_stream = self._streams
        
        self.session = ClientSession(read_stream, write_stream)
        await self.session.__aenter__()
        init_result = await asyncio.wait_for(
            self.session.initialize(),
            timeout=self.timeout
        )
        
        self._connected = True
        log.info("mcp.client.connected", {
            "server": self.name,
            "transport": "sse",
            "protocol_version": getattr(init_result, 'protocolVersion', 'unknown'),
            "server_info": getattr(init_result, 'serverInfo', {})
        })
    
    async def _cleanup_connection(self) -> None:
        """Clean up connection resources after a failed attempt"""
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._streams_context:
            try:
                await self._streams_context.__aexit__(None, None, None)
            except Exception:
                pass
        self.session = None
        self._streams = None
        self._streams_context = None
        self._connected = False
        self._transport_type = None
    
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

    async def _connect_local(self) -> None:
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

        stderr_file = tempfile.TemporaryFile(mode="w+")
        try:
            self._streams_context = stdio_client(server_params, errlog=stderr_file)
            self._streams = await self._streams_context.__aenter__()
            read_stream, write_stream = self._streams

            self.session = ClientSession(read_stream, write_stream)
            await self.session.__aenter__()
            init_result = await asyncio.wait_for(
                self.session.initialize(),
                timeout=self.timeout,
            )

            self._connected = True
            self._transport_type = "stdio"
            log.info("mcp.client.connected", {
                "server": self.name,
                "transport": "stdio",
                "protocol_version": getattr(init_result, 'protocolVersion', 'unknown'),
                "server_info": getattr(init_result, 'serverInfo', {}),
            })
        except asyncio.TimeoutError:
            stderr_output = self._read_stderr(stderr_file)
            await self._cleanup_connection()
            log.error("mcp.client.timeout", {
                "server": self.name,
                "transport": "stdio",
                "stderr": stderr_output,
            })
            detail = f"Connection timeout (stdio): {self.name}"
            if stderr_output:
                detail += f"\nServer stderr:\n{stderr_output}"
            raise RuntimeError(detail)
        except Exception as e:
            stderr_output = self._read_stderr(stderr_file)
            root_cause = _extract_root_cause(e)
            await self._cleanup_connection()
            log.error("mcp.client.stdio_failed", {
                "server": self.name,
                "error": root_cause,
                "stderr": stderr_output,
            })
            detail = f"Stdio connection failed: {self.name}: {root_cause}"
            if stderr_output:
                detail += f"\nServer stderr:\n{stderr_output}"
            raise RuntimeError(detail)
        finally:
            stderr_file.close()
    
    async def disconnect(self) -> None:
        """Disconnect from server"""
        if not self._connected:
            return
        
        try:
            # Close session first
            if self.session:
                try:
                    await self.session.__aexit__(None, None, None)
                except Exception as e:
                    log.warn("mcp.client.session_close_error", {
                        "server": self.name,
                        "error": str(e)
                    })
            
            # Then close streams
            if self._streams_context:
                try:
                    await self._streams_context.__aexit__(None, None, None)
                except Exception as e:
                    log.warn("mcp.client.streams_close_error", {
                        "server": self.name,
                        "error": str(e)
                    })
        except Exception as e:
            log.error("mcp.client.disconnect_error", {
                "server": self.name,
                "error": str(e)
            })
        finally:
            self._connected = False
            self.session = None
            self._streams = None
            self._streams_context = None
            log.info("mcp.client.disconnected", {"server": self.name})
    
    async def list_tools(self) -> List[McpToolDef]:
        """
        List available tools
        
        Returns:
            List of tool definitions
            
        Raises:
            RuntimeError: If not connected
        """
        if not self._connected or not self.session:
            raise RuntimeError(f"Client not connected: {self.name}")
        
        try:
            result = await asyncio.wait_for(
                self.session.list_tools(),
                timeout=self.timeout
            )
            tools = [McpToolDef.from_sdk(tool) for tool in result.tools]
            log.debug("mcp.client.tools_listed", {
                "server": self.name,
                "count": len(tools)
            })
            return tools
        except Exception as e:
            log.error("mcp.client.list_tools_error", {
                "server": self.name,
                "error": str(e)
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
        if not self._connected or not self.session:
            raise RuntimeError(f"Client not connected: {self.name}")
        
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name=name, arguments=arguments),
                timeout=self.timeout
            )
            log.debug("mcp.client.tool_called", {
                "server": self.name,
                "tool": name
            })
            return result
        except asyncio.TimeoutError:
            log.error("mcp.client.call_timeout", {
                "server": self.name,
                "tool": name
            })
            from concurrent.futures import TimeoutError as _FuturesTimeoutError
            raise _FuturesTimeoutError(f"MCP工具调用超时 ({self.timeout}s): {name}")
        except Exception as e:
            log.error("mcp.client.call_error", {
                "server": self.name,
                "tool": name,
                "error": str(e)
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
        if not self._connected or not self.session:
            raise RuntimeError(f"Client not connected: {self.name}")
        
        try:
            result = await asyncio.wait_for(
                self.session.list_resources(),
                timeout=self.timeout
            )
            resources = []
            for r in result.resources:
                resources.append(McpResource(
                    name=r.name,
                    uri=r.uri,
                    description=getattr(r, 'description', None),
                    mime_type=getattr(r, 'mimeType', None),
                    server=self.name
                ))
            log.debug("mcp.client.resources_listed", {
                "server": self.name,
                "count": len(resources)
            })
            return resources
        except Exception as e:
            log.error("mcp.client.list_resources_error", {
                "server": self.name,
                "error": str(e)
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
        if not self._connected or not self.session:
            raise RuntimeError(f"Client not connected: {self.name}")
        
        try:
            result = await asyncio.wait_for(
                self.session.read_resource(uri=uri),
                timeout=self.timeout
            )
            log.debug("mcp.client.resource_read", {
                "server": self.name,
                "uri": uri
            })
            return result
        except Exception as e:
            log.error("mcp.client.read_resource_error", {
                "server": self.name,
                "uri": uri,
                "error": str(e)
            })
            raise
    
    @property
    def is_connected(self) -> bool:
        """Whether connected to server"""
        return self._connected


__all__ = ['McpClient']
