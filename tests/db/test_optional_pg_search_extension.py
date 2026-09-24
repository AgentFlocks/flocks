import os

import pytest

from flocks.db.search import PostgresMemoryIndex
from flocks.storage.storage import Storage
from flocks.storage.vector import ensure_vector_tables

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")]


async def test_failed_optional_index_does_not_rollback_memory_schema(monkeypatch):
    monkeypatch.setattr(PostgresMemoryIndex, "accelerator_statements", lambda self: ["SELECT 1 / 0"])
    status = await ensure_vector_tables(Storage.get_db_path())
    assert status["vector_tables"] and status["fts5"]
    async with Storage.transaction(ensure_init=False) as db:
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_files") == (0,)
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_fts") == (0,)
