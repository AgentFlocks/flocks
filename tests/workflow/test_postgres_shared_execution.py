"""Shared scheduler/workflow/session contracts on an isolated real PostgreSQL schema.

Schemas are deliberately retained for inspection. No SQLite fallback is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest

from flocks.config.config import Config
from flocks.db import registry
from flocks.db.active import reset
from flocks.db.connect import close_pools
from flocks.session.session import Session, SessionInfo
from flocks.storage.storage import Storage
from flocks.task.manager import TaskManager
from flocks.task.models import SchedulerMode, TaskScheduler, TaskStatus, TaskTrigger
from flocks.task.scheduler import TaskScheduler as SchedulerLoop
from flocks.task.store import TaskStore
from flocks.workflow.execution_store import build_initial_execution_record, create_execution_record, record_execution_result
from flocks.workflow.ownership import WorkflowAlreadyRunning, WorkflowOwnershipError, execution_cancelled
from flocks.workflow.poller_manager import WorkflowPollerManager
from flocks.workflow.store import WorkflowStore

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is required")]


@pytest.fixture
async def shared_pg(tmp_path, monkeypatch):
    schema = "shared_execution_" + uuid4().hex
    connection = await asyncpg.connect(PG_DSN, timeout=10)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    parts = urlsplit(PG_DSN)
    query = dict(parse_qsl(parts.query), search_path=schema + ",public")
    dsn = urlunsplit(parts._replace(query=urlencode(query)))
    monkeypatch.setenv("FLOCKS_DB_BACKEND", "postgres")
    monkeypatch.delenv("FLOCKS_DB_DSN_FILE", raising=False)
    monkeypatch.setenv("FLOCKS_DB_DSN", dsn)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("FLOCKS_TASK_LEASE_SECONDS", "6")
    monkeypatch.setenv("FLOCKS_TASK_HEARTBEAT_SECONDS", "1")
    monkeypatch.chdir(tmp_path)
    registry.reset()
    await reset()
    Config._global_config = None
    Config._cached_config = None
    Storage._initialized = False
    TaskStore._handle = None
    TaskStore._initialized = False
    WorkflowStore._handle = None
    WorkflowStore._initialized = False
    Session.invalidate_cache()
    await Storage.init()
    await TaskStore.init()
    await WorkflowStore.init()
    print(f"retained_schema={schema}")
    try:
        yield dsn
    finally:
        await WorkflowStore.close()
        await TaskStore.close()
        await Storage.shutdown()
        await reset()
        await close_pools()
        registry.reset()
        Session.invalidate_cache()


async def due_scheduler(tmp_path, *, mode=SchedulerMode.ONCE):
    due = datetime.now(timezone.utc) - timedelta(seconds=5)
    scheduler = TaskScheduler(
        title="shared schedule", mode=mode,
        trigger=TaskTrigger(run_immediately=False, run_at=due if mode == SchedulerMode.ONCE else None,
                            next_run=due, cron="*/5 * * * *" if mode == SchedulerMode.CRON else None),
        workspace_directory=str(tmp_path),
    )
    await TaskStore.create_scheduler(scheduler)
    return scheduler


async def test_sixteen_schedulers_claim_one_occurrence(shared_pg, tmp_path):
    scheduler = await due_scheduler(tmp_path)
    await asyncio.gather(*(SchedulerLoop()._tick() for _ in range(16)))
    executions, total = await TaskStore.list_scheduler_executions(scheduler.id)
    assert total == 1
    assert executions[0].status == TaskStatus.QUEUED
    db = await TaskStore.raw_db()
    assert await db.fetch_one("SELECT count(*) FROM task_execution_queue_refs WHERE execution_id = ?", (executions[0].id,)) == (1,)
    assert (await TaskStore.get_scheduler(scheduler.id)).status.value == "disabled"


async def test_cron_advance_and_future_disabled_boundary(shared_pg, tmp_path):
    scheduler = await due_scheduler(tmp_path, mode=SchedulerMode.CRON)
    await SchedulerLoop()._tick()
    advanced = await TaskStore.get_scheduler(scheduler.id)
    assert advanced.trigger.next_run > datetime.now(timezone.utc)
    await asyncio.gather(SchedulerLoop()._tick(), SchedulerLoop()._tick())
    assert (await TaskStore.list_scheduler_executions(scheduler.id))[1] == 1
    once = await due_scheduler(tmp_path)
    await TaskManager.disable_scheduler(once.id)
    await SchedulerLoop()._tick()
    assert (await TaskStore.list_scheduler_executions(once.id))[1] == 0


async def test_manual_rerun_remains_explicit(shared_pg, tmp_path):
    scheduler = await due_scheduler(tmp_path)
    a, b = await asyncio.gather(TaskManager.rerun_scheduler(scheduler.id), TaskManager.rerun_scheduler(scheduler.id))
    assert a.id != b.id
    assert (await TaskStore.list_scheduler_executions(scheduler.id))[1] == 2


async def test_scope_claim_is_cross_handle_atomic(shared_pg):
    async def claim(index):
        return await WorkflowStore.start_execution(build_initial_execution_record("wf-scope"), owner_node_id=f"worker-{index}", lease_seconds=60, scope_key="scope")
    results = await asyncio.gather(*(claim(i) for i in range(16)), return_exceptions=True)
    winners = [row for row in results if isinstance(row, dict)]
    assert len(winners) == 1
    assert sum(isinstance(row, WorkflowAlreadyRunning) for row in results) == 15
    await WorkflowStore.release_execution_lease(winners[0]["id"], winners[0])
    assert isinstance(await claim(17), dict)


async def test_expired_generation_cannot_renew_or_finish(shared_pg):
    original = build_initial_execution_record("wf-generation")
    a = await WorkflowStore.start_execution(original, owner_node_id="a", lease_seconds=60)
    await WorkflowStore.release_execution_lease(a["id"], a)
    b = await WorkflowStore.start_execution(original, owner_node_id="b", lease_seconds=60)
    assert b["ownerGeneration"] == a["ownerGeneration"] + 1
    assert not await WorkflowStore.renew_execution_lease(a["id"], a, 60)
    with pytest.raises(WorkflowOwnershipError):
        await WorkflowStore.upsert_execution({**a, "currentPhase": "late progress"})
    with pytest.raises(WorkflowOwnershipError):
        await WorkflowStore.record_step(a["id"], 1, {"node_id": "late"}, token=a)
    with pytest.raises(WorkflowOwnershipError):
        await WorkflowStore.complete_execution({**a, "status": "success"}, [])
    assert await WorkflowStore.complete_execution({**b, "status": "success"}, [])
    assert not await WorkflowStore.complete_execution({**b, "status": "success"}, [])
    assert (await WorkflowStore.get_stats("wf-generation"))["callCount"] == 1


async def test_recovery_preserves_live_peer_and_terminal_result(shared_pg):
    live = await WorkflowStore.start_execution(build_initial_execution_record("wf-recovery"), owner_node_id="peer", lease_seconds=60)
    expired = await WorkflowStore.start_execution(build_initial_execution_record("wf-recovery"), owner_node_id="gone", lease_seconds=60)
    await WorkflowStore.release_execution_lease(expired["id"], expired)
    recovered, completed = await asyncio.gather(
        WorkflowStore.recover_expired_executions("expired owner"),
        WorkflowStore.complete_execution({**live, "status": "success"}, []),
    )
    assert recovered == 1 and completed
    assert (await WorkflowStore.get_execution(live["id"]))["status"] == "success"
    assert (await WorkflowStore.get_execution(expired["id"]))["status"] == "error"
    assert await WorkflowStore.recover_expired_executions("again") == 0


async def test_live_lease_renewal_and_remote_cancel(shared_pg):
    record = await create_execution_record("wf-renew")
    db = await WorkflowStore.raw_db()
    before = await db.fetch_one("SELECT lease_expires_at FROM workflow_execution_leases WHERE execution_id = ?", (record["id"],))
    await asyncio.sleep(1.2)
    after = await db.fetch_one("SELECT lease_expires_at FROM workflow_execution_leases WHERE execution_id = ?", (record["id"],))
    assert after[0] > before[0]
    assert not execution_cancelled(record["id"])
    assert await WorkflowStore.request_execution_cancel(record["id"])
    await asyncio.sleep(1.2)
    assert execution_cancelled(record["id"])
    await record_execution_result("wf-renew", record["id"], {**record, "status": "cancelled"})
    assert (await WorkflowStore.get_execution(record["id"]))["status"] == "cancelled"


async def test_two_poller_managers_execute_one_real_workflow(shared_pg):
    workflow = {"id": "wf-poller", "name": "Probe", "start": "n1", "nodes": [
        {"id": "n1", "type": "python", "code": "import time\ntime.sleep(0.3)\noutputs['ok'] = True"}], "edges": []}
    config = {"noOverlap": True, "timeoutSeconds": 10, "inputs": {}}
    managers = [WorkflowPollerManager(), WorkflowPollerManager()]
    results = await asyncio.gather(*(m._execute_run("wf-poller", workflow, config) for m in managers))
    assert sorted(row["lastStatus"] for row in results) == ["skipped", "success"]
    rows = await WorkflowStore.list_executions("wf-poller")
    assert len(rows) == 1 and rows[0]["status"] == "success"
    assert (await WorkflowStore.get_stats("wf-poller"))["callCount"] == 1


async def test_failed_workflow_releases_scope_and_running_history_is_retained(shared_pg):
    record = await create_execution_record("wf-error", scope_key="error-scope")
    await record_execution_result("wf-error", record["id"], {**record, "status": "error", "errorMessage": "expected test failure"})
    next_record = await create_execution_record("wf-error", scope_key="error-scope")
    await WorkflowStore.trim_executions("wf-error", keep=0)
    assert (await WorkflowStore.get_execution(next_record["id"]))["status"] == "running"
    assert (await WorkflowStore.get_stats("wf-error"))["errorCount"] == 1


async def test_peer_process_session_write_is_immediately_fresh(shared_pg, tmp_path):
    original = SessionInfo(id="peer-session", project_id="project", directory=str(tmp_path), title="before")
    await Storage.set("session:project:peer-session", original, "session")
    assert (await Session.list_all())[0].title == "before"
    peer_script = """
import asyncio
from flocks.session.session import Session, SessionInfo
from flocks.storage.storage import Storage
from flocks.db.connect import close_pools
async def main():
    await Storage.init()
    assert await Session.update('project', 'peer-session', title='after')
    await Storage.set('session:project:peer-created', SessionInfo(id='peer-created', project_id='project', directory='/tmp', title='created'), 'session')
    await Storage.shutdown()
    await close_pools()
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", peer_script, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    output, _ = await asyncio.wait_for(child.communicate(), timeout=20)
    assert child.returncode == 0, output.decode()
    assert (await Session.get_by_id("peer-session")).title == "after"
    assert (await Session.get_by_id("peer-created")).title == "created"
    assert {s.title for s in await Session.list_all()} == {"after", "created"}
    await Session.update("project", "peer-session", status="archived")
    assert (await Session.get_by_id("peer-session")).status == "archived"


async def test_session_key_cache_is_bounded_and_evicted_key_remains_readable(shared_pg, tmp_path):
    original = SessionInfo(id="evicted", project_id="project", directory=str(tmp_path), title="persisted")
    await Storage.set("session:project:evicted", original, "session")
    assert (await Session.get_by_id("evicted")).title == "persisted"
    for index in range(Session._id_index_limit + 1):
        Session._remember_key(f"hint-{index}", f"session:project:hint-{index}")
    assert len(Session._id_index) == Session._id_index_limit
    assert "evicted" not in Session._id_index
    assert (await Session.get_by_id("evicted")).title == "persisted"
    assert len(Session._id_index) == Session._id_index_limit


async def test_two_processes_share_one_no_overlap_scope(shared_pg):
    script = """
import asyncio, json, os
from flocks.storage.storage import Storage
from flocks.workflow.store import WorkflowStore
from flocks.workflow.execution_store import build_initial_execution_record
from flocks.workflow.ownership import WorkflowAlreadyRunning
from flocks.db.connect import close_pools
async def main():
    try:
        record = await WorkflowStore.start_execution(build_initial_execution_record('wf-process'), owner_node_id=str(os.getpid()), lease_seconds=60, scope_key='process-scope')
        result = {'status': 'claimed', 'id': record['id']}
    except WorkflowAlreadyRunning:
        result = {'status': 'skipped'}
    await WorkflowStore.close()
    await Storage.shutdown()
    await close_pools()
    print('RESULT:' + json.dumps(result), flush=True)
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}

    async def peer():
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", script, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await asyncio.wait_for(child.communicate(), timeout=20)
        assert child.returncode == 0, output.decode()
        return json.loads(next(line[7:] for line in output.decode().splitlines() if line.startswith("RESULT:")))

    outcomes = await asyncio.gather(peer(), peer())
    assert sorted(result["status"] for result in outcomes) == ["claimed", "skipped"]
    assert len(await WorkflowStore.list_executions("wf-process")) == 1


async def test_concurrent_scheduler_definition_dedup(shared_pg, tmp_path):
    schedulers = [TaskScheduler(title="same definition", dedup_key="same-key", workspace_directory=str(tmp_path)) for _ in range(16)]
    results = await asyncio.gather(*(TaskStore.create_scheduler(item) for item in schedulers))
    assert sum(item is not None for item in results) == 1
    assert (await TaskStore.list_schedulers())[1] == 1


async def test_database_outage_stops_run_at_local_lease_deadline(shared_pg, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setenv("FLOCKS_TASK_LEASE_SECONDS", "1")
    record = await create_execution_record("wf-outage")
    monkeypatch.setattr(WorkflowStore, "renew_execution_lease", AsyncMock(side_effect=ConnectionError("isolated outage")))
    await asyncio.sleep(1.1)
    assert execution_cancelled(record["id"])
    with pytest.raises(WorkflowOwnershipError):
        await record_execution_result("wf-outage", record["id"], {**record, "status": "success"})
    assert await WorkflowStore.recover_expired_executions("owner unavailable") == 1


async def test_poller_explicit_overlap_allows_parallel_runs(shared_pg):
    workflow = {"id": "wf-overlap", "start": "n", "nodes": [{"id": "n", "type": "python", "code": "outputs['ok'] = True"}], "edges": []}
    config = {"noOverlap": False, "timeoutSeconds": 5, "inputs": {}}
    results = await asyncio.gather(*(WorkflowPollerManager()._execute_run("wf-overlap", workflow, config) for _ in range(2)))
    assert all(result["lastStatus"] == "success" for result in results)
    assert (await WorkflowStore.get_stats("wf-overlap"))["callCount"] == 2


async def test_route_failure_before_dispatch_releases_claim(shared_pg, monkeypatch):
    from fastapi import HTTPException
    from unittest.mock import AsyncMock
    from flocks.server.routes import workflow as route
    from flocks.workflow import ownership

    monkeypatch.setattr(route, "_read_workflow_from_fs", lambda _: {"workflowJson": {}})
    monkeypatch.setattr(route, "_build_workflow_tool_context", AsyncMock(return_value=None))
    monkeypatch.setattr(WorkflowStore, "upsert_execution", AsyncMock(side_effect=RuntimeError("pre-dispatch failure")))

    with pytest.raises(HTTPException, match="pre-dispatch failure"):
        await route.run_workflow_endpoint("wf-pre-dispatch", route.WorkflowRunRequest(session_id="existing-session"))

    records = await WorkflowStore.list_executions("wf-pre-dispatch")
    assert len(records) == 1
    assert records[0]["id"] not in ownership._leases
    assert await WorkflowStore.recover_expired_executions("failed before dispatch") == 1


async def test_route_cancelled_before_runner_starts_releases_claim(shared_pg, monkeypatch):
    from unittest.mock import AsyncMock
    from flocks.server.routes import workflow as route
    from flocks.workflow import ownership

    monkeypatch.setattr(route, "_read_workflow_from_fs", lambda _: {"workflowJson": {}})
    monkeypatch.setattr(route, "_build_workflow_tool_context", AsyncMock(return_value=None))
    result = await route.run_workflow_endpoint("wf-cancel-before-start", route.WorkflowRunRequest())
    execution_id = result.id
    task = route._active_workflow_executions[execution_id].task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    for _ in range(100):
        if execution_id not in ownership._leases:
            break
        await asyncio.sleep(0.01)
    assert execution_id not in ownership._leases
    assert execution_id not in route._active_workflow_executions
    assert await WorkflowStore.recover_expired_executions("cancelled before dispatch") == 1
