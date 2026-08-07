"""Tests for the host-neutral agent control loop."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from flocks.agent.runtime import (
    AgentLoop,
    AgentRunState,
    AgentRunStatus,
    AttemptEffects,
    ContinuationDecision,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
    QueuedInputBatch,
    RuntimeModel,
    StepFailure,
    StepResult,
    TurnPreparationStatus,
)


@dataclass(frozen=True)
class Message:
    """Minimal persisted-message stand-in with stable identity."""

    id: str
    content: str


PreparationFactory = Callable[
    [AgentRunState[Message]],
    ModelTurnPreparation[Message],
]


class FakeStepEngine:
    """Return deterministic step results and record immutable inputs."""

    def __init__(self, results: list[StepResult]):
        self._results = deque(results)
        self.snapshots: list[ModelTurnSnapshot[Message]] = []

    async def run(self, snapshot: ModelTurnSnapshot[Message]) -> StepResult:
        self.snapshots.append(snapshot)
        return self._results.popleft()


class FakeRuntimeServices:
    """Expose scripted host-boundary decisions to the agent loop."""

    def __init__(
        self,
        preparations: list[ModelTurnPreparation[Message] | PreparationFactory],
        boundaries: list[ModelTurnBoundary[Message]],
        continuations: list[ContinuationDecision[Message]] | None = None,
    ):
        self._preparations = deque(preparations)
        self._boundaries = deque(boundaries)
        self._continuations = deque(continuations or [])
        self.prepared_messages: list[tuple[Message, ...]] = []
        self.events = []

    async def prepare_model_turn(
        self,
        state: AgentRunState[Message],
    ) -> ModelTurnPreparation[Message]:
        self.prepared_messages.append(tuple(state.messages))
        preparation = self._preparations.popleft()
        if callable(preparation):
            return preparation(state)
        return preparation

    async def complete_model_turn(
        self,
        state: AgentRunState[Message],
        step_result: StepResult,
    ) -> ModelTurnBoundary[Message]:
        del state, step_result
        return self._boundaries.popleft()

    async def resolve_continuation(
        self,
        state: AgentRunState[Message],
        step_result: StepResult,
    ) -> ContinuationDecision[Message]:
        del state, step_result
        if not self._continuations:
            return ContinuationDecision()
        return self._continuations.popleft()

    async def emit_event(self, event) -> None:
        self.events.append(event)


def _state(messages: list[Message] | None = None) -> AgentRunState[Message]:
    return AgentRunState(
        session_id="session-1",
        agent_name="rex",
        active_model=RuntimeModel("provider-a", "model-a"),
        messages=list(messages or [Message("user-1", "hello")]),
    )


def _ready(
    state: AgentRunState[Message],
    *,
    turn: int = 0,
) -> ModelTurnPreparation[Message]:
    messages = tuple(state.messages)
    return ModelTurnPreparation(
        status=TurnPreparationStatus.READY,
        snapshot=ModelTurnSnapshot(
            session_id=state.session_id,
            agent_name=state.agent_name,
            active_model=state.active_model,
            model_turn_index=turn,
            trace_step=turn,
            messages=messages,
            last_user=messages[-1],
        ),
    )


@pytest.mark.asyncio
async def test_loop_honors_deferred_preparation_then_completes() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "done")
    state = _state([user])
    engine = FakeStepEngine([StepResult(action="stop")])
    services = FakeRuntimeServices(
        preparations=[
            ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE),
            _ready,
        ],
        boundaries=[ModelTurnBoundary(messages=(user, assistant), last_message=assistant)],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == AgentRunStatus.COMPLETED
    assert outcome.last_message == assistant
    assert len(engine.snapshots) == 1
    assert len(services.prepared_messages) == 2


@pytest.mark.asyncio
async def test_loop_runs_another_turn_after_tool_continue() -> None:
    user = Message("user-1", "hello")
    tool_result = Message("tool-1", "tool result")
    assistant = Message("assistant-1", "done")
    state = _state([user])
    engine = FakeStepEngine(
        [StepResult(action="continue"), StepResult(action="stop")],
    )
    services = FakeRuntimeServices(
        preparations=[_ready, lambda current: _ready(current, turn=1)],
        boundaries=[
            ModelTurnBoundary(messages=(user, tool_result), last_message=tool_result),
            ModelTurnBoundary(
                messages=(user, tool_result, assistant),
                last_message=assistant,
            ),
        ],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == AgentRunStatus.COMPLETED
    assert len(engine.snapshots) == 2
    assert engine.snapshots[1].messages == (user, tool_result)


@pytest.mark.asyncio
async def test_queued_input_precedes_natural_stop() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "first answer")
    duplicate_assistant = Message("assistant-1", "reloaded answer")
    queued_user = Message("user-2", "follow up")
    final = Message("assistant-2", "second answer")
    state = _state([user])
    engine = FakeStepEngine(
        [StepResult(action="stop"), StepResult(action="stop")],
    )
    services = FakeRuntimeServices(
        preparations=[_ready, lambda current: _ready(current, turn=1)],
        boundaries=[
            ModelTurnBoundary(
                messages=(user, assistant),
                last_message=assistant,
                queued_inputs=QueuedInputBatch(
                    messages=(duplicate_assistant, queued_user),
                    cursor="input-2",
                ),
            ),
            ModelTurnBoundary(
                messages=(user, assistant, queued_user, final),
                last_message=final,
            ),
        ],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == AgentRunStatus.COMPLETED
    assert state.consumed_input_cursor == "input-2"
    assert engine.snapshots[1].messages == (user, assistant, queued_user)


@pytest.mark.asyncio
async def test_queued_input_is_not_lost_after_final_step_failure() -> None:
    user = Message("user-1", "hello")
    failed = Message("assistant-1", "provider failed")
    queued_user = Message("user-2", "try this instead")
    final = Message("assistant-2", "done")
    state = _state([user])
    failure = StepFailure(
        message="provider failed",
        error_data={},
        assistant_message_id=failed.id,
        reason="provider_error",
        allow_fallback=False,
        attempt_state=AttemptEffects(observable_output_started=True),
    )
    engine = FakeStepEngine(
        [
            StepResult(action="stop", error=failure.message, failure=failure),
            StepResult(action="stop"),
        ]
    )
    services = FakeRuntimeServices(
        preparations=[_ready, lambda current: _ready(current, turn=1)],
        boundaries=[
            ModelTurnBoundary(
                messages=(user, failed, queued_user),
                last_message=failed,
                queued_inputs=QueuedInputBatch(
                    messages=(queued_user,),
                    cursor=queued_user.id,
                ),
            ),
            ModelTurnBoundary(
                messages=(user, failed, queued_user, final),
                last_message=final,
            ),
        ],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == AgentRunStatus.COMPLETED
    assert len(engine.snapshots) == 2
    assert engine.snapshots[1].messages == (user, failed, queued_user)


@pytest.mark.asyncio
async def test_host_continuation_starts_another_turn() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "working")
    continuation = Message("user-2", "continue goal")
    final = Message("assistant-2", "done")
    state = _state([user])
    engine = FakeStepEngine(
        [StepResult(action="stop"), StepResult(action="stop")],
    )
    services = FakeRuntimeServices(
        preparations=[_ready, lambda current: _ready(current, turn=1)],
        boundaries=[
            ModelTurnBoundary(messages=(user, assistant), last_message=assistant),
            ModelTurnBoundary(
                messages=(user, assistant, continuation, final),
                last_message=final,
            ),
        ],
        continuations=[
            ContinuationDecision(messages=(continuation,), reason="goal"),
            ContinuationDecision(),
        ],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == AgentRunStatus.COMPLETED
    assert engine.snapshots[1].messages == (user, assistant, continuation)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("effects", "expected_status"),
    [
        (AttemptEffects(received_chunk=True), AgentRunStatus.RETRYABLE_FAILURE),
        (
            AttemptEffects(tool_execution_started=True),
            AgentRunStatus.FATAL_FAILURE,
        ),
    ],
)
async def test_failure_is_retryable_only_before_observable_effects(
    effects: AttemptEffects,
    expected_status: AgentRunStatus,
) -> None:
    user = Message("user-1", "hello")
    state = _state([user])
    failure = StepFailure(
        message="provider failed",
        error_data={},
        assistant_message_id=None,
        reason="provider_error",
        allow_fallback=True,
        attempt_state=effects,
    )
    engine = FakeStepEngine(
        [StepResult(action="stop", error=failure.message, failure=failure)],
    )
    services = FakeRuntimeServices(
        preparations=[_ready],
        boundaries=[ModelTurnBoundary(messages=(user,))],
    )

    outcome = await AgentLoop(engine, services).run(state)

    assert outcome.status == expected_status
    assert outcome.failure is failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (StepResult(action="compact", error="context overflow"), AgentRunStatus.CONTEXT_OVERFLOW),
        (StepResult(action="unexpected"), AgentRunStatus.FATAL_FAILURE),
    ],
)
async def test_loop_returns_structured_non_success_outcomes(
    result: StepResult,
    expected_status: AgentRunStatus,
) -> None:
    user = Message("user-1", "hello")
    engine = FakeStepEngine([result])
    services = FakeRuntimeServices(
        preparations=[_ready],
        boundaries=[ModelTurnBoundary(messages=(user,))],
    )

    outcome = await AgentLoop(engine, services).run(_state([user]))

    assert outcome.status == expected_status


@pytest.mark.asyncio
async def test_loop_aborts_after_current_step_boundary() -> None:
    user = Message("user-1", "hello")
    checks = iter([False, True])
    engine = FakeStepEngine([StepResult(action="stop")])
    services = FakeRuntimeServices(
        preparations=[_ready],
        boundaries=[ModelTurnBoundary(messages=(user,))],
    )

    outcome = await AgentLoop(
        engine,
        services,
        abort_requested=lambda: next(checks),
    ).run(_state([user]))

    assert outcome.status == AgentRunStatus.ABORTED


@pytest.mark.asyncio
async def test_ready_preparation_requires_snapshot() -> None:
    services = FakeRuntimeServices(
        preparations=[ModelTurnPreparation(status=TurnPreparationStatus.READY)],
        boundaries=[],
    )

    outcome = await AgentLoop(FakeStepEngine([]), services).run(_state())

    assert outcome.status == AgentRunStatus.FATAL_FAILURE
    assert outcome.error == "Runtime services returned READY without a model-turn snapshot"
