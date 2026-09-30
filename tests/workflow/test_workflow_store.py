from __future__ import annotations

import asyncio
import os
import json
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
async def test_step_duration_column_migrates_without_backfilling_old_payloads() -> None:
    path = WorkflowStore.get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"node_id": "normalize", "duration_ms": 50, "stdout": "x" * 100_000})
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE workflow_execution_steps (
            exec_id TEXT, step_index INTEGER, node_id TEXT, node_type TEXT,
            inputs TEXT, outputs TEXT, error TEXT, payload TEXT,
            PRIMARY KEY(exec_id, step_index))""")
        db.execute("INSERT INTO workflow_execution_steps VALUES ('old',1,'normalize','python','{}','{}',NULL,?)", (payload,))
    await WorkflowStore.init()
    await WorkflowStore.close()
    await WorkflowStore.init()  # Idempotent on a migrated database.
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(workflow_execution_steps)")]
        assert columns.count("duration_ms") == 1
        assert db.execute("SELECT duration_ms,payload FROM workflow_execution_steps WHERE exec_id='old'").fetchone() == (None, payload)
    await WorkflowStore.record_step("new", 1, {"node_id": "normalize", "duration_ms": 50})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT duration_ms FROM workflow_execution_steps WHERE exec_id='new'").fetchone()[0] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("duration, expected", [(0, 0), (.25, .25), (20, 20), (None, None),
    (-1, None), (True, None), ("20", None), (float("inf"), None), (float("nan"), None), (10**1000, None)])
async def test_record_step_stores_only_valid_numeric_duration(duration, expected) -> None:
    await WorkflowStore.record_step("timed", 1, {"node_id": "load_dedup_file", "duration_ms": duration})
    with sqlite3.connect(WorkflowStore.get_db_path()) as db:
        assert db.execute("SELECT duration_ms FROM workflow_execution_steps").fetchone()[0] == expected


@pytest.mark.asyncio
async def test_complete_execution_persists_step_duration_independent_of_payload_size() -> None:
    await WorkflowStore.complete_execution(
        {"id": "triage", "workflowId": "stream_alert_triage", "status": "success", "startedAt": 100},
        [(1, {"node_id": "load_dedup_file", "duration_ms": 20}),
         (2, {"node_id": "concurrent_triage", "duration_ms": 50, "stdout": "x" * 100_000})],
    )
    with sqlite3.connect(WorkflowStore.get_db_path()) as db:
        assert db.execute("SELECT node_id,duration_ms FROM workflow_execution_steps ORDER BY step_index").fetchall() == [
            ("load_dedup_file", 20), ("concurrent_triage", 50)]


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
async def test_open_history_snapshot_does_not_poison_new_execution_writes():
    for i in range(4):
        await WorkflowStore.upsert_execution({"id": f"seed-{i}", "workflowId": "wf", "status": "running"})
    reader = await WorkflowStore.raw_db()
    cursor = await reader.execute("SELECT payload FROM workflow_executions")
    await cursor.fetchone()
    try:
        await WorkflowStore.complete_execution({"id": "seed-0", "workflowId": "wf", "status": "success"}, [])
        for i in range(3):
            await WorkflowStore.upsert_execution({"id": f"new-{i}", "workflowId": "wf", "status": "running"})
    finally:
        await cursor.close()
    assert len(await WorkflowStore.list_executions("wf")) == 7
    assert not reader.in_transaction
    assert not (await WorkflowStore.raw_completion_db()).in_transaction


@pytest.mark.asyncio
async def test_cancelled_writer_rolls_back_only_its_transaction(monkeypatch):
    await WorkflowStore.init()
    writer = await WorkflowStore.raw_completion_db()
    entered = asyncio.Event()
    original = writer.commit
    calls = 0
    async def blocked_commit():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        await original()
    monkeypatch.setattr(writer, "commit", blocked_commit)
    first = asyncio.create_task(WorkflowStore.kv_put("first", {"value": 1}))
    await entered.wait()
    second = asyncio.create_task(WorkflowStore.kv_put("second", {"value": 2}))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 2)
    assert await WorkflowStore.kv_get("first") is None
    assert await WorkflowStore.kv_get("second") == {"value": 2}
    assert not writer.in_transaction


@pytest.mark.asyncio
async def test_external_writer_lock_is_bounded_and_store_recovers():
    import sqlite3
    await WorkflowStore.init()
    writer = await WorkflowStore.raw_completion_db()
    await writer.execute("PRAGMA busy_timeout=1")
    blocker = sqlite3.connect(WorkflowStore.get_db_path(), isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await asyncio.wait_for(WorkflowStore.kv_put("blocked", 1), 2)
        assert not writer.in_transaction
        blocker.rollback()
        await WorkflowStore.kv_put("recovered", 2)
        assert await WorkflowStore.kv_get("blocked") is None
        assert await WorkflowStore.kv_get("recovered") == 2
    finally:
        blocker.close()


@pytest.mark.asyncio
async def test_temporary_external_lock_retries_before_body():
    import sqlite3
    await WorkflowStore.init()
    writer = await WorkflowStore.raw_completion_db()
    await writer.execute("PRAGMA busy_timeout=1")
    blocker = sqlite3.connect(WorkflowStore.get_db_path(), isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(WorkflowStore.kv_put("retry", 1))
        await asyncio.sleep(.025)
        blocker.rollback()
        await asyncio.wait_for(task, 2)
        assert await WorkflowStore.kv_get("retry") == 1
    finally:
        blocker.close()


@pytest.mark.asyncio
async def test_concurrent_first_writes_share_initialized_writer():
    await asyncio.wait_for(asyncio.gather(*(
        WorkflowStore.kv_put(f"cold-{i}", i) for i in range(20)
    )), 5)
    assert len(await WorkflowStore.kv_list_keys("cold-")) == 20
    assert not (await WorkflowStore.raw_completion_db()).in_transaction


@pytest.mark.asyncio
async def test_mixed_history_reads_and_parallel_writers_keep_all_results():
    await WorkflowStore.init()
    async def run_batch(worker):
        for n in range(40):
            row = {"id": f"{worker}-{n}", "workflowId": "wf", "status": "running"}
            await WorkflowStore.upsert_execution(row)
            row["status"] = "success"
            await WorkflowStore.complete_execution(row, [(1, {"node_id": "done"})])
            await WorkflowStore.increment_stats("wf", success=True, duration=1)
    async def history():
        for _ in range(100):
            await WorkflowStore.list_executions("wf", limit=1000)
            await WorkflowStore.get_stats("wf")
            await WorkflowStore.put_config("wf", {"enabled": True}, kind="workflow_poller_config")
    await asyncio.wait_for(asyncio.gather(history(), *(run_batch(i) for i in range(5))), 20)
    records = await WorkflowStore.list_executions("wf", limit=1000)
    assert len(records) == 200
    assert all(item["status"] == "success" for item in records)
    assert (await WorkflowStore.get_stats("wf"))["successCount"] == 200
