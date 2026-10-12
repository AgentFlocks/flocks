import os
import shutil

from flocks.tool.path_utils import (
    ask_edit_permission,
    resolve_tool_path,
    tree_entries,
    tree_permission_patterns,
)
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


@ToolRegistry.register_function(
    name="delete",
    group="文件操作",
    description="Delete file or directory permanently.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="Path to delete", required=True),
    ],
)
async def delete_tool(ctx: ToolContext, path: str) -> ToolResult:
    try:
        resolution = await resolve_tool_path(ctx, path)
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), title=path)
    source = resolution.resolved_path
    if not os.path.exists(source):
        return ToolResult(success=False, error=f"Path not found: {source}", title=resolution.display_path)
    def planned_patterns() -> list[str]:
        # rmtree removes links as links, so nothing is reached through them.
        return tree_permission_patterns(resolution.permission_pattern, source, tree_entries(source))

    if not await ask_edit_permission(ctx, "delete", planned_patterns, {"filepath": source}):
        return ToolResult(
            success=False,
            error=f"{source} changed while waiting for permission; check it again and retry.",
            title=resolution.display_path,
        )
    if os.path.isdir(source):
        shutil.rmtree(source)
    else:
        os.remove(source)
    return ToolResult(
        success=True,
        output=f"Deleted permanently: {source}",
        title=resolution.display_path,
        metadata={"path": source},
    )
