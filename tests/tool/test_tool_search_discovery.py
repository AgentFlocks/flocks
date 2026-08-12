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
    event_callback = AsyncMock()

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)

    ctx = SimpleNamespace(session_id="session-3", event_publish_callback=event_callback)
    result = await tool_search(ctx, query="web", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["websearch"]
    assert result.output["callableToolCount"] == 1
    assert result.output["matches"][0]["name"] == "websearch"
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
    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)

    ctx = SimpleNamespace(session_id="session-select", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="select:WebSearchTool,webfetch", limit=5)

    assert result.success is True
    assert result.output["normalizedQuery"] == "websearch webfetch"
    assert result.output["callableToolNames"] == ["webfetch", "websearch"]
    assert [match["name"] for match in result.output["matches"]] == ["websearch", "webfetch"]


@pytest.mark.asyncio
async def test_tool_search_returns_user_plugin_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("plugin_memory", ToolCategory.CUSTOM, native=False),
        _tool("read", ToolCategory.FILE),
    ]

    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
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
    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)

    ctx = SimpleNamespace(session_id="session-nondeferred", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="read", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "read"
    assert result.output["callableToolNames"] == ["read"]


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
    monkeypatch.setattr(
        "flocks.tool.system.tool_search.ToolRegistry.list_tools",
        lambda: [enabled_tool, disabled_tool],
    )

    ctx = SimpleNamespace(session_id="session-disabled", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="disabled searchable", limit=5)

    assert result.success is True
    assert result.output["count"] == 0
    assert result.output["matches"] == []
    assert result.output["callableToolNames"] == []


@pytest.mark.asyncio
async def test_tool_search_adds_device_candidate_metadata_without_affecting_callable_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        ToolInfo(
            name="tdp_event_list",
            description="List TDP events",
            category=ToolCategory.CUSTOM,
            native=True,
            enabled=True,
            source="device",
            provider="tdp_v3_3_10",
            vendor="threatbook",
        ),
    ]
    monkeypatch.setattr("flocks.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr(
        "flocks.tool.device.store.list_groups",
        AsyncMock(return_value=[SimpleNamespace(id="g1", name="上海机房")]),
    )
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(
                id="dev-1",
                name="TDP-A",
                group_id="g1",
                storage_key="tdp_v3_3_10",
                enabled=True,
            ),
            SimpleNamespace(
                id="dev-2",
                name="TDP-B",
                group_id="g1",
                storage_key="tdp_v3_3_10",
                enabled=True,
            ),
        ]),
    )

    ctx = SimpleNamespace(session_id="session-device", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="tdp event", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["tdp_event_list"]
    assert result.output["deviceAwareToolCount"] == 1
    match = result.output["matches"][0]
    assert match["name"] == "tdp_event_list"
    assert match["toolSetId"] == "tdp_v3_3_10"
    assert match["ambiguity"] == "multiple"
    assert match["requiresDeviceId"] is True
    assert [device["device_id"] for device in match["candidateDevices"]] == ["dev-1", "dev-2"]


def test_runtime_tool_events_are_recognized() -> None:
    from flocks.server.routes.event import is_runtime_event

    assert is_runtime_event("runtime.tool_selection") is True
    assert is_runtime_event("runtime.tool_discovery") is True
