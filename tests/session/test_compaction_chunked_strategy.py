"""Tests for the preemptive chunked-summarisation decision logic.

The original compaction code only switched from ``summarize_single_pass``
to ``summarize_chunked`` when the conversation was *bigger* than the
provider's context window (``total_chars > target_chars * 2``).  Field
data showed that a single 10–14k char ``summarize_single_pass`` call
against a slow OpenAI-compatible provider can take 60–90s — way longer
than running 3 small calls in parallel + 1 merge call.

These tests pin the new heuristic in
``compaction._decide_chunked_strategy`` and verify the new ``chunk_size``
parameter on ``summarize_chunked`` actually changes how the conversation
is split.
"""

from __future__ import annotations

import asyncio

import pytest

from flocks.session.lifecycle.compaction import compaction as compaction_mod
from flocks.session.lifecycle.compaction import summary as summary_mod


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_strategy_env(monkeypatch):
    for key in (
        "FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO",
        "FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS",
        "FLOCKS_COMPACTION_MIN_CHUNK_CHARS",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


class _StubResponse:
    def __init__(self, text: str):
        self.content = text


class _RecordingProvider:
    """Records the number of ``chat()`` invocations for chunk-count checks."""

    def __init__(self):
        self.calls = 0
        self._lock = asyncio.Lock()

    async def chat(self, *, model_id, messages, max_tokens):
        async with self._lock:
            self.calls += 1
        # A valid 4-section summary so quality validation always passes.
        return _StubResponse(
            "## Decisions\n- ok\n## Current Task\nfoo\n"
            "## Open TODOs\n- none\n## Key Files & Identifiers\n- none"
        )


class _StubMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


# ---------------------------------------------------------------------------
# _decide_chunked_strategy: pure-function decision matrix
# ---------------------------------------------------------------------------


def test_strategy_small_conversation_uses_single_pass():
    """Conversations under the preemptive threshold should NOT chunk.

    Below the threshold the parallelism speedup is dwarfed by the cost
    of the extra merge LLM call; single_pass wins.
    """
    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=2_000, target_chars=29_680,
    )
    assert use_chunked is False
    assert chunk_size is None
    assert decision == "single_pass"


def test_strategy_medium_conversation_triggers_preemptive_chunking():
    """The exact field scenario: ~10k conv, ~30k target → preemptive chunked.

    With defaults (ratio=0.2, parallel=3, min_chunk=3000) a 10885-char
    conversation crosses ``max(6000, 5936) = 6000`` and gets split into
    ~3 chunks.  This is the regression that motivated the heuristic.
    """
    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=10_885, target_chars=29_680,
    )
    assert use_chunked is True
    assert decision == "preemptive"
    assert chunk_size is not None
    # 10885 / 3 + 1 = 3629 → above the 3000 floor.
    assert chunk_size == 10_885 // 3 + 1


def test_strategy_oversize_conversation_uses_legacy_chunked():
    """Conversations bigger than 2x target still hit the legacy branch.

    For oversize content we keep ``chunk_size=None`` so
    ``summarize_chunked`` falls back to using ``target_chars`` as the
    split cap (legacy behaviour, intentionally not made finer).
    """
    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=80_000, target_chars=29_680,
    )
    assert use_chunked is True
    assert decision == "oversize"
    assert chunk_size is None


def test_strategy_chunk_size_floor_respected(monkeypatch):
    """Tiny conversations near the threshold must not be split into N tiny chunks.

    With ratio=0.5 and a 6500-char conv against a 12000-char target we
    cross the threshold but ``total_chars / parallel = 2166 < min_chunk
    (3000)``; chunk_size must be clamped to the floor.
    """
    monkeypatch.setenv("FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO", "0.5")
    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=6_500, target_chars=12_000,
    )
    assert use_chunked is True
    assert decision == "preemptive"
    # Floor is 3000; raw division would give 2167.
    assert chunk_size == 3_000


def test_strategy_env_overrides_take_effect(monkeypatch):
    """All three knobs (ratio, parallel, min_chunk) must override defaults."""
    monkeypatch.setenv("FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO", "0.5")
    monkeypatch.setenv("FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS", "5")
    monkeypatch.setenv("FLOCKS_COMPACTION_MIN_CHUNK_CHARS", "1000")

    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=10_000, target_chars=20_000,
    )
    assert use_chunked is True
    assert decision == "preemptive"
    # 10000 / 5 + 1 = 2001 → above the 1000 floor.
    assert chunk_size == 10_000 // 5 + 1


def test_strategy_invalid_env_falls_back_to_default(monkeypatch):
    """Garbage env vars must not break the decision; fall back to defaults.

    Defensive guard so a fat-fingered ratio doesn't take down compaction
    entirely.
    """
    monkeypatch.setenv("FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO", "not-a-number")
    monkeypatch.setenv("FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS", "-1")
    monkeypatch.setenv("FLOCKS_COMPACTION_MIN_CHUNK_CHARS", "0")

    use_chunked, chunk_size, decision = compaction_mod._decide_chunked_strategy(
        total_chars=10_000, target_chars=29_680,
    )
    # With defaults restored, 10000 > max(6000, 29680*0.2=5936) = 6000 →
    # still chunked.
    assert use_chunked is True
    assert decision == "preemptive"
    assert chunk_size is not None


# ---------------------------------------------------------------------------
# summarize_chunked: chunk_size actually wired through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_chunked_honours_chunk_size_param():
    """``chunk_size`` must drive splitting; ``target_chars`` is only the cap.

    Twelve 200-char messages × 12 = 2400 chars total.  With
    ``target_chars=10000`` the legacy logic produces 1 chunk
    (everything fits).  Passing ``chunk_size=600`` should force ~4
    chunks → ~5 chat() calls (4 chunk + 1 merge).
    """
    msgs = [_StubMessage("user", "x" * 200) for _ in range(12)]
    provider = _RecordingProvider()

    result = await summary_mod.summarize_chunked(
        chat_messages=msgs,
        prompt_text="prompt",
        target_chars=10_000,
        provider_client=provider,
        model_id="m",
        max_tokens=2000,
        session_id="sess-chunk-size",
        chunk_size=600,
    )

    assert result is not None
    # 4 chunks + merge ≥ 5 calls (could be 5 or 6 depending on prefix overhead).
    assert provider.calls >= 5, (
        f"chunk_size=600 should split into ~4 chunks, but only {provider.calls} chat() calls were made"
    )


@pytest.mark.asyncio
async def test_summarize_chunked_chunk_size_none_falls_back_to_target():
    """``chunk_size=None`` must reproduce the legacy behaviour.

    Same payload, no ``chunk_size`` → everything fits ``target_chars``
    so we get exactly 1 chunk + 1 merge call.
    """
    msgs = [_StubMessage("user", "x" * 200) for _ in range(12)]
    provider = _RecordingProvider()

    await summary_mod.summarize_chunked(
        chat_messages=msgs,
        prompt_text="prompt",
        target_chars=10_000,
        provider_client=provider,
        model_id="m",
        max_tokens=2000,
        session_id="sess-default-chunk",
        # chunk_size omitted → defaults to None → uses target_chars.
    )

    assert provider.calls == 2, (
        f"chunk_size=None should produce 1 chunk + 1 merge = 2 calls, got {provider.calls}"
    )
