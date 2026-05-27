"""Tests for PR-5 (E3 three-stage degradation, E4 anti-thrashing, E5
post-prune wiring) and PR-6 (E6 per-message token cache).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.session.lifecycle.compaction import compaction as compaction_module
from flocks.session.lifecycle.compaction.compaction import (
    CompactionHistory,
    COOLDOWN_AFTER_INEFFECTIVE,
    COOLDOWN_SUPPRESS_COMPACTIONS,
    INEFFECTIVE_SAVINGS_THRESHOLD,
)
from flocks.session.lifecycle.compaction.summary import (
    _build_oversized_placeholder_section,
    _partition_oversized,
    summarize_in_stages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str, content: str) -> Any:
    m = MagicMock()
    m.role = role
    m.content = content
    return m


def _structured(label: str) -> str:
    return (
        f"# Iter summary [{label}]\n\n"
        "## Decisions\nD\n\n"
        "## Current Task\nT\n\n"
        "## Open TODOs\n- [ ] x\n\n"
        "## Key Files & Identifiers\n- f.py\n"
    )


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


# ---------------------------------------------------------------------------
# E3 — partitioning + placeholder
# ---------------------------------------------------------------------------

class TestPartitionOversized:
    def test_partition_keeps_order(self) -> None:
        small_a = _msg("user", "a")
        big = _msg("assistant", "x" * 50)
        small_b = _msg("user", "b")
        safe, oversized = _partition_oversized([small_a, big, small_b], threshold_chars=10)
        assert safe == [small_a, small_b]
        assert oversized == [big]

    def test_threshold_inclusive(self) -> None:
        exactly = _msg("assistant", "x" * 100)
        safe, oversized = _partition_oversized([exactly], threshold_chars=100)
        assert oversized == [exactly]
        assert safe == []

    def test_no_oversized(self) -> None:
        safe, oversized = _partition_oversized([_msg("user", "ok")], threshold_chars=1000)
        assert oversized == []
        assert len(safe) == 1


class TestOversizedPlaceholder:
    def test_empty_returns_empty(self) -> None:
        assert _build_oversized_placeholder_section([]) == ""

    def test_includes_role_and_char_count(self) -> None:
        big = _msg("assistant", "hello " * 100)
        section = _build_oversized_placeholder_section([big])
        assert "## Oversized Items Skipped" in section
        assert "assistant" in section
        assert "600 chars" in section  # 6 * 100 = 600


# ---------------------------------------------------------------------------
# E3 — summarize_in_stages flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarizeInStages:
    async def test_stage1_success_returns_immediately(self) -> None:
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_Resp(_structured("ok")))
        chat_messages = [_msg("user", "hi")]
        result = await summarize_in_stages(
            chat_messages,
            prompt_text="STRUCT",
            target_chars=1000,
            provider_client=provider,
            model_id="m",
            max_tokens=2000,
            session_id="ses1",
        )
        assert result is not None
        assert "ok" in result
        # Only ONE LLM round-trip — no Stage 2 / Stage 3 happened.
        assert provider.chat.await_count == 1

    async def test_stage3_fallback_when_provider_always_empty(self) -> None:
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_Resp(""))
        chat_messages = [_msg("user", "hello world")]
        result = await summarize_in_stages(
            chat_messages,
            prompt_text="STRUCT",
            target_chars=1000,
            provider_client=provider,
            model_id="m",
            max_tokens=2000,
            session_id="ses2",
        )
        # Stage 3 deterministic fallback ALWAYS returns a usable string.
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Session Summary" in result  # signature of build_fallback_summary

    async def test_stage1_quality_failure_still_returned_when_stage2_unhelpful(self) -> None:
        # Stage 1 returns SOMETHING but missing structural sections.
        # No oversize messages -> Stage 2 will simply repeat.
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_Resp("just a sentence, no sections"))
        result = await summarize_in_stages(
            [_msg("user", "hi")],
            prompt_text="STRUCT",
            target_chars=1000,
            provider_client=provider,
            model_id="m",
            max_tokens=2000,
            session_id="ses3",
        )
        # Should fall back to either Stage-1 raw text or Stage-3 fallback.
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# E4 — Anti-thrashing history accounting
# ---------------------------------------------------------------------------

class TestCompactionHistory:
    def setup_method(self) -> None:
        compaction_module.reset_compaction_history()

    def test_first_lookup_creates_default(self) -> None:
        h = compaction_module._get_compaction_history("s1")
        assert isinstance(h, CompactionHistory)
        assert h.cooldown_remaining == 0
        assert h.ineffective_count == 0

    def test_history_is_persistent_across_lookups(self) -> None:
        h1 = compaction_module._get_compaction_history("s1")
        h1.ineffective_count = 2
        h2 = compaction_module._get_compaction_history("s1")
        assert h2.ineffective_count == 2
        assert h1 is h2

    def test_threshold_constants_consistent(self) -> None:
        assert 0.0 < INEFFECTIVE_SAVINGS_THRESHOLD < 1.0
        assert COOLDOWN_AFTER_INEFFECTIVE >= 1
        assert COOLDOWN_SUPPRESS_COMPACTIONS >= 1

    def test_reset_specific(self) -> None:
        compaction_module._get_compaction_history("s1").ineffective_count = 3
        compaction_module._get_compaction_history("s2").ineffective_count = 5
        compaction_module.reset_compaction_history("s1")
        # s1 wiped (new default); s2 untouched.
        assert compaction_module._get_compaction_history("s1").ineffective_count == 0
        assert compaction_module._get_compaction_history("s2").ineffective_count == 5

    def test_reset_all(self) -> None:
        for s in ("s1", "s2", "s3"):
            compaction_module._get_compaction_history(s).ineffective_count = 1
        compaction_module.reset_compaction_history()
        assert len(compaction_module._compaction_history) == 0


# ---------------------------------------------------------------------------
# E5 — post-prune is wired into orchestrator.run_compaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestOrchestratorPostPrune:
    async def test_run_compaction_calls_prune_after_process(self, monkeypatch) -> None:
        """``run_compaction`` should invoke ``SessionCompaction.prune`` AFTER
        ``SessionCompaction.process`` so the tail can be cleaned up."""
        from flocks.session.lifecycle.compaction import orchestrator

        call_order: list[str] = []

        async def fake_process(**kwargs):  # noqa: ARG001
            call_order.append("process")
            return "continue"

        async def fake_prune(session_id, *args, **kwargs):  # noqa: ARG001, ARG002
            call_order.append("prune")

        monkeypatch.setattr(
            orchestrator.SessionCompaction, "process",
            classmethod(lambda cls, **kwargs: fake_process(**kwargs)),
        )
        monkeypatch.setattr(
            orchestrator.SessionCompaction, "prune",
            classmethod(lambda cls, *a, **kw: fake_prune(*a, **kw)),
        )
        monkeypatch.setattr(
            orchestrator.SessionStatus, "set", lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            orchestrator.SessionStatus, "clear", lambda *a, **kw: None,
        )

        from flocks.session.lifecycle.compaction.policy import CompactionPolicy
        result = await orchestrator.run_compaction(
            "ses_e5",
            parent_message_id="m_parent",
            messages=[],
            provider_id="prov",
            model_id="model",
            auto=True,
            policy=CompactionPolicy.default(),
        )
        assert result == "continue"
        assert call_order == ["process", "prune"], (
            f"prune must run AFTER process; got {call_order}"
        )

    async def test_post_prune_failure_is_swallowed(self, monkeypatch) -> None:
        """A failing post-prune must NOT break the compaction result."""
        from flocks.session.lifecycle.compaction import orchestrator

        async def fake_process(**kwargs):  # noqa: ARG001
            return "continue"

        async def boom(session_id, *args, **kwargs):  # noqa: ARG001, ARG002
            raise RuntimeError("simulated prune failure")

        monkeypatch.setattr(
            orchestrator.SessionCompaction, "process",
            classmethod(lambda cls, **kwargs: fake_process(**kwargs)),
        )
        monkeypatch.setattr(
            orchestrator.SessionCompaction, "prune",
            classmethod(lambda cls, *a, **kw: boom(*a, **kw)),
        )
        monkeypatch.setattr(
            orchestrator.SessionStatus, "set", lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            orchestrator.SessionStatus, "clear", lambda *a, **kw: None,
        )

        from flocks.session.lifecycle.compaction.policy import CompactionPolicy
        # Should not raise.
        result = await orchestrator.run_compaction(
            "ses_e5_fail",
            parent_message_id="m_parent",
            messages=[],
            provider_id="prov",
            model_id="model",
            auto=True,
            policy=CompactionPolicy.default(),
        )
        assert result == "continue"


# ---------------------------------------------------------------------------
# E6 — per-message token cache behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMessageTokenCache:
    def setup_method(self) -> None:
        from flocks.session.prompt import SessionPrompt
        SessionPrompt.invalidate_message_cache()

    async def test_finished_message_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two consecutive ``estimate_full_context_tokens`` calls over the
        same finished message should incur only ONE ``Message.parts`` lookup."""
        from flocks.session.prompt import SessionPrompt
        from flocks.session import message as message_mod

        call_count = {"n": 0}

        async def _parts(message_id, session_id):  # noqa: ARG001
            call_count["n"] += 1
            return []

        monkeypatch.setattr(message_mod.Message, "parts", staticmethod(_parts))

        msg = MagicMock()
        msg.id = "m_cached"
        msg.content = "hello"
        msg.finish = "end_turn"  # finished

        await SessionPrompt.estimate_full_context_tokens("ses", [msg])
        first_calls = call_count["n"]
        await SessionPrompt.estimate_full_context_tokens("ses", [msg])
        second_calls = call_count["n"]

        # Second call must NOT have re-queried parts.
        assert first_calls == 1
        assert second_calls == 1, (
            f"second call should hit cache (Message.parts seen {second_calls} times)"
        )

    async def test_streaming_message_is_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Messages still streaming (``finish is None``) must always be
        re-counted so their growing token total is reflected."""
        from flocks.session.prompt import SessionPrompt
        from flocks.session import message as message_mod

        call_count = {"n": 0}

        async def _parts(message_id, session_id):  # noqa: ARG001
            call_count["n"] += 1
            return []

        monkeypatch.setattr(message_mod.Message, "parts", staticmethod(_parts))

        msg = MagicMock()
        msg.id = "m_streaming"
        msg.content = "hello"
        msg.finish = None

        await SessionPrompt.estimate_full_context_tokens("ses", [msg])
        await SessionPrompt.estimate_full_context_tokens("ses", [msg])

        # Both calls must hit Message.parts.
        assert call_count["n"] == 2

    async def test_invalidate_single_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from flocks.session.prompt import SessionPrompt
        from flocks.session import message as message_mod

        calls: list[str] = []

        async def _parts(message_id, session_id):  # noqa: ARG001
            calls.append(message_id)
            return []

        monkeypatch.setattr(message_mod.Message, "parts", staticmethod(_parts))

        msg = MagicMock()
        msg.id = "m_x"
        msg.content = "x"
        msg.finish = "end_turn"

        await SessionPrompt.estimate_full_context_tokens("ses", [msg])
        await SessionPrompt.estimate_full_context_tokens("ses", [msg])  # cached
        SessionPrompt.invalidate_message_cache("m_x")
        await SessionPrompt.estimate_full_context_tokens("ses", [msg])

        # Two genuine fetches: first, and after invalidation.
        assert len(calls) == 2

    async def test_invalidate_iterable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from flocks.session.prompt import SessionPrompt

        # Seed cache with arbitrary entries
        SessionPrompt._message_token_cache["a"] = 1
        SessionPrompt._message_token_cache["b"] = 2
        SessionPrompt._message_token_cache["c"] = 3

        SessionPrompt.invalidate_message_cache(["a", "c", "missing"])
        assert "a" not in SessionPrompt._message_token_cache
        assert "c" not in SessionPrompt._message_token_cache
        assert "b" in SessionPrompt._message_token_cache

    async def test_fifo_eviction_when_over_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from flocks.session.prompt import SessionPrompt

        monkeypatch.setattr(SessionPrompt, "_MESSAGE_CACHE_MAX", 3, raising=True)
        from flocks.session import message as message_mod

        async def _parts(message_id, session_id):  # noqa: ARG001
            return []

        monkeypatch.setattr(message_mod.Message, "parts", staticmethod(_parts))

        for i in range(5):
            m = MagicMock()
            m.id = f"m{i}"
            m.content = "x"
            m.finish = "end_turn"
            await SessionPrompt.estimate_full_context_tokens("ses", [m])

        # Oldest entries (m0, m1) must have been evicted.
        assert "m0" not in SessionPrompt._message_token_cache
        assert "m1" not in SessionPrompt._message_token_cache
        # Newest 3 retained.
        for i in (2, 3, 4):
            assert f"m{i}" in SessionPrompt._message_token_cache
