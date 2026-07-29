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
            last_message=SimpleNamespace(id="msg_done"),
        )
    )
    set_main = []

    with (
        patch(
            "flocks.memory.evolution.agent_runner.Agent.get",
            new=AsyncMock(return_value=SimpleNamespace(name="dream")),
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
            "flocks.memory.evolution.agent_runner.Message.get_text_content",
            new=AsyncMock(return_value="done"),
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
        result = await run_evolution_agent(
            agent_name="dream",
            prompt="evidence",
            project_id="default",
            directory="/workspace",
            provider_id="provider",
            model_id="model",
        )

    assert result.summary == "done"
    assert created.await_args.kwargs["category"] == "task"
    assert (
        created.await_args.kwargs["metadata"]["hideFromSessionManager"]
        is True
    )
    assert message_create.await_args.kwargs["model"] == {
        "providerID": "provider",
        "modelID": "model",
    }
    loop.assert_awaited_once_with(
        session_id="ses_evolution",
        provider_id="provider",
        model_id="model",
        agent_name="dream",
        working_directory="/workspace",
    )
    deleted.assert_awaited_once_with("default", "ses_evolution")
    assert set_main == ["ses_main", "ses_main"]


def test_evolution_agents_are_hidden_and_have_expected_tools() -> None:
    agent_root = Path(__file__).parents[2] / "flocks" / "agent" / "agents"
    dream = load_agent(agent_root / "dream", native=True)
    learn = load_agent(agent_root / "learn", native=True)

    assert dream is not None
    assert dream.hidden is True
    assert dream.delegatable is False
    assert dream.tools == [
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "bash",
    ]
    assert learn is not None
    assert learn.hidden is True
    assert learn.delegatable is False
    assert "skill_load" in learn.tools
