"""Tests for the explicit Dream self-improvement command."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.command.command import Command
from flocks.command.direct import run_direct_command
from flocks.memory.evolution.common import DreamTarget


def test_evolution_commands_are_registered_as_direct_commands() -> None:
    dream = Command.get("dream")

    assert dream is not None
    assert dream.execution_kind == "direct"
    assert dream.requires_existing_session is True
    assert Command.get("learn") is None


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
            backlog=False,
            memory_changed=True,
            skill_changed=True,
            changed_memory_files=(
                "global/USER.md",
                "project/MEMORY.md",
            ),
            changed_skills=("release-check",),
        )
    )
    statuses = []

    async def publish_status(status: str, message: str | None) -> None:
        statuses.append((status, message))

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
            status_callback=publish_status,
        )

    assert result.success is True
    assert result.text == (
        "Dream completed\n\n"
        "- Target: Project prj_test\n"
        "- Evidence processed: 2\n"
        "- Memory: Updated global/USER.md, project/MEMORY.md\n"
        "- Skill: Updated release-check"
    )
    assert statuses[0][0] == "dreaming"
    assert "Project prj_test" in statuses[0][1]
    assert statuses[-1] == ("idle", None)
    bridge.assert_awaited_once_with(
        DreamTarget.project("prj_test"),
        parent_session_id="ses_test",
    )


@pytest.mark.asyncio
async def test_dream_command_clears_foreground_status_after_failure() -> None:
    session = SimpleNamespace(id="ses_test", project_id="default")
    statuses = []

    async def publish_status(status: str, message: str | None) -> None:
        statuses.append((status, message))

    with (
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.memory.evolution.dream.run_dream_bridge",
            new=AsyncMock(side_effect=RuntimeError("model unavailable")),
        ),
    ):
        result = await run_direct_command(
            "dream",
            session_id=session.id,
            status_callback=publish_status,
        )

    assert result.success is False
    assert result.text == "Dream failed: model unavailable"
    assert statuses[0][0] == "dreaming"
    assert statuses[-1] == ("idle", None)
