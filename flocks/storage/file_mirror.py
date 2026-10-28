"""Session uploads, channel media and memory notes kept on the local disk.

Callers keep these files through a :class:`FileMirror` per *area*
(``uploads``, ``channel_media``, ``memory``): they store a file after writing
it, and ask for it back before reading one that is missing.  In one process
on one disk the local file is the only copy, so storing keeps nothing more
and there is never another copy to bring back.  A file a message records is
read where its URL says (:func:`readable_url`).

A database backend that lets several processes share the data replaces this
module (``flocks.db_backend``) with one that keeps each file in the database
as well.  The paths, names and return values here are the ones that module
keeps.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from flocks.utils.log import Log

log = Log.create(service="storage.file_mirror")

#: Expected value meaning "there must be no stored copy yet".
ABSENT = ""
#: Expected value meaning "nothing may ever have been stored here".
NEVER_STORED = "\x00never-stored"

#: Files larger than this are not kept by a backend that shares files
#: (FLOCKS_FILE_MIRROR_MAX_BYTES).
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


def max_bytes() -> int:
    raw = os.environ.get("FLOCKS_FILE_MIRROR_MAX_BYTES", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


class FileTooLargeToShare(ValueError):
    """The file is over the size a sharing backend keeps: it is kept on the local disk only."""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StoredFile:
    path: str
    sha256: str
    size: int
    #: A tombstone: the file was removed (when: ``updated_at``, epoch seconds).
    deleted: bool = False
    updated_at: float = 0.0


class FileMirror:
    """One area's files under *root*; this instance's disk is the only copy."""

    def __init__(self, area: str, root: Path) -> None:
        self.area = area
        self.root = Path(root)

    # -- paths ----------------------------------------------------------------

    def relative(self, path: Path) -> str:
        """*path* as stored: POSIX, relative to the root, never escaping it."""
        root = self.root.resolve(strict=False)
        target = Path(path).resolve(strict=False)
        if target == root or not target.is_relative_to(root):
            raise ValueError(f"{path} is outside the {self.area} root")
        return target.relative_to(root).as_posix()

    def local(self, relative: str) -> Path:
        parts = PurePosixPath(relative).parts
        if not parts or any(part in {"", ".", ".."} for part in parts) or PurePosixPath(relative).is_absolute():
            raise ValueError(f"Invalid stored path: {relative!r}")
        return self.root.joinpath(*parts)

    def is_linked(self, relative: str) -> bool:
        """Whether a directory on the way to *relative*, or the file itself, is a link.

        Such a path is not this area's to write, delete or read through: it
        may lead out of the root, or onto someone's own arrangement.
        """
        current = self.root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    # -- the stored copies: none beyond the local file --------------------------

    async def listing(self, prefix: str = "", *, tombstones: bool = False) -> dict[str, StoredFile]:
        """What is stored under *prefix* besides the local files: nothing."""
        return {}

    async def fetch(self, relative: str) -> Optional[bytes]:
        return None

    async def put_bytes(self, relative: str, data: bytes, *, expected: Optional[str] = None) -> bool:
        """Keep *data* at *relative*: the caller's local file already is the copy."""
        self.local(relative)  # validates the path
        return True

    async def put(self, path: Path, *, expected: Optional[str] = None) -> bool:
        """Keep the local file at *path*: it already is the copy."""
        self.relative(path)  # validates the path
        return True

    async def forget(self, relative: str, *, expected: Optional[str] = None) -> bool:
        """Nothing is stored apart from the local file, so there is nothing to forget."""
        return False

    async def forget_tree(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            raise ValueError("A directory prefix ends with '/'")
        return 0

    async def move(self, source: Path, destination: Path) -> None:
        """The copy is the local file, which the caller has already renamed."""
        self.relative(source)
        self.relative(destination)

    # -- the local files ---------------------------------------------------------

    @staticmethod
    def _write_local(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".mirror-", dir=target.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    async def write_local(self, relative: str, data: bytes) -> Path:
        target = self.local(relative)
        if await asyncio.to_thread(self.is_linked, relative):
            raise ValueError(f"{relative} goes through a link; it is not written")
        await asyncio.to_thread(self._write_local, target, data)
        return target

    async def restore(self, path: Path, *, trust_size: bool = False) -> bool:
        """Whether a stored copy exists to bring back: none does."""
        self.relative(path)
        return False

    async def restore_tree(self, prefix: str = "", *, trust_size: bool = False) -> int:
        return 0

    async def is_removed(self, relative: str) -> bool:
        return False


def readable_url(url: str, *, session_id: Optional[str] = None) -> str:
    """The file URL a message part (of *session_id*) records, as this process reads it.

    Here the URL itself: the file is where the URL says.  A backend that
    shares the files reads them from its own data directory instead.
    """
    return url


__all__ = ["ABSENT", "FileMirror", "StoredFile", "readable_url", "sha256_of"]
