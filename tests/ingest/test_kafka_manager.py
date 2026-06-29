"""Unit tests for the Kafka → workflow ingest pipeline.

These tests exercise :class:`KafkaManager` in isolation (no real broker) by
driving the bounded queue and worker pool directly, plus the connection-failure
path of ``restart_workflow``.  They verify the same backpressure invariants as
the syslog manager:

1. A fixed worker pool bounds the number of in-flight workflow dispatches.
2. ``stop_workflow`` cancels and drains the worker pool cleanly.
3. A consumer that cannot connect surfaces ``state == "failed"`` instead of
   pretending to be running.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from flocks.ingest.kafka import manager as kafka_manager
from flocks.workflow import execution_store
from flocks.workflow.triggers.models import TriggerDefinition


@pytest.mark.asyncio
async def test_worker_pool_bounds_in_flight_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixed worker pool must cap concurrent ``_trigger_workflow`` calls."""

    manager = kafka_manager.KafkaManager()
    pool_size = kafka_manager._MAX_CONCURRENT_EXECUTIONS
    trigger = TriggerDefinition.model_validate(
        {"id": "kafka-default", "type": "kafka", "mapping": {"kafka_message": "$.body"}}
    )

    in_flight = 0
    max_in_flight = 0
    completed = 0
    lock = asyncio.Lock()

    async def _fake_trigger(workflow_id, workflow_json, msg, input_key, producer=None, output_topic="", **kwargs):  # noqa: ANN001
        nonlocal in_flight, max_in_flight, completed
        async with lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
            completed += 1

    monkeypatch.setattr(manager, "_trigger_workflow", _fake_trigger)

    workflow_id = "test-wf"
    queue: asyncio.Queue = asyncio.Queue(maxsize=kafka_manager._MAX_QUEUE_SIZE)
    abort = asyncio.Event()

    manager._queues[workflow_id] = queue
    manager._abort_events[workflow_id] = abort
    workers = [
        asyncio.create_task(
                manager._worker_loop(workflow_id, {}, trigger, {}, queue, abort, "topic-a"),
            name=f"test-worker-{i}",
        )
        for i in range(pool_size)
    ]
    manager._worker_pools[workflow_id] = workers

    burst_size = pool_size * 6
    for i in range(burst_size):
        queue.put_nowait({"_seq": i})

    deadline = asyncio.get_event_loop().time() + 5.0
    while completed < burst_size and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)

    abort.set()
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    assert completed == burst_size, f"expected {burst_size} dispatches, got {completed}"
    assert max_in_flight <= pool_size, (
        f"in-flight dispatches exceeded worker pool size: "
        f"max_in_flight={max_in_flight}, pool_size={pool_size}"
    )


@pytest.mark.asyncio
async def test_worker_decodes_queued_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kafka workers should decode raw bytes only when a worker is ready."""

    manager = kafka_manager.KafkaManager()
    workflow_id = "test-wf-raw-queue"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    abort = asyncio.Event()
    captured: list[dict] = []
    trigger = TriggerDefinition.model_validate(
        {"id": "kafka-default", "type": "kafka", "mapping": {"kafka_message": "$.body"}}
    )

    async def _fake_trigger(workflow_id, workflow_json, msg, input_key, producer=None, output_topic="", **kwargs):  # noqa: ANN001
        captured.append(msg)
        abort.set()

    monkeypatch.setattr(manager, "_trigger_workflow", _fake_trigger)
    queue.put_nowait(
        kafka_manager._QueuedKafkaMessage(  # noqa: SLF001
            raw_value=b'{"ok": true}',
            size_bytes=len(b'{"ok": true}'),
        )
    )

    worker = asyncio.create_task(
        manager._worker_loop(workflow_id, {}, trigger, {}, queue, abort, "topic-a"),
        name="test-worker-raw-queue",
    )
    await asyncio.wait_for(worker, timeout=1.0)

    assert captured == [{"ok": True}]


@pytest.mark.asyncio
async def test_stop_workflow_cancels_worker_pool() -> None:
    """``stop_workflow`` must cancel and drain the worker pool cleanly."""

    manager = kafka_manager.KafkaManager()
    workflow_id = "test-wf-stop"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    abort = asyncio.Event()
    trigger = TriggerDefinition.model_validate(
        {"id": "kafka-default", "type": "kafka", "mapping": {"kafka_message": "$.body"}}
    )
    manager._queues[workflow_id] = queue
    manager._abort_events[workflow_id] = abort
    manager._status[workflow_id] = {"state": "running", "error": None}

    async def _noop_trigger(*args, **kwargs):  # noqa: ANN001
        return None

    manager._trigger_workflow = _noop_trigger  # type: ignore[assignment]

    workers = [
        asyncio.create_task(
            manager._worker_loop(workflow_id, {}, trigger, {}, queue, abort, "topic-a"),
            name=f"stop-worker-{i}",
        )
        for i in range(3)
    ]
    manager._worker_pools[workflow_id] = workers

    await asyncio.sleep(0.05)
    await manager.stop_workflow(workflow_id)

    for w in workers:
        assert w.done(), "stop_workflow must terminate every worker in the pool"
    assert workflow_id not in manager._worker_pools
    assert workflow_id not in manager._queues
    assert manager._status[workflow_id]["state"] == "stopped"


@pytest.mark.asyncio
async def test_restart_disabled_config_reports_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled (or missing) config must leave the consumer ``stopped``."""

    manager = kafka_manager.KafkaManager()

    async def _fake_get_config(workflow_id: str, *, kind: str) -> dict:
        assert workflow_id == "wf-disabled"
        assert kind == "workflow_kafka_config"
        return {"enabled": False}

    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", _fake_get_config)

    status = await manager.restart_workflow("wf-disabled")
    assert status == {"state": "stopped", "error": None}


@pytest.mark.asyncio
async def test_restart_missing_broker_reports_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled config without broker/topic must fail fast (no real connect)."""

    manager = kafka_manager.KafkaManager()

    async def _fake_get_config(workflow_id: str, *, kind: str) -> dict:
        assert workflow_id == "wf-no-broker"
        assert kind == "workflow_kafka_config"
        return {"enabled": True, "inputBroker": "", "inputTopic": ""}

    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", _fake_get_config)

    status = await manager.restart_workflow("wf-no-broker")
    assert status["state"] == "failed"
    assert status["error"] == "missing_input_broker_or_topic"


@pytest.mark.asyncio
async def test_restart_workflow_cleans_resources_after_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed consumer start must not leave workers or producers behind."""

    manager = kafka_manager.KafkaManager()
    workflow_id = "wf-connect-failed"

    async def _fake_get_config(workflow_id_value: str, *, kind: str) -> dict:
        assert workflow_id_value == workflow_id
        assert kind == "workflow_kafka_config"
        return {
            "enabled": True,
            "inputBroker": "localhost:9092",
            "inputTopic": "workflow-input",
            "inputGroupId": "wf-group",
            "inputKey": "kafka_message",
        }

    class _Consumer:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.stopped = False

        async def start(self) -> None:
            raise RuntimeError("broker unreachable")

        async def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(
        kafka_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}},
    )
    monkeypatch.setitem(sys.modules, "aiokafka", SimpleNamespace(AIOKafkaConsumer=_Consumer))

    status = await manager.restart_workflow(workflow_id)

    assert status["state"] == "failed"
    assert status["error"] == "broker unreachable"
    assert workflow_id not in manager._tasks
    assert workflow_id not in manager._worker_pools
    assert workflow_id not in manager._queues
    assert workflow_id not in manager._abort_events


def test_decode_message_variants() -> None:
    """``_decode_message`` decodes JSON, falls back to text, then hex."""

    assert kafka_manager._decode_message(b'{"a": 1}') == {"a": 1}
    assert kafka_manager._decode_message(b"plain text") == "plain text"
    assert kafka_manager._decode_message(None) is None
    # Invalid UTF-8 bytes fall back to a hex repr.
    assert kafka_manager._decode_message(b"\xff\xfe") == "fffe"


@pytest.mark.asyncio
async def test_trigger_workflow_compacts_kafka_execution_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kafka-triggered execution rows should not retain full raw alert bodies."""

    manager = kafka_manager.KafkaManager()
    captured_input_params: dict = {}
    captured_exec_data: dict = {}
    captured_run_kwargs: dict = {}
    recorded_steps: list[tuple[str, int, dict]] = []

    async def _fake_create_execution_record(workflow_id, *, input_params=None, exec_id=None):  # noqa: ANN001
        captured_input_params.update(input_params or {})
        return {"id": "exec-compact", "workflowId": workflow_id, "inputParams": input_params}

    async def _fake_record_execution_result(workflow_id, exec_id, exec_data):  # noqa: ANN001
        captured_exec_data.update(exec_data)

    async def _fake_record_execution_step(exec_id, step_index, step):  # noqa: ANN001
        recorded_steps.append((exec_id, step_index, step))
        return step

    async def _fake_run_workflow(**kwargs):  # noqa: ANN003
        captured_run_kwargs.update(kwargs)
        large_alert = {"raw_log_id": "alert-1", "req_body": "x" * 50_000}
        await asyncio.to_thread(
            kwargs["on_step_complete"],
            SimpleNamespace(
                model_dump=lambda mode="json": {
                    "node_id": "receive_alert",
                    "inputs": {"kafka_message": {"alarmData": "x" * 50_000}},
                    "outputs": {"raw_alerts": [large_alert]},
                }
            )
        )
        await asyncio.to_thread(
            kwargs["on_step_complete"],
            SimpleNamespace(
                model_dump=lambda mode="json": {
                    "node_id": "dedup_and_write",
                    "inputs": {"filtered_alerts": [large_alert]},
                    "outputs": {"enriched_alerts": [large_alert]},
                }
            )
        )
        return SimpleNamespace(
            status="SUCCEEDED",
            error=None,
            outputs={
                "enriched_alerts": [large_alert],
                "kafka_messages": [{"raw_log_id": "alert-1"}],
            },
            history=[],
            last_node_id="done",
            steps=2,
        )

    monkeypatch.setattr(kafka_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(kafka_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(kafka_manager, "run_workflow_managed", _fake_run_workflow)
    monkeypatch.setattr(execution_store, "record_execution_step", _fake_record_execution_step)

    await manager._trigger_workflow(
        "wf-compact",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"alarmData": "x" * 50_000},
        "kafka_message",
    )

    assert captured_input_params["kafka_message"]["alarmData"]["_type"] == "string"
    assert captured_input_params["kafka_message"]["alarmData"]["chars"] == 50_000
    assert captured_run_kwargs["exec_id"] == "exec-compact"
    assert captured_run_kwargs["history_mode"] == "summary"
    assert callable(captured_run_kwargs["on_step_complete"])
    assert captured_exec_data["outputResults"] == {
        "_enriched_alerts_count": 1,
        "_kafka_messages_count": 1,
    }
    assert captured_exec_data["executionLog"] == []
    assert captured_exec_data["stepCount"] == 2
    assert recorded_steps[0][0] == "exec-compact"
    assert recorded_steps[0][1] == 1
    assert recorded_steps[0][2]["outputs"] == {"_raw_alerts_count": 1}
    assert recorded_steps[1][1] == 2
    assert recorded_steps[1][2]["inputs"] == {"_filtered_alerts_count": 1}
    assert len(json.dumps(captured_exec_data, ensure_ascii=False)) < 10_000


@pytest.mark.asyncio
async def test_trigger_workflow_merges_configured_inputs_with_consumed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = kafka_manager.KafkaManager()
    captured_run_kwargs: dict = {}
    recorded_input_params: dict = {}

    async def _fake_create_execution_record(workflow_id, *, input_params=None, exec_id=None):  # noqa: ANN001
        recorded_input_params.update(input_params or {})
        return {"id": "exec-merge", "workflowId": workflow_id, "inputParams": input_params}

    async def _fake_record_execution_result(workflow_id, exec_id, exec_data):  # noqa: ANN001
        return None

    async def _fake_run_workflow(**kwargs):  # noqa: ANN003
        captured_run_kwargs.update(kwargs)
        return SimpleNamespace(
            status="SUCCEEDED",
            error=None,
            outputs={"ok": True},
            history=[],
            last_node_id="done",
            steps=1,
        )

    monkeypatch.setattr(kafka_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(kafka_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(kafka_manager, "run_workflow_managed", _fake_run_workflow)

    await manager._trigger_workflow(
        "wf-merge",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"alarmData": {"id": 1}},
        "kafka_message",
        {
            "_comment": "remove me",
            "kafka_message": {"should": "be overridden"},
            "kafka_output_enabled": True,
            "kafka_output_topic": "topic_soc_flocks_result_log",
        },
    )

    assert captured_run_kwargs["inputs"]["kafka_message"] == {"alarmData": {"id": 1}}
    assert captured_run_kwargs["inputs"]["kafka_output_enabled"] is True
    assert captured_run_kwargs["inputs"]["kafka_output_topic"] == "topic_soc_flocks_result_log"
    assert captured_run_kwargs["inputs"]["_trigger"] == "kafka"
    assert captured_run_kwargs["inputs"]["_flocks"]["trigger"]["id"] == "kafka-default"
    assert recorded_input_params["_trigger"] == "kafka"
    assert recorded_input_params["kafka_output_enabled"] is True
    assert recorded_input_params["kafka_output_topic"] == "topic_soc_flocks_result_log"
    assert recorded_input_params["kafka_message"]["_type"] == "dict"
    assert recorded_input_params["kafka_message"]["keys"] == ["alarmData"]


@pytest.mark.asyncio
async def test_trigger_workflow_applies_mapping_and_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = kafka_manager.KafkaManager()
    captured_run_kwargs: dict = {}
    recorded_exec_data: dict = {}

    async def _fake_create_execution_record(workflow_id, *, input_params=None, exec_id=None):  # noqa: ANN001
        return {"id": "exec-filter", "workflowId": workflow_id, "inputParams": input_params}

    async def _fake_record_execution_result(workflow_id, exec_id, exec_data):  # noqa: ANN001
        recorded_exec_data.update(exec_data)

    async def _fake_run_workflow(**kwargs):  # noqa: ANN003
        captured_run_kwargs.update(kwargs)
        return SimpleNamespace(
            status="SUCCEEDED",
            error=None,
            outputs={"ok": True},
            history=[],
            last_node_id="done",
            steps=1,
        )

    monkeypatch.setattr(kafka_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(kafka_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(kafka_manager, "run_workflow_managed", _fake_run_workflow)

    trigger = TriggerDefinition.model_validate(
        {
            "id": "kafka-orders",
            "type": "kafka",
            "mapping": {
                "order_id": "$.body.order.id",
                "region": "$.body.order.region",
            },
            "inputs": {"pipeline": "orders"},
            "filter": {"expr": "body.order.region == 'cn'"},
        }
    )

    await manager._trigger_workflow(
        "wf-orders",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"order": {"id": 7, "region": "cn"}},
        "kafka_message",
        trigger=trigger,
        source="orders-topic",
    )

    assert captured_run_kwargs["inputs"]["order_id"] == 7
    assert captured_run_kwargs["inputs"]["region"] == "cn"
    assert captured_run_kwargs["inputs"]["pipeline"] == "orders"
    assert recorded_exec_data["triggerId"] == "kafka-orders"
    assert recorded_exec_data["triggerSource"] == "orders-topic"

    captured_run_kwargs.clear()
    await manager._trigger_workflow(
        "wf-orders",
        {"start": "receive_alert", "nodes": [], "edges": []},
        {"order": {"id": 8, "region": "us"}},
        "kafka_message",
        trigger=trigger,
        source="orders-topic",
    )
    assert captured_run_kwargs == {}
