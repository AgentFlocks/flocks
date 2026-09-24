"""Bounded PostgreSQL transactions for synchronous plugins and adapters.

Call this API from a worker thread. Connections are borrowed from the shared
background loop's pool for one context manager and never shared by callers.
"""

from __future__ import annotations

import asyncio
import hashlib
import pickle
import re
import tempfile
import time
import weakref

from flocks.db import registry
from flocks.db.markers import rewrite_markers
from flocks.db.sync_store import _background_loop


def identity() -> str:
    return hashlib.sha256((registry.current().dsn or "").encode()).hexdigest()[:24]


class Row:
    """Record-compatible indexed/named access for a locally spooled result."""

    def __init__(self, names, values, positions):
        self._names, self._values, self._positions = names, values, positions

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._positions[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._names

    def values(self):
        return self._values

    def items(self):
        return zip(self._names, self._values)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class Query:
    # execute completes the SQL, even if a RETURNING result is never read.
    # Results spill to a private tempfile instead of retaining an unbounded
    # asyncpg result list or an unfinished write portal.
    MEMORY_LIMIT = 1024 * 1024
    FETCH_ROWS = 128

    def __init__(self, connection: "Connection", sql: str, params: tuple) -> None:
        self._result = tempfile.SpooledTemporaryFile(max_size=self.MEMORY_LIMIT)
        self._names = ()
        self._positions = {}
        try:
            deadline = time.monotonic() + connection.timeout
            cursor = connection._run(self._start(connection, sql, params))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("query exceeded its total execution timeout")
                rows = connection._run(cursor.fetch(self.FETCH_ROWS, timeout=remaining))
                if not rows:
                    break
                # This class is used on a caller's worker thread. Spooling
                # here keeps filesystem work outside the shared DB event loop
                # and prevents timeout cancellation racing a spool writer.
                self._append(rows)
            self._result.seek(0)
        except BaseException:
            self.close()
            raise
        connection._queries.add(self)

    async def _start(self, connection, sql, params):
        statement = await connection._connection.prepare(sql, timeout=connection.timeout)
        self._names = tuple(attribute.name for attribute in statement.get_attributes())
        self._positions = {name: position for position, name in enumerate(self._names)}
        return await statement.cursor(*params, timeout=connection.timeout)

    def _append(self, rows):
        for row in rows:
            pickle.dump(tuple(row), self._result, protocol=pickle.HIGHEST_PROTOCOL)

    def fetchone(self):
        if self._result.closed:
            raise RuntimeError("query result is closed")
        try:
            return Row(self._names, pickle.load(self._result), self._positions)
        except EOFError:
            return None

    def fetchall(self):
        return list(self)

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row

    def close(self):
        self._result.close()


class Connection:
    def __init__(self, *, timeout: float = 5.0, readonly: bool = False) -> None:
        self.timeout = min(30.0, max(0.05, float(timeout)))
        self.readonly = readonly
        self._pool = self._connection = self._transaction = None
        self._queries = weakref.WeakSet()

    def _run(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, _background_loop())
        try:
            return future.result(timeout=self.timeout + 1.0)
        except BaseException:
            future.cancel()
            raise

    def __enter__(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("synchronous database queries must run in a worker thread")

        async def acquire():
            from flocks.db.connect import pool_for
            self._pool = await pool_for(registry.current().dsn or "")
            connection = await self._pool.acquire(timeout=self.timeout)
            try:
                transaction = connection.transaction(readonly=self.readonly)
                await transaction.start()
                await connection.execute("SELECT set_config('statement_timeout', $1, true)", str(int(self.timeout * 1000)))
                self._connection, self._transaction = connection, transaction
            except BaseException:
                await self._pool.release(connection, timeout=self.timeout)
                raise
        self._run(acquire())
        return self

    def __exit__(self, error_type, error, traceback):
        async def release():
            if self._connection is None:
                return
            try:
                if error_type is None:
                    await self._transaction.commit()
                else:
                    await self._transaction.rollback()
            finally:
                await self._pool.release(self._connection, timeout=self.timeout)
                self._connection = self._transaction = None
        try:
            self._run(release())
        finally:
            for query in self._queries:
                query.close()
            self._queries.clear()

    def execute(self, sql: str, params=()):
        statement = rewrite_markers(sql, lambda position: f"${position}") if "?" in sql else sql
        if self._connection is None:
            raise RuntimeError("database connection is not open")
        # Permit leading comments and RETURNING across line breaks. WITH is
        # always executed eagerly: its body may contain data modifications.
        head = re.sub(r"\A(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", statement, flags=re.S)
        if head.upper().startswith(("SELECT", "WITH", "SHOW", "VALUES", "EXPLAIN")) or re.search(r"\bRETURNING\b", head, re.I):
            return Query(self, statement, tuple(params))
        status = self._run(self._connection.execute(statement, *tuple(params), timeout=self.timeout))
        return status

    def executemany(self, sql: str, rows):
        statement = rewrite_markers(sql, lambda position: f"${position}") if "?" in sql else sql
        self._run(self._connection.executemany(statement, rows, timeout=self.timeout))

    def table_exists(self, name: str) -> bool:
        return bool(self.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?)", (name.strip('"'),),
        ).fetchone()[0])

    def columns(self, name: str) -> set[str]:
        return {row[0] for row in self.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", (name,),
        ).fetchall()}


def connect(*, timeout: float = 5.0, readonly: bool = False) -> Connection:
    return Connection(timeout=timeout, readonly=readonly)


__all__ = ["connect", "identity"]
