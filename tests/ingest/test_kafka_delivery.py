"""Kafka delivery guarantees with an in-memory broker, real queues and workers."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from typing import NamedTuple

import pytest

from flocks.ingest.kafka import manager as kafka_manager


class Partition(NamedTuple):
    topic: str
    partition: int


def record(partition: int, offset: int, **body):  # noqa: ANN003
    return SimpleNamespace(
        topic="alerts",
        partition=partition,
        offset=offset,
        value=json.dumps({"partition": partition, "offset": offset, **body}).encode(),
    )


async def eventually(predicate, timeout=3):  # noqa: ANN001
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class Broker:
    """Model group positions and records arriving after initial assignment.

    A fresh partition's initial highwatermark is its first future delivery.
    Fetching a record advances that watermark. Without an initial committed
    anchor, reset=latest on restart therefore really skips fetched records.
    """

    def __init__(self, records):  # noqa: ANN001
        self.records = records
        self.committed = {}
        self.initial_positions = {}
        self.commit_attempts = []
        self.fetched = []
        self.consumers = []
        self.commit_hook = None
        self.fetched_hook = None
        self.beginning_offsets = {}
        for item in records:
            partition = Partition(item.topic, item.partition)
            self.beginning_offsets[partition] = min(
                item.offset, self.beginning_offsets.get(partition, item.offset),
            )
        self.highwatermarks = dict(self.beginning_offsets)
        broker = self

        class Consumer:
            def __init__(self, **config):  # noqa: ANN003
                self.config = config
                self.closed = False
                self.listener = None
                self.pending = deque()
                self.positions = {}
                broker.consumers.append(self)

            def subscribe(self, *, topics, listener):  # noqa: ANN001
                assert topics == ["alerts"]
                self.listener = listener

            async def start(self):  # noqa: ANN202
                for partition in broker.beginning_offsets:
                    self.reset_position(partition)
                self.pending.extend(
                    item for item in broker.records
                    if item.offset >= self.positions[Partition(item.topic, item.partition)]
                )
                await self.listener.on_partitions_assigned(
                    {Partition(item.topic, item.partition) for item in broker.records}
                )

            async def getone(self):  # noqa: ANN202
                if self.pending:
                    item = self.pending.popleft()
                    broker.fetched.append((item.partition, item.offset))
                    partition = Partition(item.topic, item.partition)
                    self.positions[partition] = item.offset + 1
                    broker.highwatermarks[partition] = max(broker.highwatermarks[partition], item.offset + 1)
                    if broker.fetched_hook:
                        broker.fetched_hook(self, item)
                    return item
                await asyncio.Event().wait()

            async def commit(self, offsets):  # noqa: ANN001, ANN202
                assert offsets, "implicit fetch-position commits must never be used"
                broker.commit_attempts.append(dict(offsets))
                if broker.commit_hook:
                    await broker.commit_hook(offsets)
                broker.committed.update(offsets)

            async def committed(self, partition):  # noqa: ANN001, ANN202
                return broker.committed.get(partition, broker.initial_positions.get(partition, 0))

            def reset_position(self, partition):  # noqa: ANN001, ANN202
                committed = broker.committed.get(partition, broker.initial_positions.get(partition, 0))
                if committed is None or committed < broker.beginning_offsets[partition]:
                    self.positions[partition] = (
                        broker.highwatermarks[partition] if self.config["auto_offset_reset"] == "latest"
                        else broker.beginning_offsets[partition]
                    )
                else:
                    self.positions[partition] = committed

            async def position(self, partition):  # noqa: ANN001, ANN202
                return self.positions[partition]

            async def stop(self):  # noqa: ANN202
                self.closed = True

        self.Consumer = Consumer


@pytest.fixture
async def pipeline(monkeypatch):  # noqa: ANN001
    manager = kafka_manager.KafkaManager()
    monkeypatch.setattr(kafka_manager, "_DELIVERY_RETRY_DELAY_S", 0.001)
    monkeypatch.setattr(kafka_manager, "_REBALANCE_RECOVERY_DELAY_S", 0.01)

    async def start(  # noqa: ANN202
        broker, *, workers=3, filter_spec=None, reset_policy="earliest", expected_state="running",
    ):  # noqa: ANN001
        monkeypatch.setitem(
            sys.modules,
            "aiokafka",
            SimpleNamespace(
                AIOKafkaConsumer=broker.Consumer,
                ConsumerRebalanceListener=object,
                TopicPartition=Partition,
            ),
        )
        config = {
            "enabled": True,
            "inputBroker": "memory:9092",
            "inputTopic": "alerts",
            "inputGroupId": "delivery-test",
            "autoOffsetReset": reset_policy,
        }

        async def get_config(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            return config

        workflow = {
            "name": "delivery-test",
            "start": "accept",
            "nodes": [{"id": "accept", "type": "python", "code": "outputs['ok'] = True"}],
            "edges": [],
            "triggers": [{
                "id": "input",
                "type": "kafka",
                "enabled": True,
                "mapping": {"kafka_message": "$.body"},
                "inputs": {"tool_context_required": False},
                "concurrency": {"maxParallel": workers, "queueSize": 8},
                **({"filter": filter_spec} if filter_spec else {}),
            }],
        }
        monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", get_config)
        monkeypatch.setattr(kafka_manager, "read_workflow_from_fs", lambda _: {"workflowJson": workflow})
        status = await manager.restart_workflow("delivery-test")
        assert status["state"] == expected_state
        assert broker.consumers[-1].config["enable_auto_commit"] is False

    yield manager, start
    await manager.stop_all()


def acknowledge():  # noqa: ANN201
    return kafka_manager._KafkaDispatchOutcome(acknowledged=True, exec_id="durable-success")


@pytest.mark.asyncio
async def test_completed_later_offsets_wait_for_partition_prefix_and_other_partition_progresses(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 5), record(0, 8), record(0, 10), record(1, 3)])
    broker.committed.update({Partition("alerts", 0): 5, Partition("alerts", 1): 3})
    release_first = asyncio.Event()
    later_finished = set()

    async def dispatch(wid, plan, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if message["partition"] == 0 and message["offset"] == 5:
            await release_first.wait()
        else:
            later_finished.add((message["partition"], message["offset"]))
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=4)
    await eventually(lambda: len(later_finished) == 3 and broker.committed[Partition("alerts", 1)] == 4)
    assert broker.committed[Partition("alerts", 0)] == 5
    assert not any(Partition("alerts", 0) in attempt for attempt in broker.commit_attempts)
    release_first.set()
    await eventually(lambda: broker.committed[Partition("alerts", 0)] == 11)
    assert [a[Partition("alerts", 0)] for a in broker.commit_attempts if Partition("alerts", 0) in a] == [11]


@pytest.mark.asyncio
async def test_failed_dispatch_is_bounded_and_never_skipped_by_later_completion_or_restart(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0), record(0, 1), record(1, 0)])
    calls = []
    failing = True

    async def dispatch(wid, plan, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        calls.append((message["partition"], message["offset"], kwargs["delivery_id"]))
        if failing and message["partition"] == 0 and message["offset"] == 0:
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = 5
            error.sqlite_errorname = "SQLITE_BUSY"
            raise error
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker)
    await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
    await eventually(lambda: broker.consumers[0].closed)
    assert Partition("alerts", 0) not in broker.committed
    assert broker.committed[Partition("alerts", 1)] == 1
    failed_calls = [call for call in calls if call[:2] == (0, 0)]
    assert len(failed_calls) == kafka_manager._DELIVERY_MAX_ATTEMPTS
    assert {call[2] for call in failed_calls} == {"kafka:alerts:0:0"}
    status = manager.get_consumer_status("delivery-test")
    assert status["sqlite_errorcode"] == 5
    assert status["sqlite_errorname"] == "SQLITE_BUSY"
    assert status["offset"] == 0

    # Offset 1 already completed in memory, but cannot be committed past 0.
    # Both are replayed after restart, using the same delivery id for idempotency.
    replay_start = len(calls)
    failing = False
    await start(broker)
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 2)
    assert {call[:2] for call in calls[replay_start:]} == {(0, 0), (0, 1)}
    assert [call[2] for call in calls[replay_start:] if call[:2] == (0, 0)] == [failed_calls[0][2]]


@pytest.mark.asyncio
async def test_slow_partition_head_bounds_completed_but_uncommitted_backlog(pipeline, monkeypatch):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, offset) for offset in range(100)])
    release_first = asyncio.Event()
    processed = []

    async def dispatch(wid, plan, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        processed.append(message["offset"])
        if message["offset"] == 0:
            await release_first.wait()
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=4)
    # Queue capacity 8 + four active workers. One extra fetched raw message
    # may wait for an acknowledgement slot, but cannot reach a workflow yet.
    await eventually(lambda: len(processed) == 12 and len(broker.fetched) == 13)
    assert broker.commit_attempts == []
    assert manager.get_consumer_status("delivery-test")["state"] == "running"
    release_first.set()
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 100)
    assert len(processed) == 100
    assert set(processed) == set(range(100))


@pytest.mark.parametrize("previous_offset", [None, 0])
@pytest.mark.asyncio
async def test_latest_reset_pins_first_offset_before_failed_delivery(
    pipeline, monkeypatch, previous_offset,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 42)])
    partition = Partition("alerts", 0)
    broker.initial_positions[partition] = previous_offset
    seen = []
    failing = True

    async def dispatch(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        # The starting position must already survive restart before any effect.
        assert broker.committed[partition] == 42
        seen.append(kwargs["delivery_id"])
        if failing:
            raise RuntimeError("workflow unavailable")
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, reset_policy="latest")
    await eventually(lambda: broker.consumers[0].closed)
    assert manager.get_consumer_status("delivery-test")["state"] == "failed"
    assert broker.commit_attempts == [{partition: 42}]
    assert broker.committed[partition] == 42  # 42 itself remains unconsumed.
    failing = False
    await start(broker, reset_policy="latest")
    await eventually(lambda: broker.committed[partition] == 43)
    assert set(seen) == {"kafka:alerts:0:42"}


@pytest.mark.asyncio
async def test_fresh_group_anchor_failure_stops_before_workflow_effects(pipeline, monkeypatch):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 42)])
    broker.initial_positions[Partition("alerts", 0)] = None
    executions, _ = mock_execution(monkeypatch)

    async def fail_commit(offsets):  # noqa: ANN001, ANN202
        raise RuntimeError("cannot persist initial Kafka position")

    broker.commit_hook = fail_commit
    await start(broker, reset_policy="latest", expected_state="failed")
    await eventually(lambda: broker.consumers[0].closed)
    assert len(broker.commit_attempts) == kafka_manager._DELIVERY_MAX_ATTEMPTS
    assert broker.committed == {}
    assert executions == []
    assert manager.get_consumer_status("delivery-test")["state"] == "failed"


@pytest.mark.asyncio
async def test_new_partition_is_anchored_before_fetch_even_when_other_partition_fills_all_slots(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([*[record(0, offset) for offset in range(12)], record(1, 42)])
    partition = Partition("alerts", 1)
    broker.initial_positions[partition] = None
    release_first = asyncio.Event()
    calls = []
    first_cancel = None
    restarting = False

    async def dispatch(wid, plan, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        nonlocal first_cancel
        calls.append((message["partition"], message["offset"]))
        if message["partition"] == 0 and message["offset"] == 0 and not restarting:
            first_cancel = kwargs["generation_cancel_event"]
            await release_first.wait()
        return acknowledge()

    def fetched(consumer, item):  # noqa: ANN001, ANN202
        if item.partition == 1:
            assert broker.committed[partition] == 42  # Must precede getone, not just dispatch.

    broker.fetched_hook = fetched
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=4, reset_policy="latest")
    await eventually(lambda: len(broker.fetched) == 13 and len(calls) == 12)
    assert broker.highwatermarks[partition] == 43
    stopping = asyncio.create_task(manager.stop_workflow("delivery-test"))
    await eventually(lambda: first_cancel is not None and first_cancel.is_set())
    release_first.set()
    await stopping
    assert (1, 42) not in calls
    assert broker.committed[partition] == 42
    restarting = True
    await start(broker, workers=4, reset_policy="latest")
    await eventually(lambda: broker.committed[partition] == 43)
    assert (1, 42) in calls


def successful_result(**changes):  # noqa: ANN003, ANN201
    values = {
        "status": "SUCCEEDED", "error": None, "outputs": {},
        "history": [], "last_node_id": "accept", "steps": 1,
    }
    return SimpleNamespace(**{**values, **changes})


def mock_execution(monkeypatch, *, result=None, persist=None):  # noqa: ANN001, ANN201
    executions = []
    persisted = []

    async def create(wid, **kwargs):  # noqa: ANN001, ANN003, ANN202
        return {"id": f"execution-{len(executions)}", "inputParams": kwargs["input_params"]}

    async def save(wid, eid, data, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if persist:
            await persist(data)
        persisted.append(dict(data))

    def run(**kwargs):  # noqa: ANN003, ANN202
        executions.append(kwargs)
        return result or successful_result()

    monkeypatch.setattr(kafka_manager, "create_execution_record", create)
    monkeypatch.setattr(kafka_manager, "record_execution_result", save)
    monkeypatch.setattr(kafka_manager, "run_workflow", run)
    return executions, persisted


@pytest.mark.asyncio
async def test_offset_waits_for_successful_terminal_record_persistence(pipeline, monkeypatch):  # noqa: ANN001
    _, start = pipeline
    broker = Broker([record(0, 0)])
    saving = asyncio.Event()
    release = asyncio.Event()

    async def persist(data):  # noqa: ANN001, ANN202
        saving.set()
        await release.wait()

    executions, persisted = mock_execution(monkeypatch, persist=persist)
    await start(broker)
    await saving.wait()
    assert len(executions) == 1
    assert broker.commit_attempts == []
    release.set()
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
    assert persisted[0]["status"] == "success"
    assert executions[0]["inputs"]["_flocks"]["trigger"]["deliveryId"] == "kafka:alerts:0:0"


@pytest.mark.asyncio
async def test_terminal_record_failure_cannot_ack_a_successful_business_run(pipeline, monkeypatch):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])

    async def persist(data):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("database is locked")

    executions, persisted = mock_execution(monkeypatch, persist=persist)
    await start(broker)
    await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
    assert len(executions) == kafka_manager._DELIVERY_MAX_ATTEMPTS
    assert persisted == []
    assert broker.commit_attempts == []
    assert manager.get_consumer_status("delivery-test")["error_code"] == "execution_record_failed"


@pytest.mark.asyncio
async def test_restart_during_failed_generation_cleanup_finishes_old_consumer_first(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    closing = asyncio.Event()
    release = asyncio.Event()
    cancelled = []
    failing = True

    async def slow_stop(consumer):
        closing.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(consumer)
            raise
        consumer.closed = True

    async def dispatch(*args, **kwargs):
        if failing:
            raise RuntimeError("synthetic business failure")
        return acknowledge()

    monkeypatch.setattr(broker.Consumer, "stop", slow_stop)
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker)
    await asyncio.wait_for(closing.wait(), 3)
    failing = False
    restarting = asyncio.create_task(start(broker))
    try:
        await asyncio.sleep(0.03)
        assert not cancelled, "restart cancelled the old client's in-progress stop"
        assert not restarting.done()
        assert len(broker.consumers) == 1
        release.set()
        await restarting
        assert broker.consumers[0].closed
        await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
    finally:
        release.set()
        await asyncio.gather(restarting, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_client_close_bounds_restart_and_retains_the_old_supervisor(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    closing = asyncio.Event()
    release = asyncio.Event()

    async def slow_stop(consumer):
        closing.set()
        await release.wait()
        consumer.closed = True

    async def dispatch(*args, **kwargs):
        return acknowledge()

    monkeypatch.setattr(broker.Consumer, "stop", slow_stop)
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    monkeypatch.setattr(kafka_manager, "_CONSUMER_STOP_WAIT_TIMEOUT_S", 0.02)
    await start(broker)
    old = manager._tasks["delivery-test"]
    try:
        await asyncio.wait_for(manager.stop_workflow("delivery-test"), 1)
        await asyncio.wait_for(closing.wait(), 1)
        for _ in range(2):
            status = await asyncio.wait_for(manager.restart_workflow("delivery-test"), 1)
            assert status["state"] == "failed"
            assert status["error_code"] == "consumer_stop_timeout"
            assert manager._tasks["delivery-test"] is old
            assert not old.done()
            assert len(broker.consumers) == 1
        release.set()
        await manager.stop_workflow("delivery-test")
        assert broker.consumers[0].closed
        assert "delivery-test" not in manager._tasks
        await start(broker)
        assert len(broker.consumers) == 2
    finally:
        release.set()


@pytest.mark.asyncio
async def test_transient_initial_database_lock_recovers_without_losing_or_reexecuting_business_work(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, persisted = mock_execution(monkeypatch)
    attempts = 0

    async def create(wid, **kwargs):  # noqa: ANN001, ANN003, ANN202
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return {"id": "recovered-execution"}

    monkeypatch.setattr(kafka_manager, "create_execution_record", create)
    await start(broker)
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
    assert attempts == 3
    assert len(executions) == 1
    assert persisted[0]["id"] == "recovered-execution"
    assert manager.get_consumer_status("delivery-test")["state"] == "running"


@pytest.mark.parametrize("result", [
    successful_result(status="FAILED", error="syntax error"),
    successful_result(outputs={"workflow_success": False, "error": "output write failed"}),
    successful_result(status="CANCELLED"),
    successful_result(status="TIMEOUT"),
])
@pytest.mark.asyncio
async def test_persisted_business_error_or_cancel_is_not_a_successful_ack(
    pipeline, monkeypatch, result,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, persisted = mock_execution(monkeypatch, result=result)
    await start(broker)
    await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
    assert len(executions) == kafka_manager._DELIVERY_MAX_ATTEMPTS
    assert len(persisted) == len(executions)
    assert all(item["status"] != "success" for item in persisted)
    assert broker.commit_attempts == []


@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.asyncio
async def test_commit_retries_do_not_execute_successful_workflow_again(
    pipeline, monkeypatch, permanent,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, _ = mock_execution(monkeypatch)

    async def commit(offsets):  # noqa: ANN001, ANN202
        if permanent or len(broker.commit_attempts) == 1:
            raise RuntimeError("broker unavailable during offset commit")

    broker.commit_hook = commit
    await start(broker)
    if permanent:
        await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
        assert len(broker.commit_attempts) == kafka_manager._DELIVERY_MAX_ATTEMPTS
        assert broker.committed == {}
    else:
        await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
        assert len(broker.commit_attempts) == 2
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_client_internal_commit_retry_cannot_hang_consumer_indefinitely(pipeline, monkeypatch):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, _ = mock_execution(monkeypatch)
    monkeypatch.setattr(kafka_manager, "_OFFSET_OPERATION_TIMEOUT_S", 0.01)

    async def hung_commit(offsets):  # noqa: ANN001, ANN202
        await asyncio.Event().wait()

    broker.commit_hook = hung_commit
    await start(broker)
    await eventually(lambda: broker.consumers[0].closed)
    assert broker.committed == {}
    assert len(broker.commit_attempts) == kafka_manager._DELIVERY_MAX_ATTEMPTS
    assert len(executions) == 1
    assert manager.get_consumer_status("delivery-test")["error_code"] == "commit_timeout"


@pytest.mark.asyncio
async def test_valid_unmatched_filter_commits_without_executing_workflow(pipeline, monkeypatch):  # noqa: ANN001
    _, start = pipeline
    broker = Broker([record(0, 0, process=False)])
    executions, persisted = mock_execution(monkeypatch)
    await start(broker, filter_spec={"expr": "body.process == True"})
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
    assert executions == []
    assert persisted == []


@pytest.mark.asyncio
async def test_invalid_filter_stops_delivery_without_commit(pipeline, monkeypatch):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, persisted = mock_execution(monkeypatch)
    await start(broker, filter_spec={"expr": "missing_name == True"})
    await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
    assert broker.commit_attempts == []
    assert executions == []
    assert persisted == []


@pytest.mark.asyncio
async def test_stop_cancels_workflow_and_never_commits_prefetched_or_cancelled_records(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0), record(0, 1)])
    started = threading.Event()
    persisted = []

    async def create(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return {"id": "cancelled-execution"}

    async def save(wid, eid, data, **kwargs):  # noqa: ANN001, ANN003, ANN202
        persisted.append(dict(data))

    def run(**kwargs):  # noqa: ANN003, ANN202
        started.set()
        while not kwargs["cancel"]():
            time.sleep(0.001)
        return successful_result(status="CANCELLED")

    monkeypatch.setattr(kafka_manager, "create_execution_record", create)
    monkeypatch.setattr(kafka_manager, "record_execution_result", save)
    monkeypatch.setattr(kafka_manager, "run_workflow", run)
    await start(broker, workers=1)
    assert await asyncio.to_thread(started.wait, 1)
    await manager.stop_workflow("delivery-test")
    assert broker.commit_attempts == []
    assert persisted[0]["status"] == "cancelled"
    assert broker.consumers[0].closed


@pytest.mark.asyncio
async def test_unexpected_worker_cancellation_fails_generation_instead_of_hanging_slots(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0), record(0, 1)])

    async def dispatch(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise asyncio.CancelledError()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=1)
    await eventually(lambda: broker.consumers[0].closed)
    assert broker.commit_attempts == []
    status = manager.get_consumer_status("delivery-test")
    assert status["state"] == "failed"
    assert status["error_code"] == "worker_cancelled"


@pytest.mark.asyncio
async def test_rebalance_invalidates_queued_delivery_before_late_worker_completion(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        started.set()
        await release.wait()
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker)
    await started.wait()
    consumer = broker.consumers[0]
    await consumer.listener.on_partitions_revoked({Partition("alerts", 0)})
    await consumer.listener.on_partitions_assigned({Partition("alerts", 0)})
    release.set()
    await eventually(lambda: consumer.closed)
    assert broker.commit_attempts == []
    status = manager.get_consumer_status("delivery-test")
    assert status["state"] == "connecting"
    assert status["error_code"] == "partition_revoked_with_pending_deliveries"
    await eventually(lambda: len(broker.consumers) == 2 and broker.committed.get(Partition("alerts", 0)) == 1)
    assert manager.get_consumer_status("delivery-test")["state"] == "running"
    assert broker.consumers[1].config["group_id"] == "delivery-test"


@pytest.mark.asyncio
async def test_rebalance_between_fetch_and_queue_cannot_adopt_old_record_into_new_assignment(
    pipeline, monkeypatch,
):  # noqa: ANN001
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    executions, _ = mock_execution(monkeypatch)

    def after_fetch(consumer, item):  # noqa: ANN001, ANN202
        broker.fetched_hook = None
        async def rebalance():  # noqa: ANN202
            partition = Partition(item.topic, item.partition)
            await consumer.listener.on_partitions_revoked({partition})
            consumer.reset_position(partition)
            await consumer.listener.on_partitions_assigned({partition})

        asyncio.create_task(rebalance())

    broker.fetched_hook = after_fetch
    await start(broker)
    await eventually(lambda: broker.consumers[0].closed)
    assert broker.commit_attempts == []
    assert executions == []
    assert manager.get_consumer_status("delivery-test")["error_code"] == "partition_changed_before_dispatch"
    await eventually(lambda: len(broker.consumers) == 2 and broker.committed.get(Partition("alerts", 0)) == 1)
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_rebalance_waits_for_old_workers_before_replay(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    monkeypatch.setattr(kafka_manager, "_WORKER_DRAIN_TIMEOUT_S", 0.01)
    release, started = asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(*args, **kwargs):
        calls.append(kwargs["delivery_id"])
        started.set()
        if len(calls) == 1:
            await release.wait()
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=1)
    await started.wait()
    await broker.consumers[0].listener.on_partitions_revoked({Partition("alerts", 0)})
    try:
        await eventually(lambda: manager.get_consumer_status("delivery-test").get("recoveryPhase") == "waiting_for_workers")
        assert len(broker.consumers) == 1
        assert broker.committed == {}
    finally:
        release.set()
    await eventually(lambda: broker.committed.get(Partition("alerts", 0)) == 1)
    assert len(broker.consumers) == 2
    assert calls == ["kafka:alerts:0:0", "kafka:alerts:0:0"]


@pytest.mark.asyncio
async def test_stop_during_rebalance_backoff_never_revives_consumer(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    monkeypatch.setattr(kafka_manager, "_REBALANCE_RECOVERY_DELAY_S", 60)
    release, started = asyncio.Event(), asyncio.Event()

    async def dispatch(*args, **kwargs):
        started.set()
        await release.wait()
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=1)
    await started.wait()
    await broker.consumers[0].listener.on_partitions_revoked({Partition("alerts", 0)})
    release.set()
    await eventually(lambda: manager.get_consumer_status("delivery-test").get("recoveryPhase") == "backoff")
    await manager.stop_workflow("delivery-test")
    await asyncio.sleep(0.02)
    assert len(broker.consumers) == 1
    assert manager.get_consumer_status("delivery-test")["state"] == "stopped"
    assert "delivery-test" not in manager._tasks


@pytest.mark.asyncio
async def test_repeated_rebalances_replay_fresh_latest_group_from_anchored_offset(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    partition = Partition("alerts", 0)
    broker.initial_positions[partition] = None
    gates = [asyncio.Event() for _ in range(3)]
    started = [asyncio.Event() for _ in range(3)]
    deliveries = []

    async def dispatch(*args, **kwargs):
        index = len(deliveries)
        deliveries.append(kwargs["delivery_id"])
        started[index].set()
        await gates[index].wait()
        return acknowledge()

    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, workers=1, reset_policy="latest")
    try:
        for index in range(2):
            await started[index].wait()
            assert broker.committed[partition] == 0
            await broker.consumers[index].listener.on_partitions_revoked({partition})
            gates[index].set()
            await eventually(lambda: len(broker.consumers) == index + 2)
        await started[2].wait()
        assert broker.committed[partition] == 0
        gates[2].set()
        await eventually(lambda: broker.committed[partition] == 1)
        assert deliveries == ["kafka:alerts:0:0"] * 3
        assert manager.get_consumer_status("delivery-test")["state"] == "running"
    finally:
        for gate in gates:
            gate.set()


@pytest.mark.asyncio
async def test_stop_waits_for_in_progress_restart_then_leaves_consumer_stopped(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    await start(broker, filter_spec={"expr": "False"})
    real_get = kafka_manager.WorkflowStore.get_config
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_get(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real_get(*args, **kwargs)

    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", blocked_get)
    restarting = asyncio.create_task(manager.restart_workflow("delivery-test"))
    await entered.wait()
    stopping = asyncio.create_task(manager.stop_workflow("delivery-test"))
    try:
        await asyncio.sleep(0.02)
        assert not stopping.done(), "stop must not finish before an in-progress restart creates its consumer"
    finally:
        release.set()
        await asyncio.gather(restarting, stopping, return_exceptions=True)
    assert manager.get_consumer_status("delivery-test")["state"] == "stopped"
    assert all(consumer.closed for consumer in broker.consumers)


@pytest.mark.asyncio
async def test_stop_all_covers_restart_not_yet_registered_as_consumer(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    await start(broker, filter_spec={"expr": "False"})
    original_get = kafka_manager.WorkflowStore.get_config
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_get(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_get(*args, **kwargs)

    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", blocked_get)
    restarting = asyncio.create_task(manager.restart_workflow("delivery-test"))
    await entered.wait()
    stopping = asyncio.create_task(manager.stop_all())
    try:
        await asyncio.sleep(0.02)
        assert not stopping.done()
    finally:
        release.set()
        await asyncio.gather(restarting, stopping, return_exceptions=True)
    assert len(broker.consumers) == 1
    assert manager.get_consumer_status("delivery-test")["state"] == "stopped"
    assert not manager._tasks


def test_late_old_worker_failure_cannot_overwrite_stopped_status():
    manager = kafka_manager.KafkaManager()
    manager._status["stopped-workflow"] = {"state": "stopped", "error": None}
    old_abort = asyncio.Event()
    manager._fail_generation("stopped-workflow", old_abort, RuntimeError("late failure"))
    assert manager.get_consumer_status("stopped-workflow") == {"state": "stopped", "error": None}


@pytest.mark.asyncio
async def test_restart_config_read_failure_is_visible_in_status_api(monkeypatch):
    manager = kafka_manager.KafkaManager()
    async def fail(*args, **kwargs):
        raise sqlite3.OperationalError("configuration read failed")
    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", fail)
    result = await manager.restart_workflow("configuration-test")
    assert result["state"] == "failed"
    assert manager.get_consumer_status("configuration-test")["state"] == "failed"
    assert manager.get_consumer_status("configuration-test")["error"] == "configuration read failed"


@pytest.mark.asyncio
async def test_invalid_trigger_is_visible_as_startup_failure(monkeypatch):
    manager = kafka_manager.KafkaManager()
    async def config(*args, **kwargs):
        return {"enabled": True, "inputBroker": "memory:9092", "inputTopic": "alerts"}
    monkeypatch.setattr(kafka_manager.WorkflowStore, "get_config", config)
    monkeypatch.setattr(kafka_manager, "read_workflow_from_fs", lambda _: {"workflowJson": {
        "name": "invalid-trigger", "start": "accept", "nodes": [
            {"id": "accept", "type": "python", "code": "pass"}], "edges": [],
        "triggers": [{"id": "bad", "type": "kafka", "enabled": True,
                      "concurrency": {"maxParallel": "invalid"}}]}})
    result = await manager.restart_workflow("invalid-trigger")
    assert result["state"] == "failed"
    assert "workflow_plan_failed" in result["error"]
    assert manager.get_consumer_status("invalid-trigger")["state"] == "failed"


@pytest.mark.asyncio
async def test_async_assignment_must_anchor_before_any_record_is_fetched(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 42)])
    partition = Partition("alerts", 0)
    broker.initial_positions[partition] = None
    position_entered, release_position = asyncio.Event(), asyncio.Event()
    release_business = asyncio.Event()
    callback_tasks = []
    original = broker.Consumer
    class ConcurrentAssignmentConsumer(original):
        async def start(self):
            self.reset_position(partition)
            self.pending.extend(broker.records)
            # aiokafka signals wait_for_assignment before awaiting the listener.
            callback_tasks.append(asyncio.create_task(self.listener.on_partitions_assigned({partition})))
            await position_entered.wait()
        async def position(self, assigned):
            position_entered.set()
            await release_position.wait()
            return await super().position(assigned)
    broker.Consumer = ConcurrentAssignmentConsumer
    async def dispatch(*args, **kwargs):
        await release_business.wait()
        return acknowledge()
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    starting = asyncio.create_task(start(broker, reset_policy="latest"))
    try:
        await asyncio.wait_for(position_entered.wait(), timeout=2)
        # Give the consumer loop a chance to fetch while position() is pending.
        await asyncio.sleep(0.03)
        assert broker.fetched == [], "getone advanced the position before the assignment anchor was durable"
        release_position.set()
        await starting
        await eventually(lambda: broker.fetched == [(0, 42)])
        assert broker.committed[partition] == 42
        release_business.set()
        await eventually(lambda: broker.committed[partition] == 43)
    finally:
        release_position.set()
        release_business.set()
        await asyncio.gather(starting, *callback_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_idle_fetch_is_cancelled_across_repeated_assignment_callbacks(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    partition = Partition("alerts", 0)
    waiting = asyncio.Event()
    original = broker.Consumer
    class WatchingConsumer(original):
        async def getone(self):
            if not self.pending:
                waiting.set()
            return await super().getone()
    broker.Consumer = WatchingConsumer
    calls = []
    async def dispatch(wid, plan, message, *args, **kwargs):
        calls.append(message["offset"])
        return acknowledge()
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker)
    consumer = broker.consumers[0]
    await eventually(lambda: broker.committed.get(partition) == 1 and waiting.is_set())
    for offset in range(1, 11):
        await consumer.listener.on_partitions_revoked({partition})
        waiting.clear()
        consumer.reset_position(partition)
        consumer.pending.append(record(0, offset))
        await consumer.listener.on_partitions_assigned({partition})
        await eventually(lambda: broker.committed.get(partition) == offset + 1 and waiting.is_set())
    assert calls == list(range(11))
    assert len(broker.consumers) == 1
    assert manager.get_consumer_status("delivery-test")["state"] == "running"


@pytest.mark.asyncio
async def test_consumer_constructor_failure_reports_failed_promptly(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    original = broker.Consumer
    class InvalidConsumer(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            raise ValueError("invalid bootstrap configuration")
    broker.Consumer = InvalidConsumer
    monkeypatch.setattr(kafka_manager, "_CONNECT_WAIT_TIMEOUT_S", 0.02)
    await start(broker, expected_state="failed")
    assert "invalid bootstrap configuration" in manager.get_consumer_status("delivery-test")["error"]


@pytest.mark.asyncio
async def test_assignment_callback_does_not_block_coordinator_heartbeat_startup(pipeline, monkeypatch):
    manager, start = pipeline
    broker = Broker([record(0, 42)])
    partition = Partition("alerts", 0)
    broker.initial_positions[partition] = None
    heartbeat_started = asyncio.Event()
    original = broker.Consumer
    class CoordinatorConsumer(original):
        async def start(self):
            self.reset_position(partition)
            self.pending.extend(broker.records)
            await self.listener.on_partitions_assigned({partition})
            # Real GroupCoordinator starts heartbeat only after this callback.
            heartbeat_started.set()
        async def position(self, assigned):
            await heartbeat_started.wait()
            return await super().position(assigned)
    broker.Consumer = CoordinatorConsumer
    monkeypatch.setattr(kafka_manager, "_OFFSET_OPERATION_TIMEOUT_S", 0.02)
    async def dispatch(*args, **kwargs):
        return acknowledge()
    monkeypatch.setattr(manager, "_trigger_workflow", dispatch)
    await start(broker, reset_policy="latest")
    await eventually(lambda: broker.committed.get(partition) == 43)
    assert heartbeat_started.is_set()


@pytest.mark.asyncio
async def test_unexpected_fetch_cancellation_fails_instead_of_silently_hanging(pipeline):
    manager, start = pipeline
    broker = Broker([record(0, 0)])
    original = broker.Consumer
    class CancelledConsumer(original):
        async def getone(self):
            raise asyncio.CancelledError()
    broker.Consumer = CancelledConsumer
    await start(broker)
    await eventually(lambda: manager.get_consumer_status("delivery-test")["state"] == "failed")
    assert manager.get_consumer_status("delivery-test")["error_code"] == "fetch_cancelled"
    assert not broker.commit_attempts
