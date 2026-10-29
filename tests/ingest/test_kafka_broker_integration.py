"""Opt-in tests against a real disposable Kafka broker.

FLOCKS_KAFKA_TEST_BROKER=127.0.0.1:19092 python -m pytest \
    tests/ingest/test_kafka_broker_integration.py

Only UUID-named topics/groups created by this fixture are used or deleted.
The broker must allow topic creation/deletion. No customer configuration is read.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace
import uuid

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from flocks.config.config import Config
from flocks.ingest.kafka import manager as kafka_manager
from flocks.storage.storage import Storage
from flocks.workflow.store import WorkflowStore

pytestmark = pytest.mark.integration


async def eventually(predicate, timeout=30):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)


@pytest.fixture
async def broker_pipeline(monkeypatch, tmp_path):
    address = os.getenv("FLOCKS_KAFKA_TEST_BROKER")
    if not address:
        pytest.skip("set FLOCKS_KAFKA_TEST_BROKER to an isolated test broker")
    token = uuid.uuid4().hex
    topic = f"flocks-reliability-test-{token}"
    group = f"flocks-reliability-test-{token}"
    workflow_id = f"broker-test-{token}"
    for key, directory in {
        "FLOCKS_ROOT": tmp_path, "FLOCKS_SHARED_ROOT": tmp_path,
        "FLOCKS_DATA_DIR": tmp_path / "data",
        "FLOCKS_CONFIG_DIR": tmp_path / "config",
        "FLOCKS_WORKSPACE_DIR": tmp_path / "workspace",
    }.items():
        monkeypatch.setenv(key, str(directory))
    monkeypatch.setenv("FLOCKS_CONFIG_CONTENT", "{}")
    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", None)
    monkeypatch.setattr(Storage, "_initialized", False)
    monkeypatch.setattr(Storage, "_db_path", None)
    monkeypatch.setattr(Storage, "_init_pid", None)
    await WorkflowStore.init()
    manager = kafka_manager.KafkaManager()
    admin = AIOKafkaAdminClient(bootstrap_servers=address, request_timeout_ms=5000)
    producer = AIOKafkaProducer(bootstrap_servers=address, request_timeout_ms=5000)
    config = {"enabled": True, "inputBroker": address, "inputTopic": topic,
              "inputGroupId": group, "autoOffsetReset": "earliest"}
    workflow = {
        "name": workflow_id, "start": "accept",
        "nodes": [{"id": "accept", "type": "python",
                   "code": "outputs['received'] = inputs['kafka_message']"}],
        "edges": [],
        "triggers": [{"id": "input", "type": "kafka", "enabled": True,
                      "mapping": {"kafka_message": "$.body"},
                      "inputs": {"tool_context_required": False},
                      "concurrency": {"maxParallel": 4, "queueSize": 8}}],
    }

    async def get_config(*args, **kwargs):
        return config

    monkeypatch.setattr(WorkflowStore, "get_config", get_config)
    monkeypatch.setattr(kafka_manager, "read_workflow_from_fs", lambda _: {"workflowJson": workflow})
    monkeypatch.setattr(kafka_manager, "_DELIVERY_RETRY_DELAY_S", 0.02)
    monkeypatch.setattr(kafka_manager, "_REBALANCE_RECOVERY_DELAY_S", 0.05)

    async def start():
        status = await manager.restart_workflow(workflow_id)
        assert status["state"] in {"connecting", "running"}, status
        async def running():
            status = manager.get_consumer_status(workflow_id)
            assert status["state"] != "failed", status
            return status["state"] == "running"
        await eventually(running)

    async def send(partition, value):
        return await producer.send_and_wait(topic, json.dumps(value).encode(), partition=partition)

    async def committed(partition):
        offsets = await admin.list_consumer_group_offsets(group)
        value = offsets.get(TopicPartition(topic, partition))
        return value.offset if value else None

    async def wait_offset(partition, offset):
        async def matched():
            return await committed(partition) == offset
        await eventually(matched)

    async def wait_state(state):
        async def matched():
            return manager.get_consumer_status(workflow_id)["state"] == state
        await eventually(matched)

    created = False
    try:
        await admin.start()
        await admin.create_topics([NewTopic(topic, num_partitions=2, replication_factor=1)])
        created = True
        await producer.start()
        yield SimpleNamespace(
            manager=manager, admin=admin, address=address, topic=topic,
            group=group, workflow_id=workflow_id, config=config,
            start=start, send=send, committed=committed,
            wait_offset=wait_offset, wait_state=wait_state,
        )
    finally:
        await manager.stop_all()
        await producer.stop()
        if created:
            await admin.delete_topics([topic])
        await admin.close()
        await WorkflowStore.close()
        await Storage.shutdown()


async def test_real_broker_commits_only_completed_partition_prefix(broker_pipeline, monkeypatch):
    p = broker_pipeline
    blocked = asyncio.Event()
    release = asyncio.Event()
    completed = set()

    async def dispatch(wid, plan, message, *args, **kwargs):
        if message["id"] == "first":
            blocked.set()
            await release.wait()
        else:
            completed.add(message["id"])
        return kafka_manager._KafkaDispatchOutcome(acknowledged=True)

    monkeypatch.setattr(p.manager, "_trigger_workflow", dispatch)
    await p.start()
    try:
        await p.send(0, {"id": "first"})
        await asyncio.wait_for(blocked.wait(), 10)
        await p.send(0, {"id": "later"})
        await p.send(1, {"id": "independent"})
        await p.wait_offset(1, 1)
        assert completed == {"later", "independent"}
        assert await p.committed(0) == 0
        release.set()
        await p.wait_offset(0, 2)
        await p.manager.stop_workflow(p.workflow_id)
        await p.start()
        assert await p.committed(0) == 2
        assert await p.committed(1) == 1
    finally:
        release.set()


async def test_real_latest_group_replays_after_sqlite_terminal_failure(broker_pipeline, monkeypatch):
    p = broker_pipeline
    p.config["autoOffsetReset"] = "latest"
    await p.send(0, {"id": "before-subscription"})
    original_save = kafka_manager.record_execution_result
    saving = asyncio.Event()
    release = asyncio.Event()
    failing = True
    attempts = []

    async def save(wid, eid, data, **kwargs):
        if failing:
            attempts.append(data["deliveryId"])
            saving.set()
            await release.wait()
            raise sqlite3.OperationalError("synthetic terminal persistence failure")
        await original_save(wid, eid, data, **kwargs)

    monkeypatch.setattr(kafka_manager, "record_execution_result", save)
    await p.start()
    try:
        assert await p.committed(0) == 1
        await p.send(0, {"id": "must-replay"})
        await asyncio.wait_for(saving.wait(), 15)
        assert await p.committed(0) == 1
        release.set()
        await p.wait_state("failed")
        assert p.manager.get_consumer_status(p.workflow_id)["error_code"] == "execution_record_failed"
        assert len(attempts) == kafka_manager._DELIVERY_MAX_ATTEMPTS
        assert set(attempts) == {f"kafka:{p.topic}:0:1"}
        assert await p.committed(0) == 1
        failing = False
        await p.start()
        await p.wait_offset(0, 2)
        executions = await WorkflowStore.list_executions(p.workflow_id)
        succeeded = [record for record in executions if record["status"] == "success"]
        assert len(succeeded) == 1
        assert succeeded[0]["deliveryId"] == attempts[0]
        assert succeeded[0]["outputResults"]["received"] == {"id": "must-replay"}
    finally:
        release.set()


async def test_real_rebalance_rejoins_and_replays_unfinished_deliveries(broker_pipeline, monkeypatch):
    p = broker_pipeline
    blocked = asyncio.Event()
    allow_success = asyncio.Event()
    cancellations = []
    replayed = set()
    seen = set()

    async def dispatch(wid, plan, message, *args, **kwargs):
        delivery_id = kwargs["delivery_id"]
        cancel = kwargs["generation_cancel_event"]
        if not allow_success.is_set():
            seen.add(delivery_id)
            if len(seen) == 2:
                blocked.set()
            while not cancel.is_set() and not allow_success.is_set():
                await asyncio.sleep(0.01)
            if cancel.is_set():
                cancellations.append(delivery_id)
                return kafka_manager._KafkaDispatchOutcome(acknowledged=False, error="cancelled")
        replayed.add(delivery_id)
        return kafka_manager._KafkaDispatchOutcome(acknowledged=True)

    monkeypatch.setattr(p.manager, "_trigger_workflow", dispatch)
    competitor = AIOKafkaConsumer(
        p.topic, bootstrap_servers=p.address, group_id=p.group,
        enable_auto_commit=False, auto_offset_reset="earliest",
    )
    await p.start()
    try:
        await p.send(0, {"id": "partition-0"})
        await p.send(1, {"id": "partition-1"})
        await asyncio.wait_for(blocked.wait(), 10)
        await competitor.start()
        async def cancelled():
            return len(cancellations) >= 2
        await eventually(cancelled)
        assert await p.committed(0) == 0
        assert await p.committed(1) == 0
        allow_success.set()
        await competitor.stop()
        await p.wait_offset(0, 1)
        await p.wait_offset(1, 1)
        await p.wait_state("running")
        assert replayed == seen
        assert p.manager._tasks[p.workflow_id].done() is False
    finally:
        allow_success.set()
        await competitor.stop()
