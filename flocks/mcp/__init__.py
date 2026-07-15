"""
MCP (Model Context Protocol) Integration Module

Provides MCP server management, tool registration, resource access, and more
"""

from typing import Dict, Optional, Any
from flocks.mcp.types import (
    McpStatus,
    McpStatusInfo,
    McpToolDef,
    McpResource,
    McpServerInfo,
    McpToolSource,
    ServerConfig,
)
from flocks.mcp.client import McpClient
from flocks.mcp.server import McpServerManager
from flocks.mcp.adapter import McpToolAdapter
from flocks.mcp.registry import McpToolRegistry
from flocks.mcp.catalog import McpCatalog, CatalogEntry, CategoryInfo
from flocks.mcp.installer import preflight_install
from flocks.mcp.utils import (
    build_mcp_url,
    resolve_env_var,
    sanitize_name,
    generate_tool_name,
)
from flocks.utils.log import Log

log = Log.create(service="mcp")

# Global manager instance (singleton)
_manager_instance: Optional[McpServerManager] = None


def get_manager() -> McpServerManager:
    """
    Get global MCP server manager
    
    Returns:
        McpServerManager instance (singleton)
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = McpServerManager()
    return _manager_instance


class MCP:
    """
    MCP Namespace - Provides unified MCP operation interface
    
    Entry point for all MCP-related operations
    """
    
    @classmethod
    async def init(cls) -> None:
        """
        Initialize MCP subsystem
        
        Load configuration and start all MCP servers
        Called in Instance.bootstrap()
        """
        manager = get_manager()
        await manager.init()
        log.info("mcp.system_initialized")
    
    @classmethod
    async def shutdown(cls) -> None:
        """
        Shutdown MCP subsystem
        
        Disconnect all connections and clean up resources
        """
        manager = get_manager()
        await manager.shutdown()
        log.info("mcp.system_shutdown")
    
    @classmethod
    async def status(cls) -> Dict[str, McpStatusInfo]:
        """
        Get status of all servers
        
        Returns:
            Dictionary mapping server name to status info
        """
        manager = get_manager()
        return await manager.status()
    
    @classmethod
    async def get_server_info(cls, name: str) -> Optional[McpServerInfo]:
        """
        Get detailed server information
        
        Args:
            name: Server name
            
        Returns:
            Server information, or None if not found
        """
        manager = get_manager()
        return await manager.get_server_info(name)
    
    @classmethod
    async def connect(cls, name: str, config: Dict[str, Any]) -> bool:
        """
        Connect to MCP server
        
        Args:
            name: Server name
            config: Server configuration
            
        Returns:
            True if connection successful
        """
        manager = get_manager()
        return await manager.connect(name, config)
    
    @classmethod
    async def disconnect(cls, name: str) -> bool:
        """
        Disconnect from MCP server (status entry kept as DISCONNECTED).

        Args:
            name: Server name

        Returns:
            True if disconnection successful
        """
        manager = get_manager()
        return await manager.disconnect(name)

    @classmethod
    async def remove(cls, name: str) -> bool:
        """
        Fully remove an MCP server from memory.

        Disconnects the client, unregisters all tools, and purges all
        in-memory state so the server no longer appears in any listing
        without requiring a restart.

        Args:
            name: Server name

        Returns:
            True always (best-effort cleanup)
        """
        manager = get_manager()
        return await manager.remove(name)
    
    @classmethod
    async def refresh_tools(cls, name: str) -> int:
        """
        Refresh server's tool list
        
        Args:
            name: Server name
            
        Returns:
            Number of updated tools
        """
        manager = get_manager()
        return await manager.refresh_tools(name)
    
    @classmethod
    def get_tool_source(cls, flocks_tool_name: str) -> Optional[McpToolSource]:
        """
        Get tool source information
        
        Args:
            flocks_tool_name: Flocks tool name
            
        Returns:
            Tool source information, or None if not an MCP tool
        """
        return McpToolRegistry.get_source(flocks_tool_name)
    
    @classmethod
    def get_server_tools(cls, server_name: str) -> list:
        """
        Get all tools from a server
        
        Args:
            server_name: Server name
            
        Returns:
            List of tool names
        """
        return McpToolRegistry.get_server_tools(server_name)
    
    @classmethod
    def is_mcp_tool(cls, flocks_tool_name: str) -> bool:
        """
        Check if it's an MCP tool
        
        Args:
            flocks_tool_name: Flocks tool name
            
        Returns:
            True if it's an MCP tool
        """
        return McpToolRegistry.is_mcp_tool(flocks_tool_name)
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """
        Get MCP statistics
        
        Returns:
            Statistics dictionary
        """
        return McpToolRegistry.get_stats()


__all__ = [
    # Main interface
    'MCP',
    'get_manager',
    
    # Core classes
    'McpClient',
    'McpServerManager',
    'McpToolAdapter',
    'McpToolRegistry',
    
    # Catalog
    'McpCatalog',
    'CatalogEntry',
    'CategoryInfo',

    # Installer
    'preflight_install',
    
    # Types
    'McpStatus',
    'McpStatusInfo',
    'McpToolDef',
    'McpResource',
    'McpServerInfo',
    'McpToolSource',
    'ServerConfig',
    
    # Utility functions
    'build_mcp_url',
    'resolve_env_var',
    'sanitize_name',
    'generate_tool_name',
]
