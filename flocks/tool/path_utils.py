"""
Shared path helpers for tool implementations.

Keeps path resolution behavior consistent across file, search, and runtime
tools while preserving Flocks' existing relative-path compatibility.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from flocks.project.instance import Instance
from flocks.tool.registry import ToolContext
from flocks.config.config import get_user_plugins_root


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


class DirectoryLinkLoop(ValueError):
    """A directory link points back at one of its own ancestors."""


class TreeChanged(ValueError):
    """A directory was replaced (by a link, say) while the tree was being listed."""


def tree_entries(root: str, *, follow_links: bool = False) -> list[str]:
    """Relative paths of ``root`` ("") and of every entry below it.

    Deleting, copying or moving a directory changes every file under it, so
    path rules written for any of them must see the request.
    ``follow_links`` mirrors ``copytree(symlinks=False)``, which copies what a
    directory link points at, once for every link that reaches it; ``rmtree``
    and renames keep links as links. A link back into its own ancestors would
    make that copy endless, so it raises :class:`DirectoryLinkLoop`.

    Without ``follow_links``, a directory that is not the one checked just
    before it was read -- replaced by a link to someone else's files, say --
    raises :class:`TreeChanged`: what was read there is not this tree.
    """
    entries = [""]
    if not os.path.isdir(root) or (os.path.islink(root) and not follow_links):
        return entries
    canonical_root = os.path.realpath(root)
    pending = [(root, "", (canonical_root,))]
    while pending:
        directory, prefix, ancestors = pending.pop()
        if not follow_links:
            # Queued as a real directory reached without links: still one, and
            # still reached that way (no link put on the way), before and after
            # it is read.
            expected = os.path.join(canonical_root, prefix) if prefix else canonical_root
            identity = _directory_identity(directory)
            if identity is None or os.path.realpath(directory) != expected:
                raise TreeChanged(f"{directory} changed while the tree was listed")
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        if not follow_links and (
            _directory_identity(directory) != identity or os.path.realpath(directory) != expected
        ):
            raise TreeChanged(f"{directory} changed while the tree was listed")
        for name in names:
            path = os.path.join(directory, name)
            relative = os.path.join(prefix, name) if prefix else name
            entries.append(relative)
            if not os.path.isdir(path) or (os.path.islink(path) and not follow_links):
                continue
            real = os.path.realpath(path)
            if real in ancestors:
                raise DirectoryLinkLoop(f"{path} links back to {real}")
            pending.append((path, relative, ancestors + (real,)))
    return entries


def _directory_identity(path: str) -> Optional[tuple[int, int]]:
    """(device, inode) of a real directory at ``path``; None for anything else."""
    try:
        status = os.lstat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino) if stat.S_ISDIR(status.st_mode) else None


def tree_permission_patterns(
    base_pattern: str,
    base_path: str,
    entries: list[str],
    *,
    worktree: Optional[str] = None,
) -> list[str]:
    """Permission patterns for ``entries`` taken from or landing at ``base_path``.

    With ``worktree`` (where the tree lands), an entry whose real location
    differs because a symlink is on the way is also named by that location:
    the write goes there.
    """
    patterns: list[str] = []
    for relative in entries:
        patterns.append(os.path.join(base_pattern, relative) if relative else base_pattern)
        if worktree is not None:
            logical = os.path.normpath(os.path.join(base_path, relative) if relative else base_path)
            real = os.path.realpath(logical)
            if real != logical:
                patterns.append(safe_relpath(real, worktree))
    return list(dict.fromkeys(patterns))


PathIdentity = tuple[str, str, Optional[tuple[int, int]]]


def path_identities(*paths: str) -> tuple[PathIdentity, ...]:
    """Where resolved tool paths lead now, to compare after a permission wait."""
    return tuple(
        (path, os.path.realpath(path), _entry_identity(path))
        for path in dict.fromkeys(paths)
    )


def moved_since_resolved(*paths: str) -> list[str]:
    """The paths, as ``resolve_tool_path`` returned them, that no longer lead to themselves.

    Checked before an operation reads or lists anything through them and again
    before it asks for approval: through a link put on the way, the request
    (its diff, its patterns) would show another user's file contents or names.
    """
    return [path for path in dict.fromkeys(paths) if os.path.realpath(path) != path]


def changed_before_asking(path: str) -> str:
    return f"{path} changed after it was checked; check it again and retry."


def checked_error(paths: Sequence[str], message: str) -> str:
    """``message``, unless one of ``paths`` moved since it was checked.

    An error raised through a path that now leads elsewhere may quote what it
    found there (file contents, names), so it is replaced by a generic one.
    """
    moved = moved_since_resolved(*paths)
    return changed_before_asking(moved[0]) if moved else message


def resolved_identities(*paths: str) -> tuple[PathIdentity, ...]:
    """Snapshot paths as ``resolve_tool_path`` returned them and the policy checked them.

    Such a path is canonical, so where it should lead is the path itself, not
    wherever it leads by the time of the snapshot: a link put on the way at
    any point after it was resolved -- before the snapshot as much as during
    a permission wait -- shows as a change.
    """
    return tuple((path, path, _entry_identity(path)) for path in dict.fromkeys(paths))


def paths_changed(identities: Sequence[PathIdentity]) -> list[str]:
    """The paths that no longer lead where they did when snapshotted.

    Paths are checked against the workspace, sandbox and policy bindings when
    they are resolved; a link put on the way during a permission wait would
    send the approved operation to another user's files, and so would a hard
    link or another file put in place of the one the request was about.
    """
    return [
        path
        for path, realpath, identity in identities
        if os.path.realpath(path) != realpath or _entry_identity(path) != identity
    ]


def _entry_identity(path: str) -> Optional[tuple[int, int]]:
    try:
        status = os.lstat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


async def ask_edit_permission(
    ctx: ToolContext,
    tool: str,
    planned_patterns: Callable[[], list[str]],
    metadata: dict,
    *,
    planned_paths: Optional[Callable[[], Sequence[str]]] = None,
    resolved_paths: Sequence[str] = (),
) -> bool:
    """Ask for ``edit`` on the paths an operation will change.

    The paths are listed again once the (possibly long) permission wait is
    over; False means they changed meanwhile (new files, a target that became
    a directory, a new link, one of ``planned_paths`` -- every path the
    operation reads or writes -- now leading elsewhere, or one of
    ``resolved_paths``, as resolve_tool_path returned them, no longer leading
    to itself) and the approval no longer covers the operation. Walking a
    large tree takes a while, so the first listing runs off the event loop.
    The last one stays on it, with no await between it and the caller's
    operation, so nothing else in the process can change the tree in between.
    """
    # Taken before the first await, against the paths the policy checked
    # rather than what the listing will find there.
    if moved_since_resolved(*resolved_paths):
        return False
    resolved = resolved_identities(*resolved_paths)

    def first_listing() -> Optional[tuple[list[str], tuple[PathIdentity, ...]]]:
        # Nothing listed through a path that moved goes into the request.
        if moved_since_resolved(*resolved_paths):
            return None
        try:
            patterns = planned_patterns()
            paths = planned_paths() if planned_paths is not None else ()
        except TreeChanged:
            return None
        if moved_since_resolved(*resolved_paths):
            return None
        return patterns, path_identities(*paths)

    listing = await asyncio.to_thread(first_listing)
    if listing is None or moved_since_resolved(*resolved_paths):
        return False
    patterns, identities = listing
    await ctx.ask(
        permission="edit",
        patterns=patterns,
        always=["*"],
        metadata={"tool": tool, **metadata},
    )
    try:
        listed_again = planned_patterns()
    except TreeChanged:
        return False
    return (
        set(listed_again) == set(patterns)
        and not paths_changed(resolved)
        and not paths_changed(identities)
    )


def landing_location(source: str, target: str, target_pattern: str) -> tuple[str, str]:
    """Path and permission pattern of where ``shutil.move`` / ``copy2`` put ``source``.

    Both drop the source inside ``target`` when it is an existing directory.
    """
    if os.path.isdir(target):
        name = os.path.basename(os.path.normpath(source))
        return os.path.join(target, name), os.path.join(target_pattern, name)
    return target, target_pattern


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


def _resolve_host_memory_path(path: str) -> Optional[tuple[str, str]]:
    """Resolve an absolute path when it belongs to Flocks' host Memory root."""
    expanded = Path(str(path).strip()).expanduser()
    if not expanded.is_absolute():
        return None

    from flocks.config import Config
    from flocks.memory.paths import path_is_within

    memory_root = (Config.get_data_path() / "memory").resolve(strict=False)
    candidate = expanded.resolve(strict=False)
    if not path_is_within(memory_root, candidate):
        return None
    return str(candidate), str(memory_root)


def _resolve_host_skill_path(
    ctx: ToolContext,
    path: str,
) -> Optional[tuple[str, str]]:
    """Resolve self-improve writes inside the host user Skill root."""
    if ctx.agent != "self-improve":
        return None
    expanded = Path(str(path).strip()).expanduser()
    if not expanded.is_absolute():
        return None

    from flocks.memory.paths import path_is_within

    skill_root = (get_user_plugins_root() / "skills").resolve(strict=False)
    candidate = expanded.resolve(strict=False)
    if not path_is_within(skill_root, candidate):
        return None
    return str(candidate), str(skill_root)


async def resolve_tool_path(
    ctx: ToolContext,
    path: str,
    *,
    base_dir: Optional[str] = None,
    worktree: Optional[str] = None,
    allow_host_memory: bool = False,
    allow_host_skills: bool = False,
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
    - optionally allow the host Memory root or self-improve's user Skill root
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
        normalized_root = normalize_user_path(sandbox_root)
        host_path = (
            _resolve_host_memory_path(normalized_input)
            if allow_host_memory
            else None
        )
        if host_path is None and allow_host_skills:
            host_path = _resolve_host_skill_path(ctx, normalized_input)
        if host_path is not None:
            resolved_path, host_root = host_path
            resolved_base = host_root
            resolved_worktree = str(Path(host_root).parent)
        else:
            from flocks.sandbox.paths import assert_sandbox_path

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
                allowed_locations = (
                    "the sandbox workspace or an allowed Flocks data root"
                    if allow_host_memory or allow_host_skills
                    else "the sandbox workspace"
                )
                raise ValueError(
                    f"Path escapes sandbox workspace: {raw_path}. "
                    f"Use paths inside {allowed_locations} only. ({exc})"
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
