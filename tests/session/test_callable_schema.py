from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.session.callable_state import (
    get_session_callable_tools,
    set_session_callable_tools,
)
from flocks.storage.storage import Storage
from flocks.tool.registry import ToolCategory, ToolInfo
from flocks.tool.system.tool_search import tool_search


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
        always_load_tool_names={"question", "tool_search", "send_to_session", "sleep"},
    )
    assert result.metadata["callableToolCount"] == 2


@pytest.mark.asyncio
async def test_callable_schema_strict_scope_loads_declared_tools_missing_from_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("bash", ToolCategory.CODE),
        _tool("agent_only", ToolCategory.CUSTOM, native=False),
    ]
    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "question", "tool_search", "bash"}),
    )

    result = await list_session_callable_tool_infos(
        session_id="session-isolated",
        declared_tool_names=["read", "agent_only"],
        strict_declared_tools=True,
    )

    assert [tool.name for tool in result.tool_infos] == ["read", "agent_only"]
    assert result.metadata["callableToolNames"] == ["agent_only", "read"]
    assert result.metadata["alwaysLoadToolNames"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_tools", [None, []])
@pytest.mark.parametrize("existing_tools", [set(), {"bash", "question", "tool_search"}])
async def test_callable_schema_strict_empty_scope_stays_empty_without_overwriting_session(
    monkeypatch: pytest.MonkeyPatch,
    declared_tools,
    existing_tools,
) -> None:
    session_id = "session-empty-declaration"
    await set_session_callable_tools(session_id, existing_tools)
    original = await Storage.get(f"session_callable_tools:{session_id}")
    monkeypatch.setattr(
        "flocks.session.callable_schema.ToolRegistry.list_tools",
        lambda: [
            _tool("bash", ToolCategory.CODE),
            _tool("question", ToolCategory.SYSTEM),
            _tool("tool_search", ToolCategory.SYSTEM),
        ],
    )

    result = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=declared_tools,
        strict_declared_tools=True,
    )

    assert result.tool_infos == []
    assert result.metadata["callableToolNames"] == []
    assert result.metadata["alwaysLoadToolNames"] == []
    assert await get_session_callable_tools(session_id) == existing_tools
    assert await Storage.get(f"session_callable_tools:{session_id}") == original


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_tools", [set(), {"bash", "rex_discovered"}])
async def test_callable_schema_strict_scope_preserves_session_tools(
    monkeypatch: pytest.MonkeyPatch,
    existing_tools,
) -> None:
    session_id = "session-preserved-discovery"
    await set_session_callable_tools(session_id, existing_tools)
    original = await Storage.get(f"session_callable_tools:{session_id}")
    monkeypatch.setattr(
        "flocks.session.callable_schema.ToolRegistry.list_tools",
        lambda: [
            _tool("bash", ToolCategory.CODE),
            _tool("rex_discovered", ToolCategory.CUSTOM, native=False),
            _tool("agent_only", ToolCategory.CUSTOM, native=False),
        ],
    )

    result = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=["agent_only"],
        strict_declared_tools=True,
    )

    assert [tool.name for tool in result.tool_infos] == ["agent_only"]
    assert await get_session_callable_tools(session_id) == existing_tools
    assert await Storage.get(f"session_callable_tools:{session_id}") == original


@pytest.mark.asyncio
async def test_callable_schema_switches_scopes_without_search_expansion_and_restores_rex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "session-agent-switch"
    rex_tools = {"bash", "rex_discovered"}
    await set_session_callable_tools(session_id, rex_tools)
    tools = [
        _tool("bash", ToolCategory.CODE),
        _tool("rex_discovered", ToolCategory.CUSTOM, native=False),
        _tool("agent_a_only", ToolCategory.CUSTOM, native=False),
        _tool("agent_b_only", ToolCategory.CUSTOM, native=False),
        _tool("outside_scope", ToolCategory.CUSTOM, native=False),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("question", ToolCategory.SYSTEM),
    ]
    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)

    first = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=["agent_a_only", "tool_search"],
        strict_declared_tools=True,
        agent="agent-a",
    )
    discovery = await tool_search(
        SimpleNamespace(session_id=session_id, event_publish_callback=None),
        query="select:outside_scope",
    )
    after_search = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=["agent_a_only", "tool_search"],
        strict_declared_tools=True,
        agent="agent-a",
    )
    second = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=["agent_b_only"],
        strict_declared_tools=True,
        agent="agent-b",
    )
    rex_again = await list_session_callable_tool_infos(
        session_id,
        declared_tool_names=["bash"],
        agent="rex",
    )

    assert discovery.success is True
    assert discovery.output["callableToolNames"] == ["outside_scope"]
    assert {tool.name for tool in first.tool_infos} == {"agent_a_only", "tool_search"}
    assert {tool.name for tool in after_search.tool_infos} == {"agent_a_only", "tool_search"}
    assert {tool.name for tool in second.tool_infos} == {"agent_b_only"}
    assert rex_tools | {"outside_scope"} <= {tool.name for tool in rex_again.tool_infos}
    assert "agent_a_only" not in {tool.name for tool in rex_again.tool_infos}
    assert "agent_b_only" not in {tool.name for tool in rex_again.tool_infos}
    assert await get_session_callable_tools(session_id) == rex_tools | {"outside_scope"}


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


@pytest.mark.asyncio
async def test_callable_schema_dynamically_exposes_device_manage_when_enabled_devices_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("question", ToolCategory.SYSTEM),
        _tool("tool_search", ToolCategory.SYSTEM),
        _tool("device_manage", ToolCategory.SYSTEM),
    ]

    monkeypatch.setattr("flocks.session.callable_schema.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"question", "tool_search"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.ToolRegistry.get",
        lambda name: SimpleNamespace(info=_tool(name, ToolCategory.SYSTEM))
        if name == "device_manage"
        else None,
    )
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[SimpleNamespace(enabled=True)]),
    )

    result = await list_session_callable_tool_infos(session_id="session-device-aware")

    names = [tool.name for tool in result.tool_infos]
    assert set(names) == {"device_manage", "question", "tool_search"}
    assert "device_manage" in result.metadata["alwaysLoadToolNames"]
