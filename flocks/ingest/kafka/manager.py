"""Lifecycle manager for Kafka consumers → workflow runs.

This mirrors :mod:`flocks.ingest.syslog.manager`: one async consumer task per
workflow id (when enabled), draining a bounded queue with a fixed worker pool so
an inbound burst cannot translate into unbounded ``asyncio.Task`` growth.

Differences from the syslog manager:

* The transport is a Kafka *consumer* (``aiokafka.AIOKafkaConsumer``) instead of
  a UDP/TCP socket bind.  "binding/listening" is replaced by
  "connecting/running"; a connection failure (broker unreachable, auth error)
  is surfaced the same way a bind failure is.
* Backpressure uses a *blocking* ``queue.put`` instead of ``put_nowait``+drop.
  Offsets are committed explicitly after successful workflow completion and
  persistence, never past an unfinished message in the same partition. A crash
  between workflow effects and offset commit can replay a message; workflows
  with external effects must use the stable Kafka delivery id for idempotency.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from flocks.hooks.execution import execute_with_hooks
from flocks.hooks.pipeline import HookPipeline
from flocks.utils.log import Log
from flocks.workflow.execution_store import (
    DEFAULT_LARGE_LIST_KEYS,
    compact_history_for_storage,
    compact_outputs_for_storage,
    create_execution_record,
    ExecutionStepRecorder,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.execution_plan import build_workflow_execution_plan
from flocks.workflow.fs_store import read_workflow_from_fs
from flocks.workflow.models import Workflow
from flocks.workflow.runner import run_workflow
from flocks.workflow.store import WorkflowStore
from flocks.workflow.tool_context import (
    build_workflow_tool_context,
    cleanup_workflow_tool_context,
)

from flocks.ingest.kafka.constants import WORKFLOW_KAFKA_CONFIG_PREFIX
from flocks.workflow.triggers.compat import legacy_kafka_trigger_from_config
from flocks.workflow.triggers.dispatcher import EventDispatcher, TriggerDispatchError, build_trigger_event
from flocks.workflow.triggers.models import (
    TriggerDefinition,
    workflow_json_declares_triggers,
    workflow_trigger_definitions_from_json,
)

log = Log.create(service="kafka.manager")


# Maximum concurrent workflow executions per workflow to avoid FD exhaustion and
# SQLite write contention. Kafka messages can carry large JSON payloads, so keep
# this bounded even when a trigger requests a larger worker pool.
_MAX_CONCURRENT_EXECUTIONS = 8
# Maximum number of buffered Kafka messages per workflow.  Unlike syslog we do
# not drop on overflow; a full queue applies backpressure to the consumer loop.
_MAX_QUEUE_SIZE = 1000
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
_DELIVERY_MAX_ATTEMPTS = 3
_DELIVERY_RETRY_DELAY_S = 0.25
# aiokafka may retry retriable commit failures internally without a deadline.
_OFFSET_OPERATION_TIMEOUT_S = _REQUEST_TIMEOUT_MS / 1000 + 1.0
_WORKER_DRAIN_TIMEOUT_S = 5.0
_REBALANCE_RECOVERY_DELAY_S = 1.0
_REBALANCE_RECOVERY_MAX_DELAY_S = 30.0
_RECOVERABLE_OWNERSHIP_ERRORS = frozenset({
    "partition_revoked_with_pending_deliveries", "partition_changed_before_dispatch", "partition_not_owned",
})


def _worker_count_for_trigger(trigger: TriggerDefinition) -> int:
    return min(_MAX_CONCURRENT_EXECUTIONS, max(1, int(trigger.concurrency.maxParallel)))


def _queue_size_for_trigger(trigger: TriggerDefinition) -> int:
    return min(_MAX_QUEUE_SIZE, max(1, int(trigger.concurrency.queueSize)))


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
    delivery: Optional[_KafkaDelivery] = None


class _KafkaGenerationStopped(Exception):
    """The consumer generation no longer owns its queued deliveries."""


class _KafkaDeliveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _KafkaDispatchOutcome:
    acknowledged: bool
    exec_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class _KafkaDelivery:
    tracker: _KafkaOffsetTracker
    partition: Any
    offset: int
    epoch: int
    completed: bool = False

    @property
    def delivery_id(self) -> str:
        return f"kafka:{self.partition.topic}:{self.partition.partition}:{self.offset}"

    def diagnostics(self) -> Dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "topic": self.partition.topic,
            "partition": self.partition.partition,
            "offset": self.offset,
        }


class _KafkaOffsetTracker:
    """Commit only completed prefixes of records fetched in partition order.

    Kafka offsets can have gaps (compaction/transactions), so track the actual
    delivered records rather than assuming every integer offset is present.
    All methods run on the consumer's event loop. Rebalance callbacks invalidate
    ownership synchronously and must not await the commit lock.
    """

    def __init__(self, consumer: Any, abort: asyncio.Event, *, max_pending: int) -> None:
        self.consumer = consumer
        self.abort = abort
        self.closed = False
        self._epochs: Dict[Any, int] = {}
        self._assigned: set[Any] = set()
        self._anchored: set[Any] = set()
        self._pending: Dict[Any, deque[_KafkaDelivery]] = {}
        self._commit_lock = asyncio.Lock()
        # A slow partition head can block commits even while later workers
        # finish quickly. Bound that completed-but-uncommitted backlog too.
        self._slots = asyncio.Semaphore(max(1, max_pending))

    async def wait_for_slot(self) -> None:
        await self._slots.acquire()

    def assigned(self, partitions: Iterable[Any]) -> None:
        for partition in partitions:
            self._assigned.add(partition)
            self._anchored.discard(partition)
            self._epochs[partition] = self._epochs.get(partition, 0) + 1

    def revoked(self, partitions: Iterable[Any]) -> bool:
        pending = False
        for partition in partitions:
            self._assigned.discard(partition)
            pending = bool(self._pending.pop(partition, ())) or pending
            self._anchored.discard(partition)
            self._epochs[partition] = self._epochs.get(partition, 0) + 1
        return pending

    def close(self) -> None:
        self.closed = True

    def epoch_for(self, partition: Any) -> int:
        if partition not in self._assigned:
            raise _KafkaDeliveryError("partition_not_owned", "Kafka delivered a record from a revoked partition")
        return self._epochs[partition]

    def register(self, partition: Any, offset: int, epoch: int) -> _KafkaDelivery:
        if partition not in self._assigned or self._epochs.get(partition) != epoch:
            raise _KafkaDeliveryError(
                "partition_changed_before_dispatch",
                "Kafka partition ownership changed while the fetched record waited for capacity",
            )
        delivery = _KafkaDelivery(self, partition, offset, epoch)
        pending = self._pending.setdefault(partition, deque())
        if pending and offset <= pending[-1].offset:
            raise _KafkaDeliveryError("offset_order_invalid", "Kafka returned offsets out of partition order")
        pending.append(delivery)
        return delivery

    def _check_partition_owner(self, partition: Any, epoch: int) -> None:
        if (
            self.closed
            or self.abort.is_set()
            or partition not in self._assigned
            or epoch != self._epochs.get(partition, 0)
        ):
            raise _KafkaGenerationStopped()

    async def anchor_partition(self, partition: Any, epoch: int) -> None:
        """Pin an assignment's reset position before fetching any record.

        With reset=latest and no committed position, restarting after the first
        failed delivery would otherwise reset to a newer end offset. The same
        applies when retention invalidated an old committed position. Anchor
        all assigned partitions before getone, including ones that might later
        wait for capacity behind a different partition's slow head message.
        """
        self._check_partition_owner(partition, epoch)
        if partition in self._anchored:
            return
        async with self._commit_lock:
            self._check_partition_owner(partition, epoch)
            if partition in self._anchored:
                return
            position = await self.consumer.position(partition)
            committed = await self.consumer.committed(partition)
            self._check_partition_owner(partition, epoch)
            if committed != position:
                await self.consumer.commit({partition: position})
                self._check_partition_owner(partition, epoch)
                log.info(
                    "kafka.offset_start_anchored",
                    {"topic": partition.topic, "partition": partition.partition, "offset": position},
                )
            self._anchored.add(partition)

    async def complete(self, delivery: _KafkaDelivery) -> None:
        async with self._commit_lock:
            self._check_partition_owner(delivery.partition, delivery.epoch)
            delivery.completed = True
            pending = self._pending.get(delivery.partition, ())
            prefix = []
            for item in pending:
                if not item.completed:
                    break
                prefix.append(item)
            if not prefix:
                return
            # Never use commit() without explicit offsets: the consumer's fetch
            # position can already be far beyond the completed worker prefix.
            await self.consumer.commit({delivery.partition: prefix[-1].offset + 1})
            if not self.closed and not self.abort.is_set():
                for _ in prefix:
                    pending.popleft()
                    self._slots.release()


def _delivery_error_diagnostics(exc: BaseException) -> Dict[str, Any]:
    facts: Dict[str, Any] = {"error": str(exc), "error_type": type(exc).__name__}
    if isinstance(exc, _KafkaDeliveryError):
        facts["error_code"] = exc.code
    seen: set[int] = set()
    cause: Optional[BaseException] = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        for attr in ("sqlite_errorcode", "sqlite_errorname"):
            value = getattr(cause, attr, None)
            if value is not None:
                facts[attr] = value
        cause = cause.__cause__ or cause.__context__
    return facts


async def _until_aborted(operation: Awaitable[Any], abort: asyncio.Event) -> Any:
    """Wake a blocked fetch/put promptly when a worker stops the generation."""
    task = asyncio.ensure_future(operation)
    stopped = asyncio.create_task(abort.wait())
    try:
        await asyncio.wait((task, stopped), return_when=asyncio.FIRST_COMPLETED)
        if abort.is_set():
            raise _KafkaGenerationStopped()
        return task.result()
    finally:
        for pending in (task, stopped):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, stopped, return_exceptions=True)


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


def _configured_bool(value: Any, *, default: bool) -> bool:
    """Parse a boolean trigger input without treating ``"false"`` as true."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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
        if key == "kafka_output" or (isinstance(value, str) and len(value) > _STORAGE_PREVIEW_CHARS):
            compacted[key] = _summarize_large_value(value)
    return compacted


def _compact_history_for_kafka_storage(
    history: Any,
    *,
    input_key: str,
    input_keys: Iterable[str] | None = None,
) -> List[Any]:
    compacted = compact_history_for_storage(
        history,
        keys=_KAFKA_STORAGE_LIST_KEYS,
        size_threshold=0,
    )
    raw_input_keys = _KAFKA_RAW_INPUT_KEYS | frozenset(input_keys or {input_key})
    for step in compacted:
        if not isinstance(step, dict):
            continue
        for field in ("inputs", "outputs"):
            payload = step.get(field)
            if not isinstance(payload, dict):
                continue
            for key, value in list(payload.items()):
                if (
                    key in raw_input_keys
                    or key == "kafka_output"
                    or (isinstance(value, str) and len(value) > _STORAGE_PREVIEW_CHARS)
                ):
                    payload[key] = _summarize_large_value(value)
    return compacted


def _compact_step_for_kafka_storage(
    step: Any,
    *,
    input_key: str,
    input_keys: Iterable[str] | None = None,
) -> Dict[str, Any]:
    compacted = _compact_history_for_kafka_storage(
        [step],
        input_key=input_key,
        input_keys=input_keys,
    )
    return compacted[0] if compacted and isinstance(compacted[0], dict) else {}


class KafkaManager:
    """One async consumer task per workflow id (when enabled)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        # Serialize lifecycle requests across awaits (e.g. a save/restart and
        # a simultaneous disable). Otherwise the earlier restart can revive
        # a consumer after the later stop has already returned.
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._stopping_all = False
        self._abort_events: dict[str, asyncio.Event] = {}
        # Per-workflow bounded message queue for backpressure
        self._queues: dict[str, asyncio.Queue] = {}
        # Per-workflow fixed worker pool draining the queue
        self._worker_pools: dict[str, List[asyncio.Task]] = {}
        # One cancellation event per consumer generation. It exists before
        # workers start, closing the stop-vs-run-registration race.
        self._generation_cancel_events: dict[str, threading.Event] = {}
        self._draining_workers: dict[str, set[asyncio.Task]] = {}
        # Per-workflow consumer runtime status for the kafka-status API.
        # State values: "connecting" | "running" | "failed" | "stopped".
        self._status: dict[str, Dict[str, Any]] = {}
        # Per-workflow event signalled once the consumer has either connected
        # successfully or failed; used by ``restart_workflow``.
        self._ready: dict[str, asyncio.Event] = {}
        self._dispatcher = EventDispatcher()

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_KAFKA_CONFIG_PREFIX}{workflow_id}"

    @staticmethod
    def _default_trigger_from_config(data: Dict[str, Any]) -> TriggerDefinition:
        trigger = legacy_kafka_trigger_from_config(data)
        if trigger is None:
            return TriggerDefinition.model_validate(
                {
                    "id": "kafka-default",
                    "type": "kafka",
                    "enabled": bool(data.get("enabled")),
                    "source": {
                        "inputBroker": data.get("inputBroker") or "",
                        "inputTopic": data.get("inputTopic") or "",
                        "inputGroupId": data.get("inputGroupId") or "",
                        "autoOffsetReset": data.get("autoOffsetReset") or "latest",
                    },
                    "mapping": {
                        str(data.get("inputKey") or "kafka_message"): "$.body",
                    },
                    "inputs": _strip_execution_only_comments(
                        data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
                    ),
                    "updatedAt": data.get("updatedAt"),
                }
            )
        return trigger

    def _resolve_active_trigger(self, workflow_json: Dict[str, Any], data: Dict[str, Any]) -> TriggerDefinition:
        if workflow_json_declares_triggers(workflow_json):
            triggers = workflow_trigger_definitions_from_json(workflow_json)
            trigger = next((item for item in triggers if item.type == "kafka"), None)
            if trigger is not None:
                return trigger
        return self._default_trigger_from_config(data)

    async def start_all(self) -> None:
        self._stopping_all = False
        try:
            configs = await WorkflowStore.list_configs(kind="workflow_kafka_config")
        except Exception as exc:
            log.warning("kafka.list_configs_failed", {"error": str(exc)})
            return

        for workflow_id, data in configs:
            if not workflow_id:
                continue
            if isinstance(data, dict) and data.get("enabled"):
                try:
                    await self.restart_workflow(workflow_id, startup=True)
                except Exception as exc:
                    self._status[workflow_id] = {"state": "failed", "error": str(exc)}
                    log.warning(
                        "kafka.start_failed",
                        {"workflow_id": workflow_id, "error": str(exc)},
                    )

    async def stop_all(self) -> None:
        self._stopping_all = True
        # An in-progress restart can be waiting for config before it registers
        # a task. Include lifecycle owners so shutdown cannot miss that work.
        for workflow_id in list(self._tasks.keys() | self._lifecycle_locks.keys()):
            await self.stop_workflow(workflow_id)

    async def _cleanup_runtime_resources(self, workflow_id: str) -> None:
        abort = self._abort_events.get(workflow_id)
        if abort is not None:
            abort.set()
        cancel_event = self._generation_cancel_events.pop(workflow_id, None)
        if cancel_event is not None:
            cancel_event.set()

        # Let workers drain the current ``to_thread`` workflow after the
        # cooperative cancellation signal. Pending workers stay tracked.
        pool = self._worker_pools.pop(workflow_id, None)
        if pool:
            _, pending = await asyncio.wait(pool, timeout=_WORKER_DRAIN_TIMEOUT_S)
            self._track_draining_workers(workflow_id, pending)

        self._queues.pop(workflow_id, None)
        self._abort_events.pop(workflow_id, None)
        self._ready.pop(workflow_id, None)

    def _track_draining_workers(
        self,
        workflow_id: str,
        workers: set[asyncio.Task],
    ) -> None:
        if not workers:
            return
        bucket = self._draining_workers.setdefault(workflow_id, set())
        bucket.update(workers)

        def _discard(done: asyncio.Task) -> None:
            current = self._draining_workers.get(workflow_id)
            if current is None:
                return
            current.discard(done)
            if not current:
                self._draining_workers.pop(workflow_id, None)

        for worker in workers:
            worker.add_done_callback(_discard)

    def _active_draining_workers(self, workflow_id: str) -> set[asyncio.Task]:
        workers = self._draining_workers.get(workflow_id, set())
        active = {worker for worker in workers if not worker.done()}
        if active:
            self._draining_workers[workflow_id] = active
        else:
            self._draining_workers.pop(workflow_id, None)
        return active

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
        async with self._lifecycle_locks.setdefault(workflow_id, asyncio.Lock()):
            await self._stop_workflow_unlocked(workflow_id)

    async def _stop_workflow_unlocked(self, workflow_id: str) -> None:
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

    async def restart_workflow(
        self, workflow_id: str, *, startup: bool = False,
    ) -> Dict[str, Any]:
        async with self._lifecycle_locks.setdefault(workflow_id, asyncio.Lock()):
            if self._stopping_all:
                return {"state": "stopped", "error": None}
            return await self._restart_workflow_unlocked(workflow_id, startup=startup)

    async def _restart_workflow_unlocked(
        self,
        workflow_id: str,
        *,
        startup: bool = False,
    ) -> Dict[str, Any]:
        """Restart the consumer and return its post-connect runtime status.

        Blocks until the consumer connects, the connection fails, or
        ``_CONNECT_WAIT_TIMEOUT_S`` elapses, so the HTTP save endpoint can
        surface connection errors to the user.
        """
        await self._stop_workflow_unlocked(workflow_id)
        if self._active_draining_workers(workflow_id):
            error = "previous_workers_still_draining"
            self._status[workflow_id] = {"state": "failed", "error": error}
            return {"state": "failed", "error": error}
        try:
            data = await WorkflowStore.get_config(workflow_id, kind="workflow_kafka_config")
        except Exception as exc:
            log.warning("kafka.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"state": "failed", "error": str(exc)}
        if self._stopping_all or not isinstance(data, dict) or not data.get("enabled"):
            self._status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}

        # Load and cache the workflow JSON once; avoids a disk read per message.
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            err = "workflow_not_found"
            if startup:
                self._status[workflow_id] = {"state": "stopped", "error": err}
                log.info(
                    "kafka.workflow_not_found_on_start",
                    {
                        "workflow_id": workflow_id,
                        "action": "stale_config_skipped",
                    },
                )
                return {"state": "stopped", "error": err}
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.workflow_not_found", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        input_broker = str(data.get("inputBroker") or "").strip()
        input_topic = str(data.get("inputTopic") or "").strip()
        if not input_broker or not input_topic:
            err = "missing_input_broker_or_topic"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.config_incomplete", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            err = "workflow_json_missing"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.workflow_json_missing_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        trigger = self._resolve_active_trigger(workflow_json, data)
        try:
            workflow_plan = build_workflow_execution_plan(Workflow.from_dict(workflow_json))
        except Exception as exc:
            err = f"workflow_plan_failed: {exc}"
            self._status[workflow_id] = {"state": "failed", "error": err}
            log.warning("kafka.workflow_plan_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return self.get_consumer_status(workflow_id)
        group_id = str(data.get("inputGroupId") or "").strip() or f"flocks-consumer-{workflow_id}"
        configured_inputs = _strip_execution_only_comments(trigger.inputs if isinstance(trigger.inputs, dict) else {})

        queue, abort = self._create_generation(workflow_id, workflow_plan, trigger, configured_inputs, input_topic)
        ready = asyncio.Event()
        self._ready[workflow_id] = ready
        self._status[workflow_id] = {
            "state": "connecting", "error": None, "broker": input_broker,
            "topic": input_topic, "groupId": group_id,
        }
        task = asyncio.create_task(
            self._supervise_consumer(
                workflow_id, input_broker, input_topic, group_id,
                str(data.get("autoOffsetReset") or "latest"), queue, abort, ready,
                workflow_plan, trigger, configured_inputs,
            ), name=f"kafka-{workflow_id}",
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

    def _create_generation(
        self, workflow_id: str, workflow_plan: Any, trigger: TriggerDefinition,
        configured_inputs: Dict[str, Any], topic: str,
    ) -> tuple[asyncio.Queue, asyncio.Event]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_queue_size_for_trigger(trigger))
        abort = asyncio.Event()
        cancel = threading.Event()
        self._queues[workflow_id] = queue
        self._abort_events[workflow_id] = abort
        self._generation_cancel_events[workflow_id] = cancel
        self._worker_pools[workflow_id] = [asyncio.create_task(
            self._worker_loop(workflow_id, workflow_plan, trigger, configured_inputs, queue, abort, topic, cancel),
            name=f"kafka-worker-{workflow_id}-{index}",
        ) for index in range(_worker_count_for_trigger(trigger))]
        return queue, abort

    async def _supervise_consumer(
        self, workflow_id: str, broker: str, topic: str, group_id: str, reset: str,
        queue: asyncio.Queue, abort: asyncio.Event, ready: asyncio.Event,
        workflow_plan: Any, trigger: TriggerDefinition, configured_inputs: Dict[str, Any],
    ) -> None:
        """Rejoin after ownership loss, only after the old generation has drained.

        The stable supervisor is also the stop/restart cancellation boundary.
        Workflow/commit failures remain visible and do not restart endlessly.
        """
        attempt = 0
        try:
            while True:
                await self._consumer_loop(workflow_id, broker, topic, group_id, reset, queue, abort, ready)
                status = self._status.get(workflow_id, {})
                if status.get("error_code") not in _RECOVERABLE_OWNERSHIP_ERRORS:
                    return
                attempt += 1
                self._status[workflow_id] = {**status, "state": "connecting", "recoveryAttempt": attempt,
                                             "recoveryPhase": "waiting_for_workers"}
                while pending := self._active_draining_workers(workflow_id):
                    await asyncio.wait(pending, timeout=1.0)
                delay = min(_REBALANCE_RECOVERY_MAX_DELAY_S,
                            _REBALANCE_RECOVERY_DELAY_S * 2 ** min(attempt - 1, 10))
                self._status[workflow_id].update(recoveryPhase="backoff", retryAt=time.time() + delay)
                log.warning("kafka.recovery_scheduled", {"workflow_id": workflow_id,
                            "attempt": attempt, "delay_s": delay, "error_code": status.get("error_code")})
                await asyncio.sleep(delay)
                # Keep the same group and committed positions. Never seek to
                # latest or reuse an old epoch/queue to hide unfinished work.
                queue, abort = self._create_generation(workflow_id, workflow_plan, trigger, configured_inputs, topic)
                ready = asyncio.Event()
                self._ready[workflow_id] = ready
        finally:
            await self._cleanup_runtime_resources(workflow_id)
            if self._tasks.get(workflow_id) is asyncio.current_task():
                self._tasks.pop(workflow_id, None)

    def _fail_generation(
        self,
        workflow_id: str,
        abort: asyncio.Event,
        error: BaseException,
        *,
        delivery: Optional[_KafkaDelivery] = None,
    ) -> None:
        if delivery is not None:
            delivery.tracker.close()
        if self._abort_events.get(workflow_id) is abort:
            self._status[workflow_id] = {
                **self._status.get(workflow_id, {}),
                "state": "connecting" if getattr(error, "code", None) in _RECOVERABLE_OWNERSHIP_ERRORS else "failed",
                **_delivery_error_diagnostics(error),
                **(delivery.diagnostics() if delivery is not None else {}),
            }
        log.warning("kafka.generation_failed", {"workflow_id": workflow_id,
                    **_delivery_error_diagnostics(error)})
        abort.set()

    async def _retry_delivery(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        workflow_id: str,
        abort: asyncio.Event,
        cancel: threading.Event,
        phase: str,
        delivery: Optional[_KafkaDelivery],
    ) -> Any:
        for attempt in range(1, _DELIVERY_MAX_ATTEMPTS + 1):
            if abort.is_set() or cancel.is_set():
                raise _KafkaGenerationStopped()
            try:
                if phase == "dispatch":
                    return await operation()
                try:
                    return await asyncio.wait_for(operation(), timeout=_OFFSET_OPERATION_TIMEOUT_S)
                except asyncio.TimeoutError as exc:
                    raise _KafkaDeliveryError(
                        f"{phase}_timeout",
                        f"Kafka {phase} did not complete within {_OFFSET_OPERATION_TIMEOUT_S}s",
                    ) from exc
            except (asyncio.CancelledError, _KafkaGenerationStopped):
                raise
            except Exception as exc:
                log.warning(
                    "kafka.worker_dispatch_failed" if phase == "dispatch" else "kafka.offset_commit_failed",
                    {
                        "workflow_id": workflow_id,
                        "phase": phase,
                        "attempt": attempt,
                        "max_attempts": _DELIVERY_MAX_ATTEMPTS,
                        "error_code": getattr(exc, "code", f"{phase}_failed"),
                        **(delivery.diagnostics() if delivery is not None else {}),
                        **_delivery_error_diagnostics(exc),
                    },
                )
                if attempt == _DELIVERY_MAX_ATTEMPTS:
                    if isinstance(exc, _KafkaDeliveryError):
                        raise
                    raise _KafkaDeliveryError(f"{phase}_failed", str(exc)) from exc
                try:
                    await asyncio.wait_for(abort.wait(), timeout=_DELIVERY_RETRY_DELAY_S * attempt)
                except asyncio.TimeoutError:
                    pass
        raise AssertionError("unreachable")

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
            from aiokafka import AIOKafkaConsumer, ConsumerRebalanceListener, TopicPartition
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
            bootstrap_servers=broker,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset=auto_offset_reset if auto_offset_reset in ("latest", "earliest") else "latest",
            request_timeout_ms=_REQUEST_TIMEOUT_MS,
            fetch_max_bytes=_FETCH_MAX_BYTES,
            max_partition_fetch_bytes=_MAX_PARTITION_FETCH_BYTES,
            max_poll_records=_MAX_POLL_RECORDS,
        )
        tracker = _KafkaOffsetTracker(
            consumer,
            abort,
            max_pending=queue.maxsize + max(1, len(self._worker_pools.get(workflow_id, ()))),
        )
        manager = self
        cancel_event = self._generation_cancel_events.get(workflow_id) or threading.Event()

        class _RebalanceListener(ConsumerRebalanceListener):
            async def on_partitions_revoked(self, revoked: Any) -> None:
                # Do not wait for workers (or the commit lock) from this callback:
                # commit itself can be waiting for the coordinator/rebalance.
                # Uncommitted records will be replayed by their next owner.
                if tracker.revoked(revoked):
                    tracker.close()
                    manager._fail_generation(
                        workflow_id,
                        abort,
                        _KafkaDeliveryError(
                            "partition_revoked_with_pending_deliveries",
                            "Kafka partition ownership changed with unfinished deliveries; rejoining to replay",
                        ),
                    )

            async def on_partitions_assigned(self, assigned: Any) -> None:
                tracker.assigned(assigned)
                for partition in assigned:
                    epoch = tracker.epoch_for(partition)
                    try:
                        await manager._retry_delivery(
                            lambda: tracker.anchor_partition(partition, epoch),
                            workflow_id=workflow_id,
                            abort=abort,
                            cancel=cancel_event,
                            phase="anchor",
                            delivery=None,
                        )
                    except _KafkaGenerationStopped:
                        return
                    except Exception as exc:
                        # aiokafka logs and suppresses listener exceptions. Set
                        # our failed state explicitly instead of allowing fetch.
                        tracker.close()
                        manager._fail_generation(workflow_id, abort, exc)
                        return

        try:
            consumer.subscribe(topics=[topic], listener=_RebalanceListener())
            await consumer.start()
            if abort.is_set():
                raise _KafkaGenerationStopped()
        except _KafkaGenerationStopped:
            ready.set()
            await self._cleanup_runtime_resources(workflow_id)
            await consumer.stop()
            return
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

        delivery = None

        async def _fetch() -> Any:
            message = await consumer.getone()
            partition = TopicPartition(message.topic, message.partition)
            # Capture ownership in the same task immediately after getone,
            # before the message can wait for queue/acknowledgement capacity.
            return message, partition, tracker.epoch_for(partition)

        try:
            while not abort.is_set():
                delivery = None
                msg, partition, epoch = await _until_aborted(_fetch(), abort)
                raw_value = msg.value
                await _until_aborted(tracker.wait_for_slot(), abort)
                delivery = tracker.register(partition, msg.offset, epoch)
                queued = _QueuedKafkaMessage(
                    raw_value=raw_value,
                    size_bytes=len(raw_value) if raw_value is not None else 0,
                    delivery=delivery,
                )
                # Blocking put applies backpressure instead of dropping messages.
                await _until_aborted(queue.put(queued), abort)
        except _KafkaGenerationStopped:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_generation(workflow_id, abort, exc, delivery=delivery)
            log.error(
                "kafka.consumer_error",
                {"workflow_id": workflow_id, **_delivery_error_diagnostics(exc)},
            )
        finally:
            tracker.close()
            await self._cleanup_runtime_resources(workflow_id)
            try:
                await consumer.stop()
            except Exception:
                pass

    async def _worker_loop(
        self,
        workflow_id: str,
        workflow_plan: Any,
        trigger: TriggerDefinition,
        configured_inputs: Dict[str, Any],
        queue: asyncio.Queue,
        abort: asyncio.Event,
        source: str,
        generation_cancel_event: Optional[threading.Event] = None,
    ) -> None:
        run_cancel_event = (
            generation_cancel_event or self._generation_cancel_events.get(workflow_id) or threading.Event()
        )

        def _unexpected_cancel(delivery: Optional[_KafkaDelivery] = None) -> None:
            if abort.is_set() or run_cancel_event.is_set():
                return
            error = _KafkaDeliveryError("worker_cancelled", "Kafka worker cancelled without stopping its generation")
            run_cancel_event.set()
            self._fail_generation(workflow_id, abort, error, delivery=delivery)
            log.error(
                "kafka.worker_cancelled",
                {
                    "workflow_id": workflow_id,
                    **(delivery.diagnostics() if delivery is not None else {}),
                    **_delivery_error_diagnostics(error),
                },
            )

        while not abort.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                _unexpected_cancel()
                return
            delivery = msg.delivery if isinstance(msg, _QueuedKafkaMessage) else None
            try:
                if isinstance(msg, _QueuedKafkaMessage):
                    msg = _decode_message(msg.raw_value)

                async def _dispatch() -> None:
                    outcome = await self._trigger_workflow(
                        workflow_id,
                        workflow_plan,
                        msg,
                        next(iter(trigger.mapping or {}), "kafka_message"),
                        configured_inputs,
                        trigger=trigger,
                        source=source,
                        generation_cancel_event=run_cancel_event,
                        delivery_id=delivery.delivery_id if delivery is not None else None,
                        kafka_metadata=delivery.diagnostics() if delivery is not None else None,
                    )
                    if isinstance(outcome, _KafkaDispatchOutcome) and not outcome.acknowledged:
                        raise _KafkaDeliveryError(
                            "workflow_unsuccessful",
                            f"Workflow execution {outcome.exec_id} did not succeed: {outcome.error}",
                        )
                    if delivery is not None and not isinstance(outcome, _KafkaDispatchOutcome):
                        raise _KafkaDeliveryError("dispatch_not_acknowledged", "No durable workflow acknowledgement")

                await self._retry_delivery(
                    _dispatch,
                    workflow_id=workflow_id,
                    abort=abort,
                    cancel=run_cancel_event,
                    phase="dispatch",
                    delivery=delivery,
                )
                if delivery is not None:
                    await self._retry_delivery(
                        lambda: delivery.tracker.complete(delivery),
                        workflow_id=workflow_id,
                        abort=abort,
                        cancel=run_cancel_event,
                        phase="commit",
                        delivery=delivery,
                    )
            except _KafkaGenerationStopped:
                return
            except asyncio.CancelledError:
                _unexpected_cancel(delivery)
                return
            except Exception as exc:
                run_cancel_event.set()
                self._fail_generation(workflow_id, abort, exc, delivery=delivery)
                return
            finally:
                queue.task_done()

    async def _trigger_workflow(
        self,
        workflow_id: str,
        workflow_plan: Any,
        message: Any,
        input_key: str,
        configured_inputs: Optional[Dict[str, Any]] = None,
        *,
        trigger: Optional[TriggerDefinition] = None,
        source: Optional[str] = None,
        generation_cancel_event: Optional[threading.Event] = None,
        delivery_id: Optional[str] = None,
        kafka_metadata: Optional[Dict[str, Any]] = None,
    ) -> _KafkaDispatchOutcome:
        run_cancel_event = generation_cancel_event or threading.Event()
        trigger = trigger or TriggerDefinition.model_validate(
            {
                "id": "kafka-default",
                "type": "kafka",
                "enabled": True,
                "mapping": {input_key: "$.body"},
                "inputs": _strip_execution_only_comments(
                    configured_inputs if isinstance(configured_inputs, dict) else {}
                ),
            }
        )
        configured_inputs = _strip_execution_only_comments(
            configured_inputs if isinstance(configured_inputs, dict) else {}
        )
        tool_context_required = _configured_bool(
            configured_inputs.get("tool_context_required"),
            default=True,
        )
        event = build_trigger_event(
            workflow_id=workflow_id,
            trigger=trigger,
            body=message,
            raw=message,
            source=source or str((trigger.source or {}).get("inputTopic") or "kafka"),
            delivery_id=delivery_id or f"kafka-{uuid.uuid4().hex}",
            headers={"kafka": kafka_metadata} if kafka_metadata is not None else None,
        )

        async def _executor(mapped_inputs: Dict[str, Any]) -> Dict[str, Any]:
            summarized_inputs = {"_trigger": trigger.type}
            for key, value in mapped_inputs.items():
                summarized_inputs[key] = _summarize_large_value(value)

            exec_data = await create_execution_record(
                workflow_id,
                input_params=summarized_inputs,
            )
            exec_id = exec_data["id"]
            start_time = time.time()
            trigger_meta = mapped_inputs.get("_flocks", {}).get("trigger", {})
            trigger_input_keys = list((trigger.mapping or {}).keys()) or [input_key]
            step_recorder = ExecutionStepRecorder(
                step_compactor=lambda step: _compact_step_for_kafka_storage(
                    step,
                    input_key=input_key,
                    input_keys=trigger_input_keys,
                ),
            )
            tool_context = None
            try:
                if tool_context_required:
                    tool_context = await build_workflow_tool_context(
                        workflow_id=workflow_id,
                        action_name=f"trigger:{trigger.type}",
                    )
                result = await asyncio.to_thread(
                    run_workflow,
                    workflow=workflow_plan,
                    inputs=mapped_inputs,
                    run_id=exec_id,
                    trace=False,
                    execution_profile="high_frequency",
                    cancel=run_cancel_event.is_set,
                    on_step_complete=step_recorder.on_step_complete,
                    tool_context=tool_context,
                )
                status, error_msg = resolve_execution_outcome(result)
                duration = time.time() - start_time
                step_count = step_recorder.step_count or result.steps
                exec_data.update(step_recorder.summary)
                exec_data.update(
                    {
                        "status": status,
                        "outputResults": _compact_for_kafka_storage(result.outputs),
                        "finishedAt": int(time.time() * 1000),
                        "duration": duration,
                        "errorMessage": error_msg,
                        "executionLog": [],
                        "stepCount": step_count,
                        "currentNodeId": result.last_node_id,
                        "currentPhase": status,
                        "currentStepIndex": step_count,
                        "triggerId": trigger.id,
                        "triggerType": trigger.type,
                        "deliveryId": trigger_meta.get("deliveryId"),
                        "attempt": trigger_meta.get("attempt"),
                        "triggerSource": trigger_meta.get("source"),
                    }
                )
            except Exception as exc:
                duration = time.time() - start_time
                log.error(
                    "kafka.workflow_run_failed",
                    {"workflow_id": workflow_id, "exec_id": exec_id, "error": str(exc)},
                )
                exec_data.update(step_recorder.summary)
                exec_data.update(
                    {
                        "status": "error",
                        "errorMessage": str(exc),
                        "finishedAt": int(time.time() * 1000),
                        "duration": duration,
                        "executionLog": [],
                        "currentPhase": "error",
                        "triggerId": trigger.id,
                        "triggerType": trigger.type,
                        "deliveryId": trigger_meta.get("deliveryId"),
                        "attempt": trigger_meta.get("attempt"),
                        "triggerSource": trigger_meta.get("source"),
                    }
                )
            finally:
                steps = step_recorder.take_steps()
                if tool_context is not None:
                    await cleanup_workflow_tool_context(tool_context)
                try:
                    await record_execution_result(
                        workflow_id,
                        exec_id,
                        exec_data,
                        steps=steps,
                    )
                except Exception as exc:
                    log.warning(
                        "kafka.exec_record_failed",
                        {"workflow_id": workflow_id, "exec_id": exec_id, **_delivery_error_diagnostics(exc)},
                    )
                    raise _KafkaDeliveryError("execution_record_failed", str(exc)) from exc
            return exec_data

        try:
            action_payload = {
                "operation": "workflow.trigger.kafka",
                "transport": "headless",
                # Internal adapter contract; never derive permission-carrier
                # fields from the untrusted message body.
                "legacy_compat": True,
                "workflow_id": workflow_id,
                "trigger": trigger,
                "event": event,
                "metadata": {
                    "legacy_compat": True,
                    "trigger_type": "kafka",
                },
            }
            dispatched = await execute_with_hooks(
                action_payload,
                lambda: self._dispatcher.dispatch(
                    trigger=trigger,
                    event=event,
                    executor=_executor,
                ),
                before=HookPipeline.run_ingress_before,
                after=HookPipeline.run_ingress_after,
            )
            if dispatched.get("matched") is False and dispatched.get("executed") is False:
                # A valid filter deliberately discarded this message. Filter
                # expression errors raise TriggerDispatchError instead.
                return _KafkaDispatchOutcome(acknowledged=True)
            result = dispatched.get("result") or {}
            return _KafkaDispatchOutcome(
                acknowledged=result.get("status") == "success" and not run_cancel_event.is_set(),
                exec_id=result.get("id"),
                error=result.get("errorMessage") or result.get("status"),
            )
        except TriggerDispatchError as exc:
            log.warning(
                "kafka.trigger_dispatch_failed",
                {"workflow_id": workflow_id, "trigger_id": trigger.id, "error": str(exc)},
            )
            raise


default_manager = KafkaManager()
