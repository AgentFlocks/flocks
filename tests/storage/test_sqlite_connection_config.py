from __future__ import annotations

import aiosqlite
import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.manager import TaskManager


@pytest.fixture(autouse=True)
async def isolated_storage(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    Config._global_config = None
    Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None

    yield

    Storage._initialized = False
    Storage._db_path = None
    Config._global_config = None
    Config._cached_config = None


@pytest.mark.asyncio
async def test_storage_init_enables_wal_for_fresh_database() -> None:
    await Storage.init()

    async with aiosqlite.connect(Storage.get_db_path()) as db:
        async with db.execute("PRAGMA journal_mode") as cursor:
            journal_mode = (await cursor.fetchone())[0]

    assert journal_mode == "wal"


@pytest.mark.asyncio
async def test_storage_connect_applies_runtime_sqlite_pragmas() -> None:
    await Storage.init()

    async with Storage.connect() as db:
        async with db.execute("PRAGMA busy_timeout") as cursor:
            busy_timeout = (await cursor.fetchone())[0]
        async with db.execute("PRAGMA foreign_keys") as cursor:
            foreign_keys = (await cursor.fetchone())[0]

    assert busy_timeout == Storage._sqlite_busy_timeout_ms
    assert foreign_keys == 1


def test_storage_connect_sync_applies_runtime_sqlite_pragmas() -> None:
    import asyncio

    asyncio.run(Storage.init())

    with Storage.connect_sync() as db:
        busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]

    assert busy_timeout == Storage._sqlite_busy_timeout_ms
    assert foreign_keys == 1


@pytest.mark.asyncio
async def test_task_manager_sync_connection_uses_storage_sqlite_contract() -> None:
    await Storage.init()

    with TaskManager._with_db_connection() as db:
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'storage'"
        ).fetchone()
        busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]

    assert row is not None
    assert busy_timeout == Storage._sqlite_busy_timeout_ms
    assert foreign_keys == 1
