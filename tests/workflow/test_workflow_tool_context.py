from __future__ import annotations

from pathlib import Path

import pytest

from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.storage.storage import Storage
from flocks.workflow.tool_context import (
    build_workflow_tool_context,
    cleanup_workflow_tool_context,
)


@pytest.fixture
async def isolated_storage(tmp_path: Path):
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(tmp_path / "workflow-tool-context.db")
    yield
    Storage._initialized = False
    Storage._db_path = None


@pytest.mark.asyncio
async def test_build_workflow_tool_context_creates_temp_parent_session_and_message(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    tool_context = await build_workflow_tool_context(
        workflow_id="wf-1",
        action_name="invoke",
    )

    session = await Session.get_by_id(tool_context.session_id)
    assert session is not None
    assert session.title == "Workflow invoke: wf-1"
    assert session.metadata["workflowTempParent"] is True
    assert session.metadata["hideFromSessionManager"] is True
    assert session.metadata["workflowId"] == "wf-1"
    assert session.metadata["workflowAction"] == "invoke"
    assert session.directory == str(tmp_path)
    assert tool_context.extra["workspace_dir"] == str(tmp_path)
    assert tool_context.extra["main_session_key"] == tool_context.session_id

    message = await Message.get(tool_context.session_id, tool_context.message_id)
    assert message is not None
    assert message.role == MessageRole.USER
    assert message.agent == "rex"

    parts = await Message.parts(tool_context.message_id, tool_context.session_id)
    assert len(parts) == 1
    assert getattr(parts[0], "text", "") == "[Workflow invoke] wf-1"
    assert getattr(parts[0], "synthetic", None) is True


@pytest.mark.asyncio
async def test_build_workflow_tool_context_reuses_existing_parent_session(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    parent = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
        title="Existing session",
        agent="rex-junior",
        category="task",
    )

    tool_context = await build_workflow_tool_context(
        workflow_id="wf-2",
        action_name="run",
        session_id=parent.id,
    )

    assert tool_context.session_id == parent.id
    assert tool_context.agent == "rex-junior"
    assert tool_context.extra["workspace_dir"] == str(tmp_path / "workspace")
    assert tool_context.extra["main_session_key"] == parent.id

    message = await Message.get(tool_context.session_id, tool_context.message_id)
    assert message is not None
    assert message.role == MessageRole.USER
    assert message.agent == "rex-junior"

    assert await cleanup_workflow_tool_context(tool_context) is False
    assert await Session.get_by_id(parent.id) is not None


@pytest.mark.asyncio
async def test_cleanup_workflow_tool_context_purges_unused_temp_parent(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    tool_context = await build_workflow_tool_context(
        workflow_id="wf-cleanup",
        action_name="trigger:kafka",
    )
    session_id = tool_context.session_id
    message_id = tool_context.message_id

    cleaned = await cleanup_workflow_tool_context(tool_context)

    assert cleaned is True
    assert await Session.get_by_id(session_id) is None
    assert await Message.get(session_id, message_id) is None
    assert await Storage.get(f"session_callable_tools:{session_id}") is None


@pytest.mark.asyncio
async def test_cleanup_workflow_tool_context_preserves_parent_with_child_session(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    tool_context = await build_workflow_tool_context(
        workflow_id="wf-delegated",
        action_name="trigger:schedule",
    )
    parent = await Session.get_by_id(tool_context.session_id)
    assert parent is not None
    child = await Session.create(
        project_id=parent.project_id,
        directory=parent.directory,
        title="Delegated child",
        parent_id=parent.id,
        agent="rex-junior",
        category="task",
    )
    tool_context.extra["workflow_child_session_created"] = True

    cleaned = await cleanup_workflow_tool_context(tool_context)

    assert cleaned is False
    assert await Session.get_by_id(parent.id) is not None
    assert await Session.get_by_id(child.id) is not None
