"""Synchronous reads have to reach the configured backend too.

License status is read from synchronous code and cannot become a coroutine
without changing its callers, so it goes through this reader instead of
opening SQLite directly. If it silently returned nothing on PostgreSQL, a Pro
deployment would look unactivated with no error anywhere.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from flocks.db import registry, sync_store

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")


@pytest.fixture(autouse=True)
def restore_backend():
    yield
    registry.reset()














@requires_pg
def test_postgres_reads_from_runtime_state(monkeypatch):
    monkeypatch.setenv(registry.BACKEND_ENV, "postgres")
    registry.reset()

    async def seed():
        import asyncpg

        connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
        try:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS runtime_state ("
                " key TEXT PRIMARY KEY, value JSONB NOT NULL,"
                " value_type TEXT NOT NULL DEFAULT 'json')"
            )
            await connection.execute(
                "INSERT INTO runtime_state (key, value) VALUES ($1, $2::jsonb)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                "console:upgrade_request:r9",
                json.dumps({"id": "r9", "status": "approved"}),
            )
        finally:
            await connection.close()

    asyncio.run(seed())

    reader = sync_store.reader(None)
    assert reader.available() is True
    # Called from ordinary synchronous code, with no event loop of its own.
    found = reader.get_prefix("console:upgrade_request:")
    assert found["console:upgrade_request:r9"]["status"] == "approved"


@requires_pg
@pytest.mark.asyncio
async def test_postgres_reader_works_from_inside_a_running_loop(monkeypatch):
    """asyncio.run() refuses to nest, so the reader must not rely on it here."""
    monkeypatch.setenv(registry.BACKEND_ENV, "postgres")
    registry.reset()

    from flocks.storage.storage import Storage
    await Storage.init()
    reader = sync_store.reader(None)
    assert isinstance(reader.get_prefix("console:upgrade_request:"), dict)

@pytest.mark.asyncio
async def test_sync_reader_returns_values_prefixes_and_missing_keys():
    from flocks.storage.storage import Storage
    await Storage.init()
    await Storage.set_many([
        ("reader:one", {"status": "approved"}, "json"),
        ("reader:two", [1, 2], "json"),
        ("unrelated", False, "json"),
    ])
    reader = sync_store.reader(None)
    assert reader.available()
    assert reader.get("reader:one") == {"status": "approved"}
    assert reader.get("missing") is None
    assert reader.get_prefix("reader:") == {"reader:one": {"status": "approved"}, "reader:two": [1, 2]}


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
def test_sync_reader_rejects_removed_backend(monkeypatch, backend):
    monkeypatch.setenv(registry.BACKEND_ENV, backend)
    registry.reset()
    with pytest.raises(registry.DatabaseConfigurationError):
        sync_store.reader(None)
