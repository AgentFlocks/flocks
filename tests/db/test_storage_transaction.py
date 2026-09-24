"""Storage transaction hooks share one PostgreSQL connection and atomic commit.
"""

from __future__ import annotations

import os
import uuid

import pytest

from flocks.db.store import Transaction
from flocks.storage.storage import Storage

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")


@pytest.fixture
async def backend(tmp_path, monkeypatch):
    if not PG_DSN:
        pytest.skip("FLOCKS_TEST_PG_DSN is required")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    await Storage.init()
    yield "postgres", f"probe_{uuid.uuid4().hex[:8]}"
    await Storage.shutdown()


async def _create(handle: Transaction, table: str) -> None:
    """One CREATE TABLE, written once, typed by the dialect.

    The table persists across transactions because pooled PostgreSQL operations
    can use different connections.
    """
    d = handle.dialect
    await handle.execute(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "id TEXT PRIMARY KEY, "
        f"name {d.text_type()} NOT NULL, "
        f"payload {d.json_type()}, "
        f"enabled {d.boolean_type()} NOT NULL, "
        f"updated_at {d.timestamp_type()} NOT NULL)"
    )


@pytest.mark.asyncio
async def test_transaction_yields_a_portable_handle(backend):
    _name, table = backend
    async with Storage.transaction() as handle:
        assert isinstance(handle, Transaction)
        assert handle.dialect.name == "postgres"
        await _create(handle, table)


@pytest.mark.asyncio
async def test_same_ddl_and_dml_run_on_either_backend(backend):
    _name, table = backend
    async with Storage.transaction() as handle:
        await _create(handle, table)
        mark = handle.dialect.placeholder
        await handle.execute(
            f"INSERT INTO {table} (id, name, payload, enabled, updated_at) "
            f"VALUES ({mark(1)}, {mark(2)}, {mark(3)}, {mark(4)}, {mark(5)})",
            ("row-one", "first", None, True, 1700000000000),
        )

    async with Storage.transaction() as handle:
        rows = await handle.fetch_all_mappings(f"SELECT name, updated_at FROM {table}")
        assert [row["name"] for row in rows] == ["first"]
        assert rows[0]["updated_at"] == 1700000000000


@pytest.mark.asyncio
async def test_a_failure_inside_the_block_rolls_everything_back(backend):
    _name, table = backend
    async with Storage.transaction() as handle:
        await _create(handle, table)

    with pytest.raises(RuntimeError):
        async with Storage.transaction() as handle:
            mark = handle.dialect.placeholder
            await handle.execute(
                f"INSERT INTO {table} (id, name, payload, enabled, updated_at) "
                f"VALUES ({mark(1)}, {mark(2)}, {mark(3)}, {mark(4)}, {mark(5)})",
                ("row-doomed", "doomed", None, True, 1),
            )
            raise RuntimeError("caller failed midway")

    async with Storage.transaction() as handle:
        rows = await handle.fetch_all(f"SELECT name FROM {table}")
        assert rows == []
