"""Tests for host-neutral agent runtime contracts."""

from flocks.agent.runtime.contracts import (
    AttemptEffects,
    ModelTurnSnapshot,
    RuntimeModel,
)


def test_attempt_effects_allow_replay_only_before_observable_effects() -> None:
    effects = AttemptEffects(received_chunk=True)

    assert effects.replay_safe is True

    effects.observable_output_started = True
    assert effects.replay_safe is False

    effects.observable_output_started = False
    effects.tool_execution_started = True
    assert effects.replay_safe is False

    effects.tool_execution_started = False
    effects.durable_side_effect_possible = True
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
