"""Task Manager for scheduler/execution domain."""

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as _BaseModel
from filelock import FileLock, Timeout as FileLockTimeout

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .executor import _TASK_ABSOLUTE_TIMEOUT_S, TaskExecutor, TaskResultPersistenceError
from .health import ComponentHealth
from .receipts import CompletionReceipts, atomic_json, disk_operation, ensure_private_directory
from .models import (
    DeliveryStatus,
    ExecutionMode,
    ExecutionTriggerType,
    RetryConfig,
    SchedulerMode,
    SchedulerStatus,
    TaskExecution,
    TaskPriority,
    TaskScheduler,
    TaskSource,
    TaskStatus,
    TaskTrigger,
    validate_cron_expression,
    validate_timezone,
)
from .queue import DEFAULT_MAX_CONCURRENT, TaskQueue
from .scheduler import TaskScheduler as SchedulerLoop
from .store import TaskStore

log = Log.create(service="task.manager")

_TASK_EXPIRY_HOURS: int = 24
_CLEANUP_INTERVAL_S: int = 3600
_RETRY_CHECK_INTERVAL_S: int = 30
_STALE_RECOVERY_INTERVAL_S: int = 30
_ORPHANED_QUEUE_RECOVERY_INTERVAL_S: int = 30
_DISPATCH_GUARD_TIMEOUT_S: int = _TASK_ABSOLUTE_TIMEOUT_S + 30
_RUNNING_RECOVERY_TIMEOUT_S: int = _DISPATCH_GUARD_TIMEOUT_S + 300


class _TaskEventProps(_BaseModel):
    task_id: str
    status: str
    title: str


class TaskManager:
    _instance: Optional["TaskManager"] = None
    _startup_error: Optional[str] = None

    def __init__(
        self,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        poll_interval: int = 5,
        scheduler_interval: int = 30,
        default_retry: Optional[RetryConfig] = None,
    ):
        self.queue = TaskQueue(max_concurrent=max_concurrent)
        self.scheduler = SchedulerLoop(check_interval=scheduler_interval)
        self._poll_interval = poll_interval
        self._default_retry = default_retry or RetryConfig()
        self._loop_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_retry_check: float = 0.0
        self._last_stale_recovery_check: float = 0.0
        self._last_orphaned_queue_recovery_check: float = 0.0
        self._health = ComponentHealth()
        # Occupy their original queue slots until finalization succeeds. This
        # bounds the pending results by concurrency and prevents a second run.
        self._pending_completions: dict[str, TaskExecution] = {}
        self._execution_tasks: set[asyncio.Task] = set()
        self._execution_workers: dict[str, tuple[TaskExecution, asyncio.Task]] = {}
        self._receipts = CompletionReceipts.for_store()
        self._leadership = None
        self._is_leader = False
        self._reconciling = False
        self._recovery_counts: dict[str, int] = {}
        self._leader_health: dict | None = None
        self._receipt_admission_failed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    async def start(
        cls,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        poll_interval: int = 5,
        scheduler_interval: int = 30,
    ) -> "TaskManager":
        if cls._instance and cls._instance._running:
            return cls._instance
        await TaskStore.init()
        cls._startup_error = None

        mgr = cls(
            max_concurrent=max_concurrent,
            poll_interval=poll_interval,
            scheduler_interval=scheduler_interval,
        )
        mgr._running = True
        mgr._health.started()
        cls._instance = mgr
        # Elect before any startup recovery or scheduler starts. Other servers
        # sharing the same data directory remain API-capable standby instances.
        await mgr._try_become_leader()
        if mgr._is_leader:
            try:
                await mgr._activate_leader()
            except Exception as exc:
                mgr._health.failed(exc, operation="startup_reconciliation")
        await mgr._exchange_runtime_health()
        mgr._loop_task = asyncio.create_task(mgr._leadership_loop())
        log.info("manager.started")
        return mgr

    @classmethod
    async def stop(cls) -> None:
        mgr = cls._instance
        if not mgr:
            return
        mgr._running = False
        for task_attr in ("_loop_task", "_cleanup_task"):
            task = getattr(mgr, task_attr, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await mgr.scheduler.stop()
        # Do not let workers reopen storage after shutdown has closed it.
        workers = list(mgr._execution_tasks)
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        mgr._execution_tasks.clear()
        mgr._execution_workers.clear()
        if mgr._leadership is not None:
            mgr._leadership.release()
            mgr._leadership = None
        mgr._is_leader = False
        cls._instance = None
        log.info("manager.stopped")

    @classmethod
    def get(cls) -> Optional["TaskManager"]:
        return cls._instance

    @classmethod
    def mark_start_failed(cls, error: Exception) -> None:
        cls._startup_error = str(error)

    @classmethod
    def runtime_status(cls) -> Dict[str, Any]:
        mgr = cls._instance
        if not mgr:
            return {
                "task_manager_started": False,
                "task_scheduler_running": False,
                "task_scheduler_available": False,
                "task_manager_error": cls._startup_error,
            }
        return {
            "task_manager_started": mgr._running,
            "task_scheduler_running": mgr.scheduler.is_running,
            "task_scheduler_available": mgr.scheduler.is_available,
            "task_manager_error": cls._startup_error,
        }

    @classmethod
    def health_snapshot(cls) -> Dict[str, Any]:
        """No database I/O: still responds when storage or dispatch is blocked."""
        mgr = cls._instance
        storage = TaskStore.health_snapshot()
        if mgr is None or not mgr._running:
            return {"status": "unavailable", "storage": storage, "pending_results": 0}
        return mgr._local_health(storage)

    def _local_health(self, storage: dict | None = None) -> dict:
        storage = storage if storage is not None else TaskStore.health_snapshot()
        if not self._is_leader and self._leader_health is not None:
            state = dict(self._leader_health)
            stamp = datetime.fromisoformat(state.pop("recorded_at"))
            if (datetime.now(timezone.utc) - stamp).total_seconds() > 60:
                state["status"] = "degraded"
                state["leader_stalled"] = True
            state["role"] = "standby"
            return state
        executor = self._health.snapshot(max_silence=max(60, self._poll_interval * 3))
        scheduler = self.scheduler.health_snapshot()
        degraded = (bool(self._pending_completions) or self._receipt_admission_failed or self._reconciling or
                    (self._is_leader and not self.scheduler.is_running) or
                    (self._loop_task is not None and self._loop_task.done()) or
                    any(item["status"] == "degraded" for item in (storage, executor, scheduler)))
        # A standby without a readable leader heartbeat cannot certify that
        # the shared task system is healthy.
        if not self._is_leader:
            degraded = True
        return {"status": "degraded" if degraded else "healthy", "storage": storage,
                "executor": executor, "scheduler": scheduler,
                "role": "leader" if self._is_leader else "standby",
                "reconciling": self._reconciling, "recovery": dict(self._recovery_counts),
                "receipt_admission_failed": self._receipt_admission_failed,
                "pending_results": len(self._pending_completions)}

    # ------------------------------------------------------------------
    # Scheduler APIs
    # ------------------------------------------------------------------

    @classmethod
    async def create_scheduler(
        cls,
        *,
        title: str,
        description: str = "",
        mode: SchedulerMode = SchedulerMode.ONCE,
        priority: TaskPriority = TaskPriority.NORMAL,
        source: Optional[TaskSource] = None,
        trigger: Optional[TaskTrigger] = None,
        execution_mode: ExecutionMode = ExecutionMode.AGENT,
        agent_name: str = "rex",
        workflow_id: Optional[str] = None,
        skills: Optional[List[str]] = None,
        category: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        workspace_directory: Optional[str] = None,
        tags: Optional[List[str]] = None,
        created_by: str = "rex",
        dedup_key: Optional[str] = None,
    ) -> TaskScheduler:
        trigger = trigger.model_copy(deep=True) if trigger is not None else TaskTrigger()
        if trigger.cron:
            trigger.cron = validate_cron_expression(trigger.cron)
        source = source or TaskSource()
        mgr = cls._instance
        retry = mgr._default_retry.model_copy(deep=True) if mgr else RetryConfig()
        scheduler = TaskScheduler(
            title=title,
            description=description,
            mode=mode,
            status=SchedulerStatus.ACTIVE,
            priority=priority,
            source=source,
            trigger=trigger,
            execution_mode=execution_mode,
            agent_name=agent_name,
            workflow_id=workflow_id,
            skills=skills or [],
            category=category,
            context=context or {},
            workspace_directory=workspace_directory,
            retry=retry,
            tags=tags or [],
            created_by=created_by,
            dedup_key=dedup_key,
        )
        if trigger.cron:
            scheduler.trigger.next_run = SchedulerLoop.compute_next_run(
                trigger.cron, trigger.timezone
            )
        created = await TaskStore.create_scheduler(scheduler)
        if created is None and dedup_key:
            existing = await TaskStore.get_scheduler_by_dedup_key(dedup_key)
            if existing:
                return existing
        if scheduler.mode == SchedulerMode.ONCE and scheduler.trigger.run_immediately:
            await cls.create_execution_from_scheduler(
                scheduler,
                trigger_type=ExecutionTriggerType.RUN_ONCE,
                enqueue=True,
            )
        await cls._publish_event("task.created", scheduler.id, "active", scheduler.title)
        return scheduler

    @classmethod
    async def list_schedulers(
        cls,
        *,
        status: Optional[SchedulerStatus] = None,
        priority: Optional[TaskPriority] = None,
        scheduled_only: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[TaskScheduler], int]:
        return await TaskStore.list_schedulers(
            status=status,
            priority=priority,
            scheduled_only=scheduled_only,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
        )

    @classmethod
    async def get_scheduler(cls, scheduler_id: str) -> Optional[TaskScheduler]:
        return await TaskStore.get_scheduler(scheduler_id)

    @classmethod
    async def update_scheduler(
        cls, scheduler_id: str, **fields: Any
    ) -> Optional[TaskScheduler]:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return None
        for key, value in fields.items():
            if hasattr(scheduler, key):
                setattr(scheduler, key, value)
        scheduler = await TaskStore.update_scheduler(scheduler)
        await cls._publish_event("task.status", scheduler.id, scheduler.status.value, scheduler.title)
        return scheduler

    @classmethod
    async def update_scheduler_with_trigger(
        cls,
        scheduler_id: str,
        *,
        fields: Dict[str, Any],
        cron: Optional[str] = None,
        timezone: Optional[str] = None,
        cron_description: Optional[str] = None,
        run_once: Optional[bool] = None,
        run_at: Optional[str] = None,
        user_prompt: Optional[str] = None,
    ) -> Optional[TaskScheduler]:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return None
        if user_prompt is not None:
            scheduler.source.user_prompt = user_prompt
        for key, value in fields.items():
            if key in ("misfire_policy", "misfire_grace_seconds", "max_catch_up"):
                setattr(scheduler.trigger, key, value)
                scheduler.trigger.catch_up_remaining = None
                scheduler.trigger.catch_up_until = None
            elif hasattr(scheduler, key):
                setattr(scheduler, key, value)
        if any(v is not None for v in [cron, timezone, cron_description, run_once, run_at]):
            scheduler.trigger.catch_up_remaining = None
            scheduler.trigger.catch_up_until = None
            tz = validate_timezone(timezone) if timezone else scheduler.trigger.timezone
            normalized_cron = validate_cron_expression(cron) if cron else None
            if run_once is None:
                run_once = scheduler.mode == SchedulerMode.ONCE
            if run_once:
                scheduler.mode = SchedulerMode.ONCE
                if cron is not None:
                    scheduler.trigger.cron = normalized_cron
                scheduler.trigger.timezone = tz
                scheduler.trigger.cron_description = cron_description or scheduler.trigger.cron_description
                scheduler.trigger.run_at = datetime.fromisoformat(run_at) if run_at else scheduler.trigger.run_at
                scheduler.trigger.next_run = scheduler.trigger.run_at
            else:
                # A partial update (timezone / description / runOnce=false only)
                # keeps the cron the scheduler already has.
                if not normalized_cron:
                    normalized_cron = scheduler.trigger.cron
                if not normalized_cron:
                    raise ValueError("cron is required for recurring scheduled tasks")
                scheduler.mode = SchedulerMode.CRON
                scheduler.trigger.run_at = None
                scheduler.trigger.cron = normalized_cron
                scheduler.trigger.timezone = tz
                scheduler.trigger.cron_description = cron_description or scheduler.trigger.cron_description
                scheduler.trigger.next_run = SchedulerLoop.compute_next_run(normalized_cron, tz)
        scheduler.trigger = TaskTrigger.model_validate(scheduler.trigger.model_dump())
        scheduler = await TaskStore.update_scheduler(scheduler)
        return scheduler

    @classmethod
    async def delete_scheduler(cls, scheduler_id: str) -> bool:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return False
        await cls._cleanup_scheduler_active_executions(scheduler.id)
        if scheduler.dedup_key and scheduler.dedup_key.startswith("builtin:"):
            scheduler.status = SchedulerStatus.ARCHIVED
            await TaskStore.update_scheduler(scheduler)
            return True
        return await TaskStore.delete_scheduler(scheduler_id)

    @classmethod
    async def enable_scheduler(cls, scheduler_id: str) -> Optional[TaskScheduler]:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return None
        scheduler.status = SchedulerStatus.ACTIVE
        scheduler.trigger.catch_up_remaining = None
        scheduler.trigger.catch_up_until = None
        if scheduler.mode == SchedulerMode.CRON and scheduler.trigger.cron:
            scheduler.trigger.next_run = SchedulerLoop.compute_next_run(
                scheduler.trigger.cron,
                scheduler.trigger.timezone,
            )
        elif scheduler.mode == SchedulerMode.ONCE:
            scheduler.trigger.next_run = scheduler.trigger.run_at
        return await TaskStore.update_scheduler(scheduler)

    @classmethod
    async def disable_scheduler(cls, scheduler_id: str) -> Optional[TaskScheduler]:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return None
        scheduler.status = SchedulerStatus.DISABLED
        return await TaskStore.update_scheduler(scheduler)

    # ------------------------------------------------------------------
    # Execution APIs
    # ------------------------------------------------------------------

    @classmethod
    async def create_execution_from_scheduler(
        cls,
        scheduler: TaskScheduler,
        *,
        trigger_type: ExecutionTriggerType,
        enqueue: bool = True,
    ) -> TaskExecution:
        execution = cls.build_execution_from_scheduler(scheduler, trigger_type=trigger_type)
        await TaskStore.create_execution(execution, enqueue=enqueue)
        return execution

    @classmethod
    def build_execution_from_scheduler(
        cls,
        scheduler: TaskScheduler,
        *,
        trigger_type: ExecutionTriggerType,
    ) -> TaskExecution:
        """A pending execution of *scheduler*, not yet stored."""
        execution = TaskExecution(
            scheduler_id=scheduler.id,
            title=scheduler.title,
            description=scheduler.description,
            priority=scheduler.priority,
            source=scheduler.source.model_copy(deep=True),
            trigger_type=trigger_type,
            status=TaskStatus.PENDING,
            workspace_directory=scheduler.workspace_directory,
            retry=scheduler.retry.model_copy(deep=True),
            execution_mode=scheduler.execution_mode,
            agent_name=scheduler.agent_name,
            workflow_id=scheduler.workflow_id,
            execution_input_snapshot={
                "title": scheduler.title,
                "description": scheduler.description,
                "source": scheduler.source.model_dump(mode="python", by_alias=True),
                "context": scheduler.context,
                "workspaceDirectory": scheduler.workspace_directory,
                "skills": scheduler.skills,
                "category": scheduler.category,
                "tags": scheduler.tags,
            },
        )
        if not execution.workspace_directory:
            execution.workspace_directory = cls._default_workspace_directory(execution.id)
        return execution

    @classmethod
    async def rerun_scheduler(cls, scheduler_id: str) -> Optional[TaskExecution]:
        scheduler = await TaskStore.get_scheduler(scheduler_id)
        if not scheduler:
            return None
        return await cls.create_execution_from_scheduler(
            scheduler,
            trigger_type=ExecutionTriggerType.RERUN,
            enqueue=True,
        )

    @classmethod
    async def list_executions(
        cls,
        *,
        scheduler_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        title: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        sort_by: str = "queued_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[TaskExecution], int]:
        return await TaskStore.list_executions(
            scheduler_id=scheduler_id,
            status=status,
            priority=priority,
            delivery_status=delivery_status,
            title=title,
            started_after=started_after,
            started_before=started_before,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
        )

    @classmethod
    async def get_execution(cls, execution_id: str) -> Optional[TaskExecution]:
        return await TaskStore.get_execution(execution_id)

    @classmethod
    async def list_scheduler_executions(
        cls, scheduler_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[List[TaskExecution], int]:
        return await TaskStore.list_scheduler_executions(
            scheduler_id, limit=limit, offset=offset
        )

    @classmethod
    async def cancel_execution(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        execution = await TaskStore.get_execution(execution_id)
        if not execution or execution.is_terminal:
            return execution
        completed_at = datetime.now(timezone.utc)
        duration_ms = (
            int((completed_at - execution.started_at).total_seconds() * 1000)
            if execution.started_at
            else None
        )
        # Only the status columns: the dispatcher may be writing the session
        # id of a run it is starting right now.
        cancelled = await TaskStore.cancel_active_execution(
            execution_id,
            completed_at=completed_at,
            duration_ms=duration_ms,
            error="Cancelled from task queue.",
        )
        execution = await TaskStore.get_execution(execution_id) or execution
        if not cancelled:
            return execution
        # Stop the run only after the record says cancelled: a run interrupted
        # while it still says running is queued again by _run_execution.
        await cls._cancel_execution_runtime(execution)
        await TaskStore.finish_queue_ref(execution_id)
        mgr = cls._instance
        if mgr:
            mgr.queue.mark_finished(execution_id)
        await cls._publish_execution_update(execution)
        return execution

    @classmethod
    async def retry_execution(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        execution = await TaskStore.get_execution(execution_id)
        if not execution or execution.status != TaskStatus.FAILED:
            return execution
        execution.retry.retry_count += 1
        execution.retry.retry_after = None
        # A person asked for it to start over: whatever held it is their call.
        execution.retry.blocked_reason = None
        execution.retry.unverified_effects = []
        execution.status = TaskStatus.QUEUED
        execution.queued_at = datetime.now(timezone.utc)
        execution.started_at = None
        execution.completed_at = None
        execution.duration_ms = None
        execution.error = None
        execution.result_summary = None
        execution.session_id = None
        return await cls._enqueue_execution(execution, expected_status=TaskStatus.FAILED)

    @classmethod
    async def resume_execution(cls, execution_id: str) -> Optional[TaskExecution]:
        """Go on with a run held for verification, in the session it ran in.

        For a person who checked what the held run did: the run continues
        from its persisted session, told which actions to check before it
        repeats any.  Raises ValueError for a run that is not held, or has no
        session to go on in (a workflow run is retried instead).
        """
        from flocks.task.outcome import BLOCKED_UNVERIFIED_EFFECTS

        execution = await TaskStore.get_execution(execution_id)
        if execution is None:
            return None
        if execution.status != TaskStatus.FAILED or execution.retry.blocked_reason != BLOCKED_UNVERIFIED_EFFECTS:
            raise ValueError("only a run held for verification can be resumed")
        if execution.execution_mode != ExecutionMode.AGENT or not execution.session_id:
            raise ValueError("this run has no session to go on in; retry it instead")
        execution.retry.blocked_reason = None
        execution.retry.retry_after = None
        return await cls._requeue_execution(execution, keep_session=True)

    @classmethod
    async def rerun_execution(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        execution = await TaskStore.get_execution(execution_id)
        if not execution:
            return None
        if execution.status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            await cls.cancel_execution(execution_id)
        scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
        if not scheduler:
            return None
        return await cls.create_execution_from_scheduler(
            scheduler,
            trigger_type=ExecutionTriggerType.RERUN,
            enqueue=True,
        )

    @classmethod
    async def delete_execution(cls, execution_id: str) -> bool:
        execution = await TaskStore.get_execution(execution_id)
        if not execution:
            return False
        if execution.status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            await cls.cancel_execution(execution_id)
        return await TaskStore.delete_execution(execution_id)

    @classmethod
    async def _cleanup_scheduler_active_executions(cls, scheduler_id: str) -> int:
        active_executions = await TaskStore.list_active_executions_for_scheduler(scheduler_id)
        cleaned = 0
        for execution in active_executions:
            cancelled = await cls.cancel_execution(execution.id)
            if cancelled is not None:
                cleaned += 1
        return cleaned

    @classmethod
    async def batch_cancel(cls, execution_ids: List[str]) -> int:
        count = 0
        for execution_id in execution_ids:
            existing = await TaskStore.get_execution(execution_id)
            if not existing or existing.is_terminal:
                continue
            execution = await cls.cancel_execution(execution_id)
            if execution and execution.status == TaskStatus.CANCELLED:
                count += 1
        return count

    @classmethod
    async def batch_delete(cls, execution_ids: List[str]) -> int:
        count = 0
        for execution_id in execution_ids:
            if await cls.delete_execution(execution_id):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Dashboard / queue
    # ------------------------------------------------------------------

    @classmethod
    async def dashboard(cls) -> Dict[str, Any]:
        counts = await TaskStore.dashboard_counts()
        mgr = cls._instance
        if mgr:
            await mgr._read_queue_control()
        counts["queue_paused"] = mgr.queue.paused if mgr else False
        return counts

    @classmethod
    async def queue_status(cls) -> Dict[str, Any]:
        mgr = cls._instance
        if not mgr:
            return {
                "paused": False,
                "max_concurrent": DEFAULT_MAX_CONCURRENT,
                "running": 0,
                "queued": 0,
                "stale_running": 0,
                "oldest_running_seconds": None,
            }
        await mgr._read_queue_control()
        status = await mgr.queue.status()
        running = await TaskStore.list_executions_by_status(TaskStatus.RUNNING)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RUNNING_RECOVERY_TIMEOUT_S)
        oldest_running_seconds: Optional[int] = None
        stale_running = 0
        now = datetime.now(timezone.utc)
        for execution in running:
            started_at = (
                execution.started_at
                or execution.queued_at
                or execution.created_at
            )
            elapsed = max(int((now - started_at).total_seconds()), 0)
            if oldest_running_seconds is None or elapsed > oldest_running_seconds:
                oldest_running_seconds = elapsed
            if started_at < stale_cutoff:
                stale_running += 1
        status["stale_running"] = stale_running
        status["oldest_running_seconds"] = oldest_running_seconds
        return status

    @classmethod
    async def get_unviewed_results(cls) -> List[TaskExecution]:
        return await TaskStore.get_unviewed_results()

    @classmethod
    async def mark_viewed(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        return await TaskStore.mark_execution_viewed(execution_id)

    @classmethod
    async def mark_notified(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        return await TaskStore.mark_execution_notified(execution_id)

    @classmethod
    async def get_task_page_notice(cls) -> Optional[Dict[str, Any]]:
        state = cls._read_migration_state()
        if not state.get("failed") or not cls._legacy_tables_exist():
            return None
        count = int(state.get("notice_count") or 0)
        count += 1
        state["notice_count"] = count
        cls._write_migration_state(state)
        if count >= 3:
            # Shown enough: stop asking.  The old tables stay where they are;
            # nothing reads them any more.
            cls._clear_migration_state()
        return {
            "message": "系统更新了任务表的存储，旧表自动迁移失败，请手动重建任务 scheduler",
            "displayCount": count,
        }

    @classmethod
    def pause_queue(cls) -> None:
        cls._write_queue_control(True)
        mgr = cls._instance
        if mgr:
            mgr.queue.pause()

    @classmethod
    def resume_queue(cls) -> None:
        cls._write_queue_control(False)
        mgr = cls._instance
        if mgr:
            mgr.queue.resume()

    @staticmethod
    def _write_queue_control(paused: bool) -> None:
        path = TaskStore.get_db_path().with_name("task-runtime") / "queue-control.json"
        atomic_json(path, {"paused": paused})

    @classmethod
    async def set_queue_paused(cls, paused: bool) -> None:
        await disk_operation(cls._write_queue_control, paused)
        mgr = cls._instance
        if mgr:
            mgr.queue.pause() if paused else mgr.queue.resume()

    async def _read_queue_control(self) -> None:
        path = TaskStore.get_db_path().with_name("task-runtime") / "queue-control.json"
        def read():
            try:
                if path.stat().st_size > 1024:
                    raise ValueError("Oversized queue control")
                value = json.loads(path.read_text(encoding="utf-8"))["paused"]
                if not isinstance(value, bool):
                    raise ValueError("Invalid queue control")
                return value
            except FileNotFoundError:
                return None
        try:
            paused = await disk_operation(read)
        except Exception:
            self.queue.pause()  # Fail closed if a durable pause cannot be read.
            raise
        if paused is not None and paused != self.queue.paused:
            self.queue.pause() if paused else self.queue.resume()

    async def _sync_runtime_controls(self) -> None:
        await self._read_queue_control()
        # Cancellation can arrive through a standby API process. The database
        # already says cancelled; signal the runtime in the owning process too.
        for execution_id, (execution, worker) in list(self._execution_workers.items()):
            if worker.done():
                continue
            current = await TaskStore.get_execution(execution_id)
            if current is None or current.is_terminal or current.started_at != execution.started_at:
                await self._cancel_execution_runtime(execution)
                worker.cancel()

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _try_become_leader(self) -> None:
        root = TaskStore.get_db_path().with_name("task-runtime")
        await disk_operation(ensure_private_directory, root)
        lock = FileLock(str(root / "manager.lock"), thread_local=False, mode=0o600)
        try:
            lock.acquire(timeout=0)
        except FileLockTimeout:
            return
        self._leadership = lock
        self._is_leader = True
        self._reconciling = True

    async def _leadership_loop(self) -> None:
        while self._running:
            self._health.started()
            try:
                if not self._is_leader:
                    await self._try_become_leader()
                if self._is_leader:
                    if self._reconciling:
                        await self._activate_leader()
                    await self._execution_loop()
                    return
                self._health.succeeded()
            except Exception as exc:
                self._health.failed(exc, operation="startup_reconciliation")
                log.error("manager.reconciliation_failed", {"exception_type": type(exc).__name__})
            if not self._running:
                return
            await self._exchange_runtime_health()
            if self._running:
                await asyncio.sleep(self._poll_interval)

    async def _exchange_runtime_health(self) -> None:
        path = TaskStore.get_db_path().with_name("task-runtime") / "health.json"
        try:
            if self._is_leader:
                await disk_operation(atomic_json, path, {
                    **self._local_health(), "recorded_at": datetime.now(timezone.utc).isoformat(),
                })
            else:
                def read():
                    if path.stat().st_size > 32768:
                        raise ValueError("Oversized task runtime health")
                    state = json.loads(path.read_text(encoding="utf-8"))
                    stamp = datetime.fromisoformat(state["recorded_at"])
                    if stamp.tzinfo is None or state["status"] not in ("healthy", "degraded", "unavailable"):
                        raise ValueError("Invalid task runtime health")
                    return state
                self._leader_health = await disk_operation(read)
        except Exception as exc:
            if not self._is_leader:
                self._leader_health = None
            self._health.failed(exc, operation="runtime_health")

    async def _activate_leader(self) -> None:
        # Keep the lock and pause dispatch if a receipt cannot be reconciled.
        await self._reconcile_receipts()
        while await TaskStore.reconcile_queue_refs():
            await asyncio.sleep(0)
        while await self._recover_orphaned_executions():
            await asyncio.sleep(0)
        await self._recover_orphaned_queued_executions()
        await self._sync_runtime_controls()
        self._reconciling = False
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        await self.scheduler.start()

    async def _reconcile_receipts(self) -> None:
        from .outcome import NEEDS_VERIFICATION, RunOutcome

        counts = {"finished_unsynced": 0, "interrupted": 0, "superseded": 0}
        await disk_operation(self._receipts.cleanup_temporary)
        paths = await disk_operation(self._receipts.paths)
        # Validate all receipts first. A corrupt file can conceal a completion;
        # keep dispatch paused, preserve the evidence, and report degraded health.
        for path in paths:
            await disk_operation(self._receipts.read, path)
        for path in paths:
            phase, execution = await disk_operation(self._receipts.read, path)
            current = await TaskStore.get_execution(execution.id)
            if (current is None or current.is_terminal or current.started_at != execution.started_at):
                counts["superseded"] += 1
            elif phase == "completed":
                await TaskStore.update_active_execution(execution)
                counts["finished_unsynced"] += 1
                if execution.status == TaskStatus.FAILED:
                    await self._handle_failure(execution)
            else:
                # Even a missing session cannot prove that a custom executor
                # did nothing after the durable dispatch intent was written.
                await self._mark_execution_failed(
                    current, self._interrupted_unverified_message(),
                    hold=RunOutcome(NEEDS_VERIFICATION, ("execution:interrupted_without_completion",)),
                )
                counts["interrupted"] += 1
            if (current and current.status == TaskStatus.FAILED and current.started_at == execution.started_at
                    and not current.retry.retry_after and not current.retry.blocked_reason):
                await self._handle_failure(current)
            await self._receipts.acknowledge(execution)
        self._recovery_counts = counts

    async def _execution_loop(self) -> None:
        import time

        while self._running:
            self._health.started()
            errors = []
            now = time.monotonic()
            phases = [("controls", self._sync_runtime_controls), ("results", self._flush_pending_completions)]
            for attribute, interval, operation in (
                ("_last_retry_check", _RETRY_CHECK_INTERVAL_S, self._process_retry_queue),
                ("_last_stale_recovery_check", _STALE_RECOVERY_INTERVAL_S, self._recover_stale_active_executions),
                ("_last_orphaned_queue_recovery_check", _ORPHANED_QUEUE_RECOVERY_INTERVAL_S,
                 self._recover_orphaned_queued_executions),
            ):
                if now - getattr(self, attribute) >= interval:
                    setattr(self, attribute, now)
                    phases.append((operation.__name__, operation))
            for name, operation in phases:
                try:
                    async with asyncio.timeout(10):
                        await operation()
                except Exception as exc:
                    errors.append((name, exc))
                    log.error("manager.maintenance_error", {"phase": name, "error": str(exc)})
            if not self._running:
                return
            try:
                while self._running:
                    execution = await self.queue.dequeue()
                    if not execution:
                        break
                    task = asyncio.create_task(self._run_execution(execution))
                    self._execution_tasks.add(task)
                    self._execution_workers[execution.id] = (execution, task)
                    def forget_worker(done, execution_id=execution.id):
                        record = self._execution_workers.get(execution_id)
                        if record is not None and record[1] is done:
                            self._execution_workers.pop(execution_id, None)
                    task.add_done_callback(forget_worker)
                    task.add_done_callback(self._execution_tasks.discard)
                    task.add_done_callback(self._on_task_done)
            except Exception as exc:
                errors.append(("dequeue", exc))
                log.error("manager.loop_error", {"error": str(exc)})
            if errors:
                self._health.failed(errors[0][1], operation=errors[0][0])
            else:
                self._health.succeeded()
            if not self._running:
                return
            await self._exchange_runtime_health()
            if self._running:
                await asyncio.sleep(self._poll_interval)

    async def _flush_pending_completions(self) -> None:
        error = None
        for execution_id, execution in list(self._pending_completions.items()):
            try:
                try:
                    await self._receipts.completed(execution)
                except OSError:
                    pass  # Still try the database as the durable final record.
                saved = await TaskStore.update_active_execution(execution)
                current = execution if saved else await TaskStore.get_execution(execution_id)
                if current and current.status == TaskStatus.FAILED and current.started_at == execution.started_at:
                    if not current.retry.retry_after and not current.retry.blocked_reason:
                        await self._handle_failure(current)
                await self._receipts.acknowledge(execution)
            except Exception as exc:
                error = exc
                continue
            self._pending_completions.pop(execution_id, None)
            # A person may have cancelled and retried this id meanwhile. An
            # older result must neither overwrite nor release the newer run.
            if current is None or current.is_terminal:
                self.queue.mark_finished(execution_id, expected_started_at=execution.started_at)
            if saved:
                await self._publish_execution_update(execution)
        if error is not None:
            raise error

    @staticmethod
    def _on_task_done(fut: asyncio.Task) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            log.error("manager.task_unhandled", {"error": str(exc)})

    async def _run_execution(self, execution: TaskExecution) -> None:
        claimed_started_at = execution.started_at
        if execution.started_at is None:
            execution.started_at = datetime.now(timezone.utc)
        receipt_execution = execution.model_copy(deep=True)
        scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
        if not scheduler:
            execution.status = TaskStatus.FAILED
            execution.error = f"Scheduler {execution.scheduler_id} not found"
            execution.completed_at = datetime.now(timezone.utc)
            await TaskStore.update_execution(execution)
            return
        should_finish_queue_ref = True
        dispatched = False
        try:
            # Fail before business work if its durable dispatch intent cannot
            # be recorded (disk full, permissions, or receipt backlog limit).
            await self._receipts.started(execution)
            self._receipt_admission_failed = False
            dispatched = True
            execution = await asyncio.wait_for(
                TaskExecutor.dispatch(execution, scheduler),
                timeout=_DISPATCH_GUARD_TIMEOUT_S,
            )
            # Extensions may implement dispatch themselves. Persist their final
            # fact as well, before releasing the dispatch intent.
            if (execution.is_terminal and not execution._completion_persisted
                    and execution.started_at == receipt_execution.started_at):
                async def finalize_extension():
                    try:
                        await self._receipts.completed(execution)
                    except OSError:
                        pass
                    await TaskStore.update_active_execution(execution)
                try:
                    cancelled = await TaskStore._finish_cleanup(finalize_extension())
                except Exception as exc:
                    raise TaskResultPersistenceError(execution) from exc
                if cancelled:
                    raise asyncio.CancelledError
        except TaskResultPersistenceError as exc:
            self._pending_completions[execution.id] = exc.execution
            self._health.failed(exc.__cause__ or exc, operation="save_result")
            log.warn("manager.result_pending", {"id": execution.id})
            return
        except asyncio.CancelledError:
            current = await TaskStore.get_execution(execution.id)
            if current is not None:
                execution = current
            if not dispatched and not execution.is_terminal:
                execution = await self._requeue_execution(execution, keep_session=True)
                should_finish_queue_ref = execution.status != TaskStatus.QUEUED
                await self._receipts.acknowledge(receipt_execution)
            elif not execution.is_terminal:
                from flocks.task.outcome import run_outcome

                outcome = await run_outcome(execution)
                TaskExecutor.forget_workflow_started(execution.id)
                if outcome.safe_to_retry:
                    execution = await self._requeue_execution(execution)
                    should_finish_queue_ref = execution.status != TaskStatus.QUEUED
                else:
                    # Interrupted (a shutdown) after starting work whose
                    # effects nobody confirmed: not started over by itself.
                    execution = await self._mark_execution_failed(
                        execution, self._interrupted_unverified_message(), hold=outcome,
                    )
                await self._publish_execution_update(execution)
            raise
        except asyncio.TimeoutError:
            await self._cancel_execution_runtime(execution)
            log.error(
                "manager.execution_timeout",
                {"id": execution.id, "timeout_s": _DISPATCH_GUARD_TIMEOUT_S},
            )
            execution = await self._mark_execution_failed(
                execution,
                self._dispatch_timeout_message(),
            )
        except Exception as exc:
            if not dispatched:
                self._receipt_admission_failed = True
                self._health.failed(exc, operation="receipt_admission")
                # No business work started: leave it queued for the next poll,
                # without consuming a business retry or claiming unknown effects.
                execution = await self._requeue_execution(execution, keep_session=True)
                should_finish_queue_ref = execution.status != TaskStatus.QUEUED
                return
            # Compatibility with task extensions that save their own terminal
            # result before returning from dispatch.
            if execution.is_terminal:
                self._pending_completions[execution.id] = execution.model_copy(deep=True)
                try:
                    await self._receipts.completed(execution)
                except Exception:
                    pass  # Keep the dispatch intent and in-memory result.
                self._health.failed(exc, operation="save_result")
                log.warn("manager.result_pending", {"id": execution.id})
                return
            log.error("manager.execution_error", {"id": execution.id, "error": str(exc)})
            execution = await self._mark_execution_failed(execution, str(exc))
        finally:
            if execution.id not in self._pending_completions:
                self.queue.mark_finished(execution.id, expected_started_at=claimed_started_at)
                if should_finish_queue_ref:
                    await TaskStore.finish_queue_ref(execution.id, expected_started_at=claimed_started_at)

        await self._publish_execution_update(execution)
        if execution.status == TaskStatus.FAILED:
            await self._handle_failure(execution)
        if execution.is_terminal:
            await self._receipts.acknowledge(receipt_execution)

    async def _handle_failure(self, execution: TaskExecution) -> None:
        from flocks.task.outcome import hold_for_verification, run_outcome

        # The failure as it was written: a retry or a newer failure that lands
        # while the outcome is worked out wins over this snapshot.
        version = execution.updated_at
        outcome = await run_outcome(execution)
        TaskExecutor.forget_workflow_started(execution.id)
        if not outcome.safe_to_retry:
            # Running it again could repeat what it already did: a person
            # checks, then resumes it in its session or retries it.
            hold_for_verification(execution, outcome)
            if not await TaskStore.update_execution_if_unchanged(execution, expected_updated_at=version):
                log.info("manager.failure_handling_superseded", {"id": execution.id})
                return
            log.warn("manager.execution_held_for_verification", {
                "id": execution.id, "effects": list(outcome.effects)[:20],
            })
            await self._publish_event("task.needs_verification", execution.id, execution.status.value, execution.title)
            return
        if execution.retry.retry_count < execution.retry.max_retries:
            delay = execution.retry.retry_delay_seconds
            execution.retry.retry_count += 1
            execution.retry.retry_after = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            )
            if not await TaskStore.update_execution_if_unchanged(execution, expected_updated_at=version):
                log.info("manager.failure_handling_superseded", {"id": execution.id})
        else:
            await self._publish_event(
                "task.failed",
                execution.id,
                execution.status.value,
                execution.title,
            )

    async def _process_retry_queue(self) -> None:
        retryable = await TaskStore.list_retryable_failed_executions()
        failure = None
        for execution in retryable:
            try:
                execution.retry.retry_after = None
                execution.status = TaskStatus.QUEUED
                execution.queued_at = datetime.now(timezone.utc)
                execution.started_at = None
                execution.completed_at = None
                execution.duration_ms = None
                execution.session_id = None
                await self._enqueue_execution(execution, expected_status=TaskStatus.FAILED)
            except Exception as exc:
                failure = exc
                log.error("manager.record_recovery_error", {"id": execution.id,
                                                           "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None)})
        if failure is not None:
            raise failure

    async def _recover_orphaned_executions(self) -> int:
        """Settle the runs a previous process left running.

        One that may have done part of its work (run a command, sent a
        message) is failed and held for a person instead of being started
        again from the beginning (flocks.task.outcome).
        """
        from flocks.task.outcome import run_outcome

        orphans = await TaskStore.list_executions_by_status(TaskStatus.RUNNING)
        for execution in orphans:
            outcome = await run_outcome(execution)
            TaskExecutor.forget_workflow_started(execution.id)
            if outcome.safe_to_retry:
                await self._requeue_execution(execution)
                continue
            execution = await self._mark_execution_failed(
                execution, self._interrupted_unverified_message(), hold=outcome,
            )
            await TaskStore.finish_queue_ref(execution.id)
        return len(orphans)

    async def _recover_orphaned_queued_executions(self) -> int:
        orphans = await TaskStore.list_orphaned_queued_executions()
        failure = None
        for execution in orphans:
            try:
                await self._requeue_execution(execution)
            except Exception as exc:
                failure = exc
                log.error("manager.record_recovery_error", {"id": execution.id,
                                                           "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None)})
        if failure is not None:
            raise failure
        return len(orphans)

    async def _cleanup_loop(self) -> None:
        delay = _CLEANUP_INTERVAL_S
        while self._running:
            await asyncio.sleep(delay)
            try:
                async with asyncio.timeout(10):
                    expired = await self._expire_stale_executions()
                delay = 1 if expired >= 100 else _CLEANUP_INTERVAL_S
            except Exception as exc:
                delay = 30
                log.error("manager.cleanup_loop_error", {"error": str(exc)})

    async def _expire_stale_executions(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS)
        stale = await TaskStore.list_stale_queued_executions(before=cutoff)
        expired = 0
        failure = None
        for execution in stale:
            try:
                # Only while it still waits: one claimed since it was read runs on.
                if await TaskStore.expire_queued_execution(
                    execution.id,
                    completed_at=datetime.now(timezone.utc),
                    error=f"任务创建后超过 {_TASK_EXPIRY_HOURS} 小时未能执行，已自动取消。",
                ):
                    expired += 1
            except Exception as exc:
                failure = exc
                log.error("manager.record_recovery_error", {"id": execution.id,
                                                           "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None)})
        if failure is not None:
            raise failure
        return expired

    async def _recover_stale_active_executions(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RUNNING_RECOVERY_TIMEOUT_S)
        stale = await TaskStore.list_stale_running_executions(before=cutoff)
        recovered = 0
        failure = None
        for execution in stale:
            try:
                if execution.id in self._pending_completions:
                    continue
                from flocks.task.outcome import run_outcome

                await self._cancel_execution_runtime(execution)
                outcome = await run_outcome(execution)
                TaskExecutor.forget_workflow_started(execution.id)
                execution = await self._mark_execution_failed(
                    execution,
                    self._stale_execution_message(),
                    hold=None if outcome.safe_to_retry else outcome,
                )
                self.queue.mark_finished(execution.id)
                await TaskStore.finish_queue_ref(execution.id)
                recovered += 1
            except Exception as exc:
                failure = exc
                log.error("manager.record_recovery_error", {"id": execution.id,
                                                           "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None)})
        if failure is not None:
            raise failure
        if recovered:
            log.warn(
                "manager.stale_active_recovered",
                {"count": recovered, "timeout_s": _RUNNING_RECOVERY_TIMEOUT_S},
            )
        return recovered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _migration_state_path() -> Path:
        return Storage.get_db_path().with_name("task_migration_state.json")

    @classmethod
    def _read_migration_state(cls) -> Dict[str, Any]:
        path = cls._migration_state_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @classmethod
    def _write_migration_state(cls, state: Dict[str, Any]) -> None:
        path = cls._migration_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")

    @classmethod
    def _clear_migration_state(cls) -> None:
        path = cls._migration_state_path()
        if path.exists():
            path.unlink()

    @staticmethod
    def _with_db_connection() -> sqlite3.Connection:
        return Storage.connect_sync()

    @staticmethod
    def _legacy_table_db_paths() -> list[Path]:
        paths = [Storage.get_db_path(), TaskStore.get_db_path()]
        unique: list[Path] = []
        for path in paths:
            if path not in unique:
                unique.append(path)
        return unique

    @classmethod
    def _legacy_tables_exist(cls) -> bool:
        for db_path in cls._legacy_table_db_paths():
            if not db_path.exists():
                continue
            with Storage.connect_sync(db_path) as conn:
                for table_name in ("tasks", "task_execution_records", "task_queue_refs"):
                    row = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                        (table_name,),
                    ).fetchone()
                    if row is not None:
                        return True
        return False

    @classmethod
    async def _enqueue_execution(
        cls, execution: TaskExecution, *, expected_status: Optional[TaskStatus] = None,
    ) -> TaskExecution:
        return await cls._requeue_execution(execution, expected_status=expected_status)

    @classmethod
    async def _requeue_execution(
        cls,
        execution: TaskExecution,
        *,
        keep_session: bool = False,
        expected_status: Optional[TaskStatus] = None,
    ) -> TaskExecution:
        """Queue *execution* again while the row is still as it was read.

        *expected_status* is the status it was read in when the caller already
        changed it on the object (the object's ``updated_at`` is still the
        read one).  One that moved on meanwhile (claimed, cancelled, retried
        elsewhere, failed again) is returned as it is now, and nothing is written.
        """
        expected = execution.status if expected_status is None else expected_status
        version = execution.updated_at
        execution.status = TaskStatus.QUEUED
        execution.queued_at = datetime.now(timezone.utc)
        execution.started_at = None
        execution.completed_at = None
        execution.duration_ms = None
        if not keep_session:
            execution.session_id = None
        execution.result_summary = None
        execution.error = None
        if await TaskStore.requeue_execution(execution, expected_status=expected, expected_updated_at=version):
            return execution
        log.info("manager.requeue_skipped", {"id": execution.id, "expected": expected.value})
        return await TaskStore.get_execution(execution.id) or execution

    @staticmethod
    def _default_workspace_directory(execution_id: str) -> str:
        # Named after the execution, not the clock: executions started in the
        # same second used to share one directory and overwrite each other's
        # files. Same layout as TaskExecutor's fallback for unset directories.
        from flocks.workspace.manager import WorkspaceManager

        workspace_root = WorkspaceManager.get_instance().get_workspace_dir()
        today = date.today().isoformat()
        return str(workspace_root / "tasks" / today / execution_id)

    @staticmethod
    def _dispatch_timeout_message() -> str:
        return (
            "Task execution exceeded the hard timeout "
            f"({_DISPATCH_GUARD_TIMEOUT_S}s) and was cancelled to unblock the queue."
        )

    @staticmethod
    def _interrupted_unverified_message() -> str:
        return (
            "Task execution was interrupted after starting actions whose effects were not "
            "confirmed; it is not started again automatically. Check them, then resume or retry it."
        )

    @staticmethod
    def _stale_execution_message() -> str:
        return (
            "Task execution was still marked running past the recovery threshold "
            f"({_RUNNING_RECOVERY_TIMEOUT_S}s) and was failed automatically "
            "to unblock scheduled tasks."
        )

    @classmethod
    async def _cancel_execution_runtime(cls, execution: TaskExecution) -> None:
        latest = await TaskStore.get_execution(execution.id)
        if latest is not None:
            execution.execution_mode = latest.execution_mode
            execution.session_id = execution.session_id or latest.session_id
            execution.started_at = execution.started_at or latest.started_at

        try:
            TaskExecutor.request_runtime_cancel(execution)
        except Exception:
            pass

        session_id = execution.session_id
        if not session_id:
            return

        try:
            from flocks.task.background import cancel_background_runs_for_session

            # The run lives in the manager of the task loop, not of the
            # request that cancels it.
            cancel_background_runs_for_session(session_id)
        except Exception:
            pass

        try:
            from flocks.server.routes.question import reject_session_questions

            await reject_session_questions(session_id)
        except Exception:
            pass

    @classmethod
    async def _mark_execution_failed(
        cls,
        execution: TaskExecution,
        error: str,
        *,
        hold: Any = None,
    ) -> TaskExecution:
        """Fail the run; *hold* (a flocks.task.outcome.RunOutcome) keeps it from automatic retries."""
        current = await TaskStore.get_execution(execution.id)
        if current is not None:
            if execution.started_at is not None and current.started_at != execution.started_at:
                return current
            execution = current
            if execution.is_terminal:
                return execution
        version = execution.updated_at
        if hold is not None:
            from flocks.task.outcome import hold_for_verification

            hold_for_verification(execution, hold)

        completed_at = datetime.now(timezone.utc)
        execution.status = TaskStatus.FAILED
        execution.error = error
        execution.completed_at = completed_at
        if execution.started_at:
            execution.duration_ms = int(
                (completed_at - execution.started_at).total_seconds() * 1000
            )
        if await TaskStore.update_execution_if_unchanged(execution, expected_updated_at=version):
            return execution
        # It ended (or was cancelled) after it was read: that end stands.
        return await TaskStore.get_execution(execution.id) or execution

    @classmethod
    async def _publish_execution_update(cls, execution: TaskExecution) -> None:
        try:
            from flocks.server.routes.event import publish_event

            await publish_event(
                "task.updated",
                {
                    "executionID": execution.id,
                    "schedulerID": execution.scheduler_id,
                    "status": execution.status.value,
                    "sessionID": execution.session_id,
                },
            )
        except Exception as exc:
            log.warn("manager.sse_notify_error", {"execution_id": execution.id, "error": str(exc)})

    @classmethod
    async def _publish_event(
        cls, event_type: str, entity_id: str, status: str, title: str
    ) -> None:
        try:
            from flocks.bus.bus import Bus
            from flocks.bus.bus_event import BusEvent

            evt = BusEvent.define(event_type, _TaskEventProps)
            await Bus.publish(
                evt,
                {
                    "task_id": entity_id,
                    "status": status,
                    "title": title,
                },
            )
        except Exception as exc:
            log.warn("manager.event_publish_error", {"error": str(exc)})
