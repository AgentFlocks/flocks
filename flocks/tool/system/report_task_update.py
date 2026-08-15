"""Publish report task lifecycle updates to the existing SSE stream."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


REPORT_TASK_UPDATED_EVENT = "report.task.updated"
REPORT_TASK_STATUSES = ["queued", "running", "completed", "failed", "cancelled"]


@ToolRegistry.register_function(
    name="report_task_update",
    description=(
        "Notify the UI that a report generation task changed state. Call this only "
        "for report tasks that provide a generation ID, and emit completed only after "
        "the report has been successfully persisted and can be fetched by the UI."
    ),
    description_cn=(
        "通过现有 SSE 长连接通知 UI 报告生成任务状态变化。仅在报告任务提供 generation ID 时调用，"
        "且必须在报告成功持久化并可查询后才能发送 completed。"
    ),
    category=ToolCategory.SYSTEM,
    native=True,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Report generation task ID supplied by the calling application.",
            required=True,
        ),
        ToolParameter(
            name="status",
            type=ParameterType.STRING,
            description="Current report generation state.",
            required=True,
            enum=REPORT_TASK_STATUSES,
        ),
        ToolParameter(
            name="progress",
            type=ParameterType.INTEGER,
            description="Optional completion percentage from 0 through 100.",
            required=False,
        ),
        ToolParameter(
            name="report_id",
            type=ParameterType.STRING,
            description="Optional persisted report ID returned by the report backend.",
            required=False,
        ),
        ToolParameter(
            name="revision",
            type=ParameterType.INTEGER,
            description="Optional monotonically increasing task revision.",
            required=False,
        ),
        ToolParameter(
            name="error_code",
            type=ParameterType.STRING,
            description="Optional stable error code for a failed report task.",
            required=False,
        ),
        ToolParameter(
            name="error_message",
            type=ParameterType.STRING,
            description="Optional user-facing error message for a failed report task.",
            required=False,
        ),
    ],
    tags=["report", "report-generation", "sse", "ui-notification"],
)
async def report_task_update_tool(
    ctx: ToolContext,
    generation_id: Any,
    status: Any,
    progress: Optional[int] = None,
    report_id: Optional[str] = None,
    revision: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> ToolResult:
    if not isinstance(generation_id, str) or not generation_id.strip():
        return ToolResult(success=False, error="generation_id must be a non-empty string")
    if not isinstance(status, str):
        return ToolResult(success=False, error="status must be a string")

    generation_id = generation_id.strip()
    if generation_id.lower() in {"none", "null"}:
        return ToolResult(success=False, error="generation_id must be a non-empty string")
    status = status.strip().lower()
    report_id = report_id.strip() if report_id else None
    error_code = error_code.strip() if error_code else None
    error_message = error_message.strip() if error_message else None

    if not generation_id:
        return ToolResult(success=False, error="generation_id is required")
    if status not in REPORT_TASK_STATUSES:
        return ToolResult(
            success=False,
            error=f"status must be one of: {', '.join(REPORT_TASK_STATUSES)}",
        )
    if progress is not None and not 0 <= progress <= 100:
        return ToolResult(success=False, error="progress must be between 0 and 100")
    if revision is not None and revision < 0:
        return ToolResult(success=False, error="revision must be zero or greater")
    if not ctx.event_publish_callback:
        return ToolResult(success=False, error="SSE event publisher is unavailable")

    properties: dict[str, Any] = {
        "sessionID": ctx.session_id,
        "generationID": generation_id,
        "status": status,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    if progress is not None:
        properties["progress"] = progress
    if report_id:
        properties["reportID"] = report_id
    if revision is not None:
        properties["revision"] = revision
    if error_code or error_message:
        properties["error"] = {
            key: value
            for key, value in {
                "code": error_code,
                "message": error_message,
            }.items()
            if value
        }

    try:
        await ctx.event_publish_callback(REPORT_TASK_UPDATED_EVENT, properties)
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Failed to publish report task SSE event: {exc}",
        )

    return ToolResult(
        success=True,
        output=properties,
        title=f"Report task {status}",
        metadata=properties,
    )
