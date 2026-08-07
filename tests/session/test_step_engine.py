"""Tests for the existing session runner's StepEngine adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.agent.runtime.contracts import (
    AttemptEffects,
    ModelTurnSnapshot,
    RuntimeModel,
    StepResult,
)
from flocks.session.runner import (
    LlmAttemptState,
    StepResult as LegacyStepResult,
)
from flocks.session.step_engine import SessionStepEngine


@pytest.mark.asyncio
async def test_session_step_engine_delegates_immutable_snapshot() -> None:
    last_user = SimpleNamespace(id="user-1")
    expected = StepResult(action="stop", content="done")
    runner = SimpleNamespace(
        _process_step=AsyncMock(return_value=expected),
        _session_start_fired=True,
        _attempt_state=AttemptEffects(received_chunk=True),
        _step=0,
    )
    engine = SessionStepEngine(runner)
    snapshot = ModelTurnSnapshot(
        session_id="session-1",
        agent_name="rex",
        active_model=RuntimeModel("provider", "model"),
        model_turn_index=1,
        trace_step=8,
        messages=(last_user,),
        last_user=last_user,
    )

    result = await engine.run(snapshot)

    assert result is expected
    assert runner._step == 8
    runner._process_step.assert_awaited_once_with([last_user], last_user)
    assert engine.session_start_fired is True
    assert engine.attempt_effects.received_chunk is True


def test_legacy_runner_contract_exports_remain_compatible() -> None:
    assert LegacyStepResult is StepResult
    assert LlmAttemptState is AttemptEffects
