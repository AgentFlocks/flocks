import os

from flocks.tool.path_utils import (
    changed_before_asking,
    moved_since_resolved,
    paths_changed,
    resolve_tool_path,
    resolved_identities,
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
    name="mkdir",
    group="文件操作",
    description="Create a directory recursively.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="Directory path", required=True),
    ],
)
async def mkdir_tool(ctx: ToolContext, path: str) -> ToolResult:
    try:
        resolution = await resolve_tool_path(ctx, path)
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    resolved = resolution.resolved_path
    if moved_since_resolved(resolved):
        return ToolResult(success=False, error=changed_before_asking(resolved))
    identities = resolved_identities(resolved)
    await ctx.ask(
        permission="edit",
        patterns=[resolution.permission_pattern],
        always=["*"],
        metadata={"tool": "mkdir", "filepath": resolved, "pattern_base": resolution.worktree},
    )
    if paths_changed(identities):
        return ToolResult(
            success=False,
            error=f"{resolved} changed while waiting for permission; check it again and retry.",
        )
    os.makedirs(resolved, exist_ok=True)
    return ToolResult(success=True, output=f"Created directory: {resolved}", metadata={"path": resolved})
