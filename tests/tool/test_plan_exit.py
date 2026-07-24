from __future__ import annotations

from pathlib import Path

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


def _context(
    events: list[tuple[str, dict]],
    tmp_path,
    *,
    plan_content: str = "# Approved plan\n",
) -> ToolContext:
    async def publish(event_type: str, properties: dict) -> None:
        events.append((event_type, properties))

    plan_path = tmp_path / ".flocks" / "plans" / "1234-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan_content, encoding="utf-8")
    return ToolContext(
        session_id="plan-exit-session",
        message_id="message-1",
        agent="rex",
        extra={
            "execution_mode": "plan",
            "workspace_dir": str(tmp_path),
            "model": {"providerID": "openai", "modelID": "gpt-test"},
            "plan_file_path": str(plan_path),
            "plan_relative_path": ".flocks/plans/1234-plan.md",
            "plan_permission_path": ".flocks/plans/1234-plan.md",
        },
        event_publish_callback=publish,
    )


@pytest.mark.asyncio
async def test_plan_exit_approval_queues_build_turn(monkeypatch, tmp_path) -> None:
    events: list[tuple[str, dict]] = []

    async def approve(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="approved",
            metadata={"answers": [[plan_exit.START_IMPLEMENTING]]},
        )

    monkeypatch.setattr(plan_exit, "question_tool", approve)

    result = await plan_exit.plan_exit_tool(_context(events, tmp_path))
    queued = await InteractionQueue.list("plan-exit-session")

    assert result.success
    assert result.metadata["approved"] is True
    assert len(queued) == 1
    assert queued[0].executionMode == SessionExecutionMode.BUILD
    assert queued[0].agent == "rex"
    assert queued[0].model == {"providerID": "openai", "modelID": "gpt-test"}
    assert ".flocks/plans/1234-plan.md" in queued[0].parts[0]["text"]
    assert result.metadata["planPath"] == ".flocks/plans/1234-plan.md"
    assert [event_type for event_type, _ in events] == [
        "session.prompt_queue.updated",
        "session.execution_mode.changed",
    ]


@pytest.mark.asyncio
async def test_plan_exit_decline_stays_in_plan(monkeypatch, tmp_path) -> None:
    async def decline(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="declined",
            metadata={"answers": [[plan_exit.CONTINUE_PLANNING]]},
        )

    monkeypatch.setattr(plan_exit, "question_tool", decline)

    result = await plan_exit.plan_exit_tool(_context([], tmp_path))

    assert result.success
    assert result.metadata == {"approved": False, "executionMode": "plan"}
    assert await InteractionQueue.list("plan-exit-session") == []


@pytest.mark.asyncio
async def test_plan_exit_does_not_approve_deferred_channel_question(
    monkeypatch,
    tmp_path,
) -> None:
    async def deferred(*_args, **_kwargs):
        return ToolResult(
            success=True,
            output="sent",
            metadata={"deferred": True},
        )

    monkeypatch.setattr(plan_exit, "question_tool", deferred)

    result = await plan_exit.plan_exit_tool(_context([], tmp_path))

    assert result.metadata["deferred"] is True
    assert await InteractionQueue.list("plan-exit-session") == []


@pytest.mark.asyncio
async def test_plan_exit_requires_non_empty_plan_file(tmp_path) -> None:
    missing_ctx = _context([], tmp_path)
    missing_path = missing_ctx.extra["plan_file_path"]
    Path(missing_path).unlink()

    missing = await plan_exit.plan_exit_tool(missing_ctx)
    empty = await plan_exit.plan_exit_tool(
        _context([], tmp_path, plan_content=" \n")
    )

    assert not missing.success
    assert "Write the session plan file" in (missing.error or "")
    assert not empty.success
    assert "empty" in (empty.error or "")
