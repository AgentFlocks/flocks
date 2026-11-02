"""WAL snapshot contention, cancellation, and result-save failures on the real task store."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.executor import TaskExecutor
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionMode, ExecutionTriggerType, SchedulerMode, TaskStatus, TaskTrigger
from flocks.task.scheduler import TaskScheduler as SchedulerLoop
from flocks.task.store import TaskStore


@pytest.fixture(autouse=True)
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    Config._global_config = Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None
    TaskManager._instance = None
    await TaskStore.close()
    await Storage.init()
    await TaskStore.init()
    yield
    await TaskManager.stop()
    await TaskStore.close()
    Storage._initialized = False
    Storage._db_path = None
    Config._global_config = Config._cached_config = None


async def scheduler():
    return await TaskManager.create_scheduler(
        title="reliability test", mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_at=datetime.now(timezone.utc) + timedelta(days=1)),
    )


def busy_snapshot():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    error.sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"
    return error


async def test_dashboard_cursor_cannot_poison_the_writer():
    definition = await scheduler()
    reader = await TaskStore._db()
    writer = await TaskStore.raw_db()
    assert reader is not writer
    cursor = await reader.execute("SELECT COUNT(*) FROM task_schedulers")
    try:
        # Another connection commits while the dashboard's SELECT retains its snapshot.
        with sqlite3.connect(TaskStore.get_db_path()) as other:
            other.execute("UPDATE task_schedulers SET title='external change' WHERE id=?", (definition.id,))
        definition.title = "core edit after external commit"
        await TaskStore.update_scheduler(definition)
        assert not writer.in_transaction
    finally:
        await cursor.close()
    assert (await TaskStore.get_scheduler(definition.id)).title == definition.title
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        await reader.execute("DELETE FROM task_schedulers")


async def test_external_writer_contention_is_bounded_and_recovers(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    await writer.execute("PRAGMA busy_timeout=20")
    other = sqlite3.connect(TaskStore.get_db_path())
    other.execute("BEGIN IMMEDIATE")
    try:
        definition.title = "after contention"
        with pytest.raises(sqlite3.OperationalError):
            await asyncio.wait_for(TaskStore.update_scheduler(definition), 2)
        assert not writer.in_transaction
        assert TaskStore.health_snapshot()["status"] == "degraded"
    finally:
        other.rollback()
        other.close()
    await TaskStore.update_scheduler(definition)
    assert TaskStore.health_snapshot()["status"] == "healthy"
    assert (await TaskStore.get_scheduler(definition.id)).title == "after contention"


async def test_retry_replays_the_entire_admission_without_duplicate_rows(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    real_commit = writer.commit
    calls = 0

    async def fail_commit_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise busy_snapshot()
        await real_commit()

    monkeypatch.setattr(writer, "commit", fail_commit_once)
    execution = await TaskManager.create_execution_from_scheduler(
        definition, trigger_type=ExecutionTriggerType.RERUN,
    )
    assert calls == 2
    assert execution.status == TaskStatus.QUEUED
    assert len((await TaskStore.list_scheduler_executions(definition.id))[0]) == 1
    assert await TaskStore.count_queued_refs() == 1
    assert not writer.in_transaction


async def test_failed_commit_does_not_advance_the_in_memory_scheduler(monkeypatch):
    definition = await scheduler()
    definition.trigger.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await TaskStore.update_scheduler(definition)
    writer = await TaskStore.raw_db()
    real_commit = writer.commit
    calls = 0

    async def fail_commit_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise busy_snapshot()
        await real_commit()

    monkeypatch.setattr(writer, "commit", fail_commit_once)
    await SchedulerLoop()._tick()
    assert calls == 2
    assert len((await TaskStore.list_scheduler_executions(definition.id))[0]) == 1
    assert (await TaskStore.get_scheduler(definition.id)).status.value == "disabled"


async def test_repeat_cancellation_drains_rollback_before_the_next_writer(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    wrote, rollback_entered, release_rollback = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_rollback = writer.rollback

    async def delayed_rollback():
        rollback_entered.set()
        await release_rollback.wait()
        await real_rollback()

    async def interrupted_mutation():
        await writer.execute("UPDATE task_schedulers SET title='must roll back' WHERE id=?", (definition.id,))
        wrote.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(writer, "rollback", delayed_rollback)
    task = asyncio.create_task(TaskStore._run_write_transaction(interrupted_mutation, action="cancel_test"))
    await asyncio.wait_for(wrote.wait(), 2)
    task.cancel()
    await asyncio.wait_for(rollback_entered.wait(), 2)
    task.cancel()
    following = asyncio.create_task(TaskStore.update_scheduler(definition))
    await asyncio.sleep(0.02)
    assert not following.done()
    release_rollback.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    await asyncio.wait_for(following, 2)
    assert not writer.in_transaction
    assert (await TaskStore.get_scheduler(definition.id)).title == definition.title


async def test_rollback_failure_retires_the_connection(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    monkeypatch.setattr(writer, "rollback", AsyncMock(side_effect=RuntimeError("rollback broken")))

    async def fail_after_write():
        await writer.execute("UPDATE task_schedulers SET title='uncommitted' WHERE id=?", (definition.id,))
        raise busy_snapshot()

    with pytest.raises(RuntimeError, match="rollback broken"):
        await TaskStore._run_write_transaction(fail_after_write, action="rollback_test")
    assert TaskStore._write_conn is None
    await TaskStore.update_scheduler(definition)
    assert await TaskStore.raw_db() is not writer
    assert (await TaskStore.get_scheduler(definition.id)).title == definition.title


async def test_concurrent_initialization_opens_one_reader_and_writer():
    await TaskStore.close()
    await asyncio.gather(*(TaskStore.init() for _ in range(12)))
    readers = await asyncio.gather(*(TaskStore._db() for _ in range(12)))
    writers = await asyncio.gather(*(TaskStore.raw_db() for _ in range(12)))
    assert len({id(db) for db in readers}) == len({id(db) for db in writers}) == 1
    assert readers[0] is not writers[0]


async def test_result_persistence_failure_never_repeats_business_work(monkeypatch):
    definition = await scheduler()
    definition.execution_mode = ExecutionMode.WORKFLOW
    await TaskStore.update_scheduler(definition)
    execution = await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    manager = TaskManager()
    claimed = await manager.queue.dequeue()
    assert claimed.id == execution.id
    business = AsyncMock(return_value="business finished")
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", business)
    save = TaskStore.update_active_execution
    monkeypatch.setattr(TaskStore, "update_active_execution", AsyncMock(side_effect=busy_snapshot()))
    await manager._run_execution(claimed)
    assert business.await_count == 1
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
    assert execution.id in manager._pending_completions
    assert execution.id in manager.queue._running_ids
    monkeypatch.setattr(TaskStore, "update_active_execution", save)
    await manager._flush_pending_completions()
    assert business.await_count == 1
    assert not manager._pending_completions
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED
    assert await TaskStore.get_queue_ref(execution.id) is None


async def test_scheduler_error_does_not_skip_other_due_tasks(monkeypatch):
    first, second = await scheduler(), await scheduler()
    for definition in (first, second):
        definition.trigger.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await TaskStore.update_scheduler(definition)
    fire = TaskStore.fire_scheduler

    async def fail_first(definition, *args, **kwargs):
        if definition.id == first.id:
            raise busy_snapshot()
        return await fire(definition, *args, **kwargs)

    monkeypatch.setattr(TaskStore, "fire_scheduler", fail_first)
    loop = SchedulerLoop()
    await loop._tick()
    assert (await TaskStore.list_scheduler_executions(first.id))[1] == 0
    assert (await TaskStore.list_scheduler_executions(second.id))[1] == 1
    assert loop.health_snapshot()["status"] == "degraded"


async def test_maintenance_error_does_not_disable_queue_dispatch(monkeypatch):
    definition = await scheduler()
    await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    manager = TaskManager(poll_interval=0.01)
    manager._running = True
    dispatched = asyncio.Event()
    monkeypatch.setattr(manager, "_recover_stale_active_executions", AsyncMock(side_effect=RuntimeError("one stale row")))

    async def run(execution):
        dispatched.set()
        manager._running = False

    monkeypatch.setattr(manager, "_run_execution", run)
    await asyncio.wait_for(manager._execution_loop(), 2)
    assert dispatched.is_set()
    assert manager._health.snapshot()["status"] == "degraded"


async def test_health_snapshot_does_not_depend_on_database_access(monkeypatch):
    manager = TaskManager()
    manager._running = True
    manager.scheduler._running = True
    manager._health.failed(busy_snapshot(), operation="dequeue")
    monkeypatch.setattr(TaskManager, "_instance", manager)
    monkeypatch.setattr(TaskStore, "_db", AsyncMock(side_effect=AssertionError("health must not query DB")))
    state = TaskManager.health_snapshot()
    assert state["status"] == "degraded"
    assert state["executor"]["sqlite_error_code"] == 517
    manager._running = False


async def test_old_completion_does_not_overwrite_a_newer_attempt():
    definition = await scheduler()
    execution = await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    claimed, _ = await TaskStore.claim_next_queue_execution()
    old = claimed.model_copy(deep=True)
    old.status = TaskStatus.COMPLETED
    old.completed_at = datetime.now(timezone.utc)
    claimed.started_at += timedelta(seconds=1)
    await TaskStore.update_execution(claimed)
    manager = TaskManager()
    manager.queue.mark_started(execution.id)
    manager._pending_completions[execution.id] = old
    await manager._flush_pending_completions()
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
    assert execution.id in manager.queue._running_ids


async def test_equivalent_timestamp_formats_can_finalize_a_run():
    definition = await scheduler()
    execution = await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    claimed, _ = await TaskStore.claim_next_queue_execution()
    writer = await TaskStore.raw_db()
    await writer.execute("UPDATE task_executions SET started_at=? WHERE id=?",
                         (claimed.started_at.isoformat().replace('+00:00', 'Z'), execution.id))
    claimed.status = TaskStatus.COMPLETED
    assert await TaskStore.update_active_execution(claimed)
    assert await TaskStore.get_queue_ref(execution.id) is None


async def test_manual_admission_rolls_back_both_record_and_queue_on_failure(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    monkeypatch.setattr(writer, 'commit', AsyncMock(side_effect=busy_snapshot()))
    with pytest.raises(sqlite3.OperationalError):
        await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    assert (await TaskStore.list_scheduler_executions(definition.id))[1] == 0
    assert await TaskStore.count_queued_refs() == 0
    assert not writer.in_transaction


async def test_stop_drains_dispatched_workers_before_storage_is_closed(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def run(self, execution):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(TaskManager, '_run_execution', run)
    manager = await TaskManager.start(poll_interval=0.01)
    definition = await scheduler()
    await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    await asyncio.wait_for(started.wait(), 2)
    await TaskManager.stop()
    assert stopped.is_set()
    assert not manager._execution_tasks


async def test_a_write_waiter_times_out_without_rolling_back_the_owner(monkeypatch):
    definition = await scheduler()
    writer = await TaskStore.raw_db()
    ready, release = asyncio.Event(), asyncio.Event()

    async def owner():
        async with TaskStore._write_gate():
            await writer.execute('BEGIN IMMEDIATE')
            await writer.execute('UPDATE task_schedulers SET title=? WHERE id=?', ('owner committed', definition.id))
            ready.set()
            await release.wait()
            await writer.commit()

    owning = asyncio.create_task(owner())
    await asyncio.wait_for(ready.wait(), 2)
    monkeypatch.setattr(TaskStore, '_write_timeout_s', 0.02)
    with pytest.raises(TimeoutError):
        await TaskStore.update_scheduler(definition)
    assert writer.in_transaction, 'the timed-out waiter must not touch the owning transaction'
    release.set()
    await asyncio.wait_for(owning, 2)
    assert (await TaskStore.get_scheduler(definition.id)).title == 'owner committed'


async def test_superseded_worker_does_not_release_the_new_attempt(monkeypatch):
    definition = await scheduler()
    definition.execution_mode = ExecutionMode.WORKFLOW
    await TaskStore.update_scheduler(definition)
    execution = await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
    manager = TaskManager()
    claimed = await manager.queue.dequeue()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(*args):
        started.set()
        await release.wait()
        return 'old result'

    monkeypatch.setattr(TaskExecutor, '_trigger_workflow', work)
    run = asyncio.create_task(manager._run_execution(claimed))
    await asyncio.wait_for(started.wait(), 2)
    newer = await TaskStore.get_execution(execution.id)
    newer.started_at += timedelta(seconds=1)
    await TaskStore.update_execution(newer)
    manager.queue._running_started_at[execution.id] = newer.started_at
    release.set()
    await asyncio.wait_for(run, 2)
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
    assert await TaskStore.get_queue_ref(execution.id) is not None
    assert execution.id in manager.queue._running_ids


async def test_one_failed_retry_does_not_starve_other_retries(monkeypatch):
    definition = await scheduler()
    executions = []
    for _ in range(2):
        execution = await TaskManager.create_execution_from_scheduler(definition, trigger_type=ExecutionTriggerType.RERUN)
        execution.status = TaskStatus.FAILED
        execution.retry.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        await TaskStore.update_execution(execution)
        executions.append(execution)
    monkeypatch.setattr(TaskStore, 'list_retryable_failed_executions', AsyncMock(return_value=executions))
    requeue = TaskStore.requeue_execution

    async def fail_one(execution, **kwargs):
        if execution.id == executions[0].id:
            raise busy_snapshot()
        return await requeue(execution, **kwargs)

    monkeypatch.setattr(TaskStore, 'requeue_execution', fail_one)
    with pytest.raises(sqlite3.OperationalError):
        await TaskManager()._process_retry_queue()
    assert (await TaskStore.get_execution(executions[0].id)).status == TaskStatus.FAILED
    assert (await TaskStore.get_execution(executions[1].id)).status == TaskStatus.QUEUED
