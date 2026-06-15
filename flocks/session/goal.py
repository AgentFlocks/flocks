"""Persistent session goals."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

from flocks.provider.options import build_provider_options
from flocks.provider.provider import ChatMessage, Provider
from flocks.storage.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="session.goal")

DEFAULT_GOAL_MAX_TURNS = 20
JUDGE_RESPONSE_MAX_CHARS = 4096
JUDGE_MAX_TOKENS = 4096
GOAL_CLARIFICATION_MAX_CHARS = 2000
GoalStatus = Literal["active", "paused", "completed", "blocked"]
GoalVerdict = Literal["complete", "blocked", "continue", "waiting", "inactive"]

_MODEL_JUDGE_SYSTEM_PROMPT = """You are a strict goal completion judge.

Return only valid JSON with exactly this shape:
{"done": true|false, "reason": "one sentence"}

Judging rules:
- done=true only if the assistant's latest final response explicitly confirms the goal is complete, the requested deliverable is clearly produced, or the goal is impossible/blocked and the response clearly says why.
- done=false if work remains, the assistant only made partial progress, the assistant asks the user for more input/clarification/approval, or the latest response is ambiguous.
- The reason must be concise and grounded only in the provided goal, user clarification, and latest response.
- Keep the entire JSON response under 200 characters.
- Do not include markdown, code fences, or any text outside the JSON object.
"""


class GoalClarificationAnswer(BaseModel):
    question: str
    answer: str


class GoalClarification(BaseModel):
    answers: list[GoalClarificationAnswer]
    text: str
    created_at: float = Field(default_factory=time.time)
    message_id: Optional[str] = None
    call_id: Optional[str] = None


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
    initial_clarification: Optional[GoalClarification] = None


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


def _judge_input(last_response: str) -> str:
    text = last_response or ""
    if len(text) <= JUDGE_RESPONSE_MAX_CHARS:
        return text
    return text[-JUDGE_RESPONSE_MAX_CHARS:]


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _format_goal_context(objective: str, clarification: Optional[GoalClarification]) -> str:
    if clarification is None or not clarification.text.strip():
        return objective
    return (
        f"Original goal:\n{objective}\n\n"
        "Initial user clarification:\n"
        f"{clarification.text}"
    )


def _format_answer(answer: object) -> str:
    if isinstance(answer, list):
        return ", ".join(str(item).strip() for item in answer if str(item).strip())
    return str(answer or "").strip()


def _build_clarification(
    questions: list[dict],
    answers: list[list[str]],
    *,
    message_id: Optional[str] = None,
    call_id: Optional[str] = None,
) -> Optional[GoalClarification]:
    items: list[GoalClarificationAnswer] = []
    lines: list[str] = []
    for index, question in enumerate(questions):
        question_text = str(question.get("question") or "").strip()
        answer_text = _format_answer(answers[index] if index < len(answers) else [])
        if not question_text and not answer_text:
            continue
        if not question_text:
            question_text = f"Question {index + 1}"
        if not answer_text:
            answer_text = "Unanswered"
        items.append(GoalClarificationAnswer(question=question_text, answer=answer_text))
        lines.append(f'Q: {question_text}\nA: {answer_text}')

    if not items:
        return None

    return GoalClarification(
        answers=items,
        text=_truncate_text("\n\n".join(lines), GOAL_CLARIFICATION_MAX_CHARS),
        message_id=message_id,
        call_id=call_id,
    )


def _extract_json_object(text: str) -> dict:
    """Parse a strict JSON object."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty judge response")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge response was not strict JSON: {_trim_reason(raw)!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError("judge response is not a JSON object")
    return payload


async def judge_goal_with_model(
    objective: str,
    last_response: str,
    *,
    provider_id: str,
    model_id: str,
    initial_clarification: Optional[GoalClarification] = None,
) -> tuple[GoalVerdict, str]:
    """Hermes-style model judge using the active session provider/model."""
    provider = Provider.get(provider_id)
    if provider is None:
        raise RuntimeError(f"provider not found: {provider_id}")

    provider_options = build_provider_options(provider_id, model_id)
    provider_options.pop("max_tokens", None)

    response = await provider.chat(
        model_id=model_id,
        messages=[
            ChatMessage(role="system", content=_MODEL_JUDGE_SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=(
                    f"Goal:\n{_format_goal_context(objective, initial_clarification)}\n\n"
                    "Latest assistant final response (truncated to the last 4KB):\n"
                    f"{_judge_input(last_response)}"
                ),
            ),
        ],
        **provider_options,
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=0,
    )

    payload = _extract_json_object(response.content)
    done = payload.get("done")
    reason = _trim_reason(str(payload.get("reason") or ""))
    if not isinstance(done, bool):
        raise ValueError("judge JSON field 'done' must be a boolean")
    if not reason:
        reason = "model judge returned no reason"

    if not done:
        return "continue", reason

    return "complete", reason


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
    async def record_initial_clarification(
        cls,
        session_id: str,
        questions: list[dict],
        answers: list[list[str]],
        *,
        message_id: Optional[str] = None,
        call_id: Optional[str] = None,
    ) -> Optional[GoalState]:
        """Persist the first successful user clarification for an active goal."""
        state = await cls.get(session_id)
        if state is None or state.status != "active" or state.initial_clarification is not None:
            return state

        clarification = _build_clarification(
            questions,
            answers,
            message_id=message_id,
            call_id=call_id,
        )
        if clarification is None:
            return state

        state.initial_clarification = clarification
        return await cls.save(session_id, state)

    @classmethod
    def goal_prompt(cls, objective: str) -> str:
        return (
            "[Goal mode]\n"
            f"Active goal: {objective}\n\n"
            "If the active goal is ambiguous or underspecified, ask the user a "
            "clarifying question using the question tool and wait for the answer "
            "instead of continuing autonomously. "
            "Work toward the active goal. Continue taking concrete steps until the goal "
            "is complete or blocked. In your final response, make the current outcome "
            "clear with evidence of completed work or the specific blocker."
        )

    @classmethod
    def continuation_prompt(cls, state: GoalState, reason: str) -> str:
        reason = reason or "goal is still active"
        clarification = (
            "\nInitial user clarification:\n"
            f"{state.initial_clarification.text}\n"
            if state.initial_clarification is not None and state.initial_clarification.text.strip()
            else ""
        )
        return (
            "[Continuing toward active goal]\n"
            f"Goal: {state.objective}\n"
            f"{clarification}"
            f"Reason to continue: {reason}\n\n"
            "Take the next concrete step. If the goal is complete or blocked, make "
            "that outcome clear with evidence or the specific blocker."
        )

    @classmethod
    async def evaluate_after_turn(
        cls,
        session_id: str,
        last_response: str,
        *,
        pending_user_input: bool = False,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> GoalDecision:
        state = await cls.get(session_id)
        if state is None or state.status != "active":
            return GoalDecision(
                status=state.status if state else None,
                verdict="inactive",
                objective=state.objective if state else None,
            )

        state.turns_used += 1
        if pending_user_input:
            verdict = "waiting"
            reason = "session has a pending user question"
        elif provider_id and model_id:
            try:
                verdict, reason = await judge_goal_with_model(
                    state.objective,
                    last_response,
                    provider_id=provider_id,
                    model_id=model_id,
                    initial_clarification=state.initial_clarification,
                )
            except Exception as exc:
                log.warn("goal.model_judge.failed", {
                    "session_id": session_id,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "error": str(exc),
                })
                verdict = "waiting"
                reason = "goal judge failed; waiting instead of continuing autonomously"
        else:
            verdict = "waiting"
            reason = "goal judge unavailable; waiting instead of continuing autonomously"
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

        if verdict == "waiting":
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
