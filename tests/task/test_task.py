from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.manager import TaskManager
from flocks.task.models import (
    DeliveryStatus,
    ExecutionTriggerType,
    SchedulerMode,
    SchedulerStatus,
    TaskPriority,
    TaskStatus,
    TaskTrigger,
)
from flocks.task.queue import TaskQueue
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


@pytest.mark.asyncio
async def test_immediate_scheduler_creates_single_queued_execution(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="立即执行",
        description="创建后立刻入队",
        mode=SchedulerMode.ONCE,
        priority=TaskPriority.HIGH,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
    )

    executions, total = await TaskManager.list_scheduler_executions(scheduler.id)

    assert total == 1
    execution = executions[0]
    assert execution.scheduler_id == scheduler.id
    assert execution.trigger_type == ExecutionTriggerType.RUN_ONCE
    assert execution.status == TaskStatus.QUEUED
    assert execution.queued_at is not None
    assert execution.started_at is None
    assert execution.completed_at is None


@pytest.mark.asyncio
async def test_once_scheduler_tick_creates_execution_and_disables_scheduler(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="单次定时",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(
            run_immediately=False,
            run_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        ),
        workspace_directory=str(tmp_path / "workspace"),
    )

    loop = SchedulerLoop()
    await loop._tick()

    updated = await TaskManager.get_scheduler(scheduler.id)
    executions, total = await TaskManager.list_scheduler_executions(scheduler.id)

    assert updated is not None
    assert updated.status == SchedulerStatus.DISABLED
    assert updated.trigger.next_run is None
    assert total == 1
    assert executions[0].trigger_type == ExecutionTriggerType.RUN_ONCE


@pytest.mark.asyncio
async def test_cron_scheduler_does_not_spawn_second_active_execution(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="循环任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(
            cron="*/5 * * * *",
            timezone="Asia/Shanghai",
        ),
        workspace_directory=str(tmp_path / "workspace"),
    )
    assert scheduler.trigger.next_run is not None

    scheduler.trigger.next_run = datetime.now(timezone.utc) - timedelta(seconds=1)
    await TaskStore.update_scheduler(scheduler)

    loop = SchedulerLoop()
    await loop._tick()
    await loop._tick()

    executions, total = await TaskManager.list_scheduler_executions(scheduler.id, limit=10)

    assert total == 1
    assert executions[0].status == TaskStatus.QUEUED
    assert executions[0].trigger_type == ExecutionTriggerType.SCHEDULED


@pytest.mark.asyncio
async def test_retry_queue_requeues_failed_execution(tmp_path: Path):
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="失败重试",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    execution.status = TaskStatus.FAILED
    execution.retry.max_retries = 2
    execution.retry.retry_count = 0
    execution.retry.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    execution.completed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await TaskStore.update_execution(execution)

    await manager._process_retry_queue()

    reloaded = await TaskManager.get_execution(execution.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.QUEUED
    assert reloaded.retry.retry_after is None
    assert reloaded.queued_at is not None
    assert reloaded.started_at is None
    assert reloaded.completed_at is None


@pytest.mark.asyncio
async def test_queue_dequeue_respects_claimed_slots_before_running_status(tmp_path: Path):
    queue = TaskQueue(max_concurrent=1)
    scheduler = await TaskManager.create_scheduler(
        title="并发控制",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    first = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )
    second = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )

    claimed = await queue.dequeue()
    blocked = await queue.dequeue()

    assert claimed is not None
    assert claimed.id == first.id
    assert blocked is None
    queue.mark_finished(first.id)
    await TaskStore.finish_queue_ref(first.id)

    next_claimed = await queue.dequeue()
    assert next_claimed is not None
    assert next_claimed.id == second.id


@pytest.mark.asyncio
async def test_immediate_scheduler_dedup_does_not_create_duplicate_execution(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="去重立即执行",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
        dedup_key="dup-immediate",
    )
    duplicate = await TaskManager.create_scheduler(
        title="去重立即执行",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
        dedup_key="dup-immediate",
    )

    executions, total = await TaskManager.list_scheduler_executions(scheduler.id, limit=10)

    assert duplicate.id == scheduler.id
    assert total == 1
    assert executions[0].scheduler_id == scheduler.id


@pytest.mark.asyncio
async def test_batch_cancel_counts_only_actual_cancellations(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="批量取消",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    cancellable = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )
    completed = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    completed.status = TaskStatus.COMPLETED
    completed.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(completed)

    cancelled = await TaskManager.batch_cancel([cancellable.id, completed.id])

    assert cancelled == 1


@pytest.mark.asyncio
async def test_dashboard_counts_exclude_immediate_once_schedulers(tmp_path: Path):
    await TaskManager.create_scheduler(
        title="队列任务模板",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace-1"),
    )
    await TaskManager.create_scheduler(
        title="单次计划任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False, run_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        workspace_directory=str(tmp_path / "workspace-2"),
    )
    await TaskManager.create_scheduler(
        title="循环计划任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="*/5 * * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace-3"),
    )

    counts = await TaskManager.dashboard()

    assert counts["scheduled_active"] == 2


@pytest.mark.asyncio
async def test_get_unviewed_results_includes_unread_and_notified_only(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="未读结果",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    unread = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    unread.status = TaskStatus.COMPLETED
    unread.completed_at = datetime.now(timezone.utc)
    unread.delivery_status = DeliveryStatus.UNREAD
    await TaskStore.update_execution(unread)

    notified = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    notified.status = TaskStatus.COMPLETED
    notified.completed_at = datetime.now(timezone.utc)
    notified.delivery_status = DeliveryStatus.NOTIFIED
    await TaskStore.update_execution(notified)

    viewed = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    viewed.status = TaskStatus.COMPLETED
    viewed.completed_at = datetime.now(timezone.utc)
    viewed.delivery_status = DeliveryStatus.VIEWED
    await TaskStore.update_execution(viewed)

    results = await TaskManager.get_unviewed_results()
    result_ids = {item.id for item in results}

    assert unread.id in result_ids
    assert notified.id in result_ids
    assert viewed.id not in result_ids


@pytest.mark.asyncio
async def test_task_page_notice_drops_legacy_tables_after_third_display():
    db = await TaskStore.raw_db()
    await db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    await db.commit()
    TaskManager._write_migration_state({"failed": True, "notice_count": 0})

    first = await TaskManager.get_task_page_notice()
    second = await TaskManager.get_task_page_notice()
    third = await TaskManager.get_task_page_notice()

    assert first == {
        "message": "系统更新了任务表的存储，旧表自动迁移失败，请手动重建任务 scheduler",
        "displayCount": 1,
    }
    assert second is not None and second["displayCount"] == 2
    assert third is not None and third["displayCount"] == 3
    assert TaskManager._legacy_tables_exist() is False
