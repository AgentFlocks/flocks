"""
RaptorEngine — high-performance agent loop engine built into Flocks.

Architecture (self-contained; no external dependencies):

    SessionLoop.run()                               <- shared scaffolding
        └── RaptorSessionLoop._run_loop(ctx, cbs)   <- Raptor loop body
                └── RaptorSessionRunner._process_step()
                        └── RaptorStreamProcessor    <- deferred tool execution
                                └── asyncio.gather() <- parallel tool execution

Core enhancements over the native Flocks loop:

  1. Parallel tool execution
     All tool calls from one LLM response are collected first, then executed
     concurrently via asyncio.gather (up to MAX_PARALLEL_TOOLS at once).
     Path-conflict detection downgrades conflicting writes to serial execution.

  2. Full turn_state SSE semantics (design §3.2)
     Inherits RaptorSessionLoop._run_loop, which emits the same
     turn.started / turn.continued / turn.stopped events as the native loop.

  3. Queued-message continuation (design §3.2)
     Inherits _detect_queued_user_message so messages sent mid-turn are
     automatically processed after the current turn completes.

  4. Flocks agent identity preserved (§4.5, built-in)
     RaptorSessionRunner inherits SessionRunner and calls
     SessionPrompt.build_system_prompts() and _build_callable_tool_schema()
     on every step — agent system prompts, tool whitelists, and skill
     constraints behave identically to the native engine.

  5. ToolRegistry as the unified security gate (§4.4 ToolBridge, built-in)
     Every tool call is routed through ToolRegistry.execute() inside
     RaptorStreamProcessor._execute_one_tool(), so Flocks whitelist and
     skill enforcement apply automatically without a separate bridge layer.
"""

from typing import Any

from flocks.utils.log import Log
from flocks.session.session_loop import LoopCallbacks

log = Log.create(service="engine.raptor")


class RaptorEngine:
    """
    Raptor AgentLoopEngine — parallel tool execution engine built into Flocks.

    Implements the AgentLoopEngine protocol (design §4.1):
      id / display_name / description metadata + async run(ctx, callbacks).
    """

    id = "raptor"
    display_name = "Raptor"
    description = "Raptor loop: parallel tool execution · path-conflict detection · native Flocks security"

    async def run(self, ctx: Any, callbacks: Any = None) -> Any:
        """
        Execute one Raptor agent loop turn.

        Delegates directly to RaptorSessionLoop._run_loop(ctx, callbacks), which
        is a complete drop-in replacement for SessionLoop._run_loop (design §3.2)
        differing only in the runner factory.

        The outer SessionLoop.run() scaffolding (_active_loops, busy/idle status,
        abort wiring, finally cleanup) is handled by the caller and is not
        repeated here.
        """
        from flocks.engine.raptor.loop import RaptorSessionLoop

        cbs = callbacks if callbacks is not None else LoopCallbacks()

        log.info("raptor.engine.run", {
            "session_id": ctx.session.id,
            "agent": ctx.agent_name,
            "provider_id": ctx.provider_id,
            "model_id": ctx.model_id,
            "step_offset": ctx.trace_step_offset,
        })

        return await RaptorSessionLoop._run_loop(ctx, cbs)


# Auto-register into LoopEngineRegistry on module import.
try:
    from flocks.engine.registry import LoopEngineRegistry
    LoopEngineRegistry.register(RaptorEngine())
except Exception as _reg_err:
    log.warn("raptor.engine.register_failed", {"error": str(_reg_err)})
