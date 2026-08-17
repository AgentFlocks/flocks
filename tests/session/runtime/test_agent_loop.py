"""Tests for the logical-input AgentLoop."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pytest

from flocks.session.runtime.agent_loop import AgentLoop
from flocks.session.runtime.contracts import (
    AgentRunStatus,
    AttemptEffects,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
    RuntimeModel,
    StepFailure,
    StepResult,
    TurnPreparationStatus,
)


@dataclass(frozen=True)
class Message:
    id: str
    content: str


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
        preparations: list[ModelTurnPreparation[Message]],
        boundaries: list[ModelTurnBoundary[Message]],
    ) -> None:
        self._preparations = deque(preparations)
        self._boundaries = deque(boundaries)
        self.aborted = False
        self.step = 0

    async def prepare_step(self) -> ModelTurnPreparation[Message]:
        return self._preparations.popleft()

    async def commit_step(
        self,
        _step_result: StepResult,
    ) -> ModelTurnBoundary[Message]:
        return self._boundaries.popleft()


def _ready(
    messages: tuple[Message, ...],
    *,
    turn: int = 0,
) -> ModelTurnPreparation[Message]:
    return ModelTurnPreparation(
        status=TurnPreparationStatus.READY,
        snapshot=ModelTurnSnapshot(
            active_model=RuntimeModel("provider-a", "model-a"),
            trace_step=turn,
            messages=messages,
            last_user=messages[-1],
        ),
    )


async def _run(
    engine: FakeStepEngine,
    preparations,
    boundaries,
):
    turn = FakeTurn(preparations, boundaries)
    return await AgentLoop().run(turn, engine)


@pytest.mark.asyncio
async def test_loop_runs_another_step_after_tool_continue() -> None:
    user = Message("user-1", "hello")
    tool_result = Message("tool-1", "tool result")
    assistant = Message("assistant-1", "done")
    engine = FakeStepEngine(
        [StepResult(action="continue"), StepResult(action="stop")],
    )
    outcome = await _run(
        engine,
        [_ready((user,)), _ready((user, tool_result), turn=1)],
        [
            ModelTurnBoundary(last_message=tool_result),
            ModelTurnBoundary(last_message=assistant),
        ],
    )
    assert outcome.status == AgentRunStatus.COMPLETED
    assert outcome.last_message == assistant
    assert len(engine.snapshots) == 2
    assert engine.snapshots[1].messages == (user, tool_result)


@pytest.mark.asyncio
async def test_queued_input_precedes_final_step_failure() -> None:
    user = Message("user-1", "hello")
    failed = Message("assistant-1", "provider failed")
    failure = StepFailure(
        message="provider failed",
        error_data={},
        assistant_message_id=failed.id,
        reason="provider_error",
        allow_fallback=False,
        attempt_state=AttemptEffects(observable_output_started=True),
    )
    outcome = await _run(
        FakeStepEngine(
            [StepResult(action="stop", error=failure.message, failure=failure)],
        ),
        [_ready((user,))],
        [
            ModelTurnBoundary(
                last_message=failed,
                input_available=True,
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
    outcome = await _run(
        FakeStepEngine(
            [StepResult(action="stop", error=failure.message, failure=failure)],
        ),
        [_ready((user,))],
        [ModelTurnBoundary()],
    )
    assert outcome.status == expected_status
