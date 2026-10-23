"""A run whose effects nobody confirmed is not run again by itself (G14).

The case the requirement names: an external action succeeded, its receipt was
lost -- the call started, the run then failed before it heard back.  Running
the task again from the beginning would do the action twice.  Such a run is
held: no automatic retry, the calls to check are named, and a person resumes
it in the session it ran in (its persisted checkpoint) or retries it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.task.executor import TaskExecutor
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionMode, TaskExecution, TaskScheduler, TaskStatus
from flocks.task.outcome import (
    BLOCKED_UNVERIFIED_EFFECTS,
    NEEDS_VERIFICATION,
    SAFE_TO_RETRY,
    outcome_of_calls,
    tool_side_effect,
)
from flocks.task.store import TaskStore


def test_calls_that_change_nothing_leave_a_run_safe_to_retry():
    assert outcome_of_calls([("read", "c1", "completed"), ("grep", "c2", "error"), ("websearch", "c3", "running")]).kind == SAFE_TO_RETRY
    # Asked for but never started: it did nothing.
    assert outcome_of_calls([("bash", "c4", "pending")]).kind == SAFE_TO_RETRY


def test_a_started_call_whose_effect_is_unknown_needs_verification():
    outcome = outcome_of_calls([("read", "c1", "completed"), ("bash", "c2", "running"), ("write", "c3", "completed")])
    assert outcome.kind == NEEDS_VERIFICATION
    assert outcome.effects == ("bash:c2", "write:c3")
    assert tool_side_effect("a_tool_nobody_registered") == "unknown"


def test_a_tool_can_declare_its_effects(monkeypatch):
    from flocks.tool.registry import ToolRegistry

    class _Info:
        side_effect = "idempotent"

    monkeypatch.setattr(ToolRegistry, "get", lambda name: type("T", (), {"info": _Info()})() if name == "send_once" else None)
    assert outcome_of_calls([("send_once", "c1", "completed")]).kind == SAFE_TO_RETRY


def test_a_call_carries_a_stable_idempotency_key():
    from flocks.tool.registry import ToolContext

    first = ToolContext(session_id="ses_1", message_id="m", call_id="call_1")
    again = ToolContext(session_id="ses_1", message_id="m2", call_id="call_1")
    other = ToolContext(session_id="ses_1", message_id="m", call_id="call_2")
    assert first.idempotency_key == again.idempotency_key != other.idempotency_key
    assert ToolContext(session_id="ses_1", message_id="m").idempotency_key is None
    # A run started again is a new session: the same logical call has another key.
    assert ToolContext(session_id="ses_retry", message_id="m", call_id="call_1").idempotency_key != first.idempotency_key


def test_a_call_guarded_by_its_key_holds_the_run_for_a_check(monkeypatch):
    """Its key does not survive a retry, so such a tool declares queryable: no automatic retry."""
    from flocks.tool.registry import ToolRegistry

    class _Info:
        side_effect = "queryable"

    monkeypatch.setattr(ToolRegistry, "get", lambda name: type("T", (), {"info": _Info()})() if name == "charge_card" else None)
    outcome = outcome_of_calls([("charge_card", "c1", "completed")])
    assert outcome.kind == NEEDS_VERIFICATION and outcome.effects == ("charge_card:c1",)


# -- with the task store -------------------------------------------------------


@pytest.fixture()
async def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.config.config import Config
    from flocks.storage.storage import Storage

    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    Config._global_config = None
    Config._cached_config = None
    await TaskStore.close()
    Storage._db_path = None
    Storage._initialized = False
    TaskStore._initialized = False
    await Storage.init()
    await TaskStore.init()
    yield
    await TaskStore.close()
    Storage._db_path = None
    Storage._initialized = False
    TaskStore._initialized = False
    Config._global_config = None
    Config._cached_config = None


async def _agent_run_with(tool_calls: list[tuple[str, str, str]]) -> TaskExecution:
    """A failed agent run whose session made these calls."""
    from flocks.session.message import Message, MessageRole, ToolPart, ToolStateCompleted, ToolStateRunning
    from flocks.session.session import Session

    session = await Session.create(project_id="g14", directory="/tmp", title="g14", category="task")
    message = await Message.create(session.id, MessageRole.ASSISTANT, "")
    for tool, call_id, state in tool_calls:
        if state == "running":
            tool_state = ToolStateRunning(input={"cmd": "deploy"}, time={"start": 1})
        else:
            tool_state = ToolStateCompleted(input={}, output="ok", title=tool, metadata={}, time={"start": 1, "end": 2})
        await Message.store_part(session.id, message.id, ToolPart(
            sessionID=session.id, messageID=message.id, callID=call_id, tool=tool, state=tool_state,
        ))
    scheduler = TaskScheduler(title="g14", executionMode=ExecutionMode.AGENT)
    await TaskStore.create_scheduler(scheduler)
    execution = TaskExecution(
        schedulerID=scheduler.id, title="g14", status=TaskStatus.FAILED, executionMode=ExecutionMode.AGENT,
        sessionID=session.id, startedAt=datetime.now(timezone.utc), error="model connection reset",
    )
    await TaskStore.create_execution(execution)
    return execution


async def test_a_lost_receipt_is_not_followed_by_a_second_run(store, monkeypatch):
    """The command ran; its result never came back; the run failed."""
    execution = await _agent_run_with([("read", "call_r", "completed"), ("bash", "call_deploy", "running")])
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_event", AsyncMock())

    await manager._handle_failure(execution)

    held = await TaskStore.get_execution(execution.id)
    assert held.retry.retry_after is None and held.retry.retry_count == 0
    assert held.retry.blocked_reason == BLOCKED_UNVERIFIED_EFFECTS
    assert held.retry.unverified_effects == ["bash:call_deploy"]
    manager._publish_event.assert_awaited_with("task.needs_verification", execution.id, "failed", "g14")
    # The retry queue does not pick it up, now or later.
    assert execution.id not in {e.id for e in await TaskStore.list_retryable_failed_executions()}
    await manager._process_retry_queue()
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.FAILED


async def test_a_run_that_only_read_is_retried_as_before(store, monkeypatch):
    execution = await _agent_run_with([("read", "call_r", "completed"), ("grep", "call_g", "completed")])
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_event", AsyncMock())

    await manager._handle_failure(execution)

    retried = await TaskStore.get_execution(execution.id)
    assert retried.retry.retry_after is not None and retried.retry.retry_count == 1
    assert retried.retry.blocked_reason is None


async def test_a_held_run_resumes_in_its_own_session_told_what_to_check(store, monkeypatch):
    from flocks.session.message import Message

    execution = await _agent_run_with([("bash", "call_deploy", "running")])
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_event", AsyncMock())
    await manager._handle_failure(execution)

    resumed = await TaskManager.resume_execution(execution.id)
    assert resumed.status == TaskStatus.QUEUED and resumed.session_id == execution.session_id
    assert (await TaskStore.get_execution(execution.id)).session_id == execution.session_id

    # Dispatched: it goes on in that session, not a new one.
    seen = {}

    async def run_session(cls, run, session_id):
        seen["session"] = session_id
        return "checked: the deployment already ran; nothing repeated"

    monkeypatch.setattr(TaskExecutor, "_run_agent_session", classmethod(run_session))
    created = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "_create_task_session", created)
    queued = await TaskStore.get_execution(execution.id)
    queued.status = TaskStatus.RUNNING
    await TaskStore.update_execution(queued)
    scheduler = await TaskStore.get_scheduler(queued.scheduler_id)
    done = await TaskExecutor.dispatch(queued, scheduler)

    assert done.status == TaskStatus.COMPLETED and seen["session"] == execution.session_id
    created.assert_not_awaited()
    last = (await Message.list(execution.session_id))[-1]
    body = " ".join(getattr(p, "text", "") or "" for p in await Message.parts(last.id, execution.session_id))
    assert "bash:call_deploy" in body and "do not repeat" in body

    # Only a held run resumes.
    with pytest.raises(ValueError, match="held for verification"):
        await TaskManager.resume_execution(execution.id)


async def test_an_interrupted_workflow_run_that_got_going_is_held_not_requeued(store, monkeypatch):
    scheduler = TaskScheduler(title="g14 wf", executionMode=ExecutionMode.WORKFLOW, workflowID="isolated")
    await TaskStore.create_scheduler(scheduler)
    execution = TaskExecution(
        schedulerID=scheduler.id, title="g14 wf", status=TaskStatus.RUNNING,
        executionMode=ExecutionMode.WORKFLOW, workflowID="isolated", startedAt=datetime.now(timezone.utc),
    )
    await TaskStore.create_execution(execution)
    await TaskStore.enqueue_execution_ref(execution.id)
    entered = asyncio.Event()

    async def workflow_started_then_hangs(cls, run, sched):
        from flocks.task import executor as module

        with module._workflow_cancel_lock:
            module._workflow_started[run.id] = True  # handed to the worker thread
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", classmethod(workflow_started_then_hangs))
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_execution_update", AsyncMock())
    running = asyncio.create_task(manager._run_execution(execution))
    await asyncio.wait_for(entered.wait(), 3)
    running.cancel()  # a shutdown
    with pytest.raises(asyncio.CancelledError):
        await running

    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.FAILED, "not put back in the queue to start over"
    assert current.retry.blocked_reason == BLOCKED_UNVERIFIED_EFFECTS
    assert current.retry.unverified_effects == ["workflow:isolated"]


async def test_a_person_continues_a_held_run_through_the_api(store, monkeypatch):
    """``POST /api/task-executions/{id}/continue``; the removed pause/resume stays gone."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from flocks.server.routes.task_entities import router

    execution = await _agent_run_with([("bash", "call_api", "running")])
    manager = TaskManager()
    monkeypatch.setattr(manager, "_publish_event", AsyncMock())
    await manager._handle_failure(execution)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        continued = await client.post(f"/api/task-executions/{execution.id}/continue")
        assert continued.status_code == 200, continued.text
        assert continued.json()["status"] == TaskStatus.QUEUED.value
        assert continued.json()["sessionID"] == execution.session_id
        again = await client.post(f"/api/task-executions/{execution.id}/continue")
        assert again.status_code == 409, "only a held run goes on"
        assert (await client.post(f"/api/task-executions/{execution.id}/resume")).status_code == 404
        assert (await client.post("/api/task-executions/exec_missing/continue")).status_code == 404


async def test_a_workflow_run_this_process_never_dispatched_is_judged_by_its_start():
    """Recovered after a restart: nothing recorded here, so a run that started may have run the workflow."""
    from flocks.task.outcome import run_outcome

    execution = TaskExecution(
        schedulerID="sch_g14", title="g14 wf", status=TaskStatus.RUNNING, executionMode=ExecutionMode.WORKFLOW,
        workflowID="isolated", startedAt=datetime.now(timezone.utc),
    )
    assert TaskExecutor.workflow_started(execution) is None
    outcome = await run_outcome(execution)
    assert outcome.kind == NEEDS_VERIFICATION and outcome.effects == ("workflow:isolated",)


async def test_a_dispatched_workflow_run_that_had_not_started_is_safe_to_retry():
    from flocks.task import executor as module
    from flocks.task.outcome import run_outcome

    execution = TaskExecution(
        schedulerID="sch_g14", title="g14 wf", status=TaskStatus.RUNNING, executionMode=ExecutionMode.WORKFLOW,
        workflowID="isolated", startedAt=datetime.now(timezone.utc),
    )
    with module._workflow_cancel_lock:
        module._workflow_started[execution.id] = False  # dispatched here, never got to the workflow
    try:
        assert TaskExecutor.workflow_started(execution) is False
        assert (await run_outcome(execution)).kind == SAFE_TO_RETRY
    finally:
        TaskExecutor.forget_workflow_started(execution.id)
