"""
Write Tool - File writing with diff generation

Writes files to the local filesystem with:
- Diff generation for existing files
- LSP diagnostics reporting
- Directory creation as needed
"""

import os
from typing import Optional
from difflib import unified_diff

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.project.instance import Instance
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


def _safe_relpath(path: str, start: Optional[str]) -> str:
    """Return a relative path when possible, otherwise keep the absolute path."""
    if not start:
        return path
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return path


async def _resolve_sandbox_file_path(
    ctx: ToolContext,
    filepath: str,
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    Resolve file path under sandbox workspace when sandbox is enabled.

    Returns:
        (resolved_path, error_message, sandbox_dict)
    """
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if not isinstance(sandbox, dict):
        return filepath, None, None

    workspace_root = sandbox.get("workspace_dir")
    if not workspace_root:
        return filepath, None, sandbox

    if not os.path.isabs(filepath):
        filepath = os.path.join(workspace_root, filepath)

    try:
        from flocks.sandbox.paths import assert_sandbox_path

        resolved = await assert_sandbox_path(
            file_path=filepath,
            cwd=workspace_root,
            root=workspace_root,
        )
        return resolved.resolved, None, sandbox
    except Exception:
        return None, (
            f"Path escapes sandbox workspace: {filepath}. "
            "Use paths inside sandbox workspace only."
        ), sandbox


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
                "The absolute path to the file to write (must be absolute, not relative).\n"
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

    # Resolve path
    filepath = filePath
    if not os.path.isabs(filepath):
        base_dir = Instance.get_directory() or os.getcwd()
        filepath = os.path.join(base_dir, filepath)

    filepath, sandbox_error, sandbox = await _resolve_sandbox_file_path(ctx, filepath)
    if sandbox_error:
        return ToolResult(
            success=False,
            error=sandbox_error,
            title=filePath,
        )
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
    worktree = Instance.get_worktree() or os.getcwd()
    title = _safe_relpath(filepath, worktree)
    
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
        patterns=[_safe_relpath(filepath, worktree)],
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
