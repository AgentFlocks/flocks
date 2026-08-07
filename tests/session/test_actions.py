"""Tests for non-agent session actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.command.command import Command
from flocks.session import actions


@pytest.mark.asyncio
async def test_render_session_command_resolves_template(monkeypatch) -> None:
    monkeypatch.setattr(
        Command,
        "get",
        lambda _name: SimpleNamespace(template="Do $ARGUMENTS"),
    )

    result = await actions.render_session_command(
        "session-1",
        "test",
        "the work",
    )

    assert result["template"] == "Do the work"


@pytest.mark.asyncio
async def test_run_session_shell_preserves_legacy_response(monkeypatch) -> None:
    monkeypatch.setattr(
        actions.Session,
        "get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(directory="/tmp/project"),
        ),
    )
    messages = [
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="assistant-1"),
    ]
    monkeypatch.setattr(
        actions.Message,
        "create",
        AsyncMock(side_effect=messages),
    )
    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"done", b"")),
        returncode=0,
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(
        actions.asyncio,
        "create_subprocess_shell",
        create_process,
    )

    result = await actions.run_session_shell(
        "session-1",
        "rex",
        "echo done",
    )

    create_process.assert_awaited_once_with(
        "echo done",
        stdout=actions.asyncio.subprocess.PIPE,
        stderr=actions.asyncio.subprocess.PIPE,
        cwd="/tmp/project",
    )
    assert result["info"]["id"] == "assistant-1"
    assert result["parts"][0]["state"]["output"] == "done"
