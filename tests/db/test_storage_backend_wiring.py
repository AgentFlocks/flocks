"""Switching backends must be a configuration change, not a code change.

These tests set the environment the way a deployment would and then use the
ordinary ``Storage`` API. The decisive assertion is not that the calls succeed
-- they would succeed on SQLite too -- but that the row appears in PostgreSQL.
"""

from __future__ import annotations

import os
import uuid

import pytest

from flocks.db import active, registry
from flocks.storage.storage import Storage

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")


@pytest.fixture
async def postgres_backed(tmp_path, monkeypatch):
    """Point the process at PostgreSQL, and put it back afterwards."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv(registry.BACKEND_ENV, "postgres")
    registry.reset()
    await active.reset()
    registry.configure()
    await Storage.init(tmp_path / "unused.db")
    prefix = f"wiring:{uuid.uuid4().hex[:8]}:"
    try:
        yield prefix
    finally:
        store = await active.document_store()
        if store is not None:
            await store.mutate_many(delete_prefixes=[prefix])
        await Storage.shutdown()
        await active.reset()
        registry.reset()


async def _row_count_in_postgres(key: str) -> int:
    import asyncpg

    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        return await connection.fetchval(
            "SELECT COUNT(*) FROM runtime_state WHERE key = $1", key
        )
    finally:
        await connection.close()


def test_missing_dsn_fails_instead_of_creating_a_local_database(monkeypatch):
    monkeypatch.delenv(registry.BACKEND_ENV, raising=False)
    monkeypatch.delenv(registry.DSN_ENV, raising=False)
    registry.reset()
    with pytest.raises(registry.DatabaseConfigurationError):
        registry.current()


@requires_pg
@pytest.mark.asyncio
async def test_storage_writes_land_in_postgres(postgres_backed):
    prefix = postgres_backed
    key = prefix + "doc"

    await Storage.set(key, {"v": 1, "nested": {"a": [1, 2]}})

    # The whole point: the ordinary Storage call wrote to PostgreSQL.
    assert await _row_count_in_postgres(key) == 1
    assert await Storage.get(key) == {"v": 1, "nested": {"a": [1, 2]}}


@requires_pg
@pytest.mark.asyncio
async def test_storage_read_write_delete_cycle_on_postgres(postgres_backed):
    prefix = postgres_backed
    key = prefix + "cycle"

    assert await Storage.exists(key) is False
    await Storage.set(key, {"v": 1})
    assert await Storage.exists(key) is True
    await Storage.set(key, {"v": 2})
    assert await Storage.get(key) == {"v": 2}
    assert await _row_count_in_postgres(key) == 1

    assert await Storage.delete(key) is True
    assert await Storage.get(key) is None
    assert await _row_count_in_postgres(key) == 0


@requires_pg
@pytest.mark.asyncio
async def test_list_keys_reads_from_postgres(postgres_backed):
    prefix = postgres_backed
    await Storage.set(prefix + "b", {"v": 2})
    await Storage.set(prefix + "a", {"v": 1})
    assert await Storage.list_keys(prefix) == [prefix + "a", prefix + "b"]


@requires_pg
@pytest.mark.asyncio
async def test_pydantic_models_survive_the_round_trip(postgres_backed):
    from pydantic import BaseModel

    class Sample(BaseModel):
        name: str
        count: int

    prefix = postgres_backed
    key = prefix + "model"
    await Storage.set(key, Sample(name="x", count=3))

    assert await Storage.get(key) == {"name": "x", "count": 3}
    restored = await Storage.get(key, Sample)
    assert isinstance(restored, Sample)
    assert restored.name == "x" and restored.count == 3
