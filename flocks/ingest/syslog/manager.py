"""Lifecycle manager for syslog listeners → workflow runs."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.execution_store import (
    create_execution_record,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.fs_store import read_workflow_from_fs
from flocks.workflow.runner import run_workflow

from flocks.ingest.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.ingest.syslog.listener import run_tcp_syslog_server, run_udp_syslog_server

log = Log.create(service="syslog.manager")

# Maximum concurrent workflow executions per workflow to avoid FD exhaustion and SQLite write contention
_MAX_CONCURRENT_EXECUTIONS = 8
# Maximum number of buffered syslog messages per workflow; excess messages are dropped with a warning
_MAX_QUEUE_SIZE = 200


class SyslogManager:
    """One async listener task per workflow id (when enabled)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        # Per-workflow semaphore to cap concurrent executions
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # Per-workflow bounded message queue for backpressure
        self._queues: dict[str, asyncio.Queue] = {}
        # Per-workflow queue consumer task
        self._consumer_tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_SYSLOG_CONFIG_PREFIX}{workflow_id}"

    async def start_all(self) -> None:
        try:
            keys = await Storage.list_keys(WORKFLOW_SYSLOG_CONFIG_PREFIX)
        except Exception as exc:
            log.warning("syslog.list_keys_failed", {"error": str(exc)})
            return

        for key in keys:
            if not key.startswith(WORKFLOW_SYSLOG_CONFIG_PREFIX):
                continue
            workflow_id = key[len(WORKFLOW_SYSLOG_CONFIG_PREFIX) :]
            if not workflow_id:
                continue
            try:
                data = await Storage.read(key)
            except Exception as exc:
                log.warning("syslog.config_read_failed", {"key": key, "error": str(exc)})
                continue
            if isinstance(data, dict) and data.get("enabled"):
                await self.restart_workflow(workflow_id)

    async def stop_all(self) -> None:
        for workflow_id in list(self._tasks.keys()):
            await self.stop_workflow(workflow_id)

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
        # Stop the queue consumer task
        consumer = self._consumer_tasks.pop(workflow_id, None)
        if consumer is not None and not consumer.done():
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
        self._semaphores.pop(workflow_id, None)
        self._queues.pop(workflow_id, None)

    async def restart_workflow(self, workflow_id: str) -> None:
        await self.stop_workflow(workflow_id)
        key = self._config_key(workflow_id)
        try:
            data = await Storage.read(key)
        except Exception as exc:
            log.warning("syslog.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return
        if not isinstance(data, dict) or not data.get("enabled"):
            return

        # Load and cache the workflow JSON once; avoids a disk read per message
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            log.warning("syslog.workflow_not_found_on_start", {"workflow_id": workflow_id})
            return
        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            log.warning("syslog.workflow_json_missing_on_start", {"workflow_id": workflow_id})
            return

        # Set up concurrency control resources
        self._semaphores[workflow_id] = asyncio.Semaphore(_MAX_CONCURRENT_EXECUTIONS)
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._queues[workflow_id] = queue

        abort = asyncio.Event()
        self._abort_events[workflow_id] = abort

        input_key = str(data.get("inputKey") or "syslog_message")

        # Start the consumer that drains the queue and dispatches executions under the semaphore
        consumer = asyncio.create_task(
            self._queue_consumer(workflow_id, workflow_json, input_key, queue, abort),
            name=f"syslog-consumer-{workflow_id}",
        )
        self._consumer_tasks[workflow_id] = consumer

        task = asyncio.create_task(
            self._listener_loop(workflow_id, data, queue, abort),
            name=f"syslog-{workflow_id}",
        )
        self._tasks[workflow_id] = task
        log.info("syslog.listener_scheduled", {"workflow_id": workflow_id})

    async def _listener_loop(
        self,
        workflow_id: str,
        config: Dict[str, Any],
        queue: asyncio.Queue,
        abort: asyncio.Event,
    ) -> None:
        host = str(config.get("host") or "0.0.0.0")
        port = int(config.get("port") or 5140)
        protocol = str(config.get("protocol") or "udp").lower()
        format_hint = str(config.get("format") or "auto")

        # NOTE: keep this callback synchronous so the UDP protocol layer can
        # invoke it inline from datagram_received() without creating an
        # asyncio task per packet. That preserves the queue-based backpressure.
        def on_msg(parsed: dict) -> None:
            try:
                queue.put_nowait(parsed)
            except asyncio.QueueFull:
                log.warning("syslog.queue_full_dropped", {
                    "workflow_id": workflow_id,
                    "queue_size": queue.qsize(),
                })

        try:
            if protocol == "tcp":
                await run_tcp_syslog_server(
                    host,
                    port,
                    format_hint,
                    on_msg,
                    abort_event=abort,
                )
            else:
                await run_udp_syslog_server(
                    host,
                    port,
                    format_hint,
                    on_msg,
                    abort_event=abort,
                )
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            log.error(
                "syslog.bind_failed",
                {"workflow_id": workflow_id, "error": str(exc), "host": host, "port": port, "protocol": protocol},
            )
        except Exception as exc:
            log.error("syslog.listener_error", {"workflow_id": workflow_id, "error": str(exc)})

    async def _queue_consumer(
        self,
        workflow_id: str,
        workflow_json: Any,
        input_key: str,
        queue: asyncio.Queue,
        abort: asyncio.Event,
    ) -> None:
        """Drain the message queue and dispatch executions bounded by the semaphore."""
        semaphore = self._semaphores[workflow_id]
        pending: set[asyncio.Task] = set()

        async def _dispatch(m: dict) -> None:
            async with semaphore:
                await self._trigger_workflow(workflow_id, workflow_json, m, input_key)

        try:
            while not abort.is_set():
                try:
                    # Poll with a short timeout so we can react to abort promptly
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                t = asyncio.create_task(_dispatch(msg))
                pending.add(t)
                t.add_done_callback(pending.discard)
        except asyncio.CancelledError:
            pass
        finally:
            # Best-effort drain: wait briefly for in-flight dispatches so their
            # final Storage writes complete; cancel anything still stuck so we
            # don't leak tasks on shutdown.
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=5.0,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    for t in list(pending):
                        if not t.done():
                            t.cancel()

    async def _trigger_workflow(
        self,
        workflow_id: str,
        workflow_json: Any,
        syslog_msg: dict,
        input_key: str,
    ) -> None:
        inputs = {input_key: syslog_msg}

        exec_data = await create_execution_record(
            workflow_id,
            input_params={"_trigger": "syslog", **inputs},
        )
        exec_id = exec_data["id"]
        start_time = time.time()

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
                "outputResults": result.outputs if isinstance(result.outputs, dict) else {},
                "finishedAt": int(time.time() * 1000),
                "duration": duration,
                "errorMessage": error_msg,
                "executionLog": list(result.history or []),
                "currentNodeId": result.last_node_id,
                "currentPhase": status,
                "currentStepIndex": result.steps,
            })
        except Exception as exc:
            duration = time.time() - start_time
            log.error(
                "syslog.workflow_run_failed",
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
                log.warning("syslog.exec_record_failed", {"exec_id": exec_id, "error": str(exc)})


default_manager = SyslogManager()
