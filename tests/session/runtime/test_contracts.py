"""Tests for session-neutral agent runtime contracts."""

import dataclasses

import pytest

from flocks.session.runtime.contracts import (
    AttemptEffects,
    ModelRequest,
    ModelTurnSnapshot,
    RuntimeModel,
    StepAction,
    StepResult,
)


def test_attempt_effects_allow_replay_only_before_observable_effects() -> None:
    effects = AttemptEffects(received_chunk=True)

    assert effects.replay_safe is True

    effects.observable_output_started = True
    assert effects.replay_safe is False

    effects.observable_output_started = False
    effects.tool_execution_started = True
    assert effects.replay_safe is False


def test_model_turn_snapshot_defensively_freezes_collections() -> None:
    messages = ["user"]
    metadata = {"tool_revision": 1}
    snapshot = ModelTurnSnapshot(
        session_id="session-1",
        agent_name="rex",
        active_model=RuntimeModel("provider", "model"),
        model_turn_index=2,
        trace_step=5,
        messages=tuple(messages),
        last_user="user",
        metadata=metadata,
    )

    messages.append("new input")
    metadata["tool_revision"] = 2

    assert snapshot.messages == ("user",)
    assert snapshot.metadata == {"tool_revision": 1}


def test_model_request_freezes_and_isolates_provider_payloads() -> None:
    message = {"role": "user", "content": ["hello"]}
    tool = {"type": "function", "function": {"name": "read"}}
    options = {"reasoning": {"effort": "high"}}
    request = ModelRequest(
        provider_id="provider",
        model_id="model",
        messages=(message,),
        tools=(tool,),
        options=options,
    )

    tool["function"]["name"] = "write"
    options["reasoning"]["effort"] = "low"
    first_tools = request.provider_tools()
    first_tools[0]["function"]["name"] = "mutated"

    assert request.provider_tools()[0]["function"]["name"] == "read"
    assert request.provider_options()["reasoning"]["effort"] == "high"


def test_model_request_identity_is_frozen() -> None:
    request = ModelRequest(
        provider_id="provider",
        model_id="model",
        messages=(),
        tools=(),
        options={},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.model_id = "other"


def test_step_actions_are_explicit_but_unknown_adapter_values_remain_reportable() -> None:
    assert StepResult(action=StepAction.CONTINUE).action == "continue"
    assert StepResult(action="unexpected").action == "unexpected"
