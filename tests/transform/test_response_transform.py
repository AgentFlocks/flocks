"""
Tests for the response transform extension point (flocks.transform) and the
session rewriter that drives it.
"""

from typing import Any, Dict, List

import pytest

from flocks.session.response_rewriter import SessionResponseRewriter
from flocks.transform import (
    PendingBufferOverflow,
    ResponseTransformError,
    open_response_stream,
    register_response_transform,
    reset_response_transform,
    response_transform_active,
    transform_response_text,
    transform_response_value,
)


@pytest.fixture(autouse=True)
def _clean_transform():
    reset_response_transform()
    yield
    reset_response_transform()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MarkerStream:
    """Rewrites ``[[X]]`` markers, holding a partial marker across chunks.

    Stands in for any transform that has to see a whole marker before it can
    decide what to emit -- the reason the interface is a stream rather than a
    per-chunk call.
    """

    def __init__(self, table: Dict[str, str], calls: List[str]):
        self._table = table
        self._calls = calls
        self._held = ""

    async def feed(self, text: str) -> str:
        self._calls.append(text)
        self._held += text
        out = ""
        while "[[" in self._held:
            start = self._held.index("[[")
            end = self._held.find("]]", start)
            if end == -1:
                # Partial marker: emit the safe prefix, hold from "[[" onwards.
                out += self._held[:start]
                self._held = self._held[start:]
                break
            out += self._held[:start]
            marker = self._held[start + 2 : end]
            out += self._table.get(marker, f"[[{marker}]]")
            self._held = self._held[end + 2 :]
        if "[[" not in self._held:
            out += self._held
            self._held = ""
        return out

    async def flush(self) -> str:
        held, self._held = self._held, ""
        return held

    async def close(self) -> None:
        return None


class MarkerTransform:
    def __init__(self, table: Dict[str, str]):
        self._table = table
        self.calls: List[str] = []
        self.opened: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "marker"

    async def open_stream(self, metadata):
        self.opened.append(metadata)
        return _MarkerStream(self._table, self.calls)

    async def transform_text(self, text: str, metadata):
        stream = _MarkerStream(self._table, self.calls)
        return (await stream.feed(text)) + (await stream.flush())

    async def transform_value(self, value, metadata):
        self.calls.append(repr(value))
        if isinstance(value, str):
            return await self.transform_text(value, metadata)
        if isinstance(value, dict):
            return {k: await self.transform_value(v, metadata) for k, v in value.items()}
        return value


class _HoardingStream:
    """Never emits anything: exercises the pending-buffer cap."""

    async def feed(self, text: str) -> str:
        return ""

    async def flush(self) -> str:
        return ""

    async def close(self) -> None:
        return None


class HoardingTransform:
    @property
    def name(self) -> str:
        return "hoarding"

    async def open_stream(self, metadata):
        return _HoardingStream()

    async def transform_text(self, text, metadata):
        return ""

    async def transform_value(self, value, metadata):
        return value


class _ExplodingStream:
    async def feed(self, text: str) -> str:
        raise RuntimeError("backend gone")

    async def flush(self) -> str:
        return ""

    async def close(self) -> None:
        return None


class ExplodingTransform:
    @property
    def name(self) -> str:
        return "exploding"

    async def open_stream(self, metadata):
        return _ExplodingStream()

    async def transform_text(self, text, metadata):
        raise RuntimeError("backend gone")

    async def transform_value(self, value, metadata):
        raise RuntimeError("backend gone")


# ---------------------------------------------------------------------------
# T1-T2: default and basic streaming
# ---------------------------------------------------------------------------

async def test_default_is_passthrough():
    """T1: with nothing registered the response is delivered unchanged."""
    assert response_transform_active() is False

    stream = await open_response_stream({})
    assert await stream.feed("hello world") == "hello world"
    assert await stream.flush() == ""
    assert await transform_response_text("hello", {}) == "hello"


async def test_registered_transform_processes_streamed_text():
    """T2: a registered transform sees the stream and its output is used."""
    register_response_transform(MarkerTransform({"NAME": "Ada"}))
    assert response_transform_active() is True

    stream = await open_response_stream({})
    out = await stream.feed("hi [[NAME]], welcome")

    assert out == "hi Ada, welcome"


async def test_partial_marker_is_held_until_complete():
    """T3: a marker split across chunks is not leaked half-processed."""
    register_response_transform(MarkerTransform({"NAME": "Ada"}))
    stream = await open_response_stream({})

    first = await stream.feed("hi [[NA")
    second = await stream.feed("ME]] there")

    assert first == "hi "  # the partial marker stayed inside the transform
    assert second == "Ada there"
    assert await stream.flush() == ""


# ---------------------------------------------------------------------------
# T4-T6: failure containment
# ---------------------------------------------------------------------------

async def test_pending_buffer_cap_is_enforced():
    """T4: a transform that stops emitting cannot grow the buffer forever."""
    register_response_transform(HoardingTransform(), max_pending_chars=32)
    stream = await open_response_stream({})

    with pytest.raises(PendingBufferOverflow):
        for _ in range(10):
            await stream.feed("0123456789")


async def test_transform_failure_does_not_emit_raw_text():
    """T5: when the transform fails the caller gets an error, not the input.

    Emitting the unprocessed chunk would defeat the point of having the
    transform in the path at all.
    """
    register_response_transform(ExplodingTransform())
    stream = await open_response_stream({})

    with pytest.raises(ResponseTransformError) as excinfo:
        await stream.feed("anything")

    assert "anything" not in str(excinfo.value)
    assert "exploding" in str(excinfo.value)


async def test_stream_stays_failed_after_an_error():
    """T6: a failed stream refuses further work rather than half-processing."""
    register_response_transform(ExplodingTransform())
    stream = await open_response_stream({})

    with pytest.raises(ResponseTransformError):
        await stream.feed("first")
    with pytest.raises(ResponseTransformError):
        await stream.feed("second")


# ---------------------------------------------------------------------------
# T7-T9: non-streamed paths and registration checks
# ---------------------------------------------------------------------------

async def test_whole_response_transform():
    """T7: non-streamed responses go through the same transform."""
    register_response_transform(MarkerTransform({"NAME": "Ada"}))

    assert await transform_response_text("hi [[NAME]]", {}) == "hi Ada"


async def test_structured_payload_transform():
    """T8: structured payloads (tool arguments, metadata) are processed too."""
    register_response_transform(MarkerTransform({"HOST": "db-1"}))

    result = await transform_response_value({"target": "[[HOST]]", "port": 5432}, {})

    assert result == {"target": "db-1", "port": 5432}


async def test_incomplete_transform_is_rejected_at_registration():
    """T9: a contract gap is caught when registering, not mid-response."""

    class Partial:
        name = "partial"

        async def open_stream(self, metadata):
            return _HoardingStream()

    with pytest.raises(ValueError, match="接口不完整"):
        register_response_transform(Partial())


# ---------------------------------------------------------------------------
# T10-T14: session rewriter behaviour
# ---------------------------------------------------------------------------

async def test_rewriter_prefers_transform_over_legacy_map():
    """T10: a registered transform is used and the legacy map is ignored.

    The map exists only for builds that have not moved to a transform yet; once
    one is installed it is the only thing with the information to process the
    response, so consulting both would produce a mix of the two.
    """
    transform = MarkerTransform({"NAME": "Ada"})
    register_response_transform(transform)

    rewriter = await SessionResponseRewriter.create(
        {"sessionID": "s1"},
        legacy_replacements=[("[[NAME]]", "WRONG")],
    )

    assert await rewriter.feed_text("hi [[NAME]]") == "hi Ada"
    await rewriter.close()


async def test_rewriter_falls_back_to_legacy_map():
    """T11: builds still handing down a map keep working."""
    rewriter = await SessionResponseRewriter.create(
        {"sessionID": "s1"},
        legacy_replacements=[("[[NAME]]", "Ada")],
    )

    assert await rewriter.feed_text("hi [[NAME]]") == "hi Ada"
    await rewriter.close()


async def test_rewriter_is_absent_when_nothing_needs_rewriting():
    """T12: no transform and no map means the runner skips rewriting entirely."""
    assert await SessionResponseRewriter.create({"sessionID": "s1"}) is None


async def test_repeated_chunk_metadata_is_transformed_once():
    """T13: identical chunk metadata must not cost one call per chunk.

    Providers repeat the same metadata dict on every chunk of a reasoning
    block; without memoising, an out-of-process transform would take a round
    trip per chunk.
    """
    transform = MarkerTransform({"HOST": "db-1"})
    register_response_transform(transform)
    rewriter = await SessionResponseRewriter.create({"sessionID": "s1"})

    payload = {"target": "[[HOST]]"}
    for _ in range(5):
        result = await rewriter.transform_metadata(payload, {"sessionID": "s1"})
        assert result == {"target": "db-1"}

    metadata_calls = [call for call in transform.calls if call.startswith("{")]
    assert len(metadata_calls) == 1
    await rewriter.close()


async def test_text_and_reasoning_streams_are_independent():
    """T14: a partial marker in one channel must not corrupt the other."""
    register_response_transform(MarkerTransform({"NAME": "Ada", "ROLE": "admin"}))
    rewriter = await SessionResponseRewriter.create({"sessionID": "s1"})

    # Text holds a partial marker while reasoning completes a different one.
    assert await rewriter.feed_text("user [[NA") == "user "
    assert await rewriter.feed_reasoning("as [[ROLE]] ") == "as admin "
    assert await rewriter.feed_text("ME]] done") == "Ada done"

    assert await rewriter.flush_text() == ""
    assert await rewriter.flush_reasoning() == ""
    await rewriter.close()


# ---------------------------------------------------------------------------
# T15-T18: coverage across every model call site
# ---------------------------------------------------------------------------

async def test_complete_response_passthrough_by_default():
    """T15: with no transform installed the text is untouched."""
    from flocks.session.response_rewriter import transform_complete_response

    assert await transform_complete_response("hi [[NAME]]", {}) == "hi [[NAME]]"


async def test_complete_response_uses_the_transform():
    """T16: whole responses go through the same transform as streamed ones."""
    from flocks.session.response_rewriter import transform_complete_response

    register_response_transform(MarkerTransform({"NAME": "Ada"}))

    assert await transform_complete_response("hi [[NAME]]", {}) == "hi Ada"


async def test_complete_response_falls_back_to_legacy_map():
    """T17: builds still handing down a map keep working here too."""
    from flocks.session.response_rewriter import transform_complete_response

    result = await transform_complete_response(
        "hi [[NAME]]", {}, legacy_replacements=[("[[NAME]]", "Ada")]
    )

    assert result == "hi Ada"


def test_every_model_call_site_runs_the_response_transform():
    """T18: no model entry point is left unprocessed.

    Wiring only the chat runner would leave title generation, compaction
    summaries and goal judging emitting unprocessed model output -- the same
    responses, reached through a different door. Each site that calls the
    llm_before stage must also route its response through the transform.
    """
    import re
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[2] / "flocks"
    # The pipeline module defines the stage rather than calling it.
    definition_module = source_root / "hooks" / "pipeline.py"
    call_sites = {
        path
        for path in source_root.rglob("*.py")
        if path != definition_module and "run_llm_before" in path.read_text(encoding="utf-8")
    }
    assert len(call_sites) >= 4, f"expected the known model call sites, found {call_sites}"

    transform_markers = re.compile(
        r"transform_complete_response|SessionResponseRewriter"
    )
    unprocessed = sorted(
        str(path.relative_to(source_root))
        for path in call_sites
        if not transform_markers.search(path.read_text(encoding="utf-8"))
    )

    assert unprocessed == [], f"model responses not routed through the transform: {unprocessed}"
