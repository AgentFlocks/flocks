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

    decision = await GoalManager.evaluate_after_turn(
        session_id,
        "Implemented the feature, updated the tests, and the focused test suite passed.",
    )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "complete"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "completed"


@pytest.mark.asyncio
async def test_goal_evaluation_blocks_when_judge_finds_blocker():
    session_id = "goal_blocked_session"
    await GoalManager.set_goal(session_id, "finish implementation")

    decision = await GoalManager.evaluate_after_turn(
        session_id,
        "I cannot proceed because the repository is unavailable.",
    )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "blocked"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "blocked"


@pytest.mark.asyncio
async def test_goal_evaluation_waits_when_agent_asks_for_clarification():
    session_id = "goal_waiting_session"
    await GoalManager.set_goal(session_id, "write tests 10 times")

    decision = await GoalManager.evaluate_after_turn(
        session_id,
        "Please clarify what tests to write and where to place them.",
    )
    state = await GoalManager.get(session_id)

    assert decision.verdict == "waiting"
    assert decision.should_continue is False
    assert state is not None
    assert state.status == "active"
    assert state.last_verdict == "waiting"


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
    verdict, reason = judge_goal("finish implementation", "I made progress but the work is not complete.")

    assert verdict == "continue"
    assert reason == "judge found remaining work toward the goal"


def test_judge_goal_does_not_complete_when_response_mentions_next_step():
    verdict, reason = judge_goal("ship branch", "All tests pass. Next I will push the branch.")

    assert verdict == "continue"
    assert reason == "judge found remaining work toward the goal"


def test_judge_goal_waits_on_user_clarification():
    verdict, reason = judge_goal(
        "write tests 10 times",
        "I need to clarify what tests to write 10 times.",
    )

    assert verdict == "waiting"
    assert "need to clarify" in reason


def test_judge_goal_completes_on_obvious_delivery():
    verdict, reason = judge_goal("fix tests", "Fixed the failing tests and verified pytest passed.")

    assert verdict == "complete"
    assert "Fixed the failing tests" in reason
