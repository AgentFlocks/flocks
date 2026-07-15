"""
Tests for storage module
"""

import asyncio
from contextlib import asynccontextmanager
import os
import shutil
import sqlite3
import threading
import pytest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from flocks.project.instance import Instance
from flocks.storage.storage import Storage
from flocks.task.store import TaskStore
from flocks.workflow.store import WorkflowStore


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
        # Fresh DB is now usable before the patched global state is restored.
        await Storage.set("hello", {"value": 1})
        assert await Storage.get("hello") == {"value": 1}

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "flocks.db" in siblings
    corrupt_files = [name for name in siblings if ".corrupt." in name]
    assert any(name.startswith("flocks.db.corrupt.") for name in corrupt_files), siblings
    quarantined_main = next(
        tmp_path / name
        for name in corrupt_files
        if name.startswith("flocks.db.corrupt.")
        and not name.endswith(("-wal", "-shm"))
    )
    assert quarantined_main.with_name(quarantined_main.name + "-wal").exists()
    assert quarantined_main.with_name(quarantined_main.name + "-shm").exists()


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


@pytest.mark.asyncio
async def test_storage_get_defers_corruption_recovery_until_restart(tmp_path):
    """A request-time read must not replace a DB while the server is running."""
    db_path = tmp_path / "flocks.db"

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(
             Storage,
             "_quarantine_corrupt_db",
             side_effect=AssertionError("request-time recovery must not quarantine the live DB"),
         ):
        await Storage.init(db_path)
        await Storage.set("hello", {"value": "before"})

        corrupt_payload = Storage._SQLITE_MAGIC + b"\xff" * 2048
        db_path.write_bytes(corrupt_payload)

        with pytest.raises(sqlite3.DatabaseError):
            await Storage.get("hello")

        assert db_path.read_bytes() == corrupt_payload

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "flocks.db" in siblings
    assert not any(".corrupt." in name for name in siblings), siblings


def test_try_sqlite_recover_installs_recovered_db(tmp_path):
    """The lightweight `.recover` path should install a readable recovered DB."""
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not available")

    quarantined = tmp_path / "flocks.db.corrupt.test"
    target = tmp_path / "flocks.db"
    conn = sqlite3.connect(quarantined)
    try:
        conn.execute(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("hello", '{"value": "recovered"}', "json", "old", "old"),
        )
        conn.execute("CREATE TABLE lost_and_found (value TEXT NOT NULL)")
        conn.execute("INSERT INTO lost_and_found VALUES ('business-data')")
        conn.commit()
    finally:
        conn.close()

    assert Storage._try_sqlite_recover_sync(quarantined, target) == target
    recovered = sqlite3.connect(target)
    try:
        assert recovered.execute(
            "SELECT value FROM storage WHERE key = ?",
            ("hello",),
        ).fetchone()[0] == '{"value": "recovered"}'
        assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert recovered.execute("SELECT value FROM lost_and_found").fetchone() == (
            "business-data",
        )
    finally:
        recovered.close()


def test_sqlite_recover_reads_wal_paired_with_quarantined_main(tmp_path):
    """A committed WAL must stay paired with the renamed main DB during recovery."""
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not available")

    source = tmp_path / "source.db"
    source_conn = sqlite3.connect(source)
    try:
        assert source_conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        source_conn.execute("PRAGMA wal_autocheckpoint=0")
        source_conn.execute(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        source_conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("wal-key", '{"value": "committed"}', "json", "old", "old"),
        )
        source_conn.commit()

        db_path = tmp_path / "flocks.db"
        shutil.copy2(source, db_path)
        shutil.copy2(source.with_name("source.db-wal"), db_path.with_name("flocks.db-wal"))

        quarantined = Storage._quarantine_corrupt_db(db_path)
        assert quarantined is not None
        paired_wal = quarantined.with_name(quarantined.name + "-wal")
        assert paired_wal.exists()
        assert not db_path.with_name("flocks.db-wal").exists()
        quarantined_bytes = quarantined.read_bytes()
        wal_bytes = paired_wal.read_bytes()

        assert Storage._try_sqlite_recover_sync(quarantined, db_path) == db_path
        assert quarantined.read_bytes() == quarantined_bytes
        assert paired_wal.read_bytes() == wal_bytes
        recovered = sqlite3.connect(db_path)
        try:
            assert recovered.execute(
                "SELECT value FROM storage WHERE key = ?",
                ("wal-key",),
            ).fetchone()[0] == '{"value": "committed"}'
        finally:
            recovered.close()
    finally:
        source_conn.close()


def test_quarantine_rolls_back_main_when_wal_cannot_be_paired(tmp_path, monkeypatch):
    """A WAL rename failure must abort instead of recovering an incomplete snapshot."""

    db_path = tmp_path / "flocks.db"
    wal_path = tmp_path / "flocks.db-wal"
    main_payload = b"SQLite format 3\x00" + b"main"
    wal_payload = b"committed transactions"
    db_path.write_bytes(main_payload)
    wal_path.write_bytes(wal_payload)

    real_rename = Path.rename

    def fail_wal_rename(path: Path, target: Path) -> Path:
        if path == wal_path:
            raise OSError("simulated WAL rename failure")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_wal_rename)

    assert Storage._quarantine_corrupt_db(db_path) is None
    assert db_path.read_bytes() == main_payload
    assert wal_path.read_bytes() == wal_payload
    assert not list(tmp_path.glob("flocks.db.corrupt.*"))


@pytest.mark.asyncio
async def test_recover_corrupt_primary_refuses_online_replacement(tmp_path):
    """No partial connection barrier may advertise unsafe online recovery."""
    db_path = tmp_path / "flocks.db"

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None), \
         patch.object(Storage, "_corruption_recovery_generation", 0), \
         patch.object(
             Storage,
             "_quarantine_corrupt_db",
             side_effect=AssertionError("online recovery must fail before quarantine"),
         ):
        try:
            await Storage.init(db_path)
            inode_before = db_path.stat().st_ino
            async with Storage.connect(db_path) as held:
                await held.execute("CREATE TABLE recovery_probe (id TEXT PRIMARY KEY)")
                await held.execute("INSERT INTO recovery_probe VALUES ('before')")
                await held.commit()

                with pytest.raises(Storage.StorageError, match="while Flocks is running"):
                    await Storage.recover_corrupt_db(
                        db_path,
                        action="test.explicit_recovery",
                        exc=sqlite3.DatabaseError("database disk image is malformed"),
                    )

                await held.execute("INSERT INTO recovery_probe VALUES ('after')")
                await held.commit()

            assert db_path.stat().st_ino == inode_before
            with sqlite3.connect(db_path) as fresh:
                rows = fresh.execute("SELECT id FROM recovery_probe ORDER BY id").fetchall()
            assert rows == [("after",), ("before",)]
        finally:
            await Storage.shutdown()


@pytest.mark.asyncio
async def test_live_database_path_disappearance_fails_closed(tmp_path):
    """A moved live DB must not cause _ensure_init to create a second inode."""
    from flocks.channel.inbound import session_binding

    await session_binding.close_binding_db()
    db_path = tmp_path / "flocks.db"
    archived_path = tmp_path / "flocks.db.moved"

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        try:
            await Storage.init(db_path)
            held = await session_binding._get_db()
            await held.execute("CREATE TABLE live_probe (value TEXT)")
            await held.execute("INSERT INTO live_probe VALUES ('before')")
            await held.commit()

            db_path.rename(archived_path)
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(db_path.name + suffix)
                if sidecar.exists():
                    sidecar.rename(archived_path.with_name(archived_path.name + suffix))

            with pytest.raises(Storage.StorageError, match="active SQLite database disappeared"):
                await Storage.set("must-not-create", {"value": 1})

            assert not db_path.exists()
            with pytest.raises(ValueError, match="no active connection"):
                await held.execute("SELECT 1")
        finally:
            await session_binding.close_binding_db()
            await Storage.shutdown()


@pytest.mark.asyncio
async def test_live_database_inode_replacement_fails_closed(tmp_path):
    """An atomic restore over the live path must not mix old and new DB handles."""
    from flocks.channel.inbound import session_binding

    await session_binding.close_binding_db()
    db_path = tmp_path / "flocks.db"
    archived_path = tmp_path / "flocks.db.before-restore"

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None), \
         patch.object(Storage, "_db_identity", None):
        try:
            await Storage.init(db_path)
            original_identity = Storage._db_identity
            held = await session_binding._get_db()
            await held.execute("CREATE TABLE live_probe (value TEXT)")
            await held.execute("INSERT INTO live_probe VALUES ('old-inode')")
            await held.commit()

            db_path.rename(archived_path)
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(db_path.name + suffix)
                if sidecar.exists():
                    sidecar.rename(archived_path.with_name(archived_path.name + suffix))
            with sqlite3.connect(db_path) as replacement:
                replacement.execute("CREATE TABLE restored_probe(value TEXT)")
                replacement.execute("INSERT INTO restored_probe VALUES ('new-inode')")
                replacement.commit()

            assert Storage._file_identity(db_path) != original_identity
            with pytest.raises(Storage.StorageError, match="different file identity"):
                async with Storage.connect(db_path):
                    pass
            with pytest.raises(Storage.StorageError, match="different file identity"):
                Storage.connect_sync(db_path)
            with pytest.raises(Storage.StorageError, match="replaced on disk"):
                await Storage.set("must-not-write", {"value": 1})
            with pytest.raises(ValueError, match="no active connection"):
                await held.execute("SELECT 1")

            with sqlite3.connect(db_path) as replacement:
                assert replacement.execute("SELECT value FROM restored_probe").fetchone() == (
                    "new-inode",
                )
                assert not replacement.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='storage'"
                ).fetchone()
        finally:
            await session_binding.close_binding_db()
            await Storage.shutdown()


@pytest.mark.asyncio
async def test_cancelled_recovery_waits_for_worker_to_finish():
    """Cancellation must not release callers while a recovery thread still mutates files."""
    started = threading.Event()
    release = threading.Event()

    def blocked_worker() -> None:
        started.set()
        release.wait(timeout=5)

    worker = asyncio.create_task(asyncio.to_thread(blocked_worker))
    waiter = asyncio.create_task(Storage._wait_for_recovery_worker_on_cancel(worker))
    assert await asyncio.to_thread(started.wait, 2)

    waiter.cancel()
    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert worker.done()


@pytest.mark.asyncio
async def test_cancelled_startup_waits_for_recovery_thread(tmp_path, monkeypatch):
    """The real Storage.init recovery branch must not leave a detached installer thread."""
    db_path = tmp_path / "flocks.db"
    db_path.write_bytes(Storage._SQLITE_MAGIC + b"\xff" * 4096)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_recovery(_quarantined: Path, _target: Path) -> None:
        started.set()
        release.wait(timeout=5)
        finished.set()

    monkeypatch.setattr(Storage, "_try_sqlite_recover_sync", staticmethod(blocked_recovery))
    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None), \
         patch.object(Storage, "_db_identity", None):
        init_task = asyncio.create_task(Storage.init(db_path))
        assert await asyncio.to_thread(started.wait, 2)

        init_task.cancel()
        await asyncio.sleep(0)
        assert not init_task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await init_task
        assert finished.is_set()


@pytest.mark.asyncio
async def test_storage_init_recovers_real_malformed_sqlite_file(tmp_path):
    """Startup should recover a real DB that fails SQLite integrity checks."""
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not available")

    db_path = tmp_path / "flocks.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA page_size=4096")
        conn.execute("VACUUM")
        conn.execute(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE junk (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("hello", '{"value": "survived"}', "json", "old", "old"),
        )
        blob = os.urandom(3500)
        for _ in range(350):
            conn.execute("INSERT INTO junk (payload) VALUES (?)", (blob,))
        conn.commit()
    finally:
        conn.close()

    page_size = 4096
    page_count = db_path.stat().st_size // page_size
    with db_path.open("r+b") as fh:
        fh.seek((page_count - 3) * page_size)
        fh.write(b"BROKEN_PAGE_FOR_RECOVERY_TEST" + b"\xff" * 256)

    ok, detail = Storage._integrity_check_sync(db_path)
    assert ok is False
    assert "malformed" in detail.lower()

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)
        assert await Storage.get("hello") == {"value": "survived"}
        await Storage.shutdown()

    assert Storage._integrity_check_sync(db_path) == (True, "ok")
    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert any(name.startswith("flocks.db.corrupt.") for name in siblings), siblings
    assert "flocks.db.recover.sql" in siblings


@pytest.mark.asyncio
async def test_request_time_corruption_does_not_call_recover_corrupt_db(tmp_path):
    """The generic request wrapper must surface corruption without replacing files."""

    async def corrupt_operation():
        raise sqlite3.DatabaseError("database disk image is malformed")

    with patch.object(Storage, "recover_corrupt_db", new_callable=AsyncMock) as recover, \
         patch.object(Storage._log, "error") as error_log:
        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            await Storage._run_with_corruption_recovery(
                corrupt_operation,
                db_path=tmp_path / "flocks.db",
                action="test.request_read",
            )

    recover.assert_not_awaited()
    assert error_log.call_args.args[0] == "storage.corruption.deferred_to_restart"


@pytest.mark.asyncio
async def test_request_time_corruption_is_recovered_only_after_restart(tmp_path):
    """Close the fail-closed loop: no online swap, then startup recovery succeeds."""
    db_path = tmp_path / "flocks.db"

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(Storage, "_init_pid", None):
        await Storage.init(db_path)
        await Storage.set("before", {"value": 1})

        corrupt_payload = Storage._SQLITE_MAGIC + b"\xff" * 4096
        db_path.write_bytes(corrupt_payload)
        with pytest.raises(sqlite3.DatabaseError):
            await Storage.get("before")
        assert db_path.read_bytes() == corrupt_payload
        assert not list(tmp_path.glob("flocks.db.corrupt.*"))

        await Storage.shutdown()
        await Storage.init(db_path)
        await Storage.set("after", {"value": 2})
        assert await Storage.get("after") == {"value": 2}
        await Storage.shutdown()

    assert list(tmp_path.glob("flocks.db.corrupt.*"))


@pytest.mark.asyncio
async def test_task_store_init_recovers_corrupt_tasks_db(tmp_path):
    """TaskStore should quarantine tasks.db corruption and keep task center bootable."""
    db_path = tmp_path / "flocks.db"
    tasks_db = tmp_path / "tasks.db"
    tasks_db.write_bytes(Storage._SQLITE_MAGIC + b"\xff" * 2048)

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(TaskStore, "_initialized", False), \
         patch.object(TaskStore, "_conn", None), \
         patch.object(TaskStore, "_init_pid", None):
        await Storage.init(db_path)
        await TaskStore.init()
        await TaskStore.close()

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "tasks.db" in siblings
    assert any(name.startswith("tasks.db.corrupt.") for name in siblings), siblings


@pytest.mark.asyncio
async def test_task_store_init_recovers_corrupt_tasks_db_after_completed_migration(tmp_path, monkeypatch):
    """A corrupt existing tasks.db should not be treated like a missing migrated DB."""
    db_path = tmp_path / "flocks.db"
    tasks_db = tmp_path / "tasks.db"
    tasks_db.write_bytes(Storage._SQLITE_MAGIC + b"\xff" * 2048)

    monkeypatch.setattr(Storage, "_try_sqlite_recover_sync", staticmethod(lambda *_args: None))

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(TaskStore, "_initialized", False), \
         patch.object(TaskStore, "_conn", None), \
         patch.object(TaskStore, "_init_pid", None):
        await Storage.init(db_path)
        await asyncio.to_thread(
            Storage._write_multi_db_migration_marker_sync,
            {
                "version": 1,
                "tasks_migrated": True,
                "task_rows": 1,
            },
        )
        await TaskStore.init()
        await TaskStore.close()

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "tasks.db" in siblings
    assert any(name.startswith("tasks.db.corrupt.") for name in siblings), siblings


@pytest.mark.asyncio
async def test_workflow_store_init_recovers_corrupt_workflow_db(tmp_path):
    """WorkflowStore should quarantine workflow.db corruption and rebuild tables."""
    db_path = tmp_path / "flocks.db"
    workflow_db = tmp_path / "workflow.db"
    workflow_db.write_bytes(Storage._SQLITE_MAGIC + b"\xff" * 2048)

    with patch.object(Storage, "_initialized", False), \
         patch.object(Storage, "_db_path", None), \
         patch.object(WorkflowStore, "_initialized", False), \
         patch.object(WorkflowStore, "_conn", None), \
         patch.object(WorkflowStore, "_init_pid", None), \
         patch.object(WorkflowStore, "_db_path", None):
        await Storage.init(db_path)
        await WorkflowStore.init()
        await WorkflowStore.close()

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert "workflow.db" in siblings
    assert any(name.startswith("workflow.db.corrupt.") for name in siblings), siblings


@pytest.mark.asyncio
async def test_instance_provide_drops_failed_context_task(monkeypatch):
    """A failed project-context initialization must not poison later requests."""
    directory = "/tmp/flocks-instance-retry"
    Instance._cache.pop(directory, None)
    calls = {"count": 0}

    async def fake_from_directory(cls, requested_directory):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return {
            "project": SimpleNamespace(id="project"),
            "sandbox": requested_directory,
        }

    monkeypatch.setattr(
        "flocks.project.instance.Project.from_directory",
        classmethod(fake_from_directory),
    )

    with pytest.raises(sqlite3.DatabaseError):
        await Instance.provide(directory=directory, fn=lambda: "unreachable")

    assert directory not in Instance._cache
    assert await Instance.provide(directory=directory, fn=lambda: "ok") == "ok"
    assert calls["count"] == 2
    Instance._cache.pop(directory, None)


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
