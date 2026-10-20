"""Bring files written before they were shared into the database, and check them.

Uploads and channel media are kept in PostgreSQL (``file_mirror``) as they
arrive; those that arrived before -- or on an instance that could not reach
the database then -- exist only on the disk that has them.  This module
compares an instance's disk with the store, file by file, and stores what only
the disk has, so every instance can read it.  It never overwrites a stored copy
and never brings back a file removed elsewhere (a tombstone): those are
reported, for a person to look at.

Memory notes are kept by their own sync (flocks.memory.sync.mirror), which
also stores notes that are new on a disk; checking them here is read-only.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from flocks.storage.file_mirror import NEVER_STORED, FileMirror, FileTooLargeToShare, max_bytes, sha256_of

#: Areas whose files are written once: a local file of the stored size is the stored file.
WRITE_ONCE_AREAS = ("uploads", "channel_media")
AREAS = (*WRITE_ONCE_AREAS, "memory")

# States a file can be in, as the check reports it.
SHARED = "shared"  # here and stored, the same
LOCAL_ONLY = "local_only"  # here, not stored: backfill stores it
STORED_ONLY = "stored_only"  # stored, not here: restore brings it
DIFFERS = "differs"  # here and stored, not the same: left for a person
REMOVED_ELSEWHERE = "removed_elsewhere"  # here, removed from the store: not brought back
TOO_LARGE = "too_large"  # here, over the size the store keeps
LINKED = "linked"  # behind a symbolic link here: not this cache's
STORED_NOW = "stored"  # stored by this backfill


@dataclass(frozen=True)
class FileState:
    area: str
    path: str
    state: str
    size: Optional[int] = None
    sha256: Optional[str] = None

    def to_dict(self) -> dict:
        return {"area": self.area, "path": self.path, "state": self.state, "size": self.size, "sha256": self.sha256}


def mirror_for(area: str) -> FileMirror:
    from flocks.config.config import Config

    roots = {
        "uploads": Config.get_data_path() / "uploads",
        "channel_media": Config.get_data_path() / "channel_media",
        "memory": Config.get_data_path() / "memory",
    }
    if area not in roots:
        raise ValueError(f"unknown area {area!r}; one of {', '.join(AREAS)}")
    return FileMirror(area, roots[area].resolve())


def _local_files(mirror: FileMirror) -> Dict[str, Path]:
    """Every regular file under the area's root, by its stored path."""
    files: Dict[str, Path] = {}
    if not mirror.root.is_dir():
        return files
    for directory, dirnames, filenames in os.walk(mirror.root, followlinks=False):
        base = Path(directory)
        # Memory sync bookkeeping and editor leftovers are not notes.
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = base / name
            if mirror.area == "memory" and path.suffix != ".md":
                continue
            try:
                files[mirror.relative(path)] = path
            except ValueError:
                continue
    return files


async def check_area(area: str) -> List[FileState]:
    """How this instance's disk compares with the store, file by file."""
    mirror = mirror_for(area)
    local = await asyncio.to_thread(_local_files, mirror)
    stored = await mirror.listing("", tombstones=True)
    write_once = area in WRITE_ONCE_AREAS
    limit = max_bytes()
    states: List[FileState] = []
    for relative in sorted(set(local) | set(stored)):
        row = stored.get(relative)
        path = local.get(relative)
        if path is None:
            if row is not None and not row.deleted:
                states.append(FileState(area, relative, STORED_ONLY, row.size, row.sha256))
            continue
        if mirror.is_linked(relative):
            states.append(FileState(area, relative, LINKED))
            continue
        size = path.stat().st_size
        if row is not None and row.deleted:
            states.append(FileState(area, relative, REMOVED_ELSEWHERE, size))
            continue
        if row is None:
            state = TOO_LARGE if size > limit else LOCAL_ONLY
            states.append(FileState(area, relative, state, size))
            continue
        if size != row.size:
            states.append(FileState(area, relative, DIFFERS, size, row.sha256))
            continue
        if not write_once:
            digest = await asyncio.to_thread(lambda: sha256_of(path.read_bytes()))
            if digest != row.sha256:
                states.append(FileState(area, relative, DIFFERS, size, row.sha256))
                continue
        states.append(FileState(area, relative, SHARED, size, row.sha256))
    return states


async def backfill_area(area: str, *, dry_run: bool = False) -> List[FileState]:
    """Store what only this disk has; report everything else as the check does.

    A file is stored only while nothing is stored at its path: one stored or
    removed meanwhile by another instance is left as it is.
    """
    if area == "memory":
        raise ValueError("memory notes are stored by the memory sync; run it instead of a backfill")
    mirror = mirror_for(area)
    results: List[FileState] = []
    for state in await check_area(area):
        if state.state != LOCAL_ONLY or dry_run:
            results.append(state)
            continue
        try:
            stored = await mirror.put(mirror.local(state.path), expected=NEVER_STORED)
        except FileTooLargeToShare:
            results.append(FileState(area, state.path, TOO_LARGE, state.size))
            continue
        if stored:
            row = (await mirror.listing(state.path)).get(state.path)
            results.append(FileState(area, state.path, STORED_NOW, state.size, row.sha256 if row else None))
        else:
            # Stored, or removed, by another instance between the check and now.
            results.append(FileState(area, state.path, DIFFERS, state.size))
    return results


async def restore_area(area: str) -> int:
    """Write every stored file this disk lacks (or holds another copy of) to it."""
    mirror = mirror_for(area)
    if area == "memory":
        from flocks.memory.sync.mirror import reconcile_all

        stats = await reconcile_all(mirror.root)
        return len(stats.pulled) if stats is not None else 0
    return await mirror.restore_tree("", trust_size=area in WRITE_ONCE_AREAS)


def summarize(states: Iterable[FileState]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for state in states:
        counts[state.state] = counts.get(state.state, 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "AREAS",
    "DIFFERS",
    "FileState",
    "LINKED",
    "LOCAL_ONLY",
    "REMOVED_ELSEWHERE",
    "SHARED",
    "STORED_NOW",
    "STORED_ONLY",
    "TOO_LARGE",
    "backfill_area",
    "check_area",
    "mirror_for",
    "restore_area",
    "summarize",
]
