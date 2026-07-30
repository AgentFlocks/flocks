"""Session integration for shared filesystem Missions."""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.memory.state.mission import MissionStore
from flocks.session.features.todo import TodoInfo
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.tool.registry import ToolContext
from flocks.tool.file.write import write_tool
from flocks.tool.system.mission_record import mission_record_tool
from flocks.tool.task.todo import _sync_mission


@pytest.mark.asyncio
async def test_complex_todo_creates_session_mission(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-test",
        directory=str(workspace),
    )
    await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="Implement a durable feature.",
        agent="rex",
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")

    result = await _sync_mission(
        ctx,
        [
            TodoInfo(id="inspect", content="Inspect current code", status="in_progress"),
            TodoInfo(id="build", content="Implement feature", status="pending"),
            TodoInfo(id="verify", content="Verify with tests", status="pending"),
        ],
    )

    refreshed = await Session.get_by_id(session.id)
    assert refreshed is not None
    assert refreshed.mission_id
    assert result and result["missionID"] == refreshed.mission_id
    state = MissionStore(workspace).load(refreshed.mission_id)
    assert state["original_request"] == "Implement a durable feature."


@pytest.mark.asyncio
async def test_simple_todo_does_not_create_mission(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-simple",
        directory=str(workspace),
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")

    result = await _sync_mission(
        ctx,
        [
            TodoInfo(id="one", content="Do one thing", status="pending"),
            TodoInfo(id="two", content="Verify it", status="pending"),
        ],
    )

    refreshed = await Session.get_by_id(session.id)
    assert refreshed is not None
    assert refreshed.mission_id is None
    assert result is None


@pytest.mark.asyncio
async def test_mission_record_rejects_unbound_session(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-unbound",
        directory=str(workspace),
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")

    result = await mission_record_tool(
        ctx,
        kind="progress",
        summary="No Mission exists",
    )

    assert result.success is False
    assert "mission_id is required" in (result.error or "")


@pytest.mark.asyncio
async def test_generic_write_cannot_overwrite_mission_state(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-protected",
        directory=str(workspace),
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")
    await _sync_mission(
        ctx,
        [
            TodoInfo(id="one", content="First task", status="pending"),
            TodoInfo(id="two", content="Second task", status="pending"),
            TodoInfo(id="verify", content="Verify result", status="pending"),
        ],
    )
    refreshed = await Session.get_by_id(session.id)
    assert refreshed and refreshed.mission_id
    mission_path = MissionStore(workspace).mission_path(refreshed.mission_id)

    result = await write_tool(
        ctx,
        content="forged completed state",
        filePath=str(mission_path),
    )

    assert result.success is False
    assert "protected" in (result.error or "").lower()
    assert "forged" not in mission_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_unbound_agent_records_to_explicit_shared_mission(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = MissionStore(workspace)
    store.create(
        mission_id="shared-mission",
        session_id="root-session",
        original_request="Complete shared work.",
        todos=[
            {"id": "one", "content": "First task", "status": "pending"},
            {"id": "two", "content": "Second task", "status": "pending"},
            {"id": "verify", "content": "Verify result", "status": "pending"},
        ],
    )
    session = await Session.create(
        project_id="project-worker",
        directory=str(workspace),
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")
    mission_before = store.mission_path("shared-mission").read_bytes()

    result = await mission_record_tool(
        ctx,
        mission_id="shared-mission",
        kind="progress",
        summary="Worker completed reconnaissance",
    )

    assert result.success is True
    progress = store._read_progress_entries("shared-mission")
    assert progress[-1]["session_id"] == session.id
    assert progress[-1]["summary"] == "Worker completed reconnaissance"
    assert store.mission_path("shared-mission").read_bytes() == mission_before

    checkpoint = await mission_record_tool(
        ctx,
        mission_id="shared-mission",
        kind="checkpoint",
        summary="Replace the main Agent state",
    )
    assert checkpoint.success is False
    assert "Only the main Agent" in (checkpoint.error or "")
    assert store.mission_path("shared-mission").read_bytes() == mission_before


def test_mission_record_is_always_available_for_delegated_agents() -> None:
    from flocks.tool.catalog import get_always_load_tool_names

    assert "mission_record" in get_always_load_tool_names()
