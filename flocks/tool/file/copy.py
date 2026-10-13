import os
import shutil

from flocks.tool.path_utils import (
    DirectoryLinkLoop,
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
    name="copy",
    group="文件操作",
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
        target_resolution = await resolve_tool_path(ctx, targetPath)
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    target = target_resolution.resolved_path
    if not os.path.exists(source):
        return ToolResult(success=False, error=f"Source not found: {source}")
    def planned_patterns() -> list[str]:
        # copytree merges a directory into the target and copies what directory
        # links point at; copy2 drops a file inside an existing target directory.
        if os.path.isdir(source):
            landing_path, landing_pattern = target, target_resolution.permission_pattern
        else:
            landing_path, landing_pattern = landing_location(
                source, target, target_resolution.permission_pattern,
            )
        return tree_permission_patterns(
            landing_pattern,
            landing_path,
            tree_entries(source, follow_links=True),
            worktree=target_resolution.worktree,
        )

    try:
        unchanged = await ask_edit_permission(
            ctx, "copy", planned_patterns, {"source": source, "filepath": target},
        )
    except DirectoryLinkLoop as exc:
        return ToolResult(success=False, error=f"Not copying {source}: {exc}")
    if not unchanged:
        return ToolResult(
            success=False,
            error=f"{source} or {target} changed while waiting for permission; check them again and retry.",
        )
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if os.path.isdir(source):
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return ToolResult(success=True, output=f"Copied {source} -> {target}", metadata={"source": source, "target": target})
