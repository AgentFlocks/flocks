"""Runs this process started: cancelling them, letting them go, and recovering the rest."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.workflow import ownership
from flocks.workflow.execution_store import (
    ExecutionProgressWriter,
    create_execution_record,
    record_execution_result,
)
from flocks.workflow.store import WorkflowStore


def _reset_state() -> None:
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    Storage._init_pid = None
    WorkflowStore._initialized = False
    WorkflowStore._conn = None
    WorkflowStore._completion_conn = None
    WorkflowStore._init_pid = None
    WorkflowStore._db_path = None
    WorkflowStore._completion_lock = None


@pytest.fixture(autouse=True)
async def isolated_workflow_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ownership, "_live", {})
    monkeypatch.setattr(ownership, "_abandoned", OrderedDict())
    _reset_state()
    yield
    await WorkflowStore.close()
    _reset_state()


async def _status(exec_id: str) -> str:
    return (await WorkflowStore.get_execution(exec_id))["status"]


async def test_a_running_record_carried_over_from_the_old_store_is_recovered():
    """Migration wrote the record as running; no run of this process stands behind it."""
    await Storage.init()
    record = {"id": "legacy-run", "workflowId": "wf", "status": "running", "startedAt": 1_000}
    with sqlite3.connect(Storage.get_db_path()) as conn:
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, 'json', '', '')",
            ("workflow_execution/legacy-run", json.dumps(record)),
        )

    await WorkflowStore.init()  # migrates the old key-value records
    assert await _status("legacy-run") == "running"

    assert await WorkflowStore.recover_expired_executions("interrupted") == 1
    assert await _status("legacy-run") == "error"


async def test_a_run_is_left_alone_until_its_end_is_recorded():
    exec_data = await create_execution_record("wf")
    exec_id = exec_data["id"]

    assert ownership.running_here(exec_id)
    assert await WorkflowStore.recover_expired_executions("interrupted") == 0
    assert await _status(exec_id) == "running"

    exec_data.update(status="success", finishedAt=int(time.time() * 1000))
    await record_execution_result("wf", exec_id, exec_data, steps=[])

    assert not ownership.running_here(exec_id)
    assert not ownership.execution_cancelled(exec_id)
    assert await WorkflowStore.recover_expired_executions("interrupted") == 0
    assert await _status(exec_id) == "success"


async def test_letting_a_run_go_stops_it_and_records_it_cancelled():
    """A dispatch that fails, or an owner cancelled while the run's thread goes on."""
    exec_id = (await create_execution_record("wf"))["id"]

    await ownership.abandon_execution(exec_id)

    assert ownership.execution_cancelled(exec_id), "its thread stops at the next node"
    assert not ownership.running_here(exec_id)
    record = await WorkflowStore.get_execution(exec_id)
    assert (record["status"], record["currentPhase"]) == ("cancelled", "cancelled")
    assert record["errorMessage"] and record["finishedAt"]
    assert await WorkflowStore.recover_expired_executions("interrupted") == 0


async def test_letting_go_of_a_run_that_already_ended_changes_nothing():
    exec_data = await create_execution_record("wf")
    exec_data.update(status="success", finishedAt=int(time.time() * 1000))
    await record_execution_result("wf", exec_data["id"], exec_data, steps=[])

    await ownership.abandon_execution(exec_data["id"])

    assert await _status(exec_data["id"]) == "success"
    assert not ownership.execution_cancelled(exec_data["id"])


async def test_progress_reported_after_the_run_was_let_go_does_not_reopen_it():
    exec_data = await create_execution_record("wf")
    writer = ExecutionProgressWriter(exec_data)
    await writer.update({"currentNodeId": "first", "currentPhase": "running"})

    await ownership.abandon_execution(exec_data["id"])
    await writer.update({"currentNodeId": "first", "currentStepIndex": 1})  # the thread finished its node

    assert await _status(exec_data["id"]) == "cancelled"


async def test_a_cancel_request_reaches_a_run_of_this_process():
    exec_id = (await create_execution_record("wf"))["id"]

    assert await WorkflowStore.request_execution_cancel(exec_id)
    assert ownership.execution_cancelled(exec_id)
    # Its owner records how it ended; the run is still live until then.
    assert ownership.running_here(exec_id)
    assert not await WorkflowStore.request_execution_cancel("not-a-run-of-this-process")


def _two_node_workflow(started: Path, second: Path) -> dict:
    return {
        "id": "wf-stop",
        "name": "wf-stop",
        "start": "first",
        "nodes": [
            {
                "id": "first",
                "type": "python",
                "code": f"import time\nopen({str(started)!r}, 'w').write('1')\ntime.sleep(0.8)\noutputs['ok'] = True\n",
            },
            {"id": "second", "type": "python", "code": f"open({str(second)!r}, 'w').write('ran')\n"},
        ],
        "edges": [{"from": "first", "to": "second"}],
    }


async def test_a_triggered_run_whose_owner_is_cancelled_stops_at_the_next_node(tmp_path, monkeypatch):
    """A stopped trigger adapter cancels the waiting task; the worker thread must not go on."""
    from flocks.workflow.triggers import runtime as runtime_module
    from flocks.workflow.triggers.models import TriggerDefinition

    async def _no_tool_context(**_kwargs):
        return None

    monkeypatch.setattr(runtime_module, "build_workflow_tool_context", _no_tool_context)
    started, second = tmp_path / "started", tmp_path / "second"
    await WorkflowStore.init()
    task = asyncio.create_task(
        runtime_module.TriggerRuntime()._execute_workflow_effect(
            workflow_id="wf-stop",
            workflow_json=_two_node_workflow(started, second),
            trigger=TriggerDefinition(id="t1", type="webhook"),
            mapped_inputs={},
        )
    )
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.05)
    assert started.exists(), "the first node is running"
    [exec_id] = list(ownership._live)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.5)  # the first node finishes; the runner checks before the second

    assert not second.exists()
    assert await _status(exec_id) == "cancelled"


async def test_a_poller_run_stops_when_its_history_entry_is_cancelled(tmp_path, monkeypatch):
    """The cancel was accepted, but the poller's runner only listened to its own stop signal."""
    from flocks.workflow import poller_manager

    started, second = tmp_path / "started", tmp_path / "second"
    workflow = _two_node_workflow(started, second)

    async def config(_workflow_id, *, kind):
        return {"enabled": False, "timeoutSeconds": 30, "inputs": {}}

    async def _no_tool_context(**_kwargs):
        return None

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", config)
    monkeypatch.setattr(poller_manager, "read_workflow_from_fs", lambda _workflow_id: {"workflowJson": workflow})
    monkeypatch.setattr(poller_manager, "build_workflow_tool_context", _no_tool_context)
    await WorkflowStore.init()
    run = asyncio.create_task(poller_manager.WorkflowPollerManager().run_once("wf-stop"))
    for _ in range(100):
        if started.exists():
            break
        await asyncio.sleep(0.05)
    assert started.exists(), "the first node is running"
    [exec_id] = list(ownership._live)

    assert await WorkflowStore.request_execution_cancel(exec_id)
    await asyncio.wait_for(run, timeout=10)

    assert not second.exists()
    assert await _status(exec_id) == "cancelled"


async def test_a_triggered_run_cancelled_while_its_context_is_cleaned_up_is_let_go(monkeypatch):
    """Cancelled during the cleanup before its end was recorded: it stayed live and running for good."""
    from flocks.workflow.runner import RunWorkflowResult
    from flocks.workflow.triggers import runtime as runtime_module
    from flocks.workflow.triggers.models import TriggerDefinition

    cleaning = asyncio.Event()

    async def _no_tool_context(**_kwargs):
        return None

    async def slow_cleanup(_context):
        cleaning.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime_module, "build_workflow_tool_context", _no_tool_context)
    monkeypatch.setattr(runtime_module, "cleanup_workflow_tool_context", slow_cleanup)
    monkeypatch.setattr(runtime_module, "run_workflow", lambda **_kwargs: RunWorkflowResult(status="success"))
    await WorkflowStore.init()
    task = asyncio.create_task(
        runtime_module.TriggerRuntime()._execute_workflow_effect(
            workflow_id="wf-cleanup",
            workflow_json={"id": "wf-cleanup", "start": "a", "nodes": [], "edges": []},
            trigger=TriggerDefinition(id="t1", type="webhook"),
            mapped_inputs={},
        )
    )
    await asyncio.wait_for(cleaning.wait(), timeout=10)
    [exec_id] = list(ownership._live)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not ownership.running_here(exec_id)
    assert await _status(exec_id) == "cancelled"
    assert await WorkflowStore.recover_expired_executions("interrupted") == 0


async def test_a_run_let_go_stays_cancelled_after_many_more_are_let_go():
    """Forgetting the oldest after 1024 others let a thread still in a long node carry on."""
    for index in range(2_000):
        ownership.start_renewal({"id": f"run-{index}"})
        ownership._let_go(f"run-{index}")

    assert ownership.execution_cancelled("run-0")
