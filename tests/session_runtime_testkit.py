"""Test-only helpers for exercising SessionLoop logical-turn control."""

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
    """Run logical turns without acquiring a persisted session lease."""
    turn.callbacks = callbacks
    policy = turn.continuation_policy or SessionLoop._continuation_policy
    processed_user_id = None
    while True:
        try:
            await policy.prepare_logical_turn(turn)
            processed_user_id = turn.prepared_user_id or processed_user_id
            outcome = await SessionLoop._run_logical_input(turn)
            processed_user_id = (
                outcome.state.current_user_id or processed_user_id
            )
            if await SessionLoop._should_continue(turn, policy, outcome):
                continue
        except Exception as exc:
            outcome = await SessionLoop._handle_execution_error(
                turn,
                exc,
                processed_user_id,
            )
        return SessionLoop._to_loop_result(turn, outcome)
