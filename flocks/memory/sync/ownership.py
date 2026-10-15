"""Persist local-tree ownership without treating an incomplete tree as deletion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from flocks.db.retry import retry_deadlock_once
from flocks.storage import Storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_index_roots (
    root_id TEXT PRIMARY KEY, generation BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_index_owners (
    scope TEXT NOT NULL, scope_id TEXT NOT NULL, path TEXT NOT NULL,
    root_id TEXT NOT NULL, hash TEXT NOT NULL,
    mtime DOUBLE PRECISION NOT NULL, size BIGINT NOT NULL,
    PRIMARY KEY (scope, scope_id, path, root_id)
);
CREATE INDEX IF NOT EXISTS memory_index_owners_root ON memory_index_owners(root_id);
"""


class MemoryOwnershipConflict(RuntimeError):
    """Another tree, or unverified legacy data, owns the logical path."""


def _root_identity(root: Path) -> str | None:
    if not root.is_dir():
        # A missing mount/root is never proof that its files were deleted.
        return None
    marker = root / ".flocks-index-root"
    if not marker.exists():
        # Link a fully written private candidate atomically. A second worker
        # never reads a zero-length marker while the winner is writing it.
        with tempfile.NamedTemporaryFile(prefix=".flocks-index-", dir=root) as candidate:
            candidate.write(str(uuid4()).encode("ascii"))
            candidate.flush()
            os.fsync(candidate.fileno())
            try:
                os.link(candidate.name, marker)
            except FileExistsError:
                pass
    return str(UUID(marker.read_bytes().decode("ascii").strip()))


#: The one owner every instance indexes under once the memory notes are shared
#: through the database (``flocks.memory.sync.mirror``): their local trees are
#: copies of one tree, not separate sources.
SHARED_ROOT_ID = "shared:memory"


@dataclass(frozen=True)
class MemoryIndexOwnership:
    root_id: str
    generation: int
    root: Path

    @property
    def shared(self) -> bool:
        return self.root_id == SHARED_ROOT_ID

    @classmethod
    async def begin(cls, root: Path, *, shared: bool = False) -> MemoryIndexOwnership | None:
        """Start a scan of *root*.

        *shared*: index as the one shared tree.  Ownership rows of the
        per-instance roots used before move to it (each path keeps the hash
        its index has), so a path no longer needs every instance's copy to
        change before its index can.
        """
        if not root.is_dir():
            return None
        root_id = SHARED_ROOT_ID if shared else await asyncio.to_thread(_root_identity, root)
        if root_id is None:
            return None
        async with Storage.transaction() as db:
            # Concurrent first starts must not race PostgreSQL catalog DDL.
            await db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("memory:index-owner-schema",))
            await db.executescript(SCHEMA)
        if shared:
            async with Storage.transaction() as db:
                await db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("memory:index-owner-shared",))
                await db.execute(
                    "INSERT INTO memory_index_owners(scope,scope_id,path,root_id,hash,mtime,size) "
                    "SELECT f.scope,f.scope_id,f.path,?,f.hash,0,0 FROM memory_files f WHERE f.source='memory' "
                    "ON CONFLICT(scope,scope_id,path,root_id) DO NOTHING",
                    (SHARED_ROOT_ID,),
                )
                await db.execute("DELETE FROM memory_index_owners WHERE root_id <> ?", (SHARED_ROOT_ID,))
        async with Storage.transaction() as db:
            row = await db.fetch_one(
                "INSERT INTO memory_index_roots(root_id,generation) VALUES (?,1) "
                "ON CONFLICT(root_id) DO UPDATE SET generation=memory_index_roots.generation+1 "
                "RETURNING generation", (root_id,),
            )
        return cls(root_id, row[0], root)

    async def _check_generation(self, db) -> None:
        row = await db.fetch_one(
            "SELECT generation FROM memory_index_roots WHERE root_id=? FOR SHARE", (self.root_id,),
        )
        if row is None or row[0] != self.generation:
            raise MemoryOwnershipConflict("Memory sync was superseded by a newer scan of this local root")

    async def claim(self, db, entry: dict) -> None:
        """Called inside replacement's transaction and logical-file lock."""
        key = (entry["scope"], entry["scope_id"], entry["path"])
        # One indexed read for generation, canonical hash and ownership. The
        # root lock fences a newer scan until replacement commits or rolls back.
        state = await db.fetch_one(
            """WITH entry AS (
                 SELECT ?::text AS scope,?::text AS scope_id,?::text AS path,
                        ?::text AS root_id,?::text AS desired_hash
               ), root AS (
                 SELECT r.generation FROM memory_index_roots r,entry e
                 WHERE r.root_id=e.root_id FOR SHARE OF r
               ), owners AS (
                 SELECT o.root_id,o.hash FROM memory_index_owners o
                 JOIN entry e USING(scope,scope_id,path)
               )
               SELECT root.generation,f.hash,
                      (SELECT o.hash FROM owners o WHERE o.root_id=e.root_id),
                      EXISTS(SELECT 1 FROM owners o WHERE o.root_id<>e.root_id),
                      EXISTS(SELECT 1 FROM owners o WHERE o.root_id<>e.root_id AND o.hash<>e.desired_hash)
               FROM entry e CROSS JOIN root
               LEFT JOIN memory_files f USING(scope,scope_id,path)""",
            (*key, self.root_id, entry["hash"]),
        )
        if state is None or state[0] != self.generation:
            raise MemoryOwnershipConflict("Memory sync was superseded by a newer scan of this local root")
        _, current_hash, own_hash, foreign, conflicting_foreign = state
        if current_hash is not None and current_hash != entry["hash"]:
            if foreign or own_hash != current_hash:
                raise MemoryOwnershipConflict(
                    f"Memory path {entry['path']!r} has different content in another root or an "
                    "unverified legacy index; reconcile the source or use a distinct logical path"
                )
        if current_hash is None and conflicting_foreign:
            raise MemoryOwnershipConflict(f"Memory path {entry['path']!r} has a conflicting root owner")
        await db.execute(
            "INSERT INTO memory_index_owners(scope,scope_id,path,root_id,hash,mtime,size) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(scope,scope_id,path,root_id) DO UPDATE "
            "SET hash=EXCLUDED.hash,mtime=EXCLUDED.mtime,size=EXCLUDED.size",
            (*key, self.root_id, entry["hash"], entry["mtime"], entry["size"]),
        )

    async def clean(self, current_files: list[tuple[str, str, str]]) -> int:
        # Recheck after scanning: an unmounted/replaced tree is not a complete
        # empty scan. Do not create a replacement identity during this check.
        def same_root():
            if self.shared:
                # The shared tree's caller settled this copy with the store
                # before scanning; a root that went away is not a complete scan.
                return self.root.is_dir()
            try:
                return str(UUID((self.root / ".flocks-index-root").read_bytes().decode("ascii").strip())) == self.root_id
            except FileNotFoundError:
                return False

        if not await asyncio.to_thread(same_root):
            return 0
        return await retry_deadlock_once(lambda: self._clean_once(set(current_files)), name="memory.clean_index")

    async def _clean_once(self, current: set[tuple[str, str, str]]) -> int:
        async with Storage.transaction() as db:
            await self._check_generation(db)
            owned = await db.fetch_all(
                "SELECT scope,scope_id,path FROM memory_index_owners WHERE root_id=?", (self.root_id,),
            )
            deleted = [key for key in owned if key not in current]
            removed = 0
            for offset in range(0, len(deleted), 128):
                batch = deleted[offset:offset + 128]
                # Match replacement's locks, in a stable order; all subsequent
                # deletes are scoped to these keys, this owner and its hash.
                await db.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(lock_key)) FROM "
                    "(SELECT unnest(?::text[]) AS lock_key ORDER BY lock_key) AS locks",
                    ([f"memory:{scope}:{scope_id}:{path}" for scope, scope_id, path in batch],),
                )
                targets = json.dumps([dict(zip(("scope", "scope_id", "path"), key)) for key in batch])
                candidates = """
                    SELECT f.scope,f.scope_id,f.path FROM memory_files f
                    JOIN memory_index_owners o USING(scope,scope_id,path)
                    JOIN jsonb_to_recordset(?::jsonb) AS k(scope TEXT,scope_id TEXT,path TEXT)
                      USING(scope,scope_id,path)
                    WHERE o.root_id=? AND o.hash=f.hash AND f.source='memory'
                      AND NOT EXISTS (SELECT 1 FROM memory_index_owners other
                        WHERE other.scope=f.scope AND other.scope_id=f.scope_id AND other.path=f.path
                          AND other.root_id<>o.root_id)
                """
                for table in ("memory_fts", "memory_chunks", "memory_files"):
                    result = await db.execute(
                        f"DELETE FROM {table} d USING ({candidates}) c WHERE "
                        "d.scope=c.scope AND d.scope_id=c.scope_id AND d.path=c.path AND d.source='memory'",
                        (targets, self.root_id),
                    )
                    if table == "memory_files":
                        removed += result.rowcount
                await db.execute(
                    "DELETE FROM memory_index_owners o USING "
                    "jsonb_to_recordset(?::jsonb) AS k(scope TEXT,scope_id TEXT,path TEXT) "
                    "WHERE o.root_id=? AND o.scope=k.scope AND o.scope_id=k.scope_id AND o.path=k.path",
                    (targets, self.root_id),
                )
        return removed
