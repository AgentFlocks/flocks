"""
Path validation and extension private-file rules for HTTP endpoints.

Blocks arbitrary file read without changing :meth:`flocks.utils.file.File.read`
used by internal callers (agent tools, memory, etc.).

- Generic file roots apply to ``/api/file/*``; private-file predicates also
  apply after other routes' existing ownership and containment checks.
- Reads ``allowReadPaths`` via :meth:`flocks.config.config.Config.get` (Pydantic alias).
- When no ``.flocks`` project root exists, does not fall back to cwd as a sandbox root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional, Set

from flocks.config.config import Config, ConfigInfo
from flocks.utils.paths import find_flocks_project_root


_private_path_providers: dict[str, Callable[[Path], bool]] = {}


def register_http_private_path_provider(name: str, provider: Callable[[Path], bool]) -> None:
    """Add an HTTP-only deny predicate; re-registering a name replaces it.

    The predicate receives a resolved absolute path and must return a bool.
    Keep it synchronous and cheap: compare paths, never perform network or
    database work. A provider failure denies access rather than ignoring it.
    """
    if not isinstance(name, str) or not name.strip() or not callable(provider):
        raise ValueError("A private-path provider needs a name and callable predicate")
    _private_path_providers[name] = provider


def unregister_http_private_path_provider(name: str) -> bool:
    return _private_path_providers.pop(name, None) is not None


def assert_http_private_path_access(path: str | Path, *, resolved: bool = False) -> None:
    """Apply registered deny rules, without granting access or changing ownership.

    ``resolved=True`` is for callers which just resolved a filesystem entry;
    it avoids resolving every directory-listing candidate twice.
    """
    try:
        target = Path(path) if resolved else Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise PermissionError("Invalid private file path") from None
    for provider in list(_private_path_providers.values()):
        try:
            decision = provider(target)
            if not isinstance(decision, bool):
                raise TypeError("Private-path predicates must return bool")
        except Exception:
            raise PermissionError("Private file access policy is unavailable") from None
        if decision:
            raise PermissionError("Path is private and cannot be read over HTTP")


def http_private_path_is_blocked(path: str | Path, *, resolved: bool = False) -> bool:
    """Filter a listed entry; an unavailable policy never exposes its metadata."""
    try:
        assert_http_private_path_access(path, resolved=resolved)
    except PermissionError:
        return True
    return False


def require_http_private_path_access(path: str | Path) -> None:
    """HTTP boundary for a path already authorized by its owning route."""
    from fastapi import HTTPException

    try:
        assert_http_private_path_access(path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


def _safe_system_files_resolved() -> Set[str]:
    """Small built-in allowlist of safe system files (resolved absolute paths)."""
    out: Set[str] = set()
    for p in ("/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
        try:
            rp = Path(p).resolve()
            if rp.is_file():
                out.add(str(rp))
        except OSError:
            continue
    return out


_SAFE_SYSTEM_FILES: Set[str] = _safe_system_files_resolved()


def _normalize_allow_read_entries(entries: Optional[List[str]]) -> List[str]:
    """Validate extra readable paths from config.

    Requirements: absolute, not FS root, not under config dir or ``~/.ssh``.
    """
    if not entries:
        return []

    cfg_dir = Config.get_config_path().resolve()
    try:
        ssh_dir = (Path.home() / ".ssh").resolve()
    except OSError:
        ssh_dir = None

    out: List[str] = []
    seen: Set[str] = set()

    for raw in entries:
        if not raw or not isinstance(raw, str):
            continue
        expanded = os.path.normpath(os.path.expanduser(raw.strip()))
        if not expanded:
            continue
        if not os.path.isabs(expanded):
            continue
        try:
            p = Path(expanded).resolve()
        except OSError:
            continue

        if p.parent == p:
            continue

        ps = str(p)

        if ps == str(cfg_dir) or p.is_relative_to(cfg_dir):
            continue
        if ssh_dir is not None and (ps == str(ssh_dir) or p.is_relative_to(ssh_dir)):
            continue

        if ps not in seen:
            seen.add(ps)
            out.append(ps)

    return out


def _blocked_for_http_read(resolved_str: str) -> bool:
    """Always deny these sensitive locations, even if another allow-root would match.

    ``resolved_str`` must already be a :meth:`Path.resolve`-d absolute path.
    """
    r = Path(resolved_str)

    cfg_dir = Config.get_config_path().resolve()
    if r == cfg_dir or r.is_relative_to(cfg_dir):
        return True

    try:
        ssh = (Path.home() / ".ssh").resolve()
        if r == ssh or r.is_relative_to(ssh):
            return True
    except OSError:
        pass

    if os.name == "posix":
        for prefix in ("/proc", "/sys", "/dev"):
            if resolved_str == prefix or resolved_str.startswith(prefix + os.sep):
                return True

    assert_http_private_path_access(r, resolved=True)
    return False


def _is_filesystem_root(p: Path) -> bool:
    try:
        r = p.resolve()
    except OSError:
        return True
    return r.parent == r


def _assert_path_contained(file_path: str, root: str) -> str:
    """Check that *file_path* resolves to somewhere inside *root*.

    Follows symlinks to prevent escape via symlinked directories/files.
    Raises ``ValueError`` when the resolved path is outside *root*.

    Returns the resolved absolute path.
    """
    root_resolved = Path(root).resolve()
    target_resolved = Path(file_path).resolve()

    if not (target_resolved == root_resolved or target_resolved.is_relative_to(root_resolved)):
        raise ValueError(
            f"Path {file_path} resolves to {target_resolved} which is outside {root_resolved}"
        )

    return str(target_resolved)


def _initial_abs_path(user_path: str, project_root: Optional[Path]) -> str:
    """Turn the query path into an absolute path.

    Relative paths are only allowed when a project root exists and are
    resolved relative to it.
    """
    raw = user_path.strip()
    if not raw:
        raise PermissionError("Empty path")
    if "\x00" in raw:
        raise PermissionError("Invalid path")
    if len(raw) > 4096:
        raise PermissionError("Path too long")

    expanded = str(Path(raw).expanduser())
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)

    if project_root is None:
        raise PermissionError("Relative paths require a Flocks project root (.flocks/)")

    return os.path.normpath(os.path.join(str(project_root.resolve()), expanded))


async def resolve_path_for_http_file_access(
    user_path: str,
    config: ConfigInfo,
) -> str:
    """Resolve and authorize ``user_path`` for HTTP file access.

    On success, returns an absolute path safe to open.

    Raises:
        PermissionError: Path is not allowed for remote HTTP file access.
    """
    project_root = find_flocks_project_root()
    abs_guess = _initial_abs_path(user_path, project_root)

    extra_roots = _normalize_allow_read_entries(config.allow_read_paths)

    roots: List[str] = []
    if project_root is not None and not _is_filesystem_root(project_root):
        roots.append(str(project_root.resolve()))
    data_dir = Config.get_data_path().resolve()
    if not _is_filesystem_root(data_dir):
        roots.append(str(data_dir))

    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    ws_dir = manager.get_workspace_dir().resolve()
    # Apply the workspace's ownership policy before broader project/data or
    # allowReadPaths roots can authorize the same file. Resolve symlinks first
    # so an alias outside the workspace cannot bypass that policy.
    assert_http_workspace_path_access(abs_guess, manager=manager)
    if not _is_filesystem_root(ws_dir):
        roots.append(str(ws_dir))

    for r in extra_roots:
        if r not in roots:
            roots.append(r)

    seen: Set[str] = set()
    uniq: List[str] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    for root in uniq:
        try:
            resolved = _assert_path_contained(abs_guess, root)
            if not _blocked_for_http_read(resolved):
                return resolved
        except ValueError:
            continue

    try:
        safe_resolved = str(Path(abs_guess).resolve())
    except OSError as e:
        raise PermissionError("Invalid path") from e

    if (
        _SAFE_SYSTEM_FILES
        and safe_resolved in _SAFE_SYSTEM_FILES
        and not _blocked_for_http_read(safe_resolved)
    ):
        return safe_resolved

    raise PermissionError("Path is not allowed for remote file access")


def assert_http_workspace_path_access(path: str, *, manager=None, workspace: Path | None = None) -> None:
    """Honor the workspace resolver's policy for a canonical file/search hit.

    Core's resolver only checks containment. Extensions may add per-user
    ownership checks there; every HTTP file entry point must retain them.
    Paths outside the workspace still use the calling endpoint's read roots.
    Callers checking many hits may pass the already resolved ``workspace``.
    """
    from flocks.workspace.manager import WorkspaceManager

    manager = manager or WorkspaceManager.get_instance()
    workspace = workspace if workspace is not None else manager.get_workspace_dir().resolve()
    target = Path(path).resolve()
    if _blocked_for_http_read(str(target)):
        raise PermissionError("Path is not allowed for remote file access")
    if target.is_relative_to(workspace):
        manager.resolve_workspace_path(target.relative_to(workspace).as_posix())


__all__ = [
    "resolve_path_for_http_file_access", "assert_http_workspace_path_access",
    "register_http_private_path_provider", "unregister_http_private_path_provider",
    "assert_http_private_path_access", "require_http_private_path_access", "http_private_path_is_blocked",
]
