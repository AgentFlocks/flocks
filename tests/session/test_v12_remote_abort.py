"""An abort asked for on one node reaches the node that runs the session.

The run's execution lease carries the request; the owner's lease loop picks it
up and aborts the run where it actually runs."""

from __future__ import annotations

import asyncio
import os

import pytest

from flocks.session import ownership
from flocks.session.ownership import SessionExecutionLease, request_abort

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="isolated PostgreSQL required")


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(ownership, "ABORT_POLL_INTERVAL_S", 0.05)
    previous = ownership._abort_handler
    yield
    ownership.set_abort_handler(previous)


async def test_an_abort_from_another_node_reaches_the_owner():
    aborted: list[str] = []

    async def _abort_here(session_id: str) -> None:
        aborted.append(session_id)

    ownership.set_abort_handler(_abort_here)
    lease = await SessionExecutionLease.acquire("ses_remote_abort", duration=30)
    assert lease is not None
    async with lease:
        assert await request_abort("ses_remote_abort") is True  # as another node would
        for _ in range(100):
            if aborted:
                break
            await asyncio.sleep(0.02)
    assert aborted == ["ses_remote_abort"]


async def test_without_an_abort_path_the_run_is_stopped_outright():
    ownership.set_abort_handler(None)
    lease = await SessionExecutionLease.acquire("ses_remote_cancel", duration=30)
    assert lease is not None

    async def _run() -> None:
        async with lease:
            await asyncio.sleep(5)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    assert await request_abort("ses_remote_cancel") is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


async def test_nothing_to_abort_when_nobody_runs_the_session():
    assert await request_abort("ses_not_running") is False


async def test_a_new_run_does_not_inherit_an_old_abort_request():
    aborted: list[str] = []
    ownership.set_abort_handler(lambda session_id: aborted.append(session_id))
    first = await SessionExecutionLease.acquire("ses_reused", duration=0.2)
    assert first is not None
    await request_abort("ses_reused")
    await first.release()

    second = await SessionExecutionLease.acquire("ses_reused", duration=30)
    assert second is not None
    async with second:
        await asyncio.sleep(0.3)
    assert aborted == []


# ---------------------------------------------------------------------------
# What an abort came to (G12)
# ---------------------------------------------------------------------------


async def _owner(session_id: str, *, stop_on_abort: bool) -> tuple[SessionExecutionLease, asyncio.Task]:
    """A node running *session_id*: its run ends on an abort when stop_on_abort."""
    stopped = asyncio.Event()

    async def _abort_here(sid: str) -> None:
        if stop_on_abort and sid == session_id:
            stopped.set()

    ownership.set_abort_handler(_abort_here)
    lease = await SessionExecutionLease.acquire(session_id, duration=30)
    assert lease is not None

    async def _run() -> None:
        async with lease:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    return lease, task


async def test_an_abort_from_another_node_is_confirmed_once_the_run_has_stopped():
    _lease, task = await _owner("ses_g12_stop", stop_on_abort=True)
    target = await ownership.request_abort_of_run("ses_g12_stop")
    assert target is not None and target.generation >= 1

    assert await ownership.confirm_abort(target, timeout=3) == ownership.ABORT_STOPPED
    await asyncio.wait_for(task, timeout=2)
    # Asking again once it stopped: nothing is running any more.
    assert await ownership.request_abort_of_run("ses_g12_stop") is None


async def test_a_node_that_took_the_request_but_is_still_stopping_reads_as_requested():
    lease, task = await _owner("ses_g12_slow", stop_on_abort=False)
    try:
        target = await ownership.request_abort_of_run("ses_g12_slow")
        again = await ownership.request_abort_of_run("ses_g12_slow")
        assert again == target, "asking twice names the same run"
        assert await ownership.confirm_abort(target, timeout=0.6) == ownership.ABORT_REQUESTED
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_node_that_does_not_answer_reads_as_unconfirmed():
    # Holding the lease without running its loop: a node that died, or hangs.
    lease = await SessionExecutionLease.acquire("ses_g12_lost", duration=30)
    assert lease is not None
    try:
        target = await ownership.request_abort_of_run("ses_g12_lost")
        assert await ownership.confirm_abort(target, timeout=0.4) == ownership.ABORT_UNCONFIRMED
    finally:
        await lease.release()


async def test_a_request_made_for_an_earlier_run_is_not_this_runs():
    from flocks.storage.storage import Storage

    aborted: list[str] = []
    ownership.set_abort_handler(lambda session_id: aborted.append(session_id))
    first = await SessionExecutionLease.acquire("ses_g12_old", duration=30)
    target = await ownership.request_abort_of_run("ses_g12_old")
    await first.release()
    second = await SessionExecutionLease.acquire("ses_g12_old", duration=30)
    assert second is not None and second.generation == target.generation + 1
    # A request for the earlier run lands late, on the new run's row.
    async with Storage.transaction() as db:
        await db.execute(
            "UPDATE session_execution_leases SET abort_requested = TRUE, abort_generation = ? WHERE session_id = ?",
            (target.generation, "ses_g12_old"),
        )
    async with second:
        await asyncio.sleep(0.3)
    assert aborted == []


async def test_the_abort_route_says_what_the_abort_came_to(monkeypatch):
    from flocks.server.routes import session as session_routes

    async def _nothing_here(session_id: str) -> bool:
        return False

    monkeypatch.setattr(session_routes, "_abort_session_processing", _nothing_here)
    assert (await session_routes.abort_session("ses_g12_idle")).status == "not_running"

    _lease, task = await _owner("ses_g12_route", stop_on_abort=True)
    result = await session_routes.abort_session("ses_g12_route")
    assert result.status == "stopped" and result.generation is not None
    await asyncio.wait_for(task, timeout=2)

    async def _store_down(session_id: str):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(ownership, "request_abort_of_run", _store_down)
    assert (await session_routes.abort_session("ses_g12_down")).status == "unconfirmed"
