"""
Batch Tool - Parallel tool execution

Executes multiple tool calls in parallel for optimal performance.
Limited to 25 concurrent calls.
"""

import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.utils.log import Log


log = Log.create(service="tool.batch")


# Tools that cannot be batched
DISALLOWED_TOOLS = {"batch"}

# Maximum parallel calls
MAX_BATCH_SIZE = 25


DESCRIPTION = """Execute multiple tool calls in parallel for optimal performance.

Use this tool when:
- You need to run multiple independent operations
- Operations don't depend on each other's results
- You want to maximize throughput

Limitations:
- Maximum 25 tool calls per batch
- Cannot batch the 'batch' tool itself
- External tools (MCP) cannot be batched

Format:
- tool_calls: Array of {tool: "tool_name", parameters: {...}}
- commands: Legacy alias for tool_calls using {tool: "tool_name", args: {...}}"""


@ToolRegistry.register_function(
    name="batch",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="tool_calls",
            type=ParameterType.ARRAY,
            description="Array of tool calls to execute in parallel",
            required=False
        ),
        ToolParameter(
            name="commands",
            type=ParameterType.ARRAY,
            description="Legacy alias for tool_calls; each item may use args instead of parameters",
            required=False
        ),
    ]
)
async def batch_tool(
    ctx: ToolContext,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    commands: Optional[List[Dict[str, Any]]] = None,
) -> ToolResult:
    """
    Execute multiple tools in parallel
    
    Args:
        ctx: Tool context
        tool_calls: List of {tool: str, parameters: dict}
        
    Returns:
        ToolResult with combined results
    """
    normalized_calls = tool_calls or commands or []
    if commands and not tool_calls:
        normalized_calls = [{**call, "parameters": call.get("parameters", call.get("args", {}))} for call in commands]
    elif tool_calls:
        normalized_calls = [{**call, "parameters": call.get("parameters", call.get("args", {}))} for call in tool_calls]

    if not normalized_calls:
        return ToolResult(
            success=False,
            error="At least one tool call is required"
        )
    
    # Limit to MAX_BATCH_SIZE
    limited_calls = normalized_calls[:MAX_BATCH_SIZE]
    discarded_calls = normalized_calls[MAX_BATCH_SIZE:]
    
    async def execute_call(call: Dict[str, Any], index: int) -> Dict[str, Any]:
        """Execute a single tool call"""
        start_time = datetime.now()
        tool_name = call.get("tool", "")
        parameters = call.get("parameters", {})
        
        try:
            # Check if tool is disallowed
            if tool_name in DISALLOWED_TOOLS:
                raise ValueError(
                    f"Tool '{tool_name}' is not allowed in batch. "
                    f"Disallowed tools: {', '.join(DISALLOWED_TOOLS)}"
                )
            
            # Get tool
            tool = ToolRegistry.get(tool_name)
            if not tool:
                raise ValueError(
                    f"Tool '{tool_name}' not found in registry. "
                    f"External tools (MCP, environment) cannot be batched."
                )
            
            # Execute tool
            result = await ToolRegistry.execute(tool_name=tool_name, ctx=ctx, **parameters)
            
            return {
                "index": index,
                "tool": tool_name,
                "success": result.success,
                "result": result,
                "time": {
                    "start": start_time.isoformat(),
                    "end": datetime.now().isoformat()
                }
            }
            
        except Exception as e:
            return {
                "index": index,
                "tool": tool_name,
                "success": False,
                "error": str(e),
                "time": {
                    "start": start_time.isoformat(),
                    "end": datetime.now().isoformat()
                }
            }
    
    # Execute all calls in parallel
    tasks = [execute_call(call, i) for i, call in enumerate(limited_calls)]
    results = await asyncio.gather(*tasks)
    
    # Add discarded calls as errors
    for i, call in enumerate(discarded_calls):
        results.append({
            "index": MAX_BATCH_SIZE + i,
            "tool": call.get("tool", "unknown"),
            "success": False,
            "error": "Maximum of 25 tools allowed in batch"
        })
    
    # Count results
    successful_calls = sum(1 for r in results if r.get("success"))
    failed_calls = len(results) - successful_calls
    
    # Build output message
    if failed_calls > 0:
        output_message = f"Executed {successful_calls}/{len(results)} tools successfully. {failed_calls} failed."
    else:
        output_message = f"All {successful_calls} tools executed successfully.\n\nKeep using the batch tool for optimal performance!"
    
    # Collect attachments from successful results
    attachments = []
    for r in results:
        if r.get("success") and r.get("result"):
            result_attachments = r["result"].attachments
            if result_attachments:
                attachments.extend(result_attachments)
    
    return ToolResult(
        success=failed_calls == 0,
        output=output_message,
        title=f"Batch execution ({successful_calls}/{len(results)} successful)",
        attachments=attachments if attachments else None,
        metadata={
            "totalCalls": len(results),
            "successful": successful_calls,
            "failed": failed_calls,
            "tools": [call.get("tool", "") for call in normalized_calls],
            "details": [{"tool": r["tool"], "success": r["success"]} for r in results]
        }
    )
