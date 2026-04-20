"""Tests for the background memory-flush dispatcher.

Covers the safety guarantees added on top of the naive ``create_task``
backgrounding:

* per-session serialisation (no overlapping daily-file writes for the same
  session);
* hard timeout on the background flush (a stuck provider call cannot pin
  references forever);
* opt-out via ``FLOCKS_COMPACTION_FLUSH_BACKGROUND=0`` (legacy inline mode);
* drain helper for graceful shutdown.

Plus a coverage test for ``summarize_chunked``'s parallel chunk dispatch.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from flocks.session.lifecycle.compaction import compaction as compaction_mod
from flocks.session.lifecycle.compaction import summary as summary_mod
from flocks.session.lifecycle.compaction.compaction import (
    SessionCompaction,
    drain_pending_flush_tasks,
)


@pytest.fixture(autouse=True)
async def _reset_flush_state(monkeypatch):
    """Cancel/reset any flush state so tests do not bleed into one another."""
    # Pre-test: hard reset.
    for t in list(compaction_mod._pending_flush_tasks):
        t.cancel()
    if compaction_mod._pending_flush_tasks:
        await asyncio.gather(
            *compaction_mod._pending_flush_tasks, return_exceptions=True
        )
    compaction_mod._pending_flush_tasks.clear()
    compaction_mod._session_flush_locks.clear()
    monkeypatch.delenv("FLOCKS_COMPACTION_FLUSH_BACKGROUND", raising=False)
    monkeypatch.delenv("FLOCKS_COMPACTION_FLUSH_TIMEOUT", raising=False)

    yield

    # Post-test: cancel anything still alive before the loop tears down so
    # we never trigger "Task was destroyed but it is pending!" warnings.
    for t in list(compaction_mod._pending_flush_tasks):
        t.cancel()
    if compaction_mod._pending_flush_tasks:
        await asyncio.gather(
            *compaction_mod._pending_flush_tasks, return_exceptions=True
        )
    compaction_mod._pending_flush_tasks.clear()
    compaction_mod._session_flush_locks.clear()


def _patch_flush(monkeypatch, fake):
    """Replace ``_flush_memory_to_daily`` with ``fake`` (an async callable)."""
    monkeypatch.setattr(
        SessionCompaction,
        "_flush_memory_to_daily",
        classmethod(lambda cls, **kwargs: fake(**kwargs)),
    )


# ---------------------------------------------------------------------------
# Backgrounding & ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_immediately_in_background_mode(monkeypatch):
    """Dispatch must not wait for the slow flush to finish."""
    flush_started = asyncio.Event()
    flush_release = asyncio.Event()

    async def fake_flush(**kwargs):
        flush_started.set()
        await flush_release.wait()

    _patch_flush(monkeypatch, fake_flush)

    started = time.perf_counter()
    await SessionCompaction._dispatch_memory_flush(
        session_id="sess-A",
        summary_text="summary",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )
    elapsed = time.perf_counter() - started

    # Background task should have been scheduled but not awaited.
    assert elapsed < 0.5
    assert len(compaction_mod._pending_flush_tasks) == 1

    await asyncio.wait_for(flush_started.wait(), timeout=1.0)
    flush_release.set()
    await drain_pending_flush_tasks(timeout=2.0)
    assert not compaction_mod._pending_flush_tasks


@pytest.mark.asyncio
async def test_inline_mode_when_env_disables_background(monkeypatch):
    """``FLOCKS_COMPACTION_FLUSH_BACKGROUND=0`` falls back to await."""
    monkeypatch.setenv("FLOCKS_COMPACTION_FLUSH_BACKGROUND", "0")

    calls: list[str] = []

    async def fake_flush(**kwargs):
        calls.append(kwargs["session_id"])

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="sess-inline",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )

    assert calls == ["sess-inline"]
    assert not compaction_mod._pending_flush_tasks
    # Inline path must NOT register a per-session lock — that bookkeeping
    # only exists to serialise the background path.  Regression guard:
    # if someone moves _get_flush_lock above the background branch the
    # inline path would silently start populating the registry.
    assert "sess-inline" not in compaction_mod._session_flush_locks


# ---------------------------------------------------------------------------
# Per-session serialisation (the regression that motivated this fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_session_flushes_run_serially(monkeypatch):
    """Two dispatches on the same session must not overlap in the flush body.

    The daily memory file uses non-atomic read-modify-write, so concurrent
    flushes for the same session would silently lose data.
    """
    in_flight = 0
    max_in_flight = 0
    lock_for_counter = asyncio.Lock()

    async def fake_flush(**kwargs):
        nonlocal in_flight, max_in_flight
        async with lock_for_counter:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock_for_counter:
            in_flight -= 1

    _patch_flush(monkeypatch, fake_flush)

    for _ in range(3):
        await SessionCompaction._dispatch_memory_flush(
            session_id="same-sess",
            summary_text="x",
            chat_messages=[],
            model_id="m",
            provider_client=object(),
            ChatMessage=object,
            policy=None,
        )

    await drain_pending_flush_tasks(timeout=5.0)
    assert max_in_flight == 1, (
        f"per-session serialisation broken: max concurrent flushes = {max_in_flight}"
    )


@pytest.mark.asyncio
async def test_different_sessions_flush_in_parallel(monkeypatch):
    """Cross-session parallelism must be preserved."""
    in_flight = 0
    max_in_flight = 0
    counter_lock = asyncio.Lock()

    async def fake_flush(**kwargs):
        nonlocal in_flight, max_in_flight
        async with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.1)
        async with counter_lock:
            in_flight -= 1

    _patch_flush(monkeypatch, fake_flush)

    for sid in ("a", "b", "c", "d"):
        await SessionCompaction._dispatch_memory_flush(
            session_id=sid,
            summary_text="x",
            chat_messages=[],
            model_id="m",
            provider_client=object(),
            ChatMessage=object,
            policy=None,
        )

    await drain_pending_flush_tasks(timeout=5.0)
    assert max_in_flight >= 2, (
        f"cross-session parallelism lost: max concurrent flushes = {max_in_flight}"
    )


# ---------------------------------------------------------------------------
# Timeout safety net
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_flush_times_out(monkeypatch):
    """A stuck flush must be cancelled by the wait_for guard."""
    monkeypatch.setenv("FLOCKS_COMPACTION_FLUSH_TIMEOUT", "0.05")

    async def fake_flush(**kwargs):
        await asyncio.sleep(5.0)

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="hangs",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )

    await drain_pending_flush_tasks(timeout=2.0)
    assert not compaction_mod._pending_flush_tasks


# ---------------------------------------------------------------------------
# Lock registry cleanup (no per-session memory leak)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_lock_released_after_completion(monkeypatch):
    """``_session_flush_locks`` must not retain dead session entries."""
    async def fake_flush(**kwargs):
        return None

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="ephemeral-1",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )
    await drain_pending_flush_tasks(timeout=2.0)

    assert "ephemeral-1" not in compaction_mod._session_flush_locks


@pytest.mark.asyncio
async def test_single_task_observes_itself_in_pending_set_during_flush(monkeypatch):
    """Pin the ``current is t: continue`` invariant in cleanup logic.

    During the flush body the running task IS still in
    ``_pending_flush_tasks`` (the done-callback only fires after the
    coroutine returns).  ``_release_session_lock_if_idle`` must
    therefore exclude the current task, otherwise a single dispatch
    would always look "still pending" to itself and never clean up its
    own lock — silently re-introducing the leak that motivated the
    cleanup helper in the first place.
    """
    saw_self = False

    async def fake_flush(**kwargs):
        nonlocal saw_self
        # While we are running the flush body, our own task should
        # still be tracked.  This proves the cleanup helper must
        # special-case ``current``.
        saw_self = any(
            compaction_mod._session_id_from_task(t) == "solo"
            for t in compaction_mod._pending_flush_tasks
        )

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="solo",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )
    await drain_pending_flush_tasks(timeout=2.0)

    assert saw_self, "test premise broken: task did not observe itself in pending set"
    assert "solo" not in compaction_mod._session_flush_locks


@pytest.mark.asyncio
async def test_session_lock_kept_while_other_task_pending(monkeypatch):
    """Cleanup must not drop the lock while another same-session task is queued."""
    release = asyncio.Event()
    seen_first = asyncio.Event()
    call_count = 0

    async def fake_flush(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            seen_first.set()
            await release.wait()

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="busy",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )
    await asyncio.wait_for(seen_first.wait(), timeout=1.0)

    # Second dispatch is queued waiting on the per-session lock.
    await SessionCompaction._dispatch_memory_flush(
        session_id="busy",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )

    # While both tasks are alive the lock must still be registered.
    assert "busy" in compaction_mod._session_flush_locks

    release.set()
    await drain_pending_flush_tasks(timeout=3.0)

    # After both finish, the registry should be empty for this session.
    assert "busy" not in compaction_mod._session_flush_locks


# ---------------------------------------------------------------------------
# drain helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_returns_quickly_when_no_pending_tasks():
    leftover = await drain_pending_flush_tasks(timeout=5.0)
    assert leftover == 0


@pytest.mark.asyncio
async def test_drain_reports_leftover_without_cancel(monkeypatch):
    """Default behaviour: leftover tasks survive drain timeout."""
    release = asyncio.Event()

    async def fake_flush(**kwargs):
        await release.wait()

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="slow",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )

    leftover = await drain_pending_flush_tasks(timeout=0.05)
    assert leftover == 1
    assert len(compaction_mod._pending_flush_tasks) == 1

    # Clean up so the autouse fixture doesn't have to cancel mid-flight.
    release.set()
    await drain_pending_flush_tasks(timeout=2.0)


@pytest.mark.asyncio
async def test_drain_cancel_on_timeout_clears_pending(monkeypatch):
    """``cancel_on_timeout=True`` must leave the pending set empty."""
    async def fake_flush(**kwargs):
        await asyncio.sleep(10.0)

    _patch_flush(monkeypatch, fake_flush)

    await SessionCompaction._dispatch_memory_flush(
        session_id="hangs",
        summary_text="x",
        chat_messages=[],
        model_id="m",
        provider_client=object(),
        ChatMessage=object,
        policy=None,
    )

    leftover = await drain_pending_flush_tasks(
        timeout=0.05, cancel_on_timeout=True,
    )
    assert leftover == 0
    assert not compaction_mod._pending_flush_tasks
    # After cancellation the runner's `finally` should still drop the
    # session lock, since this is the only task targeting "hangs".
    # If this assertion ever flaps it means the cancellation-time
    # cleanup path regressed and lock entries will leak across
    # shutdown/restart cycles.
    assert "hangs" not in compaction_mod._session_flush_locks


# ---------------------------------------------------------------------------
# Timeout-env parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("", float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("   ", float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("0", float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("-5", float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("not-a-number", float(compaction_mod._DEFAULT_FLUSH_TIMEOUT_SECONDS)),
        ("90", 90.0),
        ("1.5", 1.5),
    ],
)
@pytest.mark.asyncio
async def test_flush_timeout_parsing(monkeypatch, raw, expected):
    # async so the autouse async fixture can apply.
    if raw is None:
        monkeypatch.delenv("FLOCKS_COMPACTION_FLUSH_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("FLOCKS_COMPACTION_FLUSH_TIMEOUT", raw)
    assert compaction_mod._flush_timeout_seconds() == expected


# ---------------------------------------------------------------------------
# summarize_chunked: parallel dispatch
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, text: str):
        self.content = text


class _StubProvider:
    """Records concurrent .chat() invocations and sleeps to simulate latency."""

    def __init__(self, latency: float = 0.1):
        self.latency = latency
        self._in_flight = 0
        self.peak_in_flight = 0
        self._lock = asyncio.Lock()
        self.calls = 0

    async def chat(self, *, model_id, messages, max_tokens):
        async with self._lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
            self.calls += 1
        await asyncio.sleep(self.latency)
        async with self._lock:
            self._in_flight -= 1
        return _StubResponse(
            "## Decisions\n- ok\n## Current Task\nfoo\n"
            "## Open TODOs\n- none\n## Key Files & Identifiers\n- none"
        )


class _StubMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


@pytest.mark.asyncio
async def test_summarize_chunked_runs_chunks_in_parallel(monkeypatch):
    """``summarize_chunked`` must dispatch chunk LLM calls concurrently."""
    monkeypatch.setenv("FLOCKS_COMPACTION_CHUNK_CONCURRENCY", "4")

    # Force chunking: small target_chars + many big messages.
    msgs = [_StubMessage("user", "x" * 200) for _ in range(12)]
    provider = _StubProvider(latency=0.15)

    result = await summary_mod.summarize_chunked(
        chat_messages=msgs,
        prompt_text="prompt",
        target_chars=400,  # ~2 messages per chunk → ~6 chunks
        provider_client=provider,
        model_id="m",
        max_tokens=2000,
        session_id="sess-chunked",
    )

    assert result is not None
    # At least 2 chunk calls plus the merge call → > 2 chats.
    assert provider.calls >= 3
    # Parallelism actually happened.  We deliberately do NOT add a
    # wall-clock assertion here — `peak_in_flight` is the semantic
    # check we care about, and elapsed-time bounds are flaky under CI
    # load (cold provider, GC, container scheduling).
    assert provider.peak_in_flight >= 2, (
        f"chunked summarisation ran serially: peak_in_flight = {provider.peak_in_flight}"
    )


@pytest.mark.asyncio
async def test_summarize_chunked_concurrency_limit_respected(monkeypatch):
    """Semaphore caps the number of in-flight chunk calls."""
    monkeypatch.setenv("FLOCKS_COMPACTION_CHUNK_CONCURRENCY", "2")

    msgs = [_StubMessage("user", "x" * 200) for _ in range(12)]
    provider = _StubProvider(latency=0.15)

    await summary_mod.summarize_chunked(
        chat_messages=msgs,
        prompt_text="prompt",
        target_chars=400,
        provider_client=provider,
        model_id="m",
        max_tokens=2000,
        session_id="sess-cap",
    )
    # Final merge call runs alone after gather, so the *peak* during the
    # parallel phase should be ≤ 2.
    assert provider.peak_in_flight <= 2, (
        f"concurrency limit violated: peak_in_flight = {provider.peak_in_flight}"
    )
