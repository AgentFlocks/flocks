"""Focused tests for session lifecycle hook integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.pipeline import HookContext, HookStage
from flocks.session.runner import SessionRunner
from flocks.session.session import SessionInfo
from flocks.session.session_loop import LoopCallbacks, LoopContext, SessionLoop


def _session(session_id: str = "ses_lifecycle_hooks") -> SessionInfo:
    return SessionInfo.model_construct(
        id=session_id,
        slug="hooks",
        project_id="project",
        directory="/tmp/project",
        title="Lifecycle Hooks",
        agent="rex",
    )


def _loop_context(session_id: str = "ses_lifecycle_hooks") -> LoopContext:
    return LoopContext(
        session=_session(session_id),
        provider_id="test-provider",
        model_id="test-model",
        agent_name="rex",
    )


@pytest.mark.asyncio
async def test_real_user_turn_is_detected_once_and_synthetic_is_ignored() -> None:
    ctx = _loop_context()
    first_user = SimpleNamespace(id="msg_user", model={})
    synthetic_user = SimpleNamespace(id="msg_synthetic", model={})

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(
                side_effect=[
                    [],
                    [SimpleNamespace(synthetic=True)],
                ]
            ),
        ),
        patch.object(
            SessionLoop,
            "_run_user_prompt_before_hook",
            AsyncMock(),
        ) as prompt_hook,
    ):
        for user in (first_user, first_user, synthetic_user):
            if await SessionLoop._prepare_auto_turn(ctx, user):
                await SessionLoop._run_user_prompt_before_hook(ctx, user)

    assert ctx.turn_user_id == first_user.id
    prompt_hook.assert_awaited_once_with(ctx, first_user)


@pytest.mark.asyncio
async def test_user_prompt_before_adds_ephemeral_turn_context() -> None:
    ctx = _loop_context()
    user = SimpleNamespace(id="msg_user", agent="rex")
    run_hook = AsyncMock(
        return_value=HookContext(
            stage=HookStage.USER_PROMPT_BEFORE,
            input={},
            output={"additionalContext": "  current sprint context  "},
        )
    )

    with (
        patch(
            "flocks.session.session_loop.Message.get_text_content",
            AsyncMock(return_value="implement hooks"),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_user_prompt_before",
            run_hook,
        ),
    ):
        await SessionLoop._run_user_prompt_before_hook(ctx, user)

    assert ctx.turn_additional_context == "current sprint context"
    payload = run_hook.await_args.args[0]
    assert payload["messageID"] == user.id
    assert payload["prompt"] == "implement hooks"


@pytest.mark.asyncio
async def test_session_start_runs_only_when_pending() -> None:
    runner = SessionRunner(
        session=_session("ses_session_start"),
        provider_id="test-provider",
        model_id="test-model",
        session_start_pending=True,
    )
    run_hook = AsyncMock()

    with patch(
        "flocks.session.runner.HookPipeline.run_session_start",
        run_hook,
    ):
        await runner._run_session_start_hook(SimpleNamespace(name="rex"))
        await runner._run_session_start_hook(SimpleNamespace(name="rex"))

    run_hook.assert_awaited_once()
    assert runner._session_start_fired is True


@pytest.mark.asyncio
async def test_turn_after_observes_terminal_outcome_without_continuation() -> None:
    ctx = _loop_context("ses_turn_after")
    ctx.turn_user_id = "msg_user"
    user = SimpleNamespace(id="msg_user", agent="rex")
    assistant = SimpleNamespace(id="msg_assistant", agent="rex", finish="stop")
    callbacks = LoopCallbacks(event_publish_callback=AsyncMock())
    run_hook = AsyncMock(return_value=HookContext(stage=HookStage.TURN_AFTER, input={}, output={}))

    with (
        patch(
            "flocks.session.session_loop.Message.get",
            AsyncMock(return_value=user),
        ),
        patch(
            "flocks.session.session_loop.Message.get_text_content",
            AsyncMock(side_effect=["prompt", "response"]),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_turn_after",
            run_hook,
        ),
    ):
        continued = await SessionLoop._run_turn_after_hook(
            ctx,
            callbacks,
            user,
            assistant,
        )

    assert continued is False
    payload = run_hook.await_args.args[0]
    assert payload["terminalOutcome"]["status"] == "success"
    callbacks.event_publish_callback.assert_not_awaited()
