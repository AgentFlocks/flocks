"""Task plugin DB synchronization helpers."""

from __future__ import annotations

from typing import Sequence

from flocks.utils.log import Log

from .plugin_models import TaskSpec

log = Log.create(service="task.plugin.sync")


async def upsert_task_specs(specs: Sequence[TaskSpec]) -> int:
    from flocks.task.manager import TaskManager
    from flocks.task.models import (
        ExecutionMode,
        SchedulerMode,
        SchedulerStatus,
        TaskPriority,
        TaskSource,
        TaskTrigger,
    )
    from flocks.task.scheduler import TaskScheduler as SchedulerLoop
    from flocks.task.store import TaskStore

    await TaskStore.init()
    created = 0

    for spec in specs:
        try:
            priority = TaskPriority(spec.priority)
        except ValueError:
            priority = TaskPriority.NORMAL
        try:
            exec_mode = ExecutionMode(spec.execution_mode)
        except ValueError:
            exec_mode = ExecutionMode.AGENT

        existing = await TaskStore.get_scheduler_by_dedup_key(spec.dedup_key)
        if existing is not None:
            existing.title = spec.title
            existing.description = spec.description
            existing.priority = priority
            existing.execution_mode = exec_mode
            existing.agent_name = spec.agent_name
            existing.source = TaskSource(
                source_type="scheduled_trigger",
                user_prompt=spec.user_prompt,
            )
            existing.context = spec.context
            existing.tags = spec.tags
            if spec.task_type == "scheduled" and spec.cron:
                existing.mode = SchedulerMode.CRON
                existing.status = (
                    SchedulerStatus.ACTIVE if spec.enabled else SchedulerStatus.DISABLED
                )
                existing.trigger = TaskTrigger(
                    cron=spec.cron,
                    timezone=spec.timezone,
                    cron_description=spec.cron_description,
                    next_run=SchedulerLoop.compute_next_run(spec.cron, spec.timezone),
                )
            await TaskStore.update_scheduler(existing)
            continue

        if spec.task_type != "scheduled" or not spec.cron:
            log.warn("task.plugin.missing_cron", {"dedup_key": spec.dedup_key})
            continue

        await TaskManager.create_scheduler(
            title=spec.title,
            description=spec.description,
            mode=SchedulerMode.CRON,
            priority=priority,
            source=TaskSource(
                source_type="scheduled_trigger",
                user_prompt=spec.user_prompt,
            ),
            trigger=TaskTrigger(
                cron=spec.cron,
                timezone=spec.timezone,
                cron_description=spec.cron_description,
                next_run=SchedulerLoop.compute_next_run(spec.cron, spec.timezone),
            ),
            execution_mode=exec_mode,
            agent_name=spec.agent_name,
            context=spec.context,
            tags=spec.tags,
            created_by="system",
            dedup_key=spec.dedup_key,
        )
        created += 1

    log.info("task.plugin.done", {"created": created, "total": len(specs)})
    return created
