"""
RaptorSessionRunner — SessionRunner with parallel tool execution,
dynamic tool folding, and multi-layer API error recovery.

Extends the base Flocks SessionRunner with three additional capabilities:

1. **Parallel tool execution**
   All tool calls emitted in a single LLM response are executed concurrently
   via asyncio.gather, subject to path-conflict detection.

2. **Dynamic tool folding**
   When the agent's tool list exceeds FOLD_THRESHOLD entries, the full list
   is replaced by three lightweight proxy schemas (raptor_tool_search,
   raptor_tool_describe, raptor_tool_call) that let the LLM discover and
   invoke the real tools on demand.  Always-loaded and agent-declared tools
   are never folded.

3. **Provider fallback chain**
   When the primary provider exhausts its built-in retry budget the raptor
   runner tries each fallback provider from ``RAPTOR_FALLBACK_PROVIDERS`` in
   order.  Context-overflow errors also activate the fallback chain (a longer-
   context fallback may succeed where the primary cannot).

The native Flocks SessionLoop._run_loop() is unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from flocks.utils.log import Log
from flocks.session.runner import SessionRunner, StepResult
from flocks.engine.raptor.processor import RaptorStreamProcessor
from flocks.engine.raptor.retry import (
    RaptorRetryContext,
    looks_like_context_overflow,
    looks_like_rate_limit,
)
from flocks.engine.raptor.tool_fold import (
    PROXY_TOOL_NAMES,
    maybe_fold_tools,
)

if TYPE_CHECKING:
    from flocks.session.streaming.stream_processor import StreamProcessor

log = Log.create(service="engine.raptor.runner")


class RaptorSessionRunner(SessionRunner):
    """
    Raptor-enhanced SessionRunner.

    Behavioural differences from the base SessionRunner:
    - Tool calls are executed in parallel after the LLM stream completes.
    - Large tool lists are dynamically folded into three proxy schemas.
    - Rate-limit / context-overflow errors trigger a provider fallback chain.
    """

    # Set to the full catalog when folding is active for the current step.
    # Reset to None at the start of every _build_callable_tool_schema call.
    _raptor_fold_catalog: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # Dynamic tool folding
    # ------------------------------------------------------------------

    async def _build_callable_tool_schema(
        self,
        agent: Any,
        messages: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build tool schemas with optional dynamic folding.

        When the selected tool count exceeds FOLD_THRESHOLD, the full list is
        replaced by three proxy schemas (raptor_tool_search / describe / call)
        plus the always-loaded core tools.  The full catalog is stored on
        ``self._raptor_fold_catalog`` so the stream processor can dispatch
        proxy calls to the real tools later.
        """
        all_tools = await super()._build_callable_tool_schema(agent, messages)

        # Resolve core tool names: always-load tools + agent-declared tools.
        core_names: frozenset = frozenset()
        try:
            from flocks.tool.catalog import get_tool_catalog_metadata
            from flocks.agent.toolset import agent_declares_tool

            core_names = frozenset(
                t.get("function", {}).get("name", "")
                for t in all_tools
                if (
                    agent_declares_tool(agent, t.get("function", {}).get("name", ""))
                    or get_tool_catalog_metadata(
                        t.get("function", {}).get("name", "")
                    ).always_load
                )
            )
        except Exception as exc:
            log.debug("raptor.runner.core_names_error", {"error": str(exc)})

        # Also keep any proxy tool that is already present (idempotent).
        core_names = core_names | PROXY_TOOL_NAMES

        folded_schema, catalog = maybe_fold_tools(all_tools, core_names)
        self._raptor_fold_catalog = catalog  # None when folding is inactive.

        if catalog is not None:
            log.info("raptor.runner.tool_fold.applied", {
                "session_id": self.session.id,
                "step": self._step,
                "total_tools": len(all_tools),
                "schema_size": len(folded_schema),
            })

        return folded_schema

    # ------------------------------------------------------------------
    # Pass fold catalog to the stream processor
    # ------------------------------------------------------------------

    def _make_stream_processor(self, **kwargs: Any) -> "StreamProcessor":
        """Return a deferred-execution processor with the current fold catalog."""
        return RaptorStreamProcessor(
            fold_catalog=self._raptor_fold_catalog,
            abort_event=getattr(self, "_external_abort", None) or getattr(self, "_abort", None),
            provider_id=self.provider_id,
            model_id=self.model_id,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Trigger parallel execution after stream
    # ------------------------------------------------------------------

    async def _after_tools_collected(self, processor: "StreamProcessor") -> None:
        """Execute all deferred tool calls in parallel after streaming is done."""
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

    # ------------------------------------------------------------------
    # Provider fallback chain
    # ------------------------------------------------------------------

    async def _delete_last_error_assistant(self, last_user: Any) -> None:
        """Remove the failed assistant message before trying a fallback model."""
        try:
            from flocks.session.message import Message, MessageRole

            messages = await Message.list(self.session.id)
            for msg in reversed(messages):
                if getattr(msg, "role", None) != MessageRole.ASSISTANT:
                    continue
                if getattr(msg, "parentID", None) != getattr(last_user, "id", None):
                    continue
                if not getattr(msg, "error", None):
                    continue
                deleted = await Message.delete(self.session.id, msg.id)
                if deleted and self.callbacks.event_publish_callback:
                    await self.callbacks.event_publish_callback("message.deleted", {
                        "sessionID": self.session.id,
                        "messageID": msg.id,
                    })
                return
        except Exception as exc:
            log.debug("raptor.runner.cleanup_failed_attempt.error", {
                "error": str(exc),
                "session_id": self.session.id,
            })

    async def _process_step(
        self,
        messages: List[Any],
        last_user: Any,
    ) -> StepResult:
        """Wrap the base step with a provider-fallback outer loop.

        The base SessionRunner._process_step() already handles:
          - Same-provider retries for 429 / 5xx errors (up to 7 attempts).
          - Empty-response retries (up to 3 attempts).

        RaptorSessionRunner adds an *outer* loop that switches to the next
        entry in ``RAPTOR_FALLBACK_PROVIDERS`` when the primary provider
        exhausts its budget and returns a terminal error that looks like a
        rate-limit or context-overflow failure.

        Flow::

            for provider in [primary] + fallback_chain:
                result = super()._process_step(...)  # includes its own retries
                if result is success → return
                if result.error looks like rate-limit or overflow → try next
                else → return (non-retriable error)
        """
        retry_ctx = RaptorRetryContext(self.provider_id, self.model_id)

        while True:
            # Switch the runner to the current provider/model in the chain.
            self.provider_id = retry_ctx.current_provider
            self.model_id = retry_ctx.current_model

            result = await self._call_step_once(messages, last_user)

            if result.action != "stop" or not result.error:
                # Successful step (or intentional stop without error).
                self.provider_id = retry_ctx.primary_provider
                self.model_id = retry_ctx.primary_model
                return result

            # Check whether the error warrants trying a fallback provider.
            err = result.error
            is_retriable = (
                looks_like_rate_limit(err)
                or looks_like_context_overflow(err)
            )

            if is_retriable and retry_ctx.try_next_fallback():
                await self._delete_last_error_assistant(last_user)
                log.warn("raptor.runner.provider_fallback.switch", {
                    "session_id": self.session.id,
                    "step": self._step,
                    "from_provider": retry_ctx.primary_provider,
                    "to_provider": retry_ctx.current_provider,
                    "to_model": retry_ctx.current_model,
                    "error_preview": err[:120],
                })
                continue  # retry with the new provider

            # Exhausted fallbacks or non-retriable error — restore primary and
            # surface the original error.
            self.provider_id = retry_ctx.primary_provider
            self.model_id = retry_ctx.primary_model
            return result

    async def _call_step_once(
        self,
        messages: List[Any],
        last_user: Any,
    ) -> StepResult:
        """Delegate to the base class _process_step for a single attempt."""
        return await super()._process_step(messages, last_user)

