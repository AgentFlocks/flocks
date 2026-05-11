"""
tests/agent/test_prompt_utils.py

单元测试：flocks.agent.prompt_utils 中被修改 / 新增的函数
- categorize_tools()           : 使用 ToolRegistry 真实 category
- _format_tools_for_prompt()   : 按 ToolCategory 分组显示所有工具
- build_tool_selection_table() : 仅渲染工具目录
- build_agent_selection_table(): 单独渲染 agent 调度表
- build_workflows_section()    : workflow 列表渲染
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock, patch

import pytest

from flocks.agent.agent import AvailableAgent, AvailableCategory, AvailableSkill, AvailableTool, AvailableWorkflow
from flocks.agent.prompt_utils import (
    _format_tools_for_prompt,
    build_agent_selection_table,
    build_tool_selection_table,
    build_workflows_section,
    categorize_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_entry(name: str, category_value: str) -> MagicMock:
    """Build a fake ToolRegistry entry whose .info.category.value == category_value."""
    entry = MagicMock()
    entry.info.category.value = category_value
    return entry


def _registry_side_effect(mapping: dict):
    """Return a side_effect callable for ToolRegistry.get()."""
    def _get(name):
        return mapping.get(name)
    return _get


# ===========================================================================
# categorize_tools
# ===========================================================================

class TestCategorizeTools:
    """ToolRegistry is lazy-imported inside categorize_tools(), so we patch at its real module path."""

    _REGISTRY_GET = "flocks.tool.registry.ToolRegistry.get"
    _REGISTRY_INIT = "flocks.tool.registry.ToolRegistry.init"

    def test_uses_real_category_from_registry(self):
        mapping = {
            "bash": _make_tool_entry("bash", "terminal"),
            "read": _make_tool_entry("read", "file"),
            "grep": _make_tool_entry("grep", "search"),
        }
        with patch(self._REGISTRY_GET, side_effect=_registry_side_effect(mapping)), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools(["bash", "read", "grep"])
        categories = {t.name: t.category for t in result}
        assert categories["bash"] == "terminal"
        assert categories["read"] == "file"
        assert categories["grep"] == "search"

    def test_unknown_tool_defaults_to_system(self):
        with patch(self._REGISTRY_GET, return_value=None), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools(["nonexistent_tool"])
        assert result[0].category == "system"

    def test_returns_avail_tool_objects(self):
        mapping = {"skill": _make_tool_entry("skill", "system")}
        with patch(self._REGISTRY_GET, side_effect=_registry_side_effect(mapping)), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools(["skill"])
        assert all(isinstance(t, AvailableTool) for t in result)

    def test_preserves_tool_names(self):
        names = ["read", "write", "bash"]
        mapping = {n: _make_tool_entry(n, "file") for n in names}
        with patch(self._REGISTRY_GET, side_effect=_registry_side_effect(mapping)), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools(names)
        assert [t.name for t in result] == names

    def test_empty_list_returns_empty(self):
        with patch(self._REGISTRY_GET, return_value=None), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools([])
        assert result == []

    def test_fallback_on_registry_exception(self):
        """If ToolRegistry.init() raises, fallback returns system category for all tools."""
        with patch(self._REGISTRY_INIT, side_effect=RuntimeError("registry unavailable")):
            result = categorize_tools(["any_tool"])
        assert len(result) == 1
        assert result[0].category == "system"

    def test_mixes_categories_correctly(self):
        mapping = {
            "read": _make_tool_entry("read", "file"),
            "write": _make_tool_entry("write", "file"),
            "webfetch": _make_tool_entry("webfetch", "browser"),
            "bash": _make_tool_entry("bash", "terminal"),
        }
        with patch(self._REGISTRY_GET, side_effect=_registry_side_effect(mapping)), \
             patch(self._REGISTRY_INIT):
            result = categorize_tools(list(mapping.keys()))
        cat_map = {t.name: t.category for t in result}
        assert cat_map["read"] == "file"
        assert cat_map["write"] == "file"
        assert cat_map["webfetch"] == "browser"
        assert cat_map["bash"] == "terminal"


# ===========================================================================
# _format_tools_for_prompt
# ===========================================================================

class TestFormatToolsForPrompt:

    def test_groups_by_category(self):
        tools = [
            AvailableTool(name="read", category="file"),
            AvailableTool(name="write", category="file"),
            AvailableTool(name="bash", category="terminal"),
        ]
        output = _format_tools_for_prompt(tools)
        assert "**File**" in output
        assert "`read`" in output
        assert "`write`" in output
        assert "**Terminal**" in output
        assert "`bash`" in output

    def test_no_tools_dropped(self):
        """Every tool must appear in the output regardless of category."""
        tools = [
            AvailableTool(name="run_workflow", category="system"),
            AvailableTool(name="delegate_task", category="system"),
            AvailableTool(name="grep", category="search"),
            AvailableTool(name="webfetch", category="browser"),
        ]
        output = _format_tools_for_prompt(tools)
        for t in tools:
            assert f"`{t.name}`" in output, f"Tool '{t.name}' missing from output"

    def test_unknown_category_still_rendered(self):
        """A tool with a non-standard category must still appear."""
        tools = [AvailableTool(name="my_plugin_tool", category="custom")]
        output = _format_tools_for_prompt(tools)
        assert "`my_plugin_tool`" in output

    def test_empty_returns_empty_string(self):
        assert _format_tools_for_prompt([]) == ""

    def test_ordering_respects_category_order(self):
        """File category should appear before Terminal."""
        tools = [
            AvailableTool(name="bash", category="terminal"),
            AvailableTool(name="read", category="file"),
        ]
        output = _format_tools_for_prompt(tools)
        file_pos = output.index("**File**")
        terminal_pos = output.index("**Terminal**")
        assert file_pos < terminal_pos

    def test_single_tool_each_category(self):
        tools = [AvailableTool(name="glob", category="search")]
        output = _format_tools_for_prompt(tools)
        assert "**Search**" in output
        assert "`glob`" in output

    def test_multiple_tools_same_category_comma_separated(self):
        tools = [
            AvailableTool(name="read", category="file"),
            AvailableTool(name="write", category="file"),
            AvailableTool(name="glob", category="file"),
        ]
        output = _format_tools_for_prompt(tools)
        # All three should be on the same line
        file_line = [l for l in output.splitlines() if "**File**" in l][0]
        assert "`read`" in file_line
        assert "`write`" in file_line
        assert "`glob`" in file_line


# ===========================================================================
# build_tool_selection_table
# ===========================================================================

class TestBuildToolSelectionTable:

    def test_contains_available_tools_header(self):
        tools = [AvailableTool(name="bash", category="terminal")]
        output = build_tool_selection_table([], tools)
        assert "Available Tools" in output

    def test_tools_rendered_in_output(self):
        tools = [
            AvailableTool(name="read", category="file"),
            AvailableTool(name="bash", category="terminal"),
        ]
        output = build_tool_selection_table([], tools)
        assert "`read`" in output
        assert "`bash`" in output

    def test_agents_not_rendered_in_tools_output(self):
        meta = MagicMock()
        meta.cost = "CHEAP"
        meta.category = "general"
        meta.triggers = []
        meta.key_trigger = None
        agents = [AvailableAgent(name="explore", description="explore agent.", metadata=meta)]
        output = build_tool_selection_table(agents, [])
        assert "explore" not in output
        assert "When to Use" not in output

    def test_empty_inputs_no_crash(self):
        output = build_tool_selection_table([], [])
        assert isinstance(output, str)

    def test_default_flow_hint_removed(self):
        output = build_tool_selection_table([], [])
        assert "Default flow" not in output


# ===========================================================================
# build_agent_selection_table
# ===========================================================================

class TestBuildAgentSelectionTable:

    def _make_agent(self, name: str, cost: str = "CHEAP") -> AvailableAgent:
        meta = MagicMock()
        meta.cost = cost
        meta.category = "general"
        meta.triggers = []
        meta.key_trigger = None
        agent = AvailableAgent(name=name, description=f"{name} agent.", metadata=meta)
        return agent

    def test_agents_table_present_when_agents_exist(self):
        agents = [self._make_agent("explore")]
        output = build_agent_selection_table(agents)
        assert "explore" in output
        assert "CHEAP" in output
        assert "Trigger Signals" in output

    def test_utility_agents_excluded(self):
        normal = self._make_agent("explore")
        utility = self._make_agent("utility_agent")
        utility.metadata.category = "utility"
        output = build_agent_selection_table([normal, utility])
        assert "explore" in output
        assert "utility_agent" not in output

    def test_empty_agents_still_returns_header(self):
        output = build_agent_selection_table([])
        assert "Available Agents" in output

    def test_default_flow_hint_present(self):
        output = build_agent_selection_table([])
        assert "Default flow" in output

    def test_triggers_rendered_when_available(self):
        agent = self._make_agent("explore")
        trigger = MagicMock()
        trigger.trigger = "Find Y"
        agent.metadata.triggers = [trigger]
        output = build_agent_selection_table([agent])
        assert "Find Y" in output


# ===========================================================================
# build_workflows_section
# ===========================================================================

class TestBuildWorkflowsSection:

    def _make_workflow(
        self,
        name: str = "test_wf",
        description: str = "A test workflow",
        path: str = "/tmp/test/workflow.json",
        source: str = "project",
    ) -> AvailableWorkflow:
        return AvailableWorkflow(name=name, description=description, path=path, source=source)

    def test_empty_returns_empty_string(self):
        assert build_workflows_section([]) == ""

    def test_workflow_name_in_output(self):
        wf = self._make_workflow(name="ndr_alert_triage")
        output = build_workflows_section([wf])
        assert "ndr_alert_triage" in output

    def test_workflow_description_in_output(self):
        wf = self._make_workflow(description="NDR 告警自动研判")
        output = build_workflows_section([wf])
        assert "NDR 告警自动研判" in output

    def test_project_scope_label(self):
        wf = self._make_workflow(source="project")
        output = build_workflows_section([wf])
        assert "project" in output

    def test_global_scope_label(self):
        wf = self._make_workflow(source="global")
        output = build_workflows_section([wf])
        assert "global" in output

    def test_project_workflows_before_global(self):
        project_wf = self._make_workflow(name="proj_wf", source="project")
        global_wf = self._make_workflow(name="glob_wf", source="global")
        output = build_workflows_section([global_wf, project_wf])
        proj_pos = output.index("proj_wf")
        glob_pos = output.index("glob_wf")
        assert proj_pos < glob_pos

    def test_multiple_workflows_all_present(self):
        workflows = [
            self._make_workflow(name=f"wf_{i}", source="project")
            for i in range(3)
        ]
        output = build_workflows_section(workflows)
        for i in range(3):
            assert f"wf_{i}" in output

    def test_run_workflow_usage_hint_present(self):
        wf = self._make_workflow()
        output = build_workflows_section([wf])
        assert "run_workflow" in output

    def test_section_header_present(self):
        wf = self._make_workflow()
        output = build_workflows_section([wf])
        assert "Available Workflows" in output

    def test_path_column_in_table(self):
        wf = self._make_workflow(path="/home/user/.flocks/workflow/ndr/workflow.json")
        output = build_workflows_section([wf])
        assert "/home/user/.flocks/workflow/ndr/workflow.json" in output

    def test_table_has_path_header(self):
        wf = self._make_workflow()
        output = build_workflows_section([wf])
        assert "Path" in output

    def test_multiline_description_uses_first_line_only(self):
        wf = self._make_workflow(description="First line.\nSecond line.")
        output = build_workflows_section([wf])
        assert "First line." in output
        # Second line should NOT bleed into the table cell
        lines_with_second = [l for l in output.splitlines() if "Second line." in l]
        assert len(lines_with_second) == 0
