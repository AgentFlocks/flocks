"""Task center writes that raced: a shared connection, stale snapshots, unclaimable runs."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.manager import TaskManager
from flocks.task.models import (
    ExecutionTriggerType,
    SchedulerMode,
    SchedulerStatus,
    TaskStatus,
    TaskTrigger,
)
from flocks.task.scheduler import TaskScheduler as SchedulerLoop
from flocks.task.store import TaskStore


@pytest.fixture(autouse=True)
async def isolated_task_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskManager._instance = None
    TaskManager._startup_error = None
    TaskStore._initialized = False
    TaskStore._conn = None
    await Storage.init()
    await TaskStore.init()
    yield
    await TaskManager.stop()
    await TaskStore.close()
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskManager._instance = None
    TaskManager._startup_error = None
    TaskStore._initialized = False
    TaskStore._conn = None


async def _idle_scheduler(tmp_path: Path, title: str = "任务"):
    """A scheduler that does not fire by itself during the test."""
    return await TaskManager.create_scheduler(
        title=title,
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False, run_at=datetime.now(timezone.utc) + timedelta(days=1)),
        workspace_directory=str(tmp_path / "workspace"),
    )


async def _queued_execution(tmp_path: Path):
    scheduler = await _idle_scheduler(tmp_path)
    return await TaskManager.create_execution_from_scheduler(
        scheduler, trigger_type=ExecutionTriggerType.RERUN, enqueue=True,
    )


def _stored(table: str, row_id: str, column: str):
    """Read what is committed, through a connection of its own."""
    with sqlite3.connect(TaskStore.get_db_path()) as conn:
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return row[0] if row else None


async def test_a_queue_claim_does_not_roll_back_an_edit_waiting_to_commit(tmp_path, monkeypatch):
    """The claim's rollback on the shared connection undid a scheduler edit the API had already accepted."""
    execution = await _queued_execution(tmp_path)
    scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
    conn = await TaskStore.raw_db()
    real_commit = conn.commit
    edit_waiting, release = asyncio.Event(), asyncio.Event()

    async def commit_after_release():
        if not edit_waiting.is_set():
            edit_waiting.set()
            await release.wait()
        await real_commit()

    monkeypatch.setattr(conn, "commit", commit_after_release)
    scheduler.title = "改过的标题"
    edit = asyncio.create_task(TaskStore.update_scheduler(scheduler))
    await asyncio.wait_for(edit_waiting.wait(), timeout=10)
    claim = asyncio.create_task(TaskStore.claim_next_queue_execution())
    for _ in range(20):
        await asyncio.sleep(0.01)
    release.set()
    edited, claimed = await asyncio.gather(edit, claim, return_exceptions=True)

    assert not isinstance(edited, BaseException), edited
    assert _stored("task_schedulers", scheduler.id, "title") == "改过的标题"
    assert not isinstance(claimed, BaseException), claimed
    assert claimed is not None and claimed[0].id == execution.id
    assert _stored("task_executions", execution.id, "status") == "running"


@pytest.mark.parametrize("mode", [SchedulerMode.CRON, SchedulerMode.ONCE])
async def test_a_tick_does_not_undo_an_edit_made_after_it_read_the_scheduler(tmp_path, monkeypatch, mode):
    if mode == SchedulerMode.CRON:
        trigger = TaskTrigger(cron="*/5 * * * *", timezone="Asia/Shanghai")
    else:
        trigger = TaskTrigger(run_immediately=False, run_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    scheduler = await TaskManager.create_scheduler(
        title="原标题", mode=mode, trigger=trigger, workspace_directory=str(tmp_path / "workspace"),
    )
    if mode == SchedulerMode.CRON:
        scheduler.trigger.next_run = datetime.now(timezone.utc) - timedelta(seconds=1)
        await TaskStore.update_scheduler(scheduler)

    real_active = TaskStore.get_active_execution_for_scheduler

    async def edited_meanwhile(scheduler_id):
        # The user disables and renames it after the tick read the due list.
        current = await TaskStore.get_scheduler(scheduler_id)
        current.status = SchedulerStatus.DISABLED
        current.title = "用户改的标题"
        await TaskStore.update_scheduler(current)
        return await real_active(scheduler_id)

    monkeypatch.setattr(TaskStore, "get_active_execution_for_scheduler", edited_meanwhile)
    await SchedulerLoop()._tick()

    stored = await TaskStore.get_scheduler(scheduler.id)
    assert (stored.status, stored.title) == (SchedulerStatus.DISABLED, "用户改的标题")
    _, total = await TaskManager.list_scheduler_executions(scheduler.id)
    assert total == 0, "a disabled scheduler started nothing"


async def test_expiry_leaves_a_run_claimed_after_it_was_read(tmp_path, monkeypatch):
    execution = await _queued_execution(tmp_path)
    real_list = TaskStore.list_stale_queued_executions

    async def claimed_meanwhile(before):
        stale = await real_list(before=datetime.now(timezone.utc) + timedelta(seconds=1))
        assert [item.id for item in stale] == [execution.id]
        assert await TaskStore.claim_next_queue_execution() is not None
        return stale

    monkeypatch.setattr(TaskStore, "list_stale_queued_executions", claimed_meanwhile)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    assert await manager._expire_stale_executions() == 0
    assert _stored("task_executions", execution.id, "status") == "running"
    ref = await TaskStore.get_queue_ref(execution.id)
    assert ref is not None and ref.status == TaskStatus.RUNNING


async def test_recovery_does_not_queue_again_a_run_cancelled_after_it_was_read(tmp_path, monkeypatch):
    execution = await _queued_execution(tmp_path)
    await TaskStore.finish_queue_ref(execution.id)  # queued without a ref: an orphan
    real_list = TaskStore.list_orphaned_queued_executions

    async def cancelled_meanwhile():
        orphans = await real_list()
        await TaskManager.cancel_execution(execution.id)
        return orphans

    monkeypatch.setattr(TaskStore, "list_orphaned_queued_executions", cancelled_meanwhile)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    await manager._recover_orphaned_queued_executions()

    assert _stored("task_executions", execution.id, "status") == "cancelled"
    assert await TaskStore.get_queue_ref(execution.id) is None


async def test_a_finished_run_with_a_leftover_queue_ref_is_not_claimed(tmp_path):
    execution = await _queued_execution(tmp_path)
    execution.status = TaskStatus.CANCELLED
    await TaskStore.update_execution(execution)  # the ref stays behind

    assert await TaskStore.claim_next_queue_execution() is None
    assert _stored("task_executions", execution.id, "status") == "cancelled"
    ref = await TaskStore.get_queue_ref(execution.id)
    assert ref is None or ref.status == TaskStatus.QUEUED


@pytest.mark.parametrize("kind", ["safe_to_retry", "needs_verification"])
async def test_failure_handling_does_not_undo_a_retry_made_meanwhile(tmp_path, monkeypatch, kind):
    """The failure went out to the UI, the user retried it, then the failure handling wrote its snapshot back."""
    from flocks.task import outcome as outcome_module

    await _queued_execution(tmp_path)
    claimed, _ = await TaskStore.claim_next_queue_execution()
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    failed = await manager._mark_execution_failed(claimed, "boom")
    await TaskStore.finish_queue_ref(failed.id)
    gate = asyncio.Event()

    async def outcome_after_the_retry(_execution):
        await gate.wait()
        return outcome_module.RunOutcome(kind, ("bash:call_1",) if kind != "safe_to_retry" else ())

    monkeypatch.setattr(outcome_module, "run_outcome", outcome_after_the_retry)
    handling = asyncio.create_task(manager._handle_failure(failed))
    await asyncio.sleep(0.05)
    retried = await TaskManager.retry_execution(failed.id)
    assert retried.status == TaskStatus.QUEUED
    gate.set()
    await handling

    assert _stored("task_executions", failed.id, "status") == "queued"
    ref = await TaskStore.get_queue_ref(failed.id)
    assert ref is not None and ref.status == TaskStatus.QUEUED
    assert await TaskStore.claim_next_queue_execution() is not None, "the retried run can be claimed"


async def test_a_stale_failed_snapshot_does_not_requeue_over_a_newer_failure(tmp_path):
    """Both are FAILED: the status alone cannot tell the older attempt from the newer one."""
    execution = await _queued_execution(tmp_path)
    claimed, _ = await TaskStore.claim_next_queue_execution()
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    stale = await manager._mark_execution_failed(claimed, "attempt 1")
    await TaskStore.finish_queue_ref(stale.id)  # as the run's own finally does
    # A newer attempt failed meanwhile and is held for a person to check.
    newer = await TaskStore.get_execution(execution.id)
    newer.retry.retry_count = 2
    newer.retry.blocked_reason = "unverified_effects"
    await TaskStore.update_execution(newer)

    stale.retry.retry_after = None
    result = await manager._enqueue_execution(stale, expected_status=TaskStatus.FAILED)

    assert result.status == TaskStatus.FAILED
    stored = await TaskStore.get_execution(execution.id)
    assert (stored.retry.retry_count, stored.retry.blocked_reason) == (2, "unverified_effects")
    assert await TaskStore.get_queue_ref(execution.id) is None
