"""Tests for the concrete session StepEngine."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.runtime.step_engine import (
    LlmAttemptState,
    StepResult as LegacyStepResult,
)
from flocks.session.runtime.contracts import (
    AttemptEffects,
    ModelTurnSnapshot,
    RuntimeModel,
    StepResult,
)
from flocks.session.runtime.session_turn import LoopCallbacks, LoopContext
from flocks.session.runtime.step_engine import StepCancelled, StepEngine
from flocks.session.session import SessionInfo


def _turn(*, aborted: bool = False) -> LoopContext:
    abort_event = asyncio.Event()
    if aborted:
        abort_event.set()
    return LoopContext(
        session=SessionInfo.model_construct(
            id="session-1",
            projectID="project",
            directory="/tmp/project",
            agent="rex",
            status="active",
        ),
        provider_id="provider",
        model_id="model",
        agent_name="rex",
        callbacks=LoopCallbacks(event_publish_callback=None),
        abort_event=abort_event,
        model_candidates=[RuntimeModel("provider", "model")],
        session_start_pending=True,
    )


def _snapshot(last_user) -> ModelTurnSnapshot:
    return ModelTurnSnapshot(
        session_id="session-1",
        agent_name="rex",
        active_model=RuntimeModel("provider", "model"),
        model_turn_index=1,
        trace_step=8,
        messages=(last_user,),
        last_user=last_user,
    )


@pytest.mark.asyncio
async def test_step_engine_executes_one_immutable_snapshot() -> None:
    last_user = SimpleNamespace(id="user-1")
    expected = StepResult(action="stop", content="done")
    turn = _turn()
    engine = StepEngine.from_turn(turn)

    async def execute(messages, user):
        engine._session_start_fired = True
        return expected

    process_step = AsyncMock(side_effect=execute)

    with patch.object(StepEngine, "_process_step", process_step):
        result = await engine.run(_snapshot(last_user))

    assert result is expected
    assert engine._step == 8
    process_step.assert_awaited_once_with([last_user], last_user)
    assert turn.session_start_pending is False
    assert turn._current_step_task is None


@pytest.mark.asyncio
async def test_step_engine_refreshes_memory_loaded_after_construction() -> None:
    last_user = SimpleNamespace(id="user-1")
    turn = _turn()
    engine = StepEngine.from_turn(turn)
    loaded_memory = {
        "instructions": "remember this",
        "main_memory": {"content": "project context", "inject": True},
    }
    turn.memory_bootstrap_data = loaded_memory

    async def execute(_messages, _user):
        assert engine._memory_bootstrap_data is loaded_memory
        return StepResult(action="stop", content="done")

    with patch.object(StepEngine, "_process_step", side_effect=execute):
        await engine.run(_snapshot(last_user))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("aborted", "expected_error"),
    [
        (False, asyncio.CancelledError),
        (True, StepCancelled),
    ],
)
async def test_step_engine_only_translates_user_abort(
    aborted,
    expected_error,
) -> None:
    last_user = SimpleNamespace(id="user-1")
    turn = _turn(aborted=aborted)
    engine = StepEngine.from_turn(turn)

    with (
        patch.object(
            StepEngine,
            "_process_step",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(expected_error),
    ):
        await engine.run(_snapshot(last_user))

    assert turn._current_step_task is None


def test_legacy_runner_contract_exports_remain_compatible() -> None:
    assert LegacyStepResult is StepResult
    assert LlmAttemptState is AttemptEffects
