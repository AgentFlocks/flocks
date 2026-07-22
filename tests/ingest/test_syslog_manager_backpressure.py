"""Regression tests for the syslog → workflow backpressure pipeline.

These tests exercise ``SyslogManager`` in isolation (no UDP/TCP sockets) by
driving the bounded queue directly.  They verify two invariants that the
previous semaphore-based design did *not* guarantee:

1. Under a sustained burst the number of in-flight workflow dispatches is
   bounded by ``_MAX_CONCURRENT_EXECUTIONS`` — not by the number of messages
   the listener has shoved into the queue.
2. The bounded queue itself rejects excess messages via ``QueueFull`` so the
   listener can drop+log instead of growing the consumer's pending-task set.

These tests deliberately *do not* rely on networking; the listener loop is
covered by a separate route-level test that exercises the bind failure path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.ingest.syslog import manager as syslog_manager
from flocks.workflow import execution_store
from flocks.workflow.triggers.models import TriggerDefinition


@pytest.fixture(autouse=True)
def trigger_tool_context(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = object()
    builder = AsyncMock(return_value=context)
    cleanup = AsyncMock()
    monkeypatch.setattr(syslog_manager, "build_workflow_tool_context", builder)
    monkeypatch.setattr(syslog_manager, "cleanup_workflow_tool_context", cleanup)
    return SimpleNamespace(context=context, builder=builder, cleanup=cleanup)


@pytest.mark.asyncio
async def test_worker_pool_bounds_in_flight_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixed worker pool must cap concurrent ``_trigger_workflow`` calls.

    We replace ``_trigger_workflow`` with an instrumented coroutine that
    increments a counter on entry and asserts it never exceeds the worker
    pool size before exiting.  Then we feed N messages (much larger than the
    pool) into the queue and let the workers drain them.
    """

    manager = syslog_manager.SyslogManager()
    pool_size = syslog_manager._MAX_CONCURRENT_EXECUTIONS
    trigger = TriggerDefinition.model_validate(
        {"id": "syslog-default", "type": "syslog", "mapping": {"syslog_message": "$.body"}}
    )

    in_flight = 0
    max_in_flight = 0
    completed = 0
    lock = asyncio.Lock()

    async def _fake_trigger(workflow_id, workflow_json, msg, input_key, **kwargs):  # noqa: ANN001
        nonlocal in_flight, max_in_flight, completed
        async with lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        # Hold the worker briefly so a true concurrency violation would be
        # observable; we cooperate with the event loop with a small sleep.
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
            completed += 1

    monkeypatch.setattr(manager, "_trigger_workflow", _fake_trigger)

    workflow_id = "test-wf"
    queue: asyncio.Queue = asyncio.Queue(maxsize=syslog_manager._MAX_QUEUE_SIZE)
    abort = asyncio.Event()

    # Wire the manager up the same way ``restart_workflow`` would, minus the
    # listener task itself (which would try to bind a real socket).
    manager._queues[workflow_id] = queue
    manager._abort_events[workflow_id] = abort
    workers = [
        asyncio.create_task(
            manager._worker_loop(workflow_id, {}, trigger, queue, abort),
            name=f"test-worker-{i}",
        )
        for i in range(pool_size)
    ]
    manager._worker_pools[workflow_id] = workers

    # Burst-fill the queue with more work than the pool can do at once.
    burst_size = pool_size * 6
    for i in range(burst_size):
        queue.put_nowait({"_seq": i, "_trigger": "test"})

    # Wait for the workers to drain the queue.
    deadline = asyncio.get_event_loop().time() + 5.0
    while completed < burst_size and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)

    abort.set()
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    assert completed == burst_size, f"expected {burst_size} dispatches, got {completed}"
    assert max_in_flight <= pool_size, (
        f"in-flight dispatches exceeded worker pool size: max_in_flight={max_in_flight}, pool_size={pool_size}"
    )


@pytest.mark.asyncio
async def test_bounded_queue_drops_excess_on_full() -> None:
    """``put_nowait`` must raise ``QueueFull`` once capacity is reached.

    This is the contract the synchronous ``on_msg`` callback relies on; the
    listener catches ``QueueFull`` and emits ``syslog.queue_full_dropped``
    instead of growing the queue unboundedly.
    """

    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    for i in range(4):
        queue.put_nowait({"_seq": i})
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait({"_seq": 99})
    assert queue.qsize() == 4


@pytest.mark.asyncio
async def test_stop_workflow_cancels_worker_pool() -> None:
    """``stop_workflow`` must cancel and drain the worker pool cleanly.

    Leaking worker tasks would re-introduce the symptom the worker-pool
    refactor was designed to prevent (orphan coroutines holding queue
    references after the listener has stopped).
    """

    manager = syslog_manager.SyslogManager()
    workflow_id = "test-wf-stop"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    abort = asyncio.Event()
    trigger = TriggerDefinition.model_validate(
        {"id": "syslog-default", "type": "syslog", "mapping": {"syslog_message": "$.body"}}
    )
    manager._queues[workflow_id] = queue
    manager._abort_events[workflow_id] = abort
    manager._listener_status[workflow_id] = {"state": "listening", "error": None}

    async def _noop_trigger(*args, **kwargs):  # noqa: ANN001, D401
        return None

    manager._trigger_workflow = _noop_trigger  # type: ignore[assignment]

    workers = [
        asyncio.create_task(
            manager._worker_loop(workflow_id, {}, trigger, queue, abort),
            name=f"stop-worker-{i}",
        )
        for i in range(3)
    ]
    manager._worker_pools[workflow_id] = workers

    # Let workers loop once.
    await asyncio.sleep(0.05)

    await manager.stop_workflow(workflow_id)

    for w in workers:
        assert w.done(), "stop_workflow must terminate every worker in the pool"
    assert workflow_id not in manager._worker_pools
    assert workflow_id not in manager._queues
    assert manager._listener_status[workflow_id]["state"] == "stopped"


@pytest.mark.asyncio
async def test_trigger_workflow_applies_mapping_and_filter(
    monkeypatch: pytest.MonkeyPatch,
    trigger_tool_context: SimpleNamespace,
) -> None:
    manager = syslog_manager.SyslogManager()
    captured_run_kwargs: dict = {}
    recorded_exec_data: dict = {}
    recorded_steps: list[tuple[str, int, dict]] = []

    async def _fake_create_execution_record(workflow_id, *, input_params=None, exec_id=None):  # noqa: ANN001
        return {"id": "exec-syslog", "workflowId": workflow_id, "inputParams": input_params}

    async def _fake_record_execution_result(workflow_id, exec_id, exec_data):  # noqa: ANN001
        recorded_exec_data.update(exec_data)

    async def _fake_record_execution_step(exec_id, step_index, step):  # noqa: ANN001
        recorded_steps.append((exec_id, step_index, step))
        return step

    def _fake_run_workflow(**kwargs):  # noqa: ANN003
        captured_run_kwargs.update(kwargs)
        kwargs["on_step_complete"](
            SimpleNamespace(
                model_dump=lambda mode="json": {
                    "node_id": "receive_alert",
                    "node_type": "python",
                    "inputs": {"message": "demo"},
                    "outputs": {"ok": True},
                }
            )
        )
        return type(
            "RunResult",
            (),
            {
                "status": "SUCCEEDED",
                "error": None,
                "outputs": {"ok": True},
                "history": [],
                "last_node_id": "done",
                "steps": 1,
            },
        )()

    monkeypatch.setattr(syslog_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(syslog_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(syslog_manager, "run_workflow", _fake_run_workflow)
    monkeypatch.setattr(execution_store, "record_execution_step", _fake_record_execution_step)

    trigger = TriggerDefinition.model_validate(
        {
            "id": "syslog-alerts",
            "type": "syslog",
            "mapping": {
                "message": "$.body.message",
                "hostname": "$.body.hostname",
            },
            "inputs": {"pipeline": "syslog"},
            "filter": {"expr": "body.hostname == 'router-a'"},
        }
    )

    await manager._trigger_workflow(
        "wf-syslog",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"message": "demo", "hostname": "router-a"},
        "syslog_message",
        trigger=trigger,
        source="udp://0.0.0.0:5514",
    )

    assert captured_run_kwargs["inputs"]["message"] == "demo"
    assert captured_run_kwargs["inputs"]["hostname"] == "router-a"
    assert captured_run_kwargs["inputs"]["pipeline"] == "syslog"
    assert captured_run_kwargs["run_id"] == "exec-syslog"
    assert captured_run_kwargs["execution_profile"] == "high_frequency"
    assert captured_run_kwargs["tool_context"] is trigger_tool_context.context
    assert callable(captured_run_kwargs["on_step_complete"])
    trigger_tool_context.builder.assert_awaited_once_with(
        workflow_id="wf-syslog",
        action_name="trigger:syslog",
    )
    trigger_tool_context.cleanup.assert_awaited_once_with(trigger_tool_context.context)
    assert recorded_steps[0][0] == "exec-syslog"
    assert recorded_steps[0][1] == 1
    assert recorded_steps[0][2]["node_id"] == "receive_alert"
    assert recorded_exec_data["triggerId"] == "syslog-alerts"
    assert recorded_exec_data["triggerSource"] == "udp://0.0.0.0:5514"
    assert recorded_exec_data["executionLog"] == []
    assert recorded_exec_data["stepCount"] == 1

    captured_run_kwargs.clear()
    await manager._trigger_workflow(
        "wf-syslog",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"message": "demo", "hostname": "router-b"},
        "syslog_message",
        trigger=trigger,
        source="udp://0.0.0.0:5514",
    )
    assert captured_run_kwargs == {}
