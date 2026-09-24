"""Legacy connection arguments must stay on the PostgreSQL runtime."""
import pytest

from flocks.storage.storage import Storage
from flocks.task.store import TaskStore


@pytest.mark.asyncio
async def test_legacy_paths_do_not_create_database_files(tmp_path):
    path = tmp_path / "old-storage.db"
    await Storage.init(path)
    async with Storage.connect(path) as db:
        assert db.dialect.name == "postgres"
        assert await db.fetch_one("SELECT current_schema()") is not None
    assert not path.exists()


@pytest.mark.asyncio
async def test_task_store_connection_uses_postgres():
    await TaskStore.init()
    db = await TaskStore.raw_db()
    assert db.dialect.name == "postgres"
    columns = await db.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", ("task_executions",))
    assert {"owner_node_id", "owner_generation", "lease_expires_at"} <= {row[0] for row in columns}
