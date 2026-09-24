"""Cursor-shaped storage call sites remain supported on PostgreSQL.

These cases verify async cursor contexts, row counts, iteration, and mapping
access through the pooled PostgreSQL transaction facade.
"""

from __future__ import annotations

import os
import uuid

import pytest


PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")


@pytest.fixture
async def handle(postgres_handle):
    table = f"cursor_probe_{uuid.uuid4().hex[:8]}"
    await postgres_handle.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, note TEXT)")
    yield postgres_handle, table


@pytest.mark.asyncio
async def test_async_with_and_fetchone(handle):
    db, table = handle
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("a", "first"))

    async with db.execute(f"SELECT note FROM {table} WHERE id = ?", ("a",)) as cur:
        row = await cur.fetchone()
    assert row["note"] == "first"


@pytest.mark.asyncio
async def test_fetchall_and_list_comprehension(handle):
    db, table = handle
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("a", "one"))
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("b", "two"))

    async with db.execute(f"SELECT id FROM {table} ORDER BY id") as cur:
        ids = [str(row["id"]) for row in await cur.fetchall()]
    assert ids == ["a", "b"]


@pytest.mark.asyncio
async def test_rowcount_after_delete(handle):
    db, table = handle
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("a", "one"))
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("b", "two"))

    cur = await db.execute(f"DELETE FROM {table} WHERE id = ?", ("a",))
    assert cur.rowcount == 1

    cur = await db.execute(f"DELETE FROM {table} WHERE id = ?", ("missing",))
    assert cur.rowcount == 0


@pytest.mark.asyncio
async def test_missing_row_reads_as_none(handle):
    db, table = handle
    async with db.execute(f"SELECT note FROM {table} WHERE id = ?", ("nope",)) as cur:
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_result_can_be_iterated(handle):
    db, table = handle
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("a", "one"))
    await db.execute(f"INSERT INTO {table} (id, note) VALUES (?, ?)", ("b", "two"))

    seen = []
    async for row in await db.execute(f"SELECT id FROM {table} ORDER BY id"):
        seen.append(str(row["id"]))
    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_commit_is_safe_on_either_backend(handle):
    db, _table = handle
    await db.commit()


@pytest.mark.asyncio
async def test_rollback_is_available_on_either_backend(handle):
    """Stores that open their own transaction call it in an except branch.

    Without it the AttributeError replaces the real failure, and whatever the
    block had already written stays -- which is how a half-built FTS table
    survived a failed migration and made the database look corrupt.
    """
    db, _table = handle
    await db.rollback()



@pytest.mark.asyncio
async def test_explicit_rollback_discards_transaction_write():
    from flocks.storage.storage import Storage
    async with Storage.transaction() as handle:
        await handle.execute("CREATE TABLE cursor_rollback (id TEXT PRIMARY KEY)")
    async with Storage.transaction() as handle:
        await handle.execute("INSERT INTO cursor_rollback(id) VALUES (?)", ("gone",))
        await handle.rollback()
    async with Storage.transaction() as handle:
        assert await handle.fetch_one("SELECT id FROM cursor_rollback WHERE id = ?", ("gone",)) is None
