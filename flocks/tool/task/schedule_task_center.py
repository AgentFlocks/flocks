"""
Schedule Task Center Tools for Rex

Registers the unified schedule_task tool into ToolRegistry so Rex can manage
scheduler definitions and execution instances via natural language.
"""

import json
from typing import Optional

from flocks.task.formatting import format_task_datetime, resolve_task_timezone_name
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="task.tools")


_TRUTHY_STRINGS = {"true", "1", "yes", "y", "on"}
_FALSY_STRINGS = {"false", "0", "no", "n", "off", ""}


def _coerce_legacy_bool(value: object, *, default: bool = False) -> bool:
    """Coerce values that may arrive as strings from legacy clients."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY_STRINGS:
            return True
        if normalized in _FALSY_STRINGS:
            return False
    return default


def _normalize_schedule_task_create_inputs(
    type_value: Optional[str],
    schedule_type: Optional[str],
    run_once: bool,
    run_at: Optional[str],
    cron: Optional[str],
    cron_description: Optional[str],
    timezone: str,
    schedule: Optional[str],
) -> tuple[Optional[str], bool, Optional[str], Optional[str], Optional[str], str]:
    """Accept common schedule_task_create aliases and infer scheduled tasks."""
    schedule_data: dict[str, object] = {}
    if schedule:
        normalized_schedule = schedule.strip()
        if normalized_schedule.startswith("{"):
            try:
                parsed_schedule = json.loads(normalized_schedule)
            except json.JSONDecodeError:
                parsed_schedule = None
            if isinstance(parsed_schedule, dict):
                schedule_data = parsed_schedule
            else:
                cron = cron or schedule
        else:
            cron = cron or schedule

    if schedule_data:
        type_value = type_value or schedule_data.get("type") or schedule_data.get("task_type")
        schedule_type = (
            schedule_type
            or schedule_data.get("schedule_type")
            or schedule_data.get("scheduleType")
        )
        cron = cron or schedule_data.get("cron")
        run_at = run_at or schedule_data.get("run_at") or schedule_data.get("runAt")
        cron_description = (
            cron_description
            or schedule_data.get("cron_description")
            or schedule_data.get("cronDescription")
        )
        timezone = str(schedule_data.get("timezone") or timezone)
        if schedule_data.get("run_once") is not None:
            run_once = _coerce_legacy_bool(schedule_data.get("run_once"), default=run_once)
        elif schedule_data.get("runOnce") is not None:
            run_once = _coerce_legacy_bool(schedule_data.get("runOnce"), default=run_once)

    if type_value:
        return type_value, run_once, run_at, cron, cron_description, timezone
    if not schedule_type:
        # When the caller signalled a scheduled intent (run_once / run_at / cron),
        # keep it scheduled so build_schedule can surface proper validation errors
        # instead of silently falling back to an immediate queued execution.
        if cron or run_at or run_once:
            return "scheduled", run_once, run_at, cron, cron_description, timezone
        return "queued", run_once, run_at, cron, cron_description, timezone

    normalized = schedule_type.strip().lower()
    if normalized == "queued":
        return "queued", run_once, run_at, cron, cron_description, timezone
    if normalized in {"scheduled", "cron", "recurring", "repeat"}:
        return "scheduled", False, run_at, cron, cron_description, timezone
    if normalized in {"once", "one_time", "one-time", "run_once"}:
        return "scheduled", True, run_at, cron, cron_description, timezone
    return schedule_type, run_once, run_at, cron, cron_description, timezone


# ======================================================================
# Task operation implementations
# ======================================================================

async def schedule_task_create(
    ctx: ToolContext,
    title: str,
    description: str,
    type: Optional[str] = None,
    schedule_type: Optional[str] = None,
    schedule: Optional[str] = None,
    run_once: bool = False,
    priority: str = "normal",
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: str = "Asia/Shanghai",
    user_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
    action: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import (
        SchedulerMode,
        TaskPriority,
        TaskSource,
        TaskTrigger,
        build_schedule,
    )

    del action

    type, run_once, run_at, cron, cron_description, timezone = _normalize_schedule_task_create_inputs(
        type,
        schedule_type,
        run_once,
        run_at,
        cron,
        cron_description,
        timezone,
        schedule,
    )
    if type is None:
        return ToolResult(
            success=False,
            error="type or schedule_type is required",
        )

    task_priority = TaskPriority(priority)

    if type == "queued":
        mode = SchedulerMode.ONCE
        trigger = TaskTrigger(run_immediately=True)
    else:
        try:
            trigger = build_schedule(
                run_once=run_once,
                run_at=run_at,
                cron=cron,
                cron_description=cron_description,
                timezone=timezone,
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        mode = SchedulerMode.ONCE if run_once else SchedulerMode.CRON

    source = TaskSource(
        source_type="user_conversation",
        user_prompt=user_prompt,
    )

    scheduler = await TaskManager.create_scheduler(
        title=title,
        description=description,
        mode=mode,
        priority=task_priority,
        source=source,
        trigger=trigger,
    )
    if enabled is False:
        scheduler = await TaskManager.disable_scheduler(scheduler.id) or scheduler

    display_tz = resolve_task_timezone_name(scheduler)
    output_lines = [
        f"ID: {scheduler.id}",
        f"Title: {scheduler.title}",
        f"Mode: {scheduler.mode.value}",
        f"Status: {scheduler.status.value}",
        f"Priority: {scheduler.priority.value}",
    ]
    if scheduler.trigger.run_immediately:
        executions, _ = await TaskManager.list_scheduler_executions(
            scheduler.id,
            limit=1,
        )
        if executions:
            execution = executions[0]
            output_lines.append(f"Execution ID: {execution.id}")
            output_lines.append(f"Execution Status: {execution.status.value}")
    elif scheduler.trigger.run_at:
        output_lines.append(
            f"Run at: {format_task_datetime(scheduler.trigger.run_at, display_tz)}"
        )
    elif scheduler.trigger.cron:
        output_lines.append(
            f"Cron: {scheduler.trigger.cron} ({scheduler.trigger.timezone})"
        )
        if scheduler.trigger.next_run:
            output_lines.append(
                f"Next run: {format_task_datetime(scheduler.trigger.next_run, display_tz)}"
            )

    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=f"Task created: {scheduler.title}",
    )


_SCHEDULER_STATUSES = {"active", "disabled", "paused"}
_EXECUTION_STATUSES = {"pending", "queued", "running", "completed", "failed", "cancelled", "paused"}
_EXECUTION_TYPES = {"queued", "execution"}
_VALID_TYPES = {"scheduled", "scheduler"} | _EXECUTION_TYPES


async def schedule_task_list(
    ctx: ToolContext,
    status: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 10,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import SchedulerStatus, TaskStatus

    if type is not None and type not in _VALID_TYPES:
        return ToolResult(
            success=False,
            error=(
                f"Invalid type '{type}'. "
                f"Valid values: {', '.join(sorted(_VALID_TYPES))}."
            ),
        )

    if type in {"scheduled", "scheduler"}:
        query_schedulers = True
    elif type in _EXECUTION_TYPES:
        query_schedulers = False
    elif status is None or status in _SCHEDULER_STATUSES:
        query_schedulers = True
    elif status in _EXECUTION_STATUSES:
        query_schedulers = False
    else:
        return ToolResult(
            success=False,
            error=(
                f"Invalid status '{status}'. "
                f"Scheduler statuses: {', '.join(sorted(_SCHEDULER_STATUSES))}. "
                f"Execution statuses: {', '.join(sorted(_EXECUTION_STATUSES))}. "
                "Use type='scheduled' for scheduler states or "
                "type='execution' for execution states."
            ),
        )

    if query_schedulers:
        if status is not None and status not in _SCHEDULER_STATUSES:
            return ToolResult(
                success=False,
                error=(
                    f"Invalid status '{status}' for type='scheduled'. "
                    f"Valid scheduler statuses: {', '.join(sorted(_SCHEDULER_STATUSES))}. "
                    "Use type='execution' to query execution statuses like "
                    f"'{status}'."
                ),
            )
        scheduler_status = None
        if status == "active":
            scheduler_status = SchedulerStatus.ACTIVE
        elif status in ("disabled", "paused"):
            scheduler_status = SchedulerStatus.DISABLED
        tasks, total = await TaskManager.list_schedulers(
            status=scheduler_status,
            scheduled_only=type != "scheduler",
            limit=limit,
        )
        label = "Task schedulers" if type == "scheduler" else "Scheduled tasks"
    else:
        try:
            mapped_status = "cancelled" if status == "paused" else status
            task_status = TaskStatus(mapped_status) if mapped_status else None
        except ValueError:
            return ToolResult(
                success=False,
                error=(
                    f"Invalid execution status '{status}'. "
                    f"Valid values: {', '.join(s.value for s in TaskStatus)}."
                ),
            )
        tasks, total = await TaskManager.list_executions(
            status=task_status,
            limit=limit,
        )
        label = "Task executions"

    lines = [f"{label} ({total} total, showing {len(tasks)}):"]
    for t in tasks:
        lines.append(_format_task_line(t))

    return ToolResult(success=True, output="\n".join(lines))


async def schedule_task_status(
    ctx: ToolContext,
    task_id: str,
    resource_type: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager

    if resource_type == "scheduler":
        task = await TaskManager.get_scheduler(task_id)
    elif resource_type == "execution":
        task = await TaskManager.get_execution(task_id)
    else:
        task = await TaskManager.get_execution(task_id)
        if task is None:
            task = await TaskManager.get_scheduler(task_id)
    if (
        resource_type != "scheduler"
        and task
        and getattr(getattr(task, "delivery_status", None), "value", None) == "unread"
    ):
        await TaskManager.mark_notified(task_id)
    if task is None:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=task.title,
    )


async def schedule_task_update(
    ctx: ToolContext,
    task_id: str,
    action: str = "update",
    priority: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    run_once: Optional[bool] = None,
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: Optional[str] = None,
    user_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import TaskPriority

    normalized_action = (action or "update").lower()
    if normalized_action in {"pause", "stop"}:
        normalized_action = "disable"
    elif normalized_action in {"resume", "start"}:
        normalized_action = "enable"

    if normalized_action == "cancel":
        task = await TaskManager.cancel_execution(task_id)
    elif normalized_action == "retry":
        task = await TaskManager.retry_execution(task_id)
    elif normalized_action == "disable":
        task = await TaskManager.disable_scheduler(task_id)
    elif normalized_action == "enable":
        task = await TaskManager.enable_scheduler(task_id)
    elif normalized_action == "update":
        fields = {}
        if priority:
            fields["priority"] = TaskPriority(priority)
        if title:
            fields["title"] = title
        if description is not None:
            fields["description"] = description
        try:
            task = await TaskManager.update_scheduler_with_trigger(
                task_id,
                fields=fields,
                cron=cron,
                timezone=timezone,
                cron_description=cron_description,
                run_once=run_once,
                run_at=run_at,
                user_prompt=user_prompt,
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if enabled is False:
            task = await TaskManager.disable_scheduler(task_id) or task
            normalized_action = "disable"
        elif enabled is True:
            task = await TaskManager.enable_scheduler(task_id) or task
            normalized_action = "enable"
    else:
        return ToolResult(success=False, error=f"Unknown action: {action}")

    if not task:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=f"Task {normalized_action}d: {task.title}",
    )


async def schedule_task_delete(
    ctx: ToolContext,
    task_id: str,
    resource_type: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager

    if resource_type == "execution":
        ok = await TaskManager.delete_execution(task_id)
    elif resource_type == "scheduler":
        ok = await TaskManager.delete_scheduler(task_id)
    else:
        execution = await TaskManager.get_execution(task_id)
        if execution is not None:
            ok = await TaskManager.delete_execution(task_id)
        else:
            ok = await TaskManager.delete_scheduler(task_id)
    if not ok:
        return ToolResult(success=False, error=f"Task {task_id} not found")
    return ToolResult(success=True, output=f"Task {task_id} deleted.")


async def schedule_task_rerun(
    ctx: ToolContext,
    task_id: str,
    resource_type: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager

    if resource_type == "execution":
        task = await TaskManager.rerun_execution(task_id)
    elif resource_type == "scheduler":
        task = await TaskManager.rerun_scheduler(task_id)
    else:
        task = await TaskManager.rerun_execution(task_id)
        if task is None:
            task = await TaskManager.rerun_scheduler(task_id)
    if not task:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=f"Task rerun: {task.title}",
    )


_SCHEDULE_TASK_ACTIONS = [
    "create",
    "list",
    "status",
    "update",
    "enable",
    "disable",
    "cancel",
    "retry",
    "delete",
    "rerun",
]

_SCHEDULE_TASK_ACTIONS_BY_RESOURCE = {
    "scheduler": {
        "create",
        "list",
        "status",
        "update",
        "enable",
        "disable",
        "delete",
        "rerun",
    },
    "execution": {"list", "status", "cancel", "retry", "delete", "rerun"},
}


@ToolRegistry.register_function(
    name="schedule_task",
    description=(
        "Manage task scheduler definitions and execution instances. Always select "
        "resource_type. Scheduler actions: create, list, status, update, enable, "
        "disable, delete, rerun. Execution actions: list, status, cancel, retry, "
        "delete, rerun. Create tasks only for explicitly deferred or scheduled work. "
        "For scheduled messaging, resolve channel_type and session_id with "
        "channel_message without message first and include them in description and "
        "user_prompt."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="Task operation to perform.",
            required=True,
            enum=_SCHEDULE_TASK_ACTIONS,
        ),
        ToolParameter(
            name="resource_type",
            type=ParameterType.STRING,
            description="Target resource: scheduler definition or execution instance.",
            required=True,
            enum=["scheduler", "execution"],
        ),
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Scheduler or execution ID; required except for create and list.",
            required=False,
        ),
        ToolParameter(
            name="status",
            type=ParameterType.STRING,
            description="Optional status filter for action=list.",
            required=False,
            enum=[
                "active",
                "disabled",
                "paused",
                "pending",
                "queued",
                "running",
                "completed",
                "failed",
                "cancelled",
            ],
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Maximum list results.",
            required=False,
            default=10,
        ),
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            description="Task title for create or new scheduler title for update.",
            required=False,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description=(
                "Task description for create or update. Include resolved channel_type "
                "and session_id for scheduled messaging tasks."
            ),
            required=False,
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description=(
                "Create mode: queued for deferred execution without a schedule, or "
                "scheduled for one-time/recurring execution controlled by run_once."
            ),
            required=False,
            enum=["queued", "scheduled"],
        ),
        ToolParameter(
            name="schedule_type",
            type=ParameterType.STRING,
            description="Legacy create mode alias: queued, scheduled, cron, recurring, or once.",
            required=False,
        ),
        ToolParameter(
            name="schedule",
            type=ParameterType.STRING,
            description="Legacy cron string or JSON schedule object for create.",
            required=False,
        ),
        ToolParameter(
            name="run_once",
            type=ParameterType.BOOLEAN,
            description="True for one-time scheduling; false for recurring scheduling.",
            required=False,
        ),
        ToolParameter(
            name="priority",
            type=ParameterType.STRING,
            description="Task priority for create or scheduler update.",
            required=False,
            enum=["urgent", "high", "normal", "low"],
        ),
        ToolParameter(
            name="run_at",
            type=ParameterType.STRING,
            description="ISO 8601 datetime for one-time scheduling.",
            required=False,
        ),
        ToolParameter(
            name="cron",
            type=ParameterType.STRING,
            description="Five-field cron expression for recurring scheduling.",
            required=False,
        ),
        ToolParameter(
            name="cron_description",
            type=ParameterType.STRING,
            description="Human-readable schedule description shown in the UI.",
            required=False,
        ),
        ToolParameter(
            name="timezone",
            type=ParameterType.STRING,
            description="IANA timezone for run_at or cron; defaults to Asia/Shanghai on create.",
            required=False,
        ),
        ToolParameter(
            name="user_prompt",
            type=ParameterType.STRING,
            description=(
                "Execution instructions without scheduling meta-language. Include resolved "
                "channel_type and session_id for scheduled messaging tasks."
            ),
            required=False,
        ),
        ToolParameter(
            name="enabled",
            type=ParameterType.BOOLEAN,
            description="Initial or updated scheduler enabled state.",
            required=False,
        ),
    ],
)
async def schedule_task(
    ctx: ToolContext,
    action: str,
    resource_type: str,
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
    title: Optional[str] = None,
    description: Optional[str] = None,
    type: Optional[str] = None,
    schedule_type: Optional[str] = None,
    schedule: Optional[str] = None,
    run_once: Optional[bool] = None,
    priority: Optional[str] = None,
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: Optional[str] = None,
    user_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ToolResult:
    """Dispatch task operations to the existing scheduler and execution handlers."""
    normalized_action = action.strip().lower()
    normalized_resource = resource_type.strip().lower()
    supported_actions = _SCHEDULE_TASK_ACTIONS_BY_RESOURCE.get(normalized_resource)
    if supported_actions is None:
        return ToolResult(
            success=False,
            error="resource_type must be 'scheduler' or 'execution'",
        )
    if normalized_action not in supported_actions:
        return ToolResult(
            success=False,
            error=(
                f"Action '{normalized_action}' is not supported for "
                f"resource_type='{normalized_resource}'. Valid actions: "
                f"{', '.join(sorted(supported_actions))}"
            ),
        )

    if normalized_action == "create":
        if title is None or description is None:
            return ToolResult(
                success=False,
                error="action='create' requires title and description",
            )
        return await schedule_task_create(
            ctx,
            title=title,
            description=description,
            type=type,
            schedule_type=schedule_type,
            schedule=schedule,
            run_once=run_once if run_once is not None else False,
            priority=priority or "normal",
            run_at=run_at,
            cron=cron,
            cron_description=cron_description,
            timezone=timezone or "Asia/Shanghai",
            user_prompt=user_prompt,
            enabled=enabled,
        )

    if normalized_action == "list":
        query_type = "scheduler" if normalized_resource == "scheduler" else "execution"
        return await schedule_task_list(
            ctx,
            status=status,
            type=query_type,
            limit=limit,
        )

    if not task_id:
        return ToolResult(
            success=False,
            error=f"action='{normalized_action}' requires task_id",
        )

    if normalized_action == "status":
        return await schedule_task_status(
            ctx,
            task_id,
            resource_type=normalized_resource,
        )
    if normalized_action in {"update", "enable", "disable", "cancel", "retry"}:
        return await schedule_task_update(
            ctx,
            task_id,
            action=normalized_action,
            priority=priority,
            title=title,
            description=description,
            run_once=run_once,
            run_at=run_at,
            cron=cron,
            cron_description=cron_description,
            timezone=timezone,
            user_prompt=user_prompt,
            enabled=enabled,
        )
    if normalized_action == "delete":
        return await schedule_task_delete(
            ctx,
            task_id,
            resource_type=normalized_resource,
        )
    return await schedule_task_rerun(
        ctx,
        task_id,
        resource_type=normalized_resource,
    )


# ======================================================================
# Formatting helpers
# ======================================================================

_STATUS_ICON = {
    "pending": "⏳",
    "queued": "📋",
    "running": "🟢",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}


def _format_task_line(t) -> str:
    status = getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
    icon = _STATUS_ICON.get(status, "·")
    pri = f"[{t.priority.value}]" if t.priority.value != "normal" else ""
    return f"  {icon} {t.id}  {pri} {t.title}  ({status})"


def _format_task(t) -> str:
    mode_value = getattr(getattr(t, "mode", None), "value", getattr(t, "mode", None))
    trigger = getattr(t, "trigger", None)
    display_tz = resolve_task_timezone_name(t)
    if mode_value == "cron":
        type_value = "scheduled"
    elif getattr(trigger, "run_immediately", False):
        type_value = "immediate"
    elif trigger is not None:
        type_value = "once"
    else:
        type_value = "execution"
    status_value = getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
    lines = [
        f"ID: {t.id}",
        f"Title: {t.title}",
        f"Type: {type_value}",
        f"Status: {_STATUS_ICON.get(status_value, '')} {status_value}",
        f"Priority: {t.priority.value}",
    ]
    if trigger is not None:
        if trigger.run_at:
            lines.append(f"Run at: {format_task_datetime(trigger.run_at, display_tz)}")
        if trigger.cron:
            lines.append(f"Cron: {trigger.cron} ({trigger.timezone})")
        if trigger.next_run:
            lines.append(
                f"Next run: {format_task_datetime(trigger.next_run, display_tz)}"
            )
        if trigger.cron_description:
            lines.append(f"Schedule desc: {trigger.cron_description}")
    if getattr(t, "queued_at", None):
        lines.append(f"Queued: {format_task_datetime(t.queued_at, display_tz)}")
    if getattr(t, "started_at", None):
        lines.append(f"Started: {format_task_datetime(t.started_at, display_tz)}")
    if getattr(t, "completed_at", None):
        lines.append(f"Completed: {format_task_datetime(t.completed_at, display_tz)}")
    if getattr(t, "duration_ms", None) is not None:
        lines.append(f"Duration: {t.duration_ms}ms")
    if getattr(t, "result_summary", None):
        lines.append(f"Result:\n{t.result_summary}")
    if getattr(t, "error", None):
        lines.append(f"Error: {t.error}")
    lines.append(f"Created: {format_task_datetime(t.created_at, display_tz)}")
    return "\n".join(lines)
