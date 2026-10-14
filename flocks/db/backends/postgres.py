"""PostgreSQL side of the abstraction.

asyncpg takes parameters positionally rather than as a sequence, and returns
Records rather than tuples. Both differences stop here: a hook sees the same
:class:`~flocks.db.store.Transaction` it sees on SQLite.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Optional, Sequence

from flocks.db.dialect import Dialect, PostgresDialect
from flocks.db.idempotent_ddl import schema_script_to_run
from flocks.db.markers import rewrite_markers
from flocks.db.result import Execution, Result
from flocks.runtime_state.postgres import PostgresRuntimeStateStore, _like_prefix

_DIALECT = PostgresDialect()


class PostgresTransaction:
    """A :class:`~flocks.db.store.Transaction` over an asyncpg connection.

    The connection is expected to already be inside ``async with
    connection.transaction()``; this handle does not open or commit one, so a
    caller cannot accidentally split a document write and its derived index
    into two transactions.
    """

    __slots__ = ("_connection", "_rollback_only")

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._rollback_only = False

    @property
    def dialect(self) -> Dialect:
        return _DIALECT

    @property
    def row_factory(self):
        """Accepted and ignored: asyncpg records are already keyed by name."""
        return None

    @row_factory.setter
    def row_factory(self, factory) -> None:
        return None

    def _prepare(self, sql: str) -> str:
        """Accept statements written with SQLite's ``?`` markers.

        Several hundred existing statements use them and are otherwise valid;
        converting on the way to the driver is one implementation instead of
        one diff per statement. Statements built with Params already carry
        ``$n`` and pass through untouched, because they contain no ``?``.
        """
        return rewrite_markers(sql, _DIALECT.placeholder) if "?" in sql else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Execution:
        return Execution(self._execute(sql, params))

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> Result:
        """Run a statement and return a cursor-shaped result.

        asyncpg splits "gives rows" from "reports a count", so both are tried:
        a SELECT comes back through fetch, everything else through the status
        tag that carries the affected row count.
        """
        statement = self._prepare(sql)
        head = statement.lstrip().lstrip("(").upper()
        if head.startswith(("SELECT", "WITH", "SHOW", "PRAGMA")) or " RETURNING " in statement.upper():
            records = await self._connection.fetch(statement, *tuple(params))
            # asyncpg.Record, like sqlite3.Row, supports both row[0] and
            # row["column"], iteration over values, and dict(row). Turning it
            # into a dict breaks existing positional consumers (notably auth).
            return Result(records, rowcount=len(records))
        if not params and head.startswith(("CREATE", "ALTER")):
            # Schema statements run at every start: see flocks.db.idempotent_ddl.
            statement = await schema_script_to_run(self._connection, statement)
            if statement is None:
                return Result(rowcount=0)
        status = await self._connection.execute(statement, *tuple(params))
        return Result(rowcount=_affected(status))

    async def execute_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        await self._connection.executemany(self._prepare(sql), [tuple(row) for row in rows])

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        records = await self._connection.fetch(self._prepare(sql), *tuple(params))
        return [tuple(record) for record in records]

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[tuple]:
        record = await self._connection.fetchrow(self._prepare(sql), *tuple(params))
        return tuple(record) if record is not None else None

    async def fetch_all_mappings(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        records = await self._connection.fetch(self._prepare(sql), *tuple(params))
        return [dict(record) for record in records]

    async def fetch_one_mapping(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        record = await self._connection.fetchrow(self._prepare(sql), *tuple(params))
        return dict(record) if record is not None else None

    async def executescript(self, script: str) -> None:
        """Send static schema SQL in one round trip within its transaction.

        Statements PostgreSQL would skip are left out first, so a start does
        not lock tables another process is writing (flocks.db.idempotent_ddl).
        """
        script = await schema_script_to_run(self._connection, script)
        if script is not None:
            await self._connection.execute(script)

    async def commit(self) -> None:
        # asyncpg commits when the surrounding transaction block exits, and
        # statements outside one are already committed. Callers written for
        # SQLite's explicit commit stay correct without a special case.
        return None

    async def rollback(self) -> None:
        self._rollback_only = True

    async def close(self) -> None:
        """Release the connection back to whoever owns it.

        A pooled connection is returned by the context manager that acquired
        it, so closing here would take it out of the pool underneath its
        owner. Callers that close a handle they were handed get a no-op.
        """
        return None


def _affected(result: str) -> int:
    """asyncpg reports commands as ``DELETE 3``; take the count."""
    try:
        return int(result.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


@asynccontextmanager
async def transaction_on(connection):
    """Keep every statement on one connection until the block exits.

    Compatibility commit calls inside the block do not split it. Explicit
    rollback, exceptions and cancellation all discard the entire block.
    """
    transaction = connection.transaction()
    await transaction.start()
    handle = PostgresTransaction(connection)
    try:
        yield handle
    except BaseException:
        await transaction.rollback()
        raise
    else:
        if handle._rollback_only:
            await transaction.rollback()
        else:
            await transaction.commit()


class PostgresDocumentStore(PostgresRuntimeStateStore):
    """The :class:`~flocks.db.store.DocumentStore` contract on PostgreSQL.

    The shared-state store already covers get/set/delete/list. Two operations
    the key-value layer relies on are added here: an existence check, and a
    batched mutation that carries a derived-index hook into the *same*
    transaction as the documents it derives from. Without that second one a
    search index can commit while the message it describes rolls back.
    """

    _UPSERT = """
        INSERT INTO runtime_state (key, value, value_type, updated_at)
        VALUES ($1, $2::jsonb, $3, now())
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                value_type = EXCLUDED.value_type,
                updated_at = now()
    """

    async def exists(self, key: str) -> bool:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            found = await connection.fetchval("SELECT 1 FROM runtime_state WHERE key = $1", key)
        return found is not None

    @asynccontextmanager
    async def transaction(self, db_path: Optional[Any] = None):
        # db_path is accepted and ignored: routing keys to separate files is a
        # SQLite concern that does not exist here.
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            async with transaction_on(connection) as handle:
                yield handle

    async def mutate_many(
        self,
        *,
        set_entries: Sequence[Any] = (),
        delete_keys: Sequence[str] = (),
        delete_prefixes: Sequence[str] = (),
        pre_transaction_hook: Optional[Any] = None,
        transaction_hook: Optional[Any] = None,
    ) -> int:
        entries = list(set_entries)
        keys = list(delete_keys)
        prefixes = list(delete_prefixes)
        if not entries and not keys and not prefixes and pre_transaction_hook is None and transaction_hook is None:
            return 0

        pool = await self._ensure_pool()
        deleted = 0
        async with pool.acquire() as connection:
            async with transaction_on(connection) as transaction:
                if pre_transaction_hook is not None:
                    await pre_transaction_hook(transaction)
                if entries:
                    await connection.executemany(
                        self._UPSERT,
                        [(key, json.dumps(value), value_type) for key, value, value_type in entries],
                    )
                if keys:
                    result = await connection.execute(
                        "DELETE FROM runtime_state WHERE key = ANY($1::text[])", keys
                    )
                    deleted += _affected(result)
                if prefixes:
                    result = await connection.execute(
                        "DELETE FROM runtime_state WHERE key LIKE ANY($1::text[])",
                        [_like_prefix(prefix) for prefix in prefixes],
                    )
                    deleted += _affected(result)
                if transaction_hook is not None:
                    # Same connection, same transaction: the hook's writes and
                    # the documents above commit or roll back together.
                    await transaction_hook(transaction)
        return deleted


__all__ = ["PostgresDocumentStore", "PostgresTransaction"]
