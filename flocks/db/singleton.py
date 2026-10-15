"""
"Only one instance does this" across every node sharing the database.

A :class:`ClusterSingleton` is a PostgreSQL session-level advisory lock held on
a connection of its own.  The lock lives exactly as long as that connection,
so a holder that crashes, is killed or loses its network releases it without
anyone having to notice -- and the next instance that asks gets it.  Keys are
scoped to the current database and schema, so two deployments (or two test
schemas) on one server never contend.

A holder must keep checking that it still holds the lock (:meth:`check`):
when the connection drops, the lock is already gone and another instance may
have taken over, so the work it guards has to stop.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from flocks.utils.log import Log

log = Log.create(service="db.singleton")

_KEY_SQL = "hashtext(current_database() || '/' || current_schema())"


class ClusterSingleton:
    """A named, cluster-wide exclusive lease."""

    def __init__(self, name: str, *, check_timeout_s: float = 5.0) -> None:
        self.name = name
        self._check_timeout_s = check_timeout_s
        self._connection: Any = None

    @property
    def held(self) -> bool:
        return self._connection is not None and not self._connection.is_closed()

    async def try_acquire(self) -> bool:
        """Take the lease if nobody holds it.  Never waits for a holder."""
        if self.held:
            return True
        from flocks.db import registry
        from flocks.runtime_state.postgres import import_asyncpg, validate_dsn_socket_path

        dsn = registry.current().dsn
        validate_dsn_socket_path(dsn)
        connection = await import_asyncpg().connect(dsn, timeout=30, command_timeout=30)
        try:
            acquired = await connection.fetchval(
                f"SELECT pg_try_advisory_lock({_KEY_SQL}, hashtext($1))", self.name,
            )
        except BaseException:
            await self._close(connection)
            raise
        if not acquired:
            await self._close(connection)
            return False
        self._connection = connection
        return True

    async def check(self) -> bool:
        """Whether the lease is still held; drops it when the connection is gone."""
        if not self.held:
            return False
        try:
            await asyncio.wait_for(self._connection.fetchval("SELECT 1"), timeout=self._check_timeout_s)
            return True
        except Exception as exc:
            log.warning("db.singleton.lost", {"name": self.name, "error_type": type(exc).__name__})
            await self.release()
            return False

    async def release(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await self._close(connection)

    @staticmethod
    async def _close(connection: Any) -> None:
        try:
            await asyncio.wait_for(connection.close(), timeout=5)
        except Exception:
            connection.terminate()


async def wait_for_singleton(
    singleton: ClusterSingleton,
    *,
    retry_s: float,
    stop: asyncio.Event,
) -> Optional[bool]:
    """Try to take *singleton* every *retry_s* seconds until it is held or *stop* is set.

    Returns ``True`` once held, ``None`` when stopped first.
    """
    while not stop.is_set():
        try:
            if await singleton.try_acquire():
                return True
        except Exception as exc:
            log.warning("db.singleton.acquire_failed", {"name": singleton.name, "error_type": type(exc).__name__})
        try:
            await asyncio.wait_for(stop.wait(), timeout=retry_s)
        except asyncio.TimeoutError:
            pass
    return None


@asynccontextmanager
async def cluster_lock(name: str, *, wait_s: float = 0.0, retry_s: float = 0.2) -> AsyncIterator[bool]:
    """Hold a cluster-wide lock around a short piece of work.

    Yields ``True`` once held -- waiting up to *wait_s* for another holder --
    or ``False`` when it could not be had.  Taken on a pooled connection and
    given back before the connection is: for work measured in seconds, where
    a dedicated connection per call (:class:`ClusterSingleton`) would cost
    more than the work.
    """
    import time

    from flocks.db import registry
    from flocks.db.connect import pool_for

    pool = await pool_for(registry.current().dsn)
    async with pool.acquire() as connection:
        deadline = time.monotonic() + max(wait_s, 0.0)
        held = False
        while True:
            held = bool(await connection.fetchval(f"SELECT pg_try_advisory_lock({_KEY_SQL}, hashtext($1))", name))
            if held or time.monotonic() >= deadline:
                break
            await asyncio.sleep(retry_s)
        try:
            yield held
        finally:
            if held:
                try:
                    await asyncio.shield(
                        connection.fetchval(f"SELECT pg_advisory_unlock({_KEY_SQL}, hashtext($1))", name)
                    )
                except BaseException:
                    # A connection that may still hold the lock never goes
                    # back to the pool: closing it is what releases the lock.
                    connection.terminate()
                    raise


__all__ = ["ClusterSingleton", "cluster_lock", "wait_for_singleton"]
