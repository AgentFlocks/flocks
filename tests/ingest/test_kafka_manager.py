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

import pytest

from flocks.ingest.kafka import manager as kafka_manager


@pytest.mark.asyncio
async def test_worker_pool_bounds_in_flight_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixed worker pool must cap concurrent ``_trigger_workflow`` calls."""

    manager = kafka_manager.KafkaManager()
    pool_size = kafka_manager._MAX_CONCURRENT_EXECUTIONS

    in_flight = 0
    max_in_flight = 0
    completed = 0
    lock = asyncio.Lock()

    async def _fake_trigger(workflow_id, workflow_json, msg, input_key, producer=None, output_topic=""):  # noqa: ANN001
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
            manager._worker_loop(workflow_id, {}, "kafka_message", queue, abort, None, ""),
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
async def test_stop_workflow_cancels_worker_pool() -> None:
    """``stop_workflow`` must cancel and drain the worker pool cleanly."""

    manager = kafka_manager.KafkaManager()
    workflow_id = "test-wf-stop"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    abort = asyncio.Event()
    manager._queues[workflow_id] = queue
    manager._abort_events[workflow_id] = abort
    manager._status[workflow_id] = {"state": "running", "error": None}

    async def _noop_trigger(*args, **kwargs):  # noqa: ANN001
        return None

    manager._trigger_workflow = _noop_trigger  # type: ignore[assignment]

    workers = [
        asyncio.create_task(
            manager._worker_loop(workflow_id, {}, "kafka_message", queue, abort, None, ""),
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

    async def _fake_read(key):  # noqa: ANN001
        return {"enabled": False}

    monkeypatch.setattr(kafka_manager.Storage, "read", _fake_read)

    status = await manager.restart_workflow("wf-disabled")
    assert status == {"state": "stopped", "error": None}


@pytest.mark.asyncio
async def test_restart_missing_broker_reports_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled config without broker/topic must fail fast (no real connect)."""

    manager = kafka_manager.KafkaManager()

    async def _fake_read(key):  # noqa: ANN001
        return {"enabled": True, "inputBroker": "", "inputTopic": ""}

    monkeypatch.setattr(kafka_manager.Storage, "read", _fake_read)

    status = await manager.restart_workflow("wf-no-broker")
    assert status["state"] == "failed"
    assert status["error"] == "missing_input_broker_or_topic"


@pytest.mark.asyncio
async def test_publish_execution_result_allows_output_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kafka output publishing must not require consumer input configuration."""

    manager = kafka_manager.KafkaManager()

    class _Producer:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bytes]] = []
            self.stopped = False

        async def send_and_wait(self, topic: str, value: bytes) -> None:
            self.sent.append((topic, value))

        async def stop(self) -> None:
            self.stopped = True

    producer = _Producer()

    async def _fake_read(key):  # noqa: ANN001
        return {
            "enabled": False,
            "inputBroker": "",
            "inputTopic": "",
            "outputEnabled": True,
            "outputBroker": "localhost:9092",
            "outputTopic": "workflow-output",
        }

    async def _fake_create_producer(broker: str):  # noqa: ANN001
        assert broker == "localhost:9092"
        return producer

    monkeypatch.setattr(kafka_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(manager, "_create_producer", _fake_create_producer)

    published = await manager.publish_execution_result("wf-output", "exec-1", {"ok": True})

    assert published is True
    assert len(producer.sent) == 1
    topic, value = producer.sent[0]
    assert topic == "workflow-output"
    assert json.loads(value.decode("utf-8")) == {
        "workflowId": "wf-output",
        "executionId": "exec-1",
        "outputs": {"ok": True},
    }
    assert producer.stopped is True


@pytest.mark.asyncio
async def test_publish_execution_result_skips_when_output_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured output broker/topic should not publish when the output toggle is off."""

    manager = kafka_manager.KafkaManager()
    create_producer_called = False

    async def _fake_read(key):  # noqa: ANN001
        return {
            "enabled": False,
            "outputEnabled": False,
            "outputBroker": "localhost:9092",
            "outputTopic": "workflow-output",
        }

    async def _fake_create_producer(broker: str):  # noqa: ANN001
        nonlocal create_producer_called
        create_producer_called = True

    monkeypatch.setattr(kafka_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(manager, "_create_producer", _fake_create_producer)

    published = await manager.publish_execution_result("wf-output", "exec-1", {"ok": True})

    assert published is False
    assert create_producer_called is False


def test_decode_message_variants() -> None:
    """``_decode_message`` decodes JSON, falls back to text, then hex."""

    assert kafka_manager._decode_message(b'{"a": 1}') == {"a": 1}
    assert kafka_manager._decode_message(b"plain text") == "plain text"
    assert kafka_manager._decode_message(None) is None
    # Invalid UTF-8 bytes fall back to a hex repr.
    assert kafka_manager._decode_message(b"\xff\xfe") == "fffe"
