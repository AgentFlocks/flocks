import os
import shutil

from flocks.tool.path_utils import (
    ask_edit_permission,
    landing_location,
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
    name="move",
    group="文件操作",
    description="Move or rename a file or directory.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(name="sourcePath", type=ParameterType.STRING, description="Source path", required=True),
        ToolParameter(name="targetPath", type=ParameterType.STRING, description="Target path", required=True),
    ],
)
async def move_tool(ctx: ToolContext, sourcePath: str, targetPath: str) -> ToolResult:
    try:
        source_resolution = await resolve_tool_path(ctx, sourcePath)
        target_resolution = await resolve_tool_path(ctx, targetPath)
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    source = source_resolution.resolved_path
    target = target_resolution.resolved_path
    if not os.path.exists(source):
        return ToolResult(success=False, error=f"Source not found: {source}")
    def planned_patterns() -> list[str]:
        # A rename keeps links as links; the tree lands inside an existing
        # target directory, possibly through a link on the way there.
        entries = tree_entries(source)
        landing_path, landing_pattern = landing_location(
            source, target, target_resolution.permission_pattern,
        )
        return [
            *tree_permission_patterns(source_resolution.permission_pattern, source, entries),
            *tree_permission_patterns(
                landing_pattern, landing_path, entries, worktree=target_resolution.worktree,
            ),
        ]

    if not await ask_edit_permission(
        ctx, "move", planned_patterns, {"source": source, "filepath": target},
    ):
        return ToolResult(
            success=False,
            error=f"{source} or {target} changed while waiting for permission; check them again and retry.",
        )
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    shutil.move(source, target)
    return ToolResult(success=True, output=f"Moved {source} -> {target}", metadata={"source": source, "target": target})
