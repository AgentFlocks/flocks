from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.security.delegation_context import store_delegation_security_context
from flocks.server.routes.tool import _build_http_tool_context, _execute_with_http_context
from flocks.session.message import Message
from flocks.session.session import Session
from flocks.storage.storage import Storage
from flocks.tool.registry import ToolCategory, ToolInfo, ToolRegistry, ToolResult


@pytest.fixture
async def isolated_storage(tmp_path: Path):
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(tmp_path / "tool-delegation-context.db")
    yield
    Storage._initialized = False
    Storage._db_path = None


def _tool_info() -> ToolInfo:
    return ToolInfo(
        name="b4_http_context_tool",
        description="B4 HTTP context test tool",
        category=ToolCategory.CUSTOM,
    )


@pytest.mark.asyncio
async def test_http_tool_context_restores_marked_delegation_ceiling(
    tmp_path: Path,
    isolated_storage,
) -> None:
    delegated_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
        delegation_context_required=True,
    )
    await store_delegation_security_context(
        delegated_session.id,
        {
            "parent_ceiling": {"tools": ["read"]},
            "subject": {"subject_id": "user-1", "subject_type": "human"},
        },
    )

    context = await _build_http_tool_context(
        tool_name="b4_http_context_tool",
        tool_info=_tool_info(),
        session_id=delegated_session.id,
        message_id="message-1",
        agent="rex",
    )

    assert context.extra["parent_ceiling"] == {"tools": ["read"]}
    assert context.extra["subject"] == {"subject_id": "user-1", "subject_type": "human"}


@pytest.mark.asyncio
async def test_http_tool_context_fails_closed_for_marked_session_without_record(
    tmp_path: Path,
    isolated_storage,
) -> None:
    delegated_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
        delegation_context_required=True,
    )

    context = await _build_http_tool_context(
        tool_name="b4_http_context_tool",
        tool_info=_tool_info(),
        session_id=delegated_session.id,
        message_id="message-1",
        agent="rex",
    )

    assert context.extra["parent_ceiling"] == {"invalid": True}


@pytest.mark.asyncio
async def test_http_tool_context_keeps_unmarked_session_behavior(
    tmp_path: Path,
    isolated_storage,
) -> None:
    root_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
    )

    context = await _build_http_tool_context(
        tool_name="b4_http_context_tool",
        tool_info=_tool_info(),
        session_id=root_session.id,
        message_id="message-1",
        agent="rex",
    )

    assert context.extra == {}


@pytest.mark.asyncio
async def test_http_execution_fails_closed_when_marked_session_disappears_after_validation(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
        delegation_context_required=True,
    )
    session_lookup = AsyncMock(side_effect=[delegated_session, None])
    monkeypatch.setattr(Session, "get_by_id", session_lookup)
    monkeypatch.setattr(Message, "get", AsyncMock(return_value=object()))

    captured_context = None

    async def capture_execute(*, tool_name: str, ctx, **params) -> ToolResult:
        nonlocal captured_context
        captured_context = ctx
        return ToolResult(success=True, output="ok")

    monkeypatch.setattr(ToolRegistry, "execute", capture_execute)

    result = await _execute_with_http_context(
        tool_name="b4_http_context_tool",
        tool_info=_tool_info(),
        params={},
        session_id=delegated_session.id,
        message_id="message-1",
        agent="rex",
    )

    assert result.success is True
    assert session_lookup.await_count == 2
    assert captured_context is not None
    assert captured_context.extra["parent_ceiling"] == {"invalid": True}
