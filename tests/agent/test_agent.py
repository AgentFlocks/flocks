"""
Agent system tests

Tests for Agent definitions, permissions, prompts, and registry operations.
Reflects the current architecture: built-in agents loaded from YAML folders,
no permission_compat helpers, compaction/title/summary live in session/prompts.py.
"""

import pytest
from flocks.agent import Agent, AgentInfo, AgentModel, PROMPT_COMPACTION, PROMPT_TITLE, PROMPT_SUMMARY
from flocks.session.prompt_strings import PROMPT_COMPACTION, PROMPT_TITLE, PROMPT_SUMMARY


# =============================================================================
# Agent Definition Tests
# =============================================================================

BUILTIN_AGENTS = [
    "rex", "hephaestus", "explore",
    "oracle", "librarian", "prometheus", "multimodal-looker",
    "rex-junior", "self-improve", "host-forensics", "host-forensics-fast",
]


class TestAgentDefinitions:
    """Test that all built-in agents load correctly from YAML."""

    @pytest.mark.asyncio
    async def test_all_builtin_agents_exist(self):
        for name in BUILTIN_AGENTS:
            agent = await Agent.get(name)
            assert agent is not None, f"Agent '{name}' should exist"
            assert agent.name == name

    @pytest.mark.asyncio
    async def test_agent_count(self):
        agents = await Agent.list()
        assert len(agents) >= 11, f"Should have at least 11 agents, got {len(agents)}"

    @pytest.mark.asyncio
    async def test_no_legacy_agents(self):
        """Retired agents stay absent; planning is provided by Prometheus."""
        for name in ["general", "plan", "compaction", "title", "summary"]:
            agent = await Agent.get(name)
            assert agent is None, f"Legacy agent '{name}' should not exist"


class TestPrimaryAgents:

    @pytest.mark.asyncio
    async def test_rex_agent(self):
        agent = await Agent.get("rex")
        assert agent is not None
        assert agent.mode == "primary"
        assert agent.native is True
        assert agent.hidden is False
        assert agent.delegatable is False


class TestInternalAgents:

    @pytest.mark.asyncio
    async def test_self_improve_agent(self):
        """The Dream worker is an internal agent, not a delegation target."""
        agent = await Agent.get("self-improve")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.native is True
        assert agent.hidden is True
        assert agent.delegatable is False
        assert "evolution" in agent.tags


class TestSubagents:

    @pytest.mark.asyncio
    async def test_explore_agent(self):
        agent = await Agent.get("explore")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.native is True
        assert agent.hidden is False
        assert agent.delegatable is True
        assert agent.prompt is not None and len(agent.prompt) > 0

    @pytest.mark.asyncio
    async def test_hephaestus_agent(self):
        agent = await Agent.get("hephaestus")
        assert agent is not None
        assert agent.mode == "subagent"
        # Visible subagent mode does not imply eligibility for delegation.
        assert agent.delegatable is False
        assert agent.hidden is False

    @pytest.mark.asyncio
    async def test_rex_junior_agent(self):
        agent = await Agent.get("rex-junior")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.delegatable is True
        assert agent.hidden is False

    @pytest.mark.asyncio
    async def test_prometheus_agent(self):
        agent = await Agent.get("prometheus")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.delegatable is True
        assert agent.hidden is False
        assert agent.prompt is not None and len(agent.prompt) > 0
        assert "delegate_task" not in (agent.tools or [])
        edit_rules = [
            rule for rule in (agent.permission or [])
            if getattr(rule, "permission", None) == "edit"
        ]
        assert edit_rules
        assert any(getattr(rule, "pattern", None) == ".flocks/plans/*" for rule in edit_rules)

    @pytest.mark.asyncio
    async def test_security_agents(self):
        for name in ["host-forensics", "host-forensics-fast"]:
            agent = await Agent.get(name)
            assert agent is not None
            assert agent.mode == "subagent"
            assert agent.delegatable is True


# =============================================================================
# Agent Listing Tests
# =============================================================================

class TestAgentListing:

    @pytest.mark.asyncio
    async def test_list_visible(self):
        visible = await Agent.list_visible()
        names = [a.name for a in visible]
        assert "rex" in names
        assert "explore" in names
        assert "hephaestus" in names
        # The internal Dream worker stays out of user-facing agent lists.
        assert "self-improve" not in names
        assert all(not agent.hidden for agent in visible)

    @pytest.mark.asyncio
    async def test_list_hidden(self):
        hidden = await Agent.list_hidden()
        names = [a.name for a in hidden]
        assert "self-improve" in names
        assert "plan" not in names
        assert all(agent.hidden for agent in hidden)

    @pytest.mark.asyncio
    async def test_list_subagents(self):
        subagents = await Agent.list_subagents()
        names = [a.name for a in subagents]
        assert "explore" in names
        assert "hephaestus" in names
        assert "oracle" in names
        # rex is primary, not subagent
        assert "rex" not in names
        # Hidden subagents are excluded even though their mode matches.
        assert "self-improve" not in names
        assert all(agent.mode == "subagent" and not agent.hidden for agent in subagents)

    @pytest.mark.asyncio
    async def test_list_primary(self):
        primary = await Agent.list_primary()
        names = [a.name for a in primary]
        assert "rex" in names
        assert "explore" not in names

    @pytest.mark.asyncio
    async def test_is_hidden(self):
        assert await Agent.is_hidden("self-improve") is True
        assert await Agent.is_hidden("plan") is False  # Retired, not a hidden agent.
        assert await Agent.is_hidden("explore") is False
        assert await Agent.is_hidden("rex") is False
        assert await Agent.is_hidden("nonexistent") is False

    @pytest.mark.asyncio
    async def test_is_delegatable(self):
        async def delegatable(name: str) -> bool:
            agent = await Agent.get(name)
            return bool(agent.delegatable) if agent else False

        assert await delegatable("rex") is False
        assert await delegatable("self-improve") is False
        assert await delegatable("plan") is False
        assert await delegatable("rex-junior") is True
        assert await delegatable("explore") is True
        assert await delegatable("hephaestus") is False
        assert await delegatable("oracle") is True
        assert await delegatable("prometheus") is True

    @pytest.mark.asyncio
    async def test_is_delegatable_respects_sidecar_override(self, tmp_path, monkeypatch):
        settings_file = tmp_path / "agent_delegatable_settings.json"
        monkeypatch.setattr("flocks.agent.delegatable_settings.settings_path", lambda: settings_file)

        import flocks.agent.delegatable_settings as delegatable_settings
        from flocks.agent.registry import Agent as AgentRegistry, is_delegatable

        monkeypatch.setattr(AgentRegistry, "_delegatable_settings_mtime", 0.0)
        delegatable_settings.set_override("explore", False)
        AgentRegistry.invalidate_cache()
        try:
            agent = await AgentRegistry.get("explore")
            assert agent is not None
            assert agent.delegatable is False
            assert is_delegatable("explore") is False
        finally:
            # Do not leak the overridden cached definition into later tests.
            AgentRegistry.invalidate_cache()

    @pytest.mark.asyncio
    async def test_list_names(self):
        names = await Agent.list_names()
        for name in BUILTIN_AGENTS:
            assert name in names


# =============================================================================
# Agent Permission Tests
# =============================================================================

class TestAgentPermissions:

    @pytest.mark.asyncio
    async def test_explore_tools(self):
        """Explore declares discovery tools, not the retired list or editing tools."""
        agent = await Agent.get("explore")
        assert agent is not None
        declared = {"grep", "glob", "bash", "webfetch", "websearch", "read"}
        assert set(agent.tools) == declared
        for tool_name in declared:
            assert await Agent.has_tool("explore", tool_name) is True
        for tool_name in ("list", "write", "edit", "apply_patch"):
            assert await Agent.has_tool("explore", tool_name) is False

    @pytest.mark.asyncio
    async def test_nonexistent_agent_has_no_tool(self):
        assert await Agent.has_tool("nonexistent", "read") is False


# =============================================================================
# Session Prompt Constants Tests
# =============================================================================

class TestSessionPrompts:
    """Session management prompts live in session/prompts.py, not in agent registry."""

    def test_prompt_compaction_content(self):
        assert PROMPT_COMPACTION is not None
        assert len(PROMPT_COMPACTION) > 0
        assert "summariz" in PROMPT_COMPACTION.lower()

    def test_prompt_title_content(self):
        assert PROMPT_TITLE is not None
        assert len(PROMPT_TITLE) > 0
        assert "title" in PROMPT_TITLE.lower()

    def test_prompt_summary_content(self):
        assert PROMPT_SUMMARY is not None
        assert len(PROMPT_SUMMARY) > 0


# =============================================================================
# Agent Registration Tests
# =============================================================================

class TestAgentRegistration:

    @pytest.mark.asyncio
    async def test_register_custom_agent(self):
        custom = AgentInfo(
            name="custom_test",
            description="A test agent",
            mode="subagent",
            native=False,
        )
        Agent.register("custom_test", custom)
        try:
            agents = await Agent._load_agents()
            retrieved = agents.get("custom_test")
            assert retrieved is not None
            assert retrieved.name == "custom_test"
            assert retrieved.native is False
        finally:
            Agent.unregister("custom_test")

    @pytest.mark.asyncio
    async def test_unregister_custom_agent(self):
        custom = AgentInfo(name="temp_agent", mode="subagent", native=False)
        Agent.register("temp_agent", custom)
        agents = await Agent._load_agents()
        assert agents.get("temp_agent") is not None

        result = Agent.unregister("temp_agent")
        assert result is True
        agents = await Agent._load_agents()
        assert agents.get("temp_agent") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["rex", "explore", "self-improve"])
    async def test_cannot_unregister_native_agent(self, name):
        agent = await Agent.get(name)
        assert agent is not None and agent.native is True
        assert Agent.unregister(name) is False
        reloaded = await Agent.refresh()
        assert reloaded[name].native is True

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_agent(self):
        result = Agent.unregister("nonexistent_agent")
        assert result is False


# =============================================================================
# AgentModel Tests
# =============================================================================

class TestAgentModel:

    def test_create_agent_model(self):
        model = AgentModel(model_id="gpt-4", provider_id="openai")
        assert model.model_id == "gpt-4"
        assert model.provider_id == "openai"

    @pytest.mark.asyncio
    async def test_builtin_agents_no_custom_model(self):
        for name in ("rex", "explore", "self-improve"):
            assert await Agent.get(name) is not None
            assert await Agent.get_model_config(name) is None
        assert await Agent.get_model_config("nonexistent") is None

    @pytest.mark.asyncio
    async def test_agent_with_custom_model(self):
        from flocks.agent.agent import AgentModel
        custom = AgentInfo(
            name="model_test",
            mode="subagent",
            native=False,
            model=AgentModel(model_id="claude-3", provider_id="anthropic"),
        )
        Agent.register("model_test", custom)
        try:
            agents = await Agent._load_agents()
            agent = agents.get("model_test")
            assert agent is not None
            assert agent.model is not None
            assert agent.model.model_id == "claude-3"
        finally:
            Agent.unregister("model_test")


# =============================================================================
# AgentInfo Model Tests
# =============================================================================

class TestAgentInfo:

    def test_agent_info_defaults(self):
        info = AgentInfo(name="test")
        assert info.name == "test"
        assert info.description is None
        assert info.mode == "all"
        assert info.native is False
        assert info.hidden is False
        assert info.temperature is None
        assert info.top_p is None
        assert info.color is None
        assert info.model is None
        assert info.prompt is None
        assert info.steps is None
        assert info.permission == []

    def test_agent_info_with_values(self):
        info = AgentInfo(
            name="custom",
            description="Custom agent",
            mode="subagent",
            native=False,
            hidden=True,
            temperature=0.5,
            top_p=0.9,
            color="#FF0000",
            steps=10,
        )
        assert info.temperature == 0.5
        assert info.top_p == 0.9
        assert info.color == "#FF0000"
        assert info.steps == 10


# =============================================================================
# Default Agent Tests
# =============================================================================

class TestDefaultAgent:

    @pytest.mark.asyncio
    async def test_default_agent_is_rex(self):
        default = await Agent.default_agent()
        assert default == "rex"

    @pytest.mark.asyncio
    async def test_list_sorted_with_default_first(self):
        agents = await Agent.list()
        default = await Agent.default_agent()
        assert agents[0].name == default
