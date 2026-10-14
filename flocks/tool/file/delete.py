import os

from flocks.tool import nofollow
from flocks.tool.path_utils import (
    ask_edit_permission,
    checked_error,
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
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if isinstance(sandbox, dict) and sandbox.get("workspace_access") == "ro":
        # same gate as write/edit/apply_patch: a read-only workspace must not be mutated
        return ToolResult(
            success=False,
            error=(
                "Delete is blocked in sandbox read-only workspace mode. "
                "Set sandbox.workspace_access to 'rw' to allow deletes."
            ),
            title=resolution.display_path,
        )
    if not os.path.exists(source):
        return ToolResult(success=False, error=f"Path not found: {source}", title=resolution.display_path)
    # The entries of the latest listing: what the approval covers.
    listed: dict[str, list[str]] = {}

    def planned_patterns() -> list[str]:
        # Links inside the tree are removed as links, so nothing is reached through them.
        listed["entries"] = tree_entries(source)
        return tree_permission_patterns(resolution.permission_pattern, listed["entries"])

    changed = f"{source} changed while waiting for permission; check it again and retry."
    if not await ask_edit_permission(
        ctx, "delete", planned_patterns,
        {"filepath": source, "pattern_base": resolution.worktree},
        planned_paths=lambda: (source,), resolved_paths=(source,),
    ):
        return ToolResult(success=False, error=changed, title=resolution.display_path)
    try:
        # Reached without following a link: one put on the way meanwhile
        # stops the delete instead of taking it to someone else's files.
        # Only what the approval listed is removed; anything put in the tree
        # since stays, and the delete reports the change.
        nofollow.remove(source, entries=listed.get("entries"))
    except (nofollow.LinkOnTheWay, nofollow.StaleFile):
        return ToolResult(success=False, error=changed, title=resolution.display_path)
    except OSError as exc:
        return ToolResult(
            success=False, error=checked_error((source,), f"Delete failed: {exc}"), title=resolution.display_path,
        )
    return ToolResult(
        success=True,
        output=f"Deleted permanently: {source}",
        title=resolution.display_path,
        metadata={"path": source},
    )
