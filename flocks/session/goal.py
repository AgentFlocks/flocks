"""Persistent session goals."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

from flocks.storage.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="session.goal")

DEFAULT_GOAL_MAX_TURNS = 20
GoalStatus = Literal["active", "paused", "completed", "blocked"]
GoalVerdict = Literal["complete", "blocked", "continue", "inactive"]

_COMPLETE_PATTERNS = (
    re.compile(r"\bgoal\s+(?:complete|completed|achieved)\b", re.IGNORECASE),
    re.compile(r"\bstanding\s+goal\s+(?:is\s+)?(?:complete|completed|achieved)\b", re.IGNORECASE),
    re.compile(r"(?:目标|任务)已(?:经)?完成"),
    re.compile(r"(?:目标|任务)完成"),
)
_BLOCKED_PATTERNS = (
    re.compile(r"\bgoal\s+blocked\b", re.IGNORECASE),
    re.compile(r"\bblocked\s+on\s+(?:the\s+)?goal\b", re.IGNORECASE),
    re.compile(r"目标(?:已)?阻塞"),
    re.compile(r"任务(?:已)?阻塞"),
)


class GoalState(BaseModel):
    objective: str
    status: GoalStatus = "active"
    turns_used: int = 0
    max_turns: int = DEFAULT_GOAL_MAX_TURNS
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_verdict: Optional[GoalVerdict] = None
    last_reason: Optional[str] = None
    paused_reason: Optional[str] = None


@dataclass
class GoalDecision:
    status: GoalStatus | None
    verdict: GoalVerdict
    should_continue: bool = False
    continuation_prompt: Optional[str] = None
    reason: str = ""
    objective: Optional[str] = None


def _goal_key(session_id: str) -> str:
    return f"goal:{session_id}"


def _now() -> float:
    return time.time()


def _trim_reason(text: str, max_chars: int = 240) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _agent_self_report(last_response: str) -> tuple[GoalVerdict, str] | None:
    text = last_response or ""
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return "blocked", _trim_reason(text) or "agent reported the goal is blocked"
    for pattern in _COMPLETE_PATTERNS:
        if pattern.search(text):
            return "complete", _trim_reason(text) or "agent reported the goal is complete"
    return None


def judge_goal(last_response: str) -> tuple[GoalVerdict, str]:
    """Conservative fallback judge used when the agent did not self-report."""
    text = last_response or ""
    lower = text.lower()
    if "cannot proceed" in lower or "need user input" in lower:
        return "blocked", _trim_reason(text) or "judge found the agent needs user input"
    return "continue", "goal completion was not explicitly proven"


class GoalManager:
    """Session-scoped goal state and continuation policy."""

    @classmethod
    async def get(cls, session_id: str) -> Optional[GoalState]:
        try:
            data = await Storage.get(_goal_key(session_id))
        except Exception as exc:
            log.warn("goal.get.error", {"session_id": session_id, "error": str(exc)})
            return None
        if not data:
            return None
        try:
            return GoalState(**data)
        except Exception as exc:
            log.warn("goal.get.invalid", {"session_id": session_id, "error": str(exc)})
            return None

    @classmethod
    async def save(cls, session_id: str, state: GoalState) -> GoalState:
        state.updated_at = _now()
        await Storage.set(_goal_key(session_id), state.model_dump(exclude_none=True), "goal")
        return state

    @classmethod
    async def set_goal(
        cls,
        session_id: str,
        objective: str,
        *,
        max_turns: int = DEFAULT_GOAL_MAX_TURNS,
    ) -> GoalState:
        objective = (objective or "").strip()
        if not objective:
            raise ValueError("goal text is empty")
        state = GoalState(
            objective=objective,
            status="active",
            turns_used=0,
            max_turns=max_turns if max_turns > 0 else DEFAULT_GOAL_MAX_TURNS,
        )
        return await cls.save(session_id, state)

    @classmethod
    def goal_prompt(cls, objective: str) -> str:
        return (
            "[Goal mode]\n"
            f"Active goal: {objective}\n\n"
            "Work toward the active goal. When the goal is genuinely complete, "
            'explicitly state "Goal complete: ..." with the evidence. When blocked '
            'and unable to proceed without user input, explicitly state "Goal blocked: ...". '
            "Otherwise take the next concrete step and do not claim completion."
        )

    @classmethod
    def continuation_prompt(cls, state: GoalState, reason: str) -> str:
        reason = reason or "goal is still active"
        return (
            "[Continuing toward active goal]\n"
            f"Goal: {state.objective}\n"
            f"Reason to continue: {reason}\n\n"
            "Take the next concrete step. If the goal is genuinely complete, "
            'state "Goal complete: ..." with evidence. If blocked and unable '
            'to proceed without user input, state "Goal blocked: ...".'
        )

    @classmethod
    async def evaluate_after_turn(
        cls,
        session_id: str,
        last_response: str,
    ) -> GoalDecision:
        state = await cls.get(session_id)
        if state is None or state.status != "active":
            return GoalDecision(
                status=state.status if state else None,
                verdict="inactive",
                objective=state.objective if state else None,
            )

        state.turns_used += 1
        report = _agent_self_report(last_response)
        verdict, reason = report if report is not None else judge_goal(last_response)
        state.last_verdict = verdict
        state.last_reason = reason

        if verdict == "complete":
            state.status = "completed"
            await cls.save(session_id, state)
            return GoalDecision(
                status=state.status,
                verdict=verdict,
                reason=reason,
                objective=state.objective,
            )

        if verdict == "blocked":
            state.status = "blocked"
            await cls.save(session_id, state)
            return GoalDecision(
                status=state.status,
                verdict=verdict,
                reason=reason,
                objective=state.objective,
            )

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            await cls.save(session_id, state)
            return GoalDecision(
                status=state.status,
                verdict="continue",
                reason=state.paused_reason,
                objective=state.objective,
            )

        await cls.save(session_id, state)
        return GoalDecision(
            status=state.status,
            verdict="continue",
            should_continue=True,
            continuation_prompt=cls.continuation_prompt(state, reason),
            reason=reason,
            objective=state.objective,
        )
