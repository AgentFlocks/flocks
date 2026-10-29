"""Agent understanding shares guidance without sharing host capabilities."""

from unittest.mock import AsyncMock

import pytest

from flocks.agent.agent import AgentInfo
from flocks.agent.agents.rex.prompt_builder import build_dynamic_rex_prompt
from flocks.agent.interaction_guidance import build_interaction_guidance
from flocks.provider.provider import ChatMessage
from flocks.session.callable_schema import (
    register_callable_tool_resolver,
    unregister_callable_tool_resolver,
)
from flocks.session.callable_state import set_session_callable_tools
from flocks.session.prompt import SessionPrompt, TurnPromptContext
from flocks.session.runner import SessionRunner
from flocks.tool.registry import ToolCategory, ToolContext, ToolInfo
from flocks.tool.system.tool_search import tool_search


async def test_shared_understanding_does_not_import_host_context(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("TARGET REPOSITORY INSTRUCTION", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("TARGET HOST SECRET", encoding="utf-8")
    custom = AsyncMock(side_effect=AssertionError("isolated agents must not load workspace rules"))
    monkeypatch.setattr("flocks.session.prompt.SystemPrompt.custom", custom)
    blocks = await SessionPrompt.build_system_prompt_blocks(
        session_id="isolated-guidance", session_directory=str(tmp_path),
        agent_name="reviewer", agent_prompt="Review the supplied scope.",
        prompt_profile="isolated", provider_id="fake", model_id="fake",
        memory_bootstrap_data={"instructions": "HOST MEMORY SECRET"},
        turn_context=TurnPromptContext(
            additional_context="HOST CONTEXT SECRET", tool_catalog="HOST TOOLS SECRET",
            config_instructions=("HOST CONFIG SECRET",),
        ),
    )
    prompt = "\n".join(block.content for block in blocks)
    guidance = build_interaction_guidance()
    assert guidance in prompt
    assert guidance in build_dynamic_rex_prompt([], [], [], [])
    assert "SECRET" not in prompt
    assert "TARGET REPOSITORY INSTRUCTION" not in prompt
    assert "`tool_search`" not in prompt
    custom.assert_not_awaited()


@pytest.mark.parametrize("query", ["", "select:read,blocked,hook_hidden,undeclared", "blocked", "undeclared"])
async def test_discovery_intersects_declaration_projection_and_final_turn(query, monkeypatch):
    tools = [ToolInfo(name=name, description=name, category=ToolCategory.FILE) for name in
             ("read", "blocked", "hook_hidden", "undeclared", "tool_search")]
    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.list_tools", lambda: tools)
    agent = AgentInfo(name="reviewer", prompt_profile="isolated",
                      tools=["read", "blocked", "hook_hidden", "tool_search"])
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=agent))
    # Simulate stale/tampered callable state containing an undeclared tool.
    await set_session_callable_tools("isolated-search", [tool.name for tool in tools])
    recorded = []

    def restrict(candidates, context):
        recorded.append(context)
        # A resolver cannot expand the declaration ceiling either.
        return [tool for tool in candidates if tool.name != "blocked"] + [tools[3]]

    register_callable_tool_resolver("isolated-search-test", restrict)
    add = AsyncMock()
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add)
    device_hints = AsyncMock(side_effect=AssertionError("host devices must not load"))
    monkeypatch.setattr("flocks.tool.system.tool_search._build_device_tool_hints", device_hints)
    try:
        ctx = ToolContext("isolated-search", "message", agent="reviewer",
                          extra={"turn_callable_tool_names": ["read", "tool_search"]})
        result = await tool_search(ctx, query=query, limit=20)
    finally:
        unregister_callable_tool_resolver("isolated-search-test")
    names = result.output["callableToolNames"]
    assert set(names) <= {"read", "tool_search"}
    if query.startswith("select:"):
        assert names == ["read"]
    if query in {"blocked", "undeclared"}:
        assert names == []
    assert recorded[0]["agent"] == "reviewer"
    assert "undeclared" not in {tool["name"] for tool in recorded[0]["candidates"]}
    add.assert_awaited_once_with("isolated-search", names)
    device_hints.assert_not_awaited()


async def test_empty_discovery_ceiling_does_not_fall_back_to_global_catalog(monkeypatch):
    tools = [ToolInfo(name="host_secret", description="host secret", category=ToolCategory.FILE)]
    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(
        return_value=AgentInfo(name="reviewer", prompt_profile="isolated", tools=[])))
    result = await tool_search(ToolContext("empty-search", "message", agent="reviewer"), query="")
    assert result.output["matches"] == []
    assert result.output["callableToolNames"] == []


async def test_discovery_fails_closed_for_missing_agent(monkeypatch):
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=None))
    add = AsyncMock()
    monkeypatch.setattr("flocks.tool.system.tool_search.add_session_callable_tools", add)
    with pytest.raises(ValueError, match="available agent"):
        await tool_search(ToolContext("missing-agent", "message", agent="missing"), query="")
    add.assert_not_awaited()


@pytest.mark.parametrize("block_content", [False, True])
def test_catalog_uses_final_schemas_without_mutating_history(block_content):
    system = [{"type": "text", "text": "Review scope"}] if block_content else "Review scope"
    messages = [ChatMessage(role="system", content=system), ChatMessage(role="user", content="继续")]
    agent = AgentInfo(name="reviewer", prompt_profile="isolated", tools=["read", "blocked", "tool_search"])
    schemas = [{"type": "function", "function": {"name": name, "description": name}}
               for name in ("read", "tool_search", "undeclared")]
    updated = SessionRunner._with_isolated_tool_catalog(messages, agent, schemas)
    rendered = str(updated[0].content)
    assert "`read`" in rendered
    assert "`tool_search`" in rendered
    assert "blocked" not in rendered
    assert "undeclared" not in rendered
    assert "Current Callable Tools" not in str(messages[0].content)
    assert updated[1] is messages[1]
    assert SessionRunner._with_isolated_tool_catalog(messages, AgentInfo(name="rex"), schemas) is messages
