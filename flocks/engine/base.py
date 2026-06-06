"""
Agent Loop Engine Protocol

Defines the pluggable interface every non-native engine must implement.
The interface mirrors SessionLoop._run_loop(ctx, callbacks) exactly so
engines are drop-in replacements for the native loop body.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentLoopEngine(Protocol):
    """
    Protocol for pluggable agent loop engines.

    Attributes:
        id:           Machine identifier used in session.metadata["loop_engine"],
                      e.g. "raptor".  Must be unique and stable.
        display_name: Human-readable name shown in the WebUI engine selector.
        description:  Short tooltip text shown in the WebUI.
    """

    id: str
    display_name: str
    description: str

    async def run(
        self,
        ctx: Any,               # flocks.session.session_loop.LoopContext
        callbacks: Any = None,  # flocks.session.session_loop.LoopCallbacks
    ) -> Any:                   # flocks.session.session_loop.LoopResult
        """
        Execute the agent loop.

        Receives a fully-prepared LoopContext (session loaded, model resolved,
        abort_event wired, _active_loops registered).  Must return a LoopResult
        with the same semantics as _run_loop:

            action: "stop" | "continue" | "compact" | "error" | "aborted"

        The engine is responsible for:
        - Emitting turn_state SSE events (turn.started / continued / stopped)
          via callbacks.event_publish_callback so the WebUI turn indicator works.
        - Detecting and processing queued user messages after each turn
          (equivalent to _run_loop's _detect_queued_user_message loop).
        - Writing assistant/tool messages back to Flocks storage so WebUI renders.
        """
        ...
