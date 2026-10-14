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
    # The entries of the latest listing, so the paths to watch come from the
    # same walk as the patterns (one walk before the wait, one after).
    listed: dict[str, list[str]] = {}

    def planned_patterns() -> list[str]:
        # A directory is merged into the target, links copied as links:
        # nothing a link inside the source points at is read, whoever it
        # belongs to, and nothing is written through a link in the target.
        # A file lands inside an existing target directory.
        if os.path.isdir(source):
            landing_pattern = target_resolution.permission_pattern
        else:
            landing_pattern = landing_location(source, target, target_resolution.permission_pattern)[1]
        listed["entries"] = tree_entries(source)
        return tree_permission_patterns(landing_pattern, listed["entries"])

    def planned_paths() -> list[str]:
        # A directory copy reads every entry of the source (a link as a link)
        # and overwrites what already exists under the target; a file copy
        # reads one file and writes one.
        if not os.path.isdir(source):
            landing = landing_location(source, target, target_resolution.permission_pattern)[0]
            return [source, target, landing]
        entries = listed.get("entries") or tree_entries(source)
        return [
            *(os.path.join(source, entry) if entry else source for entry in entries),
            *(os.path.join(target, entry) if entry else target for entry in entries),
        ]

    changed = f"{source} or {target} changed while waiting for permission; check them again and retry."
    unchanged = await ask_edit_permission(
        ctx, "copy", planned_patterns,
        {"source": source, "filepath": target, "pattern_base": target_resolution.worktree},
        planned_paths=planned_paths, resolved_paths=(source, target),
    )
    if not unchanged:
        return ToolResult(success=False, error=changed)
    try:
        # Every directory on the way to both paths, and in both trees, is
        # opened without following a link: one put there meanwhile, even
        # while the copy runs, stops it instead of leading elsewhere.
        # Only what the approval listed is copied: an entry put in the
        # source since stays behind.
        nofollow.copy(source, target, entries=listed.get("entries"))
    except (nofollow.LinkOnTheWay, nofollow.StaleFile):
        return ToolResult(success=False, error=changed)
    except nofollow.Blocked as exc:
        return ToolResult(success=False, error=f"Not copying {source}: {exc}.")
    except OSError as exc:
        return ToolResult(success=False, error=checked_error((source, target), f"Copy failed: {exc}"))
    return ToolResult(success=True, output=f"Copied {source} -> {target}", metadata={"source": source, "target": target})
