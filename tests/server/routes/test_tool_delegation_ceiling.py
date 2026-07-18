from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from flocks.security.delegation_context import store_delegation_security_context
from flocks.server.routes.tool import (
    BatchExecuteRequest,
    ToolExecuteRequest,
    _build_http_tool_context,
    _execute_with_http_context,
)
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


def test_http_requests_leave_omitted_agent_unset_for_session_fallback() -> None:
    assert ToolExecuteRequest().agent is None
    assert BatchExecuteRequest(calls=[]).agent is None


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path / "workspace"),
    )

    expected_ceiling = {"tools": ["read"], "permission_mode": "readonly"}
    build_context = AsyncMock(return_value={"parent_ceiling": expected_ceiling})
    monkeypatch.setattr(
        "flocks.security.execution_context.build_root_execution_security_context",
        build_context,
    )

    context = await _build_http_tool_context(
        tool_name="b4_http_context_tool",
        tool_info=_tool_info(),
        session_id=root_session.id,
        message_id="message-1",
        agent="rex",
    )

    assert context.extra["parent_ceiling"] == expected_ceiling
    build_context.assert_awaited_once_with(
        session_id=root_session.id,
        agent_name=root_session.agent,
        workspace=root_session.directory,
        supplied_context={},
    )


@pytest.mark.asyncio
async def test_http_root_uses_explicit_agent_for_ceiling_and_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_storage,
) -> None:
    root_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path),
        agent="rex",
    )
    resolve_agent = AsyncMock(return_value="worker")
    monkeypatch.setattr(
        "flocks.security.execution_context.resolve_execution_agent",
        resolve_agent,
    )
    builder = AsyncMock(return_value={"parent_ceiling": {"tools": ["read"]}})
    monkeypatch.setattr(
        "flocks.security.execution_context.build_root_execution_security_context",
        builder,
    )

    context = await _build_http_tool_context(
        tool_name="read",
        tool_info=_tool_info(),
        session_id=root_session.id,
        message_id="message-1",
        agent="worker",
    )

    assert context.agent == "worker"
    resolve_agent.assert_awaited_once_with("worker", "rex")
    assert builder.await_args.kwargs["agent_name"] == "worker"


@pytest.mark.asyncio
async def test_http_root_rejects_unknown_explicit_agent_before_building_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_storage,
) -> None:
    root_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path),
        agent="rex",
    )
    builder = AsyncMock()
    monkeypatch.setattr(
        "flocks.security.execution_context.resolve_execution_agent",
        AsyncMock(side_effect=ValueError("Unknown execution agent: unknown")),
    )
    monkeypatch.setattr(
        "flocks.security.execution_context.build_root_execution_security_context",
        builder,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _build_http_tool_context(
            tool_name="read",
            tool_info=_tool_info(),
            session_id=root_session.id,
            message_id="message-1",
            agent="unknown",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown execution agent: unknown"
    builder.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_delegated_session_ignores_agent_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_storage,
) -> None:
    delegated_session = await Session.create(
        project_id="project-1",
        directory=str(tmp_path),
        agent="target-agent",
        delegation_context_required=True,
    )
    await store_delegation_security_context(
        delegated_session.id,
        {"parent_ceiling": {"tools": ["read"]}},
    )
    resolve_agent = AsyncMock()
    monkeypatch.setattr(
        "flocks.security.execution_context.resolve_execution_agent",
        resolve_agent,
    )

    context = await _build_http_tool_context(
        tool_name="read",
        tool_info=_tool_info(),
        session_id=delegated_session.id,
        message_id="message-1",
        agent="attacker-agent",
    )

    assert context.agent == "target-agent"
    assert context.extra["parent_ceiling"] == {"tools": ["read"]}
    resolve_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_http_rejects_unknown_explicit_agent_before_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.security.execution_context.resolve_execution_agent",
        AsyncMock(side_effect=ValueError("Unknown execution agent: unknown")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _build_http_tool_context(
            tool_name="b4_http_context_tool",
            tool_info=_tool_info(),
            session_id=None,
            message_id=None,
            agent="unknown",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown execution agent: unknown"


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
