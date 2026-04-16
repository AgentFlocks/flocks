from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.cli.commands import task as task_cli_commands
import flocks.task.background as background_module
import flocks.task.manager as task_manager_module
from flocks.server.routes import question as question_routes
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.background import BackgroundManager, BackgroundTask
from flocks.task.executor import TaskExecutor
from flocks.task.manager import TaskManager
from flocks.task.models import (
    DeliveryStatus,
    ExecutionMode,
    ExecutionTriggerType,
    SchedulerMode,
    SchedulerStatus,
    TaskExecution,
    TaskPriority,
    TaskScheduler,
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
async def test_recover_stale_running_execution_unblocks_scheduler(tmp_path: Path):
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="恢复阻塞循环任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="*/5 * * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace"),
    )
    scheduler.trigger.next_run = datetime.now(timezone.utc) - timedelta(seconds=1)
    await TaskStore.update_scheduler(scheduler)

    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.SCHEDULED,
        enqueue=False,
    )
    started_at = datetime.now(timezone.utc) - timedelta(
        seconds=task_manager_module._RUNNING_RECOVERY_TIMEOUT_S + 5
    )
    execution.status = TaskStatus.RUNNING
    execution.started_at = started_at
    execution.queued_at = started_at
    execution.session_id = "ses_stale_task"
    await TaskStore.update_execution(execution)
    await TaskStore.enqueue_execution_ref(execution.id)

    recovered = await manager._recover_stale_active_executions()

    assert recovered == 1
    failed = await TaskManager.get_execution(execution.id)
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert "recovery threshold" in (failed.error or "")
    assert await TaskStore.get_queue_ref(execution.id) is None

    loop = SchedulerLoop()
    await loop._tick()
    executions, total = await TaskManager.list_scheduler_executions(scheduler.id, limit=10)

    assert total == 2
    assert any(item.id == execution.id and item.status == TaskStatus.FAILED for item in executions)
    assert any(item.id != execution.id and item.status == TaskStatus.QUEUED for item in executions)


@pytest.mark.asyncio
async def test_recover_orphaned_queued_execution_restores_queue_ref(tmp_path: Path):
    await TaskManager.start(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="恢复孤儿排队任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )
    await TaskStore.finish_queue_ref(execution.id)

    manager = TaskManager.get()
    assert manager is not None

    recovered = await manager._recover_orphaned_queued_executions()
    refreshed = await TaskManager.get_execution(execution.id)

    assert recovered == 1
    assert refreshed is not None
    assert refreshed.status == TaskStatus.QUEUED
    assert refreshed.started_at is None
    assert refreshed.completed_at is None
    assert refreshed.session_id is None
    queue_ref = await TaskStore.get_queue_ref(execution.id)
    assert queue_ref is not None
    assert queue_ref.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_queue_status_reports_stale_running_execution(tmp_path: Path):
    await TaskManager.start(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="阻塞诊断",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    started_at = datetime.now(timezone.utc) - timedelta(
        seconds=task_manager_module._RUNNING_RECOVERY_TIMEOUT_S + 15
    )
    execution.status = TaskStatus.RUNNING
    execution.started_at = started_at
    execution.queued_at = started_at
    await TaskStore.update_execution(execution)

    status = await TaskManager.queue_status()

    assert status["stale_running"] == 1
    assert isinstance(status["oldest_running_seconds"], int)
    assert status["oldest_running_seconds"] >= task_manager_module._RUNNING_RECOVERY_TIMEOUT_S


@pytest.mark.asyncio
async def test_queue_status_uses_largest_elapsed_running_time(tmp_path: Path):
    await TaskManager.start(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="阻塞诊断-多任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    older_execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    newer_execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    older_started_at = datetime.now(timezone.utc) - timedelta(
        seconds=task_manager_module._RUNNING_RECOVERY_TIMEOUT_S + 15
    )
    newer_started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    older_execution.status = TaskStatus.RUNNING
    older_execution.started_at = older_started_at
    older_execution.queued_at = older_started_at
    newer_execution.status = TaskStatus.RUNNING
    newer_execution.started_at = newer_started_at
    newer_execution.queued_at = newer_started_at
    await TaskStore.update_execution(older_execution)
    await TaskStore.update_execution(newer_execution)

    status = await TaskManager.queue_status()

    assert status["stale_running"] == 1
    assert isinstance(status["oldest_running_seconds"], int)
    assert status["oldest_running_seconds"] >= task_manager_module._RUNNING_RECOVERY_TIMEOUT_S + 15


@pytest.mark.asyncio
async def test_background_task_fails_fast_when_user_input_is_required(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_session_loop(_session_id: str, callbacks=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    rejected: list[str] = []

    monkeypatch.setattr(background_module, "_WATCHDOG_CHECK_INTERVAL", 0.01)
    monkeypatch.setattr(background_module.SessionLoop, "run", staticmethod(fake_session_loop))
    monkeypatch.setattr(question_routes, "has_pending_questions", lambda _session_id: True)

    async def fake_reject(session_id: str) -> int:
        rejected.append(session_id)
        return 1

    monkeypatch.setattr(question_routes, "reject_session_questions", fake_reject)

    manager = BackgroundManager()
    task = BackgroundTask(
        id="bg_test",
        status="running",
        description="scheduled",
        prompt="",
        agent="rex",
        allow_user_questions=False,
    )

    with pytest.raises(RuntimeError, match="cannot wait for user input"):
        await manager._run_session_with_watchdog(
            task,
            "ses_interactive_block",
            callbacks=None,
            timeout_seconds=1,
            allow_user_questions=False,
        )

    assert rejected == ["ses_interactive_block"]


@pytest.mark.asyncio
async def test_trigger_workflow_resolves_workflow_id_from_filesystem(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.workflow import fs_store
    from flocks.workflow import runner

    workflow_json = {
        "name": "Local Script Runner",
        "start": "start",
        "nodes": [],
        "edges": [],
    }
    captured: dict[str, object] = {}

    def fake_read_workflow_from_fs(workflow_id: str):
        captured["workflow_id"] = workflow_id
        return {"workflowJson": workflow_json}

    def fake_run_workflow(*, workflow, inputs, **_kwargs):
        captured["workflow"] = workflow
        captured["inputs"] = inputs
        return SimpleNamespace(error=None, outputs={"ok": True})

    monkeypatch.setattr(fs_store, "read_workflow_from_fs", fake_read_workflow_from_fs)
    monkeypatch.setattr(runner, "run_workflow", fake_run_workflow)

    scheduler = TaskScheduler(
        title="定时 workflow",
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="local-script-runner",
        context={"fallback": "scheduler"},
    )
    execution = TaskExecution(
        schedulerID=scheduler.id,
        title=scheduler.title,
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="local-script-runner",
        executionInputSnapshot={"context": {"source": "snapshot"}},
    )

    result = await TaskExecutor._trigger_workflow(execution, scheduler)

    assert captured["workflow_id"] == "local-script-runner"
    assert captured["workflow"] == workflow_json
    assert captured["inputs"] == {"source": "snapshot"}
    assert result == "{'ok': True}"


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
async def test_delete_scheduler_cleans_active_executions_before_cascade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    cancelled_runtime_ids: list[str] = []

    async def fake_cancel_runtime(_cls, execution):
        cancelled_runtime_ids.append(execution.id)

    monkeypatch.setattr(
        TaskManager,
        "_cancel_execution_runtime",
        classmethod(fake_cancel_runtime),
    )

    scheduler = await TaskManager.create_scheduler(
        title="删除前清理普通计划",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    pending = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    queued = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )
    running = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    running.status = TaskStatus.RUNNING
    running.queued_at = datetime.now(timezone.utc) - timedelta(seconds=3)
    running.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    running.session_id = "ses_delete_running"
    await TaskStore.update_execution(running)
    await TaskStore.enqueue_execution_ref(running.id)
    completed = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    completed.status = TaskStatus.COMPLETED
    completed.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(completed)

    deleted = await TaskManager.delete_scheduler(scheduler.id)

    assert deleted is True
    assert set(cancelled_runtime_ids) == {
        pending.id,
        queued.id,
        running.id,
    }
    for execution_id in (pending.id, queued.id, running.id, completed.id):
        assert await TaskManager.get_execution(execution_id) is None
    assert await TaskStore.get_queue_ref(queued.id) is None
    assert await TaskStore.get_queue_ref(running.id) is None

    db = await TaskStore.raw_db()
    async with db.execute(
        "SELECT COUNT(*) FROM task_executions WHERE scheduler_id = ?",
        (scheduler.id,),
    ) as cur:
        assert (await cur.fetchone())[0] == 0
    async with db.execute(
        """
        SELECT COUNT(*) FROM task_execution_queue_refs
        WHERE execution_id IN (?, ?)
        """,
        (queued.id, running.id),
    ) as cur:
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_store_init_normalizes_legacy_paused_execution(tmp_path: Path):
    scheduler = await TaskManager.create_scheduler(
        title="兼容旧 paused 状态",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )

    db = await TaskStore.raw_db()
    await db.execute(
        """
        UPDATE task_executions
        SET status = 'paused', completed_at = NULL, error = NULL
        WHERE id = ?
        """,
        (execution.id,),
    )
    await db.commit()

    assert await TaskStore.get_queue_ref(execution.id) is not None

    await TaskStore.close()
    await TaskStore.init()

    normalized = await TaskManager.get_execution(execution.id)

    assert normalized is not None
    assert normalized.status == TaskStatus.CANCELLED
    assert normalized.completed_at is not None
    assert normalized.error == "Normalized from legacy paused state."
    assert await TaskStore.get_queue_ref(execution.id) is None


@pytest.mark.asyncio
async def test_cli_list_tasks_accepts_legacy_paused_status(monkeypatch: pytest.MonkeyPatch):
    scheduler = await TaskManager.create_scheduler(
        title="CLI 兼容 paused 查询",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    execution.status = TaskStatus.CANCELLED
    execution.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(execution)
    await TaskManager.disable_scheduler(scheduler.id)

    printed: list[object] = []
    monkeypatch.setattr(task_cli_commands.console, "print", lambda *args, **kwargs: printed.append(args))

    await task_cli_commands._list_tasks("paused", None, 10, "json")
    await task_cli_commands._list_tasks("paused", "scheduled", 10, "json")

    assert printed


@pytest.mark.asyncio
async def test_delete_builtin_scheduler_archives_and_cancels_active_executions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    cancelled_runtime_ids: list[str] = []

    async def fake_cancel_runtime(_cls, execution):
        cancelled_runtime_ids.append(execution.id)

    monkeypatch.setattr(
        TaskManager,
        "_cancel_execution_runtime",
        classmethod(fake_cancel_runtime),
    )

    scheduler = await TaskManager.create_scheduler(
        title="删除前清理内置计划",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="*/5 * * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace"),
        dedup_key="builtin:test-delete-cleanup",
    )
    queued = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.SCHEDULED,
        enqueue=True,
    )
    running = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.SCHEDULED,
        enqueue=False,
    )
    running.status = TaskStatus.RUNNING
    running.queued_at = datetime.now(timezone.utc) - timedelta(seconds=3)
    running.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    running.session_id = "ses_builtin_running"
    await TaskStore.update_execution(running)
    await TaskStore.enqueue_execution_ref(running.id)
    completed = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.SCHEDULED,
        enqueue=False,
    )
    completed.status = TaskStatus.COMPLETED
    completed.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(completed)

    deleted = await TaskManager.delete_scheduler(scheduler.id)
    archived = await TaskManager.get_scheduler(scheduler.id)
    queued_execution = await TaskManager.get_execution(queued.id)
    running_execution = await TaskManager.get_execution(running.id)
    completed_execution = await TaskManager.get_execution(completed.id)

    assert deleted is True
    assert archived is not None
    assert archived.status == SchedulerStatus.ARCHIVED
    assert set(cancelled_runtime_ids) == {queued.id, running.id}
    assert queued_execution is not None
    assert queued_execution.status == TaskStatus.CANCELLED
    assert running_execution is not None
    assert running_execution.status == TaskStatus.CANCELLED
    assert completed_execution is not None
    assert completed_execution.status == TaskStatus.COMPLETED
    assert await TaskStore.get_queue_ref(queued.id) is None
    assert await TaskStore.get_queue_ref(running.id) is None


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
