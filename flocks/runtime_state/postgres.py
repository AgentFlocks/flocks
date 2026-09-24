"""
PostgreSQL implementation of shared runtime state.

Two instances point at one database and see the same sessions, conversations
and background work. The parts that matter for correctness under two writers
are done in SQL rather than in the application: ownership is claimed with a
conditional UPDATE, so two instances racing for the same task produce one
winner and one clear loser instead of both proceeding.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Tuple

from flocks.runtime_state.store import ExecutionOwnership, StateEntry
from flocks.utils.log import Log

log = Log.create(service="runtime_state.postgres")

SCHEMA_STATEMENTS: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS runtime_state (
        key         TEXT        PRIMARY KEY,
        value       JSONB       NOT NULL,
        value_type  TEXT        NOT NULL DEFAULT 'json',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Prefix scans are how sessions and messages are listed, and a plain
    # b-tree on text does not serve LIKE 'prefix%' unless it uses the pattern
    # operator class.
    """
    CREATE INDEX IF NOT EXISTS runtime_state_key_prefix
        ON runtime_state (key text_pattern_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS runtime_state_session_id
        ON runtime_state ((value->>'id')) WHERE key LIKE 'session:%'
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_executions (
        execution_id     TEXT        PRIMARY KEY,
        owner_node_id    TEXT,
        lease_expires_at TIMESTAMPTZ,
        owner_generation INTEGER     NOT NULL DEFAULT 0,
        status           TEXT        NOT NULL DEFAULT 'running',
        started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS runtime_executions_owner
        ON runtime_executions (status, owner_node_id, lease_expires_at)
    """,
]


class PostgresRuntimeStateStore:
    """Shared state backed by PostgreSQL."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None

    async def _ensure_pool(self):
        from flocks.db.connect import pool_for
        self._pool = await pool_for(self._dsn, min_size=self._min_size, max_size=self._max_size)
        return self._pool

    async def initialize(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "flocks.runtime.schema")
                await conn.execute(";\n".join(SCHEMA_STATEMENTS))
        log.info("runtime_state.postgres.ready", {})

    async def close(self) -> None:
        # The application owns the shared pool; closing one store must not
        # interrupt the task, workflow or knowledge stores using that pool.
        self._pool = None

    # -- documents ---------------------------------------------------------

    async def set(self, key: str, value: Any, value_type: str = "json") -> None:
        await self.set_many([(key, value, value_type)])

    async def set_many(self, entries: Sequence[Tuple[str, Any, str]]) -> None:
        if not entries:
            return
        pool = await self._ensure_pool()
        rows = [(key, json.dumps(value), value_type) for key, value, value_type in entries]
        async with pool.acquire() as conn:
            # One transaction: a turn's message and its parts land together, so
            # the other instance never reads a message whose content is absent.
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO runtime_state (key, value, value_type, updated_at)
                    VALUES ($1, $2::jsonb, $3, now())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value,
                            value_type = EXCLUDED.value_type,
                            updated_at = now()
                    """,
                    rows,
                )

    async def get(self, key: str) -> Optional[Any]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            raw = await conn.fetchval("SELECT value FROM runtime_state WHERE key = $1", key)
        if raw is None:
            return None
        return json.loads(raw)

    async def get_many(self, keys: Sequence[str]) -> dict[str, Any]:
        if not keys:
            return {}
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT key,value FROM runtime_state WHERE key=ANY($1::text[])", list(keys))
        return {row["key"]: json.loads(row["value"]) for row in rows}

    async def import_legacy_many(self, entries: Sequence[Tuple[str, Any, str]]) -> int:
        """Add legacy documents atomically, refusing different existing values."""
        if not entries:
            return 0
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext('flocks.offline.migration'))")
                params = [item for key, value, kind in entries for item in (key, json.dumps(value), kind)]
                values = ",".join(f"(${i*3+1},${i*3+2}::jsonb,${i*3+3})" for i in range(len(entries)))
                inserted = await conn.fetch(
                    "INSERT INTO runtime_state (key,value,value_type) VALUES " + values + " ON CONFLICT DO NOTHING RETURNING key", *params,
                )
                actual = await conn.fetch("SELECT key,value,value_type FROM runtime_state WHERE key=ANY($1::text[])", [row[0] for row in entries])
                expected = {key: (value, kind) for key, value, kind in entries}
                if len(actual) != len(expected) or any((json.loads(row["value"]), row["value_type"]) != expected[row["key"]] for row in actual):
                    raise ValueError("Existing document differs; migration transaction rolled back")
        return len(inserted)

    async def delete(self, key: str) -> bool:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM runtime_state WHERE key = $1", key)
        return result.endswith(" 1")

    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if prefix:
                rows = await conn.fetch(
                    "SELECT key FROM runtime_state WHERE key LIKE $1 ORDER BY key",
                    _like_prefix(prefix),
                )
            else:
                rows = await conn.fetch("SELECT key FROM runtime_state ORDER BY key")
        return [row["key"] for row in rows]

    async def list_entries(self, prefix: Optional[str] = None) -> List[StateEntry]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if prefix:
                rows = await conn.fetch(
                    """
                    SELECT key, value, value_type, created_at, updated_at
                    FROM runtime_state WHERE key LIKE $1 ORDER BY key
                    """,
                    _like_prefix(prefix),
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT key, value, value_type, created_at, updated_at
                    FROM runtime_state ORDER BY key
                    """
                )
        return [
            StateEntry(
                key=row["key"],
                value=json.loads(row["value"]),
                value_type=row["value_type"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    # -- work ownership ----------------------------------------------------

    async def register_execution(self, execution_id: str, *, status: str = "running") -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO runtime_executions (execution_id, status)
                VALUES ($1, $2)
                ON CONFLICT (execution_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now()
                """,
                execution_id, status,
            )

    async def claim_execution(
        self, execution_id: str, *, node_id: str, lease_seconds: int
    ) -> Optional[ExecutionOwnership]:
        pool = await self._ensure_pool()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=max(1, lease_seconds))
        async with pool.acquire() as conn:
            # Conditional update rather than read-then-write: two instances
            # claiming at the same moment both see "free" in a read, but only
            # one satisfies the WHERE.
            row = await conn.fetchrow(
                """
                UPDATE runtime_executions
                SET owner_node_id = $2,
                    lease_expires_at = $3,
                    owner_generation = owner_generation + 1,
                    updated_at = now()
                WHERE execution_id = $1
                  AND (owner_node_id IS NULL
                       OR owner_node_id = $2
                       OR lease_expires_at IS NULL
                       OR lease_expires_at < $4)
                RETURNING owner_node_id, lease_expires_at, owner_generation
                """,
                execution_id, node_id, expires, now,
            )
        if row is None:
            return None
        return ExecutionOwnership(
            execution_id=execution_id,
            node_id=row["owner_node_id"],
            lease_expires_at=row["lease_expires_at"],
            generation=row["owner_generation"],
        )

    async def renew_execution_lease(
        self, execution_id: str, *, node_id: str, generation: int, lease_seconds: int
    ) -> bool:
        pool = await self._ensure_pool()
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(1, lease_seconds))
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE runtime_executions
                SET lease_expires_at = $2, updated_at = now()
                WHERE execution_id = $1 AND owner_node_id = $3 AND owner_generation = $4
                """,
                execution_id, expires, node_id, generation,
            )
        return result.endswith(" 1")

    async def release_execution_lease(self, execution_id: str, *, node_id: str) -> bool:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE runtime_executions
                SET owner_node_id = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE execution_id = $1 AND owner_node_id = $2
                """,
                execution_id, node_id,
            )
        return result.endswith(" 1")

    async def list_reclaimable_executions(
        self, *, node_id: str, stale_before: datetime
    ) -> List[ExecutionOwnership]:
        pool = await self._ensure_pool()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT execution_id, owner_node_id, lease_expires_at, owner_generation
                FROM runtime_executions
                WHERE status = 'running'
                  AND (
                        owner_node_id = $1
                     OR (owner_node_id IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at < $2)
                     OR (owner_node_id IS NULL AND started_at < $3)
                  )
                """,
                node_id, now, stale_before,
            )
        return [
            ExecutionOwnership(
                execution_id=row["execution_id"],
                node_id=row["owner_node_id"],
                lease_expires_at=row["lease_expires_at"],
                generation=row["owner_generation"],
            )
            for row in rows
        ]


# sun_path is 104 bytes on macOS/BSD and 108 on Linux (both including the NUL);
# libpq appends "/.s.PGSQL.<port>" to the directory given as ``host``.
MAX_UNIX_SOCKET_DIR_BYTES = 103 - len("/.s.PGSQL.65535")


def import_asyncpg():
    """Import the optional PostgreSQL driver with an actionable error when absent."""
    try:
        import asyncpg
    except ImportError as exc:
        raise ImportError(
            "asyncpg is required for the PostgreSQL stores but is not installed; "
            "install the 'postgres' extra (uv sync --extra postgres)."
        ) from exc
    return asyncpg


def validate_dsn_socket_path(dsn: str) -> None:
    """Reject a Unix-socket directory the OS cannot bind (``AF_UNIX path too long``).

    asyncpg only surfaces this as a bare ``OSError`` from the socket layer, which
    reads like a network failure; the limit is a configuration constraint and is
    reported as one, before any connection attempt.
    """
    from urllib.parse import parse_qs, unquote, urlsplit

    try:
        parts = urlsplit(dsn)
        hosts = parse_qs(parts.query).get("host", [])
        # libpq also accepts the directory percent-encoded in the netloc.
        netloc_host = unquote(parts.hostname or "")
    except ValueError:
        return
    if netloc_host.startswith("/"):
        hosts.append(netloc_host)
    for host in hosts:
        if host.startswith("/") and len(host.encode("utf-8")) > MAX_UNIX_SOCKET_DIR_BYTES:
            raise ValueError(
                f"PostgreSQL Unix socket directory is too long for AF_UNIX "
                f"({len(host.encode('utf-8'))} bytes, limit {MAX_UNIX_SOCKET_DIR_BYTES}; "
                f"sun_path is 104 bytes on macOS/108 on Linux): {host}"
            )


def _is_duplicate_object(exc: Exception) -> bool:
    """Whether an error is a harmless "object already exists" from a DDL race."""
    sqlstate = getattr(exc, "sqlstate", None)
    # 42P07 duplicate_table, 42710 duplicate_object, 23505 unique_violation
    # (raised when two backends insert the same pg_type/pg_class row at once).
    return sqlstate in {"42P07", "42710", "23505"}


def _like_prefix(prefix: str) -> str:
    """Escape LIKE wildcards so a key containing % or _ is matched literally."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


__all__ = ["PostgresRuntimeStateStore", "SCHEMA_STATEMENTS", "import_asyncpg", "validate_dsn_socket_path"]
