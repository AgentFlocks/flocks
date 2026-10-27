"""Preparing input and running a session can share one caller-owned lease."""

from unittest.mock import AsyncMock, Mock

import pytest

from flocks.content.managed import current_task_scope
from flocks.session.ownership import SessionExecutionLease, current_lease, outside_execution
from flocks.session.session_loop import LoopResult, SessionLoop


async def test_run_borrows_entered_lease_and_retains_ownership(monkeypatch):
    session_id = "borrowed-loop"
    lease = await SessionExecutionLease.acquire(session_id)
    assert lease is not None
    seen = []

    async def run_owned(session_id, **kwargs):
        seen.append((current_lease(session_id), current_task_scope(), kwargs))
        # A request with no ownership context still cannot acquire this run.
        with outside_execution():
            assert await SessionExecutionLease.acquire(session_id) is None
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "_run_owned", run_owned)
    async with lease:
        result = await SessionLoop.run(session_id, agent_name="reviewer", model_id="configured")
        assert result.action == "stop"
        assert current_lease(session_id) is lease
        assert not lease.released
        assert seen == [(lease, f"session:{session_id}#{lease.generation}", {
            "provider_id": None, "model_id": "configured", "agent_name": "reviewer",
            "callbacks": None, "working_directory": None, "auto_failover": False,
        })]
    assert lease.released
    assert current_lease(session_id) is None


@pytest.mark.parametrize("lose_during_run", [False, True])
async def test_borrowed_lease_is_checked_before_and_after_run(lose_during_run, monkeypatch):
    session_id = f"borrowed-lost-{lose_during_run}"
    lease = await SessionExecutionLease.acquire(session_id)
    assert lease is not None

    async def run_owned(*args, **kwargs):
        lease.lost = True
        return LoopResult(action="stop")

    runner = AsyncMock(side_effect=run_owned)
    monkeypatch.setattr(SessionLoop, "_run_owned", runner)
    async with lease:
        if not lose_during_run:
            lease.lost = True
        result = await SessionLoop.run(session_id)
        assert result.action == "error"
        assert "ownership lost" in result.error
        assert not lease.released
    assert runner.await_count == int(lose_during_run)


async def test_other_session_context_cannot_borrow_held_target_lease(monkeypatch):
    target = await SessionExecutionLease.acquire("held-target")
    other = await SessionExecutionLease.acquire("other-session")
    assert target is not None and other is not None
    runner = AsyncMock()
    handed_off = Mock()
    monkeypatch.setattr(SessionLoop, "_run_owned", runner)
    monkeypatch.setattr("flocks.session.turn_receipt.note_handed_off", handed_off)
    try:
        async with other:
            result = await SessionLoop.run("held-target")
        assert result.action == "queued"
        assert not target.released
        runner.assert_not_awaited()
        handed_off.assert_called_once()
    finally:
        await target.release()


async def test_borrowed_run_keeps_in_process_queue_guard(monkeypatch):
    session_id = "borrowed-running"
    lease = await SessionExecutionLease.acquire(session_id)
    assert lease is not None
    handed_off = Mock()
    monkeypatch.setattr("flocks.session.turn_receipt.note_handed_off", handed_off)
    monkeypatch.setitem(SessionLoop._active_loops, session_id, object())
    async with lease:
        result = await SessionLoop.run(session_id)
        assert result.action == "queued"
        assert not lease.released
        handed_off.assert_called_once()


async def test_normal_run_still_owns_and_releases_its_lease(monkeypatch):
    observed = []

    async def run_owned(session_id, **kwargs):
        observed.append(current_lease(session_id))
        assert current_task_scope() == f"session:{session_id}#{observed[-1].generation}"
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "_run_owned", run_owned)
    result = await SessionLoop.run("ordinary-run")
    assert result.action == "stop"
    assert observed[0].released
    assert current_lease("ordinary-run") is None
