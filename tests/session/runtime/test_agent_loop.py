"""Tests for the logical-input AgentLoop."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from flocks.session.runtime.agent_loop import AgentLoop
from flocks.session.runtime.contracts import (
    AgentRunState,
    AgentRunStatus,
    AttemptEffects,
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
    id: str
    content: str


PreparationFactory = Callable[
    [AgentRunState[Message]],
    ModelTurnPreparation[Message],
]


class FakeStepEngine:
    def __init__(self, results: list[StepResult]):
        self._results = deque(results)
        self.snapshots: list[ModelTurnSnapshot[Message]] = []

    async def run(self, snapshot: ModelTurnSnapshot[Message]) -> StepResult:
        self.snapshots.append(snapshot)
        return self._results.popleft()


class FakeTurn:
    """Script the two boundaries AgentLoop is allowed to call."""

    def __init__(
        self,
        state: AgentRunState[Message],
        preparations: list[ModelTurnPreparation[Message] | PreparationFactory],
        boundaries: list[ModelTurnBoundary[Message]],
        *,
        abort_after_commit: bool = False,
    ) -> None:
        self.state = state
        self._preparations = deque(preparations)
        self._boundaries = deque(boundaries)
        self.prepared_messages: list[tuple[Message, ...]] = []
        self.aborted = False
        self._abort_after_commit = abort_after_commit
        self.session = SimpleNamespace(id=state.session_id)
        self.step = 0

    async def prepare_step(self) -> ModelTurnPreparation[Message]:
        self.prepared_messages.append(tuple(self.state.messages))
        preparation = self._preparations.popleft()
        if callable(preparation):
            return preparation(self.state)
        return preparation

    async def commit_step(
        self,
        _step_result: StepResult,
    ) -> ModelTurnBoundary[Message]:
        boundary = self._boundaries.popleft()
        if self._abort_after_commit:
            self.aborted = True
        return boundary


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


async def _run(
    state: AgentRunState[Message],
    engine: FakeStepEngine,
    preparations,
    boundaries,
    *,
    abort_after_commit: bool = False,
):
    turn = FakeTurn(
        state,
        preparations,
        boundaries,
        abort_after_commit=abort_after_commit,
    )
    outcome = await AgentLoop().run(turn, engine)
    return outcome, turn


@pytest.mark.asyncio
async def test_loop_honors_deferred_preparation_then_completes() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "done")
    engine = FakeStepEngine([StepResult(action="stop")])
    outcome, turn = await _run(
        _state([user]),
        engine,
        [ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE), _ready],
        [ModelTurnBoundary(messages=(user, assistant), last_message=assistant)],
    )

    assert outcome.status == AgentRunStatus.COMPLETED
    assert outcome.last_message == assistant
    assert len(engine.snapshots) == 1
    assert len(turn.prepared_messages) == 2


@pytest.mark.asyncio
async def test_loop_records_model_actually_used_by_step_engine() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "done")
    fallback = RuntimeModel("provider-b", "model-b")
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine([StepResult(action="stop", effective_model=fallback)]),
        [_ready],
        [ModelTurnBoundary(messages=(user, assistant), last_message=assistant)],
    )
    assert outcome.state.active_model == fallback


@pytest.mark.asyncio
async def test_loop_runs_another_step_after_tool_continue() -> None:
    user = Message("user-1", "hello")
    tool_result = Message("tool-1", "tool result")
    assistant = Message("assistant-1", "done")
    engine = FakeStepEngine(
        [StepResult(action="continue"), StepResult(action="stop")],
    )
    outcome, _ = await _run(
        _state([user]),
        engine,
        [_ready, lambda current: _ready(current, turn=1)],
        [
            ModelTurnBoundary(messages=(user, tool_result), last_message=tool_result),
            ModelTurnBoundary(
                messages=(user, tool_result, assistant),
                last_message=assistant,
            ),
        ],
    )
    assert outcome.status == AgentRunStatus.COMPLETED
    assert len(engine.snapshots) == 2
    assert engine.snapshots[1].messages == (user, tool_result)


@pytest.mark.asyncio
async def test_queued_input_yields_to_session_loop() -> None:
    user = Message("user-1", "hello")
    assistant = Message("assistant-1", "first answer")
    queued_user = Message("user-2", "follow up")
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine([StepResult(action="stop")]),
        [_ready],
        [
            ModelTurnBoundary(
                messages=(user, assistant),
                last_message=assistant,
                queued_inputs=QueuedInputBatch(messages=(queued_user,)),
            ),
        ],
    )
    assert outcome.status == AgentRunStatus.INPUT_AVAILABLE


@pytest.mark.asyncio
async def test_queued_input_precedes_final_step_failure() -> None:
    user = Message("user-1", "hello")
    failed = Message("assistant-1", "provider failed")
    queued_user = Message("user-2", "try this instead")
    failure = StepFailure(
        message="provider failed",
        error_data={},
        assistant_message_id=failed.id,
        reason="provider_error",
        allow_fallback=False,
        attempt_state=AttemptEffects(observable_output_started=True),
    )
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine(
            [StepResult(action="stop", error=failure.message, failure=failure)],
        ),
        [_ready],
        [
            ModelTurnBoundary(
                messages=(user, failed, queued_user),
                last_message=failed,
                queued_inputs=QueuedInputBatch(messages=(queued_user,)),
            ),
        ],
    )
    assert outcome.status == AgentRunStatus.INPUT_AVAILABLE
    assert outcome.step_result.failure is failure


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
    failure = StepFailure(
        message="provider failed",
        error_data={},
        assistant_message_id=None,
        reason="provider_error",
        allow_fallback=True,
        attempt_state=effects,
    )
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine(
            [StepResult(action="stop", error=failure.message, failure=failure)],
        ),
        [_ready],
        [ModelTurnBoundary(messages=(user,))],
    )
    assert outcome.status == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            StepResult(action="compact", error="context overflow"),
            AgentRunStatus.CONTEXT_OVERFLOW,
        ),
        (StepResult(action="unexpected"), AgentRunStatus.FATAL_FAILURE),
    ],
)
async def test_loop_returns_structured_non_success_outcomes(
    result: StepResult,
    expected_status: AgentRunStatus,
) -> None:
    user = Message("user-1", "hello")
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine([result]),
        [_ready],
        [ModelTurnBoundary(messages=(user,))],
    )
    assert outcome.status == expected_status


@pytest.mark.asyncio
async def test_loop_aborts_after_current_step_boundary() -> None:
    user = Message("user-1", "hello")
    outcome, _ = await _run(
        _state([user]),
        FakeStepEngine([StepResult(action="stop")]),
        [_ready],
        [ModelTurnBoundary(messages=(user,))],
        abort_after_commit=True,
    )
    assert outcome.status == AgentRunStatus.ABORTED


@pytest.mark.asyncio
async def test_ready_preparation_requires_snapshot() -> None:
    outcome, _ = await _run(
        _state(),
        FakeStepEngine([]),
        [ModelTurnPreparation(status=TurnPreparationStatus.READY)],
        [],
    )
    assert outcome.status == AgentRunStatus.FATAL_FAILURE
    assert outcome.error == "SessionTurn returned READY without a model-turn snapshot"
