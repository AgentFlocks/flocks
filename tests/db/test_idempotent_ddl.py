"""Startup schema statements with nothing left to do take no table lock."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from flocks.db import registry
from flocks.db.backends.postgres import transaction_on
from flocks.db.idempotent_ddl import split_statements

SCHEMA = """
CREATE TABLE IF NOT EXISTS ddl_probe (id TEXT PRIMARY KEY, note TEXT DEFAULT ';', created_at BIGINT);
CREATE INDEX IF NOT EXISTS ddl_probe_note ON ddl_probe(note);
ALTER TABLE ddl_probe ADD COLUMN IF NOT EXISTS created_at BIGINT;
ALTER TABLE ddl_probe ALTER COLUMN created_at TYPE BIGINT
"""


def test_statements_split_on_semicolons_outside_quotes_and_comments():
    script = """
        CREATE TABLE IF NOT EXISTS a (x TEXT DEFAULT 'a;b', "odd;name" TEXT); -- one; two
        DO $body$ BEGIN PERFORM 1; END $body$;
        /* a; /* nested; */ */ SELECT $1, '$$'
    """
    statements = split_statements(script)

    assert [statement.split()[0] for statement in statements] == ["CREATE", "--", "/*"]
    assert "PERFORM 1; END" in statements[1]
    # Backslash escapes and unterminated text are not guessed at: the script runs as written.
    assert split_statements("SELECT E'it\\'s; fine'") is None
    assert split_statements("SELECT 'open") is None


@pytest.fixture
async def connections():
    if not os.environ.get("FLOCKS_TEST_PG_DSN"):
        pytest.skip("FLOCKS_TEST_PG_DSN is required for PostgreSQL integration tests")
    starting = await asyncpg.connect(registry.current().dsn, timeout=10)
    writing = await asyncpg.connect(registry.current().dsn, timeout=10)
    # As an older release left it: no index yet, a 32-bit timestamp.
    await starting.execute("CREATE TABLE ddl_probe (id TEXT PRIMARY KEY, note TEXT, created_at INTEGER)")
    try:
        yield starting, writing
    finally:
        await starting.close()
        await writing.close()


async def _while_written(writing, table="ddl_probe"):
    """Hold the lock a writer of *table* holds, which SHARE and ACCESS EXCLUSIVE wait for."""
    transaction = writing.transaction()
    await transaction.start()
    await writing.execute(f"LOCK TABLE {table} IN ROW EXCLUSIVE MODE")
    return transaction


async def test_a_start_takes_no_lock_for_schema_that_is_already_there(connections):
    starting, writing = connections
    await starting.execute(SCHEMA)
    writer = await _while_written(writing)
    try:
        async with transaction_on(starting) as handle:
            # Waiting for the writer would fail this at once instead of hanging.
            await handle.execute("SET LOCAL lock_timeout = '500ms'")
            await handle.executescript(SCHEMA)
            for statement in split_statements(SCHEMA):
                await handle.execute(statement)
    finally:
        await writer.rollback()


async def test_schema_that_is_missing_is_still_created(connections):
    starting, _writing = connections
    async with transaction_on(starting) as handle:
        await handle.executescript(SCHEMA + "; ALTER TABLE ddl_probe ADD COLUMN IF NOT EXISTS extra TEXT")

    assert await starting.fetchval("SELECT to_regclass('ddl_probe_note') IS NOT NULL")
    columns = dict(await starting.fetch(
        "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'ddl_probe'::regclass AND attnum > 0 AND NOT attisdropped"
    ))
    assert (columns["created_at"], columns["extra"]) == ("bigint", "text")


async def test_names_are_looked_up_after_a_statement_that_may_drop_them(connections):
    starting, _writing = connections
    async with transaction_on(starting) as handle:
        await handle.executescript("ALTER TABLE ddl_probe RENAME TO ddl_probe_old;" + SCHEMA)

    assert await starting.fetchval("SELECT to_regclass('ddl_probe') IS NOT NULL")
    assert await starting.fetchval("SELECT to_regclass('ddl_probe_old') IS NOT NULL")


async def test_a_synchronous_start_takes_no_lock_either(connections):
    from flocks.db import sync_query

    starting, writing = connections
    await starting.execute(SCHEMA)
    writer = await _while_written(writing)

    def start():
        with sync_query.connect(timeout=5) as connection:
            connection.execute("SET LOCAL lock_timeout = '500ms'")
            for statement in split_statements(SCHEMA):
                connection.execute(statement)

    try:
        await asyncio.to_thread(start)
    finally:
        await writer.rollback()


async def test_a_table_created_by_the_script_is_the_one_its_later_statements_concern(connections):
    from uuid import uuid4

    starting, _writing = connections
    shadow = "shadow_" + uuid4().hex
    current = await starting.fetchval("SELECT current_schema()")
    # The same table, already indexed, in a schema further down the search path.
    await starting.execute(f'CREATE SCHEMA "{shadow}"')
    await starting.execute(f'CREATE TABLE "{shadow}".ddl_twin (id TEXT PRIMARY KEY, note TEXT)')
    await starting.execute(f'CREATE INDEX ddl_twin_note ON "{shadow}".ddl_twin (note)')
    async with transaction_on(starting) as handle:
        await handle.execute(f'SET LOCAL search_path = "{current}", "{shadow}"')
        await handle.executescript(
            "CREATE TABLE IF NOT EXISTS ddl_twin (id TEXT PRIMARY KEY, note TEXT);"
            "CREATE INDEX IF NOT EXISTS ddl_twin_note ON ddl_twin (note);"
            "ALTER TABLE ddl_twin ADD COLUMN IF NOT EXISTS note TEXT"
        )

    assert await starting.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ddl_twin_note'", current,
    ) == 1


def test_what_the_reader_cannot_place_runs_as_written():
    from flocks.db.idempotent_ddl import _check

    function = (
        "CREATE OR REPLACE FUNCTION probe() RETURNS void AS $函数$ BEGIN "
        "CREATE TABLE IF NOT EXISTS inner_table (x int); END $函数$ LANGUAGE plpgsql"
    )
    # A dollar-quoted body, whatever its tag, stays one statement.
    assert split_statements(function + "; SELECT $1") == [function, " SELECT $1"]
    assert split_statements("SELECT $x") is None
    # Any character beyond ASCII may open a tag, digits of other scripts included.
    digit_tag = function.replace("$函数$", "$١$")
    assert split_statements(digit_tag) == [digit_tag]
    # A comma or bracket inside a comment does not make two subcommands one.
    assert _check(
        "ALTER TABLE demo ADD COLUMN IF NOT EXISTS present TEXT /* ( */, ADD COLUMN missing TEXT /* ) */"
    ) is None
