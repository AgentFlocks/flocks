"""Lifecycle manager for syslog listeners → workflow runs."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

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
# Maximum time we wait for the listener to either bind successfully or fail
# during ``restart_workflow``.  Any value <0.5s makes the call too aggressive
# under busy event-loops; anything >5s would make the HTTP save endpoint feel
# hung when the user makes a typo.
_BIND_WAIT_TIMEOUT_S = 3.0


class SyslogManager:
    """One async listener task per workflow id (when enabled).

    The listener / consumer fan-out is built around bounded primitives so a
    syslog flood cannot translate into unbounded asyncio.Task growth:

    * A bounded ``asyncio.Queue`` (``_MAX_QUEUE_SIZE``) absorbs spikes; the
      listener uses ``put_nowait`` and drops excess messages with a warning.
    * A fixed pool of ``_MAX_CONCURRENT_EXECUTIONS`` worker coroutines drains
      the queue and runs ``_trigger_workflow`` serially per worker.  This is
      stronger than a per-task ``Semaphore``: the previous design called
      ``create_task`` for every queued message and only awaited the semaphore
      *inside* the task, which let pending coroutines accumulate without
      bound while the queue was emptied immediately.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        # Per-workflow bounded message queue for backpressure
        self._queues: dict[str, asyncio.Queue] = {}
        # Per-workflow fixed worker pool draining the queue
        self._worker_pools: dict[str, List[asyncio.Task]] = {}
        # Per-workflow listener runtime status for the syslog-status API.
        # Possible state values: "binding" | "listening" | "failed" | "stopped".
        self._listener_status: dict[str, Dict[str, Any]] = {}
        # Per-workflow event signalled when the listener has either bound
        # successfully or failed.  Used by ``restart_workflow`` so the HTTP
        # save endpoint can report bind failures synchronously.
        self._listener_ready: dict[str, asyncio.Event] = {}

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

    def get_listener_status(self, workflow_id: str) -> Dict[str, Any]:
        """Return a snapshot of the listener runtime state for ``workflow_id``.

        Result shape::

            {"state": "binding|listening|failed|stopped", "error": "..." | None,
             "host": "...", "port": 5140, "protocol": "udp|tcp",
             "queueSize": 12, "queueCapacity": 200, "workerCount": 8}
        """
        status = dict(self._listener_status.get(workflow_id) or {"state": "stopped"})
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
        # Cancel all worker pool tasks; pop first so callers observing a
        # stopped listener see an empty pool immediately.
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
        self._queues.pop(workflow_id, None)
        self._listener_ready.pop(workflow_id, None)
        if workflow_id in self._listener_status:
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}

    async def restart_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Restart the listener and return its post-bind runtime status.

        This call blocks until the underlying socket either binds successfully,
        the bind fails (OSError such as ``EADDRINUSE``), or
        ``_BIND_WAIT_TIMEOUT_S`` elapses.  Callers (e.g. the HTTP
        ``save_syslog_config`` route) can therefore surface bind errors to the
        user instead of silently leaving the listener in a failed state.
        """
        await self.stop_workflow(workflow_id)
        key = self._config_key(workflow_id)
        try:
            data = await Storage.read(key)
        except Exception as exc:
            log.warning("syslog.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"state": "failed", "error": str(exc)}
        if not isinstance(data, dict) or not data.get("enabled"):
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}

        # Load and cache the workflow JSON once; avoids a disk read per message
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            err = "workflow_not_found"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_not_found_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}
        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            err = "workflow_json_missing"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_json_missing_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._queues[workflow_id] = queue

        abort = asyncio.Event()
        self._abort_events[workflow_id] = abort

        ready = asyncio.Event()
        self._listener_ready[workflow_id] = ready

        host = str(data.get("host") or "0.0.0.0")
        port = int(data.get("port") or 5140)
        protocol = str(data.get("protocol") or "udp").lower()
        self._listener_status[workflow_id] = {
            "state": "binding",
            "error": None,
            "host": host,
            "port": port,
            "protocol": protocol,
        }

        input_key = str(data.get("inputKey") or "syslog_message")

        # Spin up a fixed worker pool: exactly _MAX_CONCURRENT_EXECUTIONS
        # coroutines drain the queue.  pending tasks cannot exceed this number,
        # which is the actual backpressure invariant we want.
        workers: List[asyncio.Task] = []
        for i in range(_MAX_CONCURRENT_EXECUTIONS):
            workers.append(
                asyncio.create_task(
                    self._worker_loop(workflow_id, workflow_json, input_key, queue, abort),
                    name=f"syslog-worker-{workflow_id}-{i}",
                )
            )
        self._worker_pools[workflow_id] = workers

        task = asyncio.create_task(
            self._listener_loop(workflow_id, data, queue, abort, ready),
            name=f"syslog-{workflow_id}",
        )
        self._tasks[workflow_id] = task

        # Wait briefly for the listener to bind (or fail) so the caller can
        # decide whether to surface a 502/Conflict instead of pretending the
        # listener is up.
        try:
            await asyncio.wait_for(ready.wait(), timeout=_BIND_WAIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            # Listener hasn't reported bind result; treat as best-effort
            # "scheduled" so we don't tear it down on slow boxes, but mark the
            # state explicitly so the UI can show "pending".
            current = self._listener_status.get(workflow_id) or {}
            if current.get("state") == "binding":
                self._listener_status[workflow_id] = {
                    **current,
                    "state": "binding",
                    "error": "bind_pending_timeout",
                }
            log.warning("syslog.bind_pending_timeout", {"workflow_id": workflow_id})

        log.info("syslog.listener_scheduled", {"workflow_id": workflow_id})
        return self.get_listener_status(workflow_id)

    async def _listener_loop(
        self,
        workflow_id: str,
        config: Dict[str, Any],
        queue: asyncio.Queue,
        abort: asyncio.Event,
        ready: asyncio.Event,
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

        async def _bind_and_serve() -> None:
            """Bind the socket synchronously then mark the listener ready.

            ``run_udp_syslog_server`` / ``run_tcp_syslog_server`` create the
            endpoint at the top of their body and then await abort; we wrap
            them with a tiny helper so we can flip the ``ready`` flag
            *after* the bind has succeeded.  Bind failures are caught by the
            outer ``try`` below and reported back as ``state="failed"``.
            """
            # We rely on the underlying asyncio APIs raising OSError before
            # they yield control, so wrapping the call alone is enough.  We
            # additionally schedule a single-shot "mark ready" task that
            # runs on the next event-loop tick — by which point the bind has
            # either succeeded or raised.
            mark_task = asyncio.create_task(_mark_ready_after_bind())
            try:
                if protocol == "tcp":
                    await run_tcp_syslog_server(host, port, format_hint, on_msg, abort_event=abort)
                else:
                    await run_udp_syslog_server(host, port, format_hint, on_msg, abort_event=abort)
            finally:
                if not mark_task.done():
                    mark_task.cancel()

        async def _mark_ready_after_bind() -> None:
            # Give the bind one event-loop tick to complete (or raise) so we
            # don't claim "listening" before the socket actually exists.
            await asyncio.sleep(0)
            if not ready.is_set():
                self._listener_status[workflow_id] = {
                    "state": "listening",
                    "error": None,
                    "host": host,
                    "port": port,
                    "protocol": protocol,
                }
                ready.set()

        try:
            await _bind_and_serve()
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            self._listener_status[workflow_id] = {
                "state": "failed",
                "error": str(exc),
                "host": host,
                "port": port,
                "protocol": protocol,
            }
            ready.set()
            log.error(
                "syslog.bind_failed",
                {"workflow_id": workflow_id, "error": str(exc), "host": host, "port": port, "protocol": protocol},
            )
        except Exception as exc:
            self._listener_status[workflow_id] = {
                "state": "failed",
                "error": str(exc),
                "host": host,
                "port": port,
                "protocol": protocol,
            }
            ready.set()
            log.error("syslog.listener_error", {"workflow_id": workflow_id, "error": str(exc)})

    async def _worker_loop(
        self,
        workflow_id: str,
        workflow_json: Any,
        input_key: str,
        queue: asyncio.Queue,
        abort: asyncio.Event,
    ) -> None:
        """One worker drains the queue serially.

        The worker pool size is the *only* concurrency knob; we deliberately
        do not spawn additional asyncio.Tasks per message so the total number
        of in-flight workflow runs is exactly ``_MAX_CONCURRENT_EXECUTIONS``.
        """
        while not abort.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            try:
                await self._trigger_workflow(workflow_id, workflow_json, msg, input_key)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning(
                    "syslog.worker_dispatch_failed",
                    {"workflow_id": workflow_id, "error": str(exc)},
                )

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
