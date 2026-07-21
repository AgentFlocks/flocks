from __future__ import annotations

import pytest

from flocks.tool.registry import Tool, ToolCategory, ToolContext, ToolInfo, ToolRegistry, ToolResult


def _make_tool(name: str = "lazy_demo") -> Tool:
    async def handler(ctx: ToolContext, message: str = "ok") -> ToolResult:
        return ToolResult(success=True, output=message)

    return Tool(
        info=ToolInfo(
            name=name,
            description="Lazy init demo tool",
            category=ToolCategory.SYSTEM,
        ),
        handler=handler,
    )


def _patch_lazy_init(monkeypatch: pytest.MonkeyPatch, tool: Tool) -> list[str]:
    calls: list[str] = []

    def fake_init(cls) -> None:
        calls.append("init")
        cls._initialized = True
        cls._tools = {tool.info.name: tool}
        cls._dynamic_tools_by_module = {"demo_module": [tool.info.name]}

    monkeypatch.setattr(ToolRegistry, "_initialized", False)
    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_dynamic_tools_by_module", {})
    monkeypatch.setattr(ToolRegistry, "init", classmethod(fake_init))
    return calls


def test_public_registry_reads_lazy_initialize_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _make_tool()
    init_calls = _patch_lazy_init(monkeypatch, tool)

    assert ToolRegistry.get(tool.info.name) is tool
    assert [info.name for info in ToolRegistry.list_tools()] == [tool.info.name]
    assert ToolRegistry.get_schema(tool.info.name) is not None
    assert ToolRegistry.all_tool_ids() == [tool.info.name]
    assert ToolRegistry.get_dynamic_tools_by_module() == {"demo_module": [tool.info.name]}
    assert init_calls == ["init"]


@pytest.mark.asyncio
async def test_init_async_runs_lazy_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _make_tool()
    init_calls = _patch_lazy_init(monkeypatch, tool)

    await ToolRegistry.init_async()

    assert ToolRegistry.get(tool.info.name) is tool
    assert init_calls == ["init"]


@pytest.mark.asyncio
async def test_execute_lazy_initializes_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _make_tool()
    init_calls = _patch_lazy_init(monkeypatch, tool)

    result = await ToolRegistry.execute(tool.info.name, message="hello")

    assert result.success is True
    assert result.output == "hello"
    assert init_calls == ["init"]
