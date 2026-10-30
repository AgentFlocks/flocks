import os

from flocks.tool import nofollow
from flocks.tool.path_utils import (
    ask_edit_permission,
    checked_error,
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
    # The entries of the latest listing: what the approval covers.
    listed: dict[str, list[str]] = {}

    def planned_patterns() -> list[str]:
        # A rename keeps links as links; the tree lands inside an existing
        # target directory.
        listed["entries"] = tree_entries(source)
        landing_pattern = landing_location(source, target, target_resolution.permission_pattern)[1]
        return [
            *tree_permission_patterns(source_resolution.permission_pattern, listed["entries"]),
            *tree_permission_patterns(landing_pattern, listed["entries"]),
        ]

    def planned_paths() -> list[str]:
        # A rename keeps links as links and does not open what is inside.
        landing = landing_location(source, target, target_resolution.permission_pattern)[0]
        return [source, target, landing]

    changed = f"{source} or {target} changed while waiting for permission; check them again and retry."
    if not await ask_edit_permission(
        ctx, "move", planned_patterns,
        {"source": source, "filepath": target, "pattern_base": source_resolution.worktree},
        planned_paths=planned_paths, resolved_paths=(source, target),
    ):
        return ToolResult(success=False, error=changed)
    try:
        # Both sides are reached without following a link. A tree that
        # carried anything the approval did not list goes back; across file
        # systems it is copied links as links, then removed as copied.
        nofollow.move(source, target, entries=listed.get("entries"))
    except (nofollow.LinkOnTheWay, nofollow.StaleFile):
        return ToolResult(success=False, error=changed)
    except OSError as exc:
        return ToolResult(success=False, error=checked_error((source, target), f"Move failed: {exc}"))
    return ToolResult(success=True, output=f"Moved {source} -> {target}", metadata={"source": source, "target": target})
