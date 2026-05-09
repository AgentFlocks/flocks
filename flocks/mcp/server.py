"""
MCP Server Manager

Manages MCP server lifecycle: connection, disconnection, status tracking, tool registration, etc.
"""

import asyncio
import time
from typing import Dict, Optional, Any, List
from flocks.mcp.client import McpClient
from flocks.mcp.types import (
    McpStatus, 
    McpStatusInfo, 
    McpServerInfo,
    McpToolDef,
    McpResource,
    ServerConfig
)
from flocks.mcp.adapter import McpToolAdapter
from flocks.mcp.registry import McpToolRegistry
from flocks.config.config import Config
from flocks.utils.log import Log

log = Log.create(service="mcp.server")

# Retry configuration for failed connections at startup
_RETRY_DELAYS = [5, 15, 30, 60, 120]  # seconds between retries


class McpServerManager:
    """
    MCP Server Manager
    
    Responsibilities:
    - Manage connections to multiple MCP servers
    - Discover and register tools
    - Track server status
    - Handle reconnection and error recovery
    
    Credentials are resolved at config load time via {secret:xxx} references
    in ~/.flocks/config/flocks.json. The resolved config is passed directly here.
    """
    
    def __init__(self):
        """Initialize server manager"""
        self._clients: Dict[str, McpClient] = {}
        self._status: Dict[str, McpStatusInfo] = {}
        self._tools_cache: Dict[str, List[McpToolDef]] = {}
        self._resources_cache: Dict[str, List[McpResource]] = {}
        self._configs: Dict[str, Dict[str, Any]] = {}  # saved for retry
        self._lock = asyncio.Lock()
        self._initialized = False
        self._retry_task: Optional[asyncio.Task] = None
    
    async def init(self) -> None:
        """
        Initialize all configured MCP servers
        
        Load server configurations from config file and start all enabled servers in parallel.
        Servers that fail to connect will be retried in the background with exponential backoff.
        """
        if self._initialized:
            log.warn("mcp.already_initialized")
            return
        
        log.info("mcp.initializing")
        
        # Load configuration
        config = await Config.get()
        mcp_config = getattr(config, 'mcp', None)
        
        if not mcp_config or not isinstance(mcp_config, dict):
            log.info("mcp.no_config", {"message": "No MCP servers configured"})
            self._initialized = True
            return
        
        # Filter enabled servers — config values may be Pydantic models (McpLocalConfig /
        # McpRemoteConfig) or plain dicts depending on how Pydantic validated the Union type.
        # Normalize everything to plain dicts so _connect_and_register can use dict access.
        enabled_servers: Dict[str, Dict[str, Any]] = {}
        for name, server_config in mcp_config.items():
            if hasattr(server_config, 'model_dump'):
                cfg: Dict[str, Any] = server_config.model_dump(exclude_none=True)
            elif isinstance(server_config, dict):
                cfg = server_config
            else:
                continue
            if cfg.get('enabled', True):
                enabled_servers[name] = cfg
        
        if not enabled_servers:
            log.info("mcp.no_enabled_servers")
            self._initialized = True
            return
        
        # Save configs for retry
        self._configs.update(enabled_servers)
        
        log.info("mcp.starting_servers", {
            "total": len(enabled_servers),
            "servers": list(enabled_servers.keys())
        })
        
        # Start all servers in parallel (allow partial failures)
        tasks = [
            self._connect_and_register(name, server_config)
            for name, server_config in enabled_servers.items()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count results
        succeeded = sum(1 for r in results if not isinstance(r, Exception))
        failed = len(results) - succeeded
        
        self._initialized = True
        
        log.info("mcp.initialized", {
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed
        })
        
        # Start background retry task for any failed connections
        if failed > 0:
            self._retry_task = asyncio.create_task(self._retry_failed_servers())
    
    async def _retry_failed_servers(self) -> None:
        """
        Background task: retry servers that failed to connect at startup.
        
        Uses a fixed retry schedule (_RETRY_DELAYS). Stops retrying a server
        once it connects successfully or is manually removed/disabled.
        """
        for delay in _RETRY_DELAYS:
            await asyncio.sleep(delay)
            
            failed_servers = {
                name: cfg
                for name, cfg in self._configs.items()
                if self._status.get(name, McpStatusInfo(status=McpStatus.FAILED)).status == McpStatus.FAILED
                and name not in self._clients
            }
            
            if not failed_servers:
                log.info("mcp.retry.all_connected")
                return
            
            log.info("mcp.retry.attempt", {
                "servers": list(failed_servers.keys()),
                "next_delay": delay
            })
            
            tasks = [
                self._connect_and_register(name, cfg)
                for name, cfg in failed_servers.items()
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Final check after last retry
        still_failed = [
            name for name in self._configs
            if self._status.get(name, McpStatusInfo(status=McpStatus.FAILED)).status == McpStatus.FAILED
            and name not in self._clients
        ]
        if still_failed:
            log.warn("mcp.retry.exhausted", {"servers": still_failed})

    async def _connect_and_register(
        self, 
        name: str, 
        config: Dict[str, Any]
    ) -> None:
        """
        Connect to server and register tools
        
        Complete workflow:
        1. Create client
        2. Connect to server
        3. Discover tools and resources
        4. Register tools to ToolRegistry
        5. Update status
        
        Args:
            name: Server name
            config: Server configuration
        """
        try:
            log.info("mcp.connecting", {"server": name})
            
            # Credentials are already resolved via {secret:xxx} in config loading.
            # No separate injection needed.
            
            # 1. Create client
            client = McpClient(
                name=name,
                server_type=config['type'],
                url=config.get('url'),
                command=config.get('command'),
                headers=config.get('headers'),
                env=config.get('environment'),
                auth_config=config.get('auth'),
                transport=config.get('transport', 'auto'),
                timeout=config.get('timeout', 30.0)
            )
            
            # 2. Connect
            await client.connect()
            self._clients[name] = client
            
            # 3. Discover tools
            tools = await client.list_tools()
            self._tools_cache[name] = tools
            
            # 4. Discover resources (optional)
            try:
                resources = await client.list_resources()
                self._resources_cache[name] = resources
            except Exception as e:
                log.warn("mcp.resources_unavailable", {
                    "server": name,
                    "error": str(e)
                })
                self._resources_cache[name] = []
            
            # 5. Register tools
            registered_count = await self._register_tools(name, tools, client)
            
            # 6. Update status
            self._status[name] = McpStatusInfo(
                status=McpStatus.CONNECTED,
                connected_at=time.time(),
                tools_count=len(tools),
                resources_count=len(self._resources_cache.get(name, []))
            )
            
            log.info("mcp.server_ready", {
                "server": name,
                "tools": len(tools),
                "resources": len(self._resources_cache.get(name, [])),
                "registered": registered_count
            })
            
        except Exception as e:
            log.error("mcp.connect_failed", {
                "server": name,
                "error": str(e)
            })
            self._status[name] = McpStatusInfo(
                status=McpStatus.FAILED,
                error=str(e)
            )
            raise  # Propagate exception for gather to capture
    
    async def _register_tools(
        self, 
        server_name: str, 
        tools: List[McpToolDef],
        client: McpClient
    ) -> int:
        """
        Register tools to ToolRegistry
        
        Args:
            server_name: Server name
            tools: List of tools
            client: MCP client
            
        Returns:
            Number of successfully registered tools
        """
        registered = 0
        
        for mcp_tool in tools:
            try:
                # Convert to Flocks Tool
                flocks_tool = McpToolAdapter.convert_tool(
                    server_name, 
                    mcp_tool, 
                    client
                )
                
                # Register to Flocks ToolRegistry
                from flocks.tool import ToolRegistry
                ToolRegistry.register(flocks_tool)
                
                # Track metadata
                schema_hash = McpToolAdapter.get_schema_hash(mcp_tool)
                McpToolRegistry.track(
                    server_name=server_name,
                    mcp_tool_name=mcp_tool.name,
                    flocks_tool_name=flocks_tool.info.name,
                    schema_hash=schema_hash
                )
                
                registered += 1
                
                log.debug("mcp.tool_registered", {
                    "server": server_name,
                    "mcp_tool": mcp_tool.name,
                    "flocks_tool": flocks_tool.info.name
                })
                
            except Exception as e:
                log.error("mcp.tool_register_failed", {
                    "server": server_name,
                    "tool": mcp_tool.name,
                    "error": str(e)
                })
        
        return registered
    
    async def status(self) -> Dict[str, McpStatusInfo]:
        """
        Get status of all servers
        
        Returns:
            Dictionary mapping server name to status info
        """
        if not self._initialized:
            await self.init()
        
        return self._status.copy()
    
    async def get_server_info(self, name: str) -> Optional[McpServerInfo]:
        """
        Get detailed server information
        
        Args:
            name: Server name
            
        Returns:
            Server information, or None if not found
        """
        status = self._status.get(name)
        if not status:
            return None
        
        return McpServerInfo(
            name=name,
            status=status,
            tools=self._tools_cache.get(name, []),
            resources=self._resources_cache.get(name, [])
        )
    
    async def connect(self, name: str, config: Dict[str, Any]) -> bool:
        """
        Connect to specified server
        
        Args:
            name: Server name
            config: Server configuration
            
        Returns:
            True if connection successful
        """
        async with self._lock:
            try:
                self._configs[name] = config  # save for potential retry
                await self._connect_and_register(name, config)
                return True
            except Exception as e:
                log.error("mcp.connect_error", {
                    "server": name,
                    "error": str(e)
                })
                return False
    
    async def disconnect(self, name: str) -> bool:
        """
        Disconnect from server (keeps status entry as DISCONNECTED).

        Args:
            name: Server name

        Returns:
            True if disconnection successful
        """
        async with self._lock:
            if name not in self._clients:
                return False

            try:
                # Unregister tools
                tool_names = McpToolRegistry.untrack_server(name)
                from flocks.tool import ToolRegistry
                for tool_name in tool_names:
                    ToolRegistry.unregister(tool_name)

                # Disconnect client
                client = self._clients.pop(name)
                await client.disconnect()

                # Clear cache
                self._tools_cache.pop(name, None)
                self._resources_cache.pop(name, None)

                # Update status
                self._status[name] = McpStatusInfo(status=McpStatus.DISCONNECTED)

                log.info("mcp.disconnected", {"server": name})
                return True

            except Exception as e:
                log.error("mcp.disconnect_error", {
                    "server": name,
                    "error": str(e)
                })
                return False

    async def remove(self, name: str) -> bool:
        """
        Fully remove a server from memory: disconnect, unregister tools,
        and purge all internal state so the server no longer appears in
        status/tool listings without a restart.

        Args:
            name: Server name

        Returns:
            True always (best-effort cleanup)
        """
        async with self._lock:
            try:
                # Disconnect client if connected
                if name in self._clients:
                    client = self._clients.pop(name)
                    try:
                        await client.disconnect()
                    except Exception as e:
                        log.debug("mcp.remove.disconnect_error", {"server": name, "error": str(e)})

                # Unregister all tools for this server (works even when not connected)
                tool_names = McpToolRegistry.untrack_server(name)
                from flocks.tool import ToolRegistry
                for tool_name in tool_names:
                    ToolRegistry.unregister(tool_name)

                # Purge ALL in-memory state so it never appears again
                self._status.pop(name, None)
                self._tools_cache.pop(name, None)
                self._resources_cache.pop(name, None)
                self._configs.pop(name, None)  # remove from retry candidates

                log.info("mcp.removed", {"server": name, "tools_removed": len(tool_names)})
                return True

            except Exception as e:
                log.error("mcp.remove_error", {"server": name, "error": str(e)})
                return False
    
    async def refresh_tools(self, name: str) -> int:
        """
        Refresh server's tool list
        
        Args:
            name: Server name
            
        Returns:
            Number of updated tools
        """
        if name not in self._clients:
            raise ValueError(f"Server not connected: {name}")
        
        async with self._lock:
            try:
                client = self._clients[name]
                
                # Fetch tool list again
                new_tools = await client.list_tools()
                old_tools = self._tools_cache.get(name, [])
                
                # Simple strategy: unregister all old tools, register all new tools
                # TODO: Implement incremental update (P2 feature)
                
                # Unregister old tools
                tool_names = McpToolRegistry.untrack_server(name)
                from flocks.tool import ToolRegistry
                for tool_name in tool_names:
                    ToolRegistry.unregister(tool_name)
                
                # Register new tools
                registered = await self._register_tools(name, new_tools, client)
                
                # Update cache
                self._tools_cache[name] = new_tools
                
                log.info("mcp.tools_refreshed", {
                    "server": name,
                    "old_count": len(old_tools),
                    "new_count": len(new_tools),
                    "registered": registered
                })
                
                return registered
                
            except Exception as e:
                log.error("mcp.refresh_error", {
                    "server": name,
                    "error": str(e)
                })
                raise
    
    async def shutdown(self) -> None:
        """Shutdown all connections"""
        log.info("mcp.shutting_down")
        
        # Cancel background retry task if running
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
        self._retry_task = None
        
        for name, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception as e:
                log.error("mcp.shutdown_error", {
                    "server": name,
                    "error": str(e)
                })
        
        self._clients.clear()
        self._status.clear()
        self._tools_cache.clear()
        self._resources_cache.clear()
        self._configs.clear()
        self._initialized = False
        
        log.info("mcp.shutdown_complete")


__all__ = ['McpServerManager']
