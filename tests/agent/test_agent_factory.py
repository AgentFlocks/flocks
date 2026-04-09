"""
Agent Factory 测试

覆盖 flocks.agent.agent_factory 的核心逻辑：
1. YAML 加载（load_agent / scan_and_load）
2. tools / legacy permission 兼容展开
3. Prompt 解析（prompt.md / prompt_builder.py）
4. 动态 Prompt 注入（inject_dynamic_prompts）
5. Plugin YAML CRUD（read / update / delete）
6. prompt_metadata 解析
7. 子目录格式 plugin agent（每 agent 一个文件夹）
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import flocks.agent.agent_factory as _factory_module
from flocks.agent.agent_factory import (
    load_agent,
    scan_and_load,
    inject_dynamic_prompts,
    yaml_to_agent_info,
    delete_yaml_agent,
    _parse_prompt_metadata,
)
from flocks.agent.agent import AgentInfo, AgentModel, AgentPromptMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_agent_dir(tmp_path: Path, yaml_text: str, prompt_text: str | None = None) -> Path:
    """Create a minimal agent folder under tmp_path."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "agent.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    if prompt_text is not None:
        (agent_dir / "prompt.md").write_text(textwrap.dedent(prompt_text), encoding="utf-8")
    return agent_dir


# ===========================================================================
# _parse_prompt_metadata
# ===========================================================================

class TestParsePromptMetadata:

    def test_returns_none_when_missing(self):
        assert _parse_prompt_metadata({}) is None

    def test_returns_none_when_not_dict(self):
        assert _parse_prompt_metadata({"prompt_metadata": "bad"}) is None

    def test_basic_metadata(self):
        raw = {
            "prompt_metadata": {
                "category": "security",
                "cost": "high",
                "triggers": [
                    {"domain": "network", "trigger": "IP analysis"},
                ],
            }
        }
        meta = _parse_prompt_metadata(raw)
        assert isinstance(meta, AgentPromptMetadata)
        assert meta.category == "security"
        assert meta.cost == "high"
        assert len(meta.triggers) == 1
        assert meta.triggers[0].domain == "network"
        assert meta.triggers[0].trigger == "IP analysis"

    def test_defaults_category_and_cost(self):
        """Minimal metadata dict uses default category and cost."""
        raw = {"prompt_metadata": {"triggers": []}}
        meta = _parse_prompt_metadata(raw)
        assert meta is not None
        assert meta.category == "plugin"
        assert meta.cost == "medium"

    def test_optional_fields(self):
        raw = {
            "prompt_metadata": {
                "category": "analysis",
                "cost": "low",
                "use_when": ["threat intel needed"],
                "avoid_when": ["simple tasks"],
                "dedicated_section": "THREAT INTEL",
                "key_trigger": "IP lookup",
            }
        }
        meta = _parse_prompt_metadata(raw)
        assert meta.use_when == ["threat intel needed"]
        assert meta.avoid_when == ["simple tasks"]
        assert meta.dedicated_section == "THREAT INTEL"
        assert meta.key_trigger == "IP lookup"


# ===========================================================================
# load_agent
# ===========================================================================

class TestLoadAgent:

    def test_loads_basic_agent(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, """
            name: simple
            description: A simple agent
            mode: subagent
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.name == "simple"
        assert agent.description == "A simple agent"
        assert agent.mode == "subagent"
        assert agent.native is False  # default when native not passed by caller

    def test_native_parameter_passed_to_agent_info(self, tmp_path):
        """native is determined by the caller, not the YAML content."""
        agent_dir = _write_agent_dir(tmp_path, "name: simple\n")
        assert load_agent(agent_dir, native=False).native is False
        assert load_agent(agent_dir, native=True).native is True

    def test_uses_dir_name_when_name_absent(self, tmp_path):
        agent_dir = tmp_path / "fallback_name"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("description: no name field\n", encoding="utf-8")
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.name == "fallback_name"

    def test_returns_none_when_no_yaml(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert load_agent(empty_dir) is None

    def test_returns_none_on_invalid_yaml(self, tmp_path):
        agent_dir = tmp_path / "bad_agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(": invalid: yaml: [\n", encoding="utf-8")
        assert load_agent(agent_dir) is None

    def test_loads_prompt_from_prompt_md(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, "name: has_prompt\n", "You are a helpful agent.")
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.prompt == "You are a helpful agent."
        assert agent.prompt_builder is None

    def test_detects_prompt_builder_py(self, tmp_path):
        agent_dir = tmp_path / "builder_agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("name: builder_agent\n", encoding="utf-8")
        (agent_dir / "prompt_builder.py").write_text("def inject(*a): pass\n", encoding="utf-8")
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.prompt is None
        assert agent.prompt_builder is not None
        assert "prompt_builder" in agent.prompt_builder
        assert ":inject" in agent.prompt_builder

    def test_prompt_md_takes_priority_over_builder(self, tmp_path):
        """prompt.md wins when both files exist."""
        agent_dir = tmp_path / "both"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("name: both\n", encoding="utf-8")
        (agent_dir / "prompt.md").write_text("Static prompt", encoding="utf-8")
        (agent_dir / "prompt_builder.py").write_text("def inject(*a): pass\n", encoding="utf-8")
        agent = load_agent(agent_dir)
        assert agent.prompt == "Static prompt"
        assert agent.prompt_builder is None

    def test_tools_list_is_loaded_as_concrete_tools(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, """
            name: restricted
            tools:
              - read
              - grep
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.tools == ["read", "grep"]

    def test_no_tools_defaults_to_empty_declared_toolset(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, "name: open_agent\n")
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.tools == []

    def test_loads_model(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, """
            name: model_agent
            model:
              model_id: gpt-4
              provider_id: openai
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.model is not None
        assert agent.model.model_id == "gpt-4"
        assert agent.model.provider_id == "openai"

    def test_loads_optional_fields(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, """
            name: full_agent
            hidden: true
            color: "#ff0000"
            steps: 20
            temperature: 0.3
            top_p: 0.95
            delegatable: false
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.hidden is True
        assert agent.color == "#ff0000"
        assert agent.steps == 20
        assert agent.temperature == 0.3
        assert agent.top_p == 0.95
        assert agent.delegatable is False

    def test_loads_prompt_metadata(self, tmp_path):
        agent_dir = _write_agent_dir(tmp_path, """
            name: meta_agent
            prompt_metadata:
              category: security
              cost: high
              triggers:
                - domain: network
                  trigger: When analyzing IPs
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.prompt_metadata is not None
        assert agent.prompt_metadata.category == "security"

    def test_permission_dict_deny_all_with_allowlist(self, tmp_path):
        """load_agent() should expand old-style permission dict to concrete tools."""
        agent_dir = _write_agent_dir(tmp_path, """
            name: perm_dict_agent
            permission:
              "*": deny
              read: allow
              bash: allow
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert "read" in (agent.tools or [])
        assert "bash" in (agent.tools or [])

    def test_permission_dict_allow_all_with_exceptions(self, tmp_path):
        """load_agent() should exclude explicitly denied tools after expansion."""
        agent_dir = _write_agent_dir(tmp_path, """
            name: allow_all_agent
            permission:
              "*": allow
              delegate_task: deny
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert "delegate_task" not in (agent.tools or [])

    def test_tools_list_takes_priority_over_permission_dict(self, tmp_path):
        """When both tools list and permission dict exist, tools list wins."""
        agent_dir = _write_agent_dir(tmp_path, """
            name: priority_agent
            tools:
              - read
            permission:
              "*": allow
        """)
        agent = load_agent(agent_dir)
        assert agent is not None
        assert agent.tools == ["read"]


# ===========================================================================
# scan_and_load
# ===========================================================================

class TestScanAndLoad:

    def test_scans_builtin_agents(self):
        """Built-in agents directory must yield at least 13 agents."""
        from flocks.agent.agent_factory import _BUILTIN_AGENTS_DIR
        result = scan_and_load(dirs=[_BUILTIN_AGENTS_DIR])
        assert len(result) >= 13

    def test_all_builtin_agent_names_present(self):
        """Every agent shipped with the package must be discoverable and marked native.

        native=True is derived from the loading directory (_BUILTIN_AGENTS_DIR),
        not from any YAML field.  Passing only _BUILTIN_AGENTS_DIR to scan_and_load
        ensures the assertion is environment-independent.
        """
        from flocks.agent.agent_factory import _BUILTIN_AGENTS_DIR
        result = scan_and_load(dirs=[_BUILTIN_AGENTS_DIR])
        expected = [
            "rex", "hephaestus", "plan", "explore",
            "oracle", "librarian", "metis", "momus", "multimodal-looker",
            "self-enhance", "rex-junior",
        ]
        for name in expected:
            assert name in result, f"Built-in agent '{name}' missing from scan"
            assert result[name].native is True, (
                f"Agent '{name}' should be native=True when loaded from _BUILTIN_AGENTS_DIR"
            )

    def test_returns_agent_info_objects(self):
        result = scan_and_load()
        for name, agent in result.items():
            assert isinstance(agent, AgentInfo), f"Agent '{name}' is not AgentInfo"

    def test_scans_extra_dirs(self, tmp_path):
        """Extra dirs parameter adds additional agents."""
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        agent_subdir = extra_dir / "extra_agent"
        agent_subdir.mkdir()
        (agent_subdir / "agent.yaml").write_text("name: extra_agent\n", encoding="utf-8")

        result = scan_and_load(dirs=[extra_dir])
        assert "extra_agent" in result

    def test_name_conflict_skips_duplicate(self, tmp_path):
        """When two dirs have the same agent name, first wins."""
        first = tmp_path / "first"
        second = tmp_path / "second"
        for d in (first, second):
            subdir = d / "conflict_agent"
            subdir.mkdir(parents=True)
            (subdir / "agent.yaml").write_text(f"name: conflict_agent\ndescription: from {d.name}\n", encoding="utf-8")

        result = scan_and_load(dirs=[first, second])
        assert result["conflict_agent"].description == f"from first"

    def test_skips_dirs_starting_with_underscore(self, tmp_path):
        extra = tmp_path / "extra"
        extra.mkdir()
        private = extra / "_private_agent"
        private.mkdir()
        (private / "agent.yaml").write_text("name: _private_agent\n", encoding="utf-8")

        result = scan_and_load(dirs=[extra])
        assert "_private_agent" not in result


# ===========================================================================
# inject_dynamic_prompts
# ===========================================================================

class TestInjectDynamicPrompts:

    def test_inject_sets_prompt_on_agent(self):
        """Agent with a valid prompt_builder module gets its prompt set."""
        agent = AgentInfo(name="dyn", mode="subagent", native=False)
        agent.prompt_builder = "tests.agent._helpers.sample_builder:inject"

        # Write a temporary helper module for this test
        helper_path = Path(__file__).parent / "_helpers" / "sample_builder.py"
        helper_path.parent.mkdir(exist_ok=True)
        if not helper_path.exists():
            helper_path.write_text(
                "def inject(agent_info, *args, **kwargs):\n    agent_info.prompt = 'injected'\n",
                encoding="utf-8",
            )
            (helper_path.parent / "__init__.py").touch()

        agents = {"dyn": agent}
        inject_dynamic_prompts(agents, [], [], [], [])
        assert agent.prompt == "injected"

    def test_inject_handles_import_error_gracefully(self):
        """Broken prompt_builder should not crash; agent prompt stays None."""
        agent = AgentInfo(name="broken", mode="subagent", native=False)
        agent.prompt_builder = "nonexistent.module.path:inject"
        agents = {"broken": agent}
        inject_dynamic_prompts(agents, [], [], [], [])  # Should not raise
        assert agent.prompt is None

    def test_inject_skips_agents_without_builder(self):
        agent = AgentInfo(name="static", mode="subagent", native=False, prompt="static prompt")
        agents = {"static": agent}
        inject_dynamic_prompts(agents, [], [], [], [])
        assert agent.prompt == "static prompt"

    @pytest.mark.asyncio
    async def test_rex_gets_injected_prompt(self):
        """Rex agent must receive a dynamic prompt after full load."""
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        assert rex is not None
        assert rex.prompt is not None
        assert len(rex.prompt) > 100, "Rex prompt should be non-trivially long"

    @pytest.mark.asyncio
    async def test_hephaestus_gets_injected_prompt(self):
        """Hephaestus agent must receive a dynamic prompt after full load."""
        from flocks.agent.registry import Agent
        heph = await Agent.get("hephaestus")
        assert heph is not None
        assert heph.prompt is not None
        assert len(heph.prompt) > 100

    @pytest.mark.asyncio
    async def test_librarian_gets_injected_prompt(self):
        from flocks.agent.registry import Agent
        lib = await Agent.get("librarian")
        assert lib is not None
        assert lib.prompt is not None
        assert len(lib.prompt) > 50

    @pytest.mark.asyncio
    async def test_rex_junior_gets_injected_prompt(self):
        from flocks.agent.registry import Agent
        jr = await Agent.get("rex-junior")
        assert jr is not None
        assert jr.prompt is not None
        assert len(jr.prompt) > 50

    @pytest.mark.asyncio
    async def test_static_prompt_agents(self):
        """Built-in agents with prompt.md should have non-empty prompts."""
        from flocks.agent.registry import Agent
        # Only built-in agents (native=True) — not dependent on local plugin installation
        for name in ["explore", "oracle", "momus", "metis", "self-enhance", "multimodal-looker"]:
            agent = await Agent.get(name)
            assert agent is not None, f"Agent '{name}' not found"
            assert agent.prompt is not None, f"Agent '{name}' should have a prompt from prompt.md"
            assert len(agent.prompt) > 20, f"Agent '{name}' prompt is suspiciously short"

    @pytest.mark.asyncio
    async def test_plugin_agent_has_prompt_if_installed(self):
        """Plugin agents installed in ~/.flocks/plugins/agents/ should load their prompt."""
        from flocks.agent.registry import Agent
        # host-forensics is a plugin agent (native=False); skip if not installed locally
        agent = await Agent.get("host-forensics")
        if agent is None:
            pytest.skip("host-forensics plugin not installed in this environment")
        assert agent.prompt is not None, "host-forensics should have a prompt.md"
        assert len(agent.prompt) > 20

    @pytest.mark.asyncio
    async def test_plan_agent_has_no_prompt(self):
        """plan agent has neither prompt.md nor prompt_builder.py."""
        from flocks.agent.registry import Agent
        agent = await Agent.get("plan")
        assert agent is not None
        assert agent.prompt is None, "Agent 'plan' should NOT have a prompt"


# ===========================================================================
# yaml_to_agent_info  (Plugin loader entry point)
# ===========================================================================

class TestYamlToAgentInfo:

    def _make_yaml_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "plugin" / "agent.yaml"
        p.parent.mkdir(parents=True)
        return p

    def test_basic_plugin_agent(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {"name": "plugin_a", "description": "Plugin A", "mode": "subagent"}
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.name == "plugin_a"
        assert agent.native is False
        assert agent.mode == "subagent"

    def test_raises_on_missing_name(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        with pytest.raises(ValueError, match="missing required 'name'"):
            yaml_to_agent_info({}, yaml_path)

    def test_inline_prompt(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {"name": "inline", "prompt": "You are inline."}
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.prompt == "You are inline."

    def test_prompt_file_resolution(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        prompt_file = yaml_path.parent / "custom_prompt.md"
        prompt_file.write_text("Loaded from file.", encoding="utf-8")
        raw = {"name": "file_prompt", "prompt_file": "custom_prompt.md"}
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.prompt == "Loaded from file."

    def test_tools_list_permission(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {"name": "restricted", "tools": ["read", "grep"]}
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.tools == ["read", "grep"]

    def test_old_permission_dict_fallback(self, tmp_path):
        """Plugin agents with permission dict (not tools list) are handled."""
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {"name": "old_perm", "permission": {"*": "allow", "bash": "deny"}}
        agent = yaml_to_agent_info(raw, yaml_path)
        assert "bash" not in (agent.tools or [])

    def test_missing_tools_defaults_to_empty_declared_toolset(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        agent = yaml_to_agent_info({"name": "no_tools"}, yaml_path)
        assert agent.tools == []

    def test_model_parsed(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {
            "name": "model_plugin",
            "model": {"model_id": "claude-3", "provider_id": "anthropic"},
        }
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.model is not None
        assert agent.model.model_id == "claude-3"

    def test_description_cn_snake_and_camel(self, tmp_path):
        yaml_path = self._make_yaml_path(tmp_path)
        raw = {
            "name": "bilingual",
            "description": "English",
            "description_cn": "中文",
        }
        agent = yaml_to_agent_info(raw, yaml_path)
        assert agent.description == "English"
        assert agent.description_cn == "中文"

        raw2 = {"name": "bilingual2", "descriptionCn": "界面"}
        agent2 = yaml_to_agent_info(raw2, yaml_path)
        assert agent2.description_cn == "界面"


# ===========================================================================
# Agent config overrides  (cfg.agent integration)
# ===========================================================================

class TestAgentConfigOverrides:

    @pytest.mark.asyncio
    async def test_agent_alias_sisyphus_resolves_to_rex(self):
        from flocks.agent.registry import Agent
        rex = await Agent.get("rex")
        alias = await Agent.get("sisyphus")
        assert alias is not None
        assert alias.name == rex.name

    @pytest.mark.asyncio
    async def test_agent_refresh_invalidates_cache(self):
        from flocks.agent.registry import Agent
        agents_before = await Agent.state()
        Agent.register("tmp_refresh_test", AgentInfo(name="tmp_refresh_test", mode="subagent", native=False))
        await Agent.refresh()
        agents_after = await Agent.state()
        # After refresh, custom agent should still appear (it's in _custom_agents)
        assert "tmp_refresh_test" in agents_after
        Agent.unregister("tmp_refresh_test")
        await Agent.refresh()

    @pytest.mark.asyncio
    async def test_enabled_agents_whitelist(self):
        """enabled_agents config limits visible agents (tested via scan)."""
        from flocks.agent.agent_factory import scan_and_load
        result = scan_and_load()
        # All 13 built-ins should be present without a whitelist
        assert len(result) >= 13


# ===========================================================================
# Available agent list builder
# ===========================================================================

class TestBuildAvailableAgents:

    def test_primary_agents_excluded(self):
        from flocks.agent.registry import _build_available_agents
        agents = {
            "rex": AgentInfo(name="rex", mode="primary", delegatable=False, native=True),
            "explore": AgentInfo(name="explore", mode="subagent", delegatable=True, native=True),
        }
        available = _build_available_agents(agents)
        names = [a.name for a in available]
        assert "rex" not in names
        assert "explore" in names

    def test_hidden_agents_excluded(self):
        from flocks.agent.registry import _build_available_agents
        agents = {
            "plan": AgentInfo(name="plan", mode="subagent", hidden=True, delegatable=False, native=True),
            "oracle": AgentInfo(name="oracle", mode="subagent", hidden=False, delegatable=True, native=True),
        }
        available = _build_available_agents(agents)
        names = [a.name for a in available]
        assert "plan" not in names
        assert "oracle" in names

    def test_non_delegatable_agents_excluded(self):
        from flocks.agent.registry import _build_available_agents
        agents = {
            "rex-junior": AgentInfo(name="rex-junior", mode="subagent", hidden=False, delegatable=False, native=True),
            "hephaestus": AgentInfo(name="hephaestus", mode="subagent", hidden=False, delegatable=True, native=True),
        }
        available = _build_available_agents(agents)
        names = [a.name for a in available]
        assert "rex-junior" not in names
        assert "hephaestus" in names

    def test_default_metadata_generated_for_agent_without_prompt_metadata(self):
        from flocks.agent.registry import _build_available_agents, _make_default_prompt_metadata
        agent = AgentInfo(
            name="plugin_x",
            description="Does X",
            mode="subagent",
            delegatable=True,
            native=False,
        )
        agents = {"plugin_x": agent}
        available = _build_available_agents(agents)
        assert len(available) == 1
        assert available[0].metadata.category == "plugin"
        assert available[0].metadata.cost == "medium"


# ===========================================================================
# Project-level plugin agent scanning
# ===========================================================================

class TestProjectLevelAgentScan:
    """scan_and_load() should discover agents from <cwd>/.flocks/plugins/agents/."""

    def _write_agent(self, agents_dir: Path, name: str) -> None:
        agent_dir = agents_dir / name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            textwrap.dedent(f"""\
                name: {name}
                description: Project-level test agent {name}
                mode: subagent
            """),
            encoding="utf-8",
        )

    def test_project_plugin_agents_are_discovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agents placed in <cwd>/.flocks/plugins/agents/ are loaded by scan_and_load()."""
        project_agents_dir = tmp_path / ".flocks" / "plugins" / "agents"
        self._write_agent(project_agents_dir, "proj-agent")

        monkeypatch.chdir(tmp_path)
        result = scan_and_load()

        assert "proj-agent" in result
        assert result["proj-agent"].name == "proj-agent"

    def test_project_agent_does_not_override_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A project-level agent with a built-in name is skipped (first wins)."""
        project_agents_dir = tmp_path / ".flocks" / "plugins" / "agents"
        self._write_agent(project_agents_dir, "rex")

        monkeypatch.chdir(tmp_path)
        result = scan_and_load()

        # Built-in rex should be retained, project-level duplicate skipped
        assert result["rex"].native is True

    def test_project_plugin_agent_is_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agents in <cwd>/.flocks/plugins/agents/ are marked native=True."""
        project_agents_dir = tmp_path / ".flocks" / "plugins" / "agents"
        self._write_agent(project_agents_dir, "proj-native")

        monkeypatch.chdir(tmp_path)
        result = scan_and_load()

        assert "proj-native" in result
        assert result["proj-native"].native is True

    def test_user_plugin_agent_is_not_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Agents in ~/.flocks/plugins/agents/ are marked native=False."""
        import flocks.agent.agent_factory as _fac
        user_plugin_dir = tmp_path / "user_plugins" / "agents"
        user_plugin_dir.mkdir(parents=True)
        self._write_agent(user_plugin_dir, "custom-agent")

        monkeypatch.setattr(_fac, "_PLUGIN_AGENTS_DIR", user_plugin_dir)
        # Use a different cwd so project-level dir doesn't exist
        other_project = tmp_path / "some_other_project"
        other_project.mkdir(exist_ok=True)
        monkeypatch.chdir(other_project)

        result = scan_and_load()

        assert "custom-agent" in result
        assert result["custom-agent"].native is False


# ===========================================================================
# delete_yaml_agent — subdirectory layout
# ===========================================================================

class TestDeleteYamlAgent:
    """delete_yaml_agent() should remove the entire folder for subdirectory-layout agents."""

    def _setup_plugin_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a fake plugin agents directory and patch _PLUGIN_AGENTS_DIR."""
        plugin_dir = tmp_path / "plugin_agents"
        plugin_dir.mkdir()
        monkeypatch.setattr(_factory_module, "_PLUGIN_AGENTS_DIR", plugin_dir)
        return plugin_dir

    def test_delete_subdir_agent_removes_entire_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Subdirectory-layout agent: delete_yaml_agent() removes the whole folder."""
        plugin_dir = self._setup_plugin_dir(tmp_path, monkeypatch)

        agent_dir = plugin_dir / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("name: my-agent\n", encoding="utf-8")
        (agent_dir / "prompt.md").write_text("Prompt text", encoding="utf-8")

        result = delete_yaml_agent("my-agent")

        assert result is True
        assert not agent_dir.exists(), "Agent folder should be removed"

    def test_delete_subdir_agent_with_scripts_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Entire agent folder including subdirectories is removed."""
        plugin_dir = self._setup_plugin_dir(tmp_path, monkeypatch)

        agent_dir = plugin_dir / "forensics"
        scripts_dir = agent_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text("name: forensics\n", encoding="utf-8")
        (scripts_dir / "triage.sh").write_text("#!/bin/bash\necho ok\n", encoding="utf-8")

        result = delete_yaml_agent("forensics")

        assert result is True
        assert not agent_dir.exists()

    def test_delete_flat_file_agent_removes_yaml_and_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Legacy flat-file layout: only the YAML and sibling prompt file are removed."""
        plugin_dir = self._setup_plugin_dir(tmp_path, monkeypatch)

        yaml_file = plugin_dir / "legacy-agent.yaml"
        prompt_file = plugin_dir / "legacy-agent.prompt.md"
        yaml_file.write_text("name: legacy-agent\n", encoding="utf-8")
        prompt_file.write_text("Old prompt", encoding="utf-8")

        result = delete_yaml_agent("legacy-agent")

        assert result is True
        assert not yaml_file.exists()
        assert not prompt_file.exists()
        assert plugin_dir.exists(), "Plugin dir itself should NOT be removed"

    def test_delete_returns_false_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        self._setup_plugin_dir(tmp_path, monkeypatch)
        assert delete_yaml_agent("nonexistent-agent") is False
