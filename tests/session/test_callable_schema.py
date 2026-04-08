from unittest.mock import AsyncMock

import pytest

from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.tool.registry import ToolCategory, ToolInfo


def _tool(name: str, category: ToolCategory, native: bool = True) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} description",
        category=category,
        native=native,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_callable_schema_returns_session_callable_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("bash", ToolCategory.CODE),
    ]
    event_callback = AsyncMock()

    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "bash"}),
    )

    result = await list_session_callable_tool_infos(
        session_id="session-1",
        event_publish_callback=event_callback,
    )

    names = [tool.name for tool in result.tool_infos]
    assert "read" in names
    assert "bash" in names
    assert "question" in names
    assert "tool_search" in names
    assert result.metadata["callableToolCount"] == 2


@pytest.mark.asyncio
async def test_callable_schema_initializes_from_declared_tools_when_session_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("websearch", ToolCategory.BROWSER),
    ]

    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value=set()),
    )
    initialize_mock = AsyncMock(return_value={"read", "websearch", "question", "tool_search"})
    monkeypatch.setattr(
        "flocks.session.callable_schema.initialize_session_callable_tools",
        initialize_mock,
    )

    result = await list_session_callable_tool_infos(
        session_id="session-terminal",
        declared_tool_names=["read", "websearch"],
    )

    names = [tool.name for tool in result.tool_infos]
    assert "read" in names
    assert "websearch" in names
    initialize_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_callable_schema_does_not_expand_empty_declared_tools_to_all_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("websearch", ToolCategory.BROWSER),
    ]

    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value=set()),
    )
    initialize_mock = AsyncMock(return_value={"question", "tool_search"})
    monkeypatch.setattr(
        "flocks.session.callable_schema.initialize_session_callable_tools",
        initialize_mock,
    )

    result = await list_session_callable_tool_infos(
        session_id="session-always-load-only",
        declared_tool_names=[],
    )

    names = [tool.name for tool in result.tool_infos]
    assert names == ["question", "tool_search"]
    initialize_mock.assert_awaited_once_with(
        "session-always-load-only",
        [],
        always_load_tool_names={"question", "tool_search"},
    )
    assert result.metadata["callableToolCount"] == 2


@pytest.mark.asyncio
async def test_callable_schema_keeps_user_plugin_tools_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("project_memory", ToolCategory.CUSTOM, native=False),
    ]

    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"project_memory"}),
    )

    result = await list_session_callable_tool_infos(
        session_id="session-plugin",
        declared_tool_names=["read"],
    )

    names = [tool.name for tool in result.tool_infos]
    assert "project_memory" in names
