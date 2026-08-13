"""Tests for delegate_task subagent runner helper."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.agent.delegate_task import _run_subagent_with_hooks
from flocks.tool.registry import ToolContext


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_subagent_runner_executes_child_loop(resumed: bool) -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )
    loop_result = SimpleNamespace(action="stop", error=None, last_message=None)

    with patch(
        "flocks.tool.agent.delegate_task.SessionLoop.run",
        AsyncMock(return_value=loop_result),
    ) as run_loop:
        result = await _run_subagent_with_hooks(
            ctx=ctx,
            child_session_id="ses_child",
            child_agent="explore",
            workspace="/tmp/project",
            prompt="inspect hooks",
            description="Inspect hooks",
            resumed=resumed,
        )

    assert result is loop_result
    run_loop.assert_awaited_once_with(
        "ses_child",
        provider_id=None,
        model_id=None,
        callbacks=None,
    )


@pytest.mark.asyncio
async def test_subagent_runner_propagates_child_error() -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )

    with patch(
        "flocks.tool.agent.delegate_task.SessionLoop.run",
        AsyncMock(side_effect=RuntimeError("child failed")),
    ):
        with pytest.raises(RuntimeError, match="child failed"):
            await _run_subagent_with_hooks(
                ctx=ctx,
                child_session_id="ses_child",
                child_agent="explore",
                workspace="/tmp/project",
                prompt="inspect hooks",
                description="Inspect hooks",
                resumed=False,
            )


@pytest.mark.asyncio
async def test_subagent_runner_propagates_cancellation() -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )

    with patch(
        "flocks.tool.agent.delegate_task.SessionLoop.run",
        AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _run_subagent_with_hooks(
                ctx=ctx,
                child_session_id="ses_child",
                child_agent="explore",
                workspace="/tmp/project",
                prompt="inspect hooks",
                description="Inspect hooks",
                resumed=True,
            )
