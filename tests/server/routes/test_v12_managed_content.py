"""Content a deployment manages replaces local Agents, Skills and Workflows of
the same name, and stays read-only through the API.

The deployment's source is an in-memory stand-in registered the way the
commercial core's is (exclusive, ahead of everything local); a local copy of
each managed item sits where a user could have dropped it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
from unittest.mock import AsyncMock

import pytest

from flocks.content import ContentBody, ContentEntry, ContentRef, register_source, reset_sources
from flocks.content.managed import ManagedContent

AGENT = "sealed-analyst"
SKILL = "sealed-triage"
WORKFLOW = "sealed-nightly"

MANAGED_AGENT = f"""---
name: {AGENT}
description: Managed analyst
mode: subagent
---
MANAGED PROMPT v1
"""

MANAGED_WORKFLOW = {
    "name": "Managed nightly",
    "start": "p",
    "nodes": [{"id": "p", "type": "python", "code": "outputs['who'] = 'managed'"}],
    "edges": [],
}


class DeploymentSource:
    def __init__(self) -> None:
        self.latest: Dict[Tuple[str, str], str] = {}
        self.texts: Dict[Tuple[str, str, str], str] = {}

    @property
    def name(self) -> str:
        return "deployment"

    def publish(self, kind: str, content_id: str, version: str, text: str) -> None:
        self.latest[(kind, content_id)] = version
        self.texts[(kind, content_id, version)] = text

    def supports(self, kind: str) -> bool:
        return kind in {"agent", "skill", "workflow"}

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        return [
            ContentEntry(ref=ContentRef(kind=k, content_id=cid, version=v), name=cid, summary=f"{cid} (managed)")
            for (k, cid), v in sorted(self.latest.items())
            if k == kind
        ]

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        version = ref.version or self.latest.get((ref.kind, ref.content_id))
        text = self.texts.get((ref.kind, ref.content_id, version or ""))
        if text is None:
            return None
        return ContentBody(ref=ContentRef(kind=ref.kind, content_id=ref.content_id, version=version), text=text)


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.agent import agent_factory, registry
    from flocks.agent.registry import Agent
    from flocks.config.config import get_user_plugins_root
    from flocks.skill.skill import Skill
    from flocks.tool.registry import ToolRegistry

    # Local copies, where a user could have put them.
    builtin = tmp_path / "builtin-agents"
    builtin.mkdir()
    plugin_agents = tmp_path / "plugin-agents"
    (plugin_agents / AGENT).mkdir(parents=True)
    (plugin_agents / AGENT / "agent.yaml").write_text(
        f"name: {AGENT}\ndescription: Local copy\nmode: subagent\nprompt: LOCAL PROMPT\n", encoding="utf-8"
    )
    plugins = get_user_plugins_root()
    (plugins / "skills" / SKILL).mkdir(parents=True)
    (plugins / "skills" / SKILL / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: Local copy\n---\nLOCAL SKILL BODY\n", encoding="utf-8"
    )
    (plugins / "workflows" / WORKFLOW).mkdir(parents=True)
    (plugins / "workflows" / WORKFLOW / "workflow.json").write_text(
        json.dumps({**MANAGED_WORKFLOW, "name": "Local copy",
                    "nodes": [{"id": "p", "type": "python", "code": "outputs['who'] = 'local'"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(agent_factory, "_BUILTIN_AGENTS_DIR", builtin)
    monkeypatch.setattr(agent_factory, "_SYSTEM_AGENT_ROOTS", (builtin,))
    monkeypatch.setattr(agent_factory, "_PLUGIN_AGENTS_DIR", plugin_agents)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry.PluginLoader, "load_extension", lambda *args, **kwargs: None)
    monkeypatch.setattr(ToolRegistry, "init_async", AsyncMock())
    monkeypatch.setattr(ToolRegistry, "list_tools", lambda: [])
    monkeypatch.setattr("flocks.workflow.center.scan_skill_workflows", AsyncMock(return_value=[]))
    monkeypatch.setattr("flocks.server.routes.agent._get_all_tool_names_async", AsyncMock(return_value=[]))

    reset_sources()
    ManagedContent.reset()
    source = DeploymentSource()
    source.publish("agent", AGENT, "1", MANAGED_AGENT)
    source.publish("skill", SKILL, "1", f"---\nname: {SKILL}\ndescription: Managed triage\n---\nMANAGED SKILL BODY v1\n")
    source.publish("workflow", WORKFLOW, "1", json.dumps(MANAGED_WORKFLOW))
    register_source(source, priority=0, exclusive_kinds=("agent", "skill", "workflow"))
    Skill.clear_cache()
    Agent.invalidate_cache()
    yield source
    reset_sources()
    ManagedContent.reset()
    Skill.clear_cache()
    Agent.invalidate_cache()


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

async def test_managed_agent_replaces_the_local_one_and_is_sealed(client, managed):
    from flocks.agent.registry import Agent

    await ManagedContent.refresh()

    agent = await Agent.get(AGENT)
    assert agent is not None
    assert agent.prompt == "MANAGED PROMPT v1"
    assert agent.prompt_protected and agent.native
    detail = await client.get(f"/api/agent/{AGENT}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["prompt"] is None and detail.json()["promptProtected"] is True
    assert (await client.get(f"/api/agent/{AGENT}/prompt")).status_code == 403
    assert (await client.put(f"/api/agent/{AGENT}", json={"description": "edited"})).status_code == 403
    assert (await client.delete(f"/api/agent/{AGENT}")).status_code == 403


async def test_a_new_managed_version_reaches_the_registry_without_a_manual_refresh(managed):
    from flocks.agent.registry import Agent

    await ManagedContent.refresh()
    assert (await Agent.get(AGENT)).prompt == "MANAGED PROMPT v1"

    managed.publish("agent", AGENT, "2", MANAGED_AGENT.replace("v1", "v2"))
    await ManagedContent.refresh()

    assert (await Agent.get(AGENT)).prompt == "MANAGED PROMPT v2"


async def test_no_runtime_registration_or_config_rewrites_a_managed_agent(managed, monkeypatch):
    from flocks.agent.agent import AgentInfo
    from flocks.agent.registry import Agent
    from flocks.config.config import Config, ConfigInfo

    await ManagedContent.refresh()
    Agent.register(AGENT, AgentInfo(name=AGENT, mode="subagent", prompt="REGISTERED", prompt_protected=True))
    cfg = ConfigInfo.model_validate({"agent": {AGENT: {"prompt": "FROM CONFIG", "description": "config"}}})
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=cfg))
    try:
        agent = (await Agent.refresh())[AGENT]
        assert agent.prompt == "MANAGED PROMPT v1"
        assert agent.description == "Managed analyst"

        # Disabling it is still a local choice.
        cfg = ConfigInfo.model_validate({"agent": {AGENT: {"disable": True}}})
        monkeypatch.setattr(Config, "get", AsyncMock(return_value=cfg))
        assert AGENT not in await Agent.refresh()
    finally:
        Agent.unregister(AGENT)


async def test_an_unusable_managed_agent_keeps_its_name_reserved(client, managed):
    from flocks.agent.registry import Agent

    managed.publish("agent", AGENT, "2", f"name: {AGENT}\nprompt_file: prompt.md\n")
    await ManagedContent.refresh()

    # Neither the local copy nor an empty stand-in answers for the name ...
    assert await Agent.get(AGENT) is None
    # ... and nothing local can be created under it.
    created = await client.post(
        "/api/agent",
        json={"name": AGENT, "description": "x", "mode": "subagent", "permission": [], "options": {}, "prompt": "MINE"},
    )
    assert created.status_code == 409, created.text


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

async def test_managed_skill_replaces_the_local_one_and_is_read_only(client, managed):
    from flocks.skill.skill import Skill

    await ManagedContent.refresh()

    skill = await Skill.get(SKILL)
    assert skill is not None and Skill.is_managed(skill)
    assert "MANAGED SKILL BODY v1" in await Skill.read_body(skill, scope="s-1")
    assert Skill.base_directory(skill) is None
    detail = await client.get(f"/api/skills/{SKILL}")
    assert detail.status_code == 200, detail.text
    assert "MANAGED SKILL BODY v1" in detail.json()["content"]
    assert "LOCAL" not in detail.json()["content"]
    assert (await client.delete(f"/api/skills/{SKILL}")).status_code == 403

    # A task keeps the version it started with.
    managed.publish("skill", SKILL, "2", f"---\nname: {SKILL}\ndescription: Managed triage\n---\nMANAGED SKILL BODY v2\n")
    await ManagedContent.refresh()
    skill = await Skill.get(SKILL)
    assert "v1" in await Skill.read_body(skill, scope="s-1")
    assert "v2" in await Skill.read_body(skill, scope="s-2")


async def test_the_skill_tool_loads_the_managed_body(managed):
    from flocks.tool.registry import ToolContext
    from flocks.tool.skill.skill_load import skill_tool_impl

    await ManagedContent.refresh()

    result = await skill_tool_impl(ToolContext(session_id="s-tool", message_id="m-1"), name=SKILL)

    text = result.output if hasattr(result, "output") else str(result)
    assert "MANAGED SKILL BODY v1" in text and "LOCAL SKILL BODY" not in text


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

async def test_managed_workflow_replaces_the_local_one_and_is_read_only(client, managed):
    from flocks.workflow.fs_store import read_workflow_from_fs, resolve_workflow_from_fs

    await ManagedContent.refresh()

    data = read_workflow_from_fs(WORKFLOW)
    assert data is not None and data["managed"] is True and data["source"] == "managed"
    assert data["workflowJson"]["nodes"][0]["code"] == "outputs['who'] = 'managed'"
    # No local directory answers for it, so nothing can be written there.
    assert resolve_workflow_from_fs(WORKFLOW) is None

    detail = await client.get(f"/api/workflow/{WORKFLOW}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["managed"] is True and detail.json()["name"] == "Managed nightly"
    listed = (await client.get("/api/workflow")).json()
    assert [item["name"] for item in listed if item["id"] == WORKFLOW] == ["Managed nightly"]
    assert (await client.put(f"/api/workflow/{WORKFLOW}", json={"name": "renamed"})).status_code == 403
    assert (await client.delete(f"/api/workflow/{WORKFLOW}")).status_code == 403


async def test_a_managed_workflow_runs_its_managed_definition(client, managed, monkeypatch):
    from flocks.server.routes import workflow as routes

    dispatched: list[dict] = []

    async def _capture(**kwargs):
        dispatched.append(kwargs["workflow_json"])

    monkeypatch.setattr(routes, "_run_workflow_execution_task", _capture)
    await ManagedContent.refresh()

    ran = await client.post(f"/api/workflow/{WORKFLOW}/run", json={"inputs": {}})

    assert ran.status_code == 200, ran.text
    for _ in range(50):
        if dispatched:
            break
        await asyncio.sleep(0.01)
    assert dispatched and dispatched[0]["nodes"][0]["code"] == "outputs['who'] = 'managed'"


async def test_no_legacy_record_stands_in_for_an_unusable_managed_subworkflow(managed):
    from flocks.storage.storage import Storage
    from flocks.workflow.engine import _default_workflow_loader
    from flocks.workflow.errors import NodeExecutionError

    managed.publish("workflow", WORKFLOW, "2", "- not\n- a mapping\n")
    await ManagedContent.refresh()
    await Storage.write(f"workflow/{WORKFLOW}", {"workflowJson": MANAGED_WORKFLOW})

    with pytest.raises(NodeExecutionError, match="Workflow not found"):
        _default_workflow_loader(WORKFLOW)


async def test_the_workflow_center_lists_the_managed_workflow_and_never_its_local_copy(managed):
    from flocks.config.config import get_user_plugins_root
    from flocks.workflow import center

    await ManagedContent.refresh()

    entries = center._managed_center_entries()

    assert [entry["workflowId"] for entry in entries] == [WORKFLOW]
    assert entries[0]["sourceType"] == "managed"

    local = get_user_plugins_root() / "workflows" / WORKFLOW / "workflow.json"
    assert center._load_discoverable_workflow(local) is None
    with pytest.raises(center.WorkflowCenterError, match="managed by the deployment"):
        center._refuse_managed_publish(local)


async def test_no_skill_can_be_installed_over_a_managed_one(managed):
    from flocks.skill import installer
    from flocks.skill.installer import SkillInstaller

    await ManagedContent.refresh()

    # Even an install that may overwrite: a local copy would never load.
    token = installer._OVERWRITE_ALLOWED.set(True)  # secret-guard: allow 误报：ContextVar token
    try:
        result = SkillInstaller._save_skill_content(
            f"---\nname: {SKILL}\ndescription: replacement\n---\nREPLACEMENT\n", "user",
        )
    finally:
        installer._OVERWRITE_ALLOWED.reset(token)

    assert result.success is False
    assert result.error.startswith("Skill already exists") and "managed" in result.error


async def test_the_workflow_tool_runs_a_managed_workflow_by_its_listed_locator(managed):
    from flocks.content.managed import managed_location
    from flocks.tool.registry import ToolContext
    from flocks.tool.task.run_workflow import run_workflow_tool

    await ManagedContent.refresh()

    result = await run_workflow_tool(
        ToolContext(session_id="s-wf", message_id="m-wf"),
        workflow=managed_location("workflow", WORKFLOW),
        inputs={},
        use_llm=False,
        ensure_requirements=False,
    )

    assert result.success, result.error
    assert "managed" in json.dumps(result.output, default=str)
    assert "local" not in json.dumps(result.output, default=str)


# ---------------------------------------------------------------------------
# Nothing writes a managed workflow's definition, and nothing runs its local copy
# ---------------------------------------------------------------------------

def _local_copy() -> Path:
    from flocks.config.config import get_user_plugins_root

    return get_user_plugins_root() / "workflows" / WORKFLOW / "workflow.json"


async def test_operations_that_rewrite_the_definition_refuse_a_managed_workflow(client, managed):
    await ManagedContent.refresh()
    before = _local_copy().read_text(encoding="utf-8")

    trigger = {"id": "t1", "type": "webhook", "source": {}}
    responses = {
        "trigger create": await client.post(f"/api/workflow/{WORKFLOW}/triggers", json=trigger),
        "trigger update": await client.put(f"/api/workflow/{WORKFLOW}/triggers/t1", json=trigger),
        "trigger delete": await client.delete(f"/api/workflow/{WORKFLOW}/triggers/t1"),
        "sample inputs": await client.post(f"/api/workflow/{WORKFLOW}/sample-inputs", json={"sampleInputs": {"a": 1}}),
        "publish": await client.post(f"/api/workflow/{WORKFLOW}/publish"),
    }

    assert {name: response.status_code for name, response in responses.items()} == {name: 403 for name in responses}
    # Neither the deployment's definition nor anything else landed on disk.
    assert _local_copy().read_text(encoding="utf-8") == before


async def test_the_definition_writer_itself_refuses_a_managed_id(managed):
    from flocks.server.routes.workflow import _write_workflow_to_fs
    from flocks.workflow.fs_store import ManagedWorkflowReadOnly

    await ManagedContent.refresh()

    with pytest.raises(ManagedWorkflowReadOnly):
        _write_workflow_to_fs(WORKFLOW, MANAGED_WORKFLOW, {"name": "x"})


async def test_legacy_records_are_not_migrated_under_a_managed_id(managed):
    from flocks.config.config import get_user_plugins_root
    from flocks.server.routes.workflow import _find_workspace_root, _migrate_storage_to_filesystem
    from flocks.storage.storage import Storage

    managed.publish("workflow", "sealed-other", "1", json.dumps(MANAGED_WORKFLOW))
    await ManagedContent.refresh()
    await Storage.write("workflow/sealed-other", {"name": "legacy", "workflowJson": MANAGED_WORKFLOW})
    marker = _find_workspace_root() / ".flocks" / ".storage_migrated"
    if marker.exists():
        marker.unlink()

    await _migrate_storage_to_filesystem()

    for root in (get_user_plugins_root() / "workflows", _find_workspace_root() / ".flocks" / "plugins" / "workflows"):
        assert not (root / "sealed-other").exists()


async def test_the_trigger_runtime_sees_the_managed_definition(managed):
    from flocks.workflow.triggers.runtime import default_runtime

    await ManagedContent.refresh()

    workflows = {item["id"]: item for item in default_runtime._iter_workflows()}

    assert workflows[WORKFLOW]["managed"] is True
    assert workflows[WORKFLOW]["workflowJson"]["nodes"][0]["code"] == "outputs['who'] = 'managed'"


async def test_a_local_copys_path_runs_the_managed_workflow(managed):
    from flocks.tool.registry import ToolContext
    from flocks.tool.task.run_workflow import run_workflow_tool
    from flocks.tool.task.run_workflow_node import _load_workflow_dict

    await ManagedContent.refresh()

    assert _load_workflow_dict(str(_local_copy()))["nodes"][0]["code"] == "outputs['who'] = 'managed'"
    result = await run_workflow_tool(
        ToolContext(session_id="s-path", message_id="m-path"),
        workflow=str(_local_copy()),
        inputs={},
        use_llm=False,
        ensure_requirements=False,
    )
    assert result.success, result.error
    assert "managed" in json.dumps(result.output, default=str)
    assert "local" not in json.dumps(result.output, default=str)


async def test_the_hub_does_not_install_local_copies_of_managed_items(managed):
    from flocks.hub import installer

    await ManagedContent.refresh()

    assert installer._is_managed_ref("skill", SKILL)
    assert installer._is_managed_ref("agent", AGENT)
    assert not installer._is_managed_ref("tool", SKILL)
    with pytest.raises(PermissionError, match="managed by the deployment"):
        await installer._install_plugin("workflow", WORKFLOW)



# ---------------------------------------------------------------------------
# Review round 1 (batch 2): permissions, withdrawal, consumers, logs
# ---------------------------------------------------------------------------

async def test_global_permission_limits_bind_a_managed_agent(managed, monkeypatch):
    from flocks.agent.registry import Agent
    from flocks.config.config import Config, ConfigInfo
    from flocks.permission.next import PermissionNext

    managed.publish("agent", AGENT, "2", MANAGED_AGENT.replace(
        "mode: subagent\n", "mode: subagent\npermission:\n  bash: allow\n"))
    await ManagedContent.refresh()
    cfg = ConfigInfo.model_validate({"permission": {"bash": "deny"}})
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=cfg))

    agent = (await Agent.refresh())[AGENT]

    assert PermissionNext.evaluate("bash", "*", agent.permission) == "deny"


async def test_a_withdrawn_item_stays_reserved_across_a_restart(client, managed):
    from flocks.agent.registry import Agent
    from flocks.skill.skill import Skill
    from flocks.workflow.fs_store import read_workflow_from_fs

    await ManagedContent.refresh()
    for key in list(managed.latest):
        del managed.latest[key]
    await ManagedContent.refresh()

    async def nothing_local_answers():
        assert await Agent.get(AGENT) is None
        assert await Skill.get(SKILL) is None
        assert read_workflow_from_fs(WORKFLOW) is None
        assert (await client.put(f"/api/workflow/{WORKFLOW}", json={"name": "mine"})).status_code == 403

    await nothing_local_answers()
    # A restart forgets the snapshot, not the reservations.
    ManagedContent.reset()
    reset_sources()
    register_source(managed, priority=0, exclusive_kinds=("agent", "skill", "workflow"))
    await ManagedContent.ensure_loaded(attempts=1, delay=0)
    await nothing_local_answers()


async def test_consumers_of_a_managed_workflow_restart_when_it_changes(managed, monkeypatch):
    from flocks.workflow.triggers.runtime import default_runtime

    await ManagedContent.refresh()
    restarted: list[tuple[str, dict]] = []

    async def _restart(workflow_id, workflow_json=None):
        restarted.append((workflow_id, workflow_json))
        return {}

    monkeypatch.setattr(default_runtime, "restart_workflow", _restart)
    ManagedContent.add_change_listener(default_runtime._on_managed_content_changed)

    managed.publish("workflow", WORKFLOW, "2", json.dumps({**MANAGED_WORKFLOW, "name": "v2"}))
    await ManagedContent.refresh()
    del managed.latest[("workflow", WORKFLOW)]
    await ManagedContent.refresh()

    assert [(wid, (js or {}).get("name")) for wid, js in restarted] == [(WORKFLOW, "v2"), (WORKFLOW, None)]


async def test_a_definition_that_fails_to_parse_is_not_quoted_in_the_log(managed, monkeypatch):
    from flocks.agent import managed as agent_managed
    from flocks.workflow import fs_store

    logged: list[str] = []
    monkeypatch.setattr(agent_managed.log, "error", lambda event, data=None: logged.append(repr(data)))
    monkeypatch.setattr(fs_store.log, "error", lambda event, data=None: logged.append(repr(data)))
    secret = "SEALED-PROMPT-MARKER"  # secret-guard: allow 测脱敏的假标记
    managed.publish("agent", AGENT, "2", f"name: {AGENT}\nprompt: {secret}: broken: [\n")
    managed.publish("workflow", WORKFLOW, "2", f"name: {secret}: broken: [\n")
    await ManagedContent.refresh()

    assert agent_managed.managed_agents() == {}
    assert fs_store.managed_workflow_data(WORKFLOW) is None
    assert logged and all(secret not in line for line in logged)



# ---------------------------------------------------------------------------
# Review round 2 (batch 2): reservations, consumer retries, node runner
# ---------------------------------------------------------------------------

async def test_reservations_from_two_instances_both_survive(managed):
    from flocks.content.managed import RESERVATIONS_PREFIX
    from flocks.storage.storage import Storage

    await ManagedContent.refresh()  # this instance reserves AGENT, SKILL, WORKFLOW
    # Another instance (its own memory) reserves something else on the same database.
    ManagedContent.reset()
    reset_sources()
    other = DeploymentSource()
    other.publish("skill", "other-skill", "1", "---\nname: other-skill\n---\nbody")
    register_source(other, priority=0, exclusive_kinds=("agent", "skill", "workflow"))
    await ManagedContent.refresh()

    keys = set(await Storage.list_keys(RESERVATIONS_PREFIX))
    assert {f"{RESERVATIONS_PREFIX}skill/{SKILL}", f"{RESERVATIONS_PREFIX}skill/other-skill"} <= keys
    assert {SKILL, "other-skill"} <= ManagedContent.ids("skill")


async def test_an_unsaved_reservation_is_saved_by_a_later_refresh(managed, monkeypatch):
    from flocks.content.managed import RESERVATIONS_PREFIX
    from flocks.storage.storage import Storage

    real_write = Storage.write
    failures = {"left": 3}

    async def _flaky(key, content):
        if failures["left"] and str(key).startswith(RESERVATIONS_PREFIX):
            failures["left"] -= 1
            raise RuntimeError("database briefly unavailable")
        return await real_write(key, content)

    monkeypatch.setattr(Storage, "write", _flaky)
    await ManagedContent.refresh()
    assert ManagedContent._unsaved, "the failed writes are remembered"

    await ManagedContent.refresh()  # nothing changed, the reservations are still saved

    assert not ManagedContent._unsaved
    assert f"{RESERVATIONS_PREFIX}agent/{AGENT}" in await Storage.list_keys(RESERVATIONS_PREFIX)


async def test_startup_refuses_when_reservations_cannot_be_read(managed, monkeypatch):
    from flocks.content.managed import ManagedContentUnavailable
    from flocks.storage.storage import Storage

    async def _broken(prefix=None):
        raise RuntimeError("cannot read")

    monkeypatch.setattr(Storage, "list_keys", _broken)

    with pytest.raises(ManagedContentUnavailable, match="reservations"):
        await ManagedContent.ensure_loaded(attempts=1, delay=0)


async def test_reservations_read_late_still_hide_local_copies(managed, monkeypatch):
    from flocks.content.managed import RESERVATIONS_PREFIX
    from flocks.skill.skill import Skill
    from flocks.storage.storage import Storage

    # An earlier instance reserved a skill that is no longer listed; this one
    # could not read the reservations at first and cached the local copy.
    await Storage.write(f"{RESERVATIONS_PREFIX}skill/{SKILL}", {"reserved_at": 0})
    del managed.latest[("skill", SKILL)]
    real_list_keys = Storage.list_keys
    calls = {"n": 0}

    async def _late(prefix=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("not yet")
        return await real_list_keys(prefix)

    monkeypatch.setattr(Storage, "list_keys", _late)
    await ManagedContent.refresh()
    assert (await Skill.get(SKILL)) is not None  # the local copy, reservations unknown yet

    await ManagedContent.refresh()  # reservations readable now

    assert await Skill.get(SKILL) is None


# ---------------------------------------------------------------------------
# Review round 3 (batch 2): reservations other instances record, unsaved ones at startup
# ---------------------------------------------------------------------------

async def test_a_reservation_another_instance_records_later_holds_here_too(managed):
    from flocks.content.managed import RESERVATIONS_PREFIX
    from flocks.skill.skill import Skill
    from flocks.storage.storage import Storage

    # This instance never saw the skill listed: its local copy loads.
    del managed.latest[("skill", SKILL)]
    await ManagedContent.ensure_loaded(attempts=1, delay=0)
    assert (await Skill.get(SKILL)) is not None

    # Another instance saw it listed, reserved it, and then saw it withdrawn.
    await Storage.write(f"{RESERVATIONS_PREFIX}skill/{SKILL}", {"reserved_at": 0})
    await ManagedContent.refresh()

    assert SKILL in ManagedContent.ids("skill")
    assert await Skill.get(SKILL) is None


async def test_startup_refuses_when_a_new_reservation_cannot_be_saved(managed, monkeypatch):
    from flocks.content.managed import RESERVATIONS_PREFIX, ManagedContentUnavailable
    from flocks.storage.storage import Storage

    real_write = Storage.write

    async def _refusing(key, content):
        if str(key).startswith(RESERVATIONS_PREFIX):
            raise RuntimeError("database read-only")
        return await real_write(key, content)

    monkeypatch.setattr(Storage, "write", _refusing)

    with pytest.raises(ManagedContentUnavailable, match="could not be saved"):
        await ManagedContent.ensure_loaded(attempts=1, delay=0)

    # Once the database takes them, the start goes through.
    monkeypatch.setattr(Storage, "write", real_write)
    await ManagedContent.ensure_loaded(attempts=1, delay=0)
    assert not ManagedContent._unsaved


async def test_a_failed_consumer_rebuild_stops_the_old_plan_and_is_retried(managed, monkeypatch):
    from flocks.ingest.kafka.manager import default_manager as kafka_manager
    from flocks.workflow.triggers.runtime import default_runtime

    stopped: list[str] = []
    attempts: list[str] = []

    async def _stop(workflow_id):
        stopped.append(workflow_id)

    async def _restart(workflow_id, workflow_json=None):
        attempts.append(workflow_id)
        if len(attempts) == 1:
            raise RuntimeError("config store unavailable")
        return {}

    monkeypatch.setattr(kafka_manager, "stop_workflow", _stop)
    monkeypatch.setattr(default_runtime, "restart_workflow", _restart)
    monkeypatch.setattr(default_runtime, "_managed_pending", set())
    await ManagedContent.refresh()
    ManagedContent.add_change_listener(default_runtime._on_managed_content_changed)

    managed.publish("workflow", WORKFLOW, "2", json.dumps({**MANAGED_WORKFLOW, "name": "v2"}))
    await ManagedContent.refresh()
    assert attempts == [WORKFLOW] and WORKFLOW in stopped  # failed, but the old plan stopped
    assert default_runtime._managed_pending == {WORKFLOW}

    await ManagedContent.refresh()  # nothing changed: the pending rebuild is retried

    assert attempts == [WORKFLOW, WORKFLOW]
    assert default_runtime._managed_pending == set()


async def test_a_consumer_that_answers_failed_keeps_the_rebuild_pending_and_only_it_is_retried(managed, monkeypatch):
    from flocks.ingest.kafka.manager import default_manager as kafka_manager
    from flocks.ingest.syslog.manager import default_manager as syslog_manager
    from flocks.workflow.poller_manager import default_manager as poller_manager
    from flocks.workflow.triggers.runtime import default_runtime

    full_rebuilds: list[str] = []
    syslog_retries: list[str] = []
    kafka_restarts: list[str] = []

    async def _stop(workflow_id):
        return None

    async def _restart(workflow_id, workflow_json=None):
        full_rebuilds.append(workflow_id)
        # The old plan's syslog workers are still draining; Kafka came up.
        return {
            "syslog": {"state": "failed", "error": "previous_workers_still_draining"},
            "kafka": {"state": "running", "error": None},
            "schedule": {"state": "stopped", "error": None},
        }

    async def _syslog_restart(workflow_id):
        syslog_retries.append(workflow_id)
        return {"state": "listening", "error": None}

    async def _kafka_restart(workflow_id):
        kafka_restarts.append(workflow_id)
        return {"state": "running", "error": None}

    for manager in (syslog_manager, kafka_manager, poller_manager):
        monkeypatch.setattr(manager, "stop_workflow", _stop)
    monkeypatch.setattr(default_runtime, "restart_workflow", _restart)
    monkeypatch.setattr(syslog_manager, "restart_workflow", _syslog_restart)
    monkeypatch.setattr(kafka_manager, "restart_workflow", _kafka_restart)
    monkeypatch.setattr(default_runtime, "_managed_pending", set())
    monkeypatch.setattr(default_runtime, "_managed_failed", {}, raising=False)
    await ManagedContent.refresh()
    ManagedContent.add_change_listener(default_runtime._on_managed_content_changed)

    managed.publish("workflow", WORKFLOW, "2", json.dumps({**MANAGED_WORKFLOW, "name": "v2"}))
    await ManagedContent.refresh()
    assert full_rebuilds == [WORKFLOW]
    assert default_runtime._managed_pending == {WORKFLOW}

    await ManagedContent.refresh()  # nothing changed: only syslog is tried again

    assert full_rebuilds == [WORKFLOW] and syslog_retries == [WORKFLOW] and kafka_restarts == []
    assert default_runtime._managed_pending == set()
    assert default_runtime._managed_failed == {}


async def test_a_custom_adapter_that_fails_after_starting_is_started_again(managed, monkeypatch):
    """Review round 2 (batch 3 recheck): a start() that raises later is not a consumer that started."""
    from types import SimpleNamespace

    from flocks.workflow.triggers import runtime as runtime_module
    from flocks.workflow.triggers.runtime import default_runtime

    started: list[int] = []
    running = asyncio.Event()

    class _Adapter:
        def __init__(self, trigger):
            self.attempt = len(started) + 1
            started.append(self.attempt)

        async def start(self, trigger, emit):
            await asyncio.sleep(0)
            if self.attempt == 1:
                raise ConnectionError("broker unreachable")
            running.set()
            await asyncio.Event().wait()

        def stop(self):
            return None

    plugin = {"id": "test-adapter"}
    monkeypatch.setattr(runtime_module, "list_trigger_plugins", lambda: [plugin])
    monkeypatch.setattr(runtime_module, "load_trigger_plugin_module",
                        lambda spec: SimpleNamespace(create_trigger_adapter=_Adapter))
    for name in ("_managed_pending", "_custom_adapter_tasks", "_custom_adapters", "_custom_status",
                 "_custom_adapter_signatures"):
        monkeypatch.setattr(default_runtime, name, set() if name == "_managed_pending" else {})
    monkeypatch.setattr(default_runtime, "_managed_failed", {}, raising=False)
    await ManagedContent.refresh()
    ManagedContent.add_change_listener(default_runtime._on_managed_content_changed)

    with_adapter = {
        **MANAGED_WORKFLOW,
        "name": "v2",
        "triggers": [{"id": "feed", "type": "custom_adapter", "enabled": True, "source": {"adapterId": "test-adapter"}}],
    }
    managed.publish("workflow", WORKFLOW, "2", json.dumps(with_adapter))
    try:
        await ManagedContent.refresh()  # rebuilt; the adapter's start() fails a moment later
        for _ in range(50):
            if default_runtime._custom_status.get((WORKFLOW, "feed"), {}).get("state") == "failed":
                break
            await asyncio.sleep(0.01)
        assert default_runtime._managed_pending == {WORKFLOW}

        await ManagedContent.refresh()  # nothing changed: the adapter is started again
        await asyncio.wait_for(running.wait(), timeout=2)

        assert started == [1, 2]
        assert default_runtime._managed_pending == set()
    finally:
        for key in list(default_runtime._custom_adapter_tasks):
            await default_runtime._stop_custom_adapter(*key)


async def test_the_node_runner_does_not_fall_back_to_a_file_for_an_unavailable_id(managed, tmp_path, monkeypatch):
    from flocks.tool.task.run_workflow_node import _load_workflow_dict

    await ManagedContent.refresh()
    del managed.latest[("workflow", WORKFLOW)]
    await ManagedContent.refresh()
    monkeypatch.chdir(tmp_path)
    (tmp_path / WORKFLOW).write_text(json.dumps({**MANAGED_WORKFLOW, "name": "local file"}), encoding="utf-8")

    with pytest.raises(ValueError, match="unavailable from its managed source"):
        _load_workflow_dict(WORKFLOW)
