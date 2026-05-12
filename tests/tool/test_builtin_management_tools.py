from flocks.tool.registry import ToolRegistry


def test_flocks_mcp_is_registered_as_builtin_tool() -> None:
    ToolRegistry.init()

    tool = ToolRegistry.get("flocks_mcp")

    assert tool is not None
    assert tool.info.native is True
    assert tool.info.source in {None, "builtin"}


def test_skill_remains_registered_as_builtin_tool() -> None:
    ToolRegistry.init()

    tool = ToolRegistry.get("skill")

    assert tool is not None
    assert tool.info.native is True
    assert tool.info.source in {None, "builtin"}


def test_lsp_remains_non_native_by_default() -> None:
    ToolRegistry.init()

    tool = ToolRegistry.get("lsp")

    assert tool is not None
    assert tool.info.native is False
