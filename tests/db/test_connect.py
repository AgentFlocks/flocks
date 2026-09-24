"""Obtaining a handle must follow the configured backend, and give it back.

The store modules no longer choose a driver; they ask ``open_transaction``.
That makes this the single place where "which database does this process
talk to" is decided, so it is worth testing directly -- including the failure
path, because a connection dropped there leaks a non-daemon worker thread and
the interpreter then cannot exit. That is exactly how it first surfaced: the
whole test suite passed in seven seconds and then hung forever.
"""

from __future__ import annotations

import os

import pytest

from flocks.db import connect, registry
from flocks.db.registry import DatabaseConfigurationError

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")


@pytest.fixture(autouse=True)
def restore_backend():
    yield
    registry.reset()










@pytest.mark.asyncio
async def test_an_unsupported_backend_is_named_rather_than_guessed(monkeypatch):
    monkeypatch.setenv(registry.BACKEND_ENV, "mysql")
    monkeypatch.setenv(registry.DSN_ENV, "mysql://user@localhost/db")
    registry.reset()

    with pytest.raises(DatabaseConfigurationError) as caught:
        await connect.open_transaction()
    assert "PostgreSQL" in str(caught.value)


@requires_pg
@pytest.mark.asyncio
async def test_postgres_handle_comes_from_the_pool_and_goes_back(monkeypatch):
    monkeypatch.setenv(registry.BACKEND_ENV, "postgres")
    registry.reset()

    try:
        handle = await connect.open_transaction()
        try:
            assert handle.dialect.name == "postgres"
            # Written with SQLite's markers, answered by PostgreSQL. No table
            # is created: this is about which backend the handle reached.
            row = await handle.fetch_one_mapping(
                "SELECT ? AS note, current_database() AS db", ("first",)
            )
            assert row["note"] == "first"
        finally:
            await handle.close()

        pool = await connect.pool_for(registry.current().dsn)
        # Released, not held: a process-lived handle that never gave its
        # connection back would exhaust the pool one store at a time.
        assert pool.get_idle_size() >= 1

        second = await connect.pool_for(registry.current().dsn)
        assert second is pool, "the pool is created once per DSN and loop"
    finally:
        await connect.close_pools()

@pytest.mark.asyncio
async def test_legacy_path_and_configuration_callback_cannot_open_sqlite(tmp_path):
    from unittest.mock import AsyncMock
    legacy_path = tmp_path / "legacy.db"
    configure = AsyncMock()
    handle = await connect.open_transaction(sqlite_path=legacy_path, configure=configure, daemon_worker=True)
    try:
        assert await handle.fetch_one("SELECT 1") == (1,)
        assert handle.dialect.name == "postgres"
        assert not legacy_path.exists()
        configure.assert_not_awaited()
    finally:
        await handle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
async def test_removed_backend_cannot_open_a_handle(monkeypatch, backend):
    monkeypatch.setenv(registry.BACKEND_ENV, backend)
    registry.reset()
    with pytest.raises(DatabaseConfigurationError):
        await connect.open_transaction()
