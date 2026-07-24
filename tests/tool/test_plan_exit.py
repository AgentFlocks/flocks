from __future__ import annotations

import pytest

from flocks.session.execution_mode import SessionExecutionMode
from flocks.session.interaction_queue import InteractionQueue
from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.system import plan_exit


@pytest.fixture(autouse=True)
async def clear_queue():
    session_id = "plan-exit-session"
    await InteractionQueue.clear(session_id)
    yield
    await InteractionQueue.clear(session_id)


def _context(events: list[tuple[str, dict]]) -> ToolContext:
    async def publish(event_type: str, properties: dict) -> None:
        events.append((event_type, properties))

    return ToolContext(
        session_id="plan-exit-session",
        message_id="message-1",
        agent="rex",
        extra={"execution_mode": "plan"},
        event_publish_callback=publish,
    )


@pytest.mark.asyncio
async def test_plan_exit_approval_queues_build_turn(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []

    async def approve(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="approved",
            metadata={"answers": [[plan_exit.START_IMPLEMENTING]]},
        )

    monkeypatch.setattr(plan_exit, "question_tool", approve)

    result = await plan_exit.plan_exit_tool(_context(events))
    queued = await InteractionQueue.list("plan-exit-session")

    assert result.success
    assert result.metadata["approved"] is True
    assert len(queued) == 1
    assert queued[0].executionMode == SessionExecutionMode.BUILD
    assert queued[0].agent == "rex"
    assert [event_type for event_type, _ in events] == [
        "session.prompt_queue.updated",
        "session.execution_mode.changed",
    ]


@pytest.mark.asyncio
async def test_plan_exit_decline_stays_in_plan(monkeypatch) -> None:
    async def decline(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="declined",
            metadata={"answers": [[plan_exit.CONTINUE_PLANNING]]},
        )

    monkeypatch.setattr(plan_exit, "question_tool", decline)

    result = await plan_exit.plan_exit_tool(_context([]))

    assert result.success
    assert result.metadata == {"approved": False, "executionMode": "plan"}
    assert await InteractionQueue.list("plan-exit-session") == []


@pytest.mark.asyncio
async def test_plan_exit_does_not_approve_deferred_channel_question(monkeypatch) -> None:
    async def deferred(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="sent",
            metadata={"deferred": True},
        )

    monkeypatch.setattr(plan_exit, "question_tool", deferred)

    result = await plan_exit.plan_exit_tool(_context([]))

    assert result.metadata["deferred"] is True
    assert await InteractionQueue.list("plan-exit-session") == []
