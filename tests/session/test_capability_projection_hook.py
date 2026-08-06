from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.identity import Subject, reset_current_subject, set_current_subject
from flocks.session.callable_schema import (
    list_session_callable_tool_infos,
    register_callable_tool_resolver,
    unregister_callable_tool_resolver,
)
from flocks.tool.registry import ToolCategory, ToolInfo


def _tool(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} description",
        category=ToolCategory.CUSTOM,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_capability_projection_preserves_candidates_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_tool("read"), _tool("write")]
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "write"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )

    result = await list_session_callable_tool_infos("session-1")

    assert result.tool_infos == candidates
    assert result.tool_infos[0] is candidates[0]


@pytest.mark.asyncio
async def test_capability_projection_ignores_legacy_projection_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_tool("read"), _tool("write")]
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "write"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )

    result = await list_session_callable_tool_infos("session-1")

    assert [tool.name for tool in result.tool_infos] == ["read", "write"]


@pytest.mark.asyncio
async def test_capability_projection_remains_stable_with_subject_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_tool("read"), _tool("bash")]
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "bash"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )
    subject = Subject(
        subject_id="user-1",
        subject_type="human",
        attributes={
            "entry": "webui",
            "permission_mode": "readonly",
            "role": "operator",
            "department": "platform",
            "tenant_id": "tenant-a",
        },
    )
    token = set_current_subject(subject)
    try:
        result = await list_session_callable_tool_infos("session-1", agent="build")
    finally:
        reset_current_subject(token)

    assert [tool.name for tool in result.tool_infos] == ["read", "bash"]


@pytest.mark.asyncio
async def test_declarative_resolver_can_only_narrow_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_tool("read"), _tool("write")]
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "write"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )

    async def resolver(tool_infos, _context):
        return [tool_infos[0], _tool("injected")]

    register_callable_tool_resolver("test.narrow", resolver)
    try:
        result = await list_session_callable_tool_infos("session-1")
    finally:
        unregister_callable_tool_resolver("test.narrow")

    assert [tool.name for tool in result.tool_infos] == ["read"]
