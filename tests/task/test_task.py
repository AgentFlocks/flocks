from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from flocks.cli.commands import task as task_cli_commands
import flocks.task.background as background_module
import flocks.task.manager as task_manager_module
import flocks.task.plugin_sync as plugin_sync_module
from flocks.server.routes import question as question_routes
from flocks.config.config import Config
from flocks.session.message import ToolPart, ToolStateCompleted
from flocks.storage.storage import Storage
from flocks.task.background import BackgroundManager, BackgroundTask, LaunchInput
from flocks.task.executor import TaskExecutor
from flocks.task.formatting import format_task_datetime
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
from flocks.task.plugin_models import TaskSpec
from flocks.task.plugin_sync import upsert_task_specs
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
async def test_list_executions_tolerates_legacy_non_utf8_description(
    tmp_path: Path,
):
    legacy_description = "扫描 Windows 主机 192.168.254.1"
    scheduler = await TaskManager.create_scheduler(
        title="legacy encoding",
        description="placeholder",
        mode=SchedulerMode.ONCE,
        priority=TaskPriority.NORMAL,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
    )
    executions, _ = await TaskManager.list_scheduler_executions(scheduler.id)
    execution_id = executions[0].id

    db = await TaskStore.raw_db()
    await db.execute(
        """
        UPDATE task_executions
        SET description = CAST(? AS TEXT)
        WHERE id = ?
        """,
        (legacy_description.encode("gbk"), execution_id),
    )
    await db.commit()

    executions, total = await TaskManager.list_executions(limit=20)

    assert total == 1
    assert executions[0].description == legacy_description


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
async def test_create_scheduler_rejects_six_field_cron(tmp_path: Path):
    with pytest.raises(ValueError, match="only 5-field cron is supported"):
        await TaskManager.create_scheduler(
            title="非法 cron",
            mode=SchedulerMode.CRON,
            trigger=TaskTrigger(
                cron="0 0 6 * * *",
                timezone="Asia/Shanghai",
            ),
            workspace_directory=str(tmp_path / "workspace"),
        )


@pytest.mark.asyncio
async def test_create_scheduler_rejects_out_of_range_five_field_cron(tmp_path: Path):
    with pytest.raises(ValueError, match="not a valid 5-field cron"):
        await TaskManager.create_scheduler(
            title="越界 cron",
            mode=SchedulerMode.CRON,
            trigger=TaskTrigger(
                cron="70 6 * * *",
                timezone="Asia/Shanghai",
            ),
            workspace_directory=str(tmp_path / "workspace"),
        )


@pytest.mark.asyncio
async def test_create_scheduler_does_not_mutate_trigger_argument(tmp_path: Path):
    trigger = TaskTrigger(
        cron="  0 6 * * *  ",
        timezone="Asia/Shanghai",
    )

    scheduler = await TaskManager.create_scheduler(
        title="不修改调用方 trigger",
        mode=SchedulerMode.CRON,
        trigger=trigger,
        workspace_directory=str(tmp_path / "workspace"),
    )

    assert trigger.cron == "  0 6 * * *  "
    assert scheduler.trigger.cron == "0 6 * * *"


@pytest.mark.asyncio
async def test_plugin_sync_skips_new_scheduler_with_invalid_six_field_cron(
    monkeypatch: pytest.MonkeyPatch,
):
    warn_calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        plugin_sync_module.log,
        "warn",
        lambda event, props=None: warn_calls.append((event, props or {})),
    )

    created = await upsert_task_specs(
        [
            TaskSpec(
                dedup_key="builtin:invalid-cron-new",
                title="无效 cron 新任务",
                cron="0 0 6 * * *",
                timezone="Asia/Shanghai",
            )
        ]
    )

    scheduler = await TaskStore.get_scheduler_by_dedup_key("builtin:invalid-cron-new")

    assert created == 0
    assert scheduler is None
    assert warn_calls == [
        (
            "task.plugin.invalid_cron",
            {
                "action": "skipped_entire_spec",
                "dedup_key": "builtin:invalid-cron-new",
                "cron": "0 0 6 * * *",
                "error": (
                    "Invalid cron expression: only 5-field cron is supported "
                    "(`minute hour day month weekday`). "
                    "Example: use `0 6 * * *` for daily 06:00 in Asia/Shanghai. "
                    "6-field cron (e.g. Quartz format with leading seconds, "
                    "such as `0 0 6 * * *`) and shortcuts such as `@daily` "
                    "are not supported."
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_plugin_sync_does_not_overwrite_existing_scheduler_with_invalid_six_field_cron(
    tmp_path: Path,
):
    scheduler = await TaskManager.create_scheduler(
        title="原始内置任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(
            cron="*/5 * * * *",
            timezone="Asia/Shanghai",
        ),
        workspace_directory=str(tmp_path / "workspace"),
        dedup_key="builtin:invalid-cron-existing",
    )

    created = await upsert_task_specs(
        [
            TaskSpec(
                dedup_key="builtin:invalid-cron-existing",
                title="被拒绝的更新",
                cron="0 0 6 * * *",
                timezone="Asia/Shanghai",
                enabled=False,
            )
        ]
    )

    unchanged = await TaskManager.get_scheduler(scheduler.id)

    assert created == 0
    assert unchanged is not None
    assert unchanged.title == "原始内置任务"
    assert unchanged.trigger.cron == "*/5 * * * *"
    assert unchanged.status == SchedulerStatus.ACTIVE


@pytest.mark.asyncio
async def test_update_scheduler_with_trigger_run_once_preserves_existing_cron_when_omitted(
    tmp_path: Path,
):
    scheduler = await TaskManager.create_scheduler(
        title="切成单次任务时保留旧 cron",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(
            cron="*/5 * * * *",
            timezone="Asia/Shanghai",
        ),
        workspace_directory=str(tmp_path / "workspace"),
    )

    updated = await TaskManager.update_scheduler_with_trigger(
        scheduler.id,
        fields={},
        run_once=True,
        run_at="2026-05-16T06:00:00+08:00",
    )

    assert updated is not None
    assert updated.mode == SchedulerMode.ONCE
    assert updated.trigger.cron == "*/5 * * * *"
    assert updated.trigger.run_at is not None
    assert updated.trigger.run_at.isoformat() == "2026-05-16T06:00:00+08:00"


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
    # Pause the execution loop so it cannot race the test by claiming the
    # execution before we manually simulate the orphan state (queued row
    # with no queue ref).
    TaskManager.pause_queue()
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
    async def fake_session_loop(
        _session_id: str,
        provider_id=None,
        model_id=None,
        agent_name=None,
        callbacks=None,
    ):
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
@pytest.mark.parametrize(
    ("loop_metadata", "expected"),
    [({"aborted": True}, "cancelled"), ({}, "completed")],
)
async def test_existing_session_run_stopped_from_the_session_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    loop_metadata: dict,
    expected: str,
):
    # The task session's stop button aborts the loop, which returns normally.
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None, metadata=loop_metadata))
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)

    manager = BackgroundManager()
    task = await manager.run_existing_session(
        "ses_task_stop", "scheduled", "rex", allow_user_questions=False,
    )
    finished = await manager.wait_for(task.id, timeout_ms=5000)

    assert finished is not None
    assert finished.status == expected


@pytest.mark.asyncio
async def test_task_stopped_from_its_session_is_cancelled_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="会话里停止",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    monkeypatch.setattr(
        TaskExecutor, "_create_task_session", AsyncMock(return_value="ses_stopped_task"),
    )
    monkeypatch.setattr(
        background_module.SessionLoop,
        "run",
        AsyncMock(return_value=SimpleNamespace(last_message=None, metadata={"aborted": True})),
    )

    await manager._run_execution(execution)

    stopped = await TaskManager.get_execution(execution.id)
    assert stopped.status == TaskStatus.CANCELLED
    assert stopped.retry.retry_after is None
    assert stopped.retry.retry_count == 0


@pytest.mark.asyncio
async def test_existing_background_session_pins_agent_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None))
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)

    manager = BackgroundManager()
    task = BackgroundTask(
        id="bg_identity",
        status="running",
        description="security worker",
        prompt="",
        agent="isolated-worker",
    )

    await manager._run_session_with_watchdog(
        task,
        "ses_security_worker",
        callbacks=None,
        agent_name=task.agent,
    )

    assert loop_run.await_args.args == ("ses_security_worker",)
    assert loop_run.await_args.kwargs["agent_name"] == "isolated-worker"


@pytest.mark.asyncio
async def test_background_event_progress_refreshes_inactivity_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.server.routes import event as event_routes

    publish = AsyncMock()
    monkeypatch.setattr(event_routes, "publish_event", publish)
    task = BackgroundTask(
        id="bg_progress",
        status="running",
        description="security worker",
        prompt="",
        agent="isolated-solver",
        last_activity_at=1,
    )

    callbacks = BackgroundManager()._build_activity_callbacks(task)
    assert callbacks.event_publish_callback is not None
    await callbacks.event_publish_callback("message.part.updated", {"part": {"status": "running"}})

    assert task.last_activity_at is not None and task.last_activity_at > 1
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_background_session_rejects_capsule_identity_mismatch() -> None:
    manager = BackgroundManager()

    with pytest.raises(ValueError, match="execution capsule"):
        await manager.run_existing_session(
            session_id="ses_security_worker",
            description="security worker",
            agent="isolated-worker",
            execution_capsule={
                "session_id": "ses_other",
                "agent_name": "isolated-worker",
            },
        )

    assert manager.list_tasks() == []


@pytest.mark.asyncio
async def test_existing_background_session_waits_for_start_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackgroundManager()
    run = AsyncMock(return_value=SimpleNamespace(action=None, last_message=None))
    monkeypatch.setattr(manager, "_run_session_with_watchdog", run)
    monkeypatch.setattr(manager, "_inject_parent_completion", AsyncMock())
    start_gate = asyncio.Event()

    task = await manager.run_existing_session(
        session_id="ses_security_worker",
        description="security worker",
        agent="isolated-worker",
        start_gate=start_gate,
    )
    await asyncio.sleep(0)

    assert task.status == "pending"
    run.assert_not_awaited()

    start_gate.set()
    completed = await manager.wait_for(task.id, timeout_ms=1_000)

    assert completed is task
    assert task.status == "completed"
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_background_session_preserves_loop_error_for_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackgroundManager()
    monkeypatch.setattr(
        background_module.SessionLoop,
        "run",
        AsyncMock(
            return_value=SimpleNamespace(
                action="error",
                error="provider 503 stream interrupted",
                last_message=None,
            )
        ),
    )
    monkeypatch.setattr(manager, "_inject_parent_completion", AsyncMock())
    task = BackgroundTask(
        id="bg_provider_error",
        status="pending",
        description="security worker",
        prompt="",
        agent="isolated-worker",
    )

    await manager._run_existing_session(task, "ses_security_worker")

    assert task.status == "error"
    assert task.error == "provider 503 stream interrupted"


@pytest.mark.asyncio
async def test_background_task_uses_unpinned_model_as_runtime_override(
    monkeypatch: pytest.MonkeyPatch,
):
    create_mock = AsyncMock(return_value=SimpleNamespace(id="ses_task_child"))
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None))

    monkeypatch.setattr(background_module.Session, "create", create_mock)
    monkeypatch.setattr(background_module.Message, "create", AsyncMock())
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)

    manager = BackgroundManager()
    task = BackgroundTask(
        id="bg_test_runtime_override",
        status="pending",
        description="scheduled",
        prompt="",
        agent="rex",
    )

    await manager._run_task(
        task,
        LaunchInput(
            description="scheduled",
            prompt="Run a quick check",
            agent="rex",
            parent_session_id=None,
            parent_message_id=None,
            parent_agent=None,
            model={
                "providerID": "anthropic",
                "modelID": "claude-haiku-4-5",
            },
            model_pinned=False,
            directory="/tmp/project",
            project_id="proj",
        ),
    )

    create_kwargs = create_mock.await_args.kwargs
    assert "provider" not in create_kwargs
    assert "model" not in create_kwargs
    assert "model_pinned" not in create_kwargs
    assert loop_run.await_count == 1
    assert loop_run.await_args.args == ("ses_task_child",)
    assert loop_run.await_args.kwargs["provider_id"] == "anthropic"
    assert loop_run.await_args.kwargs["model_id"] == "claude-haiku-4-5"
    assert loop_run.await_args.kwargs["callbacks"] is not None
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_background_task_completion_injects_parent_context(
    monkeypatch: pytest.MonkeyPatch,
):
    create_message = AsyncMock()
    update_part = AsyncMock()
    parent_loop_run = AsyncMock(return_value=SimpleNamespace(action="stop"))
    parent_part = ToolPart(
        id="part-parent-tool",
        sessionID="ses-parent",
        messageID="msg-parent",
        type="tool",
        callID="call-parent",
        tool="delegate_task",
        state=ToolStateCompleted(
            status="completed",
            input={
                "description": "inspect cache",
                "prompt": "Inspect cache",
                "subagent_type": "explore",
                "run_in_background": True,
            },
            output="Background task launched",
            title="inspect cache",
            metadata={
                "sessionId": "ses-child",
                "taskId": "bg_parent_inject",
                "status": "running",
                "background": True,
            },
            time={"start": 1, "end": 2},
        ),
    )
    monkeypatch.setattr(background_module.Message, "create", create_message)
    monkeypatch.setattr(background_module.Message, "parts", AsyncMock(return_value=[parent_part]))
    monkeypatch.setattr(background_module.Message, "update_part", update_part)
    monkeypatch.setattr(background_module.SessionLoop, "run", parent_loop_run)

    manager = BackgroundManager()
    task = BackgroundTask(
        id="bg_parent_inject",
        status="completed",
        description="inspect cache",
        prompt="",
        agent="explore",
        parent_session_id="ses-parent",
        parent_message_id="msg-parent",
        parent_call_id="call-parent",
        parent_agent="rex",
        parent_model={"providerID": "anthropic", "modelID": "claude-sonnet"},
        session_id="ses-child",
        output="cache keys are built in three files",
    )

    await manager._inject_parent_completion(task)

    create_message.assert_awaited_once()
    kwargs = create_message.await_args.kwargs
    assert kwargs["session_id"] == "ses-parent"
    assert kwargs["synthetic"] is True
    assert kwargs["part_metadata"]["kind"] == "background_task_result"
    assert '<task id="bg_parent_inject" session_id="ses-child" state="completed">' in kwargs["content"]
    assert "cache keys are built in three files" in kwargs["content"]
    update_part.assert_awaited_once()
    update_kwargs = update_part.await_args.kwargs
    assert update_kwargs["part_id"] == "part-parent-tool"
    assert update_kwargs["state"].status == "completed"
    assert update_kwargs["state"].metadata["status"] == "completed"
    assert update_kwargs["state"].output == "cache keys are built in three files"
    await asyncio.sleep(0)
    parent_loop_run.assert_awaited_once()
    assert parent_loop_run.await_args.kwargs["session_id"] == "ses-parent"
    assert parent_loop_run.await_args.kwargs["agent_name"] == "rex"


@pytest.mark.asyncio
async def test_background_task_completion_does_not_resume_running_parent(
    monkeypatch: pytest.MonkeyPatch,
):
    parent_loop_run = AsyncMock(return_value=SimpleNamespace(action="stop"))
    monkeypatch.setattr(background_module.SessionLoop, "run", parent_loop_run)
    monkeypatch.setattr(background_module.SessionLoop, "is_running", lambda _session_id: True)

    manager = BackgroundManager()
    task = BackgroundTask(
        id="bg_parent_running",
        status="completed",
        description="inspect cache",
        prompt="",
        agent="explore",
        parent_session_id="ses-parent",
        parent_agent="rex",
        session_id="ses-child",
        output="done",
    )

    manager._schedule_parent_resume(task)
    await asyncio.sleep(0)

    parent_loop_run.assert_not_awaited()


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
    # Only a run whose record still says running is started.
    await TaskStore.create_scheduler(scheduler)
    execution.status = TaskStatus.RUNNING
    await TaskStore.create_execution(execution)

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
async def test_executions_without_workspace_directory_get_their_own(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    frozen = datetime(2026, 10, 10, 8, 36, 33, 400000, tzinfo=timezone.utc)

    class _SameSecond(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz else frozen.replace(tzinfo=None)

    # Two "run now" clicks in the same second used to share
    # tasks/<date>/exec-<epoch seconds> and overwrite each other's files.
    monkeypatch.setattr(task_manager_module, "datetime", _SameSecond)
    first_scheduler = await TaskManager.create_scheduler(
        title="独立任务 A",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
    )
    second_scheduler = await TaskManager.create_scheduler(
        title="独立任务 B",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
    )
    pinned_scheduler = await TaskManager.create_scheduler(
        title="指定目录",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "shared"),
    )

    first = await TaskManager.rerun_scheduler(first_scheduler.id)
    second = await TaskManager.rerun_scheduler(second_scheduler.id)
    again = await TaskManager.rerun_scheduler(first_scheduler.id)
    pinned = await TaskManager.rerun_scheduler(pinned_scheduler.id)

    directories = [first.workspace_directory, second.workspace_directory, again.workspace_directory]
    assert len(set(directories)) == 3
    for execution in (first, second, again):
        assert Path(execution.workspace_directory).name == execution.id
        stored = await TaskManager.get_execution(execution.id)
        assert stored.workspace_directory == execution.workspace_directory
    assert pinned.workspace_directory == str(tmp_path / "shared")


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
async def test_cli_list_tasks_accepts_legacy_paused_status(capsys: pytest.CaptureFixture[str]):
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

    await task_cli_commands._list_tasks("paused", None, 10, "json")
    executions = json.loads(capsys.readouterr().out)
    await task_cli_commands._list_tasks("paused", "scheduled", 10, "json")
    schedulers = json.loads(capsys.readouterr().out)

    assert [item["id"] for item in executions] == [execution.id]
    assert [item["id"] for item in schedulers] == [scheduler.id]


@pytest.mark.asyncio
async def test_cli_show_execution_reads_its_snapshot(monkeypatch: pytest.MonkeyPatch):
    scheduler = await TaskManager.create_scheduler(
        title="Literal [red]TASK[/red] 执行详情",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        skills=["triage", "report"],
        category="ops",
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    execution.session_id = "ses_task_show"
    execution.result_summary = "- [x] done\n- [/x] closing tag"
    await TaskStore.update_execution(execution)

    rendered: list[object] = []
    monkeypatch.setattr(task_cli_commands.console, "print", lambda *args, **kwargs: rendered.append(args[0]))

    await task_cli_commands._show_task(execution.id)

    out = Console(file=io.StringIO(), width=200)
    out.print(rendered[0])
    text = out.file.getvalue()
    assert f"ID:       {execution.id}" in text
    assert f"Scheduler: {scheduler.id}" in text
    assert "Session:  ses_task_show" in text
    assert "Skills:   triage, report" in text
    assert "Category: ops" in text
    # Stored text is shown as written, not read as Rich markup.
    assert "Literal [red]TASK[/red] 执行详情" in text
    assert "- [x] done" in text and "- [/x] closing tag" in text


@pytest.mark.asyncio
async def test_cli_show_formats_scheduler_times_in_schedule_timezone(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler = TaskScheduler(
        title="CLI 定时任务详情",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(
            cron="0 6 * * *",
            timezone="Asia/Shanghai",
            next_run=datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc),
        ),
        created_at=datetime(2026, 5, 15, 1, 53, 8, tzinfo=timezone.utc),
    )
    await TaskStore.create_scheduler(scheduler)

    rendered: list[object] = []
    monkeypatch.setattr(task_cli_commands.console, "print", lambda *args, **kwargs: rendered.append(args[0]))

    await task_cli_commands._show_task(scheduler.id)

    assert rendered
    panel = rendered[0]
    assert "Created:  2026-05-15 09:53:08+08:00 (Asia/Shanghai)" in panel.renderable
    assert "Next run: 2026-05-16 06:00:00+08:00 (Asia/Shanghai)" in panel.renderable


@pytest.mark.asyncio
async def test_format_task_datetime_uses_utc_label_when_timezone_not_found():
    rendered = format_task_datetime(
        datetime(2026, 5, 15, 1, 53, 8, tzinfo=timezone.utc),
        "Mars/Base",
    )

    assert rendered == '2026-05-15 01:53:08+00:00 (UTC ("Mars/Base" not found))'


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


@pytest.mark.asyncio
async def test_cancel_from_a_request_stops_the_agent_run_and_does_not_requeue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The task loop's runs live in the manager of no instance; a request cancels from its own."""
    from flocks.project.instance import Instance

    background_module._manager_state.invalidate_all()
    started = asyncio.Event()
    stopped: list[str] = []

    async def hanging_loop(session_id, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append(session_id)
            raise

    monkeypatch.setattr(background_module.SessionLoop, "run", hanging_loop)
    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="ses_cancel_from_request"))
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    scheduler = await TaskManager.create_scheduler(
        title="请求里取消",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    execution.status = TaskStatus.RUNNING
    await TaskStore.update_execution(execution)
    run = asyncio.create_task(manager._run_execution(execution))
    await asyncio.wait_for(started.wait(), timeout=5)

    request_dir = tmp_path / "request-cwd"
    request_dir.mkdir()

    async def cancel_in_request() -> None:
        await TaskManager.cancel_execution(execution.id)

    await Instance.provide(directory=str(request_dir), fn=cancel_in_request)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, timeout=5)

    assert stopped == ["ses_cancel_from_request"]
    final = await TaskManager.get_execution(execution.id)
    assert final.status == TaskStatus.CANCELLED
    assert final.session_id == "ses_cancel_from_request"
    background_module._manager_state.invalidate_all()


async def _cron_execution(tmp_path: Path, title: str) -> TaskExecution:
    scheduler = await TaskManager.create_scheduler(
        title=title,
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 3 * * *", timezone="Asia/Shanghai"),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=False,
    )
    execution.status = TaskStatus.RUNNING
    return await TaskStore.update_execution(execution)


@pytest.mark.asyncio
async def test_cancel_while_the_session_is_being_created_does_not_start_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    execution = await _cron_execution(tmp_path, "创建会话时取消")
    loop_run = AsyncMock()

    async def create_session_while_cancelled(*_args, **_kwargs):
        # The cancel lands after dispatch read the execution, before the session exists.
        await TaskManager.cancel_execution(execution.id)
        return "ses_created_after_cancel"

    monkeypatch.setattr(TaskExecutor, "_create_task_session", create_session_while_cancelled)
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    await manager._run_execution(execution)

    loop_run.assert_not_awaited()
    final = await TaskManager.get_execution(execution.id)
    assert final.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_run_that_ends_after_its_cancel_keeps_the_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    execution = await _cron_execution(tmp_path, "取消后才结束")
    running = asyncio.Event()
    release = asyncio.Event()

    async def loop_until_released(_session_id, **_kwargs):
        running.set()
        await release.wait()
        return SimpleNamespace(last_message=None, metadata={})

    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="ses_late_finish"))
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_until_released)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    run = asyncio.create_task(manager._run_execution(execution))
    await asyncio.wait_for(running.wait(), timeout=5)

    # Recorded as cancelled while the run, not reached by the cancel, carries on.
    assert await TaskStore.cancel_active_execution(
        execution.id,
        completed_at=datetime.now(timezone.utc),
        duration_ms=None,
        error="Cancelled from task queue.",
    )
    release.set()
    await asyncio.wait_for(run, timeout=5)

    final = await TaskManager.get_execution(execution.id)
    assert final.status == TaskStatus.CANCELLED
    assert final.session_id == "ses_late_finish"


@pytest.mark.asyncio
async def test_cancel_by_session_leaves_finished_background_tasks_alone():
    manager = BackgroundManager()

    async def _noop() -> None:
        return None

    finished = asyncio.create_task(_noop())
    await finished
    manager._tasks["bg_done"] = BackgroundTask(
        id="bg_done", status="completed", description="d", prompt="p", agent="rex",
        session_id="ses_done", completed_at=1_700_000_000_000,
    )
    manager._task_handles["bg_done"] = finished

    assert manager.cancel_by_session_id("ses_done") == 0
    assert (manager._tasks["bg_done"].status, manager._tasks["bg_done"].completed_at) == ("completed", 1_700_000_000_000)


@pytest.mark.asyncio
async def test_cancel_by_session_spares_a_completed_task_still_reporting_to_its_parent():
    manager = BackgroundManager()
    reporting = asyncio.Event()

    async def _inject_parent_completion() -> None:
        await reporting.wait()

    handle = asyncio.create_task(_inject_parent_completion())
    manager._tasks["bg_reporting"] = BackgroundTask(
        id="bg_reporting", status="completed", description="d", prompt="p", agent="rex",
        session_id="ses_reporting", parent_session_id="ses_parent", completed_at=1_700_000_000_000,
    )
    manager._task_handles["bg_reporting"] = handle

    try:
        assert manager.cancel_by_session_id("ses_reporting") == 0
        await asyncio.sleep(0)
        assert not handle.cancelled()
        assert manager._tasks["bg_reporting"].status == "completed"
    finally:
        reporting.set()
        await handle


@pytest.mark.asyncio
async def test_cancel_before_the_agent_run_is_registered_still_stops_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import flocks.server.routes.event as event_routes

    execution = await _cron_execution(tmp_path, "登记运行前取消")
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None, metadata={}))
    original_publish = event_routes.publish_event

    async def publish_then_cancel(event, payload, *args, **kwargs):
        # Dispatch announces the run (record running, session set) before any
        # run exists; the cancel lands right there.
        if event == "task.updated" and payload.get("status") == "running":
            await TaskManager.cancel_execution(execution.id)
            return None
        return await original_publish(event, payload, *args, **kwargs)

    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="ses_before_register"))
    monkeypatch.setattr(event_routes, "publish_event", publish_then_cancel)
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    await manager._run_execution(execution)
    await asyncio.sleep(0)

    loop_run.assert_not_awaited()
    final = await TaskManager.get_execution(execution.id)
    assert final.status == TaskStatus.CANCELLED
    assert final.session_id == "ses_before_register"


@pytest.mark.asyncio
async def test_cancel_before_the_workflow_run_is_registered_still_stops_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.workflow import fs_store, runner, tool_context

    execution = await _cron_execution(tmp_path, "工作流登记前取消")
    execution.execution_mode = ExecutionMode.WORKFLOW
    execution.workflow_id = "wf-before-register"
    execution = await TaskStore.update_execution(execution)
    started: list[dict] = []

    async def build_context_then_cancel(**_kwargs):
        # The cancel lands while the context is built, before the workflow runs.
        await TaskManager.cancel_execution(execution.id)
        return None

    def run_workflow(**kwargs):
        started.append(kwargs)
        return SimpleNamespace(status="SUCCEEDED", error=None, outputs={"ok": True})

    monkeypatch.setattr(
        fs_store,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"name": "w", "start": "s", "nodes": [], "edges": []}},
    )
    monkeypatch.setattr(tool_context, "build_workflow_tool_context", build_context_then_cancel)
    monkeypatch.setattr(runner, "run_workflow", run_workflow)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    await manager._run_execution(execution)

    assert started == []
    final = await TaskManager.get_execution(execution.id)
    assert final.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_then_delete_before_the_agent_run_is_registered_still_stops_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import flocks.server.routes.event as event_routes

    execution = await _cron_execution(tmp_path, "取消并删除")
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None, metadata={}))
    original_publish = event_routes.publish_event

    async def publish_then_delete(event, payload, *args, **kwargs):
        if event == "task.updated" and payload.get("status") == "running":
            # Deleting a running execution cancels it, then removes the row.
            await TaskManager.delete_execution(execution.id)
            return None
        return await original_publish(event, payload, *args, **kwargs)

    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="ses_deleted"))
    monkeypatch.setattr(event_routes, "publish_event", publish_then_delete)
    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)
    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    await manager._run_execution(execution)
    await asyncio.sleep(0)

    loop_run.assert_not_awaited()
    assert await TaskManager.get_execution(execution.id) is None


@pytest.mark.asyncio
async def test_a_failed_record_read_does_not_leave_the_held_agent_run_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    execution = await _cron_execution(tmp_path, "读取记录失败")
    loop_run = AsyncMock(return_value=SimpleNamespace(last_message=None, metadata={}))

    async def unreadable(_execution_id: str) -> bool:
        raise RuntimeError("record read failed")

    monkeypatch.setattr(background_module.SessionLoop, "run", loop_run)
    monkeypatch.setattr(TaskExecutor, "_still_running", staticmethod(unreadable))

    with pytest.raises(RuntimeError, match="record read failed"):
        await TaskExecutor._run_agent_session(execution, "ses_unreadable")
    await asyncio.sleep(0)

    held = [
        (manager, task)
        for manager in background_module.all_background_managers()
        for task in manager.list_tasks()
        if task.session_id == "ses_unreadable"
    ]
    assert len(held) == 1
    manager, task = held[0]
    assert task.status == "cancelled"
    assert manager._task_handles[task.id].done()
    loop_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cancelled_dispatch_keeps_the_running_workflow_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import threading

    import flocks.task.executor as executor_module
    from flocks.workflow import fs_store, runner, tool_context

    execution = await _cron_execution(tmp_path, "工作流线程还在跑")
    execution.execution_mode = ExecutionMode.WORKFLOW
    execution.workflow_id = "wf-still-running"
    execution = await TaskStore.update_execution(execution)
    scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
    started = threading.Event()
    release = threading.Event()
    cancel_seen: list[bool] = []

    def run_workflow(*, cancel, **_kwargs):
        started.set()
        release.wait(5)
        cancel_seen.append(cancel())
        return SimpleNamespace(status="CANCELLED", error="cancelled", outputs=None)

    monkeypatch.setattr(
        fs_store,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"name": "w", "start": "s", "nodes": [], "edges": []}},
    )
    monkeypatch.setattr(tool_context, "build_workflow_tool_context", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "run_workflow", run_workflow)

    dispatch = asyncio.create_task(TaskExecutor._trigger_workflow(execution, scheduler))
    assert await asyncio.to_thread(started.wait, 5)
    dispatch.cancel()  # what the dispatch guard's timeout does
    with pytest.raises(asyncio.CancelledError):
        await dispatch

    try:
        # The thread still runs; the manager's follow-up cancel has to reach it.
        assert TaskExecutor.request_runtime_cancel(execution) is True
    finally:
        release.set()
    for _ in range(250):
        if execution.id not in executor_module._workflow_cancel_events:
            break
        await asyncio.sleep(0.02)

    assert cancel_seen == [True]
    assert execution.id not in executor_module._workflow_cancel_events
    assert execution.id not in executor_module._workflow_done_events


def test_a_workflow_cancelled_while_queued_for_a_thread_never_starts(monkeypatch: pytest.MonkeyPatch):
    import threading

    import flocks.task.executor as executor_module
    from flocks.task.executor import TaskExecutionStopped
    from flocks.workflow import runner

    started: list[dict] = []
    monkeypatch.setattr(runner, "run_workflow", lambda **kwargs: started.append(kwargs))
    cancel_event = threading.Event()
    done_event = threading.Event()
    with executor_module._workflow_cancel_lock:
        executor_module._workflow_cancel_events["txe_queued"] = cancel_event
        executor_module._workflow_done_events["txe_queued"] = done_event
    cancel_event.set()  # the dispatch was cancelled before a pool thread picked this up

    with pytest.raises(TaskExecutionStopped):
        TaskExecutor._run_workflow_sync("txe_queued", {"nodes": []}, {}, None, cancel_event, done_event)

    assert started == []
    assert done_event.is_set()
    assert "txe_queued" not in executor_module._workflow_cancel_events
    assert "txe_queued" not in executor_module._workflow_done_events
