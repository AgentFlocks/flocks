"""Terminal loop outcomes must survive background supervision."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.task import background


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["caller", "inactivity", "question"])
async def test_watchdog_preserves_termination_when_loop_swallows_cancel(monkeypatch, cause):
    started = asyncio.Event()

    async def loop(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return SimpleNamespace(action="stop", metadata={})

    from flocks.server.routes import question

    monkeypatch.setattr(background.SessionLoop, "run", loop)
    monkeypatch.setattr(background, "_WATCHDOG_CHECK_INTERVAL", 0.001)
    monkeypatch.setattr(question, "has_pending_questions", lambda _: cause == "question")
    monkeypatch.setattr(question, "reject_session_questions", AsyncMock())
    task = background.BackgroundTask(id="test", status="running", description="audit", prompt="", agent="rex")
    task.last_activity_at = 1
    manager = background.BackgroundManager()
    running = asyncio.create_task(manager._run_session_with_watchdog(
        task, "test_session", None,
        timeout_seconds=0 if cause == "inactivity" else 10**12,
        allow_user_questions=False,
    ))
    await started.wait()
    if cause == "caller":
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    else:
        with pytest.raises(RuntimeError, match="无活跃交互" if cause == "inactivity" else "cannot wait for user input"):
            await asyncio.wait_for(running, 2)


@pytest.mark.asyncio
async def test_background_keeps_structured_quota_metadata(monkeypatch):
    metadata = {"error_code": "model_quota_exhausted"}
    monkeypatch.setattr(background.SessionLoop, "run", AsyncMock(return_value=SimpleNamespace(
        action="error", error="provider billing failure", metadata=metadata,
    )))
    manager = background.BackgroundManager()
    monkeypatch.setattr(manager, "_inject_parent_completion", AsyncMock())
    task = background.BackgroundTask(id="test", status="pending", description="audit", prompt="", agent="rex")
    await manager._run_existing_session(task, "test_session")
    assert task.status == "error"
    assert task.error == "provider billing failure"
    assert task.execution_metadata == metadata
