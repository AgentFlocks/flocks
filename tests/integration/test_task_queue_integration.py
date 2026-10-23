from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import flocks.task.manager as task_manager_module
from flocks.task.models import ExecutionMode
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.executor import TaskExecutor
from flocks.task.manager import TaskManager
from flocks.task.models import (
    ExecutionTriggerType,
    SchedulerMode,
    SchedulerStatus,
    TaskStatus,
    TaskTrigger,
)
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


async def _wait_for_execution(execution_id: str, *, status: TaskStatus, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        execution = await TaskManager.get_execution(execution_id)
        if execution is not None and execution.status == status:
            return execution
        await asyncio.sleep(0.02)
    raise AssertionError(f"execution {execution_id} did not reach {status.value}")


async def _stop_and_drain_execution_workers(timeout: float = 5.0) -> None:
    """Stop dispatching, then wait for every TaskManager._run_execution to return.

    A worker still running after the fixture closes TaskStore would reopen it
    outside tmp_path (its status can read completed before the worker is done).
    """
    await TaskManager.stop()
    workers = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "") == "TaskManager._run_execution"
    ]
    if workers:
        _done, pending = await asyncio.wait(workers, timeout=timeout)
        assert not pending, f"{len(pending)} execution workers still running"


@pytest.mark.asyncio
async def test_immediate_scheduler_runs_through_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fake_dispatch(execution, scheduler):
        execution.started_at = datetime.now(timezone.utc)
        execution.status = TaskStatus.COMPLETED
        execution.completed_at = execution.started_at + timedelta(milliseconds=10)
        execution.duration_ms = 10
        execution.result_summary = f"{scheduler.title} done"
        return await TaskStore.update_execution(execution)

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    await TaskManager.start(max_concurrent=1, poll_interval=0.01, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="立即执行链路",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
    )
    executions, total = await TaskManager.list_scheduler_executions(scheduler.id)

    assert total == 1
    completed = await _wait_for_execution(executions[0].id, status=TaskStatus.COMPLETED)

    assert completed.scheduler_id == scheduler.id
    assert completed.queued_at is not None
    assert completed.started_at is not None
    assert completed.completed_at is not None
    assert completed.queued_at <= completed.started_at <= completed.completed_at
    assert completed.result_summary == "立即执行链路 done"


@pytest.mark.asyncio
async def test_default_concurrency_runs_ten_executions_and_queues_the_eleventh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    release = asyncio.Event()
    started: list[str] = []

    async def fake_dispatch(execution, scheduler):
        started.append(execution.id)
        await release.wait()
        execution.status = TaskStatus.COMPLETED
        execution.completed_at = datetime.now(timezone.utc)
        return await TaskStore.update_execution(execution)

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    try:
        await TaskManager.start(poll_interval=0.01, scheduler_interval=999)
        scheduler = await TaskManager.create_scheduler(
            title="默认并发上限",
            mode=SchedulerMode.ONCE,
            trigger=TaskTrigger(run_immediately=False),
            workspace_directory=str(tmp_path / "workspace"),
        )
        executions = [
            await TaskManager.create_execution_from_scheduler(
                scheduler,
                trigger_type=ExecutionTriggerType.RUN_ONCE,
                enqueue=True,
            )
            for _ in range(11)
        ]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while len(started) < 10 and loop.time() < deadline:
            await asyncio.sleep(0.02)
        assert len(started) == 10
        # Leave the loop many more polls to (wrongly) start the 11th execution.
        await asyncio.sleep(0.3)
        assert len(started) == 10

        (waiting_id,) = {execution.id for execution in executions} - set(started)
        waiting = await TaskManager.get_execution(waiting_id)
        assert waiting is not None
        assert waiting.status == TaskStatus.QUEUED
        queue_status = await TaskManager.queue_status()
        assert queue_status["max_concurrent"] == 10
        assert queue_status["running"] == 10
        assert queue_status["queued"] == 1

        release.set()
        await _wait_for_execution(waiting_id, status=TaskStatus.COMPLETED)
        assert started[-1] == waiting_id
    finally:
        release.set()
        await _stop_and_drain_execution_workers()


@pytest.mark.asyncio
async def test_one_poll_starts_every_execution_that_fits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    release = asyncio.Event()
    started: list[str] = []

    async def fake_dispatch(execution, scheduler):
        started.append(execution.id)
        await release.wait()
        execution.status = TaskStatus.COMPLETED
        execution.completed_at = datetime.now(timezone.utc)
        return await TaskStore.update_execution(execution)

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    try:
        scheduler = await TaskManager.create_scheduler(
            title="一次轮询派满",
            mode=SchedulerMode.ONCE,
            trigger=TaskTrigger(run_immediately=False),
            workspace_directory=str(tmp_path / "workspace"),
        )
        executions = [
            await TaskManager.create_execution_from_scheduler(
                scheduler,
                trigger_type=ExecutionTriggerType.RUN_ONCE,
                enqueue=True,
            )
            for _ in range(11)
        ]

        # The next poll is 999s away, so only the first one can start anything.
        await TaskManager.start(poll_interval=999, scheduler_interval=999)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while len(started) < 10 and loop.time() < deadline:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)

        assert len(started) == 10
        (waiting_id,) = {execution.id for execution in executions} - set(started)
        waiting = await TaskManager.get_execution(waiting_id)
        assert waiting is not None
        assert waiting.status == TaskStatus.QUEUED
    finally:
        release.set()
        await _stop_and_drain_execution_workers()


@pytest.mark.asyncio
async def test_rerun_execution_creates_new_execution_with_same_scheduler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fake_dispatch(execution, scheduler):
        execution.started_at = datetime.now(timezone.utc)
        execution.status = TaskStatus.COMPLETED
        execution.completed_at = execution.started_at + timedelta(milliseconds=5)
        execution.duration_ms = 5
        return await TaskStore.update_execution(execution)

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    await TaskManager.start(max_concurrent=1, poll_interval=0.01, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="重复执行",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace"),
    )
    first = (await TaskManager.list_scheduler_executions(scheduler.id))[0][0]
    first = await _wait_for_execution(first.id, status=TaskStatus.COMPLETED)

    rerun = await TaskManager.rerun_execution(first.id)
    assert rerun is not None
    rerun = await _wait_for_execution(rerun.id, status=TaskStatus.COMPLETED)

    assert rerun.id != first.id
    assert rerun.scheduler_id == first.scheduler_id
    assert rerun.trigger_type == ExecutionTriggerType.RERUN


@pytest.mark.asyncio
async def test_dispatch_timeout_releases_queue_for_following_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls = {"count": 0}

    async def fake_dispatch(execution, scheduler):
        calls["count"] += 1
        execution.started_at = datetime.now(timezone.utc)
        execution.status = TaskStatus.RUNNING
        await TaskStore.update_execution(execution)

        if calls["count"] == 1:
            await asyncio.sleep(0.2)

        execution.status = TaskStatus.COMPLETED
        execution.completed_at = execution.started_at + timedelta(milliseconds=10)
        execution.duration_ms = 10
        execution.result_summary = f"{scheduler.title} done"
        return await TaskStore.update_execution(execution)

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    monkeypatch.setattr(task_manager_module, "_DISPATCH_GUARD_TIMEOUT_S", 0.05)
    await TaskManager.start(max_concurrent=1, poll_interval=0.01, scheduler_interval=999)

    first_scheduler = await TaskManager.create_scheduler(
        title="超时任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace-1"),
    )
    second_scheduler = await TaskManager.create_scheduler(
        title="后续任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        workspace_directory=str(tmp_path / "workspace-2"),
    )

    first = (await TaskManager.list_scheduler_executions(first_scheduler.id))[0][0]
    second = (await TaskManager.list_scheduler_executions(second_scheduler.id))[0][0]

    failed = await _wait_for_execution(first.id, status=TaskStatus.FAILED, timeout=1.0)
    completed = await _wait_for_execution(second.id, status=TaskStatus.COMPLETED, timeout=1.0)

    assert "hard timeout" in (failed.error or "")
    assert completed.result_summary == "后续任务 done"


@pytest.mark.asyncio
async def test_delete_scheduler_releases_claimed_queue_slot(tmp_path: Path):
    await TaskManager.start(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="删除释放槽位",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace-1"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )

    manager = TaskManager.get()
    assert manager is not None

    claimed = await manager.queue.dequeue()

    assert claimed is not None
    assert claimed.id == execution.id
    assert execution.id in manager.queue._running_ids

    deleted = await TaskManager.delete_scheduler(scheduler.id)

    assert deleted is True
    assert execution.id not in manager.queue._running_ids
    assert await TaskManager.get_execution(execution.id) is None
    assert await TaskStore.get_queue_ref(execution.id) is None

    next_scheduler = await TaskManager.create_scheduler(
        title="后续可领取",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace-2"),
    )
    next_execution = await TaskManager.create_execution_from_scheduler(
        next_scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )

    next_claimed = await manager.queue.dequeue()

    assert next_claimed is not None
    assert next_claimed.id == next_execution.id


@pytest.mark.asyncio
async def test_cancelled_worker_requeues_execution_instead_of_losing_queue_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    async def fake_dispatch(_execution, _scheduler):
        raise asyncio.CancelledError()

    monkeypatch.setattr(TaskExecutor, "dispatch", fake_dispatch)
    await TaskManager.start(max_concurrent=1, poll_interval=999, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="取消后回队",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
        workspace_directory=str(tmp_path / "workspace"),
    )
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler,
        trigger_type=ExecutionTriggerType.RUN_ONCE,
        enqueue=True,
    )

    manager = TaskManager.get()
    assert manager is not None

    claimed = await manager.queue.dequeue()
    assert claimed is not None
    assert claimed.id == execution.id

    with pytest.raises(asyncio.CancelledError):
        await manager._run_execution(claimed)

    refreshed = await TaskManager.get_execution(execution.id)
    queue_ref = await TaskStore.get_queue_ref(execution.id)

    assert refreshed is not None
    assert refreshed.status == TaskStatus.QUEUED
    assert refreshed.started_at is None
    assert refreshed.completed_at is None
    assert refreshed.session_id is None
    assert queue_ref is not None
    assert queue_ref.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_workflow_timeout_signals_cancel_and_stops_before_next_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from flocks.workflow import fs_store

    side_effect_file = tmp_path / "step2.txt"
    workflow_json = {
        "name": "slow-cancellable-workflow",
        "start": "step1",
        "nodes": [
            {
                "id": "step1",
                "type": "python",
                "code": "import time\ntime.sleep(0.05)\noutputs['value'] = 1",
            },
            {
                "id": "step2",
                "type": "python",
                "code": (
                    f"from pathlib import Path\n"
                    f"Path({str(side_effect_file)!r}).write_text('ran', encoding='utf-8')\n"
                    "outputs['value'] = inputs['value'] + 1"
                ),
            },
        ],
        "edges": [
            {"from": "step1", "to": "step2"},
        ],
    }

    monkeypatch.setattr(
        fs_store,
        "read_workflow_from_fs",
        lambda workflow_id: {"workflowJson": workflow_json} if workflow_id == "wf_slow_cancel" else None,
    )
    monkeypatch.setattr(task_manager_module, "_DISPATCH_GUARD_TIMEOUT_S", 0.01)
    await TaskManager.start(max_concurrent=1, poll_interval=0.01, scheduler_interval=999)

    scheduler = await TaskManager.create_scheduler(
        title="可取消 workflow",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
        execution_mode=ExecutionMode.WORKFLOW,
        workflow_id="wf_slow_cancel",
        workspace_directory=str(tmp_path / "workspace"),
    )

    execution = (await TaskManager.list_scheduler_executions(scheduler.id))[0][0]
    failed = await _wait_for_execution(execution.id, status=TaskStatus.FAILED, timeout=1.0)
    await asyncio.sleep(0.15)

    assert "hard timeout" in (failed.error or "")
    assert not side_effect_file.exists()


# The standalone legacy-task migration script (scripts/migrate_legacy_task_tables.py)
# writes a separate normalized database instead of dropping the legacy tables;
# tests/scripts/test_legacy_task_migration.py covers it.
