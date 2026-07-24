"""Tests for paired delegate_task subagent lifecycle hooks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.agent.delegate_task import _run_subagent_with_hooks
from flocks.tool.registry import ToolContext


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_subagent_hooks_wrap_child_run(resumed: bool) -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )
    last_message = SimpleNamespace(id="msg_child_final")
    loop_result = SimpleNamespace(
        action="stop",
        error=None,
        last_message=last_message,
    )
    start_hook = AsyncMock()
    stop_hook = AsyncMock()

    with (
        patch(
            "flocks.tool.agent.delegate_task.SessionLoop.run",
            AsyncMock(return_value=loop_result),
        ) as run_loop,
        patch(
            "flocks.tool.agent.delegate_task.Message.get_text_content",
            AsyncMock(return_value="child summary"),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_start",
            start_hook,
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_stop",
            stop_hook,
        ),
    ):
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
    start_payload = start_hook.await_args.args[0]
    assert start_payload["parentSessionID"] == "ses_parent"
    assert start_payload["childSessionID"] == "ses_child"
    assert start_payload["agentType"] == "explore"
    assert start_payload["resumed"] is resumed
    stop_payload = stop_hook.await_args.args[0]
    assert stop_payload["status"] == "completed"
    assert stop_payload["summary"] == "child summary"
    assert stop_payload["durationMs"] >= 0


@pytest.mark.asyncio
async def test_subagent_stop_reports_child_error() -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )
    start_hook = AsyncMock()
    stop_hook = AsyncMock()

    with (
        patch(
            "flocks.tool.agent.delegate_task.SessionLoop.run",
            AsyncMock(side_effect=RuntimeError("child failed")),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_start",
            start_hook,
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_stop",
            stop_hook,
        ),
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

    start_hook.assert_awaited_once()
    stop_hook.assert_awaited_once()
    stop_payload = stop_hook.await_args.args[0]
    assert stop_payload["status"] == "error"
    assert stop_payload["summary"] is None
    assert stop_payload["error"] == "child failed"


@pytest.mark.asyncio
async def test_subagent_stop_reports_interruption() -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )
    stop_hook = AsyncMock()

    with (
        patch(
            "flocks.tool.agent.delegate_task.SessionLoop.run",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_start",
            AsyncMock(),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_stop",
            stop_hook,
        ),
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

    stop_hook.assert_awaited_once()
    stop_payload = stop_hook.await_args.args[0]
    assert stop_payload["status"] == "interrupted"
    assert stop_payload["summary"] is None
    assert stop_payload["error"] == "Sub-agent execution was interrupted"


@pytest.mark.asyncio
async def test_subagent_stop_reports_normalized_loop_abort() -> None:
    ctx = ToolContext(
        session_id="ses_parent",
        message_id="msg_parent",
        agent="rex",
    )
    stop_hook = AsyncMock()
    loop_result = SimpleNamespace(
        action="stop",
        error=None,
        last_message=None,
        metadata={"aborted": True},
    )

    with (
        patch(
            "flocks.tool.agent.delegate_task.SessionLoop.run",
            AsyncMock(return_value=loop_result),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_start",
            AsyncMock(),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_subagent_stop",
            stop_hook,
        ),
    ):
        result = await _run_subagent_with_hooks(
            ctx=ctx,
            child_session_id="ses_child",
            child_agent="explore",
            workspace="/tmp/project",
            prompt="inspect hooks",
            description="Inspect hooks",
            resumed=False,
        )

    assert result is loop_result
    stop_hook.assert_awaited_once()
    stop_payload = stop_hook.await_args.args[0]
    assert stop_payload["status"] == "interrupted"
    assert stop_payload["summary"] is None
    assert stop_payload["error"] == "Sub-agent execution was interrupted"
