import os

from flocks.tool.path_utils import resolve_tool_path
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


@ToolRegistry.register_function(
    name="mkdir",
    description="Create a directory recursively.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="Directory path", required=True),
    ],
)
async def mkdir_tool(ctx: ToolContext, path: str) -> ToolResult:
    try:
        resolved = (await resolve_tool_path(ctx, path)).resolved_path
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    os.makedirs(resolved, exist_ok=True)
    return ToolResult(success=True, output=f"Created directory: {resolved}", metadata={"path": resolved})
