from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from flocks.agent.agent_factory import scan_and_load
from flocks.skill.skill import Skill
from flocks.situation_report.product.orchestrator import ALLOWED_PRODUCT_MODELS
from flocks.tool.registry import ToolCategory, ToolRegistry


@pytest.mark.asyncio
async def test_product_agent_and_skill_have_only_phase_one_a1_capabilities():
    repo_root = Path(__file__).resolve().parents[2]
    agent_path = repo_root / ".flocks/plugins/agents/situation-report-product/agent.yaml"
    declared = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent = scan_and_load()["situation-report-product"]
    allowed = {
        "skill_load",
        "situation_product_context_read",
        "situation_product_material_read",
        "situation_product_source_read",
        "situation_product_report_write",
        "situation_product_report_validate",
    }

    assert agent.hidden is True
    assert agent.delegatable is False
    assert agent.options == {"strict_tools": True}
    assert set(agent.tools or []) == allowed
    assert set(declared["tools"]) == allowed
    assert agent.model.provider_id == "threatbook-cn-llm"
    assert agent.model.model_id == "bailian:deepseek-v4-pro"
    assert ALLOWED_PRODUCT_MODELS == {
        ("threatbook-cn-llm", "bailian:deepseek-v4-pro"),
        ("threatbook-cn-llm", "bailian:deepseek-v4-flash-0731"),
        ("anthropic", "claude-opus-4-6"),
        ("anthropic", "claude-opus-4-8"),
    }
    assert {
        "delegate_task",
        "run_workflow",
        "read",
        "write",
        "edit",
        "bash",
        "situation_draft_write",
        "situation_material_read",
    }.isdisjoint(allowed)
    for tool_name in allowed - {"skill_load"}:
        tool = ToolRegistry.get(tool_name)
        assert tool is not None
        assert tool.info.category == ToolCategory.SYSTEM

    skill = await Skill.get("situation-report-product")
    assert skill is not None and skill.source == "project"
    content = Path(skill.location).read_text(encoding="utf-8")
    assert "phase-one" in skill.description
    assert "Do not change templates, materials, language" in content
    assert "Stop after three validation attempts" in content
    assert "never expand every material by default" in content
    assert "selected-material detail" in content
    assert "authoritative writing specification" in content
    assert "never print it in report Markdown" in content
    assert "evidence_map" in content
    assert "Selection and ranking metadata is not event evidence" in content
    assert "deterministicCounts" in content
    assert "Keep every tool-returned" not in content
