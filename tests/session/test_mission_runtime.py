"""Session integration for Memory-State Missions."""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.memory.mission import MissionStore
from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.session.callable_state import (
    get_session_callable_tools,
    set_session_callable_tools,
)
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
        memory_enabled=True,
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
    assert "mission_record" in await get_session_callable_tools(session.id)
    state = MissionStore(workspace).load(refreshed.mission_id)
    assert state["original_request"] == "Implement a durable feature."


@pytest.mark.asyncio
async def test_simple_todo_does_not_create_mission(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-simple",
        directory=str(workspace),
        memory_enabled=True,
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
    assert "mission_record" not in await get_session_callable_tools(session.id)


@pytest.mark.asyncio
async def test_mission_record_rejects_unbound_session(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-unbound",
        directory=str(workspace),
        memory_enabled=True,
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")

    result = await mission_record_tool(
        ctx,
        kind="progress",
        summary="No Mission exists",
    )

    assert result.success is False
    assert "No Mission" in (result.error or "")


@pytest.mark.asyncio
async def test_generic_write_cannot_overwrite_mission_state(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-protected",
        directory=str(workspace),
        memory_enabled=True,
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
async def test_write_cannot_replace_existing_project_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    memory_dir = workspace / ".flocks" / "memory"
    memory_dir.mkdir(parents=True)
    memory_path = memory_dir / "MEMORY.md"
    memory_path.write_text("stable fact", encoding="utf-8")
    session = await Session.create(
        project_id="project-memory-write",
        directory=str(workspace),
        memory_enabled=True,
    )
    ctx = ToolContext(session_id=session.id, message_id="message-test")

    result = await write_tool(
        ctx,
        content="replacement",
        filePath=str(memory_path),
    )

    assert result.success is False
    assert "use edit" in (result.error or "").lower()
    assert memory_path.read_text(encoding="utf-8") == "stable fact"


@pytest.mark.asyncio
async def test_restored_session_recovers_conditional_mission_tool(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = await Session.create(
        project_id="project-restored",
        directory=str(workspace),
        memory_enabled=True,
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
    await set_session_callable_tools(session.id, {"read"})

    result = await list_session_callable_tool_infos(
        session.id,
        declared_tool_names=["read"],
    )

    assert "mission_record" in {tool.name for tool in result.tool_infos}
    assert "mission_record" in await get_session_callable_tools(session.id)
