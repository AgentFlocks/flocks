from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
async def test_tool_search_adds_matches_to_session_callable_tools_and_emits_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("read", ToolCategory.FILE),
        _tool("plugin_only", ToolCategory.CUSTOM, native=False),
    ]
    add_callable = AsyncMock(return_value={"websearch"})
    event_callback = AsyncMock()

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add_callable)

    ctx = SimpleNamespace(session_id="session-3", event_publish_callback=event_callback)
    result = await tool_search(ctx, query="web", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["websearch"]
    assert result.output["callableToolCount"] == 1
    assert result.output["matches"][0]["name"] == "websearch"
    add_callable.assert_awaited_once_with("session-3", ["websearch"])
    event_callback.assert_awaited()


@pytest.mark.asyncio
async def test_tool_search_supports_category_and_tag_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        ToolInfo(
            name="websearch",
            description="Search the web for public information",
            category=ToolCategory.BROWSER,
            native=True,
            enabled=True,
            tags=["web", "research"],
        ),
        ToolInfo(
            name="read",
            description="Read local files",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
            tags=["code-reading"],
        ),
    ]

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.tool.system.tool_search.add_session_callable_tools",
        AsyncMock(return_value={"websearch"}),
    )

    ctx = SimpleNamespace(session_id="session-4", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="research", category="browser", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "websearch"
    assert result.output["matches"][0]["matchedTags"] == ["research"]
    assert result.output["matchedTags"] == ["research"]


@pytest.mark.asyncio
async def test_tool_search_supports_exact_batch_select_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("webfetch", ToolCategory.BROWSER),
        _tool("read", ToolCategory.FILE),
    ]
    add_callable = AsyncMock(return_value={"websearch", "webfetch"})

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add_callable)

    ctx = SimpleNamespace(session_id="session-select", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="select:WebSearchTool,webfetch", limit=5)

    assert result.success is True
    assert result.output["normalizedQuery"] == "websearch webfetch"
    assert result.output["callableToolNames"] == ["webfetch", "websearch"]
    assert [match["name"] for match in result.output["matches"]] == ["websearch", "webfetch"]
    add_callable.assert_awaited_once_with("session-select", ["websearch", "webfetch"])


@pytest.mark.asyncio
async def test_tool_search_returns_user_plugin_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("plugin_memory", ToolCategory.CUSTOM, native=False),
        _tool("read", ToolCategory.FILE),
    ]

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.tool.system.tool_search.add_session_callable_tools",
        AsyncMock(return_value=set()),
    )

    ctx = SimpleNamespace(session_id="session-plugin", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="plugin_memory", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "plugin_memory"
    assert result.output["matches"][0]["native"] is False


@pytest.mark.asyncio
async def test_tool_search_adds_matching_tools_to_callable_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("glob", ToolCategory.SEARCH),
    ]
    add_callable = AsyncMock(return_value={"glob", "read"})

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add_callable)

    ctx = SimpleNamespace(session_id="session-nondeferred", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="read", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "read"
    assert result.output["callableToolNames"] == ["read"]
    add_callable.assert_awaited_once_with("session-nondeferred", ["read"])


@pytest.mark.asyncio
async def test_tool_search_does_not_return_disabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enabled_tool = _tool("read", ToolCategory.FILE)
    disabled_tool = ToolInfo(
        name="disabled_searchable",
        description="disabled searchable tool",
        category=ToolCategory.SEARCH,
        native=True,
        enabled=False,
    )
    add_callable = AsyncMock(return_value=set())

    monkeypatch.setattr(
        "flocks.tool.system.tool_search.ToolRegistry.list_tools",
        lambda: [enabled_tool, disabled_tool],
    )
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add_callable)

    ctx = SimpleNamespace(session_id="session-disabled", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="disabled searchable", limit=5)

    assert result.success is True
    assert result.output["count"] == 0
    assert result.output["matches"] == []
    assert result.output["callableToolNames"] == []
    add_callable.assert_awaited_once_with("session-disabled", [])


def test_runtime_tool_events_are_recognized() -> None:
    from flocks.server.routes.event import is_runtime_event

    assert is_runtime_event("runtime.tool_selection") is True
    assert is_runtime_event("runtime.tool_discovery") is True
