"""The control loop for one logical user input."""

from __future__ import annotations

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
        state = turn.state
        last_message = None

        while not turn.aborted:
            preparation = await turn.prepare_step()
            if preparation.status == TurnPreparationStatus.CONTINUE:
                continue
            if preparation.status == TurnPreparationStatus.COMPLETE:
                return AgentRunOutcome(
                    status=AgentRunStatus.COMPLETED,
                    state=state,
                    last_message=preparation.last_message or last_message,
                )
            if preparation.status == TurnPreparationStatus.FATAL:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=preparation.last_message or last_message,
                    error=preparation.error,
                )

            snapshot = preparation.snapshot
            if snapshot is None:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=last_message,
                    error=(
                        "LoopContext returned READY without a model-turn "
                        "snapshot"
                    ),
                )

            state.active_model = snapshot.active_model
            state.model_turn_index = snapshot.model_turn_index
            state.messages = list(snapshot.messages)

            try:
                step_result = await engine.run(snapshot)
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
                    state=state,
                    last_message=last_message,
                    error="Aborted",
                )

            state.active_model = (
                step_result.effective_model or snapshot.active_model
            )
            boundary = await turn.commit_step(step_result)
            state.messages = list(boundary.messages)
            last_message = boundary.last_message or last_message

            if turn.aborted:
                return AgentRunOutcome(
                    status=AgentRunStatus.ABORTED,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                )

            if boundary.queued_inputs.messages:
                return AgentRunOutcome(
                    status=AgentRunStatus.INPUT_AVAILABLE,
                    state=state,
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
                    state=state,
                    last_message=last_message,
                    error=failure.message,
                    step_result=step_result,
                )

            if step_result.action == StepAction.CONTINUE:
                continue
            if step_result.action == StepAction.COMPACT:
                return AgentRunOutcome(
                    status=AgentRunStatus.CONTEXT_OVERFLOW,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                    step_result=step_result,
                )
            if step_result.action != StepAction.STOP:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=last_message,
                    error=f"Unknown step action: {step_result.action}",
                    step_result=step_result,
                )
            if step_result.error:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                    step_result=step_result,
                )

            return AgentRunOutcome(
                status=AgentRunStatus.COMPLETED,
                state=state,
                last_message=last_message,
                step_result=step_result,
            )

        return AgentRunOutcome(
            status=AgentRunStatus.ABORTED,
            state=state,
            last_message=last_message,
            error="Aborted",
        )
