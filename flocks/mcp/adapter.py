"""
MCP Tool Adapter

Converts MCP Tools to Flocks Tools, implementing protocol adaptation
"""

from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Dict, Any

from flocks.mcp.types import McpToolDef
from flocks.mcp.client import McpClient
from flocks.mcp.utils import (
    calculate_schema_hash,
    check_name_conflict,
    generate_tool_name,
    resolve_conflict,
)
from flocks.tool.registry import (
    Tool,
    ToolInfo,
    ToolParameter,
    ToolCategory,
    ParameterType,
    ToolContext,
    ToolResult
)
from flocks.utils.log import Log

log = Log.create(service="mcp.adapter")


class McpToolAdapter:
    """
    MCP Tool Adapter
    
    Responsible for converting MCP Tools to Flocks Tools
    """
    
    @staticmethod
    def convert_tool(
        server_name: str,
        mcp_tool: McpToolDef,
        client: McpClient
    ) -> Tool:
        """
        Convert MCP Tool to Flocks Tool
        
        Conversion example (based on ThreatBook):
            MCP Tool:
                name: "ip_query"
                description: "Query IP threat intelligence"
                inputSchema: {"properties": {"ip": {"type": "string"}}}
            
            Flocks Tool:
                name: "threatbook_ip_query"
                description: "Query IP threat intelligence\n[Source: threatbook MCP]"
                parameters: [ToolParameter(name="ip", type=STRING)]
        
        Args:
            server_name: MCP server name
            mcp_tool: MCP tool definition
            client: MCP client (used for invocation)
            
        Returns:
            Flocks Tool object
        """
        # 1. Generate tool name (avoid conflicts)
        tool_name = generate_tool_name(server_name, mcp_tool.name)
        if check_name_conflict(tool_name):
            # Never overwrite an existing (built-in / plugin / other MCP) tool:
            # the registry's ``register`` replaces entries silently.
            resolved_name = resolve_conflict(tool_name)
            log.warn("mcp.tool.name_conflict", {
                "server": server_name,
                "tool": mcp_tool.name,
                "requested": tool_name,
                "resolved": resolved_name,
            })
            tool_name = resolved_name
        
        # 2. Convert parameters
        parameters = McpToolAdapter._convert_parameters(mcp_tool.input_schema)
        
        # 3. Enhance description
        description = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
        description += f"\n\n[Source: {server_name} MCP Server]"
        
        # 4. Create ToolInfo
        tool_info = ToolInfo(
            name=tool_name,
            description=description,
            category=ToolCategory.CUSTOM,
            parameters=parameters,
            enabled=True
        )
        
        # 5. Create handler (closure captures client and mcp_tool)
        async def handler(ctx: ToolContext, **kwargs) -> ToolResult:
            """
            Tool execution handler
            
            Forwards calls to MCP client
            """
            try:
                log.debug("mcp.tool.calling", {
                    "server": server_name,
                    "tool": mcp_tool.name,
                    "args": list(kwargs.keys())
                })
                
                # Call MCP tool
                result = await client.call_tool(mcp_tool.name, kwargs)
                
                # Check if it's an error
                if hasattr(result, 'isError') and result.isError:
                    error_msg = str(result.content) if hasattr(result, 'content') else "Unknown error"
                    log.error("mcp.tool.error", {
                        "server": server_name,
                        "tool": mcp_tool.name,
                        "error": error_msg
                    })
                    return ToolResult(
                        success=False,
                        error=error_msg
                    )
                
                # Extract output content
                if hasattr(result, 'content'):
                    # MCP SDK result has content field
                    content = result.content
                    if isinstance(content, list) and len(content) > 0:
                        # content is a list of blocks; join every text block so
                        # multi-part results are not truncated to the first item.
                        text_parts = [
                            item.text for item in content
                            if hasattr(item, 'text') and isinstance(item.text, str)
                        ]
                        if len(text_parts) == len(content):
                            output = "\n".join(text_parts)
                        elif len(content) == 1:
                            output = content[0]
                        else:
                            output = content
                    else:
                        output = content
                else:
                    output = result
                
                log.debug("mcp.tool.success", {
                    "server": server_name,
                    "tool": mcp_tool.name
                })
                
                return ToolResult(
                    success=True,
                    output=output,
                    metadata={
                        "mcp_server": server_name,
                        "mcp_tool": mcp_tool.name,
                        "flocks_tool": tool_name
                    }
                )
                
            except Exception as e:
                if isinstance(e, FuturesTimeoutError):
                    raise
                log.error("mcp.tool.exception", {
                    "server": server_name,
                    "tool": mcp_tool.name,
                    "error": str(e)
                })
                return ToolResult(
                    success=False,
                    error=f"MCP tool execution failed: {str(e)}"
                )
        
        # 6. Create Tool
        tool = Tool(info=tool_info, handler=handler)
        
        return tool
    
    @staticmethod
    def _convert_parameters(input_schema: Dict[str, Any]):
        """
        Convert JSON Schema parameters to ToolParameter
        
        Supported type mappings:
            - string -> STRING
            - integer -> INTEGER
            - number -> NUMBER
            - boolean -> BOOLEAN
            - array -> ARRAY
            - object -> OBJECT
        
        Args:
            input_schema: Input definition in JSON Schema format
            
        Returns:
            List of ToolParameter
        """
        parameters = []
        
        if not input_schema:
            return parameters
        
        properties = input_schema.get('properties', {})
        required = input_schema.get('required', [])
        
        for param_name, param_schema in properties.items():
            # Type mapping
            json_type = param_schema.get('type', 'string')
            param_type = {
                'string': ParameterType.STRING,
                'integer': ParameterType.INTEGER,
                'number': ParameterType.NUMBER,
                'boolean': ParameterType.BOOLEAN,
                'array': ParameterType.ARRAY,
                'object': ParameterType.OBJECT
            }.get(json_type, ParameterType.STRING)
            
            # Create parameter
            parameters.append(ToolParameter(
                name=param_name,
                type=param_type,
                description=param_schema.get('description', ''),
                required=param_name in required,
                default=param_schema.get('default'),
                enum=param_schema.get('enum')
            ))
        
        return parameters
    
    @staticmethod
    def get_schema_hash(mcp_tool: McpToolDef) -> str:
        """
        Calculate tool schema hash value
        
        Used to detect changes in tool definition
        
        Args:
            mcp_tool: MCP tool definition
            
        Returns:
            SHA256 hash value
        """
        return calculate_schema_hash(mcp_tool.input_schema)


__all__ = ['McpToolAdapter']
