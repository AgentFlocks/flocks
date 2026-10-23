"""Lifecycle manager for syslog listeners → workflow runs."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from flocks.coordination import ClusterSingleton, wait_for_singleton
from flocks.hooks.execution import execute_with_hooks
from flocks.hooks.pipeline import HookPipeline
from flocks.utils.log import Log
from flocks.workflow.execution_store import (
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
from flocks.workflow.ownership import abandon_execution, execution_cancelled
from flocks.workflow.tool_context import (
    build_workflow_tool_context,
    cleanup_workflow_tool_context,
)

from flocks.ingest.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.ingest.syslog.listener import run_tcp_syslog_server, run_udp_syslog_server
from flocks.workflow.triggers.compat import legacy_syslog_trigger_from_config
from flocks.workflow.triggers.dispatcher import EventDispatcher, TriggerDispatchError, build_trigger_event
from flocks.workflow.triggers.models import (
    TriggerDefinition,
    workflow_json_declares_triggers,
    workflow_trigger_definitions_from_json,
)

log = Log.create(service="syslog.manager")


# Maximum concurrent workflow executions per workflow to avoid FD exhaustion and database contention
_MAX_CONCURRENT_EXECUTIONS = 8
# Maximum number of buffered syslog messages per workflow; excess messages are dropped with a warning.
# Increased from 200 to 1000 to absorb larger inbound bursts before the worker pool catches up.
_MAX_QUEUE_SIZE = 1000
# Maximum time we wait for the listener to either bind successfully or fail
# during ``restart_workflow``.  Any value <0.5s makes the call too aggressive
# under busy event-loops; anything >5s would make the HTTP save endpoint feel
# hung when the user makes a typo.
_BIND_WAIT_TIMEOUT_S = 3.0
# How often a listener confirms it still holds its cluster lease, and how often
# a standby instance asks for it.
_LEASE_CHECK_INTERVAL_S = 5.0
# Minimum interval between two ``syslog.queue_full_dropped`` warnings; a
# sustained queue overflow is aggregated into a single warning per window.
_DROP_LOG_WINDOW_S = 1.0


def _worker_count_for_trigger(trigger: TriggerDefinition) -> int:
    return min(_MAX_CONCURRENT_EXECUTIONS, max(1, int(trigger.concurrency.maxParallel)))


def _queue_size_for_trigger(trigger: TriggerDefinition) -> int:
    return min(_MAX_QUEUE_SIZE, max(1, int(trigger.concurrency.queueSize)))


class _DropWarningThrottle:
    """Aggregate per-workflow ``QueueFull`` drops into windowed warnings.

    The listener's ``on_msg`` callback runs synchronously from the UDP
    protocol layer; without throttling, each dropped datagram emits its own
    log record, which (a) drowns out the surrounding logs and (b) can make
    the very logging itself a bottleneck.  This helper keeps a small running
    tally and emits at most one warning per ``window_s`` seconds, plus an
    explicit ``flush`` for the trailing count when the flood stops.
    """

    def __init__(
        self,
        workflow_id: str,
        queue: asyncio.Queue,
        window_s: float = _DROP_LOG_WINDOW_S,
    ) -> None:
        self._workflow_id = workflow_id
        self._queue = queue
        self._window_s = float(window_s)
        self._count: int = 0
        self._last_log: float = 0.0

    @property
    def count(self) -> int:
        """Current un-flushed drop count (useful for tests)."""
        return self._count

    def record_drop(self) -> None:
        """Account for one dropped datagram; emit if the window elapsed."""
        self._count += 1
        if time.monotonic() - self._last_log >= self._window_s:
            self._flush(trigger="threshold")

    def maybe_flush(self) -> None:
        """Emit a warning if the trailing count has waited long enough."""
        if self._count > 0 and time.monotonic() - self._last_log >= self._window_s:
            self._flush(trigger="flush")

    def flush_remaining(self, trigger: str = "shutdown") -> None:
        """Emit any leftover count regardless of window; used on shutdown."""
        if self._count > 0:
            self._flush(trigger=trigger)

    def _flush(self, *, trigger: str) -> None:
        log.warning(
            "syslog.queue_full_dropped",
            {
                "workflow_id": self._workflow_id,
                "queue_size": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "dropped_in_window": int(self._count),
                "trigger": trigger,
            },
        )
        self._count = 0
        self._last_log = time.monotonic()


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
        # One cancellation event per listener generation. It is installed
        # before any worker starts, so stop cannot miss a run that is still
        # creating its execution record or tool context.
        self._generation_cancel_events: dict[str, threading.Event] = {}
        self._draining_workers: dict[str, set[asyncio.Task]] = {}
        # Per-workflow listener runtime status for the syslog-status API.
        # Possible state values: "binding" | "listening" | "failed" | "stopped".
        self._listener_status: dict[str, Dict[str, Any]] = {}
        # Per-workflow event signalled when the listener has either bound
        # successfully or failed.  Used by ``restart_workflow`` so the HTTP
        # save endpoint can report bind failures synchronously.
        self._listener_ready: dict[str, asyncio.Event] = {}
        self._dispatcher = EventDispatcher()
        # One listener per workflow across every instance on the database:
        # the holder of its cluster lease listens, the others stand by and
        # take over when the holder goes away ("standby" state).
        self._leases: dict[str, ClusterSingleton] = {}
        self._lease_tasks: dict[str, asyncio.Task] = {}
        self._lease_stops: dict[str, asyncio.Event] = {}
        # Restart, stop and lease changes of one workflow happen one at a
        # time: two of them interleaving would leave a lease, a watcher or a
        # listener that nothing tracks any more.
        self._workflow_locks: dict[str, asyncio.Lock] = {}
        self._stopped = False

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        lock = self._workflow_locks.get(workflow_id)
        if lock is None:
            lock = self._workflow_locks[workflow_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_SYSLOG_CONFIG_PREFIX}{workflow_id}"

    @staticmethod
    def _default_trigger_from_config(data: Dict[str, Any]) -> TriggerDefinition:
        trigger = legacy_syslog_trigger_from_config(data)
        if trigger is None:
            return TriggerDefinition.model_validate(
                {
                    "id": "syslog-default",
                    "type": "syslog",
                    "enabled": bool(data.get("enabled")),
                    "source": {
                        "protocol": data.get("protocol") or "udp",
                        "host": data.get("host") or "0.0.0.0",
                        "port": int(data.get("port") or 5140),
                        "format": data.get("format") or "auto",
                    },
                    "mapping": {
                        str(data.get("inputKey") or "syslog_message"): "$.body",
                    },
                    "updatedAt": data.get("updatedAt"),
                }
            )
        return trigger

    def _resolve_active_trigger(self, workflow_json: Dict[str, Any], data: Dict[str, Any]) -> TriggerDefinition:
        if workflow_json_declares_triggers(workflow_json):
            triggers = workflow_trigger_definitions_from_json(workflow_json)
            trigger = next((item for item in triggers if item.type == "syslog"), None)
            if trigger is not None:
                return trigger
        return self._default_trigger_from_config(data)

    async def start_all(self) -> None:
        self._stopped = False
        try:
            configs = await WorkflowStore.list_configs(kind="workflow_syslog_config")
        except Exception as exc:
            log.warning("syslog.list_configs_failed", {"error": str(exc)})
            return

        for workflow_id, data in configs:
            if not workflow_id:
                continue
            if isinstance(data, dict) and data.get("enabled"):
                try:
                    await self.restart_workflow(workflow_id, startup=True)
                except Exception as exc:
                    self._listener_status[workflow_id] = {
                        "state": "failed",
                        "error": str(exc),
                    }
                    log.warning(
                        "syslog.start_failed",
                        {"workflow_id": workflow_id, "error": str(exc)},
                    )

    async def stop_all(self) -> None:
        # Standing-by workflows have no listener task, only a lease watcher:
        # they are stopped too, or they would take over after shutdown.  A
        # restart still under way (reading its config, say) has registered
        # nothing yet: every workflow that ever took its lock is visited, so
        # stopping waits for that restart, which then sees ``_stopped`` and
        # starts nothing (see _restart_locked).
        self._stopped = True
        workflows = (
            set(self._tasks) | set(self._leases) | set(self._lease_tasks) | set(self._lease_stops)
            | set(self._workflow_locks)
        )
        for workflow_id in sorted(workflows):
            await self.stop_workflow(workflow_id)

    def get_listener_status(self, workflow_id: str) -> Dict[str, Any]:
        """Return a snapshot of the listener runtime state for ``workflow_id``.

        Result shape::

            {"state": "binding|listening|failed|stopped", "error": "..." | None,
             "host": "...", "port": 5140, "protocol": "udp|tcp",
             "queueSize": 12, "queueCapacity": <queue.maxsize>,
             "workerCount": <_MAX_CONCURRENT_EXECUTIONS>}

        ``queueCapacity`` always mirrors the runtime ``asyncio.Queue.maxsize``
        of the active listener (currently ``_MAX_QUEUE_SIZE``).
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

    async def stop_workflow(self, workflow_id: str) -> None:
        async with self._lock_for(workflow_id):
            await self._stop_workflow_locked(workflow_id)

    async def _stop_workflow_locked(self, workflow_id: str) -> None:
        await self._stop_lease(workflow_id)
        await self._stop_listener(workflow_id)

    async def _stop_lease(self, workflow_id: str) -> None:
        stop = self._lease_stops.pop(workflow_id, None)
        if stop is not None:
            stop.set()
        task = self._lease_tasks.pop(workflow_id, None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        lease = self._leases.pop(workflow_id, None)
        if lease is not None:
            await lease.release()

    def _watch_lease(self, workflow_id: str, lease: ClusterSingleton, *, holding: bool) -> None:
        """Keep checking a held lease, or wait to take one another instance holds."""
        stop = asyncio.Event()
        self._lease_stops[workflow_id] = stop

        async def _hold() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_LEASE_CHECK_INTERVAL_S)
                    return
                except asyncio.TimeoutError:
                    pass
                if not await lease.check():
                    # The lease went with its connection; another instance may
                    # already be listening, so this one must stop at once.
                    log.warning("syslog.lease_lost", {"workflow_id": workflow_id})
                    async with self._lock_for(workflow_id):
                        if stop.is_set() or self._leases.get(workflow_id) is not lease:
                            return  # stopped or restarted meanwhile
                        if self._lease_tasks.get(workflow_id) is asyncio.current_task():
                            self._lease_tasks.pop(workflow_id, None)
                        await self._stop_listener(workflow_id)
                        self._listener_status[workflow_id] = {"state": "standby", "error": "lease_lost"}
                        self._watch_lease(workflow_id, lease, holding=False)
                    return

        async def _await_turn() -> None:
            if await wait_for_singleton(lease, retry_s=_LEASE_CHECK_INTERVAL_S, stop=stop):
                # Ours now: start listening from a clean restart -- unless the
                # workflow was stopped or restarted while this waited.
                await lease.release()
                async with self._lock_for(workflow_id):
                    if stop.is_set() or self._stopped or self._leases.get(workflow_id) is not lease:
                        return
                    if self._lease_tasks.get(workflow_id) is asyncio.current_task():
                        self._lease_tasks.pop(workflow_id, None)
                    await self._restart_locked(workflow_id)

        self._lease_tasks[workflow_id] = asyncio.create_task(
            _hold() if holding else _await_turn(), name=f"syslog-lease-{workflow_id}",
        )

    async def _stop_listener(self, workflow_id: str) -> None:
        cancel_event = self._generation_cancel_events.pop(workflow_id, None)
        if cancel_event is not None:
            cancel_event.set()
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
        # Let workers drain their current ``to_thread`` workflow after the
        # cooperative cancel signal. Pending workers stay tracked instead of
        # becoming invisible orphan threads.
        pool = self._worker_pools.pop(workflow_id, None)
        if pool:
            _, pending = await asyncio.wait(pool, timeout=5.0)
            self._track_draining_workers(workflow_id, pending)
        self._queues.pop(workflow_id, None)
        self._listener_ready.pop(workflow_id, None)
        if workflow_id in self._listener_status:
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}

    async def restart_workflow(
        self,
        workflow_id: str,
        *,
        startup: bool = False,
    ) -> Dict[str, Any]:
        """Restart the listener and return its post-bind runtime status.

        This call blocks until the underlying socket either binds successfully,
        the bind fails (OSError such as ``EADDRINUSE``), or
        ``_BIND_WAIT_TIMEOUT_S`` elapses.  Callers (e.g. the HTTP
        ``save_syslog_config`` route) can therefore surface bind errors to the
        user instead of silently leaving the listener in a failed state.
        """
        async with self._lock_for(workflow_id):
            return await self._restart_locked(workflow_id, startup=startup)

    async def _restart_locked(self, workflow_id: str, *, startup: bool = False) -> Dict[str, Any]:
        await self._stop_workflow_locked(workflow_id)
        if self._active_draining_workers(workflow_id):
            error = "previous_workers_still_draining"
            self._listener_status[workflow_id] = {"state": "failed", "error": error}
            return {"state": "failed", "error": error}
        try:
            data = await WorkflowStore.get_config(workflow_id, kind="workflow_syslog_config")
        except Exception as exc:
            log.warning("syslog.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"state": "failed", "error": str(exc)}
        if self._stopped:
            # The manager was stopped while the config was read.
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}
        if not isinstance(data, dict) or not data.get("enabled"):
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}

        # Load and cache the workflow JSON once; avoids a disk read per message
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            err = "workflow_not_found"
            if startup:
                self._listener_status[workflow_id] = {"state": "stopped", "error": err}
                log.info(
                    "syslog.workflow_not_found_on_start",
                    {
                        "workflow_id": workflow_id,
                        "action": "stale_config_skipped",
                    },
                )
                return {"state": "stopped", "error": err}
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_not_found", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}
        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            err = "workflow_json_missing"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_json_missing_on_start", {"workflow_id": workflow_id})
            return {"state": "failed", "error": err}

        trigger = self._resolve_active_trigger(workflow_json, data)
        try:
            workflow_plan = build_workflow_execution_plan(Workflow.from_dict(workflow_json))
        except Exception as exc:
            err = f"workflow_plan_failed: {exc}"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_plan_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return self.get_listener_status(workflow_id)

        host = str(data.get("host") or "0.0.0.0")
        port = int(data.get("port") or 5140)
        protocol = str(data.get("protocol") or "udp").lower()

        lease = ClusterSingleton(f"syslog-listener:{workflow_id}")
        try:
            holding = await lease.try_acquire()
        except Exception as exc:
            err = f"lease_unavailable: {type(exc).__name__}"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.lease_unavailable", {"workflow_id": workflow_id, "error": str(exc)})
            return self.get_listener_status(workflow_id)
        if self._stopped:
            # Stopped while the lease was being taken: hand it back, start nothing.
            if holding:
                await lease.release()
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}
        self._leases[workflow_id] = lease
        if not holding:
            # Another instance listens for this workflow; stand by.
            self._listener_status[workflow_id] = {
                "state": "standby", "error": None, "host": host, "port": port, "protocol": protocol,
            }
            self._watch_lease(workflow_id, lease, holding=False)
            log.info("syslog.listener_standby", {"workflow_id": workflow_id})
            return self.get_listener_status(workflow_id)
        self._watch_lease(workflow_id, lease, holding=True)

        queue_capacity = _queue_size_for_trigger(trigger)
        worker_count = _worker_count_for_trigger(trigger)
        queue: asyncio.Queue = asyncio.Queue(maxsize=queue_capacity)
        self._queues[workflow_id] = queue

        abort = asyncio.Event()
        self._abort_events[workflow_id] = abort
        generation_cancel_event = threading.Event()
        self._generation_cancel_events[workflow_id] = generation_cancel_event

        ready = asyncio.Event()
        self._listener_ready[workflow_id] = ready

        self._listener_status[workflow_id] = {
            "state": "binding",
            "error": None,
            "host": host,
            "port": port,
            "protocol": protocol,
        }

        # Spin up the trigger-configured worker pool within service safety caps.
        workers: List[asyncio.Task] = []
        for i in range(worker_count):
            workers.append(
                asyncio.create_task(
                    self._worker_loop(
                        workflow_id,
                        workflow_plan,
                        trigger,
                        queue,
                        abort,
                        generation_cancel_event,
                    ),
                    name=f"syslog-worker-{workflow_id}-{i}",
                )
            )
        self._worker_pools[workflow_id] = workers

        task = asyncio.create_task(
            self._listener_loop(workflow_id, data, queue, abort, ready),
            name=f"syslog-{workflow_id}",
        )
        self._tasks[workflow_id] = task
        task.add_done_callback(lambda done, wf=workflow_id: self._on_listener_exit(wf, done))

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

        status = self.get_listener_status(workflow_id)
        if status.get("state") == "failed":
            # Holding the lease without listening would keep every other
            # instance standing by for nothing: let one of them try.
            await self._stop_lease(workflow_id)
            return status
        log.info("syslog.listener_scheduled", {"workflow_id": workflow_id})
        return status

    def _on_listener_exit(self, workflow_id: str, task: asyncio.Task) -> None:
        """A listener that ended on its own -- a bind that failed late, a crash --
        gives its lease back, so another instance can listen instead."""
        if task.cancelled() or self._tasks.get(workflow_id) is not task:
            return  # stopped on purpose, or already replaced
        asyncio.ensure_future(self._release_after_exit(workflow_id, task))

    async def _release_after_exit(self, workflow_id: str, task: asyncio.Task) -> None:
        async with self._lock_for(workflow_id):
            if self._tasks.get(workflow_id) is not task:
                return
            status = dict(self._listener_status.get(workflow_id) or {})
            await self._stop_lease(workflow_id)
            await self._stop_listener(workflow_id)
            if status.get("state") != "failed":
                status = {**status, "state": "failed", "error": status.get("error") or "listener_exited"}
            self._listener_status[workflow_id] = status
            log.warning("syslog.listener_exited", {"workflow_id": workflow_id, "error": status.get("error")})

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

        # Aggregate per-window drop warnings so a sustained queue overflow
        # does not turn into its own log flood.  The trailing count is
        # flushed by a 1-second polling task plus a shutdown best-effort.
        throttle = _DropWarningThrottle(workflow_id, queue)

        # NOTE: keep this callback synchronous so the UDP protocol layer can
        # invoke it inline from datagram_received() without creating an
        # asyncio task per packet. That preserves the queue-based backpressure.
        def on_msg(parsed: dict) -> None:
            try:
                queue.put_nowait(parsed)
            except asyncio.QueueFull:
                throttle.record_drop()

        async def _periodic_drop_flush() -> None:
            """Flush leftover drop count when the flood stops."""
            while not abort.is_set():
                try:
                    await asyncio.wait_for(abort.wait(), timeout=_DROP_LOG_WINDOW_S)
                    return  # abort signalled
                except asyncio.TimeoutError:
                    pass
                throttle.maybe_flush()

        async def _bind_and_serve() -> None:
            """Serve the socket and flip ``ready`` once the bind really happened.

            ``run_udp_syslog_server`` / ``run_tcp_syslog_server`` invoke
            ``on_bound`` right after the endpoint exists.  Guessing with "one
            event-loop tick" was wrong whenever the loop had to resolve the
            host first (hostnames, malformed literals such as ``999.1.1.1``):
            the bind result arrived later than the tick and the route
            reported ``listening`` for a socket that then failed.  Bind
            failures are caught by the outer ``try`` below and reported back
            as ``state="failed"``.
            """
            flush_task = asyncio.create_task(_periodic_drop_flush())
            try:
                if protocol == "tcp":
                    await run_tcp_syslog_server(
                        host, port, format_hint, on_msg, abort_event=abort, on_bound=_mark_listening
                    )
                else:
                    await run_udp_syslog_server(
                        host, port, format_hint, on_msg, abort_event=abort, on_bound=_mark_listening
                    )
            finally:
                if not flush_task.done():
                    flush_task.cancel()
                # Best-effort: emit any leftover drop count on shutdown so a
                # tail of dropped messages isn't silently lost.
                throttle.flush_remaining()

        def _mark_listening() -> None:
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
        workflow_plan: Any,
        trigger: TriggerDefinition,
        queue: asyncio.Queue,
        abort: asyncio.Event,
        generation_cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """One worker drains the queue serially.

        The worker pool size is the *only* concurrency knob; we deliberately
        do not spawn additional asyncio.Tasks per message so the total number
        of in-flight workflow runs is exactly ``_MAX_CONCURRENT_EXECUTIONS``.
        """
        run_cancel_event = (
            generation_cancel_event or self._generation_cancel_events.get(workflow_id) or threading.Event()
        )
        while not abort.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            try:
                await self._trigger_workflow(
                    workflow_id,
                    workflow_plan,
                    msg,
                    next(iter(trigger.mapping or {}), "syslog_message"),
                    trigger=trigger,
                    source=f"{(trigger.source or {}).get('protocol', 'udp')}://{(trigger.source or {}).get('host', '0.0.0.0')}:{(trigger.source or {}).get('port', 5140)}",
                    generation_cancel_event=run_cancel_event,
                )
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
        workflow_plan: Any,
        syslog_msg: dict,
        input_key: str,
        *,
        trigger: Optional[TriggerDefinition] = None,
        source: Optional[str] = None,
        generation_cancel_event: Optional[threading.Event] = None,
    ) -> None:
        run_cancel_event = generation_cancel_event or threading.Event()
        trigger = trigger or TriggerDefinition.model_validate(
            {
                "id": "syslog-default",
                "type": "syslog",
                "enabled": True,
                "mapping": {input_key: "$.body"},
            }
        )
        event = build_trigger_event(
            workflow_id=workflow_id,
            trigger=trigger,
            body=syslog_msg,
            raw=syslog_msg,
            source=source or "syslog",
            delivery_id=f"syslog-{uuid.uuid4().hex}",
        )

        async def _executor(mapped_inputs: Dict[str, Any]) -> Dict[str, Any]:
            summarized_inputs = {"_trigger": trigger.type}
            summarized_inputs.update(mapped_inputs)

            exec_data = await create_execution_record(
                workflow_id,
                input_params=summarized_inputs,
            )
            exec_id = exec_data["id"]
            # Every way out of here ends the run: one whose end was not recorded (a
            # cancel while its context is cleaned up, say) is let go, not left live.
            try:
                step_recorder = ExecutionStepRecorder()
                start_time = time.time()
                trigger_meta = mapped_inputs.get("_flocks", {}).get("trigger", {})
                tool_context = None
                try:
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
                        cancel=lambda: run_cancel_event.is_set() or execution_cancelled(exec_id),
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
                            "outputResults": compact_outputs_for_storage(result.outputs),
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
                        "syslog.workflow_run_failed",
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
                    await cleanup_workflow_tool_context(tool_context)
                    try:
                        await record_execution_result(
                            workflow_id,
                            exec_id,
                            exec_data,
                            steps=steps,
                        )
                    except Exception as exc:
                        log.warning("syslog.exec_record_failed", {"exec_id": exec_id, "error": str(exc)})
                return exec_data
            finally:
                await abandon_execution(exec_id)

        try:
            action_payload = {
                "operation": "workflow.trigger.syslog",
                "transport": "headless",
                "workflow_id": workflow_id,
                "trigger": trigger,
                "event": event,
                # The listener itself is the authenticated ingress here; ingress
                # policies read the legacy-compat carrier from the top level
                # (the metadata copy feeds the action recheck), so keep both.
                "legacy_compat": True,
                "evidence": {
                    "auth_scheme": "syslog",
                    "trigger_id": trigger.id,
                    "trigger_type": trigger.type,
                },
                "metadata": {
                    "legacy_compat": True,
                    "trigger_type": "syslog",
                },
            }
            await execute_with_hooks(
                action_payload,
                lambda: self._dispatcher.dispatch(
                    trigger=trigger,
                    event=event,
                    executor=_executor,
                ),
                before=HookPipeline.run_ingress_before,
                after=HookPipeline.run_ingress_after,
            )
        except TriggerDispatchError as exc:
            log.warning(
                "syslog.trigger_dispatch_failed",
                {"workflow_id": workflow_id, "trigger_id": trigger.id, "error": str(exc)},
            )


default_manager = SyslogManager()
