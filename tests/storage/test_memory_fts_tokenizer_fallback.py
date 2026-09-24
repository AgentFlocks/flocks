"""Memory indexing is initialized with PostgreSQL pg_trgm."""
import pytest

from flocks.db.search import memory_search_index_for, PostgresMemoryIndex
from flocks.storage.storage import Storage
from flocks.storage import vector


def test_postgres_is_the_only_runtime_memory_index():
    assert isinstance(memory_search_index_for("postgres"), PostgresMemoryIndex)
    assert memory_search_index_for("sqlite") is None
    assert memory_search_index_for("mysql") is None


@pytest.mark.asyncio
async def test_memory_schema_initialization_is_idempotent():
    await Storage.init()
    first = await vector.ensure_vector_tables(Storage.get_db_path())
    second = await vector.ensure_vector_tables(Storage.get_db_path())
    assert first["vector_tables"] and second["vector_tables"]
    assert first["fts5"] and second["fts5"]
    async with Storage.transaction() as db:
        index = memory_search_index_for("postgres")
        assert await db.fetch_one("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename = ?", (index.table,)) == (index.table,)
