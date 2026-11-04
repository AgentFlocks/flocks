"""Regressions for YAML tool file placement and python tool deletion (G3a, 2026-09-14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from flocks.tool import tool_loader


@pytest.fixture
def plugin_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugins" / "tools"
    monkeypatch.setattr(tool_loader, "_TOOLS_SUBDIR", root)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.mark.parametrize("name", ["../evil", "../../x", "a/b", "/abs", ".hidden", ""])
def test_create_yaml_tool_rejects_unsafe_names(plugin_tools: Path, name: str):
    with pytest.raises(ValueError):
        tool_loader.create_yaml_tool({"name": name, "handler": {"type": "http", "url": "https://x"}})
    assert not plugin_tools.exists() or not list(plugin_tools.rglob("*.yaml"))


def test_create_yaml_tool_rejects_unsafe_provider(plugin_tools: Path):
    with pytest.raises(ValueError):
        tool_loader.create_yaml_tool(
            {"name": "ok_tool", "handler": {"type": "http", "url": "https://x"}},
            provider="../../escape",
        )
    assert not plugin_tools.exists() or not list(plugin_tools.rglob("*.yaml"))


def test_create_yaml_tool_writes_inside_tools_dir(plugin_tools: Path):
    path = tool_loader.create_yaml_tool(
        {"name": "ok_tool", "handler": {"type": "http", "url": "https://x"}},
        provider="svc-1",
    )
    assert path == (plugin_tools / "api" / "svc-1" / "ok_tool.yaml").resolve()
    assert path.is_file()


def test_delete_python_tool_unlinks_import_only_file(plugin_tools: Path):
    py_dir = plugin_tools / "python"
    py_dir.mkdir(parents=True)
    only_one = py_dir / "single.py"
    only_one.write_text(
        "from flocks.tool.registry import ToolRegistry, ToolResult, ToolCategory\n\n\n"
        "@ToolRegistry.register_function(name=\"solo_tool\", description=\"t\", category=ToolCategory.CUSTOM)\n"
        "async def solo_tool(ctx):\n    return ToolResult(success=True, output=\"t\")\n"
    )
    two = py_dir / "pair.py"
    two.write_text(
        "from flocks.tool.registry import ToolRegistry, ToolResult, ToolCategory\n\n\n"
        "@ToolRegistry.register_function(name=\"pair_a\", description=\"a\", category=ToolCategory.CUSTOM)\n"
        "async def pair_a(ctx):\n    return ToolResult(success=True, output=\"a\")\n\n\n"
        "@ToolRegistry.register_function(name=\"pair_b\", description=\"b\", category=ToolCategory.CUSTOM)\n"
        "async def pair_b(ctx):\n    return ToolResult(success=True, output=\"b\")\n"
    )

    assert tool_loader.delete_python_tool("solo_tool") is True
    assert not only_one.exists(), "a file left with only imports must be removed"

    assert tool_loader.delete_python_tool("pair_a") is True
    remaining = two.read_text()
    assert "pair_a" not in remaining and "pair_b" in remaining
