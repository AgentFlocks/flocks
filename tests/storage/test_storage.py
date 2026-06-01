"""
Tests for storage module
"""

from contextlib import asynccontextmanager
import os
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


def test_is_sqlite_busy_error_checks_sqlite_error_code():
    """Busy/locked sqlite error codes should be recognized without message matching."""
    exc = sqlite3.OperationalError("custom wrapper text")
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY

    assert Storage._is_sqlite_busy_error(exc) is True


def test_is_sqlite_busy_error_ignores_non_sqlite_custom_exceptions():
    """Non-sqlite exceptions should not be retried based on message substring alone."""

    class FakeError(Exception):
        pass

    exc = FakeError("database is locked")
    assert Storage._is_sqlite_busy_error(exc) is False


@pytest.mark.asyncio
async def test_storage_init_retries_when_create_table_hits_sqlite_busy(tmp_path):
    """Initialization should retry table creation on transient SQLite lock errors."""
    db_path = tmp_path / "retry-init.db"
    original_connect = Storage.connect
    call_count = {"count": 0}

    @asynccontextmanager
    async def _flaky_connect(target_db_path=None):
        target = Path(target_db_path) if target_db_path is not None else Storage.get_db_path()
        if call_count["count"] == 0:
            call_count["count"] += 1

            class BusyConnection:
                async def execute(self, *_args, **_kwargs):
                    raise sqlite3.OperationalError("database is locked")

                async def close(self):
                    return None

            yield BusyConnection()
            return

        async with original_connect(target) as real_conn:
            yield real_conn

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "connect", side_effect=_flaky_connect):
        await Storage.init(db_path)

    assert call_count["count"] == 1
    assert db_path.exists()


# ---------------------------------------------------------------------------
# DB corruption recovery (file is not a database)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_init_quarantines_invalid_header_and_boots(tmp_path):
    """Non-SQLite garbage at the DB path is renamed aside so init still succeeds.

    Reproduces the production failure ``sqlite3.DatabaseError: file is not a
    database`` (which historically killed server startup) and asserts that:
      * ``Storage.init()`` no longer raises,
      * the corrupt main file is preserved under a ``.corrupt.<ts>`` suffix
        for offline recovery, and
      * adjacent WAL/SHM sidecars are quarantined alongside it.
    """
    db_path = tmp_path / "flocks.db"
    db_path.write_bytes(b"This is garbage, not a sqlite file\n")
    db_path.with_name("flocks.db-wal").write_bytes(b"fake wal payload")
    db_path.with_name("flocks.db-shm").write_bytes(b"fake shm payload")

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None):
        await Storage.init(db_path)

    # Fresh DB is now usable
    await Storage.set("hello", {"value": 1})
    assert await Storage.get("hello") == {"value": 1}

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "flocks.db" in siblings
    corrupt_files = [name for name in siblings if ".corrupt." in name]
    assert any(name.startswith("flocks.db.corrupt.") for name in corrupt_files), siblings
    assert any(name.startswith("flocks.db-wal.corrupt.") for name in corrupt_files), siblings
    assert any(name.startswith("flocks.db-shm.corrupt.") for name in corrupt_files), siblings


@pytest.mark.asyncio
async def test_storage_init_recovers_when_pragma_reports_corruption(tmp_path):
    """Files whose magic header is valid but inner pages are damaged still recover.

    Forges a payload that begins with the real SQLite magic header so the
    fast-path check passes, then fails on the first PRAGMA — exercising the
    fallback quarantine+retry branch inside ``Storage.init``.
    """
    db_path = tmp_path / "flocks.db"
    db_path.write_bytes(Storage._SQLITE_MAGIC + b"\xff" * 2048)

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None):
        await Storage.init(db_path)

    await Storage.set("hello", {"value": 2})
    assert await Storage.get("hello") == {"value": 2}

    assert db_path.exists()
    quarantined = [
        p for p in tmp_path.iterdir()
        if p.name.startswith("flocks.db.corrupt.")
    ]
    assert quarantined, list(tmp_path.iterdir())


def test_is_db_corruption_error_recognizes_known_messages():
    """Both ``NotADBError`` and ``DatabaseError`` variants are flagged as corruption."""
    not_a_db = sqlite3.DatabaseError("file is not a database")
    malformed = sqlite3.DatabaseError("database disk image is malformed")
    encrypted = sqlite3.DatabaseError("file is encrypted or is not a database")
    benign = sqlite3.OperationalError("database is locked")
    other = ValueError("file is not a database")  # non-sqlite exception

    assert Storage._is_db_corruption_error(not_a_db) is True
    assert Storage._is_db_corruption_error(malformed) is True
    assert Storage._is_db_corruption_error(encrypted) is True
    assert Storage._is_db_corruption_error(benign) is False
    assert Storage._is_db_corruption_error(other) is False


def test_is_db_corruption_error_recognizes_sqlite_error_code():
    """Recognise corruption via the ``SQLITE_NOTADB`` error code, not just text."""
    notadb_code = getattr(sqlite3, "SQLITE_NOTADB", None)
    if notadb_code is None:
        pytest.skip("SQLite build does not expose SQLITE_NOTADB")
    exc = sqlite3.DatabaseError("custom wrapper text")
    exc.sqlite_errorcode = notadb_code
    assert Storage._is_db_corruption_error(exc) is True


def test_file_has_invalid_sqlite_header_only_flags_non_sqlite(tmp_path):
    """Empty / missing / SQLite-magic files must not be treated as corrupt."""
    missing = tmp_path / "missing.db"
    empty = tmp_path / "empty.db"
    empty.touch()
    sqlite_like = tmp_path / "ok.db"
    sqlite_like.write_bytes(Storage._SQLITE_MAGIC + b"\x00" * 100)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite file")

    assert Storage._file_has_invalid_sqlite_header(missing) is False
    assert Storage._file_has_invalid_sqlite_header(empty) is False
    assert Storage._file_has_invalid_sqlite_header(sqlite_like) is False
    assert Storage._file_has_invalid_sqlite_header(bad) is True


# ---------------------------------------------------------------------------
# Durability: WAL checkpoint on shutdown / startup, fork-safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_shutdown_truncates_wal_file(tmp_path):
    """``Storage.shutdown()`` must drain the WAL so next start needs no recovery.

    This is the missing counterpart to ``init()`` and the root-cause fix for
    the ``file is not a database`` corruption pattern: a SIGKILL during WAL
    recovery is what writes a half-baked main-DB header page.  After a clean
    shutdown the ``-wal`` file must be zero-length.

    To stop SQLite's automatic *last-connection checkpoint* from masking the
    test (it normally drains the WAL whenever the last open connection
    closes), we keep a holder connection open across the writes — exactly
    like the long-lived ``TaskStore`` / ``session_binding`` connections do
    in production — so the WAL stays non-empty until ``shutdown()`` runs.
    """
    import aiosqlite

    db_path = tmp_path / "shutdown.db"
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)

        holder = await aiosqlite.connect(
            str(db_path), timeout=Storage._sqlite_timeout_s
        )
        try:
            await Storage.configure_connection(holder)
            for i in range(50):
                await Storage.set(f"key_{i}", {"i": i})

            wal_file = db_path.with_name(db_path.name + "-wal")
            assert wal_file.exists(), "WAL mode should produce a -wal sidecar"
            assert wal_file.stat().st_size > 0
        finally:
            await holder.close()
            # After the holder closes, SQLite *may* auto-checkpoint, but our
            # contract is that ``shutdown()`` truncates the WAL deterministically
            # regardless of what the kernel-side autoflush did.

        await Storage.shutdown()

        wal_file = db_path.with_name(db_path.name + "-wal")
        # The WAL is now either fully removed or truncated to zero bytes —
        # both are acceptable post-checkpoint states.
        if wal_file.exists():
            assert wal_file.stat().st_size == 0, (
                "wal_checkpoint(TRUNCATE) should leave the WAL empty so the "
                "next process start does not need to do WAL recovery"
            )
        assert Storage._initialized is False
        assert Storage._init_pid is None


@pytest.mark.asyncio
async def test_storage_init_truncates_residual_wal_from_previous_run(tmp_path):
    """A leftover WAL from a SIGKILL'd previous process is drained on startup.

    Simulates the worst-case sequence:
      1. Process writes data while a long-lived connection keeps the WAL open.
      2. Process is SIGKILL'd before shutdown can checkpoint — the holder
         connection's file descriptor is dropped without close().
      3. Next process starts → must drain the residual WAL *before* a
         second crash creates a half-recovered main-DB.
    """
    import aiosqlite

    db_path = tmp_path / "startup.db"
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)
        holder = await aiosqlite.connect(
            str(db_path), timeout=Storage._sqlite_timeout_s
        )
        try:
            await Storage.configure_connection(holder)
            for i in range(50):
                await Storage.set(f"k{i}", {"i": i})

            wal_file = db_path.with_name(db_path.name + "-wal")
            assert wal_file.stat().st_size > 0, "expected WAL to grow"
        finally:
            # Simulate SIGKILL: close the holder so the test can clean up,
            # but skip the explicit ``Storage.shutdown()``.
            await holder.close()

        Storage._initialized = False
        Storage._init_pid = None
        Storage._db_path = None

    # Fresh ``init`` on the same file should drain any residual WAL.
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)
        wal_file = db_path.with_name(db_path.name + "-wal")
        if wal_file.exists():
            assert wal_file.stat().st_size == 0, (
                "Startup checkpoint should have truncated the residual WAL"
            )


@pytest.mark.asyncio
async def test_storage_detects_fork_and_reinitialises(tmp_path):
    """``_ensure_init`` must rebuild Storage state after a ``fork()``.

    Sharing an open SQLite connection across processes is documented to
    corrupt the DB.  We simulate ``fork()`` by mutating ``_init_pid`` to a
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
async def test_storage_checkpoint_raises_when_sqlite_reports_busy(tmp_path):
    """``PRAGMA wal_checkpoint`` returns ``busy=1`` *without* raising.

    Reproduces the silent-failure mode flagged by review: a concurrent
    reader holds a shared lock, so ``TRUNCATE`` cannot complete and SQLite
    returns ``(1, log_pages, 0)`` from the PRAGMA — no SQL exception.
    The contract is that ``Storage._checkpoint`` surfaces this as
    :class:`CheckpointBusyError` so callers cannot mistakenly report
    success.
    """
    import aiosqlite

    db_path = tmp_path / "busy.db"
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)

        # Hold a long-running reader transaction to keep a shared lock.
        # SQLite's ``TRUNCATE`` mode requires a brief exclusive moment,
        # which this reader prevents → busy=1.
        reader = await aiosqlite.connect(
            str(db_path), timeout=Storage._sqlite_timeout_s
        )
        try:
            await reader.execute("BEGIN")
            await reader.execute("SELECT * FROM storage")
            # Generate at least one WAL frame so the checkpoint has
            # something to flush (otherwise it can trivially succeed).
            await Storage.set("contend:key", {"v": 1})

            with pytest.raises(Storage.CheckpointBusyError) as exc_info:
                await Storage._checkpoint(mode="TRUNCATE")

            err = exc_info.value
            assert err.mode == "TRUNCATE"
            # SQLite reports how many pages were *not* drained.
            assert err.log_pages >= 0
            assert err.checkpointed_pages >= 0
        finally:
            await reader.close()
            await Storage.shutdown()


@pytest.mark.asyncio
async def test_storage_shutdown_reports_unfinished_on_persistent_busy(tmp_path):
    """A persistently busy checkpoint must not be logged as "done".

    We replace ``_checkpoint`` with a stub that always raises
    :class:`CheckpointBusyError` and assert that ``shutdown()``:
      * does not raise,
      * does not log the success path, and
      * still clears the in-memory state (since the process is exiting).
    """
    db_path = tmp_path / "unfinished.db"
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)

        events: list[str] = []
        real_info = Storage._log.info
        real_warn = Storage._log.warn

        def _spy_info(event, *_args, **_kwargs):
            events.append(f"info:{event}")
            return real_info(event, *_args, **_kwargs)

        def _spy_warn(event, *_args, **_kwargs):
            events.append(f"warn:{event}")
            return real_warn(event, *_args, **_kwargs)

        async def _always_busy(*, mode="TRUNCATE"):
            raise Storage.CheckpointBusyError(mode, log_pages=42, checkpointed_pages=0)

        with patch.object(Storage._log, "info", side_effect=_spy_info), \
             patch.object(Storage._log, "warn", side_effect=_spy_warn), \
             patch.object(Storage, "_checkpoint", side_effect=_always_busy), \
             patch.object(Storage, "_shutdown_checkpoint_attempts", 2), \
             patch.object(Storage, "_shutdown_checkpoint_backoff_s", 0.0):
            await Storage.shutdown()

        assert any("warn:storage.shutdown.checkpoint.busy" in e for e in events), events
        assert any("warn:storage.shutdown.checkpoint.unfinished" in e for e in events), events
        assert not any("info:storage.shutdown.checkpoint.done" in e for e in events), (
            "shutdown must not log success when the WAL was not truncated"
        )
        assert Storage._initialized is False
        assert Storage._init_pid is None


@pytest.mark.asyncio
async def test_storage_init_raises_when_quarantine_fails_on_invalid_header(tmp_path):
    """Invalid-header fast-path must abort init when quarantine fails.

    If we keep going after a failed rename, SQLite will open the bad
    file and delete the adjacent WAL/SHM sidecars we wanted to preserve
    for offline recovery — defeating the purpose of the fast-path
    pre-flight check.
    """
    db_path = tmp_path / "garbage.db"
    db_path.write_bytes(b"This is garbage, not a sqlite file\n")
    db_path.with_name("garbage.db-wal").write_bytes(b"wal payload")

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None), \
         patch.object(Storage, "_quarantine_corrupt_db", return_value=None):
        with pytest.raises(Storage.StorageError, match="could not be quarantined"):
            await Storage.init(db_path)

    # Sidecars must still be present and untouched.
    assert db_path.exists()
    assert db_path.with_name("garbage.db-wal").exists()
    assert db_path.with_name("garbage.db-wal").read_bytes() == b"wal payload"
