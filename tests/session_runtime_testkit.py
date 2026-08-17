"""Test-only helpers for exercising SessionLoop logical-turn control."""

from unittest.mock import AsyncMock, patch

from flocks.session.session_loop import (
    LoopCallbacks,
    LoopContext,
    LoopResult,
    SessionLoop,
)


async def run_logical_turns(
    turn: LoopContext,
    callbacks: LoopCallbacks,
) -> LoopResult:
    """Run the production owned-loop path with an in-memory test lease."""
    turn.callbacks = callbacks
    lease = SessionLoop._leases.acquire(turn.session.id, turn)
    if lease is None:
        raise RuntimeError(f"Session {turn.session.id} already has a test lease")

    with (
        patch.object(
            turn,
            "has_late_input",
            AsyncMock(return_value=False),
        ),
        patch.object(SessionLoop, "_publish_released", AsyncMock()),
    ):
        try:
            return await SessionLoop._run_owned_loop(lease, callbacks)
        finally:
            if SessionLoop._leases.owns(lease):
                SessionLoop._finalize_release_state_locked(lease)
