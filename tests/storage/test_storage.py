"""
Tests for storage module
"""

from contextlib import asynccontextmanager
import sqlite3
import pytest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from flocks.storage.storage import Storage


class StorageTestModel(BaseModel):
    """Test model for storage"""
    id: str
    name: str
    value: int


@pytest.fixture
async def storage():
    """Create a temporary storage for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        await Storage.init(db_path)
        yield Storage
        # Cleanup
        await Storage.clear()


@pytest.mark.asyncio
async def test_storage_set_get(storage):
    """Test basic set and get operations"""
    await storage.set("test_key", {"value": 123}, "test")
    
    result = await storage.get("test_key")
    assert result == {"value": 123}


@pytest.mark.asyncio
async def test_storage_with_model(storage):
    """Test storage with Pydantic models"""
    model = StorageTestModel(id="test_1", name="Test", value=42)
    
    await storage.set("model_key", model, "test_model")
    
    retrieved = await storage.get("model_key", StorageTestModel)
    assert retrieved.id == "test_1"
    assert retrieved.name == "Test"
    assert retrieved.value == 42


@pytest.mark.asyncio
async def test_storage_delete(storage):
    """Test delete operation"""
    await storage.set("delete_key", {"data": "test"}, "test")
    
    exists = await storage.exists("delete_key")
    assert exists is True
    
    deleted = await storage.delete("delete_key")
    assert deleted is True
    
    exists = await storage.exists("delete_key")
    assert exists is False


@pytest.mark.asyncio
async def test_storage_list_keys(storage):
    """Test listing keys with prefix"""
    await storage.set("prefix:key1", {"data": 1}, "test")
    await storage.set("prefix:key2", {"data": 2}, "test")
    await storage.set("other:key", {"data": 3}, "test")
    
    keys = await storage.list_keys(prefix="prefix:")
    assert len(keys) == 2
    assert "prefix:key1" in keys
    assert "prefix:key2" in keys
    assert "other:key" not in keys


@pytest.mark.asyncio
async def test_storage_list_entries(storage):
    """Test batch listing entries with model deserialization."""
    item1 = StorageTestModel(id="m1", name="Alpha", value=1)
    item2 = StorageTestModel(id="m2", name="Beta", value=2)
    await storage.set("batch:key1", item1, "test_model")
    await storage.set("batch:key2", item2, "test_model")
    await storage.set("other:key", {"skip": True}, "test")

    entries = await storage.list_entries(prefix="batch:", model=StorageTestModel)

    assert len(entries) == 2
    entry_map = {key: value for key, value in entries}
    assert set(entry_map) == {"batch:key1", "batch:key2"}
    assert entry_map["batch:key1"].name == "Alpha"
    assert entry_map["batch:key2"].value == 2


@pytest.mark.asyncio
async def test_storage_clear(storage):
    """Test clearing storage"""
    await storage.set("clear1", {"data": 1}, "test")
    await storage.set("clear2", {"data": 2}, "test")
    
    deleted = await storage.clear()
    assert deleted == 2
    
    keys = await storage.list_keys()
    assert len(keys) == 0


@pytest.mark.asyncio
async def test_storage_set_retries_on_sqlite_busy():
    """`Storage.set()` should retry transient SQLite lock contention."""
    execute_calls = {"count": 0}

    class FakeConnection:
        async def execute(self, *_args, **_kwargs):
            execute_calls["count"] += 1
            if execute_calls["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return SimpleNamespace(rowcount=1)

        async def commit(self):
            return None

        async def close(self):
            return None

    @asynccontextmanager
    async def _fake_connect(_db_path=None):
        yield FakeConnection()

    with patch.object(Storage, "_ensure_init", AsyncMock()), \
         patch.object(Storage, "connect", side_effect=_fake_connect), \
         patch.object(Storage, "_db_path", Path("/tmp/test-storage.db")):
        await Storage.set("busy:key", {"value": 1}, "test")

    assert execute_calls["count"] == 2


@pytest.mark.asyncio
async def test_storage_set_does_not_swallow_non_busy_sqlite_errors():
    """Unexpected SQLite errors should still surface to callers."""

    class FakeConnection:
        async def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("near \"INSERT\": syntax error")

        async def commit(self):
            return None

        async def close(self):
            return None

    @asynccontextmanager
    async def _fake_connect(_db_path=None):
        yield FakeConnection()

    with patch.object(Storage, "_ensure_init", AsyncMock()), \
         patch.object(Storage, "connect", side_effect=_fake_connect), \
         patch.object(Storage, "_db_path", Path("/tmp/test-storage.db")):
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            await Storage.set("bad:key", {"value": 1}, "test")
