"""Per-event-loop LLM concurrency budget (flocks.provider.llm_budget)."""

from __future__ import annotations

import asyncio

from flocks.provider import llm_budget


async def _hold(peak: dict, seconds: float = 0.05) -> None:
    async with llm_budget.llm_slot():
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(seconds)
        peak["now"] -= 1


async def test_loop_without_budget_is_unlimited():
    loop = asyncio.get_running_loop()
    llm_budget.set_loop_budget(loop, 0)
    assert llm_budget.get_loop_budget() == 0
    peak = {"now": 0, "max": 0}
    await asyncio.gather(*(_hold(peak) for _ in range(6)))
    assert peak["max"] == 6
    assert llm_budget.loop_budget_snapshot() == {"limit": 0, "in_flight": 0, "waiting": 0}


async def test_budget_caps_concurrent_slots_on_the_loop():
    loop = asyncio.get_running_loop()
    llm_budget.set_loop_budget(loop, 2)
    try:
        assert llm_budget.get_loop_budget(loop) == 2
        peak = {"now": 0, "max": 0}
        tasks = [asyncio.create_task(_hold(peak)) for _ in range(6)]
        await asyncio.sleep(0.01)
        snapshot = llm_budget.loop_budget_snapshot(loop)
        assert snapshot["limit"] == 2
        assert snapshot["in_flight"] == 2
        assert snapshot["waiting"] == 4
        await asyncio.gather(*tasks)
        assert peak["max"] == 2
        assert llm_budget.loop_budget_snapshot(loop) == {"limit": 2, "in_flight": 0, "waiting": 0}
    finally:
        llm_budget.set_loop_budget(loop, None)


async def test_limit_change_applies_to_new_slots_and_holders_release_cleanly():
    loop = asyncio.get_running_loop()
    llm_budget.set_loop_budget(loop, 1)
    try:
        slot = llm_budget.llm_slot()
        await slot.acquire()
        assert llm_budget.loop_budget_snapshot(loop)["in_flight"] == 1

        llm_budget.set_loop_budget(loop, 3)
        peak = {"now": 0, "max": 0}
        await asyncio.gather(*(_hold(peak) for _ in range(5)))
        assert peak["max"] == 3  # new semaphore of 3, the old holder does not count against it

        slot.release()
        slot.release()  # idempotent
        assert llm_budget.loop_budget_snapshot(loop)["in_flight"] == 0
    finally:
        llm_budget.set_loop_budget(loop, None)


async def test_slot_released_when_protected_region_raises():
    loop = asyncio.get_running_loop()
    llm_budget.set_loop_budget(loop, 1)
    try:
        async def _boom():
            async with llm_budget.llm_slot():
                raise RuntimeError("provider failed")

        try:
            await _boom()
        except RuntimeError:
            pass
        assert llm_budget.loop_budget_snapshot(loop)["in_flight"] == 0
        # The slot is free again: a second holder does not block.
        await asyncio.wait_for(_hold({"now": 0, "max": 0}, 0.0), timeout=1)
    finally:
        llm_budget.set_loop_budget(loop, None)


async def test_waiting_for_a_slot_can_be_cancelled():
    loop = asyncio.get_running_loop()
    llm_budget.set_loop_budget(loop, 1)
    try:
        holder = llm_budget.llm_slot()
        await holder.acquire()
        waiter = asyncio.create_task(_hold({"now": 0, "max": 0}))
        await asyncio.sleep(0.01)
        assert llm_budget.loop_budget_snapshot(loop)["waiting"] == 1
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        assert llm_budget.loop_budget_snapshot(loop)["waiting"] == 0
        holder.release()
        assert llm_budget.loop_budget_snapshot(loop)["in_flight"] == 0
    finally:
        llm_budget.set_loop_budget(loop, None)


async def test_slow_acquire_is_logged_with_the_queue_state(monkeypatch):
    """A saturated budget must leave a trace: the wait counts towards the
    workflow node timeout, so otherwise it just looks like a node timing out."""
    loop = asyncio.get_running_loop()
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        llm_budget.log, "warn", lambda event, payload=None: warnings.append((event, payload or {}))
    )
    monkeypatch.setattr(llm_budget, "SLOW_ACQUIRE_LOG_SECONDS", 0.05)
    llm_budget.set_loop_budget(loop, 1)
    try:
        holder = llm_budget.llm_slot()
        await holder.acquire()
        assert warnings == []  # an immediate acquire stays quiet

        waiter = asyncio.create_task(_hold({"now": 0, "max": 0}, 0.0))
        await asyncio.sleep(0.1)
        holder.release()
        await waiter

        assert [event for event, _ in warnings] == ["llm_budget.slot.slow_acquire"]
        payload = warnings[0][1]
        assert payload["limit"] == 1
        assert payload["waited_s"] >= 0.05
        assert payload["acquired"] is True
        # Counters already include this caller, so a saturated budget reads
        # in_flight == limit instead of one short.
        assert payload["in_flight"] == 1
        assert payload["waiting"] == 0  # this waiter already left the queue
    finally:
        llm_budget.set_loop_budget(loop, None)


async def test_slow_acquire_log_says_when_the_wait_ended_in_a_cancel(monkeypatch):
    """A queued call killed by a node timeout must not read as "got a slot"."""
    loop = asyncio.get_running_loop()
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        llm_budget.log, "warn", lambda event, payload=None: warnings.append((event, payload or {}))
    )
    monkeypatch.setattr(llm_budget, "SLOW_ACQUIRE_LOG_SECONDS", 0.05)
    llm_budget.set_loop_budget(loop, 1)
    try:
        holder = llm_budget.llm_slot()
        await holder.acquire()

        waiter = asyncio.create_task(_hold({"now": 0, "max": 0}, 0.0))
        await asyncio.sleep(0.1)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass

        assert [event for event, _ in warnings] == ["llm_budget.slot.slow_acquire"]
        payload = warnings[0][1]
        assert payload["acquired"] is False
        assert payload["in_flight"] == 1  # the holder only
        assert payload["waiting"] == 0
        holder.release()
        assert llm_budget.loop_budget_snapshot(loop)["in_flight"] == 0
    finally:
        llm_budget.set_loop_budget(loop, None)
