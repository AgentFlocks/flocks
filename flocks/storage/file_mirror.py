"""Files kept in PostgreSQL, with the local copy as a cache.

Session uploads and memory notes used to exist only on the disk of the
instance that wrote them: a second instance on the same database -- or the
standby taking over from a failed one -- could not read them.  Each such file
is also kept in ``file_mirror``, under an *area* (``uploads``, ``memory``) and
its path relative to the area's root.  An instance that is missing a file, or
holds a different copy, writes the stored one to its own disk before reading
it.

Writes can be conditional on the stored copy still being the one the writer
last saw (``expected``), so two instances updating the same file do not
silently overwrite each other: the second one is told, and decides.

A removed file leaves a *tombstone* (the row stays, its content goes): an
instance whose cache still holds the file can tell "removed elsewhere" from
"new here", and does not bring it back.

The local side is only ever written inside the area's root: a path whose
directories include a symbolic link is left alone, in both directions.
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

_SCHEMA_LOCK = "flocks.file_mirror.schema"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_mirror (
    area       TEXT        NOT NULL,
    path       TEXT        NOT NULL,
    content    BYTEA       NOT NULL,
    sha256     TEXT        NOT NULL,
    size       BIGINT      NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted    BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (area, path)
);
ALTER TABLE file_mirror ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE
"""
#: Expected value meaning "there must be no stored copy yet" (a tombstone counts as none).
ABSENT = ""
#: Expected value meaning "nothing may ever have been stored here": not even a
#: tombstone, so a file removed elsewhere is not brought back.
NEVER_STORED = "\x00never-stored"

#: Files larger than this stay on the instance that has them
#: (FLOCKS_FILE_MIRROR_MAX_BYTES): the database is not a store for anything
#: of any size.  Uploads and channel media are capped well below it upstream.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


def max_bytes() -> int:
    raw = os.environ.get("FLOCKS_FILE_MIRROR_MAX_BYTES", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


class FileTooLargeToShare(ValueError):
    """The file is over the size kept in the database: it stays on this instance only."""

_schema_ready: set[str] = set()
_schema_locks: dict[int, asyncio.Lock] = {}


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


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FileMirror:
    """One area's files: stored in PostgreSQL, cached under *root*."""

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

        Such a path is not this cache's to write, delete or read through: it
        may lead out of the root, or onto someone's own arrangement.
        """
        current = self.root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    # -- database -------------------------------------------------------------

    @staticmethod
    async def _pool():
        from flocks.db import registry
        from flocks.db.connect import pool_for

        dsn = registry.current().dsn
        pool = await pool_for(dsn)
        if dsn not in _schema_ready:
            loop_lock = _schema_locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
            async with loop_lock:
                if dsn not in _schema_ready:
                    from flocks.db.idempotent_ddl import schema_script_to_run

                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _SCHEMA_LOCK)
                            script = await schema_script_to_run(conn, _SCHEMA)
                            if script is not None:
                                await conn.execute(script)
                    _schema_ready.add(dsn)
        return pool

    async def listing(self, prefix: str = "", *, tombstones: bool = False) -> dict[str, StoredFile]:
        """What is stored under *prefix* (a directory ending in ``/``, or a name prefix).

        Removed files are left out unless *tombstones*.
        """
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT path, sha256, size, deleted, extract(epoch FROM updated_at) AS updated "
                "FROM file_mirror WHERE area = $1 AND path LIKE $2" + ("" if tombstones else " AND NOT deleted"),
                self.area, _escape_like(prefix) + "%",
            )
        return {
            row["path"]: StoredFile(
                row["path"], row["sha256"], int(row["size"]), bool(row["deleted"]), float(row["updated"] or 0),
            )
            for row in rows
        }

    async def fetch(self, relative: str) -> Optional[bytes]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            content = await conn.fetchval(
                "SELECT content FROM file_mirror WHERE area = $1 AND path = $2 AND NOT deleted", self.area, relative,
            )
        return bytes(content) if content is not None else None

    async def put_bytes(self, relative: str, data: bytes, *, expected: Optional[str] = None) -> bool:
        """Store *data* at *relative*.

        With *expected*, only if the stored copy still has that hash
        (:data:`ABSENT`: only if nothing is stored, a tombstone included;
        :data:`NEVER_STORED`: only if there is no row at all, tombstone or not).
        Returns whether it was written.
        """
        self.local(relative)  # validates the path
        if len(data) > max_bytes():
            raise FileTooLargeToShare(f"{self.area}/{relative} is {len(data)} bytes; at most {max_bytes()} are shared")
        pool = await self._pool()
        digest = sha256_of(data)
        upsert = (
            "INSERT INTO file_mirror (area, path, content, sha256, size, deleted) VALUES ($1, $2, $3, $4, $5, FALSE) "
            "ON CONFLICT (area, path) DO UPDATE SET content = EXCLUDED.content, sha256 = EXCLUDED.sha256, "
            "size = EXCLUDED.size, deleted = FALSE, updated_at = clock_timestamp()"
        )
        async with pool.acquire() as conn:
            if expected is None:
                await conn.execute(upsert, self.area, relative, data, digest, len(data))
                return True
            if expected == NEVER_STORED:
                written = await conn.fetchval(
                    "INSERT INTO file_mirror (area, path, content, sha256, size, deleted) "
                    "VALUES ($1, $2, $3, $4, $5, FALSE) ON CONFLICT (area, path) DO NOTHING RETURNING 1",
                    self.area, relative, data, digest, len(data),
                )
                return written is not None
            if expected == ABSENT:
                written = await conn.fetchval(
                    upsert + " WHERE file_mirror.deleted RETURNING 1",
                    self.area, relative, data, digest, len(data),
                )
                return written is not None
            written = await conn.fetchval(
                "UPDATE file_mirror SET content = $3, sha256 = $4, size = $5, updated_at = clock_timestamp() "
                "WHERE area = $1 AND path = $2 AND sha256 = $6 AND NOT deleted RETURNING 1",
                self.area, relative, data, digest, len(data), expected,
            )
            return written is not None

    async def put(self, path: Path, *, expected: Optional[str] = None) -> bool:
        """Store the local file at *path*.  Raises FileTooLargeToShare over the limit."""
        relative = self.relative(path)
        size = await asyncio.to_thread(lambda: Path(path).stat().st_size)
        if size > max_bytes():
            raise FileTooLargeToShare(f"{self.area}/{relative} is {size} bytes; at most {max_bytes()} are shared")
        data = await asyncio.to_thread(Path(path).read_bytes)
        return await self.put_bytes(relative, data, expected=expected)

    async def forget(self, relative: str, *, expected: Optional[str] = None) -> bool:
        """Remove a stored file, leaving its tombstone."""
        pool = await self._pool()
        tombstone = (
            "UPDATE file_mirror SET deleted = TRUE, content = ''::bytea, sha256 = '', size = 0, "
            "updated_at = clock_timestamp() WHERE area = $1 AND path = $2 AND NOT deleted"
        )
        async with pool.acquire() as conn:
            if expected is None:
                result = await conn.execute(tombstone, self.area, relative)
            else:
                result = await conn.execute(tombstone + " AND sha256 = $3", self.area, relative, expected)
        return result.split()[-1] != "0"

    async def forget_tree(self, prefix: str) -> int:
        """Remove everything stored under the directory *prefix* (``a/b/``)."""
        if not prefix.endswith("/"):
            raise ValueError("A directory prefix ends with '/'")
        pool = await self._pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE file_mirror SET deleted = TRUE, content = ''::bytea, sha256 = '', size = 0, "
                "updated_at = clock_timestamp() WHERE area = $1 AND path LIKE $2 AND NOT deleted",
                self.area, _escape_like(prefix) + "%",
            )
        return int(result.split()[-1])

    async def move(self, source: Path, destination: Path) -> None:
        """The stored copy follows a local rename (*destination* already holds the file).

        A file over the size limit was never stored (``put`` keeps it on this
        instance only) and is not stored now either: it stays local, and
        whatever was stored under the old name goes.
        """
        old, new = self.relative(source), self.relative(destination)
        if old == new:
            return
        size = await asyncio.to_thread(lambda: Path(destination).stat().st_size)
        if size > max_bytes():
            await self.forget(old)
            return
        data = await asyncio.to_thread(Path(destination).read_bytes)
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO file_mirror (area, path, content, sha256, size, deleted) "
                    "VALUES ($1, $2, $3, $4, $5, FALSE) "
                    "ON CONFLICT (area, path) DO UPDATE SET content = EXCLUDED.content, sha256 = EXCLUDED.sha256, "
                    "size = EXCLUDED.size, deleted = FALSE, updated_at = clock_timestamp()",
                    self.area, new, data, sha256_of(data), len(data),
                )
                await conn.execute(
                    "UPDATE file_mirror SET deleted = TRUE, content = ''::bytea, sha256 = '', size = 0, "
                    "updated_at = clock_timestamp() WHERE area = $1 AND path = $2",
                    self.area, old,
                )

    # -- the local cache --------------------------------------------------------

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
        """Make the local copy at *path* match the stored one.

        Returns whether a stored copy exists.  *trust_size*: a local file of
        the stored size is taken to be the same file -- for areas whose files
        are written once and never changed, so a read does not hash them.
        """
        relative = self.relative(path)
        stored = (await self.listing(relative)).get(relative)
        if stored is None:
            return False
        if await self._matches(Path(path), stored, trust_size=trust_size):
            return True
        data = await self.fetch(relative)
        if data is None:
            return False
        try:
            await self.write_local(relative, data)
        except ValueError:
            return False
        return True

    async def restore_tree(self, prefix: str = "", *, trust_size: bool = False) -> int:
        """Restore every stored file under *prefix*; returns how many were written."""
        written = 0
        for relative, stored in (await self.listing(prefix)).items():
            target = self.local(relative)
            if await self._matches(target, stored, trust_size=trust_size):
                continue
            data = await self.fetch(relative)
            if data is None:
                continue
            try:
                await self.write_local(relative, data)
            except ValueError:
                continue  # behind a link: not this cache's to write
            written += 1
        return written

    async def is_removed(self, relative: str) -> bool:
        """Whether *relative* was stored and then removed (a tombstone)."""
        stored = (await self.listing(relative, tombstones=True)).get(relative)
        return stored is not None and stored.deleted

    @staticmethod
    async def _matches(path: Path, stored: StoredFile, *, trust_size: bool) -> bool:
        def check() -> bool:
            try:
                if not path.is_file() or path.is_symlink():
                    return False
                if path.stat().st_size != stored.size:
                    return False
                return trust_size or sha256_of(path.read_bytes()) == stored.sha256
            except OSError:
                return False

        return await asyncio.to_thread(check)


__all__ = ["ABSENT", "FileMirror", "StoredFile", "sha256_of"]
