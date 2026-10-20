"""The /ready database probe: a reachable primary is ready, a standby is not."""

import os

import pytest

from flocks.db.health import database_ready


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="needs FLOCKS_TEST_PG_DSN")
async def test_primary_database_is_ready() -> None:
    ok, detail = await database_ready()
    assert ok, detail
    assert detail == "database primary"


@pytest.mark.asyncio
async def test_standby_is_not_ready(monkeypatch) -> None:
    class _Tx:
        async def fetch_one(self, sql, params=()):
            assert "pg_is_in_recovery" in sql
            return (True,)

    class _Ctx:
        async def __aenter__(self):
            return _Tx()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("flocks.storage.storage.Storage.transaction", lambda *args, **kwargs: _Ctx())
    ok, detail = await database_ready()
    assert not ok and "standby" in detail


@pytest.mark.asyncio
async def test_unreachable_database_raises_for_the_probe_runner(monkeypatch) -> None:
    """The probe runner turns an exception into "not ready"; the probe does not hide it."""

    class _Ctx:
        async def __aenter__(self):
            raise ConnectionRefusedError("database down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("flocks.storage.storage.Storage.transaction", lambda *args, **kwargs: _Ctx())
    with pytest.raises(ConnectionRefusedError):
        await database_ready()
