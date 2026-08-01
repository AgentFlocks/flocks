"""
Shared path helpers for tool implementations.

Keeps path resolution behavior consistent across file, search, and runtime
tools while preserving Flocks' existing relative-path compatibility.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flocks.project.instance import Instance
from flocks.tool.registry import ToolContext


@dataclass(frozen=True)
class ToolPathResolution:
    """Normalized path details returned by shared tool path resolution."""

    raw_path: str
    resolved_path: str
    display_path: str
    permission_pattern: str
    base_dir: str
    worktree: str
    sandbox_root: Optional[str] = None


def get_tool_base_dir() -> str:
    """Return the default base directory for relative tool paths."""
    return Instance.get_directory() or os.getcwd()


def get_tool_worktree() -> str:
    """Return the default worktree used for display and permission paths."""
    return Instance.get_worktree() or get_tool_base_dir()


def _context_workspace_dir(ctx: ToolContext) -> str | None:
    """Return the session-owned workspace supplied by the execution runtime."""
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    workspace_dir = str(extra.get("workspace_dir") or "").strip()
    return workspace_dir or None


def _assert_verified_path(ctx: ToolContext, raw_path: str, resolved_path: str) -> None:
    """Reject a path whose canonical target changed after policy approval."""
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    binding = extra.get("filesystem_path_binding")
    if not isinstance(binding, dict):
        return
    for side in ("source", "target"):
        if str(binding.get(f"{side}_path") or "") != str(raw_path):
            continue
        expected = binding.get(f"{side}_canonical")
        if not isinstance(expected, dict):
            continue
        current_realpath = os.path.realpath(resolved_path)
        current_parent = os.path.realpath(os.path.dirname(resolved_path))
        if (
            expected.get("realpath") != current_realpath
            or expected.get("parent_realpath") != current_parent
            or expected.get("final_target") != current_realpath
        ):
            raise ValueError("Filesystem path changed after policy approval")
        return


def safe_relpath(path: str, start: Optional[str]) -> str:
    """Return a relative path when possible, otherwise keep the absolute path."""
    if not start:
        return path
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return path


def normalize_user_path(path: str) -> str:
    """
    Normalize user-provided path text before resolution.

    Expands ``~`` and normalizes path shape while preserving compatibility for
    both existing and not-yet-created files.
    """
    normalized = str(path).strip()
    expanded = Path(normalized).expanduser()
    return os.path.normpath(os.path.abspath(str(expanded)))


def resolve_host_path(path: str, *, base_dir: Optional[str] = None) -> str:
    """Resolve a user path on the host using a stable explicit base directory."""
    resolved_base = normalize_user_path(base_dir or get_tool_base_dir())
    normalized = str(path).strip()
    expanded = Path(normalized).expanduser()
    candidate = expanded if expanded.is_absolute() else Path(resolved_base) / expanded
    return str(candidate.resolve(strict=False))


async def resolve_tool_path(
    ctx: ToolContext,
    path: str,
    *,
    base_dir: Optional[str] = None,
    worktree: Optional[str] = None,
) -> ToolPathResolution:
    """
    Resolve a tool path consistently across host and sandbox contexts.

    Host mode:
    - expand ``~``
    - resolve relative paths against ``base_dir``
    - normalize to an absolute canonical path

    Sandbox mode:
    - resolve against sandbox workspace root
    - reject path traversal and symlink escapes
    """
    raw_path = path
    session_workspace_dir = _context_workspace_dir(ctx)
    resolved_base = normalize_user_path(base_dir or session_workspace_dir or get_tool_base_dir())
    resolved_worktree = normalize_user_path(worktree or session_workspace_dir or get_tool_worktree())

    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    sandbox_root = sandbox.get("workspace_dir") if isinstance(sandbox, dict) else None
    resolved_path: str
    normalized_input = str(raw_path).strip()

    if sandbox_root:
        from flocks.sandbox.paths import assert_sandbox_path

        normalized_root = normalize_user_path(sandbox_root)
        sandbox_input = str(Path(normalized_input).expanduser())
        if os.path.isabs(sandbox_input):
            sandbox_input = os.path.normpath(os.path.abspath(sandbox_input))
        try:
            result = await assert_sandbox_path(
                file_path=sandbox_input,
                cwd=normalized_root,
                root=normalized_root,
            )
        except Exception as exc:
            raise ValueError(
                f"Path escapes sandbox workspace: {raw_path}. "
                f"Use paths inside sandbox workspace only. ({exc})"
            ) from exc

        resolved_path = str(Path(result.resolved).resolve(strict=False))
        resolved_base = normalized_root
        resolved_worktree = normalized_root
    else:
        resolved_path = resolve_host_path(normalized_input, base_dir=resolved_base)

    _assert_verified_path(ctx, str(raw_path), resolved_path)
    display_path = safe_relpath(resolved_path, resolved_worktree)
    return ToolPathResolution(
        raw_path=raw_path,
        resolved_path=resolved_path,
        display_path=display_path,
        permission_pattern=display_path,
        base_dir=resolved_base,
        worktree=resolved_worktree,
        sandbox_root=normalize_user_path(sandbox_root) if sandbox_root else None,
    )
