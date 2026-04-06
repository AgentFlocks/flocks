"""
Task Scheduler

Lightweight asyncio-based cron scheduler.
Periodically checks enabled scheduled tasks and creates queued instances.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from flocks.utils.log import Log

from .models import (
    Task,
    TaskExecution,
    TaskExecutionRecord,
    TaskSchedule,
    TaskStatus,
)
from .store import TaskStore

log = Log.create(service="task.scheduler")

try:
    from croniter import croniter  # type: ignore[import-untyped]
    import pytz  # type: ignore[import-untyped]
except ImportError:
    croniter = None  # graceful fallback; scheduler will refuse to start
    pytz = None


class TaskScheduler:
    """Asyncio background task that triggers scheduled tasks via cron."""

    def __init__(self, check_interval: int = 30):
        self._check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_available(self) -> bool:
        return croniter is not None

    async def start(self) -> None:
        if croniter is None:
            log.warn("scheduler.disabled", {
                "reason": "croniter not installed (pip install croniter)"
            })
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("scheduler.started", {"interval": self._check_interval})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("scheduler.stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                log.error("scheduler.tick_error", {"error": str(e)})
            await asyncio.sleep(self._check_interval)

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        tasks = await TaskStore.get_scheduled_tasks(enabled_only=True)
        for task in tasks:
            if not task.schedule:
                continue
            next_run = self._parse_next_run(task.schedule)
            if next_run and next_run <= now:
                if not task.schedule.run_once:
                    # Advance next_run BEFORE triggering so concurrent ticks do
                    # not enqueue the same recurring task twice.
                    self._advance_next_run(task.schedule, now)
                    task.schedule.next_run = self._parse_next_run(task.schedule)
                    await TaskStore.update_task(task)

                try:
                    triggered = await self._trigger(task, now)
                except Exception as e:
                    log.error("scheduler.trigger_error", {
                        "task_id": task.id, "error": str(e),
                    })
                    triggered = False

                if task.schedule.run_once and triggered:
                    task.schedule.enabled = False
                    task.schedule.next_run = None
                    await TaskStore.update_task(task)
                    log.info("scheduler.one_shot_completed", {"task_id": task.id})

    async def _trigger(self, template: Task, now: datetime) -> bool:
        """Queue a scheduled task definition for execution."""
        active_ref = await TaskStore.get_active_queue_ref(template.id)
        if active_ref is not None:
            log.info("scheduler.enqueue_skipped", {
                "task_id": template.id,
                "queue_ref_id": active_ref.id,
            })
            return False

        record = TaskExecutionRecord(
            task_id=template.id,
            status=TaskStatus.QUEUED,
            started_at=now,
            session_id=None,
        )
        await TaskStore.create_record(record)
        ctx = dict(template.context or {})
        ctx["_execution_record_id"] = record.id
        template.context = ctx
        template.status = TaskStatus.QUEUED
        template.execution = TaskExecution(agent=template.agent_name)
        await TaskStore.update_task(template)
        ref = await TaskStore.enqueue_task_ref(
            template.id,
            execution_record_id=record.id,
        )
        if ref is None:
            log.info("scheduler.enqueue_race_skipped", {
                "task_id": template.id,
                "record_id": record.id,
            })
            return False

        log.info("scheduler.triggered", {
            "task_id": template.id,
            "queue_ref_id": ref.id,
            "record_id": record.id,
        })
        return True

    # ------------------------------------------------------------------
    # Cron helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_next_run(schedule: TaskSchedule) -> Optional[datetime]:
        # One-time task: use run_at if next_run not yet set
        if schedule.run_once and schedule.run_at and not schedule.next_run:
            ra = schedule.run_at
            if ra.tzinfo is None:
                ra = ra.replace(tzinfo=timezone.utc)
            return ra
        if schedule.next_run:
            nr = schedule.next_run
            if nr.tzinfo is None:
                nr = nr.replace(tzinfo=timezone.utc)
            return nr
        if schedule.cron:
            return TaskScheduler._compute_next(schedule)
        return None

    @staticmethod
    def _advance_next_run(schedule: TaskSchedule, after: datetime) -> None:
        nxt = TaskScheduler._compute_next(schedule, after)
        schedule.next_run = nxt

    @staticmethod
    def _compute_next(
        schedule: TaskSchedule,
        after: Optional[datetime] = None,
    ) -> Optional[datetime]:
        if croniter is None or not schedule.cron:
            return None
        base_utc = after or datetime.now(timezone.utc)
        try:
            # Interpret the cron expression in the task's configured timezone.
            # croniter computes "next" relative to the base time in local tz,
            # then we convert the result back to UTC for storage.
            tz_name = schedule.timezone or "Asia/Shanghai"
            if pytz is not None:
                try:
                    local_tz = pytz.timezone(tz_name)
                    base_local = base_utc.astimezone(local_tz)
                    it = croniter(schedule.cron, base_local)
                    nxt_local = it.get_next(datetime)
                    # croniter returns naive datetime — attach local tz then convert
                    if nxt_local.tzinfo is None:
                        nxt_local = local_tz.localize(nxt_local)
                    return nxt_local.astimezone(timezone.utc)
                except Exception:
                    pass  # fall through to UTC computation below
            it = croniter(schedule.cron, base_utc)
            return it.get_next(datetime).replace(tzinfo=timezone.utc)
        except Exception as e:
            log.warn("scheduler.cron_parse_error", {
                "cron": schedule.cron, "error": str(e),
            })
            return None

    @classmethod
    def compute_next_run(cls, cron: str, tz: str = "Asia/Shanghai") -> Optional[datetime]:
        """Public utility: compute next run for a cron expression."""
        sched = TaskSchedule(cron=cron, timezone=tz)
        return cls._compute_next(sched)
