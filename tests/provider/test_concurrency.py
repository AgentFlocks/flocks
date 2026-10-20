"""Tests for the process-wide model request limiter (flocks.provider.concurrency)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from flocks.config.config import ConfigInfo
from flocks.provider import concurrency
from flocks.provider.concurrency import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    ConcurrencyLimiter,
    acquire_llm_slot,
    get_provider_limiter,
    report_provider_rate_limited,
    resolve_max_parallel_subagents,
    resolve_provider_request_limit,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    concurrency.reset_limiters_for_testing()
    yield
    concurrency.reset_limiters_for_testing()


async def _hold(limiter: ConcurrencyLimiter, started: list, release: asyncio.Event, label: str, **kwargs):
    slot = await limiter.acquire(**kwargs)
    started.append(label)
    try:
        await release.wait()
    finally:
        slot.release()


@pytest.mark.asyncio
async def test_limit_bounds_concurrent_holders_and_queues_in_fifo_order():
    limiter = ConcurrencyLimiter("p", limit=2)
    started: list[str] = []
    release = asyncio.Event()

    tasks = [
        asyncio.create_task(_hold(limiter, started, release, f"t{i}"))
        for i in range(4)
    ]
    await asyncio.sleep(0.05)
    assert started == ["t0", "t1"]
    assert limiter.snapshot()["active"] == 2
    assert limiter.snapshot()["waiting"] == 2

    release.set()
    await asyncio.gather(*tasks)
    assert started == ["t0", "t1", "t2", "t3"]
    assert limiter.snapshot() == {
        "name": "p",
        "limit": 2,
        "active": 0,
        "waiting": 0,
        "cooldownRemainingS": 0.0,
        "cooldownReason": None,
    }


@pytest.mark.asyncio
async def test_interactive_waiters_are_served_before_background_waiters():
    limiter = ConcurrencyLimiter("p", limit=1)
    started: list[str] = []
    first_release = asyncio.Event()
    others_release = asyncio.Event()

    holder = asyncio.create_task(_hold(limiter, started, first_release, "holder"))
    await asyncio.sleep(0.01)
    background = asyncio.create_task(
        _hold(limiter, started, others_release, "background", priority=PRIORITY_BACKGROUND)
    )
    await asyncio.sleep(0.01)
    interactive = asyncio.create_task(
        _hold(limiter, started, others_release, "interactive", priority=PRIORITY_INTERACTIVE)
    )
    await asyncio.sleep(0.01)

    first_release.set()
    await holder
    await asyncio.sleep(0.02)
    # The interactive waiter arrived later but jumps ahead of the background one.
    assert started == ["holder", "interactive"]

    others_release.set()
    await asyncio.gather(background, interactive)
    assert started == ["holder", "interactive", "background"]


@pytest.mark.asyncio
async def test_zero_limit_means_unbounded():
    limiter = ConcurrencyLimiter("p", limit=0)
    slots = [await limiter.acquire() for _ in range(20)]
    assert limiter.snapshot()["active"] == 20
    for slot in slots:
        slot.release()
    assert limiter.snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_release_is_idempotent():
    limiter = ConcurrencyLimiter("p", limit=1)
    slot = await limiter.acquire()
    slot.release()
    slot.release()
    assert limiter.snapshot()["active"] == 0
    # A second acquire must still work (active never goes negative / stuck).
    other = await asyncio.wait_for(limiter.acquire(), timeout=0.5)
    other.release()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_the_slot():
    limiter = ConcurrencyLimiter("p", limit=1)
    holder = await limiter.acquire()

    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)
    assert limiter.snapshot()["waiting"] == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert limiter.snapshot()["waiting"] == 0

    holder.release()
    assert limiter.snapshot()["active"] == 0
    slot = await asyncio.wait_for(limiter.acquire(), timeout=0.5)
    slot.release()


@pytest.mark.asyncio
async def test_slot_handed_to_a_waiter_cancelled_at_the_same_time_is_passed_on():
    limiter = ConcurrencyLimiter("p", limit=1)
    holder = await limiter.acquire()

    doomed = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)
    survivor = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)

    # Release pops ``doomed`` and schedules delivery on this loop; cancel it
    # before that callback runs. The slot must reach ``survivor``.
    holder.release()
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed
    slot = await asyncio.wait_for(survivor, timeout=0.5)
    assert limiter.snapshot()["active"] == 1
    slot.release()
    assert limiter.snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_wait_timeout_proceeds_over_the_cap_instead_of_hanging():
    limiter = ConcurrencyLimiter("p", limit=1)
    holder = await limiter.acquire()
    started = time.monotonic()
    slot = await asyncio.wait_for(limiter.acquire(timeout_s=0.05), timeout=1.0)
    assert time.monotonic() - started >= 0.04
    assert limiter.snapshot()["active"] == 2
    slot.release()
    holder.release()
    assert limiter.snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_rate_limit_cooldown_delays_new_requests_and_is_capped():
    limiter = ConcurrencyLimiter("p", limit=4)
    applied = limiter.report_rate_limited(0.15)
    assert applied == pytest.approx(0.15, abs=0.01)
    started = time.monotonic()
    slot = await limiter.acquire()
    assert time.monotonic() - started >= 0.1
    slot.release()

    assert limiter.report_rate_limited(3600) == concurrency.MAX_RATE_LIMIT_COOLDOWN_S
    assert limiter.cooldown_remaining() <= concurrency.MAX_RATE_LIMIT_COOLDOWN_S
    assert limiter.report_rate_limited(-1) == 0.0
    assert limiter.report_rate_limited("nope") == 0.0


@pytest.mark.asyncio
async def test_raising_the_limit_unblocks_queued_waiters():
    limiter = ConcurrencyLimiter("p", limit=1)
    holder = await limiter.acquire()
    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)
    limiter.set_limit(2)
    slot = await asyncio.wait_for(waiter, timeout=0.5)
    slot.release()
    holder.release()


@pytest.mark.asyncio
async def test_lowering_the_limit_retires_slots_until_active_converges():
    limiter = ConcurrencyLimiter("p", limit=3)
    held = [await limiter.acquire() for _ in range(3)]
    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)

    limiter.set_limit(1)
    # Two releases only shrink ``active`` (3 -> 2 -> 1); the queued waiter
    # still has to wait because the new cap is already saturated.
    held[0].release()
    held[1].release()
    await asyncio.sleep(0.01)
    assert limiter.snapshot()["active"] == 1
    assert not waiter.done()

    held[2].release()
    slot = await asyncio.wait_for(waiter, timeout=0.5)
    assert limiter.snapshot()["active"] == 1
    slot.release()
    assert limiter.snapshot()["active"] == 0


def test_slots_hand_over_between_event_loops_on_different_threads():
    """Server-loop and workflow-loop callers share one limiter."""
    limiter = ConcurrencyLimiter("p", limit=1)
    order: list[str] = []
    background_waiting = threading.Event()
    background_done = threading.Event()

    async def server_side():
        slot = await limiter.acquire()
        order.append("server-acquired")
        # Let the workflow thread queue up behind us, then release.
        await asyncio.get_running_loop().run_in_executor(None, background_waiting.wait, 2.0)
        await asyncio.sleep(0.05)
        slot.release()
        order.append("server-released")
        await asyncio.get_running_loop().run_in_executor(None, background_done.wait, 2.0)

    async def workflow_side():
        background_waiting.set()
        slot = await limiter.acquire(priority=PRIORITY_BACKGROUND)
        order.append("workflow-acquired")
        slot.release()
        background_done.set()

    def run_workflow_loop():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(workflow_side())
        finally:
            loop.close()

    server_loop = asyncio.new_event_loop()
    try:
        thread = threading.Thread(target=run_workflow_loop, daemon=True)

        async def orchestrate():
            task = asyncio.ensure_future(server_side())
            await asyncio.sleep(0.02)
            thread.start()
            await task

        server_loop.run_until_complete(asyncio.wait_for(orchestrate(), timeout=5))
        thread.join(timeout=5)
    finally:
        server_loop.close()

    assert order == ["server-acquired", "server-released", "workflow-acquired"]
    assert limiter.snapshot()["active"] == 0


def test_limit_resolution_prefers_provider_then_global_then_env_then_default(monkeypatch):
    config = ConfigInfo.model_validate({
        "concurrency": {"maxConcurrentRequests": 3, "maxParallelSubagents": 2},
        "provider": {"openai": {"options": {"maxConcurrentRequests": 5}}},
    })
    assert resolve_provider_request_limit(config, "openai") == 5
    assert resolve_provider_request_limit(config, "anthropic") == 3
    assert resolve_max_parallel_subagents(config) == 2

    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "11")
    monkeypatch.setenv("FLOCKS_SUBAGENT_MAX_PARALLEL", "7")
    assert resolve_provider_request_limit(ConfigInfo(), "anthropic") == 11
    assert resolve_max_parallel_subagents(ConfigInfo()) == 7

    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "junk")
    monkeypatch.delenv("FLOCKS_SUBAGENT_MAX_PARALLEL")
    assert resolve_provider_request_limit(None, "anthropic") == concurrency.DEFAULT_MAX_CONCURRENT_REQUESTS
    assert resolve_max_parallel_subagents(None) == concurrency.DEFAULT_MAX_PARALLEL_SUBAGENTS

    # Snake-case keys in extra config still resolve.
    config = ConfigInfo.model_validate({"concurrency": {"max_concurrent_requests": 0}})
    assert resolve_provider_request_limit(config, "x") == 0


@pytest.mark.asyncio
async def test_acquire_llm_slot_reports_the_session_while_it_is_queued(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "1")

    async def fake_config():
        return ConfigInfo()

    monkeypatch.setattr("flocks.config.config.Config.get", fake_config)
    first = await acquire_llm_slot("prov", session_id="ses_first")
    assert not concurrency.is_waiting_for_llm_slot("ses_first")

    queued = asyncio.create_task(acquire_llm_slot("prov", session_id="ses_queued"))
    await asyncio.sleep(0.02)
    assert concurrency.is_waiting_for_llm_slot("ses_queued")
    first.release()
    slot = await asyncio.wait_for(queued, timeout=0.5)
    assert not concurrency.is_waiting_for_llm_slot("ses_queued")

    cancelled = asyncio.create_task(acquire_llm_slot("prov", session_id="ses_cancelled"))
    await asyncio.sleep(0.02)
    assert concurrency.is_waiting_for_llm_slot("ses_cancelled")
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not concurrency.is_waiting_for_llm_slot("ses_cancelled")
    slot.release()


@pytest.mark.asyncio
async def test_acquire_llm_slot_uses_configured_limit(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "1")

    async def fake_config():
        return ConfigInfo()

    monkeypatch.setattr("flocks.config.config.Config.get", fake_config)
    first = await acquire_llm_slot("prov")
    assert get_provider_limiter("prov").limit == 1
    second = asyncio.create_task(acquire_llm_slot("prov"))
    await asyncio.sleep(0.02)
    assert not second.done()
    first.release()
    slot = await asyncio.wait_for(second, timeout=0.5)
    slot.release()

    applied = report_provider_rate_limited("prov", 0.01)
    assert applied == pytest.approx(0.01, abs=0.005)
    snapshots = concurrency.limiter_snapshots()
    assert [entry["name"] for entry in snapshots] == ["prov"]
