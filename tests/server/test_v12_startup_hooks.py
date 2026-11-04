"""Extensions resume unfinished work when the server starts: startup hooks
run in order, and one that fails or hangs does not stop the others."""

from __future__ import annotations

import asyncio

import pytest

from flocks import extensions


@pytest.fixture(autouse=True)
def clean_hooks():
    saved = list(extensions._startup_hooks)
    extensions._startup_hooks.clear()
    yield
    extensions._startup_hooks[:] = saved


async def test_hooks_run_and_failures_do_not_stop_the_rest():
    ran: list[str] = []

    async def first() -> None:
        ran.append("first")

    async def broken() -> None:
        raise RuntimeError("cannot resume")

    async def hangs() -> None:
        await asyncio.sleep(10)

    def plain() -> None:
        ran.append("plain")

    extensions.register_startup_hook("first", first)
    extensions.register_startup_hook("broken", broken)
    extensions.register_startup_hook("hangs", hangs, timeout_seconds=0.05)
    extensions.register_startup_hook("plain", plain)

    await extensions.run_startup_hooks()

    assert ran == ["first", "plain"]


async def test_registering_a_name_again_replaces_it():
    ran: list[str] = []
    extensions.register_startup_hook("x", lambda: ran.append("old"))
    extensions.register_startup_hook("x", lambda: ran.append("new"))

    await extensions.run_startup_hooks()

    assert ran == ["new"]


async def test_cancelling_startup_stops_the_running_hook():
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def slow() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            stopped.set()

    extensions.register_startup_hook("slow", slow, timeout_seconds=30)
    runner = asyncio.ensure_future(extensions.run_startup_hooks())
    await started.wait()

    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    await asyncio.wait_for(stopped.wait(), timeout=1)
