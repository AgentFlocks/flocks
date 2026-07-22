"""
Write Tool - File writing with diff generation

Writes files to the local filesystem with:
- Diff generation for existing files
- LSP diagnostics reporting
- Directory creation as needed
"""

import os
from difflib import unified_diff
from typing import Optional

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.tool.path_utils import resolve_tool_path
from flocks.utils.log import Log


log = Log.create(service="tool.write")


DESCRIPTION = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


def generate_diff(filepath: str, old_content: str, new_content: str) -> str:
    """
    Generate unified diff between old and new content
    
    Args:
        filepath: File path for diff header
        old_content: Original content
        new_content: New content
        
    Returns:
        Unified diff string
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    
    diff_lines = list(unified_diff(
        old_lines,
        new_lines,
        fromfile=filepath,
        tofile=filepath,
        lineterm=""
    ))
    
    return "".join(diff_lines)


def trim_diff(diff: str) -> str:
    """
    Trim indentation from diff content lines
    
    Ported from original trimDiff function for cleaner display.
    
    Args:
        diff: Original diff string
        
    Returns:
        Trimmed diff string
    """
    if not diff:
        return diff
    
    lines = diff.split("\n")
    
    # Find content lines (starting with +, -, or space, but not --- or +++)
    content_lines = [
        line for line in lines
        if (line.startswith("+") or line.startswith("-") or line.startswith(" "))
        and not line.startswith("---")
        and not line.startswith("+++")
    ]
    
    if not content_lines:
        return diff
    
    # Find minimum indentation
    min_indent = float('inf')
    for line in content_lines:
        content = line[1:]  # Skip the first character (+, -, or space)
        if content.strip():
            indent = len(content) - len(content.lstrip())
            min_indent = min(min_indent, indent)
    
    if min_indent == float('inf') or min_indent == 0:
        return diff
    
    # Trim lines
    trimmed_lines = []
    for line in lines:
        if (line.startswith("+") or line.startswith("-") or line.startswith(" ")) \
           and not line.startswith("---") and not line.startswith("+++"):
            prefix = line[0]
            content = line[1:]
            trimmed_lines.append(prefix + content[min_indent:])
        else:
            trimmed_lines.append(line)
    
    return "\n".join(trimmed_lines)

def _looks_like_filename_only_intent(raw_path: str, resolved_path: str, base_dir: str) -> bool:
    """
    Best-effort detect "user gave only a filename (no directory)" intent.

    Cases considered filename-only:
    - Relative path without any directory separator (e.g. ``hello.txt``)
    - Absolute path that points to the source root + basename
      (common when model auto-expands a bare filename to cwd/source dir)
    """
    normalized = (raw_path or "").replace("\\", "/")
    if not raw_path:
        return False
    if not os.path.isabs(raw_path):
        return "/" not in normalized
    try:
        return os.path.realpath(os.path.dirname(resolved_path)) == os.path.realpath(base_dir)
    except Exception:
        return False


async def _resolve_owner_username(ctx: ToolContext) -> Optional[str]:
    """Resolve owner username from auth context, then session ownership."""
    try:
        from flocks.auth.context import get_current_auth_user
        auth_user = get_current_auth_user()
        if auth_user and getattr(auth_user, "username", None):
            return str(auth_user.username)
    except Exception:
        pass

    session_id = getattr(ctx, "session_id", None)
    if not session_id or session_id == "default":
        return None
    try:
        from flocks.session.session import Session

        session = await Session.get_by_id(session_id)
        if session and getattr(session, "owner_username", None):
            return str(session.owner_username)
    except Exception:
        pass
    return None


async def _maybe_redirect_to_default_outputs(
    ctx: ToolContext,
    *,
    original_path: str,
    resolved_path: str,
    base_dir: str,
) -> str:
    """
    For filename-only writes, force stable default output location.

    This prevents nondeterministic writes to source root when user did not
    specify a directory and model expanded filename against cwd.
    """
    if not _looks_like_filename_only_intent(original_path, resolved_path, base_dir):
        return resolved_path
    if os.path.exists(resolved_path):
        # Existing file writes should remain explicit and deterministic.
        return resolved_path

    filename = os.path.basename(original_path) or os.path.basename(resolved_path)
    if not filename:
        return resolved_path

    try:
        from flocks.workspace.manager import WorkspaceManager

        owner_username = await _resolve_owner_username(ctx)
        outputs_dir = WorkspaceManager.get_instance().get_default_outputs_dir(
            username=owner_username
        )
        redirected = str((outputs_dir / filename).resolve())
        if redirected != resolved_path:
            log.info(
                "write.default_output_redirect",
                {"from": resolved_path, "to": redirected, "session_id": ctx.session_id},
            )
        return redirected
    except Exception as exc:
        log.debug("write.default_output_redirect.failed", {"error": str(exc)})
        return resolved_path


@ToolRegistry.register_function(
    name="write",
    description=DESCRIPTION,
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description="The content to write to the file",
            required=True
        ),
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description=(
                "The path to the file to write. It may be absolute, use `~`, or be relative to the current project directory.\n"
                "\n"
                "IMPORTANT — choose the correct directory from <env>:\n"
                "- Project source file (source code, tests, configs that belong to the project)"
                " → Source code directory\n"
                "- Agent-generated output (scripts, reports, examples, analysis results, drafts"
                " requested by user) → Workspace outputs directory\n"
                "\n"
                "Agent-generated outputs MUST go to the Workspace outputs directory."
                " NEVER write them into the Source code directory."
            ),
            required=True
        ),
    ]
)
async def write_tool(
    ctx: ToolContext,
    content: str,
    filePath: str,
) -> ToolResult:
    """
    Write content to a file
    
    Args:
        ctx: Tool context
        content: Content to write
        filePath: Target file path
        
    Returns:
        ToolResult with operation status
    """
    # Coerce non-string content: dicts/lists → JSON, everything else → str
    if not isinstance(content, str):
        if isinstance(content, (dict, list)):
            import json as _json
            content = _json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content = str(content)

    try:
        resolution = await resolve_tool_path(ctx, filePath)
        if resolution.sandbox_root is None:
            redirected_path = await _maybe_redirect_to_default_outputs(
                ctx,
                original_path=filePath,
                resolved_path=resolution.resolved_path,
                base_dir=resolution.base_dir,
            )
            if redirected_path != resolution.resolved_path:
                resolution = await resolve_tool_path(
                    ctx,
                    redirected_path,
                    base_dir=resolution.base_dir,
                    worktree=resolution.worktree,
                )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            title=filePath,
        )
    filepath = resolution.resolved_path

    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if isinstance(sandbox, dict) and sandbox.get("workspace_access") == "ro":
        return ToolResult(
            success=False,
            error=(
                "Write is blocked in sandbox read-only workspace mode. "
                "Set sandbox.workspace_access to 'rw' to allow writes."
            ),
            title=filePath,
        )
    
    # Get relative title for display
    title = resolution.display_path
    
    # Check if file exists and get old content
    exists = os.path.exists(filepath)
    old_content = ""
    
    if exists:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                old_content = f.read()
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to read existing file: {str(e)}",
                title=title
            )
    
    # Generate diff
    diff = trim_diff(generate_diff(filepath, old_content, content))
    
    # Request permission
    await ctx.ask(
        permission="edit",
        patterns=[resolution.permission_pattern],
        always=["*"],
        metadata={
            "filepath": filepath,
            "diff": diff
        }
    )
    
    # Create parent directory if needed
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to create directory: {str(e)}",
                title=title
            )
    
    # Write file
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to write file: {str(e)}",
            title=title
        )
    
    # Build output
    output = "Wrote file successfully."
    
    # Note: LSP diagnostics integration would go here
    # For now we just return success
    
    return ToolResult(
        success=True,
        output=output,
        title=title,
        metadata={
            "filepath": filepath,
            "exists": exists,
            "diagnostics": {}
        }
    )
