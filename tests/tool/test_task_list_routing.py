"""
Regression tests for task_list query routing matrix.

Covers:
- type='scheduled' + invalid execution status -> error
- Default (no params) lists only true scheduled tasks (scheduled_only=True)
- Valid type/status combinations route correctly
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.task.task_center import task_list

_TM_PATH = "flocks.task.manager.TaskManager"


def _ctx() -> ToolContext:
    return ToolContext(
        session_id="test",
        message_id="msg1",
        agent="test",
        call_id="call1",
    )


def _fake_scheduler(id_: str = "sched_1", status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        title="Daily scan",
        status=SimpleNamespace(value=status),
        priority=SimpleNamespace(value="normal"),
        mode=SimpleNamespace(value="cron"),
        trigger=SimpleNamespace(
            run_immediately=False,
            run_at=None,
            cron="0 8 * * *",
            next_run=None,
            cron_description=None,
            timezone="Asia/Shanghai",
        ),
        created_at=SimpleNamespace(isoformat=lambda: "2025-01-01T00:00:00+00:00"),
    )


def _fake_execution(id_: str = "exec_1", status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        title="Scan run #1",
        status=SimpleNamespace(value=status),
        priority=SimpleNamespace(value="normal"),
    )


# ------------------------------------------------------------------
# Issue 1: type='scheduled' + execution status -> must return error
# ------------------------------------------------------------------

_EXECUTION_ONLY_STATUSES = ["completed", "failed", "running", "pending", "queued", "cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", _EXECUTION_ONLY_STATUSES)
async def test_scheduled_type_with_execution_status_returns_error(bad_status: str):
    result = await task_list(_ctx(), status=bad_status, type="scheduled")
    assert not result.success
    assert "Invalid status" in result.error
    assert bad_status in result.error


@pytest.mark.asyncio
async def test_scheduled_type_with_valid_scheduler_status():
    mock_list = AsyncMock(return_value=([_fake_scheduler()], 1))
    with patch(_TM_PATH) as tm:
        tm.list_schedulers = mock_list
        result = await task_list(_ctx(), status="active", type="scheduled")
    assert result.success
    assert "1 total" in result.output


@pytest.mark.asyncio
async def test_scheduled_type_with_disabled_status():
    mock_list = AsyncMock(return_value=([_fake_scheduler(status="disabled")], 1))
    with patch(_TM_PATH) as tm:
        tm.list_schedulers = mock_list
        result = await task_list(_ctx(), status="disabled", type="scheduled")
    assert result.success
    mock_list.assert_called_once()
    call_kwargs = mock_list.call_args.kwargs
    assert call_kwargs["status"].value == "disabled"


# ------------------------------------------------------------------
# Issue 2: Default (no params) must pass scheduled_only=True
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_no_params_uses_scheduled_only():
    mock_list = AsyncMock(return_value=([], 0))
    with patch(_TM_PATH) as tm:
        tm.list_schedulers = mock_list
        result = await task_list(_ctx())
    assert result.success
    mock_list.assert_called_once()
    call_kwargs = mock_list.call_args.kwargs
    assert call_kwargs.get("scheduled_only") is True, (
        "Default task_list must pass scheduled_only=True to exclude "
        "run_immediately queue templates"
    )


@pytest.mark.asyncio
async def test_default_label_is_scheduled_tasks():
    mock_list = AsyncMock(return_value=([], 0))
    with patch(_TM_PATH) as tm:
        tm.list_schedulers = mock_list
        result = await task_list(_ctx())
    assert result.success
    assert "Scheduled tasks" in result.output


# ------------------------------------------------------------------
# Routing: execution statuses without explicit type go to executions
# ------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("exec_status", ["running", "completed", "failed", "pending", "queued", "cancelled"])
async def test_execution_status_routes_to_executions(exec_status: str):
    mock_list = AsyncMock(return_value=([_fake_execution(status=exec_status)], 1))
    with patch(_TM_PATH) as tm:
        tm.list_executions = mock_list
        result = await task_list(_ctx(), status=exec_status)
    assert result.success
    assert "Task executions" in result.output


# ------------------------------------------------------------------
# Invalid type parameter
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_type_returns_error():
    result = await task_list(_ctx(), type="bogus")
    assert not result.success
    assert "Invalid type" in result.error


# ------------------------------------------------------------------
# Explicit type='execution' with execution status works
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_execution_type_with_completed_status():
    mock_list = AsyncMock(return_value=([_fake_execution()], 1))
    with patch(_TM_PATH) as tm:
        tm.list_executions = mock_list
        result = await task_list(_ctx(), status="completed", type="execution")
    assert result.success
    assert "Task executions" in result.output
