"""
RaptorSessionRunner — SessionRunner with parallel tool execution.

Subclasses Flocks SessionRunner and overrides two extension points:
  - _make_stream_processor: returns RaptorStreamProcessor (deferred tool execution)
  - _after_tools_collected: triggers parallel execution of all collected tool calls

All other session semantics — abort handling, SSE events, message persistence,
context compaction, retry logic, permission gates, hook pipeline — are
inherited unchanged from SessionRunner.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from flocks.utils.log import Log
from flocks.session.runner import SessionRunner
from flocks.engine.raptor.processor import RaptorStreamProcessor

if TYPE_CHECKING:
    from flocks.session.streaming.stream_processor import StreamProcessor

log = Log.create(service="engine.raptor.runner")


class RaptorSessionRunner(SessionRunner):
    """
    Raptor-enhanced SessionRunner.

    The only behavioural difference from the base SessionRunner is tool
    execution order: instead of executing each tool call serially as it
    arrives in the LLM stream (base-class behaviour), RaptorSessionRunner
    collects all tool calls from a single LLM response and executes them in
    parallel after the stream completes — using path-conflict detection to
    prevent concurrent writes to the same file.
    """

    def _make_stream_processor(self, **kwargs: Any) -> "StreamProcessor":
        """Return the deferred-execution processor for parallel tool batching."""
        return RaptorStreamProcessor(**kwargs)

    async def _after_tools_collected(self, processor: "StreamProcessor") -> None:
        """
        Execute all deferred tool calls in parallel after streaming is done.

        Called by SessionRunner._call_llm() right after flush_remaining(),
        before FinishEvent is emitted, so tool results are persisted by the
        time the stream is considered complete.
        """
        if not isinstance(processor, RaptorStreamProcessor):
            return
        if not processor._deferred:
            return

        log.info("raptor.runner.parallel_exec", {
            "session_id": self.session.id,
            "step": self._step,
            "tool_count": len(processor._deferred),
        })
        await processor.execute_deferred_parallel()
