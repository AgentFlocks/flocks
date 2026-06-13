from __future__ import annotations

import pytest

from flocks.command.direct import run_direct_command
from flocks.session.goal import GoalManager, judge_goal


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
    assert "Goal complete:" in result.prompt

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
async def test_goal_evaluation_prefers_agent_self_report():
    session_id = "goal_complete_session"
    await GoalManager.set_goal(session_id, "finish implementation")

    decision = await GoalManager.evaluate_after_turn(
        session_id,
        "Goal complete: implementation and tests are done.",
    )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "complete"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "completed"


@pytest.mark.asyncio
async def test_goal_evaluation_continues_until_budget_then_pauses():
    session_id = "goal_budget_session"
    state = await GoalManager.set_goal(session_id, "keep going", max_turns=1)

    decision = await GoalManager.evaluate_after_turn(session_id, "I made progress.")
    state = await GoalManager.get(session_id)

    assert decision.verdict == "continue"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "turn budget exhausted (1/1)"


def test_judge_goal_is_conservative_fallback():
    verdict, reason = judge_goal("I made progress but the work is not complete.")

    assert verdict == "continue"
    assert reason == "goal completion was not explicitly proven"


def test_judge_goal_does_not_complete_on_tests_only():
    verdict, reason = judge_goal("All tests pass. Next I will push the branch.")

    assert verdict == "continue"
    assert reason == "goal completion was not explicitly proven"
