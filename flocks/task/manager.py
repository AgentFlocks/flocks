"""Task Manager for scheduler/execution domain."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as _BaseModel

from flocks.utils.log import Log

from .executor import _TASK_ABSOLUTE_TIMEOUT_S, TaskExecutor
from .lease import ExecutionOwnershipError
from .node import process_owner_id, heartbeat_seconds, lease_seconds
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
        recovered = await mgr._recover_orphaned_executions()
        if recovered:
            log.info("manager.orphan_recovery", {"count": recovered})
        recovered_queued = await mgr._recover_orphaned_queued_executions()
        if recovered_queued:
            log.info("manager.orphaned_queue_recovery", {"count": recovered_queued})

        mgr._running = True
        mgr._loop_task = asyncio.create_task(mgr._execution_loop())
        mgr._cleanup_task = asyncio.create_task(mgr._cleanup_loop())
        await mgr.scheduler.start()
        cls._instance = mgr
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
            if hasattr(scheduler, key):
                setattr(scheduler, key, value)
        if any(v is not None for v in [cron, timezone, cron_description, run_once, run_at]):
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
        execution = cls.build_execution(scheduler, trigger_type=trigger_type)
        await TaskStore.create_execution(execution)
        if enqueue:
            await cls._enqueue_execution(execution)
        return execution

    @classmethod
    def build_execution(
        cls, scheduler: TaskScheduler, *, trigger_type: ExecutionTriggerType
    ) -> TaskExecution:
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
        return await cls._enqueue_execution(execution)

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
        return await TaskStore.requeue_execution(
            execution, expected_generation=execution.owner_generation,
            expected_status=TaskStatus.FAILED, keep_session=True,
        )

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
            if execution.lease_is_live(now=now):
                # Someone is still renewing this claim; it is running, not stuck.
                continue
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
        # Legacy-file import is an explicit offline migration.
        return None

    @classmethod
    def pause_queue(cls) -> None:
        mgr = cls._instance
        if mgr:
            mgr.queue.pause()

    @classmethod
    def resume_queue(cls) -> None:
        mgr = cls._instance
        if mgr:
            mgr.queue.resume()

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _execution_loop(self) -> None:
        import time

        from flocks.db.health import execution_allowed

        while self._running:
            try:
                if not execution_allowed():
                    # The database is down, a standby, or only just back:
                    # work started now would fail half-way. Leases already keep
                    # two nodes off one run; this keeps a node off all of them.
                    await asyncio.sleep(self._poll_interval)
                    continue
                now = time.monotonic()
                if now - self._last_retry_check >= _RETRY_CHECK_INTERVAL_S:
                    self._last_retry_check = now
                    await self._process_retry_queue()
                if now - self._last_stale_recovery_check >= _STALE_RECOVERY_INTERVAL_S:
                    self._last_stale_recovery_check = now
                    await self._recover_stale_active_executions()
                if (
                    now - self._last_orphaned_queue_recovery_check
                    >= _ORPHANED_QUEUE_RECOVERY_INTERVAL_S
                ):
                    self._last_orphaned_queue_recovery_check = now
                    await self._recover_orphaned_queued_executions()
                # Fill every free slot in one pass: one execution per poll kept
                # the rest of a burst queued for up to
                # (max_concurrent - 1) * poll_interval seconds.
                while True:
                    execution = await self.queue.dequeue()
                    if not execution:
                        break
                    task = asyncio.create_task(self._run_execution(execution))
                    task.add_done_callback(self._on_task_done)
            except Exception as exc:
                log.error("manager.loop_error", {"error": str(exc)})
            await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _on_task_done(fut: asyncio.Task) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            log.error("manager.task_unhandled", {"error": str(exc)})

    async def _run_execution(self, execution: TaskExecution) -> None:
        scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
        should_finish_queue_ref = True
        try:
            execution = await asyncio.wait_for(
                TaskExecutor.dispatch(execution, scheduler),
                timeout=_DISPATCH_GUARD_TIMEOUT_S,
            )
        except ExecutionOwnershipError as exc:
            # Only release our local concurrency slot. The peer owns the work
            # and its queue reference, including any failure/retry handling.
            should_finish_queue_ref = False
            log.info("manager.execution_owned_elsewhere", {"id": execution.id, "reason": str(exc)})
            return
        except asyncio.CancelledError:
            current = await TaskStore.get_execution(execution.id)
            if not self._same_dispatch_owner(execution, current):
                should_finish_queue_ref = False
                raise
            generation = execution.owner_generation
            if current is not None:
                execution = current
            if not execution.is_terminal:
                from flocks.task.outcome import run_outcome

                outcome = await run_outcome(execution)
                TaskExecutor.forget_workflow_started(execution.id)
                if outcome.safe_to_retry:
                    should_finish_queue_ref = False
                    try:
                        execution = await self._requeue_execution(execution, expected_generation=generation)
                    except ExecutionOwnershipError:
                        pass
                    else:
                        await self._publish_execution_update(execution)
                else:
                    # Interrupted (a shutdown) after starting work whose
                    # effects nobody confirmed: not started over by itself.
                    try:
                        execution = await self._mark_execution_failed(
                            execution, self._interrupted_unverified_message(),
                            expected_generation=generation, hold=outcome,
                        )
                    except ExecutionOwnershipError:
                        should_finish_queue_ref = False
                    else:
                        await self._publish_execution_update(execution)
            raise
        except asyncio.TimeoutError:
            current = await TaskStore.get_execution(execution.id)
            if not self._same_dispatch_owner(execution, current):
                should_finish_queue_ref = False
                return
            await self._cancel_execution_runtime(execution)
            log.error(
                "manager.execution_timeout",
                {"id": execution.id, "timeout_s": _DISPATCH_GUARD_TIMEOUT_S},
            )
            try:
                execution = await self._mark_execution_failed(
                    execution,
                    self._dispatch_timeout_message(),
                    expected_generation=execution.owner_generation,
                )
            except ExecutionOwnershipError:
                should_finish_queue_ref = False
                return
        except Exception as exc:
            current = await TaskStore.get_execution(execution.id)
            if not self._same_dispatch_owner(execution, current):
                should_finish_queue_ref = False
                return
            log.error("manager.execution_error", {"id": execution.id, "error": str(exc)})
            try:
                execution = await self._mark_execution_failed(
                    execution, str(exc), expected_generation=execution.owner_generation,
                )
            except ExecutionOwnershipError:
                should_finish_queue_ref = False
                return
        finally:
            self.queue.mark_finished(execution.id)
            if should_finish_queue_ref:
                await TaskStore.finish_queue_ref(execution.id, expected_generation=execution.owner_generation)

        await self._publish_execution_update(execution)
        if execution.status == TaskStatus.FAILED:
            await self._handle_failure(execution)

    @staticmethod
    def _same_dispatch_owner(execution: TaskExecution, current: Optional[TaskExecution]) -> bool:
        # Exiting ExecutionLease releases the owner before cancellation reaches
        # the manager. Its generation remains and still fences a later claim.
        return current is not None and (
            current.owner_generation == execution.owner_generation
            and current.owner_node_id in (None, execution.owner_node_id)
        )

    async def _handle_failure(self, execution: TaskExecution) -> None:
        from flocks.task.outcome import hold_for_verification, run_outcome

        outcome = await run_outcome(execution)
        TaskExecutor.forget_workflow_started(execution.id)
        if not outcome.safe_to_retry:
            # Running it again could repeat what it already did: a person
            # checks, then resumes it in its session or retries it.
            hold_for_verification(execution, outcome)
            await TaskStore.update_execution(execution)
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
            await TaskStore.update_execution(execution)
        else:
            await self._publish_event(
                "task.failed",
                execution.id,
                execution.status.value,
                execution.title,
            )

    async def _process_retry_queue(self) -> None:
        retryable = await TaskStore.list_retryable_failed_executions()
        for execution in retryable:
            execution.retry.retry_after = None
            try:
                await self._requeue_execution(
                    execution, expected_generation=execution.owner_generation,
                    expected_status=TaskStatus.FAILED,
                )
            except ExecutionOwnershipError:
                continue

    async def _recover_orphaned_executions(self) -> int:
        """Settle expired work at startup, leaving every live lease alone.

        Every ``running`` execution used to be treated as this node's leftover.
        With two instances on one database that is wrong in the dangerous
        direction: restarting one node put the other node's in-progress
        executions back in the queue, and the same task ran twice. Only expiry
        or an old unclaimed row is evidence that execution was abandoned.

        An abandoned execution is failed, not run again from the start: it may
        have done part of its work (sent a message, written a record), and
        whether it did is unknown. Running it again is a decision for its
        retry policy (the retry queue honours one) or for a person -- the same
        outcome the periodic recovery reaches for the same evidence.
        """
        return await self._recover_stale_active_executions()

    async def _recover_orphaned_queued_executions(self) -> int:
        orphans = await TaskStore.list_orphaned_queued_executions()
        recovered = 0
        for execution in orphans:
            try:
                await self._requeue_execution(
                    execution, expected_generation=execution.owner_generation,
                    expected_status=TaskStatus.QUEUED,
                )
                recovered += 1
            except ExecutionOwnershipError:
                continue
        return recovered

    async def _cleanup_loop(self) -> None:
        while self._running:
            await asyncio.sleep(_CLEANUP_INTERVAL_S)
            try:
                await self._expire_stale_executions()
            except Exception as exc:
                log.error("manager.cleanup_loop_error", {"error": str(exc)})

    async def _expire_stale_executions(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS)
        stale = await TaskStore.list_stale_queued_executions(before=cutoff)
        expired = 0
        for execution in stale:
            previous_status = execution.status
            execution.status = TaskStatus.CANCELLED
            execution.completed_at = datetime.now(timezone.utc)
            execution.error = (
                f"任务创建后超过 {_TASK_EXPIRY_HOURS} 小时未能执行，已自动取消。"
            )
            try:
                await TaskStore.update_execution(
                    execution, expected_generation=execution.owner_generation,
                    expected_status=previous_status,
                )
            except ExecutionOwnershipError:
                continue
            await TaskStore.finish_queue_ref(execution.id, expected_generation=execution.owner_generation)
            expired += 1
        return expired

    async def _recover_stale_active_executions(self) -> int:
        """Fail expired executions, leaving every live lease alone.

        Age alone does not mean abandoned once two instances share one database:
        a long-running execution on the other node looks exactly like a stale
        one from here. Lease expiry decides, so restarting this node cannot
        cancel work its peer is still running.
        """
        node_id = process_owner_id()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RUNNING_RECOVERY_TIMEOUT_S)
        stale = await TaskStore.list_reclaimable_executions(
            node_id=node_id,
            stale_before=cutoff,
            # This loop runs next to this node's own live executions; only a
            # claim nobody renews (or an unclaimed old row) is abandoned.
            include_own_live=False,
        )
        recovered = 0
        for execution in stale:
            claimed = await TaskStore.claim_execution(
                execution.id, node_id=node_id, lease_seconds=lease_seconds(),
                expected_generation=execution.owner_generation, allow_own_live=False,
            )
            if claimed is None:
                continue
            try:
                from flocks.task.outcome import run_outcome

                await self._cancel_execution_runtime(claimed, expected_generation=claimed.owner_generation)
                outcome = await run_outcome(claimed)
                await self._mark_execution_failed(
                    claimed, self._stale_execution_message(), expected_generation=claimed.owner_generation,
                    hold=None if outcome.safe_to_retry else outcome,
                )
                self.queue.mark_finished(claimed.id)
                await TaskStore.finish_queue_ref(claimed.id, expected_generation=claimed.owner_generation)
                recovered += 1
            except ExecutionOwnershipError:
                continue
            finally:
                await TaskStore.release_execution_lease(
                    claimed.id, node_id=node_id, expected_generation=claimed.owner_generation,
                )
        if recovered:
            log.warn(
                "manager.stale_active_recovered",
                {
                    "count": recovered,
                    "timeout_s": _RUNNING_RECOVERY_TIMEOUT_S,
                    "node_id": node_id,
                },
            )
        return recovered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    async def _enqueue_execution(cls, execution: TaskExecution) -> TaskExecution:
        return await cls._requeue_execution(execution)

    @classmethod
    async def _requeue_execution(
        cls, execution: TaskExecution, *, expected_generation: Optional[int] = None,
        expected_status: Optional[TaskStatus] = None,
    ) -> TaskExecution:
        return await TaskStore.requeue_execution(
            execution, expected_generation=expected_generation, expected_status=expected_status,
        )

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
            "Task execution was still marked running with no live owner "
            f"(recovery threshold {_RUNNING_RECOVERY_TIMEOUT_S}s) and was failed "
            "automatically to unblock scheduled tasks."
        )

    @classmethod
    async def _cancel_execution_runtime(
        cls, execution: TaskExecution, *, expected_generation: Optional[int] = None,
    ) -> None:
        latest = await TaskStore.get_execution(execution.id)
        if expected_generation is not None and (
            latest is None or latest.owner_generation != expected_generation
            or latest.owner_node_id != execution.owner_node_id
        ):
            raise ExecutionOwnershipError(f"execution ownership superseded: {execution.id}")
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
        expected_generation: Optional[int] = None,
        hold: Any = None,
    ) -> TaskExecution:
        """Fail the run; *hold* (a flocks.task.outcome.RunOutcome) keeps it from automatic retries."""
        current = await TaskStore.get_execution(execution.id)
        if current is not None:
            if expected_generation is not None and current.owner_generation != expected_generation:
                raise ExecutionOwnershipError(f"execution ownership superseded: {execution.id}")
            execution = current
            if execution.is_terminal:
                return execution
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
        return await TaskStore.update_execution(execution, expected_generation=expected_generation)

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
