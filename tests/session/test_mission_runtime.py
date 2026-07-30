"""Session integration for pure filesystem Mission State."""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.memory.state.mission import mission_path
from flocks.session.session import Session
from flocks.tool.catalog import get_always_load_tool_names
from flocks.tool.file.write import write_tool
from flocks.tool.registry import ToolContext


@pytest.mark.asyncio
async def test_generic_write_creates_mission_state(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-filesystem-state",
        directory=str(workspace),
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")
    path = mission_path(workspace, session.id)

    result = await write_tool(
        ctx,
        content="# Mission\n\n## Goal\nComplete shared work.\n",
        filePath=str(path),
    )

    assert result.success is True
    assert path.read_text(encoding="utf-8").startswith("# Mission")


def test_mission_record_tool_is_not_available() -> None:
    assert "mission_record" not in get_always_load_tool_names()
