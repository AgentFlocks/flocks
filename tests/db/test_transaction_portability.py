"""Derived index helpers use the PostgreSQL transaction/dialect contract.
"""

from __future__ import annotations

import os

import pytest

from flocks.db.store import Transaction

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")

TABLE = "portable_index"


# --- backend-agnostic code under test ------------------------------------

async def upsert_index_entry(transaction: Transaction, key: str, body: str) -> None:
    sql = transaction.dialect.upsert(
        TABLE, ("key", "body"), key_columns=("key",), update_columns=("body",)
    )
    await transaction.execute(sql, (key, body))


async def read_entry(transaction: Transaction, key: str):
    d = transaction.dialect
    sql = (
        f"SELECT {d.quote('body')} FROM {d.quote(TABLE)} "
        f"WHERE {d.quote('key')} = {d.placeholder(1)}"
    )
    return await transaction.fetch_one(sql, (key,))


async def count_rows(transaction: Transaction) -> int:
    rows = await transaction.fetch_all(f"SELECT COUNT(*) FROM {transaction.dialect.quote(TABLE)}")
    return rows[0][0]


# --- conformance ----------------------------------------------------------



@requires_pg
def test_postgres_transaction_satisfies_the_protocol():
    from flocks.db.backends.postgres import PostgresTransaction

    assert isinstance(PostgresTransaction(None), Transaction)


# --- the shared transaction contract ---------------------------------------



@requires_pg
@pytest.mark.asyncio
async def test_derived_index_code_runs_unchanged_on_postgres():
    import asyncpg

    from flocks.db.backends.postgres import PostgresTransaction

    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        async with connection.transaction():
            await connection.execute(
                f"CREATE TABLE {TABLE} (key TEXT PRIMARY KEY, body TEXT)"
            )
            transaction = PostgresTransaction(connection)

            await upsert_index_entry(transaction, "a", "hello")
            assert await read_entry(transaction, "a") == ("hello",)

            await upsert_index_entry(transaction, "a", "hello again")
            assert await read_entry(transaction, "a") == ("hello again",)
            assert await count_rows(transaction) == 1

            await transaction.execute_many(
                transaction.dialect.upsert(
                    TABLE, ("key", "body"), key_columns=("key",), update_columns=("body",)
                ),
                [("b", "second"), ("c", "third")],
            )
            assert await count_rows(transaction) == 3
            assert await read_entry(transaction, "missing") is None
    finally:
        await connection.close()




@requires_pg
@pytest.mark.asyncio
async def test_rows_read_by_column_name_match_on_postgres():
    import asyncpg

    from flocks.db.backends.postgres import PostgresTransaction

    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        async with connection.transaction():
            await connection.execute(
                f"CREATE TABLE {TABLE} (key TEXT PRIMARY KEY, body TEXT)"
            )
            transaction = PostgresTransaction(connection)
            await upsert_index_entry(transaction, "a", "hello")

            rows = await transaction.fetch_all_mappings(f"SELECT key, body FROM {TABLE}")
            assert rows == [{"key": "a", "body": "hello"}]

            one = await transaction.fetch_one_mapping(
                f"SELECT key, body FROM {TABLE} WHERE key = $1", ("a",)
            )
            assert one["body"] == "hello"
            assert await transaction.fetch_one_mapping(
                f"SELECT key FROM {TABLE} WHERE key = $1", ("missing",)
            ) is None
    finally:
        await connection.close()

