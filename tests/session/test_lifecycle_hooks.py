"""Focused tests for the Python lifecycle-hook seams."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from flocks.hooks.pipeline import HookContext, HookStage
from flocks.session.goal import GoalDecision
from flocks.session.runner import SessionRunner, StepResult
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
            "_run_user_prompt_submit_hook",
            AsyncMock(),
        ) as submit_hook,
    ):
        for user in (first_user, first_user, synthetic_user):
            if await SessionLoop._prepare_auto_turn(ctx, user):
                await SessionLoop._run_user_prompt_submit_hook(ctx, user)

    assert ctx.turn_user_id == first_user.id
    submit_hook.assert_awaited_once_with(ctx, first_user)


@pytest.mark.asyncio
async def test_user_prompt_submit_adds_ephemeral_turn_context() -> None:
    ctx = _loop_context()
    user = SimpleNamespace(id="msg_user", agent="rex")
    run_hook = AsyncMock(
        return_value=HookContext(
            stage=HookStage.USER_PROMPT_SUBMIT,
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
            "flocks.hooks.pipeline.HookPipeline.run_user_prompt_submit",
            run_hook,
        ),
    ):
        await SessionLoop._run_user_prompt_submit_hook(ctx, user)

    assert ctx.turn_additional_context == "current sprint context"
    payload = run_hook.await_args.args[0]
    assert payload["messageID"] == user.id
    assert payload["prompt"] == "implement hooks"
    assert payload["model"] == {
        "providerID": "test-provider",
        "modelID": "test-model",
    }


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
    assert run_hook.await_args.args[0]["sessionID"] == "ses_session_start"


@pytest.mark.asyncio
async def test_turn_finish_block_creates_synthetic_continuation() -> None:
    ctx = _loop_context("ses_turn_finish")
    ctx.turn_user_id = "msg_user"
    user = SimpleNamespace(
        id="msg_user",
        agent="rex",
        model={"providerID": "test-provider", "modelID": "test-model"},
    )
    assistant = SimpleNamespace(
        id="msg_assistant",
        agent="rex",
        finish="stop",
    )
    continuation = SimpleNamespace(id="msg_continuation")
    callbacks = LoopCallbacks(event_publish_callback=AsyncMock())
    create_message = AsyncMock(return_value=continuation)
    run_hook = AsyncMock(
        return_value=HookContext(
            stage=HookStage.TURN_FINISH,
            input={},
            output={
                "decision": "block",
                "reason": "Run the test suite before finishing.",
            },
        )
    )

    with (
        patch(
            "flocks.session.session_loop.Message.get",
            AsyncMock(return_value=user),
        ),
        patch(
            "flocks.session.session_loop.Message.get_text_content",
            AsyncMock(side_effect=["implement hooks", "implementation complete"]),
        ),
        patch(
            "flocks.session.session_loop.Message.create",
            create_message,
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_turn_finish",
            run_hook,
        ),
        patch(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(steps=10)),
        ),
    ):
        continued = await SessionLoop._run_turn_finish_hook(
            ctx,
            callbacks,
            user,
            assistant,
        )

    assert continued is True
    assert ctx.stop_hook_active is True
    assert create_message.await_args.kwargs["content"] == ("Run the test suite before finishing.")
    assert create_message.await_args.kwargs["synthetic"] is True
    assert create_message.await_args.kwargs["part_metadata"] == {
        "turnFinishContinuation": True,
        "stopHookActive": True,
        "sourceAssistantMessageID": assistant.id,
    }
    hook_payload = run_hook.await_args.args[0]
    assert hook_payload["finishReason"] == "stop"
    assert hook_payload["stopHookActive"] is False
    callbacks.event_publish_callback.assert_awaited_once()
    assert callbacks.event_publish_callback.await_args.args[0] == "turn.continued"


@pytest.mark.asyncio
async def test_turn_finish_block_is_ignored_at_agent_step_limit() -> None:
    ctx = _loop_context("ses_turn_finish_limit")
    ctx.turn_user_id = "msg_user"
    ctx.trace_step_offset = 2
    ctx.step = 1
    user = SimpleNamespace(id="msg_user", agent="rex")
    assistant = SimpleNamespace(
        id="msg_assistant",
        agent="rex",
        finish="stop",
    )
    create_message = AsyncMock()

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
            "flocks.session.session_loop.Message.create",
            create_message,
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_turn_finish",
            AsyncMock(
                return_value=HookContext(
                    stage=HookStage.TURN_FINISH,
                    input={},
                    output={"decision": "block", "reason": "continue"},
                )
            ),
        ),
        patch(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(steps=3)),
        ),
    ):
        continued = await SessionLoop._run_turn_finish_hook(
            ctx,
            LoopCallbacks(),
            user,
            assistant,
        )

    assert continued is False
    create_message.assert_not_awaited()


def _message(
    message_id: str,
    role: str,
    *,
    finish: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        role=role,
        finish=finish,
        tokens=None,
        summary=False,
        agent="rex",
        model={"providerID": "test-provider", "modelID": "test-model"},
    )


@pytest.mark.asyncio
async def test_turn_finish_runs_only_after_persisted_stop() -> None:
    ctx = _loop_context("ses_turn_finish_integration")
    user = _message("msg_001", "user")
    assistant = _message("msg_002", "assistant", finish="stop")
    ctx.session_ctx = SimpleNamespace(
        get_messages=AsyncMock(
            side_effect=[
                [user],
                [user, assistant],
            ]
        )
    )
    run_turn_finish = AsyncMock(return_value=False)

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.session.session_loop.Message.get_text_content",
            AsyncMock(return_value="final response"),
        ),
        patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ),
        patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            AsyncMock(
                return_value=GoalDecision(
                    status="inactive",
                    verdict="inactive",
                )
            ),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_user_prompt_submit_hook",
            AsyncMock(),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_turn_finish_hook",
            run_turn_finish,
        ),
        patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ),
        patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ),
        patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ),
    ):
        result = await SessionLoop._run_loop(ctx, LoopCallbacks())

    assert result.action == "stop"
    run_turn_finish.assert_awaited_once_with(
        ctx,
        ANY,
        user,
        assistant,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step_result", "assistant_finish"),
    [
        (StepResult(action="stop", error="provider failed"), "error"),
        (StepResult(action="continue"), "tool-calls"),
    ],
)
async def test_turn_finish_skips_errors_and_tool_calls(
    step_result: StepResult,
    assistant_finish: str,
) -> None:
    ctx = _loop_context(f"ses_turn_finish_{assistant_finish}")
    user = _message("msg_001", "user")
    assistant = _message("msg_002", "assistant", finish=assistant_finish)
    ctx.session_ctx = SimpleNamespace(
        get_messages=AsyncMock(
            side_effect=[
                [user],
                [user, assistant],
            ]
        )
    )
    run_turn_finish = AsyncMock(return_value=False)

    async def process_step(*_args, **_kwargs):
        if step_result.action == "continue":
            ctx.signal_abort()
        return step_result

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_user_prompt_submit_hook",
            AsyncMock(),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_turn_finish_hook",
            run_turn_finish,
        ),
        patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(side_effect=process_step),
        ),
        patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ),
        patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ),
    ):
        result = await SessionLoop._run_loop(ctx, LoopCallbacks())

    assert result.action == "stop"
    run_turn_finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_user_message_takes_priority_over_turn_finish() -> None:
    ctx = _loop_context("ses_turn_finish_queue")
    user = _message("msg_001", "user")
    assistant = _message("msg_002", "assistant", finish="stop")
    queued_user = _message("msg_003", "user")
    ctx.session_ctx = SimpleNamespace(
        get_messages=AsyncMock(
            side_effect=[
                [user],
                [user, assistant, queued_user],
            ]
        )
    )
    run_turn_finish = AsyncMock(return_value=False)

    async def process_step(*_args, **_kwargs):
        ctx.signal_abort()
        return StepResult(action="stop")

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_user_prompt_submit_hook",
            AsyncMock(),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_turn_finish_hook",
            run_turn_finish,
        ),
        patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(side_effect=process_step),
        ),
        patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ),
        patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ),
    ):
        await SessionLoop._run_loop(ctx, LoopCallbacks())

    run_turn_finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_continuation_takes_priority_over_turn_finish() -> None:
    ctx = _loop_context("ses_turn_finish_goal")
    user = _message("msg_001", "user")
    assistant = _message("msg_002", "assistant", finish="stop")
    goal_user = _message("msg_003", "user")
    ctx.session_ctx = SimpleNamespace(
        get_messages=AsyncMock(
            side_effect=[
                [user],
                [user, assistant],
            ]
        )
    )
    run_turn_finish = AsyncMock(return_value=False)

    async def process_step(*_args, **_kwargs):
        ctx.signal_abort()
        return StepResult(action="stop")

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.session.session_loop.Message.get_text_content",
            AsyncMock(return_value="not done"),
        ),
        patch(
            "flocks.session.session_loop.Message.create",
            AsyncMock(return_value=goal_user),
        ),
        patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ),
        patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            AsyncMock(
                return_value=GoalDecision(
                    status="active",
                    verdict="continue",
                    should_continue=True,
                    continuation_prompt="continue the goal",
                )
            ),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_user_prompt_submit_hook",
            AsyncMock(),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_turn_finish_hook",
            run_turn_finish,
        ),
        patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(side_effect=process_step),
        ),
        patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ),
        patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ),
    ):
        await SessionLoop._run_loop(ctx, LoopCallbacks())

    run_turn_finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_abort_does_not_trigger_turn_finish() -> None:
    ctx = _loop_context("ses_turn_finish_abort")
    user = _message("msg_001", "user")
    ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=[user]))
    run_turn_finish = AsyncMock(return_value=False)

    with (
        patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_user_prompt_submit_hook",
            AsyncMock(),
        ),
        patch(
            "flocks.session.session_loop.SessionLoop._run_turn_finish_hook",
            run_turn_finish,
        ),
        patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ),
        patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ),
    ):
        await SessionLoop._run_loop(ctx, LoopCallbacks())

    run_turn_finish.assert_not_awaited()
