"""
Tests for storage module
"""

import asyncio
import os
import asyncpg
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from flocks.project.instance import Instance
from flocks.storage import session_search as session_search_module
from flocks.storage.storage import Storage


class StorageTestModel(BaseModel):
    """Test model for storage"""
    id: str
    name: str
    value: int


@pytest.mark.asyncio
async def test_storage_init_continues_when_session_fts_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        session_search_module,
        "ensure_session_search_tables",
        AsyncMock(return_value=False),
    )

    with patch.object(Storage, "_initialized", False), patch.object(
        Storage,
        "_db_path",
        None,
    ), patch.object(Storage, "_session_search_available", True):
        await Storage.init(tmp_path / "fts-unavailable.db")

        assert Storage._initialized
        assert not Storage.session_search_available()


@pytest.mark.asyncio
async def test_storage_init_propagates_unexpected_session_schema_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failure = RuntimeError("unexpected schema failure")
    monkeypatch.setattr(
        session_search_module,
        "ensure_session_search_tables",
        AsyncMock(side_effect=failure),
    )

    with patch.object(Storage, "_initialized", False), patch.object(
        Storage,
        "_db_path",
        None,
    ), patch.object(Storage, "_session_search_available", True):
        with pytest.raises(RuntimeError, match="unexpected schema failure"):
            await Storage.init(tmp_path / "broken-session-schema.db")


@pytest.fixture
async def storage():
    """Use the retained, unique PostgreSQL schema created per test."""
    await Storage.init()
    yield Storage
    await Storage.shutdown()


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
async def test_storage_set_many_commits_all_entries(storage):
    """Multiple values should be persisted by one batch operation."""
    await storage.set_many([
        ("batch-set:key1", {"value": 1}, "test"),
        ("batch-set:key2", StorageTestModel(id="m2", name="Beta", value=2), "test_model"),
    ])

    assert await storage.get("batch-set:key1") == {"value": 1}
    model = await storage.get("batch-set:key2", StorageTestModel)
    assert model == StorageTestModel(id="m2", name="Beta", value=2)


@pytest.mark.asyncio
async def test_storage_set_many_rolls_back_all_entries(storage):
    """PostgreSQL rejects NaN JSON after the first batch row was submitted."""
    with pytest.raises(asyncpg.PostgresError):
        await storage.set_many([
            ("rollback:key1", {"value": 1}, "test"),
            ("rollback:key2", {"value": float("nan")}, "test"),
        ])
    assert await storage.get("rollback:key1") is None
    assert await storage.get("rollback:key2") is None


@pytest.mark.asyncio
async def test_storage_mutate_many_rolls_back_sets_and_deletes(storage):
    await storage.set("mixed:replace", {"value": "before"}, "test")
    await storage.set("mixed:delete", {"value": "keep"}, "test")
    async def fail_after_writes(transaction):
        assert await transaction.fetch_one("SELECT key FROM runtime_state WHERE key = ?", ("mixed:delete",)) is None
        raise RuntimeError("injected mixed mutation failure")
    with pytest.raises(RuntimeError, match="injected mixed mutation failure"):
        await storage.mutate_many(
            set_entries=[("mixed:replace", {"value": "after"}, "test")],
            delete_keys=["mixed:delete"], transaction_hook=fail_after_writes,
        )
    assert await storage.get("mixed:replace") == {"value": "before"}
    assert await storage.get("mixed:delete") == {"value": "keep"}


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
    
    deleted = await storage.clear("clear")
    assert deleted == 2
    
    keys = await storage.list_keys()
    assert len(keys) == 0


# ---------------------------------------------------------------------------
# DB corruption recovery (file is not a database)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_provide_drops_failed_context_task(monkeypatch):
    """A failed project-context initialization must not poison later requests."""
    directory = "/tmp/flocks-instance-retry"
    Instance._cache.pop(directory, None)
    calls = {"count": 0}

    async def fake_from_directory(cls, requested_directory):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("database connection unavailable")
        return {
            "project": SimpleNamespace(id="project"),
            "sandbox": requested_directory,
        }

    monkeypatch.setattr(
        "flocks.project.instance.Project.from_directory",
        classmethod(fake_from_directory),
    )

    with pytest.raises(ConnectionError):
        await Instance.provide(directory=directory, fn=lambda: "unreachable")

    assert directory not in Instance._cache
    assert await Instance.provide(directory=directory, fn=lambda: "ok") == "ok"
    assert calls["count"] == 2
    Instance._cache.pop(directory, None)


@pytest.fixture
def isolated_instance_initialization(monkeypatch, tmp_path):
    directory = str(tmp_path)
    monkeypatch.setattr(Instance, "_cache", {})
    monkeypatch.setattr(Instance, "_lock", asyncio.Lock())
    from_directory = AsyncMock(return_value={
        "project": SimpleNamespace(id="project"),
        "sandbox": directory,
    })
    monkeypatch.setattr("flocks.project.instance.Project.from_directory", from_directory)
    return directory, from_directory


@pytest.mark.asyncio
async def test_instance_provide_cancelled_waiter_preserves_shared_initialization(
    isolated_instance_initialization,
):
    directory, from_directory = isolated_instance_initialization
    started = asyncio.Event()
    release = asyncio.Event()

    async def initialize():
        started.set()
        await release.wait()

    init = AsyncMock(side_effect=initialize)
    cancelled_fn = AsyncMock()
    first = asyncio.create_task(Instance.provide(directory, init=init, fn=cancelled_fn))
    second = asyncio.create_task(Instance.provide(
        directory, init=init, fn=Instance.get_directory,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        shared = Instance._cache[directory]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        assert Instance._cache[directory] is shared
        assert not shared.done()
        assert not second.done()
        cancelled_fn.assert_not_awaited()

        release.set()
        assert await asyncio.wait_for(second, timeout=1) == directory
        assert await Instance.provide(directory, init=init, fn=Instance.get_directory) == directory
        assert Instance._cache[directory] is shared
        init.assert_awaited_once()
        from_directory.assert_awaited_once_with(directory)
    finally:
        release.set()
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, *Instance._cache.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_instance_provide_cancelled_initializer_can_retry(
    isolated_instance_initialization,
):
    directory, from_directory = isolated_instance_initialization
    started = asyncio.Event()
    release = asyncio.Event()

    async def initialize():
        started.set()
        await release.wait()

    init = AsyncMock(side_effect=initialize)
    waiter = asyncio.create_task(Instance.provide(directory, init=init))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        failed = Instance._cache[directory]
        failed.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert failed.cancelled()
        assert directory not in Instance._cache
        release.set()
        assert await Instance.provide(directory, init=init, fn=Instance.get_directory) == directory
        assert Instance._cache[directory] is not failed
        assert init.await_count == 2
        assert from_directory.await_count == 2
    finally:
        release.set()
        waiter.cancel()
        await asyncio.gather(waiter, *Instance._cache.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_instance_provide_keeps_fn_cancellable(
    isolated_instance_initialization,
):
    directory, from_directory = isolated_instance_initialization
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def run():
        assert Instance.get_directory() == directory
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    waiter = asyncio.create_task(Instance.provide(directory, fn=run))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        shared = Instance._cache[directory]
        assert shared.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert stopped.is_set()
        assert Instance._cache[directory] is shared
        assert not shared.cancelled()
        assert await Instance.provide(directory, fn=Instance.get_directory) == directory
        from_directory.assert_awaited_once_with(directory)
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


# ---------------------------------------------------------------------------
# Durability: WAL checkpoint on shutdown / startup, fork-safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_detects_fork_and_reinitialises(tmp_path):
    """``_ensure_init`` must rebuild Storage state after a ``fork()``.

    A pooled PostgreSQL connection belongs to one process.  We simulate ``fork()`` by mutating ``_init_pid`` to a
    PID that cannot match the current process and verify that the next
    ``_ensure_init`` call rebuilds — and that the rebuild is a no-op fast
    path on subsequent calls within the same (child) process.
    """
    db_path = tmp_path / "fork.db"
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)
        assert Storage._init_pid == os.getpid()

        # Pretend this process *is* the child of a forked parent: the
        # parent had PID 1 (which is never our own pid in pytest).
        Storage._init_pid = 1
        Storage._init_key = (-1, None, None)
        assert Storage._initialized is True

        # Spy on init so we can prove it gets called again.
        call_count = {"n": 0}
        real_init = Storage.init

        async def _spy(db_path=None):
            call_count["n"] += 1
            await real_init(db_path)

        with patch.object(Storage, "init", side_effect=_spy):
            await Storage._ensure_init()

        assert call_count["n"] == 1, (
            "Fork must trigger a fresh init in the child process"
        )
        assert Storage._init_pid == os.getpid()


@pytest.mark.asyncio
async def test_storage_shutdown_is_safe_to_call_when_not_initialised():
    """``shutdown()`` is a no-op when init was never called or failed."""
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        # Must not raise.
        await Storage.shutdown()


@pytest.mark.asyncio
async def test_runtime_cache_invalidation_is_scoped_to_the_affected_session(storage, monkeypatch):
    """Row-level deletes evict one session; only root/project prefixes reset everything."""
    from flocks.session.message import Message
    from flocks.session.session import Session

    session_calls: list = []
    message_calls: list = []
    monkeypatch.setattr(
        Session,
        "invalidate_cache",
        classmethod(lambda cls, session_id=None: session_calls.append(session_id)),
    )
    monkeypatch.setattr(
        Message,
        "invalidate_cache",
        classmethod(
            lambda cls, session_id=None, **kwargs: message_calls.append(
                (session_id, kwargs.get("discard_lock", True))
            )
        ),
    )

    await storage.mutate_many(
        delete_keys=[
            "session:proj:ses_a",
            "message:ses_a",
            "message_parts:ses_a",
            "message_parts:ses_a:msg_1",
            "todo:ses_a",
        ],
        delete_prefixes=["message_parts:ses_a:", "message_diff:ses_a:"],
    )
    assert session_calls == ["ses_a"]
    assert message_calls == [("ses_a", False)] * 4

    # Prefixes that may span several sessions still fall back to a global reset.
    session_calls.clear()
    message_calls.clear()
    await storage.mutate_many(delete_prefixes=["session:proj:", "message_parts:ses_a"])
    assert session_calls == [None]
    assert message_calls == [(None, True)]

    session_calls.clear()
    message_calls.clear()
    await storage.clear("session:")
    await storage.clear("message:")
    await storage.clear("message_parts:")
    with pytest.raises(ValueError, match="prefix"):
        await storage.clear()
    assert session_calls == [None]
    assert message_calls == [(None, True), (None, True)]


@pytest.mark.asyncio
async def test_mutate_many_can_skip_runtime_cache_invalidation(storage, monkeypatch):
    from flocks.session.message import Message

    message_calls: list = []
    monkeypatch.setattr(
        Message,
        "invalidate_cache",
        classmethod(lambda cls, session_id=None, **kwargs: message_calls.append(session_id)),
    )

    await storage.mutate_many(
        delete_keys=["message_parts:ses_a:msg_1"],
        invalidate_caches=False,
    )
    assert message_calls == []
