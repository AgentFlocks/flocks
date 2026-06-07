"""
RaptorSessionLoop — SessionLoop subclass that drives RaptorSessionRunner.

Design principle (design §3.2):
  Raptor is a complete replacement for _run_loop.  By subclassing SessionLoop
  and inheriting _run_loop wholesale, the following responsibilities are
  automatically satisfied:
    - turn.started / turn.continued / turn.stopped SSE events
    - Queued-message continuation (_detect_queued_user_message)
    - First-turn title generation
    - Compaction overflow checks
    - _active_loops registration / busy-idle status / abort chain

The only difference: _create_step_runner() returns RaptorSessionRunner,
enabling parallel tool execution on every loop step.
"""

from __future__ import annotations

from typing import Any

from flocks.session.session_loop import SessionLoop, LoopContext
from flocks.engine.raptor.runner import RaptorSessionRunner


class RaptorSessionLoop(SessionLoop):
    """
    Raptor loop: all of SessionLoop._run_loop reused; only the runner differs.

    By overriding _create_step_runner() to return RaptorSessionRunner, every
    _process_step() in this loop uses parallel tool execution while keeping:
    - Identical turn-state SSE semantics (turn.started / continued / stopped)
    - Identical queued-message continuation
    - Identical compaction, abort, and lifecycle management
    """

    @classmethod
    def _create_step_runner(cls, ctx: LoopContext, runner_cbs: Any) -> Any:
        runner = RaptorSessionRunner(
            session=ctx.session,
            provider_id=ctx.provider_id,
            model_id=ctx.model_id,
            agent_name=ctx.agent_name,
            abort_event=ctx.abort_event,
            callbacks=runner_cbs,
            session_ctx=ctx.session_ctx,
            memory_bootstrap_data=ctx.memory_bootstrap_data,
            static_cache=ctx.runner_static_cache,
        )
        runner._step = ctx.trace_step
        return runner
