"""
tests/integration/test_capability_awareness.py

集成测试：Flocks 能力自我认知全链路
验证上一轮 capability-awareness 改造的端到端效果：

1. 工具分组可见性
   - ToolRegistry 中所有已注册工具都会出现在 categorize_tools() 结果中
   - _format_tools_for_prompt() 将全部工具按 ToolCategory 分组后完整输出
   - 没有任何工具被静默丢弃

2. Workflow 注入全链路（使用临时文件模拟真实环境）
   - scan_skill_workflows() 正确发现 .flocks/workflow 目录中的 workflow.json
   - build_workflows_section() 能把它渲染成 prompt 片段
   - inject_dynamic_prompts() 将 AvailableWorkflow 传递给 prompt builder

3. Rex prompt 包含 workflow 段落
   - Rex agent 加载后，其 prompt 包含 "Available Workflows" 字样（前提：存在工作流）

4. /skills slash command 端到端（真实 Skill 扫描）
   - run_slash_command_tool("skills") 成功返回技能列表，不报错

5. /workflows slash command 端到端（mock scan）
   - run_slash_command_tool("workflows") 在有工作流时返回结构化输出
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.agent.agent import AvailableWorkflow
from flocks.agent.prompt_utils import (
    _format_tools_for_prompt,
    build_workflows_section,
    categorize_tools,
)
from flocks.tool.registry import ToolContext, ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_entry(name: str, category_value: str) -> MagicMock:
    entry = MagicMock()
    entry.name = name
    entry.info.category.value = category_value
    entry.category.value = category_value
    entry.description = f"{name} description"
    return entry


def _workflow_entry(**kwargs) -> dict:
    base = {
        "name": "test_wf",
        "description": "A test workflow",
        "workflowPath": "/tmp/test/workflow.json",
        "sourceType": "project",
        "publishStatus": "unpublished",
    }
    base.update(kwargs)
    return base


# ===========================================================================
# 1. 工具分组可见性
# ===========================================================================

@pytest.mark.integration
class TestToolVisibility:

    def test_all_registered_tools_appear_in_categorize_result(self):
        """所有 ToolRegistry 注册工具都应出现在 categorize_tools 结果中。"""
        ToolRegistry.init()
        all_tools = ToolRegistry.list_tools()
        tool_names = [t.name for t in all_tools]

        if not tool_names:
            pytest.skip("No tools registered – skipping visibility test")

        result = categorize_tools(tool_names)
        result_names = {t.name for t in result}

        missing = set(tool_names) - result_names
        assert not missing, f"Tools missing from categorize_tools result: {missing}"

    def test_no_tool_silently_dropped_in_format(self):
        """_format_tools_for_prompt 不得静默丢弃任何工具。"""
        ToolRegistry.init()
        all_tools = ToolRegistry.list_tools()
        if not all_tools:
            pytest.skip("No tools registered")

        tool_names = [t.name for t in all_tools]
        categorized = categorize_tools(tool_names)
        output = _format_tools_for_prompt(categorized)

        for tool in all_tools:
            assert tool.name in output, (
                f"Tool '{tool.name}' (category={ToolRegistry.get(tool.name).info.category.value if ToolRegistry.get(tool.name) else '?'}) "
                f"missing from _format_tools_for_prompt output"
            )

    def test_tools_grouped_by_real_categories(self):
        """输出中应包含已注册 ToolCategory 对应的标题行。"""
        from flocks.agent.prompt_utils import _CATEGORY_LABELS
        ToolRegistry.init()
        all_tools = ToolRegistry.list_tools()
        if not all_tools:
            pytest.skip("No tools registered")

        tool_names = [t.name for t in all_tools]
        categorized = categorize_tools(tool_names)
        output = _format_tools_for_prompt(categorized)

        actual_categories = {t.category for t in categorized}
        for cat in actual_categories:
            # Use the same label lookup logic as prompt_utils._format_tools_for_prompt
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            assert f"**{label}**" in output, (
                f"Category '{cat}' (label='{label}') has tools but no group header in output"
            )

    def test_mock_tool_set_all_categories_visible(self):
        """使用 mock 工具集验证六大 category 全部出现在输出中。"""
        tool_map = {
            "read": _make_tool_entry("read", "file"),
            "bash": _make_tool_entry("bash", "terminal"),
            "grep": _make_tool_entry("grep", "search"),
            "webfetch": _make_tool_entry("webfetch", "browser"),
            "skill": _make_tool_entry("skill", "system"),
        }
        with patch("flocks.tool.registry.ToolRegistry.get", side_effect=lambda n: tool_map.get(n)), \
             patch("flocks.tool.registry.ToolRegistry.init"):
            categorized = categorize_tools(list(tool_map.keys()))
        output = _format_tools_for_prompt(categorized)

        for expected_cat in ["file", "terminal", "search", "browser", "system"]:
            assert expected_cat in output.lower(), f"Category '{expected_cat}' missing from output"


# ===========================================================================
# 2. Workflow 注入全链路（临时文件）
# ===========================================================================

@pytest.mark.integration
class TestWorkflowInjectionPipeline:

    @pytest.fixture
    def temp_workflow_dir(self, tmp_path: Path):
        """Create a fake .flocks/workflow/ndr_triage directory with workflow.json."""
        wf_dir = tmp_path / ".flocks" / "workflow" / "ndr_triage"
        wf_dir.mkdir(parents=True)
        # Workflow model requires `start` pointing to a node id, and that node must exist
        workflow_json = {
            "name": "ndr_triage",
            "description": "NDR 告警自动研判",
            "start": "step_1",
            "nodes": [{"id": "step_1", "type": "python", "code": "result = {'output': 'ok'}"}],
            "edges": [],
        }
        (wf_dir / "workflow.json").write_text(json.dumps(workflow_json), encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_scan_discovers_workflow_json(self, temp_workflow_dir):
        """scan_skill_workflows(base_dir) 发现 workflow.json 后返回 name。

        base_dir 是项目根（包含 .flocks/），而非 .flocks/ 本身。
        """
        from flocks.workflow.center import scan_skill_workflows

        # Pass base_dir = tmp_path so that scan finds <base_dir>/.flocks/workflow/ndr_triage/
        entries = await scan_skill_workflows(base_dir=temp_workflow_dir)
        names = [e.get("name") for e in entries]
        assert "ndr_triage" in names

    @pytest.mark.asyncio
    async def test_workflow_description_passed_through(self, temp_workflow_dir):
        """description 字段从 workflow.json 正确传递到 scan 结果。"""
        from flocks.workflow.center import scan_skill_workflows

        entries = await scan_skill_workflows(base_dir=temp_workflow_dir)
        entry = next((e for e in entries if e.get("name") == "ndr_triage"), None)
        assert entry is not None
        assert "NDR" in (entry.get("description") or "")

    def test_build_workflows_section_with_real_objects(self):
        """AvailableWorkflow 对象 → build_workflows_section 渲染正确。"""
        workflows = [
            AvailableWorkflow(
                name="ndr_triage",
                description="NDR 告警自动研判",
                path="/tmp/.flocks/workflow/ndr_triage/workflow.json",
                source="project",
            ),
            AvailableWorkflow(
                name="host_compromise",
                description="主机入侵研判",
                path="/tmp/.flocks/workflow/host_compromise/workflow.json",
                source="global",
            ),
        ]
        section = build_workflows_section(workflows)
        assert "ndr_triage" in section
        assert "host_compromise" in section
        assert "NDR 告警自动研判" in section
        assert "主机入侵研判" in section
        assert "project" in section
        assert "global" in section
        assert "run_workflow" in section

    def test_workflow_section_injected_into_agent_prompt(self, tmp_path):
        """inject_dynamic_prompts 将 workflows 信息注入 agent prompt。"""
        from flocks.agent.agent_factory import inject_dynamic_prompts
        from flocks.agent.agent import AgentInfo
        import textwrap

        builder_code = textwrap.dedent("""
        from flocks.agent.prompt_utils import build_workflows_section
        def inject(agent_info, available_agents, tools, skills, categories, workflows=None):
            section = build_workflows_section(workflows or [])
            agent_info.prompt = f"## Capability Context\\n{section}"
        """)
        builder_path = tmp_path / "wf_inject_builder.py"
        builder_path.write_text(builder_code, encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="wf_test_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.wf_inject_builder:inject",
            )
            workflows = [
                AvailableWorkflow(
                    name="my_wf",
                    description="My integration workflow",
                    path="/tmp/wf.json",
                    source="project",
                )
            ]
            inject_dynamic_prompts({"wf_test_agent": agent}, [], [], [], [], workflows)
            assert "my_wf" in agent.prompt
            assert "My integration workflow" in agent.prompt
        finally:
            sys.path.pop(0)


# ===========================================================================
# 3. Rex prompt 包含 workflow 段落（存在工作流时）
# ===========================================================================

@pytest.mark.integration
class TestRexPromptWorkflowAwareness:

    @pytest.mark.asyncio
    async def test_rex_prompt_contains_tools_section(self):
        """Rex prompt 必须包含工具信息（基础能力感知）。"""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        assert rex is not None
        assert rex.prompt is not None
        assert len(rex.prompt) > 100

    @pytest.mark.asyncio
    async def test_rex_prompt_contains_subagents_section(self):
        """Rex prompt 必须包含 SubAgent 信息。"""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        assert rex is not None
        # Rex should know about at least one of its common subagents
        prompt = rex.prompt or ""
        assert any(name in prompt for name in ["explore", "oracle", "metis", "momus"]), (
            "Rex prompt does not reference any known subagents"
        )

    @pytest.mark.asyncio
    async def test_rex_prompt_contains_skills_section(self):
        """Rex prompt 必须包含 skills 相关信息。"""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        prompt = (rex.prompt or "").lower()
        assert "skill" in prompt

    @pytest.mark.asyncio
    async def test_rex_prompt_has_workflow_placeholder_or_section(self):
        """Rex prompt 应包含 workflow 相关内容（已注入或有占位结构）。"""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        prompt = (rex.prompt or "").lower()
        # Either the section was injected or the 'run_workflow' tool is mentioned
        assert "workflow" in prompt or "run_workflow" in prompt

    @pytest.mark.asyncio
    async def test_rex_prompt_prefers_direct_ioc_lookup_before_delegation(self):
        """单 IOC 情报查询应在提示词中优先走 Rex 直查路径。"""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        prompt = rex.prompt or ""
        assert "Single IOC basic lookup only" in prompt
        assert '"查询 8.8.8.8 的情报" -> Rex should directly query TI tools' in prompt
        assert "tool_search` if needed -> direct TI query tool -> answer" in prompt


# ===========================================================================
# 4. /skills slash command 端到端（真实 Skill 扫描）
# ===========================================================================

@pytest.mark.integration
class TestSkillsCommandE2E:

    @pytest.mark.asyncio
    async def test_skills_command_does_not_error(self):
        """run_slash_command('skills') 不应返回错误。"""
        from flocks.tool.system.slash_command import run_slash_command_tool
        ctx = ToolContext(session_id="it-sess", message_id="it-msg", agent="test")
        result = await run_slash_command_tool(ctx, "skills")
        assert result.success, f"skills command failed: {result.error}"

    @pytest.mark.asyncio
    async def test_skills_command_returns_string_output(self):
        """/skills 输出必须是非空字符串。"""
        from flocks.tool.system.slash_command import run_slash_command_tool
        ctx = ToolContext(session_id="it-sess", message_id="it-msg", agent="test")
        result = await run_slash_command_tool(ctx, "skills")
        assert isinstance(result.output, str)
        assert len(result.output) > 0


# ===========================================================================
# 5. /workflows slash command 端到端（mock scan）
# ===========================================================================

@pytest.mark.integration
class TestWorkflowsCommandE2E:

    @pytest.mark.asyncio
    async def test_workflows_command_with_mock_scan(self):
        """mock scan_skill_workflows 后，/workflows 输出完整结构化内容。"""
        from flocks.tool.system.slash_command import run_slash_command_tool
        ctx = ToolContext(session_id="it-sess", message_id="it-msg", agent="test")

        entries = [
            _workflow_entry(name="ndr_triage", description="NDR 告警研判", sourceType="project"),
            _workflow_entry(name="host_scan", description="主机扫描", sourceType="global"),
        ]
        with patch(
            "flocks.workflow.center.scan_skill_workflows",
            new_callable=AsyncMock,
            return_value=entries,
        ):
            result = await run_slash_command_tool(ctx, "workflows")

        assert result.success
        assert "ndr_triage" in result.output
        assert "host_scan" in result.output
        assert "NDR 告警研判" in result.output
        assert "run_workflow" in result.output

    @pytest.mark.asyncio
    async def test_workflows_command_real_scan_does_not_crash(self):
        """真实 scan_skill_workflows 调用不应 crash（无工作流环境下静默返回空列表）。"""
        from flocks.tool.system.slash_command import run_slash_command_tool
        ctx = ToolContext(session_id="it-sess", message_id="it-msg", agent="test")
        result = await run_slash_command_tool(ctx, "workflows")
        # Should succeed regardless of whether workflows exist
        assert result.success
