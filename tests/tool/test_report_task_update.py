from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.tool.system.report_task_update import report_task_update_tool


@pytest.mark.asyncio
async def test_report_task_update_publishes_session_scoped_sse_event() -> None:
    publish = AsyncMock()
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        agent="report-generator",
        event_publish_callback=publish,
    )

    result = await report_task_update_tool(
        ctx,
        generation_id="generation-1",
        status="completed",
        progress=100,
        report_id="report-1",
        revision=4,
    )

    assert result.success is True
    publish.assert_awaited_once()
    event_type, properties = publish.await_args.args
    assert event_type == "report.task.updated"
    assert properties == {
        "sessionID": "session-1",
        "generationID": "generation-1",
        "status": "completed",
        "progress": 100,
        "reportID": "report-1",
        "revision": 4,
        "updatedAt": properties["updatedAt"],
    }
    assert properties["updatedAt"].endswith("+00:00")
    assert result.metadata == properties


@pytest.mark.asyncio
async def test_report_task_update_rejects_invalid_progress() -> None:
    publish = AsyncMock()
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        event_publish_callback=publish,
    )

    result = await report_task_update_tool(
        ctx,
        generation_id="generation-1",
        status="running",
        progress=101,
    )

    assert result.success is False
    assert "between 0 and 100" in (result.error or "")
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_task_update_rejects_non_string_generation_id() -> None:
    publish = AsyncMock()
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        event_publish_callback=publish,
    )

    result = await report_task_update_tool(
        ctx,
        generation_id=None,
        status="running",
    )
    coerced_result = await ToolRegistry.execute(
        "report_task_update",
        ctx=ctx,
        generation_id=None,
        status="running",
    )

    assert result.success is False
    assert "non-empty string" in (result.error or "")
    assert coerced_result.success is False
    assert "non-empty string" in (coerced_result.error or "")
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_task_update_requires_sse_publisher() -> None:
    ctx = ToolContext(session_id="session-1", message_id="message-1")

    result = await report_task_update_tool(
        ctx,
        generation_id="generation-1",
        status="completed",
    )

    assert result.success is False
    assert "SSE event publisher" in (result.error or "")


def test_report_task_update_is_registered_with_status_schema() -> None:
    ToolRegistry.init()

    tool = ToolRegistry.get("report_task_update")
    schema = ToolRegistry.get_schema("report_task_update")

    assert tool is not None
    assert tool.info.native is True
    assert schema is not None
    assert schema.properties["status"]["enum"] == [
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    ]
    assert schema.required == ["generation_id", "status"]
