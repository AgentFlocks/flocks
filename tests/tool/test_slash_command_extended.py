"""
tests/tool/test_slash_command_extended.py

单元测试：run_slash_command 工具（flocks.tool.system.slash_command）
- tools    命令：按 ToolCategory 分组显示
- skills   命令：不再是 ui_only，返回完整技能列表
- workflows命令：返回已发现的 workflow 列表
- 错误情况：workflows scan 失败时返回 error
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.system.slash_command import build_tools_catalog_summary, run_slash_command_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-msg", agent="test")


def _make_tool_info(name: str, category_value: str) -> MagicMock:
    """Fake ToolRegistry list item with .name and .category.value."""
    item = MagicMock()
    item.name = name
    item.description = f"Description of {name}"
    item.category.value = category_value
    return item


def _make_skill(name: str, description: str) -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = description
    return skill


def _make_workflow_entry(name: str, description: str = "", path: str = "", source: str = "project") -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "workflowPath": path,
        "sourceType": source,
        "publishStatus": "unpublished",
    }


# ===========================================================================
# tools command
# ===========================================================================

class TestToolsCommand:

    @pytest.fixture
    def mock_tools(self):
        return [
            _make_tool_info("read", "file"),
            _make_tool_info("write", "file"),
            _make_tool_info("bash", "terminal"),
            _make_tool_info("grep", "search"),
            _make_tool_info("webfetch", "browser"),
            _make_tool_info("skill", "system"),
        ]

    async def test_returns_success(self, mock_tools):
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=mock_tools), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")
        assert result.success

    async def test_groups_by_category(self, mock_tools):
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=mock_tools), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")
        assert "file" in result.output.lower()
        assert "terminal" in result.output.lower()
        assert "search" in result.output.lower()

    async def test_all_tool_names_present(self, mock_tools):
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=mock_tools), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")
        for tool in mock_tools:
            assert tool.name in result.output

    async def test_tool_descriptions_present(self, mock_tools):
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=mock_tools), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")
        assert "Description of read" in result.output
        assert "Description of bash" in result.output

    async def test_catalog_truncates_descriptions(self):
        long_tool = _make_tool_info("websearch", "browser")
        long_tool.description = "x" * 140
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=[long_tool]), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            output = build_tools_catalog_summary(max_description_chars=100, include_tip=False)
        assert "- websearch: " in output
        assert "x" * 101 not in output
        assert "..." in output

    async def test_empty_registry_no_crash(self):
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=[]), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")
        assert result.success


# ===========================================================================
# skills command — no longer ui_only
# ===========================================================================

class TestSkillsCommand:
    # Skill.all is a classmethod on flocks.skill.skill.Skill (lazy-imported inside the tool)
    _SKILL_ALL = "flocks.skill.skill.Skill.all"

    async def test_returns_success(self):
        skills = [_make_skill("tool-builder", "Build plugin tools"), _make_skill("host-compromise", "Investigate host")]
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=skills):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        assert result.success

    async def test_skill_names_in_output(self):
        skills = [_make_skill("tool-builder", "Build plugin tools")]
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=skills):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        assert "tool-builder" in result.output

    async def test_skill_descriptions_in_output(self):
        skills = [_make_skill("my-skill", "Specialized expertise for X")]
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=skills):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        assert "Specialized expertise for X" in result.output

    async def test_no_skills_returns_success_with_message(self):
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=[]):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        assert result.success
        assert "No skills" in result.output

    async def test_not_ui_only_response(self):
        """Calling skills must NOT return the old 'Use /skills in the UI' message."""
        skills = [_make_skill("some-skill", "desc")]
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=skills):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        assert "Use /skills in the UI" not in result.output

    async def test_multiple_skills_all_listed(self):
        skills = [_make_skill(f"skill-{i}", f"Desc {i}") for i in range(5)]
        with patch(self._SKILL_ALL, new_callable=AsyncMock, return_value=skills):
            result = await run_slash_command_tool(_make_ctx(), "skills")
        for i in range(5):
            assert f"skill-{i}" in result.output


# ===========================================================================
# workflows command
# ===========================================================================

class TestWorkflowsCommand:
    # scan_skill_workflows is a lazy import inside the tool function
    _SCAN_WF = "flocks.workflow.center.scan_skill_workflows"

    async def test_returns_success_with_workflows(self):
        entries = [
            _make_workflow_entry("ndr_triage", "NDR alert analysis", "/tmp/wf/workflow.json"),
        ]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert result.success

    async def test_workflow_names_in_output(self):
        entries = [
            _make_workflow_entry("ndr_triage"),
            _make_workflow_entry("global_scan"),
        ]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert "ndr_triage" in result.output
        assert "global_scan" in result.output

    async def test_workflow_descriptions_in_output(self):
        entries = [_make_workflow_entry("wf", "Automated security analysis")]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert "Automated security analysis" in result.output

    async def test_workflow_paths_in_output(self):
        entries = [_make_workflow_entry("wf", path="/tmp/.flocks/workflow/wf/workflow.json")]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert "/tmp/.flocks/workflow/wf/workflow.json" in result.output

    async def test_source_type_in_output(self):
        entries = [
            _make_workflow_entry("proj_wf", source="project"),
            _make_workflow_entry("glob_wf", source="global"),
        ]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert "project" in result.output
        assert "global" in result.output

    async def test_no_workflows_returns_success_with_message(self):
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=[]):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert result.success
        assert "No workflows" in result.output

    async def test_scan_failure_returns_error(self):
        with patch(self._SCAN_WF, new_callable=AsyncMock, side_effect=RuntimeError("disk error")):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert not result.success
        assert "disk error" in result.error

    async def test_usage_hint_in_output(self):
        entries = [_make_workflow_entry("wf")]
        with patch(self._SCAN_WF, new_callable=AsyncMock, return_value=entries):
            result = await run_slash_command_tool(_make_ctx(), "workflows")
        assert "run_workflow" in result.output


# ===========================================================================
# ui_only commands still redirect
# ===========================================================================

class TestUiOnlyCommandsUnchanged:

    @pytest.mark.parametrize("cmd", ["tasks", "queue", "compact", "plan", "ask"])
    async def test_ui_only_returns_success_with_hint(self, cmd):
        result = await run_slash_command_tool(_make_ctx(), cmd)
        assert result.success
        assert "UI" in result.output or "in the UI" in result.output


# ===========================================================================
# help command
# ===========================================================================

class TestHelpCommand:

    async def test_help_lists_workflows_command(self):
        result = await run_slash_command_tool(_make_ctx(), "help")
        assert result.success
        assert "workflows" in result.output
