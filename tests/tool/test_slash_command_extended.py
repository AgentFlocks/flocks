"""
tests/tool/test_slash_command_extended.py

单元测试：run_slash_command 工具（flocks.tool.system.slash_command）
- 仅暴露 Agent-safe 的 direct 命令
- tools/skills/workflows/help/mcp 走共享 direct 逻辑
- 非 Agent-safe 的命令不会进入 tool schema
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.agent.agent import AgentPromptMetadata, AvailableAgent
from flocks.command.help import format_help
from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.tool.system.slash_command import (
    build_run_slash_command_description,
    build_tools_catalog_summary,
    refresh_run_slash_command_metadata,
    run_slash_command_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-msg", agent="test")


def _make_tool_info(name: str, category_value: str, *, source: str | None = None) -> MagicMock:
    """Fake ToolRegistry list item with .name and .category.value."""
    item = MagicMock()
    item.name = name
    item.description = f"Description of {name}"
    item.category.value = category_value
    item.source = source
    item.enabled = True
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


def _make_available_agent(
    name: str,
    description: str,
    *,
    category: str = "exploration",
    cost: str = "CHEAP",
) -> AvailableAgent:
    return AvailableAgent(
        name=name,
        description=description,
        metadata=AgentPromptMetadata(category=category, cost=cost),
    )


# ===========================================================================
# metadata / schema
# ===========================================================================

class TestAgentSafeCommandMetadata:
    def test_tool_schema_only_contains_agent_safe_direct_commands(self):
        refresh_run_slash_command_metadata()
        tool = ToolRegistry.get("run_slash_command")

        assert tool is not None
        command_param = next(param for param in tool.info.parameters if param.name == "command")
        assert command_param.enum == ["help", "tools", "skills", "agents", "workflows", "mcp"]

    def test_tool_schema_excludes_non_direct_and_unsafe_commands(self):
        refresh_run_slash_command_metadata()
        tool = ToolRegistry.get("run_slash_command")

        assert tool is not None
        command_param = next(param for param in tool.info.parameters if param.name == "command")
        for forbidden in ["plan", "ask", "tasks", "queue", "init", "bug", "compact", "model", "status", "new", "clear"]:
            assert forbidden not in (command_param.enum or [])

    def test_description_is_built_from_registry(self):
        description = build_run_slash_command_description()

        assert "- help: Show available commands" in description
        assert "- tools: List available tools" in description
        assert "- agents: List available agents" in description
        assert "- mcp: Inspect or refresh MCP servers" in description
        assert "plan" not in description
        assert "compact" not in description


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
            _make_tool_info("skill_load", "system"),
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

    async def test_source_aware_grouping_splits_external_tools_out_of_custom(self):
        mock_tools = [
            _make_tool_info("tdp_event_list", "custom", source="device"),
            _make_tool_info("threatbook_ip_query", "custom", source="api"),
            _make_tool_info("read", "file"),
        ]
        with patch("flocks.tool.system.slash_command.ToolRegistry.list_tools", return_value=mock_tools), \
             patch("flocks.tool.system.slash_command.ToolRegistry.init"):
            result = await run_slash_command_tool(_make_ctx(), "tools")

        assert "**device**" in result.output
        assert "**api**" in result.output
        assert "- tdp_event_list: Description of tdp_event_list" in result.output
        assert "- threatbook_ip_query: Description of threatbook_ip_query" in result.output

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

    async def test_tools_info_requires_name(self):
        result = await run_slash_command_tool(_make_ctx(), "tools", arguments="info")
        assert not result.success
        assert "Usage: /tools [list|info `name`]" in result.error


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

    async def test_skills_refresh_is_not_agent_safe(self):
        result = await run_slash_command_tool(_make_ctx(), "skills", arguments="refresh")
        assert not result.success
        assert "read-only direct variants" in result.error


# ===========================================================================
# agents command
# ===========================================================================

class TestAgentsCommand:
    _LIST_AVAILABLE = "flocks.agent.registry.Agent.list_available_agents"

    async def test_returns_success_with_agents(self):
        agents = [_make_available_agent("explore", "Explore the codebase.")]
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=agents):
            result = await run_slash_command_tool(_make_ctx(), "agents")
        assert result.success

    async def test_agent_names_in_output(self):
        agents = [
            _make_available_agent("explore", "Explore the codebase."),
            _make_available_agent("oracle", "Answer deep questions.", category="advisor", cost="EXPENSIVE"),
        ]
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=agents):
            result = await run_slash_command_tool(_make_ctx(), "agents")
        assert "`explore`" in result.output
        assert "`oracle`" in result.output

    async def test_agent_metadata_in_output(self):
        agents = [_make_available_agent("oracle", "Answer deep questions.", category="advisor", cost="EXPENSIVE")]
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=agents):
            result = await run_slash_command_tool(_make_ctx(), "agents")
        assert "advisor" in result.output
        assert "EXPENSIVE" in result.output

    async def test_no_agents_returns_success_with_message(self):
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=[]):
            result = await run_slash_command_tool(_make_ctx(), "agents")
        assert result.success
        assert "No available agents." in result.output

    async def test_agents_reject_arguments(self):
        result = await run_slash_command_tool(_make_ctx(), "agents", arguments="extra")
        assert not result.success
        assert "Usage: /agents" in result.error


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
# mcp command
# ===========================================================================

class TestMcpCommand:
    _MCP_STATUS = "flocks.mcp.MCP.status"

    async def test_mcp_status_is_available(self):
        info = MagicMock()
        info.status.value = "connected"
        info.tools_count = 3
        info.resources_count = 1
        info.error = None

        with patch(self._MCP_STATUS, new_callable=AsyncMock, return_value={"demo": info}):
            result = await run_slash_command_tool(_make_ctx(), "mcp", arguments="status")

        assert result.success
        assert "MCP Server Status:" in result.output
        assert "demo" in result.output

    async def test_mcp_refresh_is_not_agent_safe(self):
        result = await run_slash_command_tool(_make_ctx(), "mcp", arguments="refresh demo")
        assert not result.success
        assert "read-only direct variants" in result.error


# ===========================================================================
# disallowed commands
# ===========================================================================

class TestDisallowedCommands:
    @pytest.mark.parametrize("cmd", ["tasks", "queue", "compact", "plan", "ask", "init", "bug", "clear"])
    async def test_non_agent_safe_commands_fail(self, cmd):
        result = await run_slash_command_tool(_make_ctx(), cmd)
        assert not result.success
        assert "Unknown agent-safe slash command" in result.error


# ===========================================================================
# help command
# ===========================================================================

class TestHelpCommand:

    async def test_help_lists_workflows_command(self):
        result = await run_slash_command_tool(_make_ctx(), "help")
        assert result.success
        assert "workflows" in result.output
        assert "agents" in result.output

    async def test_help_matches_shared_help_formatter(self):
        expected = format_help(surface="webui")
        result = await run_slash_command_tool(_make_ctx(), "help")

        assert result.success
        assert result.output == expected

    async def test_help_shows_full_webui_help(self):
        result = await run_slash_command_tool(_make_ctx(), "help")
        assert result.success
        assert "Direct commands:" in result.output
        assert "Other commands (handled through the normal assistant/session flow):" in result.output
        assert "/clear" in result.output
        assert "/bug" in result.output
        assert "/compact" in result.output
        assert "/model" not in result.output
