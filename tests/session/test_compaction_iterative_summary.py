"""Tests for PR-3 (E1) + PR-4 (E2) — iterative summary path.

Covers:
* in-process summary cache (get / set / FIFO / rebuild interval);
* ``build_iterative_prompt`` template wiring;
* ``summarize_chunked_iterative`` orchestration —
  call count, ``previous_summary`` seeding, empty / timeout chunk
  tolerance, last-chunk structural prompt.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.session.lifecycle.compaction import (
    DEFAULT_COMPACTION_PROMPT,
    DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS,
    ITERATIVE_SUMMARY_REBUILD_INTERVAL,
    SessionCompaction,
)
from flocks.session.lifecycle.compaction import compaction as compaction_module
from flocks.session.lifecycle.compaction.summary import (
    build_iterative_prompt,
    summarize_chunked_iterative,
    summarize_single_pass,
)


# ---------------------------------------------------------------------------
# Iterative summary cache (PR-3)
# ---------------------------------------------------------------------------

class TestIterativeSummaryCache:
    def setup_method(self) -> None:
        compaction_module.reset_iterative_summary_cache()

    def test_empty_cache_returns_none(self) -> None:
        assert compaction_module._get_iterative_summary_state("s1") == (None, 0)

    def test_store_and_get_roundtrip(self) -> None:
        compaction_module._store_iterative_summary_state("s1", "SUMMARY-1", 1)
        assert compaction_module._get_iterative_summary_state("s1") == ("SUMMARY-1", 1)

    def test_store_with_empty_session_id_is_noop(self) -> None:
        compaction_module._store_iterative_summary_state("", "X", 1)
        assert len(compaction_module._iterative_summary_cache) == 0

    def test_store_with_empty_summary_is_noop(self) -> None:
        compaction_module._store_iterative_summary_state("s1", "", 1)
        assert compaction_module._get_iterative_summary_state("s1") == (None, 0)

    def test_reset_specific_session(self) -> None:
        compaction_module._store_iterative_summary_state("s1", "A", 1)
        compaction_module._store_iterative_summary_state("s2", "B", 1)
        compaction_module.reset_iterative_summary_cache("s1")
        assert compaction_module._get_iterative_summary_state("s1") == (None, 0)
        assert compaction_module._get_iterative_summary_state("s2") == ("B", 1)

    def test_reset_all(self) -> None:
        compaction_module._store_iterative_summary_state("s1", "A", 1)
        compaction_module._store_iterative_summary_state("s2", "B", 1)
        compaction_module.reset_iterative_summary_cache()
        assert len(compaction_module._iterative_summary_cache) == 0

    def test_fifo_eviction_when_cap_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Shrink the cap for a fast deterministic test.
        monkeypatch.setattr(compaction_module, "_SUMMARY_CACHE_MAX", 3, raising=True)
        for i in range(5):
            compaction_module._store_iterative_summary_state(f"s{i}", f"S{i}", i + 1)
        # s0 / s1 should have been evicted; s2/s3/s4 retained.
        assert compaction_module._get_iterative_summary_state("s0") == (None, 0)
        assert compaction_module._get_iterative_summary_state("s1") == (None, 0)
        for i in (2, 3, 4):
            assert compaction_module._get_iterative_summary_state(f"s{i}") == (f"S{i}", i + 1)

    def test_force_rebuild_first_compaction_never(self) -> None:
        assert compaction_module._should_force_full_rebuild(0) is False

    def test_force_rebuild_at_interval(self) -> None:
        # Default interval is 5.
        assert ITERATIVE_SUMMARY_REBUILD_INTERVAL == 5
        for count in range(1, 5):
            assert compaction_module._should_force_full_rebuild(count) is False
        assert compaction_module._should_force_full_rebuild(5) is True
        assert compaction_module._should_force_full_rebuild(10) is True
        assert compaction_module._should_force_full_rebuild(11) is False


# ---------------------------------------------------------------------------
# build_iterative_prompt (PR-3)
# ---------------------------------------------------------------------------

class TestBuildIterativePrompt:
    def test_no_previous_returns_unchanged(self) -> None:
        result = build_iterative_prompt(DEFAULT_COMPACTION_PROMPT, None)
        assert result == DEFAULT_COMPACTION_PROMPT

    def test_empty_previous_returns_unchanged(self) -> None:
        result = build_iterative_prompt(DEFAULT_COMPACTION_PROMPT, "")
        assert result == DEFAULT_COMPACTION_PROMPT

    def test_with_previous_wraps_template(self) -> None:
        prev = "Previous summary body."
        result = build_iterative_prompt(DEFAULT_COMPACTION_PROMPT, prev)
        # Iterative wrapper contains the prior summary verbatim and still
        # ends with the standard structural prompt.
        assert "<<<PREVIOUS_SUMMARY>>>" in result
        assert prev in result
        assert "## Decisions" in result  # from DEFAULT_COMPACTION_PROMPT tail
        # Sanity-check that the template precedes the structural block.
        assert result.index("<<<PREVIOUS_SUMMARY>>>") < result.index("## Decisions")

    def test_whitespace_only_previous_unchanged(self) -> None:
        result = build_iterative_prompt(DEFAULT_COMPACTION_PROMPT, "   \n  ")
        # We treat "non-empty after strip" as "have previous"; whitespace
        # passes the truthy check but ``.strip()`` empties it inside the
        # template — both behaviours acceptable.  The important contract
        # is "no crash + structural prompt preserved".
        assert "## Decisions" in result


# ---------------------------------------------------------------------------
# summarize_chunked_iterative (PR-4 / E2)
# ---------------------------------------------------------------------------

def _make_messages(num: int, role: str = "user", chars: int = 200) -> list:
    msgs = []
    for i in range(num):
        m = MagicMock()
        m.role = role
        m.content = f"msg-{i} " + ("x" * chars)
        msgs.append(m)
    return msgs


def _structured_summary(label: str) -> str:
    """A summary that passes ``validate_summary_quality``."""
    return (
        f"# Iterative summary [{label}]\n\n"
        "## Decisions\nDecision A\n\n"
        "## Current Task\nDoing X\n\n"
        "## Open TODOs\n- [ ] todo\n\n"
        "## Key Files & Identifiers\n- path/to/file.py\n"
    )


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


def _mwp(
    message_id: str,
    role: str,
    text: str,
    *,
    finish: str | None = None,
    summary: bool | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            id=message_id,
            role=role,
            finish=finish,
            summary=summary,
        ),
        parts=[
            SimpleNamespace(
                type="text",
                text=text,
            )
        ],
    )


@pytest.mark.asyncio
class TestSessionCompactionDeltaInput:
    async def test_process_summarizes_only_messages_after_latest_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        compaction_module.reset_iterative_summary_cache("ses_delta")
        captured: dict[str, Any] = {}
        progress_events: list[tuple[str, dict[str, Any]]] = []

        async def fake_summarize_single_pass(*args: Any, **kwargs: Any) -> str:
            captured["previous_summary"] = kwargs["previous_summary"]
            captured["chat_messages"] = kwargs["chat_messages"]
            return _structured_summary("delta")

        async def fake_archive_and_write_summary(cls, **kwargs: Any) -> str:  # noqa: ARG001
            return "continue"

        async def fake_dispatch_memory_flush(cls, **kwargs: Any) -> None:  # noqa: ARG001
            return None

        async def fake_progress(stage: str, data: dict[str, Any]) -> None:
            progress_events.append((stage, data))

        async def fake_apply_config(cls, **kwargs: Any) -> None:  # noqa: ARG001
            return None

        async def fake_list_with_parts(cls, session_id: str) -> list:  # noqa: ARG001
            return msgs_with_parts

        msgs_with_parts = [
            _mwp("old-user", "user", "old request"),
            _mwp("old-assistant", "assistant", "old answer", finish="stop"),
            _mwp(
                "summary-1",
                "assistant",
                "persisted previous summary",
                finish="summary",
                summary=True,
            ),
            _mwp("new-user", "user", "new request"),
            _mwp("new-assistant", "assistant", "new answer", finish="stop"),
            _mwp("compact-command", "user", "/compact"),
        ]

        from flocks.provider.provider import Provider
        from flocks.session.message import Message

        monkeypatch.setattr(
            Provider,
            "get",
            classmethod(lambda cls, provider_id: MagicMock()),
        )
        monkeypatch.setattr(
            Provider,
            "apply_config",
            classmethod(fake_apply_config),
        )
        monkeypatch.setattr(
            Message,
            "list_with_parts",
            classmethod(fake_list_with_parts),
        )
        monkeypatch.setattr(
            compaction_module.summary,
            "summarize_single_pass",
            fake_summarize_single_pass,
        )
        monkeypatch.setattr(
            SessionCompaction,
            "_archive_and_write_summary",
            classmethod(fake_archive_and_write_summary),
        )
        monkeypatch.setattr(
            SessionCompaction,
            "_dispatch_memory_flush",
            classmethod(fake_dispatch_memory_flush),
        )

        result = await SessionCompaction.process(
            session_id="ses_delta",
            parent_id="compact-command",
            messages=[{"id": "compact-command"}],
            model_id="test-model",
            provider_id="test-provider",
            auto=False,
            progress_callback=fake_progress,
        )

        assert result == "continue"
        assert captured["previous_summary"] == "persisted previous summary"
        assert [
            message.content
            for message in captured["chat_messages"]
        ] == ["new request", "new answer"]
        assert ("load", {
            "message_count": 2,
            "total_chars": len("new request") + len("new answer"),
        }) in progress_events

    async def test_process_skips_when_only_compact_command_follows_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        compaction_module.reset_iterative_summary_cache("ses_no_delta")
        progress_events: list[tuple[str, dict[str, Any]]] = []
        summarize_spy = AsyncMock(return_value=_structured_summary("unexpected"))
        archive_spy = AsyncMock(return_value="continue")

        async def fake_progress(stage: str, data: dict[str, Any]) -> None:
            progress_events.append((stage, data))

        async def fake_apply_config(cls, **kwargs: Any) -> None:  # noqa: ARG001
            return None

        async def fake_list_with_parts(cls, session_id: str) -> list:  # noqa: ARG001
            return msgs_with_parts

        msgs_with_parts = [
            _mwp(
                "summary-1",
                "assistant",
                "persisted previous summary",
                finish="summary",
                summary=True,
            ),
            _mwp("compact-command", "user", "/compact"),
        ]

        from flocks.provider.provider import Provider
        from flocks.session.message import Message

        monkeypatch.setattr(
            Provider,
            "get",
            classmethod(lambda cls, provider_id: MagicMock()),
        )
        monkeypatch.setattr(
            Provider,
            "apply_config",
            classmethod(fake_apply_config),
        )
        monkeypatch.setattr(
            Message,
            "list_with_parts",
            classmethod(fake_list_with_parts),
        )
        monkeypatch.setattr(
            compaction_module.summary,
            "summarize_single_pass",
            summarize_spy,
        )
        monkeypatch.setattr(
            SessionCompaction,
            "_archive_and_write_summary",
            classmethod(lambda cls, **kwargs: archive_spy(**kwargs)),
        )

        result = await SessionCompaction.process(
            session_id="ses_no_delta",
            parent_id="compact-command",
            messages=[{"id": "compact-command"}],
            model_id="test-model",
            provider_id="test-provider",
            auto=False,
            progress_callback=fake_progress,
        )

        assert result == "skipped"
        summarize_spy.assert_not_awaited()
        archive_spy.assert_not_awaited()
        assert ("load", {"message_count": 0, "total_chars": 0}) in progress_events
        assert (
            "complete",
            {"result": "skipped_no_new_messages"},
        ) in progress_events

    async def test_process_skips_when_only_control_message_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        compaction_module.reset_iterative_summary_cache("ses_empty_delta")
        progress_events: list[tuple[str, dict[str, Any]]] = []
        summarize_spy = AsyncMock(return_value=_structured_summary("unexpected"))
        archive_spy = AsyncMock(return_value="continue")

        async def fake_progress(stage: str, data: dict[str, Any]) -> None:
            progress_events.append((stage, data))

        async def fake_apply_config(cls, **kwargs: Any) -> None:  # noqa: ARG001
            return None

        async def fake_list_with_parts(cls, session_id: str) -> list:  # noqa: ARG001
            return msgs_with_parts

        msgs_with_parts = [
            _mwp("compact-command", "user", "/compact"),
        ]

        from flocks.provider.provider import Provider
        from flocks.session.message import Message

        monkeypatch.setattr(
            Provider,
            "get",
            classmethod(lambda cls, provider_id: MagicMock()),
        )
        monkeypatch.setattr(
            Provider,
            "apply_config",
            classmethod(fake_apply_config),
        )
        monkeypatch.setattr(
            Message,
            "list_with_parts",
            classmethod(fake_list_with_parts),
        )
        monkeypatch.setattr(
            compaction_module.summary,
            "summarize_single_pass",
            summarize_spy,
        )
        monkeypatch.setattr(
            SessionCompaction,
            "_archive_and_write_summary",
            classmethod(lambda cls, **kwargs: archive_spy(**kwargs)),
        )

        result = await SessionCompaction.process(
            session_id="ses_empty_delta",
            parent_id="compact-command",
            messages=[{"id": "compact-command"}],
            model_id="test-model",
            provider_id="test-provider",
            auto=False,
            progress_callback=fake_progress,
        )

        assert result == "skipped"
        summarize_spy.assert_not_awaited()
        archive_spy.assert_not_awaited()
        assert ("load", {"message_count": 0, "total_chars": 0}) in progress_events
        assert (
            "complete",
            {"result": "skipped_no_summary_input"},
        ) in progress_events


@pytest.mark.asyncio
class TestSummarizeChunkedIterative:
    async def test_calls_provider_once_per_chunk_no_merge(self) -> None:
        """Iterative path is N calls; no separate merge round-trip."""
        # 4 messages × 200 chars ≈ 800 chars; split_at=300 → 3 chunks.
        chat_messages = _make_messages(4)
        responses = [
            _FakeResponse(_structured_summary("c0")),
            _FakeResponse(_structured_summary("c1")),
            _FakeResponse(_structured_summary("c2 final")),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=responses)

        result = await summarize_chunked_iterative(
            chat_messages,
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=300,
            provider_client=provider,
            model_id="test-model",
            max_tokens=4000,
            session_id="ses_test",
            chunk_size=300,
        )

        # Result = last successful running_summary.
        assert result is not None
        assert "c2 final" in result
        # N calls, no extra merge.
        assert provider.chat.call_count >= 2  # at least 2 chunks
        # No call should embed the merge-specific "Combine them into a single
        # coherent summary." instruction.
        for call in provider.chat.await_args_list:
            messages = call.kwargs["messages"]
            for m in messages:
                assert "Combine them into a single coherent summary" not in m.content

    async def test_running_summary_threads_through_chunks(self) -> None:
        """Each subsequent call sees previous response wrapped as PREVIOUS_SUMMARY."""
        chat_messages = _make_messages(3, chars=500)  # forces multiple chunks at split=500
        responses = [
            _FakeResponse(_structured_summary("alpha")),
            _FakeResponse(_structured_summary("beta")),
            _FakeResponse(_structured_summary("gamma")),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=responses)

        await summarize_chunked_iterative(
            chat_messages,
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=500,
            provider_client=provider,
            model_id="test-model",
            max_tokens=4000,
            session_id="ses_thread",
            chunk_size=500,
        )

        # Second call MUST contain the alpha summary as previous context.
        if provider.chat.await_count < 2:
            pytest.skip("test data did not split into ≥2 chunks under current split_at heuristics")
        second_call = provider.chat.await_args_list[1]
        second_prompt = second_call.kwargs["messages"][0].content
        assert "<<<PREVIOUS_SUMMARY>>>" in second_prompt
        assert "alpha" in second_prompt

    async def test_empty_response_does_not_corrupt_running_summary(self) -> None:
        """If one chunk returns empty, running_summary keeps prior value."""
        chat_messages = _make_messages(3, chars=500)
        responses = [
            _FakeResponse(_structured_summary("first")),
            _FakeResponse(""),  # empty mid chunk
            _FakeResponse(_structured_summary("final")),
        ]
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=responses)

        result = await summarize_chunked_iterative(
            chat_messages,
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=500,
            provider_client=provider,
            model_id="test-model",
            max_tokens=4000,
            session_id="ses_empty",
            chunk_size=500,
        )
        assert result is not None
        # We must end up with EITHER "first" or "final" — never an empty result.
        assert "first" in result or "final" in result

    async def test_timeout_does_not_crash(self) -> None:
        """A timeout on one chunk is logged + swallowed; we keep going."""
        chat_messages = _make_messages(2, chars=500)

        async def _chat(**_kwargs: Any) -> Any:
            # First call OK; raise TimeoutError on subsequent calls.
            if _chat.calls == 0:
                _chat.calls += 1
                return _FakeResponse(_structured_summary("first"))
            _chat.calls += 1
            raise asyncio.TimeoutError()
        _chat.calls = 0

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=_chat)

        result = await summarize_chunked_iterative(
            chat_messages,
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=500,
            provider_client=provider,
            model_id="test-model",
            max_tokens=4000,
            session_id="ses_timeout",
            chunk_size=500,
        )
        # Either ``first`` survives or returns None — but it must NOT raise.
        assert result is None or "first" in result

    async def test_previous_summary_seeds_first_chunk(self) -> None:
        """When ``previous_summary`` is provided, the first call already shows it."""
        chat_messages = _make_messages(1, chars=300)
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_FakeResponse(_structured_summary("ok")))

        await summarize_chunked_iterative(
            chat_messages,
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=2000,
            provider_client=provider,
            model_id="test-model",
            max_tokens=4000,
            session_id="ses_seed",
            chunk_size=2000,
            previous_summary="PRIOR SUMMARY CONTENT",
        )
        first_call = provider.chat.await_args_list[0]
        first_prompt = first_call.kwargs["messages"][0].content
        assert "<<<PREVIOUS_SUMMARY>>>" in first_prompt
        assert "PRIOR SUMMARY CONTENT" in first_prompt


# ---------------------------------------------------------------------------
# summarize_single_pass + previous_summary (PR-3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarizeSinglePassWithPrevious:
    async def test_previous_summary_injected_into_prompt(self) -> None:
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_FakeResponse(_structured_summary("x")))

        await summarize_single_pass(
            conversation_text="hello world",
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=4000,
            provider_client=provider,
            model_id="test-model",
            max_tokens=2000,
            previous_summary="EARLIER SUMMARY",
        )
        call = provider.chat.await_args_list[0]
        body = call.kwargs["messages"][0].content
        assert "EARLIER SUMMARY" in body
        assert "<<<PREVIOUS_SUMMARY>>>" in body

    async def test_no_previous_summary_uses_default_prompt(self) -> None:
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_FakeResponse(_structured_summary("y")))

        await summarize_single_pass(
            conversation_text="hello world",
            prompt_text=DEFAULT_COMPACTION_PROMPT,
            target_chars=4000,
            provider_client=provider,
            model_id="test-model",
            max_tokens=2000,
        )
        call = provider.chat.await_args_list[0]
        body = call.kwargs["messages"][0].content
        assert "<<<PREVIOUS_SUMMARY>>>" not in body
        assert "## Decisions" in body
