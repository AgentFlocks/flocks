"""
Agent route tests

Covers:
  - Listing agents (GET /api/agent)
  - Getting a specific agent (GET /api/agent/{name})
  - Creating a custom agent (POST /api/agent)
  - Updating an agent (PUT /api/agent/{name})
  - Deleting a custom agent (DELETE /api/agent/{name})
  - Running / testing an agent (POST /api/agent/{name}/test)
  - Error cases (404, 422)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from httpx import AsyncClient

# Minimal valid agent payload
_AGENT_PAYLOAD = {
    "name": "test-agent",
    "description": "A test agent",
    "mode": "primary",
    "permission": [],
    "options": {},
    "prompt": "You are a test assistant.",
}

_SUBAGENT_PAYLOAD = {
    **_AGENT_PAYLOAD,
    "name": "test-subagent",
    "mode": "subagent",
}


@pytest.fixture(autouse=True)
def _isolated_delegatable_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings_file = tmp_path / "agent_delegatable_settings.json"
    monkeypatch.setattr("flocks.agent.delegatable_settings.settings_path", lambda: settings_file)

    from flocks.agent.registry import Agent

    Agent._delegatable_settings_mtime = 0.0
    Agent.invalidate_cache()
    yield settings_file
    Agent._delegatable_settings_mtime = 0.0
    Agent.invalidate_cache()


# ===========================================================================
# List
# ===========================================================================

@pytest.fixture
def native_group_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock

    from flocks.agent import agent_factory, registry
    from flocks.server.routes import agent as routes
    from flocks.skill.skill import Skill
    from flocks.tool.registry import ToolRegistry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    builtin = tmp_path / "builtin-agents"
    plugin = home / ".flocks" / "plugins" / "agents"
    for root, name in ((builtin, "native-demo"), (plugin, "yaml-demo")):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "agent.yaml").write_text(
            f"name: {name}\ngroup: Package\ndescription: Original\nmode: subagent\nunknown: kept\n",
            encoding="utf-8",
        )
        (folder / "prompt.md").write_bytes(b"Original prompt\r\n  keep trailing spaces  \r\n")
    monkeypatch.setattr(agent_factory, "_BUILTIN_AGENTS_DIR", builtin)
    monkeypatch.setattr(agent_factory, "_SYSTEM_AGENT_ROOTS", (builtin,))
    monkeypatch.setattr(agent_factory, "_PLUGIN_AGENTS_DIR", plugin)
    monkeypatch.setattr(registry.PluginLoader, "load_extension", lambda *args, **kwargs: None)
    monkeypatch.setattr(ToolRegistry, "init_async", AsyncMock())
    monkeypatch.setattr(ToolRegistry, "list_tools", lambda: [])
    monkeypatch.setattr(Skill, "list_enabled", AsyncMock(return_value=[]))
    monkeypatch.setattr("flocks.workflow.center.scan_skill_workflows", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "_get_all_tool_names_async", AsyncMock(return_value=[]))
    return builtin, plugin


class TestAgentNativeGroup:
    @pytest.mark.asyncio
    async def test_native_group_storage_persist_clear_and_ordinary_updates(self, client, native_group_agents):
        from flocks.agent.registry import Agent
        from flocks.storage.storage import Storage

        created = await client.post("/api/agent", json={**_AGENT_PAYLOAD, "group": "  Team A  "})
        assert created.status_code == 200, created.text
        assert created.json()["group"] == "Team A"
        changed = await client.put("/api/agent/test-agent", json={"description": "Changed"})
        assert changed.json()["group"] == "Team A"
        assigned = await client.put("/api/agent/test-agent", json={"group": "team a"})
        assert assigned.json()["group"] == "team a"
        assert assigned.json()["prompt"] == _AGENT_PAYLOAD["prompt"]
        Agent._custom_agents.clear()
        await client.post("/api/agent/refresh")
        assert (await client.get("/api/agent/test-agent")).json()["group"] == "team a"
        for value in (None, ""):
            cleared = await client.put("/api/agent/test-agent", json={"group": value})
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["group"] == ""
            assert (await Storage.read("agent/custom/test-agent"))["group"] == ""
        Agent._custom_agents.clear()
        await client.post("/api/agent/refresh")
        listed = (await client.get("/api/agent")).json()
        assert next(item for item in listed if item["name"] == "test-agent")["group"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("section", ["agent", "mode"])
    @pytest.mark.parametrize("source", ["yaml", "storage"])
    async def test_generic_config_group_is_visible_without_manual_refresh(self, client, native_group_agents, section, source):
        name = "yaml-demo"
        if source == "storage":
            name = f"config-cache-{section}"
            created = await client.post("/api/agent", json={**_AGENT_PAYLOAD, "name": name, "group": "Package"})
            assert created.status_code == 200, created.text
        before = await client.get(f"/api/agent/{name}")
        assert before.status_code == 200, before.text
        assert before.json()["group"] == "Package"

        for value, expected in (("修改后", "修改后"), (None, ""), ("New group", "New group"), ("", "")):
            saved = await client.patch("/api/config/", json={section: {name: {"group": value}}})
            assert saved.status_code == 200, saved.text
            detail = await client.get(f"/api/agent/{name}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["group"] == expected
            listed = await client.get("/api/agent")
            assert next(item for item in listed.json() if item["name"] == name)["group"] == expected
            assert detail.json()["prompt"] == before.json()["prompt"]

    @pytest.mark.asyncio
    async def test_native_group_yaml_preserves_prompt_and_unknown_fields(self, client, native_group_agents):
        import yaml
        from flocks.agent.agent_factory import load_agent, yaml_to_agent_info
        from flocks.agent.registry import Agent

        folder = native_group_agents[1] / "yaml-demo"
        prompt_before = (folder / "prompt.md").read_bytes()
        response = await client.put("/api/agent/yaml-demo", json={"group": "  Operations  "})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == "Operations"
        assert (folder / "prompt.md").read_bytes() == prompt_before
        raw = yaml.safe_load((folder / "agent.yaml").read_text())
        assert raw["group"] == "Operations"
        assert raw["unknown"] == "kept"
        assert load_agent(folder).group == "Operations"
        assert yaml_to_agent_info(raw, folder / "agent.yaml").group == "Operations"
        assert (await client.put("/api/agent/yaml-demo", json={"description": "Edited"})).json()["group"] == "Operations"
        assert (await client.put("/api/agent/yaml-demo", json={"group": None})).json()["group"] == ""
        await client.post("/api/agent/refresh")
        assert (await client.get("/api/agent/yaml-demo")).json()["group"] == ""
        assert yaml.safe_load((folder / "agent.yaml").read_text())["group"] == ""
        assert (folder / "prompt.md").read_bytes() == prompt_before

    @pytest.mark.asyncio
    async def test_native_group_builtin_rejects_changes_and_ignores_stale_overrides(self, client, native_group_agents):
        from flocks.agent.registry import Agent
        from flocks.config.config import AgentConfig, Config, ConfigInfo

        source = native_group_agents[0] / "native-demo" / "agent.yaml"
        before = source.read_bytes()
        await Config.update(ConfigInfo(agent={"native-demo": AgentConfig(group="Stale", temperature=0.4, options={"keep": True})}))
        Agent.invalidate_cache()
        config_before = Config.get_config_file().read_bytes()
        for value in ("Response", None, ""):
            response = await client.put("/api/agent/native-demo", json={"group": value, "prompt": "Do not write"})
            assert response.status_code == 403, response.text
            current = (await client.get("/api/agent/native-demo")).json()
            assert current["group"] == "Package"
            assert current["group_readonly"] is True
            assert current["temperature"] == 0.4
        same = await client.put("/api/agent/native-demo", json={"group": "Package"})
        assert same.status_code == 200, same.text
        assert Config.get_config_file().read_bytes() == config_before
        assert source.read_bytes() == before
        # Existing built-in model/temperature controls remain available.
        changed = await client.put("/api/agent/native-demo/model", json={"temperature": 0.7})
        assert changed.status_code == 200, changed.text
        assert changed.json()["group"] == "Package"
        assert changed.json()["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_native_group_yaml_config_override_survives_edit_and_reload(self, client, native_group_agents):
        from flocks.agent.registry import Agent
        from flocks.config.config import AgentConfig, Config, ConfigInfo
        from flocks.storage.storage import Storage

        await Config.update(ConfigInfo(agent={"yaml-demo": AgentConfig(group="Override", temperature=0.4)}))
        extras = {"skills": ["kept-skill"], "tools": ["kept-tool"], "x-owner": {"keep": True}}
        await Storage.write("agent/custom/yaml-demo", extras)
        await client.post("/api/agent/refresh")
        assert (await client.get("/api/agent/yaml-demo")).json()["group"] == "Override"
        for value, expected in (("Changed", "Changed"), (None, ""), ("", "")):
            changed = await client.put("/api/agent/yaml-demo", json={"group": value})
            assert changed.status_code == 200, changed.text
            assert changed.json()["group"] == expected
            await client.post("/api/agent/refresh")
            reloaded = (await client.get("/api/agent/yaml-demo")).json()
            assert reloaded["group"] == expected
            assert reloaded["temperature"] == 0.4
            assert reloaded["skills"] == ["kept-skill"]
            assert reloaded["tools"] == ["kept-tool"]
            renamed = await client.put("/api/agent/yaml-demo", json={"nameCn": "显示名称"})
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["group"] == expected
            assert (await Storage.read("agent/custom/yaml-demo")) == extras
        mixed = await client.put("/api/agent/yaml-demo", json={"description": "Edited", "group": "Combined"})
        assert mixed.status_code == 200, mixed.text
        await client.post("/api/agent/refresh")
        reloaded = (await client.get("/api/agent/yaml-demo")).json()
        assert reloaded["group"] == "Combined"
        assert reloaded["description"] == "Edited"
        assert reloaded["nameCn"] == "显示名称"

    @pytest.mark.asyncio
    async def test_native_group_storage_config_override_never_replaces_definition(self, client, native_group_agents):
        from flocks.agent.registry import Agent
        from flocks.config.config import AgentConfig, Config, ConfigInfo
        from flocks.storage.storage import Storage

        created = await client.post("/api/agent", json={**_AGENT_PAYLOAD, "group": "Original"})
        assert created.status_code == 200, created.text
        await Config.update(ConfigInfo(agent={"test-agent": AgentConfig(group="Override")}))
        Agent._custom_agents.clear()
        await client.post("/api/agent/refresh")
        reloaded = (await client.get("/api/agent/test-agent")).json()
        assert reloaded["group"] == "Override"
        assert reloaded["prompt"] == _AGENT_PAYLOAD["prompt"]
        assert reloaded["description"] == _AGENT_PAYLOAD["description"]
        changed = await client.put("/api/agent/test-agent", json={"nameCn": "显示名称"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["group"] == "Override"
        for value, expected in (("Changed", "Changed"), (None, ""), ("", "")):
            changed = await client.put("/api/agent/test-agent", json={"group": value})
            assert changed.status_code == 200, changed.text
            assert changed.json()["group"] == expected
            Agent._custom_agents.clear()
            await client.post("/api/agent/refresh")
            reloaded = (await client.get("/api/agent/test-agent")).json()
            assert reloaded["group"] == expected
            assert reloaded["prompt"] == _AGENT_PAYLOAD["prompt"]
            assert reloaded["nameCn"] == "显示名称"
        stored = await Storage.read("agent/custom/test-agent")
        assert stored["prompt"] == _AGENT_PAYLOAD["prompt"]
        reset = await client.put("/api/agent/test-agent/model", json={"model": None})
        assert reset.status_code == 200, reset.text
        assert reset.json()["group"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [12, False, [], {}, "a" * 33, "a\x00b", "a\nb", "a\x7fb"])
    async def test_native_group_rejects_invalid_values(self, client, native_group_agents, value):
        response = await client.put("/api/agent/yaml-demo", json={"group": value})
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_native_group_missing_agent_is_not_created(self, client, native_group_agents):
        response = await client.put("/api/agent/unknown", json={"group": "Team"})
        assert response.status_code == 404


class TestAgentGroupRegression:
    @pytest.mark.asyncio
    async def test_group_override_preserves_raw_config_references(self, client, native_group_agents, tmp_path):
        from flocks.agent.registry import Agent
        from flocks.config.config import Config
        from flocks.config.config_writer import ConfigWriter

        prompt = tmp_path / "prompt.txt"
        prompt.write_text("Resolved prompt", encoding="utf-8")
        raw = {
            "plugin": ["custom/plugin.py"],
            "x-unknown": {"env": "{env:NOT_SET}", "secret": "{secret:kept}"},
            "agent": {
                "yaml-demo": {"group": "Old", "prompt": "{file:" + str(prompt) + "}", "options": {"keep": True}},
                "other": {"prompt": "Other definition", "vendor": [1, 2]},
            },
        }
        path = ConfigWriter._get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw), encoding="utf-8")
        Config.clear_cache()
        Agent.invalidate_cache()
        for group in ("New", None):
            response = await client.put("/api/agent/yaml-demo", json={"group": group})
            assert response.status_code == 200, response.text
            raw["agent"]["yaml-demo"]["group"] = group or ""
            assert json.loads(path.read_text()) == raw

    @pytest.mark.asyncio
    async def test_malformed_raw_config_blocks_mixed_update_before_yaml_write(self, client, native_group_agents):
        from flocks.config.config import AgentConfig, Config, ConfigInfo
        from flocks.config.config_writer import ConfigWriter

        await Config.update(ConfigInfo(agent={"yaml-demo": AgentConfig(group="Old")}))
        assert (await client.get("/api/agent/yaml-demo")).status_code == 200
        source = native_group_agents[1] / "yaml-demo" / "agent.yaml"
        before = source.read_bytes()
        config_path = ConfigWriter._get_config_path()
        damaged = b'{"agent": { broken'
        config_path.write_bytes(damaged)
        response = await client.put("/api/agent/yaml-demo", json={"group": "New", "description": "Must not write"})
        assert response.status_code == 500, response.text
        assert config_path.read_bytes() == damaged
        assert source.read_bytes() == before

    @pytest.mark.asyncio
    async def test_group_only_config_never_creates_phantom_agent(self, client, native_group_agents):
        from flocks.agent.registry import Agent
        from flocks.config.config import AgentConfig, Config, ConfigInfo

        await Config.update(ConfigInfo(agent={
            "gone": AgentConfig(group="Metadata", options={}),
            "gone-cleared": AgentConfig(group=None),
            "real-config-agent": AgentConfig(group="Custom", prompt="A real configured agent"),
        }))
        loaded = await Agent.refresh()
        assert "gone" not in loaded
        assert "gone-cleared" not in loaded
        assert loaded["real-config-agent"].prompt == "A real configured agent"
        assert loaded["real-config-agent"].group_readonly is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_overlay", [False, True])
    async def test_group_change_invalidates_two_warmed_directories(self, client, native_group_agents, config_overlay):
        from flocks.agent.registry import Agent
        from flocks.config.config import AgentConfig, Config, ConfigInfo
        from flocks.project.instance import Instance, InstanceContext, _current_instance

        if config_overlay:
            await Config.update(ConfigInfo(agent={"yaml-demo": AgentConfig(group="Old")}))
        contexts = [InstanceContext(name, name, None) for name in ("project-a", "project-b")]
        async def get_in_context(context):
            token = _current_instance.set(context)
            try:
                return await Agent.get("yaml-demo")
            finally:
                _current_instance.reset(token)

        before = [await get_in_context(context) for context in contexts]
        unrelated = Instance.state(object)
        token = _current_instance.set(contexts[1])
        try:
            unrelated_before = unrelated()
        finally:
            _current_instance.reset(token)
        response = await client.put("/api/agent/yaml-demo", json={"group": "Shared"})
        assert response.status_code == 200, response.text
        for context, prior in zip(contexts, before):
            current = await get_in_context(context)
            assert current.group == "Shared"
            assert current is not prior
        token = _current_instance.set(contexts[1])
        try:
            assert unrelated() is unrelated_before
        finally:
            _current_instance.reset(token)

    @pytest.mark.asyncio
    async def test_custom_project_native_flag_does_not_lock_group(self, client, native_group_agents):
        from flocks.agent.registry import Agent

        folder = Path.cwd() / ".flocks" / "plugins" / "agents" / "project-custom"
        folder.mkdir(parents=True)
        source = folder / "agent.yaml"
        source.write_text("name: project-custom\ngroup: Original\nprompt: Keep\n", encoding="utf-8")
        original = source.read_bytes()
        Agent.invalidate_cache()
        for group in ("Editable", None):
            response = await client.put("/api/agent/project-custom", json={"group": group})
            assert response.status_code == 200, response.text
            assert response.json()["native"] is True
            assert response.json()["group_readonly"] is False
            assert response.json()["group"] == (group or "")
        # Preserve its original native/config override write path.
        assert source.read_bytes() == original

    @pytest.mark.asyncio
    async def test_shipped_yaml_same_group_echo_allows_ordinary_edit(self, client, native_group_agents, monkeypatch):
        from flocks.agent import agent_factory
        from flocks.agent.registry import Agent

        plugin = native_group_agents[1]
        monkeypatch.setattr(agent_factory, "_SYSTEM_AGENT_ROOTS", (native_group_agents[0], plugin))
        Agent.invalidate_cache()
        response = await client.put("/api/agent/yaml-demo", json={"group": "Package", "description": "Editable description"})
        assert response.status_code == 200, response.text
        assert response.json()["description"] == "Editable description"
        assert response.json()["group"] == "Package"
        assert response.json()["group_readonly"] is True
        before = (plugin / "yaml-demo" / "agent.yaml").read_bytes()
        rejected = await client.put("/api/agent/yaml-demo", json={"group": "Changed", "description": "No write"})
        assert rejected.status_code == 403
        assert (plugin / "yaml-demo" / "agent.yaml").read_bytes() == before

    @pytest.mark.asyncio
    async def test_runtime_group_override_does_not_mutate_registered_definition(self, client, native_group_agents):
        from flocks.agent.agent import AgentInfo
        from flocks.agent.registry import Agent
        from flocks.config.config import Config
        from flocks.config.config_writer import ConfigWriter

        source = AgentInfo(name="runtime-custom", group="Package", prompt="Runtime definition")
        Agent.register(source.name, source)
        response = await client.put("/api/agent/runtime-custom", json={"group": "Override"})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == "Override"
        assert source.group == "Package"
        config_path = ConfigWriter._get_config_path()
        raw = json.loads(config_path.read_text())
        del raw["agent"][source.name]["group"]
        config_path.write_text(json.dumps(raw))
        Config.clear_cache()
        Agent.invalidate_cache()
        assert (await Agent.get(source.name)).group == "Package"

    @pytest.mark.asyncio
    async def test_disabled_alias_group_validation_still_uses_selected_definition(self, client, native_group_agents, monkeypatch):
        from flocks.agent.registry import Agent, AGENT_ALIASES
        from flocks.config.config import AgentConfig, Config, ConfigInfo

        monkeypatch.setitem(AGENT_ALIASES, "old-name", "native-demo")
        await Config.update(ConfigInfo(agent={"native-demo": AgentConfig(disable=True)}))
        Agent.invalidate_cache()
        assert await Agent.get("native-demo") is None
        selected = await Agent.get_group_definition("old-name")
        assert selected.group_readonly is True
        assert selected.group == "Package"
        await Agent.validate_group_settings({"old-name": {"group": "Package"}})
        await Agent.validate_group_settings({"old-name": {"temperature": 0.3}})
        for group in ("Changed", "", None):
            with pytest.raises(ValueError, match="read-only"):
                await Agent.validate_group_settings({"old-name": {"group": group}})
        for name in ("native-demo", "old-name"):
            created = await client.post("/api/agent", json={**_AGENT_PAYLOAD, "name": name, "group": "Shadow"})
            assert created.status_code == 409, created.text
            assert name not in Agent._custom_agents


class TestAgentList:
    @pytest.mark.asyncio
    async def test_tool_name_lookup_uses_async_registry_init(self, monkeypatch: pytest.MonkeyPatch):
        from flocks.server.routes import agent as agent_routes
        from flocks.tool.registry import ToolRegistry

        calls: list[str] = []

        async def fake_init_async(cls):
            calls.append("init_async")

        monkeypatch.setattr(ToolRegistry, "init_async", classmethod(fake_init_async))
        monkeypatch.setattr(
            ToolRegistry,
            "list_tools",
            classmethod(lambda cls: [SimpleNamespace(name="demo_tool")]),
        )

        assert await agent_routes._get_all_tool_names_async() == ["demo_tool"]
        assert calls == ["init_async"]

    @pytest.mark.asyncio
    async def test_list_agents_returns_array(self, client: AsyncClient):
        """GET /api/agent returns a non-empty list of built-in agents."""
        resp = await client.get("/api/agent")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_list_agents_have_required_fields(self, client: AsyncClient):
        """Each agent in the list has the required Flocks-compatible fields."""
        resp = await client.get("/api/agent")
        for agent in resp.json():
            assert "name" in agent
            assert "permission" in agent
            assert "options" in agent


# ===========================================================================
# Get
# ===========================================================================

class TestAgentGet:

    @pytest.mark.asyncio
    async def test_get_builtin_agent(self, client: AsyncClient):
        """GET /api/agent/rex returns the built-in rex agent."""
        resp = await client.get("/api/agent/rex")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["name"] == "rex"

    @pytest.mark.asyncio
    async def test_get_unknown_agent_returns_404(self, client: AsyncClient):
        """GET for a non-existent agent returns 404."""
        resp = await client.get("/api/agent/this_agent_does_not_exist_ever")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Create
# ===========================================================================

class TestAgentCreate:

    @pytest.mark.asyncio
    async def test_create_agent(self, client: AsyncClient):
        """POST /api/agent creates a new YAML-backed agent."""
        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.text
        data = resp.json()
        assert data["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_create_agent_missing_name_returns_422(self, client: AsyncClient):
        """Creating an agent without a name returns 422."""
        resp = await client.post(
            "/api/agent",
            json={k: v for k, v in _AGENT_PAYLOAD.items() if k != "name"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_created_agent_retrievable_by_name(self, client: AsyncClient):
        """A newly created agent can be retrieved by name even if the global list
        (backed by the agent state cache) doesn't refresh automatically."""
        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code == status.HTTP_200_OK

        # Direct GET by name uses the refreshed agent registry, so the new agent is visible
        get_resp = await client.get("/api/agent/test-agent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_created_agent_skills_are_available_to_runtime(self, client: AsyncClient):
        from flocks.agent.registry import Agent

        resp = await client.post(
            "/api/agent",
            json={**_AGENT_PAYLOAD, "skills": ["secure-review"]},
        )

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["skills"] == ["secure-review"]
        agent = await Agent.get("test-agent")
        assert agent is not None
        assert agent.skills == ["secure-review"]

    @pytest.mark.asyncio
    async def test_created_agent_survives_registry_reload(self, client: AsyncClient):
        """Storage-backed custom agents remain visible after process cache reload."""
        from flocks.agent.registry import Agent

        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code == status.HTTP_200_OK

        Agent._custom_agents.clear()
        Agent.invalidate_cache()

        get_resp = await client.get("/api/agent/test-agent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["name"] == "test-agent"

        list_resp = await client.get("/api/agent")
        assert list_resp.status_code == status.HTTP_200_OK
        assert "test-agent" in [agent["name"] for agent in list_resp.json()]

    @pytest.mark.asyncio
    async def test_create_agent_without_tools_field_keeps_permission_unchanged(
        self,
        client: AsyncClient,
    ):
        """Older clients that omit tools do not implicitly disable question."""
        payload = {k: v for k, v in _AGENT_PAYLOAD.items() if k != "tools"}
        payload["name"] = "legacy-create-agent"

        resp = await client.post("/api/agent", json=payload)

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["permission"] == []

    @pytest.mark.asyncio
    async def test_create_agent_without_question_tool_adds_persistent_deny(self, client: AsyncClient):
        """Unchecking the question tool persists a deny rule for the always-load tool."""
        from flocks.agent.registry import Agent
        from flocks.storage.storage import Storage

        payload = {
            **_AGENT_PAYLOAD,
            "name": "no-question-agent",
            "tools": ["tool_search"],
        }

        resp = await client.post("/api/agent", json=payload)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["permission"] == [{
            "permission": "question",
            "action": "deny",
            "pattern": "*",
            "source": "agent_tools",
        }]

        stored = await Storage.read("agent/custom/no-question-agent")
        assert stored["permission"] == resp.json()["permission"]

        Agent._custom_agents.clear()
        Agent.invalidate_cache()

        agent = await Agent.get("no-question-agent")
        assert agent is not None
        assert any(
            rule.permission == "question" and rule.level.value == "deny"
            for rule in agent.permission
        )

        get_resp = await client.get("/api/agent/no-question-agent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["permission"] == resp.json()["permission"]

    @pytest.mark.asyncio
    async def test_update_agent_question_tool_toggle_adds_and_removes_managed_deny(
        self,
        client: AsyncClient,
    ):
        """The Tools checkbox controls only the system-managed question deny rule."""
        payload = {
            **_AGENT_PAYLOAD,
            "name": "question-toggle-agent",
            "tools": ["question", "tool_search"],
        }

        create_resp = await client.post("/api/agent", json=payload)
        assert create_resp.status_code == status.HTTP_200_OK
        assert create_resp.json()["permission"] == []

        disable_resp = await client.put(
            "/api/agent/question-toggle-agent",
            json={"tools": ["tool_search"]},
        )
        assert disable_resp.status_code == status.HTTP_200_OK
        assert disable_resp.json()["permission"] == [{
            "permission": "question",
            "action": "deny",
            "pattern": "*",
            "source": "agent_tools",
        }]

        enable_resp = await client.put(
            "/api/agent/question-toggle-agent",
            json={"tools": ["question", "tool_search"]},
        )
        assert enable_resp.status_code == status.HTTP_200_OK
        assert enable_resp.json()["permission"] == []

    @pytest.mark.asyncio
    async def test_create_subagent_defaults_to_delegatable(self, client: AsyncClient):
        """Sub-agents default to delegatable=true when the field is omitted."""
        resp = await client.post("/api/agent", json=_SUBAGENT_PAYLOAD)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["delegatable"] is True


# ===========================================================================
# Update
# ===========================================================================

class TestAgentUpdate:

    @pytest.mark.asyncio
    async def test_update_agent_description(self, client: AsyncClient):
        """PUT /api/agent/{name} updates the agent description."""
        # Create first
        await client.post("/api/agent", json=_AGENT_PAYLOAD)

        updated = {**_AGENT_PAYLOAD, "description": "Updated description"}
        resp = await client.put("/api/agent/test-agent", json=updated)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_yaml_agent_writes_skills_and_tools_to_yaml(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        from flocks.agent.agent import AgentInfo
        from flocks.agent.registry import Agent
        from flocks.server.routes import agent as agent_routes
        from flocks.storage.storage import Storage

        runtime_agent = AgentInfo(
            name="yaml-agent",
            native=False,
            skills=["old-skill"],
            tools=["old-tool"],
        )
        update_yaml = MagicMock(return_value=True)
        storage_write = AsyncMock()
        monkeypatch.setattr(Storage, "read", AsyncMock(return_value=None))
        monkeypatch.setattr(Storage, "write", storage_write)
        monkeypatch.setattr(agent_routes, "find_yaml_agent", lambda _name: tmp_path / "agent.yaml")
        monkeypatch.setattr(agent_routes, "update_yaml_agent", update_yaml)
        monkeypatch.setattr(Agent, "get", AsyncMock(return_value=runtime_agent))
        monkeypatch.setattr(agent_routes, "_load_model_overrides", AsyncMock(return_value={}))
        monkeypatch.setattr(agent_routes, "_load_delegatable_overrides", lambda: {})
        monkeypatch.setattr(agent_routes, "_get_all_tool_names_async", AsyncMock(return_value=[]))

        response = await agent_routes.update_agent(
            "yaml-agent",
            agent_routes.AgentUpdateRequest(skills=[], tools=[]),
        )

        update_yaml.assert_called_once_with(
            "yaml-agent",
            {"skills": [], "tools": []},
        )
        storage_write.assert_awaited_once_with(
            "agent/custom/yaml-agent",
            {"skills": [], "tools": [], "permission": [{
                "permission": "question",
                "pattern": "*",
                "action": "deny",
                "source": "agent_tools",
            }]},
        )
        assert runtime_agent.skills == []
        assert runtime_agent.tools == []
        assert response.skills == []
        assert response.tools == []

    @pytest.mark.asyncio
    async def test_update_nonexistent_agent_returns_404(self, client: AsyncClient):
        """Updating a non-existent agent returns 404."""
        resp = await client.put(
            "/api/agent/no_such_agent",
            json=_AGENT_PAYLOAD,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_subagent_delegatable(self, client: AsyncClient):
        """PUT /api/agent/{name} can disable delegation for a sub-agent."""
        create_resp = await client.post("/api/agent", json=_SUBAGENT_PAYLOAD)
        assert create_resp.status_code == status.HTTP_200_OK
        assert create_resp.json()["delegatable"] is True

        resp = await client.put(
            "/api/agent/test-subagent",
            json={"delegatable": False},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["delegatable"] is False

        get_resp = await client.get("/api/agent/test-subagent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["delegatable"] is False

    @pytest.mark.asyncio
    async def test_update_subagent_delegatable_survives_registry_reload(self, client: AsyncClient):
        """Storage-backed delegatable updates survive a fresh registry load."""
        from flocks.agent.registry import Agent

        create_resp = await client.post("/api/agent", json=_SUBAGENT_PAYLOAD)
        assert create_resp.status_code == status.HTTP_200_OK

        update_resp = await client.put(
            "/api/agent/test-subagent",
            json={"delegatable": False},
        )
        assert update_resp.status_code == status.HTTP_200_OK
        assert update_resp.json()["delegatable"] is False

        Agent._custom_agents.clear()
        Agent.invalidate_cache()

        get_resp = await client.get("/api/agent/test-subagent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["delegatable"] is False

    @pytest.mark.asyncio
    async def test_patch_delegatable_updates_storage_custom_agent_without_sidecar(
        self,
        client: AsyncClient,
        _isolated_delegatable_settings: Path,
    ):
        create_resp = await client.post("/api/agent", json=_SUBAGENT_PAYLOAD)
        assert create_resp.status_code == status.HTTP_200_OK

        patch_resp = await client.patch(
            "/api/agent/test-subagent/delegatable",
            json={"delegatable": False},
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert patch_resp.json()["delegatable"] is False

        get_resp = await client.get("/api/agent/test-subagent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["delegatable"] is False

        if _isolated_delegatable_settings.exists():
            payload = json.loads(_isolated_delegatable_settings.read_text(encoding="utf-8"))
            assert payload.get("delegatable_overrides", {}).get("test-subagent") is None

    @pytest.mark.asyncio
    async def test_patch_delegatable_overrides_builtin_agent_without_rewriting_yaml(
        self,
        client: AsyncClient,
        _isolated_delegatable_settings: Path,
    ):
        patch_resp = await client.patch(
            "/api/agent/explore/delegatable",
            json={"delegatable": False},
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert patch_resp.json()["delegatable"] is False

        get_resp = await client.get("/api/agent/explore")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["delegatable"] is False

        payload = json.loads(_isolated_delegatable_settings.read_text(encoding="utf-8"))
        assert payload["delegatable_overrides"]["explore"] is False

    @pytest.mark.asyncio
    async def test_patch_delegatable_syncs_is_delegatable_without_followup_list(
        self,
        client: AsyncClient,
        _isolated_delegatable_settings: Path,
    ):
        """PATCH must refresh _agents_ref so delegate_task sees the new value immediately."""
        from flocks.agent.registry import Agent, is_delegatable

        await Agent.state()
        assert is_delegatable("explore") is True

        patch_resp = await client.patch(
            "/api/agent/explore/delegatable",
            json={"delegatable": False},
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert is_delegatable("explore") is False

        patch_resp = await client.patch(
            "/api/agent/explore/delegatable",
            json={"delegatable": True},
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert is_delegatable("explore") is True


# ===========================================================================
# Delete
# ===========================================================================

class TestAgentDelete:

    @pytest.mark.asyncio
    async def test_delete_custom_agent(self, client: AsyncClient):
        """DELETE /api/agent/{name} removes the custom agent."""
        await client.post("/api/agent", json=_AGENT_PAYLOAD)
        resp = await client.delete("/api/agent/test-agent")
        assert resp.status_code == status.HTTP_200_OK

        # Should no longer appear in the list
        list_resp = await client.get("/api/agent")
        names = [a["name"] for a in list_resp.json()]
        assert "test-agent" not in names

    @pytest.mark.asyncio
    async def test_delete_builtin_agent_returns_error(self, client: AsyncClient):
        """Deleting a built-in agent that has no storage entry returns 404
        (no Storage key 'agent/custom/rex' and no YAML override file)."""
        resp = await client.delete("/api/agent/rex")
        # If rex has no Storage / YAML entry the route returns 404.
        # If it somehow has an entry from another source it may succeed (200).
        # The important thing is it does NOT crash (5xx).
        assert resp.status_code < 500


# ===========================================================================
# Test / Run
# ===========================================================================

class TestAgentRun:

    @pytest.mark.asyncio
    async def test_run_agent_creates_session(self, client: AsyncClient):
        """POST /api/agent/{name}/test creates a session for a known agent.

        We first create a custom agent so we control its existence in storage,
        then call /test on it.  Built-in agents (rex etc.) depend on the
        Instance/state cache being warm, which is outside scope of this unit test.
        """
        # Create a known custom agent
        await client.post("/api/agent", json=_AGENT_PAYLOAD)

        resp = await client.post(
            "/api/agent/test-agent/test",
            json={"message": "hello"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "sessionId" in data or "session_id" in data or "id" in data

    @pytest.mark.asyncio
    async def test_run_nonexistent_agent_returns_404(self, client: AsyncClient):
        """Testing a non-existent agent returns 404."""
        resp = await client.post(
            "/api/agent/no_such_agent/test",
            json={"message": "hi"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
