"""Memory notes kept in PostgreSQL as well as on this instance's disk.

Every ``*.md`` under the memory root is also stored in ``file_mirror`` (area
``memory``), so another instance -- or the standby that takes over -- has the
same notes.  Each sync reconciles three versions of a note: the one here, the
stored one, and the one this instance last synced (recorded beside the memory
root, in ``.<root name>-mirror.json``, so it never shows up as a note):

- changed on one side only: the change goes to the other side;
- new on one side: copied to the other;
- deleted here, unchanged in the database: deleted in the database (which
  keeps a tombstone);
- deleted in the database, unchanged here -- or, when this instance has no
  record of the note, not touched here since the deletion -- moved aside
  here, to ``<name>.md.removed-<time>-<id>`` (no longer a note, still on disk);
- changed on both sides: when one is the other plus more text (a note that was
  appended to), the longer one wins; otherwise the stored note stays and this
  instance's version is kept beside it as ``<name>.conflict-<time>-<id>.md``.

Deletions only travel when this instance can trust its record: the record
names the directory it was made for (device and inode), so a memory root that
was replaced, renamed or lost starts over as a fresh copy -- it takes the
stored notes and gives back notes that are new here, never deletions.

Paths through a symbolic link (a linked directory or note) are someone's own
arrangement: never written, deleted or moved aside, in either direction.

Writes to the database are conditional on the stored note still being the
one this instance last saw, so two instances never silently overwrite each
other; a note that changed meanwhile is settled on the next sync.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from flocks.storage.file_mirror import ABSENT, FileMirror, sha256_of
from flocks.utils.log import Log

log = Log.create(service="memory.mirror")


@dataclass
class LocalNote:
    sha256: str
    size: int
    mtime: float


@dataclass
class ReconcileStats:
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    deleted_remote: list[str] = field(default_factory=list)
    moved_aside: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    #: Not settled this time (changed in the database meanwhile, or the store
    #: could not serve it): only on this side, or only on the other, for now.
    deferred: list[str] = field(default_factory=list)
    #: Behind a link here: someone's own arrangement, left alone on purpose.
    linked: list[str] = field(default_factory=list)
    #: Conflict copies made this time (this instance's version of a note the
    #: store already had another version of); deferred too when not stored.
    kept_copies: list[str] = field(default_factory=list)
    #: The record of the last sync did not fit this directory: nothing was
    #: taken for a deletion this time.
    fresh: bool = False


def manifest_path(root: Path) -> Path:
    return root.parent / f".{root.name}-mirror.json"


def _root_identity(root: Path) -> str:
    stat = root.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


def _load_manifest(root: Path) -> tuple[Optional[str], dict[str, dict]]:
    try:
        data = json.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    files = data.get("files")
    entries = {str(k): v for k, v in files.items() if isinstance(v, dict)} if isinstance(files, dict) else {}
    root_id = data.get("root")
    return (root_id if isinstance(root_id, str) else None), entries


def _save_manifest(root: Path, files: dict[str, dict]) -> None:
    target = manifest_path(root)
    handle, temporary = tempfile.mkstemp(prefix=".flocks-mirror-", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"version": 2, "root": _root_identity(root), "files": files}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _scan(
    root: Path, manifest: dict[str, dict], only: Optional[set[str]],
) -> tuple[dict[str, LocalNote], set[str]]:
    """The notes here, and the paths behind links (left alone: not ours to touch).

    Walks without following links; a linked directory marks everything below it.
    """
    notes: dict[str, LocalNote] = {}
    linked: set[str] = set()
    if not root.is_dir():
        return notes, linked
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirnames:
            if (base / name).is_symlink():
                linked.add((base / name).relative_to(root).as_posix() + "/")
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = base / name
            relative = path.relative_to(root).as_posix()
            if only is not None and relative not in only:
                continue
            if path.is_symlink():
                linked.add(relative)
                continue
            if not path.is_file():
                continue
            stat = path.stat()
            known = manifest.get(relative)
            if known and known.get("size") == stat.st_size and known.get("mtime") == stat.st_mtime:
                # Unchanged since it was last synced: no need to read it again.
                notes[relative] = LocalNote(str(known.get("sha256")), stat.st_size, stat.st_mtime)
                continue
            notes[relative] = LocalNote(sha256_of(path.read_bytes()), stat.st_size, stat.st_mtime)
    return notes, linked


def _behind_link(relative: str, linked: set[str]) -> bool:
    return relative in linked or any(relative.startswith(prefix) for prefix in linked if prefix.endswith("/"))


def _entry(path: Path) -> dict:
    stat = path.stat()
    return {"sha256": sha256_of(path.read_bytes()), "size": stat.st_size, "mtime": stat.st_mtime}


def _unique_suffix() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _keep_copy(target: Path, data: bytes) -> Path:
    """This instance's version beside the note, under a name nothing else has."""
    while True:
        copy = target.with_name(f"{target.stem}.conflict-{_unique_suffix()}.md")
        try:
            with open(copy, "xb") as stream:
                stream.write(data)
            return copy
        except FileExistsError:
            continue


def _move_aside(target: Path) -> Path:
    """The note out of the way (no longer a note), never over anything."""
    while True:
        aside = target.with_name(f"{target.name}.removed-{_unique_suffix()}")
        if not aside.exists():
            os.replace(target, aside)
            return aside


_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _lock_for(root: Path) -> asyncio.Lock:
    # Per event loop as well: a lock is bound to the loop it first waits on.
    key = (str(root.resolve(strict=False)), id(asyncio.get_running_loop()))
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


async def reconcile(root: Path, *, only: Optional[Iterable[str]] = None) -> ReconcileStats:
    """Settle the notes under *root* with the stored ones; *only* limits it to some notes.

    One reconciliation of a root at a time in this process: each one reads and
    rewrites the record of what was last synced.
    """
    async with _lock_for(root):
        return await _reconcile(root, only)


async def _reconcile(root: Path, only: Optional[Iterable[str]]) -> ReconcileStats:
    mirror = FileMirror("memory", root)
    selected = set(only) if only is not None else None
    stored_all = await mirror.listing("", tombstones=True)
    if selected is not None:
        stored_all = {path: note for path, note in stored_all.items() if path in selected}
    stored = {path: note for path, note in stored_all.items() if not note.deleted}
    removed = {path: note for path, note in stored_all.items() if note.deleted}
    stats = ReconcileStats()
    if not stored and not root.is_dir():
        return stats
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    recorded_root, manifest = await asyncio.to_thread(_load_manifest, root)
    if recorded_root != await asyncio.to_thread(_root_identity, root):
        # Not the directory this record was made for (replaced, renamed,
        # restored, or a first sync): a fresh copy, which never deletes.
        manifest = {}
        stats.fresh = True
    local, linked = await asyncio.to_thread(_scan, root, manifest, selected)
    synced: dict[str, dict] = {
        path: entry for path, entry in manifest.items() if selected is not None and path not in selected
    }

    async def read_local(relative: str) -> bytes:
        return await asyncio.to_thread(mirror.local(relative).read_bytes)

    async def pull(relative: str) -> None:
        data = await mirror.fetch(relative)
        if data is None:
            stats.deferred.append(relative)
            return
        try:
            target = await mirror.write_local(relative, data)
        except ValueError:
            stats.deferred.append(relative)
            return
        synced[relative] = await asyncio.to_thread(_entry, target)
        stats.pulled.append(relative)

    async def push(relative: str, expected: str) -> None:
        if await mirror.put_bytes(relative, await read_local(relative), expected=expected):
            note = local[relative]
            synced[relative] = {"sha256": note.sha256, "size": note.size, "mtime": note.mtime}
            stats.pushed.append(relative)
        else:
            # Changed in the database meanwhile; the next sync sees both.
            stats.deferred.append(relative)

    def candidates() -> Iterable[str]:
        names = set(stored) | set(local) | set(removed)
        names |= set(manifest) if selected is None else set(manifest) & selected
        return sorted(names)

    for relative in candidates():
        if _behind_link(relative, linked) or await asyncio.to_thread(mirror.is_linked, relative):
            # A link here is someone's own arrangement: neither replaced by
            # the stored note nor taken as a deletion of it.
            if relative in manifest:
                synced[relative] = manifest[relative]
            stats.linked.append(relative)
            continue
        held = local.get(relative)
        kept = stored.get(relative)
        here = held.sha256 if held else None
        there = kept.sha256 if kept else None
        base = (manifest.get(relative) or {}).get("sha256")
        if here == there:
            if held is not None:
                synced[relative] = {"sha256": held.sha256, "size": held.size, "mtime": held.mtime}
            continue
        if here is None:
            if base is not None and there == base:
                if await mirror.forget(relative, expected=there):
                    stats.deleted_remote.append(relative)
                    continue
            if there is not None:
                await pull(relative)
            continue
        if there is None:
            tombstone = removed.get(relative)
            unchanged_since_sync = base is not None and here == base
            untouched_since_removal = base is None and tombstone is not None and held.mtime <= tombstone.updated_at
            if unchanged_since_sync or untouched_since_removal:
                await asyncio.to_thread(_move_aside, mirror.local(relative))
                stats.moved_aside.append(relative)
                continue
            await push(relative, ABSENT)
            continue
        if here == base:
            await pull(relative)
        elif there == base:
            await push(relative, there)
        else:
            mine, theirs = await read_local(relative), await mirror.fetch(relative)
            if theirs is None:
                stats.deferred.append(relative)
            elif theirs.startswith(mine):
                await pull(relative)
            elif mine.startswith(theirs):
                await push(relative, there)
            else:
                copy = await asyncio.to_thread(_keep_copy, mirror.local(relative), mine)
                await pull(relative)
                stats.conflicts.append(relative)
                log.warning("memory.mirror.conflict", {"path": relative, "kept_as": copy.name})
                # This instance's version now lives in the copy: shared only
                # once the copy is stored too, in this same round -- until
                # then the note's own edit is on this instance alone.
                kept_as = mirror.relative(copy)
                stats.kept_copies.append(kept_as)
                copy_stat = await asyncio.to_thread(copy.stat)
                local[kept_as] = LocalNote(sha256_of(mine), copy_stat.st_size, copy_stat.st_mtime)
                await push(kept_as, ABSENT)
                if kept_as not in stats.pushed and relative not in stats.deferred:
                    stats.deferred.append(relative)
    await asyncio.to_thread(_save_manifest, root, synced)
    if stats.pushed or stats.pulled or stats.deleted_remote or stats.moved_aside or stats.conflicts:
        log.info("memory.mirror.reconciled", {
            "pushed": len(stats.pushed), "pulled": len(stats.pulled),
            "deleted_remote": len(stats.deleted_remote), "moved_aside": len(stats.moved_aside),
            "conflicts": len(stats.conflicts), "deferred": len(stats.deferred), "fresh": stats.fresh,
        })
    return stats


class MemoryNotShared(RuntimeError):
    """A memory note could not be settled with the stored notes: the other instances do not have it."""


def _conflict_copies_of(root: Path, relative: str) -> set[str]:
    """This instance's conflict copies of the note (``<name>.conflict-*.md`` beside it)."""
    note = root / relative
    try:
        return {
            copy.relative_to(root).as_posix()
            for copy in note.parent.glob(f"{note.stem}.conflict-*.md")
            if copy.is_file() and not copy.is_symlink()
        }
    except OSError:
        return set()


async def reconcile_note(root: Path, note: Path, *, attempts: int = 1) -> bool:
    """Settle one note (by its path); whether it is now the same here and in the store.

    False -- logged, not raised -- when the store is unreachable or the note
    could not be settled (it changed in the database meanwhile).  A note that
    changed meanwhile settles on another try: *attempts* tries that often.  A
    note behind a link counts as settled: it is kept here on purpose.

    The note's conflict copies settle with it -- the ones beside it already and
    the ones made on the way: a copy holds this instance's edit, and the note
    is not shared while the copy is only here.
    """
    relative = FileMirror("memory", root).relative(note)
    targets = {relative} | await asyncio.to_thread(_conflict_copies_of, root, relative)
    for _ in range(max(1, attempts)):
        try:
            stats = await reconcile(root, only=targets)
        except ValueError:
            raise
        except Exception as exc:
            log.warning("memory.mirror.unavailable", {"error_type": type(exc).__name__})
            return False
        targets |= set(stats.kept_copies)
        if not set(stats.deferred) & targets:
            return True
    log.warning("memory.mirror.not_settled", {"path": relative})
    return False


async def reconcile_all(root: Path) -> Optional[ReconcileStats]:
    """Settle every note; what happened, or None -- the local notes left as they are -- when the store is unreachable."""
    try:
        return await reconcile(root)
    except Exception as exc:
        log.warning("memory.mirror.unavailable", {"error_type": type(exc).__name__})
        return None


async def reconcile_quietly(root: Path) -> bool:
    """Settle every note; False -- the local notes left as they are -- when the store is unreachable."""
    return await reconcile_all(root) is not None


__all__ = [
    "MemoryNotShared",
    "ReconcileStats",
    "manifest_path",
    "reconcile",
    "reconcile_all",
    "reconcile_note",
    "reconcile_quietly",
]
