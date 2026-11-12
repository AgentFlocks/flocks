"""Monitor storage over the installed Pro database gate, never tasks.db."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import re
from weakref import WeakValueDictionary

from .monitor_sql import FUNCTIONS, prepare

_initialized = set()
_transaction_locks = WeakValueDictionary()


def _transaction_lock():
    key = id(asyncio.get_running_loop())
    lock = _transaction_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _transaction_locks[key] = lock
    return lock


def active():
    from flocks.db_backend import backend_choice
    return backend_choice().backend == 'postgres'


def _catalog(sql):
    # Only legacy feature catalog probes are accepted, not arbitrary PRAGMAs.
    match = re.fullmatch(r'PRAGMA table_info\((monitor_[a-z_]+)\)', sql.strip(), re.I)
    if match:
        return ("SELECT ordinal_position-1 AS cid,column_name AS name,data_type AS type,0 AS notnull,column_default AS dflt_value,0 AS pk FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position", (match[1],))
    match = re.fullmatch(r"SELECT 1 FROM sqlite_master WHERE (?:type='table' AND )?name='(monitor_[a-z_]+)'", sql.strip(), re.I)
    if match:
        return ('SELECT 1 WHERE to_regclass(?) IS NOT NULL', (match[1],))
    return None


async def initialize():
    from flocks.task.store import TaskStore
    await TaskStore.init()
    handle = await TaskStore.raw_db()
    # The native handle's identity changes when the configured DB/schema does.
    key = (id(asyncio.get_running_loop()), getattr(handle, '_dsn', None))
    if key in _initialized:
        return
    async with handle.transaction() as db:
        await db.execute("SELECT pg_advisory_xact_lock(hashtext('flocks:zwmonitor:schema:v1'))")
        await db.executescript(Path(__file__).with_name('monitor_schema.sql').read_text())
        await db.executescript(FUNCTIONS)
    _initialized.add(key)


class AsyncConnection:
    def __init__(self, transaction):
        self.native_transaction = transaction

    def execute(self, sql, args=()):
        from flocks.db.result import Execution, Result
        if sql.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE'}:
            async def begun():
                return Result(rowcount=0)
            return Execution(begun())
        probe = _catalog(sql)
        if probe:
            sql, args = probe
        else:
            sql, args = prepare(sql, args)
        return self.native_transaction.execute(sql, args)

    async def commit(self):
        # The enclosing native transaction commits exactly once.
        pass

    async def executemany(self, sql, rows):
        batches = {}
        for values in rows:
            statement, args = prepare(sql, values)
            batches.setdefault(statement, []).append(args)
        for statement, values in batches.items():
            await self.native_transaction.execute_many(statement, values)

    async def rollback(self):
        await self.native_transaction.rollback()


@asynccontextmanager
async def connection():
    from flocks.task.store import TaskStore
    # Queue locally before borrowing a connection. A daily-session transaction
    # calls native Session.get/create, which needs another pooled connection;
    # advisory-lock waiters must not consume every remaining pool slot.
    async with _transaction_lock():
        await initialize()
        handle = await TaskStore.raw_db()
        async with handle.transaction() as db:
            # Keep cross-node serialization through the original Go DB gate.
            await db.execute("SELECT pg_advisory_xact_lock(hashtext('flocks:zwmonitor:write:v1'))")
            yield AsyncConnection(db)


class SyncConnection:
    def __init__(self, native):
        self.native = native

    def create_function(self, name, count, function, **kwargs):
        if (name, count) != ('monitor_event_version', 1):
            raise ValueError('Unsupported monitor SQL function')
        # This exact deterministic projection is installed in PostgreSQL.

    def execute(self, sql, args=()):
        if sql.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE', 'COMMIT'}:
            return None  # The native read scope already owns the snapshot.
        probe = _catalog(sql)
        if probe:
            sql, args = probe
        else:
            sql, args = prepare(sql, args)
        return self.native.execute(sql, args)


@contextmanager
def sync_connection(*, readonly=True, timeout=8):
    from flocks.db.sync_query import connect
    with connect(readonly=readonly, timeout=timeout) as db:
        yield SyncConnection(db)
