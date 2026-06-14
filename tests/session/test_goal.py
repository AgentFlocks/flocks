from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.command.direct import run_direct_command
from flocks.session.goal import JUDGE_MAX_TOKENS, GoalManager


@pytest.mark.asyncio
async def test_goal_command_sets_state_and_prompt():
    result = await run_direct_command(
        "goal",
        args="fix failing tests",
        session_id="goal_command_session",
    )

    assert result.handled is True
    assert result.text is None
    assert result.prompt is not None
    assert "Active goal: fix failing tests" in result.prompt
    assert "specific blocker" in result.prompt

    state = await GoalManager.get("goal_command_session")

    assert state is not None
    assert state.status == "active"
    assert state.objective == "fix failing tests"


@pytest.mark.asyncio
async def test_goal_command_rejects_empty_objective():
    result = await run_direct_command(
        "goal",
        args="",
        session_id="goal_empty_session",
    )

    assert result.handled is True
    assert result.success is False
    assert result.text == "Usage: /goal <objective>"
    assert result.prompt is None


@pytest.mark.asyncio
async def test_goal_evaluation_completes_when_judge_finds_done():
    session_id = "goal_complete_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": true, "reason": "The final response says the implementation and tests are complete."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Implemented the feature, updated the tests, and the focused test suite passed.",
            provider_id="test-provider",
            model_id="test-model",
        )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "complete"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "completed"


@pytest.mark.asyncio
async def test_goal_evaluation_completes_when_judge_finds_goal_unachievable():
    session_id = "goal_blocked_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": true, "reason": "The repository is unavailable, so the goal is blocked."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "I cannot proceed because the repository is unavailable.",
            provider_id="test-provider",
            model_id="test-model",
        )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "complete"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "completed"


@pytest.mark.asyncio
async def test_goal_evaluation_waits_when_agent_asks_for_clarification():
    session_id = "goal_waiting_session"
    await GoalManager.set_goal(session_id, "write tests 10 times")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": false, "reason": "The assistant is asking the user for clarification."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Please clarify what tests to write and where to place them.",
            provider_id="test-provider",
            model_id="test-model",
        )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "continue"
    assert decision.should_continue is True
    assert state is not None
    assert state.status == "active"
    assert state.last_verdict == "continue"


@pytest.mark.asyncio
async def test_goal_evaluation_waits_when_runtime_has_pending_user_input():
    session_id = "goal_pending_user_input_session"
    await GoalManager.set_goal(session_id, "triage phishing email")

    decision = await GoalManager.evaluate_after_turn(
        session_id,
        "I made progress and can continue.",
        pending_user_input=True,
    )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "waiting"
    assert decision.should_continue is False
    assert decision.reason == "session has a pending user question"
    assert state is not None
    assert state.status == "active"
    assert state.last_verdict == "waiting"


@pytest.mark.asyncio
async def test_goal_evaluation_continues_until_budget_then_pauses():
    session_id = "goal_budget_session"
    state = await GoalManager.set_goal(session_id, "keep going", max_turns=1)
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": false, "reason": "The work is not complete yet."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "I made progress.",
            provider_id="test-provider",
            model_id="test-model",
        )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "continue"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "turn budget exhausted (1/1)"


@pytest.mark.asyncio
async def test_goal_evaluation_uses_model_judge_when_provider_model_are_available():
    session_id = "goal_model_judge_complete_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": true, "reason": "The final response says the implementation and tests are complete."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Implemented the feature and tests passed.",
            provider_id="test-provider",
            model_id="test-model",
        )

    provider.chat.assert_awaited_once()
    assert decision.verdict == "complete"
    assert decision.reason == "The final response says the implementation and tests are complete."


@pytest.mark.asyncio
async def test_goal_model_judge_uses_provider_options_without_main_token_budget():
    session_id = "goal_model_judge_provider_options_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": true, "reason": "The final response says the goal is complete."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider), patch(
        "flocks.session.goal.build_provider_options",
        return_value={"extra_body": {"reasoning_split": True}, "max_tokens": 128000},
    ) as build_options:
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Goal is complete.",
            provider_id="test-provider",
            model_id="test-model",
        )

    build_options.assert_called_once_with("test-provider", "test-model")
    provider.chat.assert_awaited_once()
    kwargs = provider.chat.await_args.kwargs
    assert kwargs["extra_body"] == {"reasoning_split": True}
    assert kwargs["max_tokens"] == JUDGE_MAX_TOKENS
    assert kwargs["temperature"] == 0
    assert decision.verdict == "complete"


@pytest.mark.asyncio
async def test_goal_evaluation_continues_when_model_judge_says_not_done():
    session_id = "goal_model_judge_continue_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(
            content='{"done": false, "reason": "The response says more work remains."}'
        ))
    )

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "I made progress.",
            provider_id="test-provider",
            model_id="test-model",
        )

    provider.chat.assert_awaited_once()
    assert decision.verdict == "continue"
    assert decision.should_continue is True
    assert decision.reason == "The response says more work remains."


@pytest.mark.asyncio
async def test_goal_evaluation_waits_when_model_judge_fails():
    session_id = "goal_model_judge_failure_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(chat=AsyncMock(side_effect=RuntimeError("judge unavailable")))

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Implemented the feature and tests passed.",
            provider_id="test-provider",
            model_id="test-model",
        )

    provider.chat.assert_awaited_once()
    assert decision.verdict == "waiting"
    assert decision.should_continue is False
    assert decision.reason == "goal judge failed; waiting instead of continuing autonomously"


@pytest.mark.asyncio
async def test_goal_evaluation_skips_model_judge_when_waiting_for_user_input():
    session_id = "goal_model_judge_pending_input_session"
    await GoalManager.set_goal(session_id, "finish implementation")
    provider = SimpleNamespace(chat=AsyncMock())

    with patch("flocks.session.goal.Provider.get", return_value=provider):
        decision = await GoalManager.evaluate_after_turn(
            session_id,
            "Please provide more input.",
            pending_user_input=True,
            provider_id="test-provider",
            model_id="test-model",
        )

    provider.chat.assert_not_awaited()
    assert decision.verdict == "waiting"
    assert decision.should_continue is False
