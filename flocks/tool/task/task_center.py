"""
Task Center Tools for Rex

Registers task management tools into ToolRegistry so Rex can
create, list, update, delete, and query tasks via natural language.
"""

from typing import Optional

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


# ======================================================================
# task_create
# ======================================================================

@ToolRegistry.register_function(
    name="task_create",
    description=(
        "Create a new task (queued, one-time scheduled, or recurring scheduled). "
        "Only call this when the user explicitly asks for deferred/delayed execution "
        "(e.g. 'add to queue', 'do it later', 'schedule daily at 8am', 'run once tonight at 6pm'). "
        "Do NOT create a task for immediate requests.\n\n"
        "IMPORTANT — Clarify schedule type before creating:\n"
        "When a user mentions a specific time (e.g. '今晚6点', '明天下午3点') WITHOUT clearly "
        "indicating recurrence, you MUST ask to confirm intent before calling this tool. "
        "Ask: '请问这个任务是只执行一次，还是每天在这个时间重复执行？'\n"
        "Recurrence signals (use type=scheduled, run_once=false): "
        "'每天', '每周', '每月', '每小时', '定期', '每个工作日', '每30分钟'\n"
        "One-time signals (use type=scheduled, run_once=true): "
        "'一次', '这次', specific date like '明天下午3点', '下周五晚上', '2024-01-15 18:00'\n"
        "Queue-only (use type=queued, no schedule): "
        "'等会', '稍后', '待会', '有空时', '不着急'\n\n"
        "IMPORTANT — IM session resolution before creating:\n"
        "If the task involves sending a message to an IM platform (企业微信/WeCom、飞书/Feishu、钉钉/DingTalk), "
        "you MUST resolve the target session_id and channel_type BEFORE calling this tool "
        "(follow the IM Session Resolution for task_create protocol in your system prompt). "
        "Embed both into description and user_prompt. "
        "If the user cannot provide a session_id, do NOT create the task."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            description="Short title for the task",
            required=True,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description=(
                "Detailed task description. "
                "If the task involves sending a message to an IM platform (WeCom/Feishu/DingTalk), "
                "MUST include the resolved channel_type and session_id here. "
                "Example: '每天早上8点向飞书群发送日报 channel_type=feishu session_id=ses_abc123'"
            ),
            required=True,
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description=(
                "Task type: "
                "'queued' = deferred but no schedule (run when queue is free); "
                "'scheduled' = triggered at a specific time (one-time or recurring, "
                "controlled by run_once)"
            ),
            required=True,
            enum=["queued", "scheduled"],
        ),
        ToolParameter(
            name="run_once",
            type=ParameterType.BOOLEAN,
            description=(
                "Only for type=scheduled. "
                "True = run exactly once at the specified time then disable. "
                "False (default) = recurring, repeats per cron expression."
            ),
            required=False,
            default=False,
        ),
        ToolParameter(
            name="priority",
            type=ParameterType.STRING,
            description="Priority level",
            required=False,
            default="normal",
            enum=["urgent", "high", "normal", "low"],
        ),
        ToolParameter(
            name="run_at",
            type=ParameterType.STRING,
            description=(
                "ISO 8601 datetime string for one-time execution (used when run_once=True). "
                "e.g. '2024-01-15T18:00:00+08:00'. "
                "If only a time like '今晚18:00' is given, compute the full datetime. "
                "Required when run_once=True and no cron is provided."
            ),
            required=False,
        ),
        ToolParameter(
            name="cron",
            type=ParameterType.STRING,
            description=(
                "Cron expression for recurring tasks (run_once=False), "
                "e.g. '0 8 * * *' for daily 8am. "
                "Can also be used with run_once=True to fire at the next cron occurrence."
            ),
            required=False,
        ),
        ToolParameter(
            name="cron_description",
            type=ParameterType.STRING,
            description=(
                "Human-readable Chinese description of the schedule. "
                "Always provide this when creating a scheduled task, e.g. "
                "'每天早上8点', '每周一09:00', '今晚18:00执行一次', '2025-01-15 下午3点执行一次'. "
                "This is shown directly in the UI."
            ),
            required=False,
        ),
        ToolParameter(
            name="timezone",
            type=ParameterType.STRING,
            description="Timezone for scheduled tasks (default: Asia/Shanghai)",
            required=False,
            default="Asia/Shanghai",
        ),
        ToolParameter(
            name="user_prompt",
            type=ParameterType.STRING,
            description=(
                "The EXECUTION CONTENT ONLY — what the agent should actually do when this task runs. "
                "You MUST extract and restate only the action part from the user's message, "
                "discarding any scheduling/creation meta-instructions such as "
                "'帮我创建定时任务', '在XX点执行一次', '加到任务队列', '等会帮我' etc. "
                "Think of it as: what would you tell the agent to do if the user had said it directly? "
                "Example — user says: '创建个定时任务，在14:45执行一次：查询threatbook.cn的情报' "
                "→ user_prompt should be: '查询 threatbook.cn 的情报' "
                "Example — user says: '帮我加个任务，明天上午扫描一下内网资产' "
                "→ user_prompt should be: '扫描内网资产' "
                "CRITICAL — IM tasks: If the action involves sending a message to an IM platform "
                "(WeCom/Feishu/DingTalk), you MUST include the resolved channel_type and session_id "
                "in user_prompt. NEVER omit them — the task runs unattended and cannot ask the user. "
                "Example — user says: '每天8点发飞书消息给研发群' (session already resolved to ses_abc123) "
                "→ user_prompt should be: '向飞书(channel_type=feishu) session_id=ses_abc123 发送消息：<消息内容>' "
                "This text is displayed in the UI as '任务补充信息'."
            ),
            required=False,
        ),
    ],
)
async def task_create(
    ctx: ToolContext,
    title: str,
    description: str,
    type: str,
    run_once: bool = False,
    priority: str = "normal",
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: str = "Asia/Shanghai",
    user_prompt: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import (
        SchedulerMode,
        TaskPriority,
        TaskSource,
        TaskTrigger,
        build_schedule,
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
        output_lines.append(f"Run at: {scheduler.trigger.run_at.isoformat()}")
    elif scheduler.trigger.cron:
        output_lines.append(f"Cron: {scheduler.trigger.cron}")
        if scheduler.trigger.next_run:
            output_lines.append(f"Next run: {scheduler.trigger.next_run.isoformat()}")

    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=f"Task created: {scheduler.title}",
    )


# ======================================================================
# task_list
# ======================================================================

@ToolRegistry.register_function(
    name="task_list",
    description="List tasks with optional filters (status, type, etc.)",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="status",
            type=ParameterType.STRING,
            description="Filter by status",
            required=False,
            enum=["pending", "queued", "running", "completed", "failed", "cancelled", "paused"],
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description="Filter by type",
            required=False,
            enum=["queued", "scheduled"],
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Max results (default 10)",
            required=False,
            default=10,
        ),
    ],
)
async def task_list(
    ctx: ToolContext,
    status: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 10,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import SchedulerStatus, TaskStatus

    if type == "scheduled":
        scheduler_status = None
        if status == "running":
            scheduler_status = SchedulerStatus.ACTIVE
        elif status == "paused":
            scheduler_status = SchedulerStatus.DISABLED
        tasks, total = await TaskManager.list_schedulers(
            status=scheduler_status,
            limit=limit,
        )
    else:
        tasks, total = await TaskManager.list_executions(
            status=TaskStatus(status) if status else None,
            limit=limit,
        )

    lines = [f"Tasks ({total} total, showing {len(tasks)}):"]
    for t in tasks:
        lines.append(_format_task_line(t))

    return ToolResult(success=True, output="\n".join(lines))


# ======================================================================
# task_status
# ======================================================================

@ToolRegistry.register_function(
    name="task_status",
    description="Get detailed status and result of a specific task",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_status(ctx: ToolContext, task_id: str) -> ToolResult:
    from flocks.task.manager import TaskManager

    task = await TaskManager.get_execution(task_id)
    if task and task.delivery_status.value == "unread":
        await TaskManager.mark_notified(task_id)
    if task is None:
        task = await TaskManager.get_scheduler(task_id)
    if task is None:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=task.title,
    )


# ======================================================================
# task_update
# ======================================================================

@ToolRegistry.register_function(
    name="task_update",
    description="Update a task (priority, status, title). Supports cancel/pause/resume.",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="Action to perform",
            required=True,
            enum=["cancel", "pause", "resume", "retry", "update"],
        ),
        ToolParameter(
            name="priority",
            type=ParameterType.STRING,
            description="New priority (only for action=update)",
            required=False,
            enum=["urgent", "high", "normal", "low"],
        ),
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            description="New title (only for action=update)",
            required=False,
        ),
    ],
)
async def task_update(
    ctx: ToolContext,
    task_id: str,
    action: str,
    priority: Optional[str] = None,
    title: Optional[str] = None,
) -> ToolResult:
    from flocks.task.manager import TaskManager
    from flocks.task.models import TaskPriority

    if action == "cancel":
        task = await TaskManager.cancel_execution(task_id)
    elif action == "pause":
        task = await TaskManager.pause_execution(task_id)
    elif action == "resume":
        task = await TaskManager.resume_execution(task_id)
    elif action == "retry":
        task = await TaskManager.retry_execution(task_id)
    elif action == "update":
        fields = {}
        if priority:
            fields["priority"] = TaskPriority(priority)
        if title:
            fields["title"] = title
        task = await TaskManager.update_scheduler(task_id, **fields)
    else:
        return ToolResult(success=False, error=f"Unknown action: {action}")

    if not task:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=f"Task {action}d: {task.title}",
    )


# ======================================================================
# task_delete
# ======================================================================

@ToolRegistry.register_function(
    name="task_delete",
    description="Delete a task permanently",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_delete(ctx: ToolContext, task_id: str) -> ToolResult:
    from flocks.task.manager import TaskManager

    execution = await TaskManager.get_execution(task_id)
    if execution is not None:
        ok = await TaskManager.delete_execution(task_id)
    else:
        ok = await TaskManager.delete_scheduler(task_id)
    if not ok:
        return ToolResult(success=False, error=f"Task {task_id} not found")
    return ToolResult(success=True, output=f"Task {task_id} deleted.")


# ======================================================================
# task_rerun
# ======================================================================

@ToolRegistry.register_function(
    name="task_rerun",
    description="Rerun a task. If the task is currently running, it will be stopped and requeued.",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_rerun(ctx: ToolContext, task_id: str) -> ToolResult:
    from flocks.task.manager import TaskManager

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
    "paused": "⏸️",
}


def _format_task_line(t) -> str:
    status = getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
    icon = _STATUS_ICON.get(status, "·")
    pri = f"[{t.priority.value}]" if t.priority.value != "normal" else ""
    return f"  {icon} {t.id}  {pri} {t.title}  ({status})"


def _format_task(t) -> str:
    mode_value = getattr(getattr(t, "mode", None), "value", getattr(t, "mode", None))
    trigger = getattr(t, "trigger", None)
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
            lines.append(f"Run at: {trigger.run_at.isoformat()}")
        if trigger.cron:
            lines.append(f"Cron: {trigger.cron} ({trigger.timezone})")
        if trigger.next_run:
            lines.append(f"Next run: {trigger.next_run.isoformat()}")
        if trigger.cron_description:
            lines.append(f"Schedule desc: {trigger.cron_description}")
    if getattr(t, "queued_at", None):
        lines.append(f"Queued: {t.queued_at.isoformat()}")
    if getattr(t, "started_at", None):
        lines.append(f"Started: {t.started_at.isoformat()}")
    if getattr(t, "completed_at", None):
        lines.append(f"Completed: {t.completed_at.isoformat()}")
    if getattr(t, "duration_ms", None) is not None:
        lines.append(f"Duration: {t.duration_ms}ms")
    if getattr(t, "result_summary", None):
        lines.append(f"Result:\n{t.result_summary}")
    if getattr(t, "error", None):
        lines.append(f"Error: {t.error}")
    lines.append(f"Created: {t.created_at.isoformat()}")
    return "\n".join(lines)
