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
