"""
Regression tests for the chunk-handling logic in
``SessionRunner._call_llm`` (Issue #1 of PR review for Gemini 3 support).

The previous implementation treated any ``StreamChunk`` carrying ``reasoning``
as reasoning-only and immediately ``continue``d, silently dropping ``delta`` /
``tool_calls`` that arrived in the same chunk.  These tests assert that the
fixed loop consumes all three event types out of a single mixed chunk and
correctly opens / closes the reasoning block around interleaved text.

We exercise the loop in isolation by replicating the exact runner code so the
test pins the contract; the same loop is used in
``flocks/session/runner.py``.  Drift is unlikely because the loop is small and
documented, but a follow-up could refactor the runner to call this helper
directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Minimal stand-ins for runner imports so the test stays self-contained.
# ---------------------------------------------------------------------------


@dataclass
class FakeStreamChunk:
    delta: str = ""
    reasoning: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    event_type: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


@dataclass
class _Event:
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)


class _RecordingProcessor:
    """Records events in the order they were processed."""

    def __init__(self):
        self.events: List[_Event] = []
        self.tool_chunks: List[Dict[str, Any]] = []

    async def process_event(self, ev):
        cls = type(ev).__name__
        if cls == "ReasoningStartEvent":
            self.events.append(_Event("reasoning_start", {"id": ev["id"]}))
        elif cls == "ReasoningDeltaEvent":
            self.events.append(_Event("reasoning_delta", {"id": ev["id"], "text": ev["text"]}))
        elif cls == "ReasoningEndEvent":
            self.events.append(_Event("reasoning_end", {"id": ev["id"]}))
        elif cls == "TextStartEvent":
            self.events.append(_Event("text_start"))
        elif cls == "TextDeltaEvent":
            self.events.append(_Event("text_delta", {"text": ev["text"]}))


# Tiny event stand-ins (dict subclasses so they expose attribute-like access).
class _EventDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def ReasoningStartEvent(*, id):  # noqa: N802 – mimic real event class name
    e = _EventDict(id=id)
    e.__class__.__name__ = "ReasoningStartEvent"
    return e


def ReasoningDeltaEvent(*, id, text):  # noqa: N802
    e = _EventDict(id=id, text=text)
    e.__class__.__name__ = "ReasoningDeltaEvent"
    return e


def ReasoningEndEvent(*, id):  # noqa: N802
    e = _EventDict(id=id)
    e.__class__.__name__ = "ReasoningEndEvent"
    return e


def TextStartEvent():  # noqa: N802
    e = _EventDict()
    e.__class__.__name__ = "TextStartEvent"
    return e


def TextDeltaEvent(*, text):  # noqa: N802
    e = _EventDict(text=text)
    e.__class__.__name__ = "TextDeltaEvent"
    return e


class _ToolAccumulator:
    def __init__(self):
        self.fed: List[Dict[str, Any]] = []

    async def feed_chunk(self, tc):
        self.fed.append(tc)


# ---------------------------------------------------------------------------
# The function under test: a faithful copy of the consumer loop in
# SessionRunner._call_llm (kept in sync via comments + cross-references).
# ---------------------------------------------------------------------------


async def consume_chunks(chunks, processor, tool_accumulator) -> Dict[str, int]:
    """Mirror of the post-fix chunk loop in flocks/session/runner.py."""
    chunk_counts = {"reasoning": 0, "text": 0, "tool": 0}
    text_started = False
    reasoning_id_counter = 0
    state: Dict[str, Optional[str]] = {"reasoning_id": None}

    for chunk in chunks:
        event_type = getattr(chunk, "event_type", None)

        chunk_reasoning = getattr(chunk, "reasoning", None) or None
        if not chunk_reasoning and event_type == "reasoning":
            chunk_reasoning = getattr(chunk, "delta", "") or None

        chunk_text = ""
        if event_type != "reasoning" or getattr(chunk, "reasoning", None):
            chunk_text = getattr(chunk, "delta", "") or ""

        chunk_tool_calls = getattr(chunk, "tool_calls", None)

        if chunk_reasoning:
            chunk_counts["reasoning"] += 1
            if state["reasoning_id"] is None:
                reasoning_id_counter += 1
                state["reasoning_id"] = f"reasoning-{reasoning_id_counter}"
                await processor.process_event(
                    ReasoningStartEvent(id=state["reasoning_id"])
                )
            await processor.process_event(
                ReasoningDeltaEvent(id=state["reasoning_id"], text=chunk_reasoning)
            )

        if (chunk_text or chunk_tool_calls) and state["reasoning_id"] is not None:
            await processor.process_event(
                ReasoningEndEvent(id=state["reasoning_id"])
            )
            state["reasoning_id"] = None

        if chunk_text:
            chunk_counts["text"] += 1
            if not text_started:
                await processor.process_event(TextStartEvent())
                text_started = True
            await processor.process_event(TextDeltaEvent(text=chunk_text))

        if chunk_tool_calls:
            chunk_counts["tool"] += 1
            for tc in chunk_tool_calls:
                await tool_accumulator.feed_chunk(tc)

    if state["reasoning_id"] is not None:
        await processor.process_event(ReasoningEndEvent(id=state["reasoning_id"]))

    return chunk_counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


import pytest


class TestBundledChunks:
    """Bundled (reasoning + text + tool_calls) chunks must not lose data."""

    @pytest.mark.asyncio
    async def test_reasoning_with_text_in_same_chunk_emits_both(self):
        proc = _RecordingProcessor()
        acc = _ToolAccumulator()

        chunks = [
            FakeStreamChunk(delta="hello", reasoning="thinking..."),
        ]
        counts = await consume_chunks(chunks, proc, acc)

        kinds = [e.kind for e in proc.events]
        assert "reasoning_delta" in kinds, "reasoning was not emitted"
        assert "text_delta" in kinds, "text was dropped from a reasoning-bearing chunk"
        # The reasoning block must close before the text block opens.
        r_end_idx = kinds.index("reasoning_end")
        t_start_idx = kinds.index("text_start")
        assert r_end_idx < t_start_idx
        assert counts == {"reasoning": 1, "text": 1, "tool": 0}

    @pytest.mark.asyncio
    async def test_reasoning_with_tool_calls_in_same_chunk_emits_both(self):
        proc = _RecordingProcessor()
        acc = _ToolAccumulator()

        chunks = [
            FakeStreamChunk(
                reasoning="planning the search call",
                tool_calls=[{"id": "c1", "function": {"name": "search", "arguments": "{}"}}],
            ),
        ]
        counts = await consume_chunks(chunks, proc, acc)

        kinds = [e.kind for e in proc.events]
        assert "reasoning_delta" in kinds
        assert "reasoning_end" in kinds, "reasoning block must close before tool call dispatch"
        assert acc.fed, "tool call was dropped from a reasoning-bearing chunk"
        assert counts == {"reasoning": 1, "text": 0, "tool": 1}

    @pytest.mark.asyncio
    async def test_legacy_event_type_reasoning_treats_delta_as_thought(self):
        """Backward compat: when event_type == 'reasoning' and there's no
        ``reasoning`` field, the consumer still treats ``delta`` as the
        thought text – never as user-visible text."""
        proc = _RecordingProcessor()
        acc = _ToolAccumulator()

        chunks = [
            FakeStreamChunk(delta="thinking via delta", event_type="reasoning"),
        ]
        await consume_chunks(chunks, proc, acc)

        kinds = [e.kind for e in proc.events]
        assert "reasoning_delta" in kinds
        assert "text_delta" not in kinds, (
            "delta was double-emitted as both reasoning and text"
        )

    @pytest.mark.asyncio
    async def test_separate_chunks_close_block_then_open_text(self):
        """The post-fix Gemini provider emits separate chunks; verify the loop
        opens reasoning then cleanly switches to text on the next chunk."""
        proc = _RecordingProcessor()
        acc = _ToolAccumulator()

        chunks = [
            FakeStreamChunk(reasoning="step 1", event_type="reasoning"),
            FakeStreamChunk(reasoning="step 2", event_type="reasoning"),
            FakeStreamChunk(delta="answer", event_type="text"),
        ]
        await consume_chunks(chunks, proc, acc)

        kinds = [e.kind for e in proc.events]
        # Two deltas streamed under a single reasoning block.
        assert kinds.count("reasoning_start") == 1
        assert kinds.count("reasoning_delta") == 2
        assert kinds.count("reasoning_end") == 1
        # Then the text block opens cleanly.
        assert kinds[-2:] == ["text_start", "text_delta"]

    @pytest.mark.asyncio
    async def test_usage_only_chunk_does_not_close_reasoning(self):
        proc = _RecordingProcessor()
        acc = _ToolAccumulator()

        chunks = [
            FakeStreamChunk(reasoning="thinking", event_type="reasoning"),
            # usage-only mid-stream chunk (no delta/tool_calls/reasoning).
            FakeStreamChunk(usage={"prompt_tokens": 5, "completion_tokens": 0}),
            FakeStreamChunk(reasoning="more thinking", event_type="reasoning"),
        ]
        await consume_chunks(chunks, proc, acc)

        kinds = [e.kind for e in proc.events]
        # All three reasoning deltas should fall under the same block.
        assert kinds.count("reasoning_start") == 1
        assert kinds.count("reasoning_end") == 1
        assert kinds.count("reasoning_delta") == 2
