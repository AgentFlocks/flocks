"""Failure notifications independent of a workflow's final node and SQLite.

Only explicitly configured IM sessions are eligible. Delivery uses a durable
outbox, a cancellable tool deadline, and one retained worker per workflow. An
outer wait timeout never abandons a thread and starts an unbounded replacement.

Schedule config.inputs must include notify_session_id to opt in. Optional
notify_channel_type selects a binding (default auto requires one unique target);
notify_channel_enabled=false disables both new notices and pending delivery.
Success rounds retry older pending faults without creating success messages.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from flocks.tool import ToolContext
from flocks.utils.log import Log
from flocks.workflow.notification_delivery import deliver_workflow_notifications, notifications_enabled
from flocks.workflow.tools import ToolFacade, get_tool_registry

log = Log.create(service="workflow.health_notifications")
FAILURE_STATUSES = {"error", "failed", "timeout", "persistence_error"}
TOOL_TIMEOUT_SECONDS = 10.0
DELIVERY_WAIT_SECONDS = 12.0
NOTIFICATION_INTERVAL_SECONDS = 900


def _stable_error(error: str) -> str:
    error = re.sub(r"\brun_id=\S+", "run_id=<run>", error)
    error = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "<id>", error)
    error = re.sub(r"poller-\d+-[0-9a-f]+", "<run>", error)
    error = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+]\d{2}:\d{2})?", "<time>", error)
    return error[:600]


def _workflow_path_component(workflow_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", workflow_id) and workflow_id not in {".", ".."}:
        return workflow_id
    return "workflow-" + hashlib.sha256(workflow_id.encode()).hexdigest()[:24]


def _notification_context(tool_context: Any, session_id: str, workflow_id: str) -> ToolContext:
    # Detached from a temporary workflow parent, which cleanup may purge while
    # a slow, already-bounded delivery finishes in its retained worker.
    extra = dict(getattr(tool_context, "extra", {}) or {})
    extra.pop("workflow_temp_parent", None)
    extra["workflow_context"] = {"workflow_id": workflow_id, "action_name": "health-notification"}
    return ToolContext(
        session_id=session_id, message_id="workflow-health-notification",
        agent=getattr(tool_context, "agent", "rex"), extra=extra,
        permission_callback=getattr(tool_context, "_permission_callback", None),
        metadata_callback=getattr(tool_context, "_metadata_callback", None),
    )


class WorkflowHealthNotifier:
    def __init__(self, *, root: Path | None = None, wait_seconds: float = DELIVERY_WAIT_SECONDS,
                 interval_seconds: int = NOTIFICATION_INTERVAL_SECONDS):
        self.root = root
        self.wait_seconds = wait_seconds
        self.interval_seconds = max(1, interval_seconds)
        self._workers: dict[str, asyncio.Task[Any]] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queue_jobs: set[asyncio.Task[Any]] = set()

    def get_status(self, workflow_id: str) -> dict[str, Any] | None:
        value = self._status.get(workflow_id)
        return dict(value) if value is not None else None

    async def notify(self, workflow_id: str, config: Mapping[str, Any], *, status: str,
                     error: str, stage: str = "execution", tool_context: Any = None,
                     cancelled: bool = False) -> dict[str, Any]:
        inputs = config.get("inputs") if isinstance(config.get("inputs"), dict) else {}
        if cancelled or status not in FAILURE_STATUSES | {"success"}:
            return {"status": "skipped"}
        session_id = str(inputs.get("notify_session_id") or "").strip()
        if not session_id or not notifications_enabled(inputs):
            return {"status": "disabled"}
        # Persist each new fault before considering an existing delivery worker.
        # Short, bounded queue writes are serialized separately from transport;
        # another slow send must never make a different failure disappear.
        queue_lock = self._queue_locks.setdefault(workflow_id, asyncio.Lock())
        async def enqueue_serialized() -> dict[str, Any]:
            async with queue_lock:
                return await asyncio.to_thread(self._enqueue, workflow_id, dict(inputs), status, error, stage)
        queue_job = asyncio.create_task(enqueue_serialized(), name="workflow-health-enqueue")
        self._queue_jobs.add(queue_job)
        def queue_finished(done: asyncio.Task[Any]) -> None:
            self._queue_jobs.discard(done)
            if not done.cancelled():
                # Retrieve failures even when the caller was stopped while its
                # retained queue task continued holding the per-workflow lock.
                exc = done.exception()
                if exc is not None:
                    self._status[workflow_id] = {"status": "failed", "error": str(exc)}
                    log.warning("workflow.health_notification_queue_failed", {"workflow_id": workflow_id, "error": str(exc)})
        queue_job.add_done_callback(queue_finished)
        try:
            queued = await asyncio.shield(queue_job)
        except Exception as exc:
            report = {"status": "failed", "error": str(exc)}
            self._status[workflow_id] = report
            log.warning("workflow.health_notification_queue_failed", {"workflow_id": workflow_id, **report})
            return report
        previous = self._workers.get(workflow_id)
        if previous is not None and not previous.done():
            return {**queued, "status": "in_flight"}
        if not queued.get("notify_queued"):
            report = {"status": "rate_limited" if status in FAILURE_STATUSES else "skipped", **queued}
            self._status[workflow_id] = report
            return report
        context = _notification_context(tool_context, session_id, workflow_id)
        task = asyncio.create_task(asyncio.to_thread(
            self._deliver, workflow_id, dict(inputs), context,
        ), name=f"workflow-health-{_workflow_path_component(workflow_id)}")
        self._workers[workflow_id] = task
        self._status[workflow_id] = {"status": "in_flight"}

        def finished(done: asyncio.Task[Any]) -> None:
            if done.cancelled():
                self._status[workflow_id] = {"status": "queued", "error": "notification worker interrupted"}
                return
            try:
                report = done.result()
            except Exception as exc:
                report = {"status": "failed", "error": str(exc)}
            self._status[workflow_id] = report
            if report.get("status") == "failed":
                log.warning("workflow.health_notification_failed", {"workflow_id": workflow_id, **report})
        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self.wait_seconds)
        except asyncio.TimeoutError:
            report = {"status": "in_flight", "error": "notification deadline pending; existing worker retained"}
            self._status[workflow_id] = report
            return report
        except Exception as exc:
            report = {"status": "failed", "error": str(exc)}
            self._status[workflow_id] = report
            return report

    def _outbox(self, workflow_id: str) -> Path:
        if self.root is None:
            from flocks.config import Config
            root = Config().get_global().data_dir.parent
        else:
            root = self.root
        return Path(root) / "workspace" / "workflows" / _workflow_path_component(workflow_id) / "notification-outbox"

    def _enqueue(self, workflow_id: str, inputs: dict[str, Any], status: str,
                 error: str, stage: str) -> dict[str, Any]:
        outbox = self._outbox(workflow_id)
        if status == "success":
            if not (outbox / "pending.json").exists():
                return {"notify_queued": 0}
            messages = []
        else:
            messages = self._fault_messages(workflow_id, status, error, stage, outbox)
        result = deliver_workflow_notifications(
            None, {**inputs, "_workflow_id": workflow_id}, messages,
            outbox_dir=outbox, max_attempts=0,
        )
        return {key: value for key, value in result.items() if key != "notify_messages"}

    def _fault_messages(self, workflow_id: str, status: str, error: str,
                        stage: str, outbox: Path) -> list[dict[str, str]]:
        normalized = _stable_error(str(error))
        signature = hashlib.sha256(f"{workflow_id}\0{status}\0{stage}\0{normalized}".encode()).hexdigest()
        prefix = f"health:{signature}:"
        # Atomic outbox replacement makes an unlocked read a complete snapshot.
        # Retain just one pending reminder for a fault across restarts/buckets.
        pending_file = outbox / "pending.json"
        pending = json.loads(pending_file.read_text(encoding="utf-8")).get("pending", {}) if pending_file.exists() else {}
        already_queued = any(str(item.get("dedup_key", "")).startswith(prefix) for item in pending.values())
        now = time.time()
        return [] if already_queued else [{
            "dedup_key": prefix + str(int(now // self.interval_seconds)),
            "message": (f"工作流运行异常提醒\n工作流: {workflow_id[:128]}\n状态: {status}\n阶段: {stage}\n"
                        f"错误: {normalized or 'unknown'}\n请检查工作流执行记录及服务日志。"),
        }]

    def _deliver(self, workflow_id: str, inputs: dict[str, Any], tool_context: ToolContext) -> dict[str, Any]:
        outbox = self._outbox(workflow_id)
        deadline = time.monotonic() + TOOL_TIMEOUT_SECONDS
        registry = get_tool_registry(tool_context)
        # Adapter cancellation cancels its asynchronous tool coroutine rather
        # than abandoning a synchronous thread. HTTP channel sends also have
        # their own transport timeout. Third-party code that ignores async
        # cancellation is still bounded to one retained worker per workflow.
        registry.cancel_checker = lambda: time.monotonic() >= deadline
        result = deliver_workflow_notifications(
            ToolFacade(registry), {**inputs, "_workflow_id": workflow_id}, [],
            outbox_dir=outbox, max_attempts=1,
        )
        report_status = ("failed" if result.get("notify_failed") else "sent" if result.get("notify_sent")
                         else "queued" if result.get("notify_queued") else "rate_limited")
        return {"status": report_status, **{key: value for key, value in result.items() if key != "notify_messages"}}
