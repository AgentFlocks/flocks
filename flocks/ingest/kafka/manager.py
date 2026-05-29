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
  losing Kafka messages would desync committed offsets, so we let the consumer
  pause naturally when the worker pool falls behind.
* When an output broker/topic is configured, the successful run result is
  produced back to Kafka via a per-workflow ``AIOKafkaProducer``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.execution_store import (
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
# SQLite write contention (mirrors the syslog manager).
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
        # Per-workflow output producer (only when output is configured)
        self._producers: dict[str, Any] = {}

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
        ev = self._abort_events.pop(workflow_id, None)
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
        # Cancel all worker pool tasks; pop first so callers observing a stopped
        # consumer see an empty pool immediately.
        pool = self._worker_pools.pop(workflow_id, None)
        if pool:
            for w in pool:
                if not w.done():
                    w.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pool, return_exceptions=True),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        # Stop the output producer if one was created.
        producer = self._producers.pop(workflow_id, None)
        if producer is not None:
            try:
                await producer.stop()
            except Exception:
                pass
        self._queues.pop(workflow_id, None)
        self._ready.pop(workflow_id, None)
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
        output_broker = str(data.get("outputBroker") or "").strip()
        output_topic = str(data.get("outputTopic") or "").strip()

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

        # Optionally start an output producer up-front so failures are visible.
        producer = None
        if output_broker and output_topic:
            try:
                producer = await self._create_producer(output_broker)
                self._producers[workflow_id] = producer
            except Exception as exc:
                log.warning(
                    "kafka.producer_start_failed",
                    {"workflow_id": workflow_id, "error": str(exc)},
                )

        # Fixed worker pool drains the queue (at most _MAX_CONCURRENT_EXECUTIONS
        # concurrent runs).
        workers: List[asyncio.Task] = []
        for i in range(_MAX_CONCURRENT_EXECUTIONS):
            workers.append(
                asyncio.create_task(
                    self._worker_loop(
                        workflow_id, workflow_json, input_key, queue, abort,
                        producer, output_topic,
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

        log.info("kafka.consumer_scheduled", {"workflow_id": workflow_id})
        return self.get_consumer_status(workflow_id)

    async def _create_producer(self, broker: str) -> Any:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=broker,
            request_timeout_ms=_REQUEST_TIMEOUT_MS,
        )
        await producer.start()
        return producer

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
            return

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=broker,
            group_id=group_id,
            enable_auto_commit=True,
            auto_offset_reset=auto_offset_reset if auto_offset_reset in ("latest", "earliest") else "latest",
            request_timeout_ms=_REQUEST_TIMEOUT_MS,
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
                payload = _decode_message(msg.value)
                # Blocking put applies backpressure instead of dropping messages.
                await queue.put(payload)
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
        finally:
            try:
                await consumer.stop()
            except Exception:
                pass

    async def _worker_loop(
        self,
        workflow_id: str,
        workflow_json: Any,
        input_key: str,
        queue: asyncio.Queue,
        abort: asyncio.Event,
        producer: Any,
        output_topic: str,
    ) -> None:
        while not abort.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            try:
                await self._trigger_workflow(
                    workflow_id, workflow_json, msg, input_key, producer, output_topic,
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
        producer: Any = None,
        output_topic: str = "",
    ) -> None:
        inputs = {input_key: message}

        exec_data = await create_execution_record(
            workflow_id,
            input_params={"_trigger": "kafka", **inputs},
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
            )
            status, error_msg = resolve_execution_outcome(result)
            duration = time.time() - start_time
            exec_data.update({
                "status": status,
                "outputResults": compact_outputs_for_storage(result.outputs),
                "finishedAt": int(time.time() * 1000),
                "duration": duration,
                "errorMessage": error_msg,
                "executionLog": compact_history_for_storage(result.history),
                "currentNodeId": result.last_node_id,
                "currentPhase": status,
                "currentStepIndex": result.steps,
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
            })
        finally:
            try:
                await record_execution_result(workflow_id, exec_id, exec_data)
            except Exception as exc:
                log.warning("kafka.exec_record_failed", {"exec_id": exec_id, "error": str(exc)})

        # Produce the result back to Kafka on success when configured.
        if (
            producer is not None
            and output_topic
            and result is not None
            and exec_data.get("status") == "success"
        ):
            try:
                value = json.dumps(
                    {"workflowId": workflow_id, "executionId": exec_id, "outputs": result.outputs},
                    default=str,
                ).encode("utf-8")
                await producer.send_and_wait(output_topic, value)
            except Exception as exc:
                log.warning(
                    "kafka.produce_failed",
                    {"workflow_id": workflow_id, "exec_id": exec_id, "error": str(exc)},
                )


default_manager = KafkaManager()
