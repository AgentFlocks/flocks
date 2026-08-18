from __future__ import annotations

from types import SimpleNamespace

from flocks.agent.registry import Agent
from flocks.tool.registry import Tool, ToolRegistry, ToolResult

from flocks_code_security.agents import AGENT_TOOLS, register_agents
from flocks_code_security.projection import code_security_tool_projection
from flocks_code_security.tools import register_tools
from flocks_code_security.tools import RULESET_DIGEST, _ruleset_digest


async def _replacement_handler(_ctx, **_kwargs) -> ToolResult:
    return ToolResult(success=True)


def test_projection_only_reduces_code_security_agents() -> None:
    register_tools()
    audit_prepare = ToolRegistry.get("audit_prepare").info
    question = ToolRegistry.get("question").info
    tools = [
        audit_prepare,
        question,
        SimpleNamespace(name="bash"),
        SimpleNamespace(name="tool_search"),
    ]

    coordinator = code_security_tool_projection(tools, {"agent": "code-security"})
    ordinary = code_security_tool_projection(tools, {"agent": "rex"})

    assert [tool.name for tool in coordinator] == ["audit_prepare", "question"]
    assert ordinary == tools


def test_projection_rejects_same_name_replacement_tool() -> None:
    register_tools()
    original = ToolRegistry.get("audit_prepare")
    replacement = Tool(
        info=original.info,
        handler=_replacement_handler,
    )
    ToolRegistry.register(replacement)

    try:
        coordinator = code_security_tool_projection(
            [replacement.info],
            {"agent": "code-security"},
        )
    finally:
        ToolRegistry.register(original)

    assert coordinator == []


def test_projection_rejects_replaced_question_handler() -> None:
    register_tools()
    original = ToolRegistry.get("question")
    replacement = Tool(
        info=original.info,
        handler=_replacement_handler,
    )
    ToolRegistry.register(replacement)

    try:
        coordinator = code_security_tool_projection(
            [replacement.info],
            {"agent": "code-security"},
        )
    finally:
        ToolRegistry.register(original)

    assert coordinator == []


def test_agents_are_static_hidden_and_non_delegatable() -> None:
    register_agents()

    coordinator = Agent._custom_agents["code-security"]
    baseline = Agent._custom_agents["code-security-baseline"]

    assert coordinator.mode == "primary"
    assert coordinator.hidden is False
    assert coordinator.delegatable is False
    assert coordinator.prompt_builder is None
    assert coordinator.memory_enabled is False
    assert coordinator.require_dedicated_session is True
    assert coordinator.session_directory
    assert coordinator.tools == AGENT_TOOLS["code-security"]
    assert baseline.hidden is True
    assert baseline.delegatable is False
    assert "skill_load" not in baseline.tools
    assert "tool_search" not in baseline.tools
    assert "delegate_task" not in baseline.tools
    assert baseline.memory_enabled is False


def test_all_audit_tools_register() -> None:
    register_tools()

    expected = {name for names in AGENT_TOOLS.values() for name in names if name.startswith("audit_")}
    assert expected
    assert all(ToolRegistry.get(name) is not None for name in expected)
    for name in expected:
        parameter_names = [
            parameter.name for parameter in ToolRegistry.get(name).info.parameters
        ]
        assert len(parameter_names) == len(set(parameter_names))
    run_workers = ToolRegistry.get("audit_run_workers")
    phase = next(
        parameter
        for parameter in run_workers.info.parameters
        if parameter.name == "phase"
    )
    assert phase.enum == ["baseline", "verification"]


def test_ruleset_digest_is_derived_from_packaged_rules() -> None:
    assert RULESET_DIGEST == _ruleset_digest()
    assert len(RULESET_DIGEST) == 64
