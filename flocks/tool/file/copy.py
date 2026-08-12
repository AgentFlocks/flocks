import os
import shutil

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
    name="copy",
    description="Copy a file or directory.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(name="sourcePath", type=ParameterType.STRING, description="Source path", required=True),
        ToolParameter(name="targetPath", type=ParameterType.STRING, description="Target path", required=True),
    ],
)
async def copy_tool(ctx: ToolContext, sourcePath: str, targetPath: str) -> ToolResult:
    try:
        source = (await resolve_tool_path(ctx, sourcePath)).resolved_path
        target = (await resolve_tool_path(ctx, targetPath)).resolved_path
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    if not os.path.exists(source):
        return ToolResult(success=False, error=f"Source not found: {source}")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if os.path.isdir(source):
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return ToolResult(success=True, output=f"Copied {source} -> {target}", metadata={"source": source, "target": target})
