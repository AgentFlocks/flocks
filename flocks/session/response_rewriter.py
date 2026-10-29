"""
Applies the registered response transform to one model call's output.

The session runner deals with three separate output channels -- assistant text,
reasoning text, and chunk metadata -- and the first two are streamed.  Each
streamed channel needs its own transform stream, because a transform holding a
partial marker at the end of a text chunk must not have that state disturbed by
a reasoning chunk arriving in between.

This type owns those streams so the runner can keep treating rewriting as a
single ``feed``/``flush`` concern.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from flocks.session.llm_hook_utils import (
    StreamingTextReplacementBuffer,
    restore_text_with_replacements,
    restore_value_with_replacements,
)
from flocks.transform import (
    GuardedTransformStream,
    open_response_stream,
    response_transform_active,
    transform_response_text,
    transform_response_value,
)
from flocks.utils.log import Log

log = Log.create(service="session.rewriter")


class _LegacyBufferStream:
    """Adapts the in-process replacement buffer to the async stream shape.

    Kept so a build whose extensions still hand down a replacement map keeps
    working while they move to registering a transform instead.
    """

    def __init__(self, replacements: List[Tuple[str, str]]) -> None:
        self._buffer = StreamingTextReplacementBuffer(replacements)

    async def feed(self, text: str) -> str:
        return self._buffer.feed(text)

    async def flush(self) -> str:
        return self._buffer.flush()

    async def close(self) -> None:
        return None


class SessionResponseRewriter:
    """Rewrites one model call's streamed output through the active transform."""

    def __init__(
        self,
        text_stream: Any,
        reasoning_stream: Any,
        *,
        legacy_replacements: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        self._text_stream = text_stream
        self._reasoning_stream = reasoning_stream
        self._legacy_replacements = legacy_replacements or []
        # Chunk metadata repeats -- a whole reasoning block usually carries the
        # same dict on every chunk.  Memoising by value keeps that from turning
        # into one transform call per chunk.
        self._metadata_memo: Dict[str, Any] = {}

    @classmethod
    async def create(
        cls,
        metadata: Dict[str, Any],
        *,
        legacy_replacements: Optional[List[Tuple[str, str]]] = None,
    ) -> Optional["SessionResponseRewriter"]:
        """Build a rewriter, or return ``None`` when nothing needs rewriting.

        A registered transform wins over any replacement map an extension may
        still be handing down: when a transform is installed it is the only
        thing that has the information needed to process the response, and the
        map is not consulted at all.
        """

        if response_transform_active():
            text_stream: GuardedTransformStream = await open_response_stream(
                {**metadata, "channel": "text"}
            )
            reasoning_stream: GuardedTransformStream = await open_response_stream(
                {**metadata, "channel": "reasoning"}
            )
            return cls(text_stream, reasoning_stream)

        if legacy_replacements:
            log.debug(
                "session.rewriter.legacy_replacements",
                {"count": len(legacy_replacements)},
            )
            return cls(
                _LegacyBufferStream(legacy_replacements),
                _LegacyBufferStream(legacy_replacements),
                legacy_replacements=legacy_replacements,
            )

        return None

    # -- streamed channels -------------------------------------------------

    async def feed_text(self, text: str) -> str:
        if not text:
            return text
        return await self._text_stream.feed(text)

    async def flush_text(self) -> str:
        return await self._text_stream.flush()

    async def feed_reasoning(self, text: str) -> str:
        if not text:
            return text
        return await self._reasoning_stream.feed(text)

    async def flush_reasoning(self) -> str:
        return await self._reasoning_stream.flush()

    # -- non-streamed payloads --------------------------------------------

    async def transform_metadata(self, value: Any, metadata: Dict[str, Any]) -> Any:
        """Process a structured chunk payload alongside the streamed text."""

        if not value:
            return value
        if self._legacy_replacements:
            return restore_value_with_replacements(value, self._legacy_replacements)

        try:
            memo_key = repr(value)
        except Exception:  # unhashable/unrepresentable payloads just skip the memo
            memo_key = None
        if memo_key is not None and memo_key in self._metadata_memo:
            return self._metadata_memo[memo_key]

        result = await transform_response_value(value, {**metadata, "channel": "metadata"})
        if memo_key is not None:
            self._metadata_memo[memo_key] = result
        return result

    async def close(self) -> None:
        for stream in (self._text_stream, self._reasoning_stream):
            close = getattr(stream, "close", None)
            if close is None:
                continue
            try:
                await close()
            except Exception as exc:
                log.warn("session.rewriter.close_failed", {"error": str(exc)})


__all__ = ["SessionResponseRewriter", "transform_complete_response"]


async def transform_complete_response(
    text: str,
    metadata: Dict[str, Any],
    *,
    legacy_replacements: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Process a fully accumulated model response.

    The counterpart to :class:`SessionResponseRewriter` for the model calls
    that are consumed whole rather than streamed -- title generation, summaries,
    goal extraction. They go through the same transform as the main
    conversation, so a deployment's processing applies to every model response
    rather than only to the chat entry point.
    """

    if not text:
        return text
    if response_transform_active():
        return await transform_response_text(text, metadata)
    if legacy_replacements:
        return restore_text_with_replacements(text, legacy_replacements)
    return text
