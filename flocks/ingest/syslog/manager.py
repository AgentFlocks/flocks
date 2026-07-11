"""Lifecycle manager for syslog listeners → workflow runs."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List

from flocks.tool import ToolContext
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

from flocks.ingest.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.ingest.syslog.listener import run_tcp_syslog_server, run_udp_syslog_server
from flocks.workflow.triggers.compat import legacy_syslog_trigger_from_config
from flocks.workflow.triggers.dispatcher import EventDispatcher, TriggerDispatchError, build_trigger_event
from flocks.workflow.triggers.models import (
    TriggerDefinition,
    workflow_json_declares_triggers,
    workflow_trigger_definitions_from_json,
)
from flocks.workflow.triggers.security import execute_trigger_action

log = Log.create(service="syslog.manager")


# Maximum concurrent workflow executions per workflow to avoid FD exhaustion and SQLite write contention
_MAX_CONCURRENT_EXECUTIONS = 8
# Maximum number of buffered syslog messages per workflow; excess messages are dropped with a warning.
# Increased from 200 to 1000 to absorb larger inbound bursts before the worker pool catches up.
_MAX_QUEUE_SIZE = 1000
# Maximum time we wait for the listener to either bind successfully or fail
# during ``restart_workflow``.  Any value <0.5s makes the call too aggressive
# under busy event-loops; anything >5s would make the HTTP save endpoint feel
# hung when the user makes a typo.
_BIND_WAIT_TIMEOUT_S = 3.0
# Minimum interval between two ``syslog.queue_full_dropped`` warnings; a
# sustained queue overflow is aggregated into a single warning per window.
_DROP_LOG_WINDOW_S = 1.0


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
        # Per-workflow listener runtime status for the syslog-status API.
        # Possible state values: "binding" | "listening" | "failed" | "stopped".
        self._listener_status: dict[str, Dict[str, Any]] = {}
        # Per-workflow event signalled when the listener has either bound
        # successfully or failed.  Used by ``restart_workflow`` so the HTTP
        # save endpoint can report bind failures synchronously.
        self._listener_ready: dict[str, asyncio.Event] = {}
        self._dispatcher = EventDispatcher()
        self._service_accounts: dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _resolve_service_account(data: Dict[str, Any]) -> Dict[str, Any] | None:
        account = data.get("serviceAccount")
        if not isinstance(account, dict):
            return None
        subject_id = str(account.get("subjectId") or "").strip()
        if not subject_id:
            return None
        return {
            "subject_id": subject_id,
            "subject_type": str(account.get("subjectType") or "service_account"),
            "role": str(account.get("role") or "member"),
            "tenant_id": str(account.get("tenantId") or ""),
            "department": str(account.get("department") or ""),
            "scopes": account.get("scopes") if isinstance(account.get("scopes"), list) else [],
            "entry": "syslog",
            "auth_source": "service_account",
            "permission_mode": "headless_fail_closed",
            "verified": True,
        }

    @staticmethod
    def _should_enforce_service_account(data: Dict[str, Any]) -> bool:
        # Keep old configs working: only enforce when serviceAccount is explicitly configured.
        return "serviceAccount" in data

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
        try:
            configs = await WorkflowStore.list_configs(kind="workflow_syslog_config")
        except Exception as exc:
            log.warning("syslog.list_configs_failed", {"error": str(exc)})
            return

        for workflow_id, data in configs:
            if not workflow_id:
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
        self._service_accounts.pop(workflow_id, None)
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
        try:
            data = await WorkflowStore.get_config(workflow_id, kind="workflow_syslog_config")
        except Exception as exc:
            log.warning("syslog.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"state": "failed", "error": str(exc)}
        if not isinstance(data, dict) or not data.get("enabled"):
            self._listener_status[workflow_id] = {"state": "stopped", "error": None}
            return {"state": "stopped", "error": None}

        service_account = self._resolve_service_account(data)
        if service_account is None:
            if self._should_enforce_service_account(data):
                err = "service_account_required"
                self._listener_status[workflow_id] = {"state": "failed", "error": err}
                log.warning("syslog.service_account_missing", {"workflow_id": workflow_id})
                return {"state": "failed", "error": err}
            log.warning("syslog.service_account_legacy_mode", {"workflow_id": workflow_id})
        else:
            self._service_accounts[workflow_id] = service_account

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

        trigger = self._resolve_active_trigger(workflow_json, data)
        try:
            workflow_plan = build_workflow_execution_plan(Workflow.from_dict(workflow_json))
        except Exception as exc:
            err = f"workflow_plan_failed: {exc}"
            self._listener_status[workflow_id] = {"state": "failed", "error": err}
            log.warning("syslog.workflow_plan_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return self.get_listener_status(workflow_id)
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

        # Spin up a fixed worker pool: exactly _MAX_CONCURRENT_EXECUTIONS
        # coroutines drain the queue.  pending tasks cannot exceed this number,
        # which is the actual backpressure invariant we want.
        workers: List[asyncio.Task] = []
        for i in range(_MAX_CONCURRENT_EXECUTIONS):
            workers.append(
                asyncio.create_task(
                    self._worker_loop(
                        workflow_id,
                        workflow_plan,
                        trigger,
                        queue,
                        abort,
                        service_account=service_account,
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
            flush_task = asyncio.create_task(_periodic_drop_flush())
            try:
                if protocol == "tcp":
                    await run_tcp_syslog_server(host, port, format_hint, on_msg, abort_event=abort)
                else:
                    await run_udp_syslog_server(host, port, format_hint, on_msg, abort_event=abort)
            finally:
                if not mark_task.done():
                    mark_task.cancel()
                if not flush_task.done():
                    flush_task.cancel()
                # Best-effort: emit any leftover drop count on shutdown so a
                # tail of dropped messages isn't silently lost.
                throttle.flush_remaining()

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
        workflow_plan: Any,
        trigger: TriggerDefinition,
        queue: asyncio.Queue,
        abort: asyncio.Event,
        service_account: Dict[str, Any] | None = None,
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
                await self._trigger_workflow(
                    workflow_id,
                    workflow_plan,
                    msg,
                    next(iter(trigger.mapping or {}), "syslog_message"),
                    trigger=trigger,
                    source=f"{(trigger.source or {}).get('protocol', 'udp')}://{(trigger.source or {}).get('host', '0.0.0.0')}:{(trigger.source or {}).get('port', 5140)}",
                    service_account=service_account,
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
        service_account: Dict[str, Any] | None = None,
    ) -> None:
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
            if service_account:
                mapped_inputs = dict(mapped_inputs)
                flocks_meta = mapped_inputs.get("_flocks")
                if not isinstance(flocks_meta, dict):
                    flocks_meta = {}
                flocks_meta["subject"] = service_account
                mapped_inputs["_flocks"] = flocks_meta
            summarized_inputs = {"_trigger": trigger.type}
            summarized_inputs.update(mapped_inputs)

            exec_data = await create_execution_record(
                workflow_id,
                input_params=summarized_inputs,
            )
            exec_id = exec_data["id"]
            loop = asyncio.get_running_loop()
            step_recorder = ExecutionStepRecorder(
                exec_id=exec_id,
                loop=loop,
                logger=log,
                log_event="syslog.execution_step.write_failed",
            )
            start_time = time.time()
            trigger_meta = mapped_inputs.get("_flocks", {}).get("trigger", {})
            try:
                flocks_meta = mapped_inputs.get("_flocks", {})
                workflow_subject = None
                if isinstance(flocks_meta, dict):
                    maybe_subject = flocks_meta.get("subject")
                    if isinstance(maybe_subject, dict):
                        workflow_subject = dict(maybe_subject)
                workflow_ctx = ToolContext(
                    session_id=f"workflow:{workflow_id}",
                    message_id=f"workflow:{exec_id}",
                    agent="workflow",
                    extra={
                        "entry": "headless",
                        "trace_id": str(exec_id),
                        "subject": workflow_subject or {},
                        "legacy_compat": True,
                    },
                )
                async def _run_workflow_effect():
                    return await asyncio.to_thread(
                        run_workflow,
                        workflow=workflow_plan,
                        inputs=mapped_inputs,
                        run_id=exec_id,
                        trace=False,
                        execution_profile="high_frequency",
                        on_step_complete=step_recorder.on_step_complete,
                        tool_context=workflow_ctx,
                    )

                result = await execute_trigger_action(
                    workflow_id=workflow_id,
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    mapped_inputs=mapped_inputs,
                    effect=_run_workflow_effect,
                    subject=workflow_subject,
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
                try:
                    await record_execution_result(workflow_id, exec_id, exec_data)
                except Exception as exc:
                    log.warning("syslog.exec_record_failed", {"exec_id": exec_id, "error": str(exc)})
            return exec_data

        try:
            await self._dispatcher.dispatch(
                trigger=trigger,
                event=event,
                executor=_executor,
            )
        except TriggerDispatchError as exc:
            log.warning(
                "syslog.trigger_dispatch_failed",
                {"workflow_id": workflow_id, "trigger_id": trigger.id, "error": str(exc)},
            )


default_manager = SyslogManager()
