"""Memory notes on the local disk.

Memory code settles a note after writing it (``reconcile_note``) and every
note around a dream or a bootstrap (``reconcile_quietly``).  In one process on
one disk the notes under the memory root are the only copy, so every note is
always settled and there is nothing to bring over.

A database backend that lets several processes share the data replaces this
module (``flocks.db_backend``) with one that keeps the notes in the database
too and reconciles the local copy with it.  The names and return values here
are the ones that module keeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from flocks.storage.file_mirror import FileMirror


@dataclass
class ReconcileStats:
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    deleted_remote: list[str] = field(default_factory=list)
    moved_aside: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)
    kept_copies: list[str] = field(default_factory=list)
    fresh: bool = False


def manifest_path(root: Path) -> Path:
    """Where a sharing backend records the last sync, beside the memory root."""
    return root.parent / f".{root.name}-mirror.json"


class MemoryNotShared(RuntimeError):
    """A memory note could not be settled with the stored notes.

    ``written`` says whether the note was written all the same (it is settled
    later).  The backend that keeps the stored notes words the message.
    """

    #: What a save through the API is told when the note was written but not settled.
    SAVED_UNSETTLED = "Saved; the stored notes did not take it yet."

    def __init__(self, path: str, *, written: bool) -> None:
        self.path, self.written = path, written
        super().__init__(
            f"{path} was saved; the stored notes did not take it yet" if written
            else f"{path}: the stored notes could not be read; nothing was written"
        )


async def reconcile(root: Path, *, only: Optional[Iterable[str]] = None) -> ReconcileStats:
    """Settle the notes under *root*: the local notes are the only ones."""
    return ReconcileStats()


async def reconcile_note(root: Path, note: Path, *, attempts: int = 1) -> bool:
    """Whether the note is settled: a note on the only disk always is.

    Raises ValueError for a path outside *root*, as the sharing backend does.
    """
    FileMirror("memory", root).relative(note)
    return True


async def reconcile_all(root: Path) -> Optional[ReconcileStats]:
    return ReconcileStats()


async def reconcile_quietly(root: Path) -> bool:
    return True


__all__ = [
    "MemoryNotShared",
    "ReconcileStats",
    "manifest_path",
    "reconcile",
    "reconcile_all",
    "reconcile_note",
    "reconcile_quietly",
]
