"""
MCP Type Definitions

Defines all MCP-related types and data models
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
import time


def normalize_mcp_group(value: Optional[str]) -> Optional[str]:
    """Normalize native server grouping metadata."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PydanticCustomError("group_type", "group must be a string or null")
    value = value.strip()
    if len(value) > 32:
        raise PydanticCustomError("group_length", "group must be at most 32 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PydanticCustomError("group_control", "group cannot contain control characters")
    return value


class McpStatus(str, Enum):
    """MCP server connection status"""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    NEEDS_AUTH = "needs_auth"
    DISABLED = "disabled"


class McpStatusInfo(BaseModel):
    """MCP status information"""
    status: McpStatus
    group: Optional[str] = None
    group_readonly: bool = False
    error: Optional[str] = None
    connected_at: Optional[float] = None
    tools_count: int = 0
    resources_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class McpToolDef(BaseModel):
    """MCP tool definition"""
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    
    @classmethod
    def from_sdk(cls, sdk_tool):
        """Convert from MCP SDK Tool object"""
        return cls(
            name=sdk_tool.name,
            description=sdk_tool.description if hasattr(sdk_tool, 'description') else None,
            input_schema=sdk_tool.inputSchema if hasattr(sdk_tool, 'inputSchema') else {}
        )


class McpResource(BaseModel):
    """MCP resource definition"""
    name: str
    uri: str
    description: Optional[str] = None
    mime_type: Optional[str] = None
    server: str  # Owning server name


class McpServerInfo(BaseModel):
    """MCP server information"""
    name: str
    status: McpStatusInfo
    tools: List[McpToolDef] = Field(default_factory=list)
    resources: List[McpResource] = Field(default_factory=list)
    server_version: Optional[str] = None
    protocol_version: Optional[str] = None


class McpToolSource(BaseModel):
    """MCP tool source information - for tracking"""
    mcp_server: str
    mcp_tool: str
    flocks_tool: str
    registered_at: float = Field(default_factory=time.time)
    schema_hash: Optional[str] = None


class AuthConfig(BaseModel):
    """Authentication configuration"""
    type: str  # apikey, oauth, basic, none
    location: str = "header"  # header, query, body
    param_name: str = "Authorization"
    value: str  # Auth value or environment variable placeholder


class RetryConfig(BaseModel):
    """Retry configuration"""
    max_attempts: int = 3
    backoff_factor: float = 2.0


class ServerConfig(BaseModel):
    """MCP server configuration"""
    type: str  # remote | local
    url: Optional[str] = None  # required for remote
    command: Optional[List[str]] = None  # required for local
    args: Optional[List[str]] = None  # optional for local
    env: Optional[Dict[str, str]] = None  # optional for local
    cwd: Optional[str] = None  # optional for local
    enabled: bool = True
    group: Optional[str] = None
    _validate_group = field_validator("group", mode="before")(normalize_mcp_group)

    timeout: float = 30.0
    auth: Optional[Dict[str, Any]] = None
    retry: Optional[RetryConfig] = None
    auto_refresh: bool = False
    refresh_interval: int = 86400  # 24 hours
    metadata: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    'McpStatus',
    'McpStatusInfo',
    'McpToolDef',
    'McpResource',
    'McpServerInfo',
    'McpToolSource',
    'AuthConfig',
    'RetryConfig',
    'ServerConfig',
]
