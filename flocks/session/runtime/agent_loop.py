"""The control loop for one logical user input."""

from __future__ import annotations

import asyncio

from flocks.session.message import MessageInfo
from flocks.session.runtime.contracts import (
    AgentRunOutcome,
    AgentRunStatus,
    StepAction,
    TurnPreparationStatus,
)
from flocks.session.runtime.session_turn import LoopContext
from flocks.session.runtime.step_engine import StepCancelled, StepEngine
from flocks.utils.log import Log


log = Log.create(service="session.agent_loop")


class AgentLoop:
    """Decide whether one logical user input needs another model step."""

    async def run(
        self,
        turn: LoopContext,
        engine: StepEngine,
    ) -> AgentRunOutcome[MessageInfo]:
        """Run the current logical input to a session-level boundary."""
        last_user = None
        last_message = None

        while not turn.aborted:
            preparation = await turn.prepare_step()
            if preparation.status == TurnPreparationStatus.CONTINUE:
                continue
            if preparation.status == TurnPreparationStatus.COMPLETE:
                return AgentRunOutcome(
                    status=AgentRunStatus.COMPLETED,
                    last_user=last_user,
                    last_message=preparation.last_message or last_message,
                )

            snapshot = preparation.snapshot
            if snapshot is None:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    last_user=last_user,
                    last_message=last_message,
                    error=(
                        "LoopContext returned READY without a model-turn "
                        "snapshot"
                    ),
                )

            last_user = snapshot.last_user

            try:
                step_task = asyncio.create_task(engine.run(snapshot))
                turn._current_step_task = step_task
                try:
                    step_result = await step_task
                except asyncio.CancelledError as exc:
                    cleanup_task = asyncio.create_task(
                        engine.finalize_cancelled_attempt(),
                        name=(
                            "step-cancel-cleanup:"
                            f"{turn.session.id}:{turn.step}"
                        ),
                    )
                    while True:
                        try:
                            await asyncio.shield(cleanup_task)
                            break
                        except asyncio.CancelledError:
                            if cleanup_task.cancelled():
                                break
                            continue
                        except Exception as cleanup_error:
                            log.error(
                                "session.step.cancel_cleanup_failed",
                                {
                                    "session_id": turn.session.id,
                                    "step": turn.step,
                                    "error": str(cleanup_error),
                                },
                            )
                            break
                    if turn.aborted:
                        raise StepCancelled from exc
                    raise
                finally:
                    if turn._current_step_task is step_task:
                        turn._current_step_task = None
            except StepCancelled:
                log.info(
                    "session.step.cancelled",
                    {
                        "session_id": turn.session.id,
                        "step": turn.step,
                    },
                )
                return AgentRunOutcome(
                    status=AgentRunStatus.ABORTED,
                    last_user=last_user,
                    last_message=last_message,
                    error="Aborted",
                )

            boundary = await turn.commit_step(step_result)
            last_message = boundary.last_message or last_message

            if turn.aborted:
                return AgentRunOutcome(
                    status=AgentRunStatus.ABORTED,
                    last_user=last_user,
                    last_message=last_message,
                    error=step_result.error,
                )

            if boundary.input_available:
                return AgentRunOutcome(
                    status=AgentRunStatus.INPUT_AVAILABLE,
                    last_user=last_user,
                    last_message=last_message,
                    error=step_result.error,
                    step_result=step_result,
                )

            failure = step_result.failure
            if failure is not None:
                status = (
                    AgentRunStatus.RETRYABLE_FAILURE
                    if (
                        failure.allow_fallback
                        and failure.attempt_state.replay_safe
                    )
                    else AgentRunStatus.FATAL_FAILURE
                )
                return AgentRunOutcome(
                    status=status,
                    last_user=last_user,
                    last_message=last_message,
                    error=failure.message,
                    step_result=step_result,
                )

            if step_result.action == StepAction.CONTINUE:
                continue
            if step_result.action == StepAction.COMPACT:
                return AgentRunOutcome(
                    status=AgentRunStatus.CONTEXT_OVERFLOW,
                    last_user=last_user,
                    last_message=last_message,
                    error=step_result.error,
                    step_result=step_result,
                )
            if step_result.action != StepAction.STOP:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    last_user=last_user,
                    last_message=last_message,
                    error=f"Unknown step action: {step_result.action}",
                    step_result=step_result,
                )
            if step_result.error:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    last_user=last_user,
                    last_message=last_message,
                    error=step_result.error,
                    step_result=step_result,
                )

            return AgentRunOutcome(
                status=AgentRunStatus.COMPLETED,
                last_user=last_user,
                last_message=last_message,
                step_result=step_result,
            )

        return AgentRunOutcome(
            status=AgentRunStatus.ABORTED,
            last_user=last_user,
            last_message=last_message,
            error="Aborted",
        )
