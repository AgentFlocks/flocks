"""Tests for disposable evolution Agent Sessions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.agent.agent_factory import load_agent
from flocks.memory.evolution.agent_runner import run_evolution_agent


@pytest.mark.asyncio
async def test_evolution_agent_uses_full_session_loop_and_deletes_session() -> None:
    session = SimpleNamespace(id="ses_evolution")
    created = AsyncMock(return_value=session)
    deleted = AsyncMock(return_value=True)
    message_create = AsyncMock()
    loop = AsyncMock(
        return_value=SimpleNamespace(
            action="stop",
            error=None,
            last_message=SimpleNamespace(
                id="msg_done",
                role="assistant",
                error=None,
                finish="stop",
            ),
            metadata={},
        )
    )
    set_main = []

    with (
        patch(
            "flocks.memory.evolution.agent_runner.Agent.get",
            new=AsyncMock(return_value=SimpleNamespace(name="self-improve")),
        ),
        patch(
            "flocks.memory.evolution.agent_runner.Session.create",
            new=created,
        ),
        patch(
            "flocks.memory.evolution.agent_runner.Session.delete",
            new=deleted,
        ),
        patch(
            "flocks.memory.evolution.agent_runner.Message.create",
            new=message_create,
        ),
        patch(
            "flocks.memory.evolution.agent_runner.SessionLoop.run",
            new=loop,
        ),
        patch(
            "flocks.session.core.session_state.get_main_session_id",
            return_value="ses_main",
        ),
        patch(
            "flocks.session.core.session_state.set_main_session",
            side_effect=set_main.append,
        ),
    ):
        async def run() -> None:
            await run_evolution_agent(
                agent_name="self-improve",
                prompt="evidence",
                project_id="default",
                directory="/workspace",
                provider_id="provider",
                model_id="model",
            )

        result = await run()
        valid_result = {
            "action": "stop",
            "error": None,
            "last_message": SimpleNamespace(
                role="assistant",
                error=None,
                finish="stop",
            ),
            "metadata": {},
        }
        for overrides, error in (
            (
                {"error": "provider failed", "last_message": None},
                "provider failed",
            ),
            (
                {"last_message": None},
                "without a final assistant message",
            ),
            (
                {"metadata": {"aborted": True}},
                "was aborted",
            ),
            (
                {
                    "last_message": SimpleNamespace(
                        role="assistant",
                        error=None,
                        finish="length",
                    ),
                },
                "finish reason: length",
            ),
        ):
            loop.return_value = SimpleNamespace(**(valid_result | overrides))
            with pytest.raises(RuntimeError, match=error):
                await run()

    assert result is None
    assert created.await_args.kwargs["category"] == "task"
    assert created.await_args.kwargs["memory_enabled"] is False
    assert created.await_args.kwargs["metadata"]["hideFromSessionManager"] is True
    assert message_create.await_args.kwargs["model"] == {
        "providerID": "provider",
        "modelID": "model",
    }
    assert "permission" not in created.await_args.kwargs
    loop.assert_awaited_with(
        session_id="ses_evolution",
        provider_id="provider",
        model_id="model",
        agent_name="self-improve",
        working_directory="/workspace",
    )
    deleted.assert_awaited_with("default", "ses_evolution")
    assert set_main[-1] == "ses_main"


def test_evolution_agents_are_hidden_and_have_expected_tools() -> None:
    agent_root = Path(__file__).parents[2] / "flocks" / "agent" / "agents"
    self_improve = load_agent(
        agent_root / "self_improve",
        native=True,
    )

    assert self_improve is not None
    assert self_improve.hidden is True
    assert self_improve.delegatable is False
    assert self_improve.tools == [
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "bash",
        "skill_load",
    ]
