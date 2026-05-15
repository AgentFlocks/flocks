from flocks.tool.catalog import (
    apply_tool_catalog_defaults,
    canonical_tool_token,
    get_tool_catalog_metadata,
    list_tool_catalog_infos,
    normalize_tool_search_query,
    search_tool_catalog,
)
from flocks.tool.registry import ToolCategory, ToolInfo, ToolRegistry


def test_apply_tool_catalog_defaults_for_read_tool() -> None:
    info = ToolInfo(
        name="read",
        description="Read file contents",
        category=ToolCategory.FILE,
        native=True,
    )

    enriched = apply_tool_catalog_defaults(info)

    assert enriched.always_load is False
    assert "file-inspection" in enriched.tags


def test_catalog_metadata_uses_real_registered_read_tool_name() -> None:
    metadata = get_tool_catalog_metadata("read")

    assert "code-reading" in metadata.tags


def test_registry_uses_read_not_read_file() -> None:
    tool_ids = set(ToolRegistry.all_tool_ids())

    assert "read" in tool_ids
    assert "read_file" not in tool_ids


def test_catalog_marks_tool_search_as_always_load() -> None:
    info = ToolInfo(
        name="tool_search",
        description="Search tools",
        category=ToolCategory.SYSTEM,
        native=True,
    )

    metadata = get_tool_catalog_metadata("tool_search", info)

    assert metadata.always_load is True


def test_explicit_tags_are_merged_with_defaults() -> None:
    info = ToolInfo(
        name="websearch",
        description="Search the web",
        category=ToolCategory.BROWSER,
        native=True,
        tags=["research"],
    )

    enriched = apply_tool_catalog_defaults(info)

    assert "research" in enriched.tags
    assert "web" in enriched.tags


def test_tool_search_query_normalization_supports_aliases() -> None:
    assert canonical_tool_token("WebSearchTool") == "websearch"
    assert normalize_tool_search_query("WebSearchTool, web-fetch") == "websearch webfetch"


def test_search_tool_catalog_selects_exact_tools_in_order(monkeypatch) -> None:
    tools = [
        ToolInfo(
            name="websearch",
            description="Search the web",
            category=ToolCategory.BROWSER,
            native=True,
            enabled=True,
        ),
        ToolInfo(
            name="webfetch",
            description="Fetch a web page",
            category=ToolCategory.BROWSER,
            native=True,
            enabled=True,
        ),
        ToolInfo(
            name="read",
            description="Read file contents",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
        ),
    ]
    monkeypatch.setattr(
        "flocks.tool.registry.ToolRegistry.list_tools",
        lambda: tools,
    )

    matches, matched_tags = search_tool_catalog("select:WebFetchTool,websearch", limit=5)

    assert [match["name"] for match in matches] == ["webfetch", "websearch"]
    assert matched_tags == []


def test_list_tool_catalog_infos_excludes_disabled_tools(monkeypatch) -> None:
    enabled_tool = ToolInfo(
        name="read",
        description="Read file contents",
        category=ToolCategory.FILE,
        native=True,
        enabled=True,
    )
    disabled_tool = ToolInfo(
        name="disabled_tool",
        description="Disabled helper",
        category=ToolCategory.SYSTEM,
        native=True,
        enabled=False,
    )

    monkeypatch.setattr(
        "flocks.tool.registry.ToolRegistry.list_tools",
        lambda: [enabled_tool, disabled_tool],
    )

    infos = list_tool_catalog_infos()

    assert [tool.name for tool in infos] == ["read"]
