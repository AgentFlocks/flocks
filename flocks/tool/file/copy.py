import os
import shutil

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
        # copytree merges a directory into the target, links copied as links:
        # nothing a link inside the source points at is read, whoever it
        # belongs to. copy2 drops a file inside an existing target directory.
        if os.path.isdir(source):
            landing_path, landing_pattern = target, target_resolution.permission_pattern
        else:
            landing_path, landing_pattern = landing_location(
                source, target, target_resolution.permission_pattern,
            )
        listed["entries"] = tree_entries(source)
        return tree_permission_patterns(
            landing_pattern,
            landing_path,
            listed["entries"],
            worktree=target_resolution.worktree,
        )

    def planned_paths() -> list[str]:
        # copytree reads every entry of the source (a link as a link) and
        # overwrites what already exists under the target; copy2 reads one
        # file and writes one.
        if not os.path.isdir(source):
            landing = landing_location(source, target, target_resolution.permission_pattern)[0]
            return [source, target, landing]
        entries = listed.get("entries") or tree_entries(source)
        return [
            *(os.path.join(source, entry) if entry else source for entry in entries),
            *(os.path.join(target, entry) if entry else target for entry in entries),
        ]

    unchanged = await ask_edit_permission(
        ctx, "copy", planned_patterns,
        {"source": source, "filepath": target, "pattern_base": target_resolution.worktree},
        planned_paths=planned_paths, resolved_paths=(source, target),
    )
    if not unchanged:
        return ToolResult(
            success=False,
            error=f"{source} or {target} changed while waiting for permission; check them again and retry.",
        )
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        if os.path.isdir(source):
            # The listing the approval was checked against, taken again after it.
            blocked = _make_room_for_links(source, target, listed.get("entries") or [""])
            if blocked:
                return ToolResult(
                    success=False,
                    error=f"Not copying {source}: {blocked} is a directory where the source has a link.",
                )
            shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    except OSError as exc:
        return ToolResult(success=False, error=checked_error((source, target), f"Copy failed: {exc}"))
    return ToolResult(success=True, output=f"Copied {source} -> {target}", metadata={"source": source, "target": target})


def _make_room_for_links(source: str, target: str, entries: list[str]) -> str | None:
    """Let each link of the source replace what the target holds at its place.

    copytree creates a link and fails if anything is there, which a copy into
    an earlier copy always meets. A file or link there is replaced -- the
    approval covered writing that path -- a directory is not: its path is
    returned and nothing is changed.
    """
    replaced = []
    for entry in entries:
        landing = os.path.join(target, entry)
        if not entry or not os.path.islink(os.path.join(source, entry)) or not os.path.lexists(landing):
            continue
        if os.path.isdir(landing) and not os.path.islink(landing):
            return landing
        replaced.append(landing)
    for landing in replaced:
        os.unlink(landing)
    return None
