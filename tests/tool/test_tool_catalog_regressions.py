"""Regressions for tool catalog search (G3a, 2026-09-14)."""
from __future__ import annotations

from flocks.tool.catalog import search_tool_catalog
from flocks.tool.registry import ToolRegistry


def test_query_without_any_match_returns_nothing():
    ToolRegistry.init()
    matches, tags = search_tool_catalog("读取文件")

    assert matches == [] and tags == []


def test_limit_zero_clamps_to_one_not_default():
    ToolRegistry.init()
    matches, _ = search_tool_catalog("", limit=0)

    assert len(matches) == 1


def test_limit_none_uses_default_eight():
    ToolRegistry.init()
    matches, _ = search_tool_catalog("", limit=None)

    assert len(matches) == 8
