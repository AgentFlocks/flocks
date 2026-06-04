"""Lifecycle manager for Kafka consumers → workflow runs.

This mirrors :mod:`flocks.ingest.syslog.manager`: one async consumer task per
workflow id (when enabled), draining a bounded queue with a fixed worker pool so
an inbound burst cannot translate into unbounded ``asyncio.Task`` growth.

Differences from the syslog manager:

* The transport is a Kafka *consumer* (``aiokafka.AIOKafkaConsumer``) instead of
  a UDP/TCP socket bind.  "binding/listening" is replaced by
  "connecting/running"; a connection failure (broker unreachable, auth error)
  is surfaced the same way a bind failure is.
* Backpressure uses a *blocking* ``queue.put`` instead of ``put_nowait``+drop:
  this avoids local drops while the worker pool falls behind and lets the
  consumer pause naturally. Because ``aiokafka`` auto-commits fetched offsets,
  the current crash semantics are still best-effort / at-most-once rather than
  fully durable at-least-once delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.execution_store import (
    DEFAULT_LARGE_LIST_KEYS,
    compact_history_for_storage,
    compact_outputs_for_storage,
    create_execution_record,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.fs_store import read_workflow_from_fs
from flocks.workflow.runner import run_workflow

from flocks.ingest.kafka.constants import WORKFLOW_KAFKA_CONFIG_PREFIX

log = Log.create(service="kafka.manager")


# Maximum concurrent workflow executions per workflow to avoid FD exhaustion and
# SQLite write contention. Kafka messages can carry large JSON payloads, so keep
# this lower than syslog to avoid several full workflow histories being resident
# at the same time.
_MAX_CONCURRENT_EXECUTIONS = 2
# Maximum number of buffered Kafka messages per workflow.  Unlike syslog we do
# not drop on overflow; a full queue applies backpressure to the consumer loop.
_MAX_QUEUE_SIZE = 100
# Maximum time we wait for the consumer to either connect successfully or fail
# during ``restart_workflow`` so the HTTP save endpoint can surface connection
# errors instead of pretending the consumer is running.
_CONNECT_WAIT_TIMEOUT_S = 8.0
# Kafka client request timeout; kept short so an unreachable broker fails fast
# within the connect-wait window above.
_REQUEST_TIMEOUT_MS = 5000
# Bound aiokafka's internal fetch buffers. The explicit queue below provides the
# main backpressure; these caps stop the client from prefetching a large burst
# before the workflow workers can drain it.
_FETCH_MAX_BYTES = 8 * 1024 * 1024
_MAX_PARTITION_FETCH_BYTES = 4 * 1024 * 1024
_MAX_POLL_RECORDS = 16

_KAFKA_STORAGE_LIST_KEYS = DEFAULT_LARGE_LIST_KEYS | frozenset(
    {
        "duplicate_alerts",
        "triage_candidate_alerts",
        "enriched_alerts_with_triage",
        "kafka_messages",
    }
)
_KAFKA_RAW_INPUT_KEYS = frozenset({"kafka_message", "kafka_value", "kafka_record"})
_STORAGE_PREVIEW_CHARS = 512


@dataclass(frozen=True)
class _QueuedKafkaMessage:
    """Raw Kafka value kept in the queue until a worker is ready to process it."""

    raw_value: Optional[bytes]
    size_bytes: int


def _strip_execution_only_comments(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_execution_only_comments(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_execution_only_comments(nested)
        for key, nested in value.items()
        if not str(key).startswith("_comment")
    }


def _decode_message(raw: Optional[bytes]) -> Any:
    """Decode a Kafka message value to a Python object.

    Tries UTF-8 + JSON first (the common case for structured events); falls back
    to the raw decoded string, then to a base64-free repr for binary payloads.
    """
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
    except Exception:
        return raw.hex()
    try:
        return json.loads(text)
    except Exception:
        return text


def _summarize_large_value(value: Any) -> Any:
    """Return a bounded representation suitable for execution history storage."""
    if isinstance(value, bytes):
        return {
            "_type": "bytes",
            "sizeBytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, str):
        if len(value) <= _STORAGE_PREVIEW_CHARS:
            return value
        return {
            "_type": "string",
            "chars": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest(),
            "preview": value[:_STORAGE_PREVIEW_CHARS],
        }
    if isinstance(value, (list, tuple)):
        return {
            "_type": "list",
            "count": len(value),
            "preview": [_summarize_large_value(item) for item in list(value)[:3]],
        }
    if isinstance(value, dict):
        compacted: Dict[str, Any] = {
            "_type": "dict",
            "keys": list(value.keys())[:50],
        }
        for key in (
            "id",
            "_id",
            "log_id",
            "raw_log_id",
            "event_id",
            "message_id",
            "source",
            "product_type",
            "hostname",
        ):
            if key in value:
                compacted[key] = _summarize_large_value(value[key])
        if "alarmData" in value:
            compacted["alarmData"] = _summarize_large_value(value["alarmData"])
        return compacted
    return value


def _compact_for_kafka_storage(outputs: Any) -> Dict[str, Any]:
    """Compact all known large workflow lists for high-frequency Kafka runs."""
    compacted = compact_outputs_for_storage(
        outputs,
        keys=_KAFKA_STORAGE_LIST_KEYS,
        size_threshold=0,
    )
    for key, value in list(compacted.items()):
        if key == "kafka_output" or (
            isinstance(value, str) and len(value) > _STORAGE_PREVIEW_CHARS
        ):
            compacted[key] = _summarize_large_value(value)
    return compacted


def _compact_history_for_kafka_storage(history: Any, *, input_key: str) -> List[Any]:
    compacted = compact_history_for_storage(
        history,
        keys=_KAFKA_STORAGE_LIST_KEYS,
        size_threshold=0,
    )
    raw_input_keys = _KAFKA_RAW_INPUT_KEYS | frozenset({input_key})
    for step in compacted:
        if not isinstance(step, dict):
            continue
        for field in ("inputs", "outputs"):
            payload = step.get(field)
            if not isinstance(payload, dict):
                continue
            for key, value in list(payload.items()):
                if key in raw_input_keys or key == "kafka_output" or (
                    isinstance(value, str) and len(value) > _STORAGE_PREVIEW_CHARS
                ):
                    payload[key] = _summarize_large_value(value)
    return compacted


class KafkaManager:
    """One async consumer task per workflow id (when enabled)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        # Per-workflow bounded message queue for backpressure
        self._queues: dict[str, asyncio.Queue] = {}
        # Per-workflow fixed worker pool draining the queue
        self._worker_pools: dict[str, List[asyncio.Task]] = {}
        # Per-workflow consumer runtime status for the kafka-status API.
        # State values: "connecting" | "running" | "failed" | "stopped".
        self._status: dict[str, Dict[str, Any]] = {}
        # Per-workflow event signalled once the consumer has either connected
        # successfully or failed; used by ``restart_workflow``.
        self._ready: dict[str, asyncio.Event] = {}

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_KAFKA_CONFIG_PREFIX}{workflow_id}"

    async def start_all(self) -> None:
        try:
            keys = await Storage.list_keys(WORKFLOW_KAFKA_CONFIG_PREFIX)
        except Exception as exc:
            log.warning("kafka.list_keys_failed", {"error": str(exc)})
            return

        for key in keys:
            if not key.startswith(WORKFLOW_KAFKA_CONFIG_PREFIX):
                continue
            workflow_id = key[len(WORKFLOW_KAFKA_CONFIG_PREFIX):]
            if not workflow_id:
                continue
            try:
                data = await Storage.read(key)
            except Exception as exc:
                log.warning("kafka.config_read_failed", {"key": key, "error": str(exc)})
                continue
            if isinstance(data, dict) and data.get("enabled"):
                await self.restart_workflow(workflow_id)

    async def stop_all(self) -> None:
        for workflow_id in list(self._tasks.keys()):
            await self.stop_workflow(workflow_id)

    async def _cleanup_runtime_resources(self, workflow_id: str) -> None:
        # Cancel all worker pool tasks; pop first so callers observing a stopped
        # consumer see an empty pool immediately.
        pool = self._worker_pools.pop(workflow_id, None)
        if pool:
            for worker in pool:
                if not worker.done():
                    worker.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pool, return_exceptions=True),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        self._queues.pop(workflow_id, None)
        self._abort_events.pop(workflow_id, None)
        self._ready.pop(workflow_id, None)

    def get_consumer_status(self, workflow_id: str) -> Dict[str, Any]:
        """Return a snapshot of the consumer runtime state for ``workflow_id``.

        Result shape::

            {"state": "connecting|running|failed|stopped", "error": "..." | None,
             "broker": "...", "topic": "...", "groupId": "...",
             "queueSize": 12, "queueCapacity": <queue.maxsize>,
             "workerCount": <_MAX_CONCURRENT_EXECUTIONS>}
        """
        status = dict(self._status.get(workflow_id) or {"state": "stopped"})
        q = self._queues.get(workflow_id)
        if q is not None:
            status["queueSize"] = q.qsize()
            status["queueCapacity"] = q.maxsize
        pool = self._worker_pools.get(workflow_id)
        if pool is not None:
            status["workerCount"] = sum(1 for t in pool if not t.done())
        return status

    async def stop_workflow(self, workflow_id: str) -> None:
        ev = self._abort_events.get(workflow_id)
        if ev is not None:
            ev.set()
        task = self._tasks.pop(workflow_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self._cleanup_runtime_resources(workflow_id)
        if workflow_id in self._status:
            self._status[workflow_id] = {"state": "stopped", "error": None}

    async def restart_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Restart the consumer and return its post-connect runtime status.

        Blocks until the consumer connects, the connection fails, or
        ``_CONNECT_WAIT_TIMEOUT_S`` elapses, so the HTTP save endpoint can
        surface connection errors to the user.
        """
        await self.stop_workflow(workflow_id)
        key = self._config_key(workflow_id)
        try:
            data = await Storage.read(key)
        except Exception as exc:
            log.warning("kafka.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"state": "failed", "error": str(exc)}
        if not isinstance(data, dict) or not data.get("enabled"):
            self._status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}

        input_broker = str(data.get("inputBroker") or "").strip()
        input_topic = str(data.get("inputTopic") or "").strip()
        if not input_broker or not input_topic:
            err = "missing_input_broker_or_topic"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.config_incomplete", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        # Load and cache the workflow JSON once; avoids a disk read per message.
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            err = "workflow_not_found"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.workflow_not_found_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}
        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            err = "workflow_json_missing"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.workflow_json_missing_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        group_id = str(data.get("inputGroupId") or "").strip() or f"flocks-consumer-{workflow_id}"
        input_key = str(data.get("inputKey") or "kafka_message")
        configured_inputs = _strip_execution_only_comments(
            data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
        )

        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._queues[workflow_id] = queue

        abort = asyncio.Event()
        self._abort_events[workflow_id] = abort

        ready = asyncio.Event()
        self._ready[workflow_id] = ready

        self._status[workflow_id] = {
            "state": "connecting",
            "error": None,
            "broker": input_broker,
            "topic": input_topic,
            "groupId": group_id,
        }

        # Fixed worker pool drains the queue (at most _MAX_CONCURRENT_EXECUTIONS
        # concurrent runs).
        workers: List[asyncio.Task] = []
        for i in range(_MAX_CONCURRENT_EXECUTIONS):
            workers.append(
                asyncio.create_task(
                    self._worker_loop(
                        workflow_id, workflow_json, input_key, configured_inputs, queue, abort,
                    ),
                    name=f"kafka-worker-{workflow_id}-{i}",
                )
            )
        self._worker_pools[workflow_id] = workers

        task = asyncio.create_task(
            self._consumer_loop(
                workflow_id, input_broker, input_topic, group_id,
                str(data.get("autoOffsetReset") or "latest"),
                queue, abort, ready,
            ),
            name=f"kafka-{workflow_id}",
        )
        self._tasks[workflow_id] = task

        try:
            await asyncio.wait_for(ready.wait(), timeout=_CONNECT_WAIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            current = self._status.get(workflow_id) or {}
            if current.get("state") == "connecting":
                self._status[workflow_id] = {
                    **current,
                    "state": "connecting",
                    "error": "connect_pending_timeout",
                }
            log.warning("kafka.connect_pending_timeout", {"workflow_id": workflow_id})

        current = self._status.get(workflow_id) or {}
        if current.get("state") == "failed":
            task = self._tasks.get(workflow_id)
            if task is not None and not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            task = self._tasks.get(workflow_id)
            if task is not None and task.done():
                self._tasks.pop(workflow_id, None)

        log.info("kafka.consumer_scheduled", {"workflow_id": workflow_id})
        return self.get_consumer_status(workflow_id)

    async def _consumer_loop(
        self,
        workflow_id: str,
        broker: str,
        topic: str,
        group_id: str,
        auto_offset_reset: str,
        queue: asyncio.Queue,
        abort: asyncio.Event,
        ready: asyncio.Event,
    ) -> None:
        try:
            from aiokafka import AIOKafkaConsumer
        except Exception as exc:
            self._status[workflow_id] = {
                "state": "failed",
                "error": f"aiokafka_import_failed: {exc}",
                "broker": broker,
                "topic": topic,
                "groupId": group_id,
            }
            ready.set()
            log.error("kafka.import_failed", {"workflow_id": workflow_id, "error": str(exc)})
            await self._cleanup_runtime_resources(workflow_id)
            return

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=broker,
            group_id=group_id,
            # Auto-commit advances based on fetched progress, not worker
            # completion. Backpressure narrows the crash window but current
            # semantics remain best-effort / at-most-once.
            enable_auto_commit=True,
            auto_offset_reset=auto_offset_reset if auto_offset_reset in ("latest", "earliest") else "latest",
            request_timeout_ms=_REQUEST_TIMEOUT_MS,
            fetch_max_bytes=_FETCH_MAX_BYTES,
            max_partition_fetch_bytes=_MAX_PARTITION_FETCH_BYTES,
            max_poll_records=_MAX_POLL_RECORDS,
        )

        try:
            await consumer.start()
        except asyncio.CancelledError:
            try:
                await consumer.stop()
            except Exception:
                pass
            raise
        except Exception as exc:
            self._status[workflow_id] = {
                "state": "failed",
                "error": str(exc),
                "broker": broker,
                "topic": topic,
                "groupId": group_id,
            }
            ready.set()
            log.error(
                "kafka.connect_failed",
                {"workflow_id": workflow_id, "error": str(exc), "broker": broker, "topic": topic},
            )
            try:
                await consumer.stop()
            except Exception:
                pass
            await self._cleanup_runtime_resources(workflow_id)
            return

        self._status[workflow_id] = {
            "state": "running",
            "error": None,
            "broker": broker,
            "topic": topic,
            "groupId": group_id,
        }
        ready.set()
        log.info("kafka.consumer_running", {"workflow_id": workflow_id, "topic": topic})

        try:
            async for msg in consumer:
                if abort.is_set():
                    break
                raw_value = msg.value
                queued = _QueuedKafkaMessage(
                    raw_value=raw_value,
                    size_bytes=len(raw_value) if raw_value is not None else 0,
                )
                # Blocking put applies backpressure instead of dropping messages.
                await queue.put(queued)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._status[workflow_id] = {
                "state": "failed",
                "error": str(exc),
                "broker": broker,
                "topic": topic,
                "groupId": group_id,
            }
            log.error("kafka.consumer_error", {"workflow_id": workflow_id, "error": str(exc)})
            await self._cleanup_runtime_resources(workflow_id)
        finally:
            try:
                await consumer.stop()
            except Exception:
                pass
            current = asyncio.current_task()
            if self._tasks.get(workflow_id) is current:
                self._tasks.pop(workflow_id, None)

    async def _worker_loop(
        self,
        workflow_id: str,
        workflow_json: Any,
        input_key: str,
        configured_inputs: Dict[str, Any],
        queue: asyncio.Queue,
        abort: asyncio.Event,
    ) -> None:
        while not abort.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            try:
                if isinstance(msg, _QueuedKafkaMessage):
                    msg = _decode_message(msg.raw_value)
                await self._trigger_workflow(
                    workflow_id, workflow_json, msg, input_key, configured_inputs,
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning(
                    "kafka.worker_dispatch_failed",
                    {"workflow_id": workflow_id, "error": str(exc)},
                )

    async def _trigger_workflow(
        self,
        workflow_id: str,
        workflow_json: Any,
        message: Any,
        input_key: str,
        configured_inputs: Optional[Dict[str, Any]] = None,
    ) -> None:
        configured_inputs = _strip_execution_only_comments(
            configured_inputs if isinstance(configured_inputs, dict) else {}
        )
        delivery_id = f"kafka-{uuid.uuid4().hex}"
        inputs = {
            **configured_inputs,
            input_key: message,
            "_flocks": {
                "trigger": {
                    "id": "kafka-default",
                    "type": "kafka",
                    "source": "kafka",
                    "deliveryId": delivery_id,
                    "receivedAt": int(time.time() * 1000),
                    "attempt": 1,
                }
            },
        }
        input_params = {"_trigger": "kafka", input_key: _summarize_large_value(message)}
        for key, value in configured_inputs.items():
            if key == input_key:
                continue
            input_params[key] = _summarize_large_value(value)
        input_params["_flocks"] = inputs["_flocks"]

        exec_data = await create_execution_record(
            workflow_id,
            input_params=input_params,
        )
        exec_id = exec_data["id"]
        start_time = time.time()

        result = None
        try:
            result = await asyncio.to_thread(
                run_workflow,
                workflow=workflow_json,
                inputs=inputs,
                trace=False,
                history_mode="summary",
            )
            status, error_msg = resolve_execution_outcome(result)
            duration = time.time() - start_time
            exec_data.update({
                "status": status,
                "outputResults": _compact_for_kafka_storage(result.outputs),
                "finishedAt": int(time.time() * 1000),
                "duration": duration,
                "errorMessage": error_msg,
                "executionLog": _compact_history_for_kafka_storage(
                    result.history,
                    input_key=input_key,
                ),
                "currentNodeId": result.last_node_id,
                "currentPhase": status,
                "currentStepIndex": result.steps,
                "triggerId": "kafka-default",
                "triggerType": "kafka",
                "deliveryId": delivery_id,
                "attempt": 1,
                "triggerSource": "kafka",
            })
        except Exception as exc:
            duration = time.time() - start_time
            log.error(
                "kafka.workflow_run_failed",
                {"workflow_id": workflow_id, "exec_id": exec_id, "error": str(exc)},
            )
            exec_data.update({
                "status": "error",
                "errorMessage": str(exc),
                "finishedAt": int(time.time() * 1000),
                "duration": duration,
                "currentPhase": "error",
                "triggerId": "kafka-default",
                "triggerType": "kafka",
                "deliveryId": delivery_id,
                "attempt": 1,
                "triggerSource": "kafka",
            })
        finally:
            try:
                await record_execution_result(workflow_id, exec_id, exec_data)
            except Exception as exc:
                log.warning("kafka.exec_record_failed", {"exec_id": exec_id, "error": str(exc)})


default_manager = KafkaManager()
