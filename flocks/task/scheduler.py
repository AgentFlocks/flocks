"""Task scheduler loop for execution creation."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from flocks.utils.log import Log

from .models import ExecutionTriggerType, SchedulerMode, SchedulerStatus, TaskTrigger
from .store import TaskStore

log = Log.create(service="task.scheduler")

try:
    from croniter import croniter  # type: ignore[import-untyped]
    import pytz  # type: ignore[import-untyped]
except ImportError:
    croniter = None
    pytz = None


class TaskScheduler:
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
            log.warn("scheduler.disabled", {"reason": "croniter not installed"})
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                log.error("scheduler.tick_error", {"error": str(exc)})
            await asyncio.sleep(self._check_interval)

    async def _tick(self) -> None:
        from .manager import TaskManager

        now = datetime.now(timezone.utc)
        schedulers = await TaskStore.list_due_schedulers()
        for scheduler in schedulers:
            next_run = self._parse_next_run(scheduler.trigger)
            if not next_run or next_run > now:
                continue
            active = await TaskStore.get_active_execution_for_scheduler(scheduler.id)
            if active is not None:
                continue

            trigger_type = (
                ExecutionTriggerType.RUN_ONCE
                if scheduler.mode == SchedulerMode.ONCE
                else ExecutionTriggerType.SCHEDULED
            )
            await TaskManager.create_execution_from_scheduler(
                scheduler,
                trigger_type=trigger_type,
                enqueue=True,
            )

            if scheduler.mode == SchedulerMode.CRON and scheduler.trigger.cron:
                scheduler.trigger.next_run = self._compute_next(
                    scheduler.trigger,
                    after=now,
                )
                await TaskStore.update_scheduler(scheduler)
            if scheduler.mode == SchedulerMode.ONCE:
                scheduler.status = SchedulerStatus.DISABLED
                scheduler.trigger.next_run = None
                await TaskStore.update_scheduler(scheduler)

    @staticmethod
    def _parse_next_run(trigger: TaskTrigger) -> Optional[datetime]:
        if trigger.run_at and not trigger.next_run:
            run_at = trigger.run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            return run_at
        if trigger.next_run:
            next_run = trigger.next_run
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
            return next_run
        if trigger.cron:
            return TaskScheduler._compute_next(trigger)
        return None

    @staticmethod
    def _compute_next(
        trigger: TaskTrigger,
        after: Optional[datetime] = None,
    ) -> Optional[datetime]:
        if croniter is None or not trigger.cron:
            return None
        base_utc = after or datetime.now(timezone.utc)
        try:
            tz_name = trigger.timezone or "Asia/Shanghai"
            if pytz is not None:
                try:
                    local_tz = pytz.timezone(tz_name)
                    base_local = base_utc.astimezone(local_tz)
                    iterator = croniter(trigger.cron, base_local)
                    next_local = iterator.get_next(datetime)
                    if next_local.tzinfo is None:
                        next_local = local_tz.localize(next_local)
                    return next_local.astimezone(timezone.utc)
                except Exception:
                    pass
            iterator = croniter(trigger.cron, base_utc)
            return iterator.get_next(datetime).replace(tzinfo=timezone.utc)
        except Exception as exc:
            log.warn("scheduler.cron_parse_error", {"cron": trigger.cron, "error": str(exc)})
            return None

    @classmethod
    def compute_next_run(cls, cron: str, tz: str = "Asia/Shanghai") -> Optional[datetime]:
        return cls._compute_next(TaskTrigger(cron=cron, timezone=tz))
