"""Tests for explicit Dream and Skill evolution commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.command.command import Command
from flocks.command.direct import run_direct_command
from flocks.memory.evolution.common import DreamTarget


def test_evolution_commands_are_registered_as_direct_commands() -> None:
    dream = Command.get("dream")
    learn = Command.get("learn")

    assert dream is not None
    assert dream.execution_kind == "direct"
    assert dream.requires_existing_session is True
    assert learn is not None
    assert learn.execution_kind == "direct"
    assert learn.requires_existing_session is True


@pytest.mark.asyncio
async def test_dream_command_runs_current_project_agent() -> None:
    session = SimpleNamespace(
        id="ses_test",
        project_id="prj_test",
    )
    bridge = AsyncMock(
        return_value=SimpleNamespace(
            changed=True,
            processed_sources=2,
        )
    )

    with (
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.memory.evolution.dream.run_dream_bridge",
            new=bridge,
        ),
    ):
        result = await run_direct_command(
            "dream",
            session_id=session.id,
        )

    assert result.success is True
    bridge.assert_awaited_once_with(
        DreamTarget.project("prj_test"),
        parent_session_id="ses_test",
    )


@pytest.mark.asyncio
async def test_learn_command_runs_manual_skill_agent() -> None:
    learn = AsyncMock(return_value=False)

    with patch(
        "flocks.memory.evolution.skill.run_manual_skill_evolution",
        new=learn,
    ):
        result = await run_direct_command(
            "learn",
            session_id="ses_test",
        )

    assert result.success is True
    assert result.text == "Skill learning completed: no Skill changes."
    learn.assert_awaited_once_with("ses_test")
