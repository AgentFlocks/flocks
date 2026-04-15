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
from flocks.task.models import ExecutionTriggerType, SchedulerMode, TaskStatus, TaskTrigger
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


@pytest.mark.asyncio
async def test_standalone_legacy_migration_script_migrates_existing_tables(tmp_path: Path):
    db = await TaskStore.raw_db()
    await db.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            type TEXT,
            status TEXT,
            priority TEXT,
            source TEXT,
            schedule TEXT,
            execution_mode TEXT,
            agent_name TEXT,
            workflow_id TEXT,
            skills TEXT,
            category TEXT,
            context TEXT,
            workspace_directory TEXT,
            retry TEXT,
            tags TEXT,
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT,
            dedup_key TEXT,
            delivery_status TEXT,
            execution TEXT
        );
        CREATE TABLE task_execution_records (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            status TEXT,
            delivery_status TEXT,
            started_at TEXT,
            completed_at TEXT,
            duration_ms INTEGER,
            session_id TEXT,
            result_summary TEXT,
            error TEXT
        );
        CREATE TABLE task_queue_refs (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            execution_record_id TEXT,
            status TEXT,
            created_at TEXT,
            started_at TEXT
        );
        """
    )

    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(minutes=5)).isoformat()
    started_at = (now - timedelta(minutes=4)).isoformat()
    completed_at = (now - timedelta(minutes=3)).isoformat()
    await db.execute(
        """
        INSERT INTO tasks (
            id, title, description, type, status, priority, source, schedule,
            execution_mode, agent_name, context, workspace_directory, retry,
            tags, created_at, updated_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "task_legacy_1",
            "旧计划任务",
            "legacy description",
            "scheduled",
            "completed",
            "normal",
            json.dumps({"sourceType": "user_prompt", "userPrompt": "hello"}),
            json.dumps({"runOnce": True, "runAt": created_at, "enabled": True}),
            "agent",
            "rex",
            json.dumps({"from": "legacy"}),
            str(tmp_path / "workspace"),
            json.dumps({"maxRetries": 0, "retryDelaySeconds": 60, "retryCount": 0}),
            json.dumps(["legacy"]),
            created_at,
            started_at,
            "migration",
        ),
    )
    await db.execute(
        """
        INSERT INTO task_execution_records (
            id, task_id, status, delivery_status, started_at, completed_at,
            duration_ms, session_id, result_summary, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "texec_legacy_1",
            "task_legacy_1",
            "completed",
            "unread",
            started_at,
            completed_at,
            60000,
            "ses_123",
            "done",
            None,
        ),
    )
    await db.execute(
        """
        INSERT INTO task_queue_refs (
            id, task_id, execution_record_id, status, created_at, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "tqref_legacy_1",
            "task_legacy_1",
            "texec_legacy_1",
            "completed",
            created_at,
            started_at,
        ),
    )
    await db.commit()

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy_task_tables.py"
    state_path = tmp_path / "task_migration_state.json"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        "--db",
        str(Storage.get_db_path()),
        "--state-file",
        str(state_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, (stdout or b"").decode() + (stderr or b"").decode()

    scheduler = await TaskManager.get_scheduler("task_legacy_1")
    execution = await TaskManager.get_execution("texec_legacy_1")

    assert scheduler is not None
    assert scheduler.mode == SchedulerMode.ONCE
    assert execution is not None
    assert execution.scheduler_id == "task_legacy_1"
    assert execution.status == TaskStatus.COMPLETED
    assert execution.session_id == "ses_123"
    assert TaskManager._legacy_tables_exist() is False
    assert state_path.exists() is False
