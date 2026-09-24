"""Pooled PostgreSQL handles, keyed by DSN, process and event loop.

A handle borrows a connection per operation or explicit transaction.
Legacy path arguments are accepted for extension API compatibility only."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from flocks.db import registry
from flocks.db.registry import DatabaseConfigurationError
from flocks.db.store import Transaction

_pools: dict[tuple[str, int, Any], Any] = {}
_locks: dict[tuple[str, int, Any], asyncio.Lock] = {}
_lifetimes: dict[tuple[str, int, Any], Any] = {}


async def _pool_lifetime(key, pool):
    """Let asyncio.run/Runner close its pool before closing the owning loop.

    The loop tracks this async generator after its first iteration. Server
    shutdown closes it explicitly; short-lived CLI loops close it through
    shutdown_asyncgens, including when a command raises or is cancelled.
    """
    try:
        yield
    finally:
        try:
            await asyncio.wait_for(pool.close(), timeout=30)
        except asyncio.TimeoutError:
            pool.terminate()
        finally:
            if _pools.get(key) is pool:
                _pools.pop(key, None)
                _locks.pop(key, None)
                _lifetimes.pop(key, None)


def _key(dsn: str) -> tuple[str, int, Any]:
    return (dsn, os.getpid(), asyncio.get_running_loop())


async def pool_for(dsn: str, *, min_size: int = 1, max_size: int = 8):
    """The pool for *dsn* on this loop, created once and reused."""
    from flocks.runtime_state.postgres import import_asyncpg, validate_dsn_socket_path

    key = _key(dsn)
    existing = _pools.get(key)
    if existing is not None and not existing.is_closing():
        return existing

    # Creating the lock is synchronous, so two coroutines reaching this line
    # cannot interleave between the lookup and the insert.
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock

    async with lock:
        existing = _pools.get(key)
        if existing is not None and not existing.is_closing():
            return existing
        asyncpg = import_asyncpg()
        validate_dsn_socket_path(dsn)
        pool = await asyncpg.create_pool(
            dsn, min_size=min_size, max_size=max_size,
            timeout=30, command_timeout=60,
        )
        _pools[key] = pool
        lifetime = _pool_lifetime(key, pool)
        _lifetimes[key] = lifetime
        await anext(lifetime)
        return pool


class _PooledPostgresHandle:
    """Process-lived facade; a connection is borrowed only while it is used.

    One statement is one autocommitted operation. Related statements must use
    ``transaction()``: its scoped handle pins a connection until commit or
    rollback, including when the caller is cancelled.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._closed = False

    @property
    def dialect(self):
        from flocks.db.dialect import PostgresDialect
        return PostgresDialect()

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, factory) -> None:
        pass

    async def _call(self, method, *args):
        from flocks.db.backends.postgres import PostgresTransaction
        if self._closed:
            raise RuntimeError("database handle is closed")
        pool = await pool_for(self._dsn)
        async with pool.acquire(timeout=30) as connection:
            return await getattr(PostgresTransaction(connection), method)(*args)

    def execute(self, sql: str, params=()):
        from flocks.db.result import Execution
        if sql.strip().split(None, 1)[0].upper() in {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT"}:
            raise ValueError("use handle.transaction() for a PostgreSQL transaction")
        return Execution(self._call("execute", sql, params))

    async def execute_many(self, sql, rows):
        return await self._call("execute_many", sql, rows)

    async def fetch_all(self, sql, params=()):
        return await self._call("fetch_all", sql, params)

    async def fetch_one(self, sql, params=()):
        return await self._call("fetch_one", sql, params)

    async def fetch_all_mappings(self, sql, params=()):
        return await self._call("fetch_all_mappings", sql, params)

    async def fetch_one_mapping(self, sql, params=()):
        return await self._call("fetch_one_mapping", sql, params)

    async def executescript(self, script):
        async with self.transaction() as transaction:
            await transaction.executescript(script)

    @asynccontextmanager
    async def transaction(self, *, immediate=False):
        from flocks.db.backends.postgres import transaction_on
        if self._closed:
            raise RuntimeError("database handle is closed")
        pool = await pool_for(self._dsn)
        async with pool.acquire(timeout=30) as connection:
            async with transaction_on(connection) as transaction:
                yield transaction

    async def commit(self):
        # Single operations already committed; a transaction block owns commit.
        pass

    async def rollback(self):
        # No transaction is held outside an explicit transaction block.
        pass

    async def close(self) -> None:
        self._closed = True


async def open_transaction(
    *,
    sqlite_path: Optional[Path] = None,
    sqlite_timeout: Optional[float] = None,
    configure: Optional[Callable[[Any], Awaitable[None]]] = None,
    text_factory: Optional[Callable[[bytes], str]] = None,
    daemon_worker: bool = False,
) -> Transaction:
    """Return a PostgreSQL handle. Legacy file arguments are ignored.

    The compatibility keywords keep existing extensions source-compatible;
    none of them can open or create a SQLite database.
    """
    settings = registry.current()

    if settings.backend == "postgres":
        return _PooledPostgresHandle(settings.dsn or "")

    raise DatabaseConfigurationError(
        f"backend {settings.backend!r} has a dialect but no connection support yet"
    )


async def close_pools() -> None:
    """Close every pool this module opened. For shutdown and for tests."""
    for key, lifetime in list(_lifetimes.items()):
        if key[1] != os.getpid() or key[2] is not asyncio.get_running_loop():
            continue
        await lifetime.aclose()


__all__ = ["close_pools", "open_transaction", "pool_for"]
