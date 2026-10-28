from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.workflow.store import WorkflowStore


def _reset_state() -> None:
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    Storage._init_pid = None
    WorkflowStore._initialized = False
    WorkflowStore._conn = None
    WorkflowStore._completion_conn = None
    WorkflowStore._init_pid = None
    WorkflowStore._db_path = None
    WorkflowStore._completion_lock = None
    WorkflowStore._writer_open_lock = None


@pytest.fixture(autouse=True)
async def isolated_workflow_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    _reset_state()
    yield
    await WorkflowStore.close()
    _reset_state()


@pytest.mark.asyncio
async def test_concurrent_initialization_keeps_one_connection_pair(monkeypatch) -> None:
    import aiosqlite

    await Storage.init()
    real_connect = aiosqlite.connect
    opened = []

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if Path(args[0]) == WorkflowStore.get_db_path():
            opened.append(connection)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", tracked_connect)
    try:
        await asyncio.gather(*(WorkflowStore.init() for _ in range(10)))
        assert len(opened) == 2
        assert WorkflowStore._conn is opened[0]
        assert WorkflowStore._completion_conn is opened[1]
    finally:
        await WorkflowStore.close()
        for connection in opened:
            await connection.close()


@pytest.mark.asyncio
async def test_cancelled_initialization_closes_connection(monkeypatch) -> None:
    await Storage.init()
    entered = asyncio.Event()

    async def blocked_migration():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(WorkflowStore, "_migrate_legacy_kv", blocked_migration)
    task = asyncio.create_task(WorkflowStore.init())
    await asyncio.wait_for(entered.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert WorkflowStore._conn is None
    assert WorkflowStore._completion_conn is None
    assert not WorkflowStore._initialized


@pytest.mark.asyncio
async def test_workflow_store_records_execution_steps_config_and_kv() -> None:
    await WorkflowStore.init()

    await WorkflowStore.upsert_execution(
        {
            "id": "exec-1",
            "workflowId": "wf-1",
            "status": "running",
            "startedAt": 100,
            "triggerId": "trigger-1",
            "triggerType": "schedule",
        }
    )
    await WorkflowStore.upsert_execution(
        {
            "id": "exec-2",
            "workflowId": "wf-1",
            "status": "success",
            "startedAt": 200,
        }
    )
    await WorkflowStore.upsert_execution(
        {
            "id": "exec-other",
            "workflowId": "wf-other",
            "status": "success",
            "startedAt": 300,
        }
    )

    rows = await WorkflowStore.list_executions("wf-1", limit=10)
    assert [row["id"] for row in rows] == ["exec-2", "exec-1"]
    filtered = await WorkflowStore.list_executions(
        "wf-1",
        limit=10,
        trigger_id="trigger-1",
        trigger_type="schedule",
    )
    assert [row["id"] for row in filtered] == ["exec-1"]

    await WorkflowStore.record_steps(
        "exec-1",
        [
            (1, {"node_id": "n1", "outputs": {"ok": 1}}),
            (2, {"node_id": "n2", "outputs": {"ok": 2}}),
        ],
    )
    steps, total = await WorkflowStore.list_steps("exec-1", offset=1, limit=1)
    assert total == 2
    assert steps == [{"node_id": "n2", "outputs": {"ok": 2}}]

    await WorkflowStore.put_config("wf-1", {"enabled": True}, kind="workflow_poller_config")
    assert await WorkflowStore.get_config("wf-1", kind="workflow_poller_config") == {"enabled": True}
    assert await WorkflowStore.list_configs(kind="workflow_poller_config") == [("wf-1", {"enabled": True})]

    await WorkflowStore.kv_put("workflow_runtime/wf-1", {"status": "active"})
    assert await WorkflowStore.kv_get("workflow_runtime/wf-1") == {"status": "active"}
    assert await WorkflowStore.kv_list_keys("workflow_runtime/") == ["workflow_runtime/wf-1"]


@pytest.mark.asyncio
async def test_workflow_store_increment_stats_is_atomic_for_concurrent_updates() -> None:
    await WorkflowStore.init()
    updates = [(idx % 3 != 0, 1.0) for idx in range(60)]

    await asyncio.gather(
        *(
            WorkflowStore.increment_stats("wf-concurrent", success=success, duration=duration)
            for success, duration in updates
        )
    )

    stats = await WorkflowStore.get_stats("wf-concurrent")
    assert stats is not None
    assert stats["callCount"] == 60
    assert stats["successCount"] == sum(1 for success, _ in updates if success)
    assert stats["errorCount"] == sum(1 for success, _ in updates if not success)
    assert stats["totalRuntime"] == pytest.approx(60.0)
    assert stats["avgRuntime"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_complete_execution_writes_steps_and_summary_with_one_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await WorkflowStore.init()
    db = await WorkflowStore.raw_completion_db()
    commit_count = 0
    original_commit = db.commit

    async def counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        await original_commit()

    monkeypatch.setattr(db, "commit", counted_commit)

    await WorkflowStore.complete_execution(
        {
            "id": "exec-complete",
            "workflowId": "wf-complete",
            "status": "success",
            "startedAt": 100,
            "finishedAt": 350,
            "duration": 0.25,
            "executionLog": [],
        },
        steps=[
            (1, {"node_id": "n1", "outputs": {"ok": 1}}),
            (2, {"node_id": "n2", "outputs": {"ok": 2}}),
        ],
    )

    assert commit_count == 1
    execution = await WorkflowStore.get_execution("exec-complete")
    assert execution is not None
    assert execution["status"] == "success"
    steps, total = await WorkflowStore.list_steps("exec-complete")
    assert total == 2
    assert [step["node_id"] for step in steps] == ["n1", "n2"]
    assert await WorkflowStore.get_stats("wf-complete") is None


@pytest.mark.asyncio
async def test_complete_execution_reduces_28_step_writes_to_four_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await WorkflowStore.init()
    db = await WorkflowStore.raw_completion_db()
    commit_count = 0
    original_commit = db.commit

    async def counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        await original_commit()

    monkeypatch.setattr(db, "commit", counted_commit)
    steps = [
        (index, {"node_id": f"node-{index}", "outputs": {"ok": True}})
        for index in range(1, 8)
    ]

    await asyncio.gather(
        *(
            WorkflowStore.complete_execution(
                {
                    "id": f"exec-{index}",
                    "workflowId": "wf-trigger",
                    "status": "success",
                    "startedAt": index + 1,
                    "finishedAt": index + 2,
                    "duration": 0.01,
                    "executionLog": [],
                    "stepCount": 7,
                },
                steps,
            )
            for index in range(4)
        )
    )

    assert commit_count == 4
    executions = await WorkflowStore.list_executions("wf-trigger", limit=50)
    assert len(executions) == 4
    assert all(execution["executionLog"] == [] for execution in executions)
    for index in range(4):
        persisted_steps, total = await WorkflowStore.list_steps(f"exec-{index}")
        assert total == 7
        assert [step["node_id"] for step in persisted_steps] == [
            f"node-{step_index}" for step_index in range(1, 8)
        ]
    assert await WorkflowStore.get_stats("wf-trigger") is None


@pytest.mark.asyncio
async def test_complete_execution_rolls_back_partial_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await WorkflowStore.init()
    db = await WorkflowStore.raw_completion_db()
    original_commit = db.commit

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await WorkflowStore.complete_execution(
            {
                "id": "exec-rollback",
                "workflowId": "wf-rollback",
                "status": "success",
                "startedAt": 1,
                "finishedAt": 2,
                "duration": 0.01,
                "executionLog": [],
                "stepCount": 1,
            },
            [(1, {"node_id": "node-1", "outputs": {"ok": True}})],
        )

    monkeypatch.setattr(db, "commit", original_commit)
    assert await WorkflowStore.get_execution("exec-rollback") is None
    persisted_steps, total = await WorkflowStore.list_steps("exec-rollback")
    assert persisted_steps == []
    assert total == 0
    assert await WorkflowStore.get_stats("wf-rollback") is None


@pytest.mark.asyncio
async def test_complete_execution_rolls_back_cancelled_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await WorkflowStore.init()
    db = await WorkflowStore.raw_completion_db()
    original_commit = db.commit

    async def cancel_commit() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(db, "commit", cancel_commit)

    with pytest.raises(asyncio.CancelledError):
        await WorkflowStore.complete_execution(
            {
                "id": "exec-cancelled-commit",
                "workflowId": "wf-cancelled-commit",
                "status": "success",
                "startedAt": 1,
                "finishedAt": 2,
                "executionLog": [],
                "stepCount": 1,
            },
            [(1, {"node_id": "node-1", "outputs": {"ok": True}})],
        )

    monkeypatch.setattr(db, "commit", original_commit)
    await WorkflowStore.complete_execution(
        {
            "id": "exec-after-cancel",
            "workflowId": "wf-cancelled-commit",
            "status": "success",
            "startedAt": 3,
            "finishedAt": 4,
            "executionLog": [],
            "stepCount": 1,
        },
        [(1, {"node_id": "node-2", "outputs": {"ok": True}})],
    )

    assert await WorkflowStore.get_execution("exec-cancelled-commit") is None
    assert await WorkflowStore.get_execution("exec-after-cancel") is not None


@pytest.mark.asyncio
async def test_pid_change_drops_inherited_connections_without_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await WorkflowStore.init()
    original_db = await WorkflowStore.raw_db()
    original_completion_db = await WorkflowStore.raw_completion_db()
    original_db_close = original_db.close
    original_completion_db_close = original_completion_db.close
    db_close = AsyncMock()
    completion_db_close = AsyncMock()
    monkeypatch.setattr(original_db, "close", db_close)
    monkeypatch.setattr(original_completion_db, "close", completion_db_close)
    original_lock = WorkflowStore._completion_lock
    WorkflowStore._init_pid = -1

    try:
        refreshed_connection = await WorkflowStore.raw_completion_db()

        assert refreshed_connection is not original_completion_db
        assert WorkflowStore._completion_lock is not original_lock
        assert WorkflowStore._init_pid == os.getpid()
        db_close.assert_not_awaited()
        completion_db_close.assert_not_awaited()
    finally:
        await original_db_close()
        await original_completion_db_close()


@pytest.mark.asyncio
async def test_open_read_snapshot_cannot_poison_workflow_writes() -> None:
    await WorkflowStore.upsert_execution(
        {"id": "before", "workflowId": "wf-snapshot", "status": "success", "startedAt": 1}
    )
    reader = await WorkflowStore._db()
    writer = await WorkflowStore.raw_db()
    # Leave a SELECT on the first row, reproducing the original async cursor
    # window. A completion commit advances the WAL beyond this read snapshot.
    cursor = await reader.execute("SELECT * FROM workflow_executions")
    try:
        await WorkflowStore.complete_execution(
            {"id": "completed", "workflowId": "wf-snapshot", "status": "success", "startedAt": 2},
            [(1, {"node_id": "done"})],
        )
        await WorkflowStore.upsert_execution(
            {"id": "after", "workflowId": "wf-snapshot", "status": "running", "startedAt": 3}
        )
        await WorkflowStore.increment_stats("wf-snapshot", success=True, duration=1)
        assert await WorkflowStore.trim_executions("wf-snapshot", keep=2) == ["before"]
        assert not writer.in_transaction
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await reader.execute("DELETE FROM workflow_executions")
    finally:
        await cursor.close()
    assert await WorkflowStore.get_execution("after") is not None
    assert (await WorkflowStore.get_stats("wf-snapshot"))["callCount"] == 1


@pytest.mark.asyncio
async def test_raw_database_remains_writable_without_promoting_read_snapshots() -> None:
    db = await WorkflowStore.raw_db()
    await db.execute(
        "INSERT INTO workflow_stats(workflow_id,call_count) VALUES (?, ?)", ("wf-raw", 4)
    )
    await db.commit()
    await WorkflowStore.increment_stats("wf-raw", success=True, duration=1)
    assert (await WorkflowStore.get_stats("wf-raw"))["callCount"] == 5


@pytest.mark.asyncio
async def test_mixed_execution_stats_config_and_history_writes_are_serialized() -> None:
    async def run(index: int) -> None:
        exec_id = f"mixed-{index}"
        await WorkflowStore.upsert_execution(
            {"id": exec_id, "workflowId": "wf-mixed", "status": "running", "startedAt": index + 1}
        )
        await asyncio.gather(
            WorkflowStore.complete_execution(
                {"id": exec_id, "workflowId": "wf-mixed", "status": "success", "startedAt": index + 1},
                [(1, {"node_id": "done"})],
            ),
            WorkflowStore.increment_stats("wf-mixed", success=True, duration=0.5),
            WorkflowStore.kv_put(f"workflow_runtime/{exec_id}", {"ok": True}),
            WorkflowStore.list_executions("wf-mixed"),
        )
        await WorkflowStore.trim_executions("wf-mixed", keep=100)

    await asyncio.gather(*(run(index) for index in range(30)))
    assert len(await WorkflowStore.list_executions("wf-mixed")) == 30
    assert (await WorkflowStore.get_stats("wf-mixed"))["callCount"] == 30
    assert len(await WorkflowStore.kv_list_keys("workflow_runtime/")) == 30
    for index in range(30):
        steps, total = await WorkflowStore.list_steps(f"mixed-{index}")
        assert total == 1
        assert steps == [{"node_id": "done"}]


def _busy_snapshot() -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError("database is locked")
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    exc.sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"
    return exc


@pytest.mark.asyncio
async def test_trim_retries_entire_transaction_after_partial_delete(monkeypatch) -> None:
    for index in range(3):
        await WorkflowStore.complete_execution(
            {"id": f"trim-{index}", "workflowId": "wf-trim", "status": "success", "startedAt": index + 1},
            [(1, {"node_id": "done"})],
        )
    db = await WorkflowStore.raw_db()
    original_execute = db.execute
    selected = 0
    failed = False

    def execute(sql, *args, **kwargs):
        nonlocal selected, failed
        if "LIMIT -1 OFFSET" in sql:
            selected += 1
        if sql.startswith("DELETE FROM workflow_executions") and not failed:
            # One step row was already deleted in this transaction.
            failed = True
            raise _busy_snapshot()
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(WorkflowStore, "_write_retry_base_delay_s", 0)
    assert set(await WorkflowStore.trim_executions("wf-trim", keep=1)) == {"trim-0", "trim-1"}
    assert selected == 2
    assert not db.in_transaction
    assert [row["id"] for row in await WorkflowStore.list_executions("wf-trim")] == ["trim-2"]
    assert await WorkflowStore.list_steps("trim-0") == ([], 0)


@pytest.mark.asyncio
async def test_busy_retries_are_bounded_and_log_extended_error(monkeypatch) -> None:
    import flocks.workflow.store as store_module

    db = await WorkflowStore.raw_db()
    original_execute = db.execute
    attempts = 0
    warnings = []

    def execute(sql, *args, **kwargs):
        nonlocal attempts
        if sql.startswith("INSERT OR REPLACE INTO workflow_configs") or "INTO workflow_configs" in sql:
            attempts += 1
            raise _busy_snapshot()
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(store_module.log, "warning", lambda name, fields: warnings.append((name, fields)))
    monkeypatch.setattr(WorkflowStore, "_write_retry_base_delay_s", 0)
    with pytest.raises(sqlite3.OperationalError):
        await WorkflowStore.put_config("wf-retry", {"enabled": True})
    assert attempts == WorkflowStore._write_retry_attempts
    assert warnings[-1][0] == "workflow.store.transaction_failed"
    assert warnings[-1][1]["sqlite_errorcode"] == 517
    assert not db.in_transaction
    monkeypatch.setattr(db, "execute", original_execute)
    await WorkflowStore.put_config("wf-retry", {"enabled": True})
    assert await WorkflowStore.get_config("wf-retry") == {"enabled": True}


@pytest.mark.asyncio
async def test_external_writer_contention_retries_without_duplicate_stats(monkeypatch) -> None:
    import flocks.workflow.store as store_module

    db = await WorkflowStore.raw_db()
    await db.execute("PRAGMA busy_timeout=1")
    external = sqlite3.connect(WorkflowStore.get_db_path())
    retrying = asyncio.Event()
    warnings = []

    def warning(name, fields):
        warnings.append((name, fields))
        if name == "workflow.store.transaction_retry":
            retrying.set()

    monkeypatch.setattr(store_module.log, "warning", warning)
    external.execute("BEGIN IMMEDIATE")
    try:
        task = asyncio.create_task(WorkflowStore.increment_stats("wf-external", success=True, duration=2))
        await asyncio.wait_for(retrying.wait(), 5)
        external.commit()
        await asyncio.wait_for(task, 5)
    finally:
        external.close()
    stats = await WorkflowStore.get_stats("wf-external")
    assert stats["callCount"] == stats["successCount"] == 1
    assert stats["totalRuntime"] == 2
    assert warnings[0][1]["sqlite_errorcode"] == sqlite3.SQLITE_BUSY
    assert warnings[0][1]["phase"] == "begin"
    assert not db.in_transaction


@pytest.mark.asyncio
async def test_step_batch_retry_keeps_generator_contents(monkeypatch) -> None:
    db = await WorkflowStore.raw_db()
    original_executemany = db.executemany
    calls = 0

    def executemany(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _busy_snapshot()
        return original_executemany(*args, **kwargs)

    monkeypatch.setattr(db, "executemany", executemany)
    monkeypatch.setattr(WorkflowStore, "_write_retry_base_delay_s", 0)
    await WorkflowStore.record_steps("generator", ((i, {"node_id": f"n{i}"}) for i in range(1, 4)))
    steps, total = await WorkflowStore.list_steps("generator")
    assert calls == 2
    assert total == 3
    assert [step["node_id"] for step in steps] == ["n1", "n2", "n3"]


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_rollback_before_next_writer(monkeypatch) -> None:
    db = await WorkflowStore.raw_db()
    original_commit, original_rollback = db.commit, db.rollback
    committing, rolling_back, release_rollback = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def commit():
        if not committing.is_set():
            committing.set()
            await asyncio.Event().wait()
        await original_commit()

    async def rollback():
        rolling_back.set()
        await release_rollback.wait()
        await original_rollback()

    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr(db, "rollback", rollback)
    first = asyncio.create_task(WorkflowStore.upsert_execution(
        {"id": "cancelled", "workflowId": "wf-cancel", "status": "running"}
    ))
    await asyncio.wait_for(committing.wait(), 5)
    first.cancel()
    await asyncio.wait_for(rolling_back.wait(), 5)
    second = asyncio.create_task(WorkflowStore.upsert_execution(
        {"id": "next", "workflowId": "wf-cancel", "status": "running"}
    ))
    first.cancel()
    await asyncio.sleep(0)
    assert not first.done()
    assert not second.done()
    release_rollback.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 5)
    assert not db.in_transaction
    assert await WorkflowStore.get_execution("cancelled") is None
    assert await WorkflowStore.get_execution("next") is not None


@pytest.mark.asyncio
async def test_failed_rollback_discards_writer_before_next_transaction(monkeypatch) -> None:
    db = await WorkflowStore.raw_db()

    async def fail_commit():
        raise RuntimeError("write failed")

    async def fail_rollback():
        raise RuntimeError("rollback failed")

    monkeypatch.setattr(db, "commit", fail_commit)
    monkeypatch.setattr(db, "rollback", fail_rollback)
    with pytest.raises(RuntimeError, match="rollback failed"):
        await WorkflowStore.complete_execution(
            {"id": "discarded", "workflowId": "wf-discard", "status": "success"}, []
        )
    await WorkflowStore.complete_execution(
        {"id": "recovered", "workflowId": "wf-discard", "status": "success"}, []
    )
    assert await WorkflowStore.raw_db() is not db
    assert await WorkflowStore.get_execution("discarded") is None
    assert await WorkflowStore.get_execution("recovered") is not None


@pytest.mark.asyncio
async def test_migration_can_reopen_writer_after_skipping_failed_rollback(monkeypatch) -> None:
    async def migration():
        db = await WorkflowStore.raw_db()

        async def fail_commit():
            raise RuntimeError("migration write failed")

        async def fail_rollback():
            raise RuntimeError("migration rollback failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", fail_rollback)
        # Legacy step migration skips individual failures and continues while
        # init still owns its lifecycle lock.
        with pytest.raises(RuntimeError, match="migration rollback failed"):
            await WorkflowStore.record_step("skipped", 1, {"node_id": "old"})
        await WorkflowStore.record_step("migrated", 1, {"node_id": "new"})

    monkeypatch.setattr(WorkflowStore, "_migrate_legacy_kv", migration)
    await asyncio.wait_for(WorkflowStore.init(), 5)
    assert await WorkflowStore.list_steps("skipped") == ([], 0)
    assert await WorkflowStore.list_steps("migrated") == ([{"node_id": "new"}], 1)


@pytest.mark.asyncio
async def test_close_waits_for_writer_reopen_without_lock_cycle(monkeypatch) -> None:
    await WorkflowStore.init()
    old = await WorkflowStore.raw_db()
    await old.close()
    WorkflowStore._completion_conn = None
    configuring, release = asyncio.Event(), asyncio.Event()
    configure = Storage.configure_connection

    async def blocked_configure(connection):
        configuring.set()
        await release.wait()
        return await configure(connection)

    monkeypatch.setattr(Storage, "configure_connection", blocked_configure)
    reopen = asyncio.create_task(WorkflowStore.raw_db())
    await asyncio.wait_for(configuring.wait(), 5)
    close = asyncio.create_task(WorkflowStore.close())
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    reopened = await asyncio.wait_for(reopen, 5)
    await asyncio.wait_for(close, 5)
    assert WorkflowStore._completion_conn is None
    assert not WorkflowStore._initialized
    with pytest.raises(ValueError, match="no active connection"):
        _ = reopened.in_transaction


@pytest.mark.asyncio
async def test_close_waits_for_active_write_transaction_before_closing_connections():
    await WorkflowStore.init()
    db = await WorkflowStore.raw_completion_db()
    started, release = asyncio.Event(), asyncio.Event()

    async def operation():
        await db.execute("INSERT INTO workflow_kv(key,value,type,created_at,updated_at) VALUES ('audit-active','1','json','now','now')")
        started.set()
        await release.wait()

    writer = asyncio.create_task(WorkflowStore._run_write_transaction(operation, action="audit-close"))
    await asyncio.wait_for(started.wait(), timeout=5)
    closer = asyncio.create_task(WorkflowStore.close())
    try:
        await asyncio.sleep(0.02)
        assert not closer.done(), "close must not close a connection under an active transaction"
    finally:
        release.set()
        await asyncio.gather(writer, closer, return_exceptions=True)
    assert writer.exception() is None
    with sqlite3.connect(WorkflowStore.get_db_path()) as conn:
        assert conn.execute("SELECT value FROM workflow_kv WHERE key='audit-active'").fetchone() == ('1',)


@pytest.mark.asyncio
async def test_queued_writer_after_close_joins_reinitialized_transaction_gate():
    await WorkflowStore.init()
    first_started, first_release = asyncio.Event(), asyncio.Event()
    second_started, second_release = asyncio.Event(), asyncio.Event()

    async def first_operation():
        first_started.set()
        await first_release.wait()

    async def second_operation():
        db = await WorkflowStore._completion_db()
        await db.execute("INSERT INTO workflow_kv(key,value,type,created_at,updated_at) VALUES ('audit-second','2','json','now','now')")
        second_started.set()
        await second_release.wait()

    first = asyncio.create_task(WorkflowStore._run_write_transaction(first_operation, action="first"))
    await asyncio.wait_for(first_started.wait(), timeout=5)
    closer = asyncio.create_task(WorkflowStore.close())
    await asyncio.sleep(0.01)
    second = asyncio.create_task(WorkflowStore._run_write_transaction(second_operation, action="second"))
    first_release.set()
    await asyncio.wait_for(second_started.wait(), timeout=5)
    third = asyncio.create_task(WorkflowStore.kv_put('audit-third', 3))
    try:
        await asyncio.sleep(0.02)
        assert not third.done(), 'queued writers must share the new gate after close/reinitialize'
    finally:
        second_release.set()
        await asyncio.gather(first, closer, second, third)
    assert await WorkflowStore.kv_get('audit-second') == 2
    assert await WorkflowStore.kv_get('audit-third') == 3


@pytest.mark.asyncio
async def test_cancelled_close_finishes_both_connections_and_allows_reinitialization(monkeypatch):
    await WorkflowStore.init()
    db = await WorkflowStore._db()
    original_close = db.close
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_close():
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(db, 'close', delayed_close)
    closer = asyncio.create_task(WorkflowStore.close())
    await asyncio.wait_for(entered.wait(), timeout=5)
    closer.cancel()
    await asyncio.sleep(0)
    closer.cancel()
    try:
        await asyncio.sleep(0.01)
        assert not closer.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closer
    assert WorkflowStore._conn is None and WorkflowStore._completion_conn is None
    await WorkflowStore.kv_put('after-cancel-close', 'ready')
    assert await WorkflowStore.kv_get('after-cancel-close') == 'ready'
