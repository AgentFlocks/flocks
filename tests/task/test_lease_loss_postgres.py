"""Real PostgreSQL ownership fences and runtime cancellation regressions."""
from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from flocks.task.executor import TaskExecutor
from flocks.task.lease import (
    ExecutionLease, ExecutionOwnershipError, current_execution_cancelled, require_current_execution,
)
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionMode, TaskExecution, TaskScheduler, TaskStatus
from flocks.task.node import process_owner_id, reset_node_id_cache
from flocks.task.store import TaskStore


@pytest.fixture(autouse=True)
async def private_store():
    if not os.environ.get("FLOCKS_TEST_PG_DSN"):
        pytest.skip("requires isolated PostgreSQL test DSN")
    await TaskStore.close()
    reset_node_id_cache()
    await TaskStore.init()
    db = await TaskStore._db()
    assert (await db.fetch_one("SELECT current_schema()"))[0].startswith("test_")
    yield
    await TaskStore.close()
    reset_node_id_cache()


async def make_run(mode=ExecutionMode.WORKFLOW):
    scheduler = TaskScheduler(title="lease regression", executionMode=mode, workflowID="isolated")
    await TaskStore.create_scheduler(scheduler)
    execution = TaskExecution(
        schedulerID=scheduler.id, title="lease regression", status=TaskStatus.RUNNING,
        executionMode=mode, workflowID="isolated", startedAt=datetime.now(timezone.utc),
    )
    await TaskStore.create_execution(execution)
    return execution, scheduler


async def expire(execution_id):
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution_id),
    )


async def worker(tmp_path, execution_id):
    home = tmp_path / ("worker-" + str(time.monotonic_ns()))
    home.mkdir()
    script = """
import asyncio, json, os
from pathlib import Path
from flocks.task.manager import TaskManager
from flocks.task.node import current_node_id, process_owner_id
from flocks.db.connect import close_pools
from flocks.task.store import TaskStore
async def main():
    assert Path.home() == Path(os.environ['HOME'])
    assert os.environ['PYTHON_DOTENV_DISABLED'] == '1'
    db = await TaskStore._db()
    schema = (await db.fetch_one('SELECT current_schema()'))[0]
    assert schema.startswith('test_')
    count = await TaskManager()._recover_orphaned_executions()
    print('RESULT=' + json.dumps({'count':count, 'node':current_node_id(), 'owner':process_owner_id(), 'schema':schema}))
    await TaskStore.close()
    await close_pools()
asyncio.run(main())
"""
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(home), "USERPROFILE": str(home),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "FLOCKS_DB_BACKEND": "postgres", "FLOCKS_DB_DSN": os.environ["FLOCKS_DB_DSN"],
        "FLOCKS_NODE_ID": "shared-host",
    }
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, env=env, cwd=home,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(child.communicate(), 20)
    assert child.returncode == 0, stdout.decode()
    return json.loads(next(line[7:] for line in stdout.decode().splitlines() if line.startswith("RESULT=")))


async def test_same_hostname_process_startup_preserves_live_then_recovers_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_NODE_ID", "shared-host")
    reset_node_id_cache()
    execution, _ = await make_run()
    await TaskStore.enqueue_execution_ref(execution.id)
    owner = process_owner_id()
    async with ExecutionLease(execution.id, lease_s=60, heartbeat_s=.1) as live:
        first = await worker(tmp_path, execution.id)
        assert first["node"] == "shared-host" and first["owner"] != owner
        assert first["count"] == 0
        current = await TaskStore.get_execution(execution.id)
        assert current.owner_generation == live.generation and current.status == TaskStatus.RUNNING
        assert current.owner_node_id == owner and current.lease_is_live() and not live.lost
    claim = await TaskStore.claim_execution(execution.id, node_id=owner, lease_seconds=60)
    await expire(execution.id)
    second = await worker(tmp_path, execution.id)
    assert second["owner"] != first["owner"] and second["count"] == 1
    current = await TaskStore.get_execution(execution.id)
    # Abandoned, so failed -- not started again from scratch: whether it did
    # part of its work is unknown (the retry policy decides about a rerun).
    assert current.status == TaskStatus.FAILED and current.owner_generation > claim.owner_generation
    assert "no live owner" in (current.error or "")


@pytest.mark.parametrize("recovery", ["_recover_orphaned_executions", "_recover_stale_active_executions"])
async def test_recovery_preserves_recent_dequeue_before_claim(recovery):
    execution, _ = await make_run()
    assert await getattr(TaskManager(), recovery)() == 0
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
    assert await TaskStore.requeue_running_refs() == 0


@pytest.mark.parametrize("fault", ["exception", "stall"])
async def test_renew_failure_stops_at_local_deadline(monkeypatch, fault):
    execution, _ = await make_run()
    attempts = 0
    async def broken(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if fault == "stall":
            await asyncio.Event().wait()
        raise OSError("isolated renewal failure")
    monkeypatch.setattr(TaskStore, "renew_execution_lease", broken)
    started = time.monotonic()
    with pytest.raises(ExecutionOwnershipError):
        async with ExecutionLease(execution.id, lease_s=1, heartbeat_s=.08):
            await asyncio.sleep(3)
            pytest.fail("lease loss must interrupt the active await")
    assert .8 <= time.monotonic() - started < 2
    assert 1 <= attempts < 16


async def test_transient_renewal_failure_recovers_before_deadline(monkeypatch):
    execution, _ = await make_run()
    original = TaskStore.renew_execution_lease
    attempts = 0
    async def intermittent(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("one transient error")
        return await original(*args, **kwargs)
    monkeypatch.setattr(TaskStore, "renew_execution_lease", intermittent)
    async with ExecutionLease(execution.id, lease_s=1, heartbeat_s=.1) as lease:
        await asyncio.sleep(1.3)
        lease.require_owned()
        assert not lease.lost and attempts > 2


async def test_expired_lease_cannot_be_revived_by_renewal():
    execution, _ = await make_run()
    claim = await TaskStore.claim_execution(execution.id, node_id="old", lease_seconds=30)
    await expire(execution.id)
    assert not await TaskStore.renew_execution_lease(
        execution.id, node_id="old", generation=claim.owner_generation, lease_seconds=30,
    )


async def test_workflow_thread_deadline_does_not_depend_on_event_loop_monitor():
    require_current_execution()  # Ordinary sessions have no task lease to check.
    execution, _ = await make_run()
    observed = []
    done = threading.Event()
    def check_from_thread():
        try:
            time.sleep(1.05)
            observed.append(current_execution_cancelled())
        finally:
            done.set()
    with pytest.raises(ExecutionOwnershipError):
        async with ExecutionLease(execution.id, lease_s=1, heartbeat_s=.1) as lease:
            context = contextvars.copy_context()
            worker_thread = threading.Thread(target=context.run, args=(check_from_thread,))
            worker_thread.start()
            # Deliberate fault injection: monitor tasks cannot run during this pause.
            time.sleep(1.15)
            assert done.is_set() and observed == [True]
            require_current_execution()
    await asyncio.to_thread(worker_thread.join, 2)


async def test_completion_is_serialized_with_heartbeat(monkeypatch):
    execution, _ = await make_run()
    original = TaskStore.finish_execution
    async def delayed_return(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(.15)
        return result
    monkeypatch.setattr(TaskStore, "finish_execution", delayed_return)
    async with ExecutionLease(execution.id, lease_s=2, heartbeat_s=.02) as lease:
        execution.status = TaskStatus.COMPLETED
        assert await lease.finish_execution(execution)
        await asyncio.sleep(.05)
        assert not lease.lost
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED


async def test_manual_manager_cancellation_requeues_same_generation(monkeypatch):
    from unittest.mock import AsyncMock
    execution, _ = await make_run()
    await TaskStore.enqueue_execution_ref(execution.id)
    entered = asyncio.Event()
    async def work(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", work)
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_execution_update", AsyncMock())
    running = asyncio.create_task(manager._run_execution(execution))
    await asyncio.wait_for(entered.wait(), 3)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.QUEUED and current.owner_generation == 1
    assert current.owner_node_id is None
    assert (await TaskStore.get_queue_ref(execution.id)).status == TaskStatus.QUEUED


@pytest.mark.parametrize("cause", ["takeover", "deadline", "manual"])
async def test_real_workflow_does_not_start_next_node_after_loss(tmp_path, monkeypatch, cause):
    from flocks.task import executor as module
    execution, scheduler = await make_run()
    ready, release, effect = (tmp_path / name for name in ("ready", "release", "effect"))
    workflow = {"start": "before", "nodes": [
        {"id": "before", "type": "python", "code":
         "import time\nfrom pathlib import Path\nPath(inputs['ready']).write_text('ready')\n"
         "deadline=time.monotonic()+8\nwhile not Path(inputs['release']).exists():\n"
         "    if time.monotonic()>deadline: raise RuntimeError('test deadline')\n"
         "    time.sleep(.01)\noutputs['effect']=inputs['effect']"},
        {"id": "after", "type": "python", "code":
         "from pathlib import Path\nPath(inputs['effect']).write_text('should not run')"},
    ], "edges": [{"from": "before", "to": "after"}]}
    done = threading.Event()
    result = []
    def run():
        try:
            result.append(TaskExecutor._run_workflow_sync(execution.id, workflow, {
                "ready": str(ready), "release": str(release), "effect": str(effect),
            }))
        finally:
            done.set()
    async def trigger(*args):
        await asyncio.to_thread(run)
        return "done"
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", trigger)
    monkeypatch.setattr(module, "ExecutionLease", lambda id: ExecutionLease(id, lease_s=2, heartbeat_s=.05))
    if cause == "deadline":
        async def stall(*args, **kwargs):
            await asyncio.Event().wait()
        monkeypatch.setattr(TaskStore, "renew_execution_lease", stall)
    running = asyncio.create_task(TaskExecutor.dispatch(execution, scheduler))
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(.01)
        if cause == "takeover":
            await expire(execution.id)
            assert await TaskStore.claim_execution(execution.id, node_id="peer", lease_seconds=30)
        elif cause == "manual":
            running.cancel()
        expected = asyncio.CancelledError if cause == "manual" else ExecutionOwnershipError
        with pytest.raises(expected):
            await asyncio.wait_for(running, 5)
    finally:
        release.write_text("released")
        assert await asyncio.to_thread(done.wait, 5)
        if not running.done():
            running.cancel()
    assert not effect.exists()
    assert result[0].status == "CANCELLED"
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.RUNNING
    if cause == "takeover":
        assert current.owner_node_id == "peer" and current.owner_generation == 2


async def test_lease_cancellation_propagates_through_background_manager(monkeypatch):
    from flocks.task import executor as module
    from flocks.task.background import BackgroundManager
    from flocks.session.session import SessionInfo
    from flocks.session.session_loop import SessionLoop
    from flocks.session.session import Session
    from unittest.mock import AsyncMock
    execution, scheduler = await make_run(ExecutionMode.AGENT)
    manager = BackgroundManager()
    ready, cancelled = asyncio.Event(), asyncio.Event()
    effects = []
    async def agent_run(*args, **kwargs):
        ready.set()
        try:
            await asyncio.Event().wait()
            effects.append("continued")
        finally:
            cancelled.set()
    monkeypatch.setattr(SessionLoop, "run", agent_run)
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=SessionInfo(id="lease-agent", projectID="p", directory="/tmp")))
    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="lease-agent"))
    monkeypatch.setattr("flocks.task.background.get_background_manager", lambda: manager)
    monkeypatch.setattr(module, "ExecutionLease", lambda id: ExecutionLease(id, lease_s=2, heartbeat_s=.05))
    running = asyncio.create_task(TaskExecutor.dispatch(execution, scheduler))
    await asyncio.wait_for(ready.wait(), 3)
    await expire(execution.id)
    assert await TaskStore.claim_execution(execution.id, node_id="peer", lease_seconds=30)
    with pytest.raises(ExecutionOwnershipError):
        await asyncio.wait_for(running, 3)
    await asyncio.wait_for(cancelled.wait(), 3)
    assert not effects
