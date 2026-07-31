"""
tests/agent/test_agent_workflows.py

单元测试：
1. AvailableWorkflow 数据模型
2. inject_dynamic_prompts() 的 workflows 参数传递与 prompt 注入
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from flocks.agent.agent import AgentInfo, AvailableWorkflow
from flocks.agent.agent_factory import inject_dynamic_prompts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_with_builder(tmp_path: Path, inject_body: str) -> AgentInfo:
    """Write a minimal prompt_builder.py and return an AgentInfo pointing to it."""
    builder_path = tmp_path / "prompt_builder.py"
    builder_path.write_text(textwrap.dedent(inject_body), encoding="utf-8")
    # Derive module path from file path (simulate flocks package root = tmp_path.parent)
    rel = builder_path.relative_to(tmp_path.parent)
    module_path = str(rel.with_suffix("")).replace("/", ".")
    return AgentInfo(
        name="test_agent",
        mode="subagent",
        native=False,
        prompt_builder=f"{module_path}:inject",
    )


def _simple_workflows() -> List[AvailableWorkflow]:
    return [
        AvailableWorkflow(
            name="ndr_triage",
            description="NDR 告警自动研判",
            path="/tmp/.flocks/workflow/ndr_triage/workflow.json",
            source="project",
        ),
        AvailableWorkflow(
            name="global_scan",
            description="全局扫描工作流",
            path="/home/user/.flocks/workflow/global_scan/workflow.json",
            source="global",
        ),
    ]


# ===========================================================================
# AvailableWorkflow model
# ===========================================================================

class TestAvailableWorkflow:

    def test_basic_creation(self):
        wf = AvailableWorkflow(
            name="my_workflow",
            description="Does something useful",
            path="/tmp/wf/workflow.json",
        )
        assert wf.name == "my_workflow"
        assert wf.description == "Does something useful"
        assert wf.path == "/tmp/wf/workflow.json"

    def test_default_source_is_project(self):
        wf = AvailableWorkflow(name="wf", description="desc", path="/tmp/wf.json")
        assert wf.source == "project"

    def test_global_source_explicit(self):
        wf = AvailableWorkflow(name="wf", description="desc", path="/tmp/wf.json", source="global")
        assert wf.source == "global"

    def test_empty_description_allowed(self):
        wf = AvailableWorkflow(name="wf", description="", path="/tmp/wf.json")
        assert wf.description == ""

    def test_fields_accessible(self):
        wf = AvailableWorkflow(name="wf", description="desc", path="/p/wf.json", source="project")
        assert hasattr(wf, "name")
        assert hasattr(wf, "description")
        assert hasattr(wf, "path")
        assert hasattr(wf, "source")


# ===========================================================================
# inject_dynamic_prompts — workflows parameter
# ===========================================================================

class TestInjectDynamicPromptsWorkflows:

    def test_workflows_passed_to_inject_function(self, tmp_path):
        """inject() receives the workflows list."""
        received: list = []
        builder_code = """
def inject(agent_info, available_agents, tools, skills, workflows=None):
    agent_info.prompt = "injected"
    import builtins
    builtins._test_received_workflows = workflows
"""
        builder_path = tmp_path / "cap_builder.py"
        builder_path.write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="cap_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.cap_builder:inject",
            )
            workflows = _simple_workflows()
            inject_dynamic_prompts({"cap_agent": agent}, [], [], [], workflows)
            import builtins
            result = getattr(builtins, "_test_received_workflows", None)
            assert result is not None
            assert len(result) == 2
            assert result[0].name == "ndr_triage"
        finally:
            sys.path.pop(0)
            import builtins
            if hasattr(builtins, "_test_received_workflows"):
                delattr(builtins, "_test_received_workflows")

    def test_workflows_defaults_to_empty_list_when_none(self, tmp_path):
        """inject() receives [] when workflows=None."""
        builder_code = """
def inject(agent_info, available_agents, tools, skills, workflows=None):
    agent_info.prompt = repr(workflows)
"""
        builder_path = tmp_path / "none_builder.py"
        builder_path.write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="none_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.none_builder:inject",
            )
            inject_dynamic_prompts({"none_agent": agent}, [], [], [], None)
            assert agent.prompt == "[]"
        finally:
            sys.path.pop(0)

    def test_legacy_builder_receives_empty_categories_and_workflows(self, tmp_path):
        """Legacy inject() signatures keep workflows in the correct argument."""
        builder_code = """
def inject(agent_info, available_agents, tools, skills, categories, workflows=None):
    workflow_names = [workflow.name for workflow in (workflows or [])]
    agent_info.prompt = repr((categories, workflow_names))
"""
        builder_path = tmp_path / "legacy_builder.py"
        builder_path.write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="legacy_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.legacy_builder:inject",
            )
            inject_dynamic_prompts(
                {"legacy_agent": agent},
                [],
                [],
                [],
                _simple_workflows(),
            )
            assert agent.prompt == "([], ['ndr_triage', 'global_scan'])"
        finally:
            sys.path.pop(0)

    def test_inject_sets_prompt_on_agent(self, tmp_path):
        """inject() is expected to set agent_info.prompt."""
        builder_code = """
def inject(agent_info, available_agents, tools, skills, workflows=None):
    wf_names = ", ".join(w.name for w in (workflows or []))
    agent_info.prompt = f"I know these workflows: {wf_names}"
"""
        builder_path = tmp_path / "prompt_builder.py"
        builder_path.write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="wf_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.prompt_builder:inject",
            )
            workflows = _simple_workflows()
            inject_dynamic_prompts({"wf_agent": agent}, [], [], [], workflows)
            assert "ndr_triage" in agent.prompt
            assert "global_scan" in agent.prompt
        finally:
            sys.path.pop(0)

    def test_agent_without_builder_unaffected(self):
        """Agents without prompt_builder are silently skipped."""
        agent = AgentInfo(
            name="static_agent",
            mode="subagent",
            native=False,
            prompt="Static prompt",
        )
        inject_dynamic_prompts({"static_agent": agent}, [], [], [], _simple_workflows())
        # Prompt should remain unchanged
        assert agent.prompt == "Static prompt"

    def test_builder_error_logged_not_raised(self, tmp_path):
        """A broken inject function logs the error but does not raise."""
        builder_code = """
def inject(agent_info, available_agents, tools, skills, workflows=None):
    raise RuntimeError("inject exploded")
"""
        builder_path = tmp_path / "broken_builder.py"
        builder_path.write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agent = AgentInfo(
                name="broken_agent",
                mode="subagent",
                native=False,
                prompt_builder=f"{tmp_path.name}.broken_builder:inject",
            )
            # Must not raise
            inject_dynamic_prompts({"broken_agent": agent}, [], [], [], [])
        finally:
            sys.path.pop(0)

    def test_multiple_agents_all_injected(self, tmp_path):
        """All agents with a builder receive the workflows."""
        builder_code = """
def inject(agent_info, available_agents, tools, skills, workflows=None):
    agent_info.prompt = str(len(workflows or []))
"""
        for i in range(3):
            (tmp_path / f"builder_{i}.py").write_text(textwrap.dedent(builder_code), encoding="utf-8")

        import sys
        sys.path.insert(0, str(tmp_path.parent))
        try:
            agents = {
                f"agent_{i}": AgentInfo(
                    name=f"agent_{i}",
                    mode="subagent",
                    native=False,
                    prompt_builder=f"{tmp_path.name}.builder_{i}:inject",
                )
                for i in range(3)
            }
            inject_dynamic_prompts(agents, [], [], [], _simple_workflows())
            for agent in agents.values():
                assert agent.prompt == "2"  # 2 workflows in _simple_workflows()
        finally:
            sys.path.pop(0)
