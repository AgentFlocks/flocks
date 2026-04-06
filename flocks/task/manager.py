"""
Task Manager

Unified entry point that coordinates Store, Queue, Scheduler, and Executor.
Owns the background execution loop that polls the queue.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as _BaseModel

from flocks.utils.log import Log

from .executor import TaskExecutor
from .models import (
    DeliveryStatus,
    ExecutionMode,
    QueueTaskItem,
    RetryConfig,
    Task,
    TaskExecution,
    TaskExecutionRecord,
    TaskPriority,
    TaskSchedule,
    TaskSource,
    TaskStatus,
    TaskType,
)
from .queue import TaskQueue
from .scheduler import TaskScheduler
from .store import TaskStore

log = Log.create(service="task.manager")

# Tasks in QUEUED/PENDING state for longer than this are auto-cancelled by _cleanup_loop.
_TASK_EXPIRY_HOURS: int = 24
# How often (seconds) the cleanup loop runs.
_CLEANUP_INTERVAL_S: int = 3600  # 1 hour
# How often (seconds) the retry-queue check runs inside the execution loop.
_RETRY_CHECK_INTERVAL_S: int = 30


class _TaskEventProps(_BaseModel):
    task_id: str
    status: str
    title: str


class TaskManager:
    """
    Singleton façade for the Task Center.

    Lifecycle:
      await TaskManager.start()   # called during app startup
      await TaskManager.stop()    # called during app shutdown
    """

    _instance: Optional["TaskManager"] = None
    _startup_error: Optional[str] = None

    def __init__(
        self,
        *,
        max_concurrent: int = 1,
        poll_interval: int = 5,
        scheduler_interval: int = 30,
        default_retry: Optional[RetryConfig] = None,
    ):
        self.queue = TaskQueue(max_concurrent=max_concurrent)
        self.scheduler = TaskScheduler(check_interval=scheduler_interval)
        self._poll_interval = poll_interval
        self._default_retry = default_retry or RetryConfig()
        self._loop_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_retry_check: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    async def start(
        cls,
        *,
        max_concurrent: int = 1,
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

        # Recover orphaned RUNNING tasks left over from a previous crash/restart
        # before starting the execution loop so the running-slot counter is clean.
        recovered = await mgr._recover_orphaned_tasks()
        if recovered:
            log.info("manager.orphan_recovery", {"count": recovered})

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
            t = getattr(mgr, task_attr, None)
            if t:
                t.cancel()
                try:
                    await t
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
    # Task CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def create_task(
        cls,
        *,
        title: str,
        description: str = "",
        task_type: TaskType = TaskType.QUEUED,
        priority: TaskPriority = TaskPriority.NORMAL,
        source: Optional[TaskSource] = None,
        schedule: Optional[TaskSchedule] = None,
        execution_mode: ExecutionMode = ExecutionMode.AGENT,
        agent_name: str = "rex",
        workflow_id: Optional[str] = None,
        skills: Optional[List[str]] = None,
        category: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        workspace_directory: Optional[str] = None,
        tags: Optional[List[str]] = None,
        created_by: str = "rex",
    ) -> Task:
        mgr = cls._instance
        retry = mgr._default_retry if mgr else RetryConfig()

        if task_type == TaskType.SCHEDULED and schedule:
            next_run = TaskScheduler.compute_next_run(
                schedule.cron, schedule.timezone
            )
            schedule.next_run = next_run

        task = Task(
            title=title,
            description=description,
            type=task_type,
            status=TaskStatus.PENDING,
            priority=priority,
            source=source or TaskSource(),
            schedule=schedule,
            execution=TaskExecution(agent=agent_name),
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
        )
        if not task.workspace_directory:
            task.workspace_directory = cls._default_workspace_directory(task.id)
        created = await TaskStore.create_task(task)
        if created is None:
            # dedup_key collision — return the existing active task instead
            existing = await TaskStore.get_active_by_dedup_key(task.dedup_key) if task.dedup_key else None
            log.warn("manager.task_creation_deduped", {"title": title, "dedup_key": task.dedup_key})
            return existing or task
        task = created

        if task_type == TaskType.QUEUED:
            await cls._enqueue(task)

        await cls._publish_event("task.created", task)
        return task

    @classmethod
    async def get_task(cls, task_id: str) -> Optional[Task]:
        return await TaskStore.get_task(task_id)

    @classmethod
    async def list_tasks(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        task_type: Optional[TaskType] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[Task], int]:
        return await TaskStore.list_tasks(
            status=status,
            task_type=task_type,
            priority=priority,
            delivery_status=delivery_status,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
        )

    @classmethod
    async def list_queue_items(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[QueueTaskItem], int]:
        return await TaskStore.list_queue_items(
            status=status,
            priority=priority,
            delivery_status=delivery_status,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
            limit=limit,
        )

    @classmethod
    async def update_task(cls, task_id: str, **fields: Any) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task:
            return None
        for key, val in fields.items():
            if hasattr(task, key):
                setattr(task, key, val)
        task = await TaskStore.update_task(task)
        await cls._publish_event("task.status", task)
        return task

    @classmethod
    async def update_task_with_schedule(
        cls,
        task_id: str,
        *,
        fields: Dict[str, Any],
        cron: Optional[str] = None,
        timezone: Optional[str] = None,
        cron_description: Optional[str] = None,
        run_once: Optional[bool] = None,
        run_at: Optional[str] = None,
        user_prompt: Optional[str] = None,
    ) -> Optional[Task]:
        """Update a task, merging schedule and source changes with existing values.

        Raises ValueError on invalid schedule parameters.
        """
        schedule_touched = any(
            v is not None for v in [cron, cron_description, run_once, run_at]
        )
        needs_existing = schedule_touched or user_prompt is not None

        existing = None
        if needs_existing:
            existing = await TaskStore.get_task(task_id)
            if not existing:
                return None

        if schedule_touched:
            from datetime import datetime as _dt

            old_sched = existing.schedule
            enabled = old_sched.enabled if old_sched else True
            new_tz = timezone or (
                old_sched.timezone if old_sched else "Asia/Shanghai"
            )
            new_desc = (
                cron_description
                if cron_description is not None
                else (old_sched.cron_description if old_sched else None)
            )
            is_run_once = (
                run_once
                if run_once is not None
                else (old_sched.run_once if old_sched else False)
            )

            if is_run_once:
                run_at_dt = None
                if run_at:
                    try:
                        run_at_dt = _dt.fromisoformat(run_at)
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            f"Invalid run_at format: {run_at!r}"
                        ) from exc
                elif old_sched and old_sched.run_at:
                    run_at_dt = old_sched.run_at
                fields["schedule"] = TaskSchedule(
                    cron=cron or (old_sched.cron if old_sched else None),
                    timezone=new_tz,
                    cron_description=new_desc,
                    run_once=True,
                    run_at=run_at_dt,
                    enabled=enabled,
                )
            else:
                new_cron = cron or (old_sched.cron if old_sched else None)
                if not new_cron:
                    raise ValueError(
                        "cron is required for recurring scheduled tasks"
                    )
                next_run = TaskScheduler.compute_next_run(new_cron, new_tz)
                fields["schedule"] = TaskSchedule(
                    cron=new_cron,
                    timezone=new_tz,
                    next_run=next_run,
                    enabled=enabled,
                    cron_description=new_desc,
                )

        if user_prompt is not None:
            src = existing.source or TaskSource()
            fields["source"] = TaskSource(
                source_type=src.source_type,
                user_prompt=user_prompt,
            )

        return await cls.update_task(task_id, **fields)

    @classmethod
    async def delete_task(cls, task_id: str) -> bool:
        # Built-in tasks (seeded from YAML plugins) are soft-deleted by
        # setting status to CANCELLED instead of physically removing the row.
        # This ensures the seed logic detects the user's intent on restart
        # and does not recreate the task.
        task = await TaskStore.get_task(task_id)
        if task and task.dedup_key and task.dedup_key.startswith("builtin:"):
            if not task.is_terminal:
                task.status = TaskStatus.CANCELLED
                await TaskStore.update_task(task)
                mgr = cls._instance
                if mgr:
                    mgr.queue.mark_finished(task_id)
                await cls._publish_event("task.cancelled", task)
            return True
        return await TaskStore.delete_task(task_id)

    @classmethod
    async def cancel_queue_item(cls, item_id: str) -> bool:
        task = await TaskStore.get_task(item_id)
        if task is not None:
            return (await cls.cancel_task(item_id)) is not None

        record = await TaskStore.get_record(item_id)
        if record is None:
            return False

        task = await TaskStore.get_task(record.task_id)
        if task is None:
            return False

        await cls._cancel_scheduled_execution(task, record)
        return True

    @classmethod
    async def rerun_queue_item(cls, item_id: str) -> bool:
        task = await TaskStore.get_task(item_id)
        if task is not None:
            return (await cls.rerun_task(item_id)) is not None

        record = await TaskStore.get_record(item_id)
        if record is None:
            return False

        task = await TaskStore.get_task(record.task_id)
        if task is None:
            return False

        if record.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PAUSED):
            await cls._cancel_scheduled_execution(task, record)

        await cls._enqueue_scheduled_execution(task)
        return True

    @classmethod
    async def delete_queue_item(cls, item_id: str) -> bool:
        task = await TaskStore.get_task(item_id)
        if task is not None:
            return await cls.delete_task(item_id)

        record = await TaskStore.get_record(item_id)
        if record is None:
            return False

        task = await TaskStore.get_task(record.task_id)
        if task and record.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PAUSED):
            await cls._cancel_scheduled_execution(task, record)
        return await TaskStore.delete_record(item_id)

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    @classmethod
    async def cancel_task(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or task.is_terminal:
            return task
        task.status = TaskStatus.CANCELLED
        task = await TaskStore.update_task(task)
        await TaskStore.finish_queue_ref(task_id)
        mgr = cls._instance
        if mgr:
            mgr.queue.mark_finished(task_id)
        await cls._publish_event("task.cancelled", task)
        return task

    @classmethod
    async def pause_task(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return task
        task.status = TaskStatus.PAUSED
        task = await TaskStore.update_task(task)
        await cls._publish_event("task.status", task)
        return task

    @classmethod
    async def resume_task(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return task
        task.status = TaskStatus.QUEUED
        task.execution = TaskExecution(agent=task.agent_name)
        task = await cls._enqueue(task)
        await cls._publish_event("task.status", task)
        return task

    @classmethod
    async def retry_task(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or task.status != TaskStatus.FAILED:
            return task
        if task.retry:
            task.retry.retry_count += 1
        task.status = TaskStatus.QUEUED
        task.execution = TaskExecution(agent=task.agent_name)
        task = await cls._enqueue(task)
        await cls._publish_event("task.status", task)
        return task

    @classmethod
    async def rerun_task(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task:
            return None
        if task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.CANCELLED
            task = await TaskStore.update_task(task)
            mgr = cls._instance
            if mgr:
                mgr.queue.mark_finished(task_id)
            await TaskStore.finish_queue_ref(task_id)
            # Also cancel the running agent so it stops consuming resources
            # and so the executor's final status write is pre-empted correctly.
            session_id = task.execution.session_id if task.execution else None
            if session_id:
                try:
                    from flocks.task.background import get_background_manager
                    get_background_manager().cancel_by_session_id(session_id)
                except Exception:
                    pass  # best-effort; executor.py's overwrite guard handles the rest
        task.status = TaskStatus.QUEUED
        task.execution = TaskExecution(agent=task.agent_name)
        task = await cls._enqueue(task)
        await cls._publish_event("task.status", task)
        return task

    @classmethod
    async def batch_cancel(cls, task_ids: List[str]) -> int:
        count = await TaskStore.batch_update_status(task_ids, TaskStatus.CANCELLED)
        for task_id in task_ids:
            await TaskStore.finish_queue_ref(task_id)
        return count

    @classmethod
    async def batch_delete(cls, task_ids: List[str]) -> int:
        if not task_ids:
            return 0
        # Separate built-in tasks (soft-delete) from regular tasks (hard-delete).
        builtin_ids: List[str] = []
        regular_ids: List[str] = []
        for tid in task_ids:
            task = await TaskStore.get_task(tid)
            if task and task.dedup_key and task.dedup_key.startswith("builtin:"):
                builtin_ids.append(tid)
            else:
                regular_ids.append(tid)
        count = 0
        if builtin_ids:
            count += await TaskStore.batch_update_status(builtin_ids, TaskStatus.CANCELLED)
        if regular_ids:
            count += await TaskStore.batch_delete(regular_ids)
        return count

    # ------------------------------------------------------------------
    # Dashboard & query helpers
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
            return {"paused": False, "max_concurrent": 1, "running": 0, "queued": 0}
        return await mgr.queue.status()

    @classmethod
    async def get_unviewed_results(cls) -> List[Task]:
        return await TaskStore.get_unviewed_results()

    @classmethod
    async def mark_viewed(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if task:
            task.delivery_status = DeliveryStatus.VIEWED
            task = await TaskStore.update_task(task)
        return task

    @classmethod
    async def mark_notified(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if task and task.delivery_status == DeliveryStatus.UNREAD:
            task.delivery_status = DeliveryStatus.NOTIFIED
            task = await TaskStore.update_task(task)
        return task

    # ------------------------------------------------------------------
    # Scheduled-task helpers
    # ------------------------------------------------------------------

    @classmethod
    async def list_scheduled(cls) -> List[Task]:
        return await TaskStore.get_scheduled_tasks(enabled_only=False)

    @classmethod
    async def enable_scheduled(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or not task.schedule:
            return task
        task.schedule.enabled = True
        next_run = TaskScheduler.compute_next_run(
            task.schedule.cron, task.schedule.timezone
        )
        task.schedule.next_run = next_run
        return await TaskStore.update_task(task)

    @classmethod
    async def disable_scheduled(cls, task_id: str) -> Optional[Task]:
        task = await TaskStore.get_task(task_id)
        if not task or not task.schedule:
            return task
        task.schedule.enabled = False
        return await TaskStore.update_task(task)

    @classmethod
    async def list_records(
        cls, task_id: str, *, limit: int = 5, offset: int = 0
    ) -> tuple[List[TaskExecutionRecord], int]:
        return await TaskStore.list_records(task_id, limit=limit, offset=offset)

    # Queue-level controls
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
    # Background execution loop
    # ------------------------------------------------------------------

    async def _execution_loop(self) -> None:
        """Poll queue every N seconds and dispatch tasks."""
        import time

        while self._running:
            try:
                now = time.monotonic()
                if now - self._last_retry_check >= _RETRY_CHECK_INTERVAL_S:
                    self._last_retry_check = now
                    await self._process_retry_queue()

                task = await self.queue.dequeue()
                if task:
                    t = asyncio.create_task(self._run_task(task))
                    t.add_done_callback(self._on_task_done)
            except Exception as e:
                log.error("manager.loop_error", {"error": str(e)})
            await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _on_task_done(fut: asyncio.Task) -> None:
        """Log any unexpected exception from a fire-and-forget task."""
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            log.error("manager.task_unhandled", {"error": str(exc)})

    async def _run_task(self, task: Task) -> None:
        try:
            task = await TaskExecutor.dispatch(task)
        except Exception as e:
            log.error("manager.task_error", {"id": task.id, "error": str(e)})
            task.status = TaskStatus.FAILED
            if task.execution:
                task.execution.error = str(e)
            await TaskStore.update_task(task)
        finally:
            self.queue.mark_finished(task.id)
            await TaskStore.finish_queue_ref(task.id)

        await self._sync_execution_record(task)

        # Notify frontend via SSE immediately on terminal state.
        # _publish_event goes to Bus (internal), publish_event goes to SSE (frontend).
        try:
            from flocks.server.routes.event import publish_event
            await publish_event("task.updated", {
                "taskID": task.id,
                "status": task.status.value,
                "sessionID": task.execution.session_id if task.execution else None,
            })
        except Exception as sse_exc:
            log.warn("manager.sse_notify_error", {"task_id": task.id, "error": str(sse_exc)})

        if task.status == TaskStatus.FAILED:
            await self._handle_failure(task)
        elif task.status == TaskStatus.COMPLETED:
            await self._publish_event("task.completed", task)

    @staticmethod
    async def _sync_execution_record(task: Task) -> None:
        """Update the parent scheduled-task execution record if one exists."""
        record_id = (task.context or {}).get("_execution_record_id")
        if not record_id:
            return
        try:
            exec_info = task.execution
            now = datetime.now(timezone.utc)
            record = TaskExecutionRecord(
                id=record_id,
                task_id="",  # not used by UPDATE query
                status=task.status,
                started_at=exec_info.started_at if exec_info else None,
                completed_at=exec_info.completed_at if exec_info else now,
                duration_ms=exec_info.duration_ms if exec_info else None,
                result_summary=exec_info.result_summary if exec_info else None,
                error=exec_info.error if exec_info else None,
                session_id=exec_info.session_id if exec_info else None,
            )
            await TaskStore.update_record(record)
        except Exception as e:
            log.warn("manager.record_sync_error", {"record_id": record_id, "error": str(e)})

    async def _handle_failure(self, task: Task) -> None:
        """Persist retry_after timestamp instead of blocking with asyncio.sleep,
        so retries survive process restarts."""
        if task.retry and task.retry.retry_count < task.retry.max_retries:
            delay = task.retry.retry_delay_seconds
            task.retry.retry_count += 1
            task.retry.retry_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await TaskStore.update_task(task)
            log.info("manager.retry_scheduled", {
                "id": task.id,
                "attempt": task.retry.retry_count,
                "retry_after": task.retry.retry_after.isoformat(),
            })
        else:
            await self._publish_event("task.failed", task)

    async def _process_retry_queue(self) -> None:
        """Re-enqueue FAILED tasks whose retry_after timestamp has passed."""
        try:
            retryable = await TaskStore.list_retryable_failed()
            for task in retryable:
                task.retry.retry_after = None
                task.status = TaskStatus.QUEUED
                task.execution = TaskExecution(agent=task.agent_name)
                await self._enqueue(task)
                log.info("manager.retry_requeued", {"id": task.id, "retry_count": task.retry.retry_count})
        except Exception as e:
            log.error("manager.retry_queue_error", {"error": str(e)})

    async def _recover_orphaned_tasks(self) -> int:
        """Reset tasks stuck in RUNNING state (left over from a previous crash).

        Called once at startup, before the execution loop begins, to restore
        the running-slot count to an accurate value.
        """
        orphans = await TaskStore.list_by_status(TaskStatus.RUNNING)
        await TaskStore.requeue_running_refs()
        for task in orphans:
            task.status = TaskStatus.QUEUED
            task.execution = TaskExecution(agent=task.agent_name)
            await TaskStore.update_task(task)
            await TaskStore.enqueue_task_ref(
                task.id,
                execution_record_id=(task.context or {}).get("_execution_record_id"),
            )
            log.warn("manager.orphan_recovered", {"id": task.id, "title": task.title})
        return len(orphans)

    async def _cleanup_loop(self) -> None:
        """Periodically expire QUEUED/PENDING tasks that have been waiting too long."""
        while self._running:
            await asyncio.sleep(_CLEANUP_INTERVAL_S)
            try:
                cancelled = await self._expire_stale_tasks()
                if cancelled:
                    log.info("manager.expiry_cleanup", {"cancelled": cancelled})
            except Exception as e:
                log.error("manager.cleanup_loop_error", {"error": str(e)})

    async def _expire_stale_tasks(self) -> int:
        """Cancel tasks that have been in QUEUED/PENDING for longer than TASK_EXPIRY_HOURS."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS)
        stale = await TaskStore.list_stale_queued(before=cutoff)
        for task in stale:
            task.status = TaskStatus.CANCELLED
            task.execution = TaskExecution(
                agent=task.agent_name,
                error=(
                    f"任务创建后超过 {_TASK_EXPIRY_HOURS} 小时未能执行，已自动取消。"
                ),
            )
            await TaskStore.update_task(task)
            log.info("manager.task_expired", {"id": task.id, "title": task.title})
        return len(stale)

    @classmethod
    async def _enqueue_scheduled_execution(cls, task: Task) -> TaskExecutionRecord:
        record = TaskExecutionRecord(
            task_id=task.id,
            status=TaskStatus.QUEUED,
            started_at=datetime.now(timezone.utc),
        )
        await TaskStore.create_record(record)

        ctx = dict(task.context or {})
        ctx["_execution_record_id"] = record.id
        task.context = ctx
        task.status = TaskStatus.QUEUED
        task.execution = TaskExecution(agent=task.agent_name)
        await cls._enqueue(task)
        return record

    @classmethod
    async def _cancel_scheduled_execution(
        cls,
        task: Task,
        record: TaskExecutionRecord,
    ) -> TaskExecutionRecord:
        now = datetime.now(timezone.utc)
        session_id = (
            record.session_id
            or (task.execution.session_id if task.execution else None)
        )
        if session_id:
            try:
                from flocks.task.background import get_background_manager
                get_background_manager().cancel_by_session_id(session_id)
            except Exception:
                pass

        task = cls._restore_scheduled_template(task)
        await TaskStore.update_task(task)
        await TaskStore.finish_queue_ref(task.id)

        mgr = cls._instance
        if mgr:
            mgr.queue.mark_finished(task.id)

        record.status = TaskStatus.CANCELLED
        record.completed_at = now
        if record.started_at:
            record.duration_ms = int((now - record.started_at).total_seconds() * 1000)
        record.error = record.error or "Cancelled from task queue."
        record.session_id = session_id
        await TaskStore.update_record(record)
        return record

    @staticmethod
    def _restore_scheduled_template(task: Task) -> Task:
        ctx = dict(task.context or {})
        ctx.pop("_execution_record_id", None)
        task.context = ctx
        task.status = TaskStatus.PENDING
        task.execution = TaskExecution(agent=task.agent_name)
        return task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    async def _enqueue(cls, task: Task) -> Task:
        mgr = cls._instance
        if mgr:
            return await mgr.queue.enqueue(task)
        task.status = TaskStatus.QUEUED
        task = await TaskStore.update_task(task)
        await TaskStore.enqueue_task_ref(
            task.id,
            execution_record_id=(task.context or {}).get("_execution_record_id"),
        )
        return task

    @staticmethod
    def _default_workspace_directory(task_id: str) -> str:
        from flocks.workspace.manager import WorkspaceManager

        workspace_root = WorkspaceManager.get_instance().get_workspace_dir()
        today = date.today().isoformat()
        return str(workspace_root / "tasks" / today / task_id)

    @classmethod
    async def _publish_event(cls, event_type: str, task: Task) -> None:
        try:
            from flocks.bus.bus import Bus
            from flocks.bus.bus_event import BusEvent

            evt = BusEvent.define(event_type, _TaskEventProps)
            await Bus.publish(evt, {
                "task_id": task.id,
                "status": task.status.value,
                "title": task.title,
            })
        except Exception as e:
            log.warn("manager.event_publish_error", {"error": str(e)})
