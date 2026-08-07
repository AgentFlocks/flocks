"""Host-neutral model/tool/continuation control loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, Optional, TypeVar

from flocks.agent.runtime.contracts import (
    AgentRunOutcome,
    AgentRunState,
    AgentRunStatus,
    TurnPreparationStatus,
)
from flocks.agent.runtime.ports import RuntimeServices, StepEngine


MessageT = TypeVar("MessageT")


class AgentLoop(Generic[MessageT]):
    """Coordinate model turns without owning session policy or persistence."""

    def __init__(
        self,
        step_engine: StepEngine[MessageT],
        services: RuntimeServices[MessageT],
        *,
        abort_requested: Optional[Callable[[], bool]] = None,
    ):
        self._step_engine = step_engine
        self._services = services
        self._abort_requested = abort_requested or (lambda: False)

    async def run(
        self,
        state: AgentRunState[MessageT],
    ) -> AgentRunOutcome[MessageT]:
        """Run or resume an agent until it settles or needs host recovery."""
        last_message: Optional[MessageT] = None

        while not self._abort_requested():
            preparation = await self._services.prepare_model_turn(state)
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
                    error="Runtime services returned READY without a model-turn snapshot",
                )

            state.active_model = snapshot.active_model
            state.model_turn_index = snapshot.model_turn_index
            state.messages = list(snapshot.messages)

            step_result = await self._step_engine.run(snapshot)
            boundary = await self._services.complete_model_turn(state, step_result)
            state.messages = list(boundary.messages)
            last_message = boundary.last_message or last_message
            if boundary.queued_inputs.cursor is not None:
                state.consumed_input_cursor = boundary.queued_inputs.cursor

            if self._abort_requested():
                return AgentRunOutcome(
                    status=AgentRunStatus.ABORTED,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                )

            if boundary.queued_inputs.messages:
                self._append_new_messages(
                    state,
                    boundary.queued_inputs.messages,
                )
                continue

            failure = step_result.failure
            if failure is not None:
                status = (
                    AgentRunStatus.RETRYABLE_FAILURE
                    if failure.allow_fallback and failure.attempt_state.replay_safe
                    else AgentRunStatus.FATAL_FAILURE
                )
                return AgentRunOutcome(
                    status=status,
                    state=state,
                    last_message=last_message,
                    error=failure.message,
                    failure=failure,
                )

            if step_result.action == "continue":
                continue
            if step_result.action == "compact":
                return AgentRunOutcome(
                    status=AgentRunStatus.CONTEXT_OVERFLOW,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                )
            if step_result.action != "stop":
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=last_message,
                    error=f"Unknown step action: {step_result.action}",
                )
            if step_result.error:
                return AgentRunOutcome(
                    status=AgentRunStatus.FATAL_FAILURE,
                    state=state,
                    last_message=last_message,
                    error=step_result.error,
                )

            continuation = await self._services.resolve_continuation(
                state,
                step_result,
            )
            if continuation.should_continue:
                self._append_new_messages(state, continuation.messages)
                continue

            return AgentRunOutcome(
                status=AgentRunStatus.COMPLETED,
                state=state,
                last_message=last_message,
            )

        return AgentRunOutcome(
            status=AgentRunStatus.ABORTED,
            state=state,
            last_message=last_message,
            error="Aborted",
        )

    @staticmethod
    def _append_new_messages(
        state: AgentRunState[MessageT],
        messages: tuple[MessageT, ...],
    ) -> None:
        """Append messages not already present in the current runtime view."""
        existing_ids = {AgentLoop._message_identity(message) for message in state.messages}
        for message in messages:
            identity = AgentLoop._message_identity(message)
            if identity not in existing_ids:
                state.messages.append(message)
                existing_ids.add(identity)

    @staticmethod
    def _message_identity(message: MessageT) -> tuple[str, object]:
        """Return a stable identity for persisted or in-memory messages."""
        message_id = getattr(message, "id", None)
        if message_id is not None:
            return ("message_id", message_id)
        return ("object_id", id(message))
