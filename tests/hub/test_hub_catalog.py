import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.hub import local
from flocks.hub.catalog import list_catalog, load_manifest, load_taxonomy
from flocks.hub.files import file_tree, read_file_content
from flocks.hub.installer import install_plugin, uninstall_plugin
from flocks.plugin.loader import PluginLoader


@pytest.fixture()
def isolated_hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    project_dir = tmp_path / "project"
    home.mkdir()
    config_dir.mkdir()
    data_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    (config_dir / "flocks.json").write_text(json.dumps({}), encoding="utf-8")

    from flocks.config.config import Config
    from flocks.skill.skill import Skill

    Config._global_config = None
    Config._cached_config = None
    Skill.clear_cache()
    yield {"home": home, "config_dir": config_dir, "data_dir": data_dir, "project_dir": project_dir}
    Skill.clear_cache()


def _patch_webui_bundle_build(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from flocks.contracts.webui.models import WebUIPageBuildMeta

    built_pages: list[str] = []

    def fake_build(self, page_id: str):
        page_dir = self._store.writable_page_dir(page_id)
        bundle_path = page_dir / "dist" / "page.js"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_text(f"// built during hub install: {page_id}\n", encoding="utf-8")
        meta = WebUIPageBuildMeta(
            hash=f"fake-{page_id}",
            builtAt=1,
            status="ready",
            error=None,
            runtime="webui_page",
            runtimeVersion=1,
            sdkImport="@flocks/webui-contract-sdk",
        )
        self._store.write_build_meta(page_id, meta)
        built_pages.append(page_id)
        return meta

    monkeypatch.setattr("flocks.contracts.webui.builder.WebUIPageBuilder.build", fake_build)
    return built_pages


def test_bundled_hub_catalog_loads():
    entries = list_catalog()
    assert entries
    # ``device`` is a first-class Hub type alongside skill/agent/tool/workflow:
    # entries with ``integration_type: device`` in ``_provider.yaml`` surface
    # under ``type=device`` instead of ``type=tool``.
    assert {entry.type for entry in entries} >= {"skill", "agent", "tool", "device", "workflow", "webui", "component"}


def test_hub_catalog_snapshot_reuses_manifest_parse_for_counts(monkeypatch: pytest.MonkeyPatch):
    from flocks.hub import catalog as catalog_module

    catalog_module.clear_catalog_caches()
    original_read_yaml = catalog_module._read_yaml
    calls = 0

    def counted_read_yaml(path: Path):
        nonlocal calls
        calls += 1
        return original_read_yaml(path)

    monkeypatch.setattr(catalog_module, "_read_yaml", counted_read_yaml)

    assert catalog_module.list_catalog()
    initial_calls = calls
    assert initial_calls > 0

    catalog_module.category_counts()
    catalog_module.list_catalog(plugin_type="device")

    assert calls == initial_calls


def test_workflow_catalog_exposes_chinese_names():
    entries = {entry.id: entry for entry in list_catalog(plugin_type="workflow")}

    assert entries["stream_alert_denoise"].nameCn == "流式HTTP降噪工作流"
    assert entries["stream_alert_triage"].nameCn == "HTTP研判工作流"
    assert entries["loop_host_forensics_fast"].nameCn == "批量主机快速巡检工作流"
    assert entries["tdp_alert_triage"].nameCn == "TDP 告警调查工作流"


def test_soc_workspace_component_exposes_chinese_name():
    entries = {entry.id: entry for entry in list_catalog(plugin_type="component")}

    assert entries["soc-workspace"].nameCn == "SOC 工作区场景套件"


def test_pentest_agents_are_listed_in_agent_catalog():
    entries = list_catalog(plugin_type="agent")
    ids = {entry.id for entry in entries}

    assert "pentest-ai-agents" not in ids
    assert "web-hunter" in ids
    assert "cloud-security" in ids
    assert "swarm-orchestrator" in ids


def test_catalog_query_matches_description_cn():
    entries = list_catalog(plugin_type="agent", q="目录发现")
    ids = {entry.id for entry in entries}
    assert "web-hunter" in ids


def test_project_builtin_plugins_are_listed_as_installed():
    entries = list_catalog()
    by_key = {(entry.type, entry.id): entry for entry in entries}

    assert by_key[("skill", "tdp-use")].state == "installed"
    assert by_key[("skill", "tdp-use")].native is True
    assert by_key[("agent", "ndr-analyst")].state == "installed"
    assert by_key[("workflow", "tdp_alert_triage")].state == "installed"
    # ``tdp_v3_3_10`` declares ``integration_type: device`` in
    # ``_provider.yaml``, so it surfaces as a ``device`` plugin (not
    # ``tool``) in the Hub catalog.
    assert by_key[("device", "tdp_v3_3_10")].state == "installed"

    manifest = load_manifest("skill", "tdp-use")
    assert manifest.id == "tdp-use"
    tree = file_tree("skill", "tdp-use")
    assert any(child.name == "SKILL.md" for child in tree.children)


def test_bundled_hub_taxonomy_loads():
    taxonomy = load_taxonomy()
    assert taxonomy.categories
    assert "ndr" in taxonomy.tags
    assert "alert-triage" in taxonomy.useCases


def test_bundled_hub_manifest_and_files_load():
    manifest = load_manifest("skill", "ndr-alert-analysis")
    assert manifest.id == "ndr-alert-analysis"
    tree = file_tree("skill", "ndr-alert-analysis")
    assert any(child.name == "SKILL.md" for child in tree.children)
    content = read_file_content("skill", "ndr-alert-analysis", "SKILL.md")
    assert "NDR" in content.content

    nested_manifest = load_manifest("skill", "triaging-security-incident")
    assert nested_manifest.id == "triaging-security-incident"
    nested_tree = file_tree("skill", "triaging-security-incident")
    assert any(child.name == "SKILL.md" for child in nested_tree.children)
    nested_content = read_file_content("skill", "triaging-security-incident", "SKILL.md")
    assert "Triaging Security Incidents" in nested_content.content

    agent_manifest = load_manifest("agent", "web-hunter")
    assert agent_manifest.id == "web-hunter"
    agent_tree = file_tree("agent", "web-hunter")
    assert any(child.name == "agent.yaml" for child in agent_tree.children)
    agent_content = read_file_content("agent", "web-hunter", "agent.yaml")
    assert "name: web-hunter" in agent_content.content


async def test_hub_installs_and_uninstalls_skill(isolated_hub_env):
    record = await install_plugin("skill", "ndr-alert-analysis")
    skill_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "ndr-alert-analysis"
    assert (skill_dir / "SKILL.md").is_file()
    assert record.enabled is True

    removed = await uninstall_plugin("skill", "ndr-alert-analysis")
    assert removed is True
    assert not skill_dir.exists()


async def test_hub_installs_nested_anthropic_skill(isolated_hub_env):
    record = await install_plugin("skill", "triaging-security-incident")
    skill_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "triaging-security-incident"
    assert (skill_dir / "SKILL.md").is_file()
    assert record.id == "triaging-security-incident"

    removed = await uninstall_plugin("skill", "triaging-security-incident")
    assert removed is True
    assert not skill_dir.exists()


async def test_hub_installs_pentest_subagent(isolated_hub_env):
    record = await install_plugin("agent", "web-hunter")
    agent_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "agents" / "web-hunter"
    assert (agent_dir / "agent.yaml").is_file()
    assert (agent_dir / "prompt.md").is_file()
    assert record.id == "web-hunter"

    removed = await uninstall_plugin("agent", "web-hunter")
    assert removed is True
    assert not agent_dir.exists()


async def test_hub_installs_soc_webui_package(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    built_pages = _patch_webui_bundle_build(monkeypatch)

    record = await install_plugin("webui", "soc_ui")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    access_dir = home_plugins / "contracts" / "access" / "soc_ui"
    dashboard_manifest = json.loads((webui_dir / "soc_dashboard" / "manifest.json").read_text(encoding="utf-8"))

    assert (webui_dir / "workspace.json").is_file()
    assert (webui_dir / "soc_alerts" / "dist" / "page.js").is_file()
    assert (webui_dir / "soc_alerts" / "dist" / "page.js").read_text(encoding="utf-8") == (
        "// built during hub install: soc-alerts\n"
    )
    assert (access_dir / "soc_alerts_operations.py").is_file()
    assert set(built_pages) == {"soc-alerts", "soc-dashboard", "soc-overview"}
    assert dashboard_manifest["id"] == "soc-dashboard"
    assert record.installPath == str(webui_dir)

    removed = await uninstall_plugin("webui", "soc_ui")
    assert removed is True
    assert not webui_dir.exists()
    assert not access_dir.exists()


async def test_hub_webui_install_fails_when_bundle_build_fails(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type):
        return None

    def fail_build(_self, _page_id: str):
        raise RuntimeError("esbuild is not available; install webui dependencies first")

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    monkeypatch.setattr("flocks.contracts.webui.builder.WebUIPageBuilder.build", fail_build)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"

    with pytest.raises(RuntimeError, match="Failed to build WebUI page bundle for soc_ui/"):
        await install_plugin("webui", "soc_ui")

    assert not (home_plugins / "contracts" / "webui" / "soc_ui").exists()
    assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()
    assert local.get_record("webui", "soc_ui") is None


async def test_hub_installed_soc_webui_registers_alert_access_contract(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    await install_plugin("webui", "soc_ui")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    monkeypatch.setattr(PluginLoader, "_plugin_root", home_plugins)
    monkeypatch.setattr(PluginLoader, "_extension_points", dict(PluginLoader._extension_points))
    PluginLoader.clear_extension_points()

    from flocks.contracts.access.discovery import discover_contract_plugins

    plugins = discover_contract_plugins(project_dir=isolated_hub_env["project_dir"])

    assert any(
        contract.contract_id == "soc.alerts.operations" and contract.page_id == "soc-alerts"
        for plugin in plugins
        for contract in plugin.contracts
    )


async def test_hub_installed_soc_webui_serves_alert_access_operation(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    db_path = isolated_hub_env["data_dir"] / "soc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": "alert-1",
        "time": 1782888542,
        "direction": "in",
        "sip": "192.0.2.10",
        "dip": "198.51.100.20",
        "sport": 43123,
        "dport": 80,
        "net_type": "http",
        "req_host": "example.test",
        "req_http_url": "/login?id=1",
        "rsp_status_code": 404,
        "threat_rule_id": "D1181087257",
        "threat_name": "SQL injection",
        "threat_msg": "Detected SQL injection attempt.",
        "threat_phase": "exploit",
        "threat_type": "exploit",
        "threat_result": "failed",
        "_source_type": "tdp",
        "is_duplicate": False,
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE alert_records (
                row_id TEXT PRIMARY KEY,
                record_id TEXT,
                asset_date TEXT NOT NULL,
                source_file TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                event_time INTEGER,
                source_type TEXT,
                threat_name TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                record_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO alert_records (
                row_id, record_id, asset_date, source_file, line_number,
                event_time, source_type, threat_name, is_duplicate, record_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "row-1",
                "alert-1",
                "2026-07-01",
                "sample.jsonl",
                1,
                1782888542,
                "tdp",
                "SQL injection",
                0,
                json.dumps(record),
            ),
        )

    monkeypatch.setenv("FLOCKS_SOC_ALERTS_SQLITE_DB", str(db_path))
    await install_plugin("webui", "soc_ui")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    monkeypatch.setattr(PluginLoader, "_plugin_root", home_plugins)
    monkeypatch.setattr(PluginLoader, "_extension_points", dict(PluginLoader._extension_points))
    PluginLoader.clear_extension_points()

    from flocks.auth.context import AuthUser
    from flocks.contracts.access.discovery import discover_contract_plugins
    from flocks.contracts.access.runtime import OperationRuntime

    runtime = OperationRuntime(plugins=discover_contract_plugins(project_dir=isolated_hub_env["project_dir"]))
    response = runtime.execute(
        page_id="soc-alerts",
        contract_id="soc.alerts.operations",
        operation_name="list",
        payload={"params": {"limit": 10}},
        principal=AuthUser(id="u1", username="admin", role="admin"),
    )

    assert response.status_code == 200
    assert response.body["summary"]["totalRaw"] == 1
    assert response.body["summary"]["attackFailed"] == 1
    assert response.body["incidents"][0]["id"] == "alert-1"
    assert response.body["incidents"][0]["tableCells"]["_source_type"]["value"] == "tdp"


async def test_hub_installs_soc_workspace_component_children(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    built_pages = _patch_webui_bundle_build(monkeypatch)

    record = await install_plugin("component", "soc-workspace")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"

    assert (home_plugins / "components" / "soc-workspace" / "component.json").is_file()
    assert (home_plugins / "contracts" / "webui" / "soc_ui" / "workspace.json").is_file()
    assert (home_plugins / "contracts" / "access" / "soc_ui" / "soc_alerts_operations.py").is_file()
    assert "soc-alerts" in built_pages
    assert (home_plugins / "tools" / "python" / "soc_workspace_query" / "soc_workspace_query.py").is_file()
    assert (home_plugins / "workflows" / "stream_alert_denoise" / "guide.md").is_file()
    assert (home_plugins / "workflows" / "stream_alert_triage" / "config.json").is_file()
    assert record.id == "soc-workspace"

    removed = await uninstall_plugin("component", "soc-workspace")
    assert removed is True
    assert not (home_plugins / "components" / "soc-workspace").exists()
    assert not (home_plugins / "contracts" / "webui" / "soc_ui").exists()
    assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()
    assert not (home_plugins / "tools" / "python" / "soc_workspace_query").exists()
    assert not (home_plugins / "workflows" / "stream_alert_denoise").exists()
    assert not (home_plugins / "workflows" / "stream_alert_triage").exists()


async def test_hub_component_uninstall_preserves_existing_children(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    triage_dir = home_plugins / "workflows" / "stream_alert_triage"

    await install_plugin("webui", "soc_ui")
    assert (webui_dir / "workspace.json").is_file()
    assert local.get_record("webui", "soc_ui").installedBy is None

    await install_plugin("component", "soc-workspace")
    assert (home_plugins / "components" / "soc-workspace" / "component.json").is_file()
    assert (triage_dir / "config.json").is_file()
    assert local.get_record("workflow", "stream_alert_triage").installedBy == "component:soc-workspace"

    removed = await uninstall_plugin("component", "soc-workspace")
    assert removed is True
    assert not (home_plugins / "components" / "soc-workspace").exists()
    assert (webui_dir / "workspace.json").is_file()
    assert not triage_dir.exists()


async def test_hub_component_adopts_existing_soc_workspace_tool(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    tool_dir = home_plugins / "tools" / "python" / "soc_workspace_query"

    await install_plugin("tool", "soc_workspace_query")
    assert (tool_dir / "soc_workspace_query.py").is_file()
    assert local.get_record("tool", "soc_workspace_query").installedBy is None

    await install_plugin("component", "soc-workspace")

    assert local.get_record("tool", "soc_workspace_query").installedBy == "component:soc-workspace"

    removed = await uninstall_plugin("component", "soc-workspace")

    assert removed is True
    assert local.get_record("tool", "soc_workspace_query") is None
    assert not tool_dir.exists()


async def test_hub_component_uninstall_cleans_adoptable_legacy_tool_record(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    await install_plugin("component", "soc-workspace")

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    tool_dir = home_plugins / "tools" / "python" / "soc_workspace_query"
    tool_record = local.get_record("tool", "soc_workspace_query")
    assert tool_record is not None
    assert tool_record.installedBy == "component:soc-workspace"
    local.save_installed_record(tool_record.model_copy(update={"installedBy": None}))

    removed = await uninstall_plugin("component", "soc-workspace")

    assert removed is True
    assert local.get_record("tool", "soc_workspace_query") is None
    assert not tool_dir.exists()


async def test_hub_component_uninstall_cleans_legacy_unrecorded_webui(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    webui_access_dir = home_plugins / "contracts" / "access" / "soc_ui"

    await install_plugin("webui", "soc_ui")
    local.remove_installed_record("webui", "soc_ui")
    assert (webui_dir / "workspace.json").is_file()
    assert local.get_record("webui", "soc_ui") is None

    await install_plugin("component", "soc-workspace")
    removed = await uninstall_plugin("component", "soc-workspace")

    assert removed is True
    assert not (home_plugins / "components" / "soc-workspace").exists()
    assert not webui_dir.exists()
    assert not webui_access_dir.exists()


async def test_hub_component_install_failure_rolls_back_children(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def fail_tool_refresh(plugin_type):
        if plugin_type == "tool":
            raise RuntimeError("tool refresh failed")
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", fail_tool_refresh)
    _patch_webui_bundle_build(monkeypatch)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    webui_access_dir = home_plugins / "contracts" / "access" / "soc_ui"
    tool_dir = home_plugins / "tools" / "python" / "soc_workspace_query"
    component_dir = home_plugins / "components" / "soc-workspace"

    with pytest.raises(RuntimeError, match="tool refresh failed"):
        await install_plugin("component", "soc-workspace")

    assert not component_dir.exists()
    assert not webui_dir.exists()
    assert not webui_access_dir.exists()
    assert not tool_dir.exists()
    assert local.get_record("component", "soc-workspace") is None
    assert local.get_record("webui", "soc_ui") is None
    assert local.get_record("tool", "soc_workspace_query") is None


async def test_hub_component_uninstall_cleans_orphan_children(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    component_key = "component:soc-workspace"
    await install_plugin("webui", "soc_ui", installed_by=component_key)
    await install_plugin("tool", "soc_workspace_query", installed_by=component_key)
    await install_plugin("workflow", "stream_alert_denoise", installed_by=component_key)
    await install_plugin("workflow", "stream_alert_triage", installed_by=component_key)

    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    component_dir = home_plugins / "components" / "soc-workspace"

    assert not component_dir.exists()
    assert local.get_record("component", "soc-workspace") is None
    assert local.get_record("webui", "soc_ui").installedBy == component_key

    removed = await uninstall_plugin("component", "soc-workspace")

    assert removed is True
    assert local.get_record("webui", "soc_ui") is None
    assert local.get_record("tool", "soc_workspace_query") is None
    assert local.get_record("workflow", "stream_alert_denoise") is None
    assert local.get_record("workflow", "stream_alert_triage") is None
    assert not (home_plugins / "contracts" / "webui" / "soc_ui").exists()
    assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()
    assert not (home_plugins / "tools" / "python" / "soc_workspace_query").exists()
    assert not (home_plugins / "workflows" / "stream_alert_denoise").exists()
    assert not (home_plugins / "workflows" / "stream_alert_triage").exists()


async def test_hub_uninstalls_python_tool_without_record(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)

    record = await install_plugin("tool", "soc_workspace_query")
    tool_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "tools" / "python" / "soc_workspace_query"

    assert record.installPath == str(tool_dir)
    assert (tool_dir / "soc_workspace_query.py").is_file()
    local.remove_installed_record("tool", "soc_workspace_query")

    removed = await uninstall_plugin("tool", "soc_workspace_query")
    assert removed is True
    assert not tool_dir.exists()


async def test_catalog_clears_stale_skill_record_after_external_delete(isolated_hub_env):
    await install_plugin("skill", "ndr-alert-analysis")
    skill_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "ndr-alert-analysis"
    assert (skill_dir / "SKILL.md").is_file()

    import shutil

    shutil.rmtree(skill_dir)
    entries = list_catalog(plugin_type="skill")
    entry = next(item for item in entries if item.id == "ndr-alert-analysis")
    assert entry.state == "available"
    assert local.get_record("skill", "ndr-alert-analysis") is None


def test_hub_routes_cover_catalog_files_install_and_uninstall(isolated_hub_env):
    from flocks.server.routes.hub import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)

    catalog = client.get("/api/hub/catalog").json()
    assert any(item["id"] == "ndr-alert-analysis" for item in catalog)

    catalog_page = client.get("/api/hub/catalog", params={"limit": 1, "offset": 0}).json()
    assert isinstance(catalog_page, dict)
    assert len(catalog_page["items"]) == 1
    assert catalog_page["total"] == len(catalog)
    assert catalog_page["limit"] == 1
    assert catalog_page["facets"]["type"]
    assert catalog_page["facets"]["state"]

    taxonomy = client.get("/api/hub/categories", params={"include_counts": False}).json()
    assert taxonomy["tags"]
    assert "counts" not in taxonomy

    detail = client.get("/api/hub/plugins/skill/ndr-alert-analysis").json()
    assert detail["id"] == "ndr-alert-analysis"

    files = client.get("/api/hub/plugins/skill/ndr-alert-analysis/files").json()
    assert any(child["name"] == "SKILL.md" for child in files["children"])

    content = client.get(
        "/api/hub/plugins/skill/ndr-alert-analysis/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 200
    assert "NDR" in content.json()["content"]

    traversal = client.get(
        "/api/hub/plugins/skill/ndr-alert-analysis/files/content",
        params={"path": "../taxonomy.json"},
    )
    assert traversal.status_code == 400

    installed = client.post("/api/hub/plugins/skill/ndr-alert-analysis/install", json={"scope": "global"})
    assert installed.status_code == 200
    assert installed.json()["id"] == "ndr-alert-analysis"

    installed_catalog = client.get("/api/hub/catalog", params={"state": "installed"}).json()
    assert any(item["id"] == "ndr-alert-analysis" for item in installed_catalog)

    removed = client.delete("/api/hub/plugins/skill/ndr-alert-analysis")
    assert removed.status_code == 200
    available_catalog = client.get("/api/hub/catalog", params={"state": "available"}).json()
    assert any(item["id"] == "ndr-alert-analysis" for item in available_catalog)


def test_hub_refresh_clears_catalog_and_device_template_caches(monkeypatch):
    from flocks.server.routes import hub as hub_routes

    calls: list[str] = []
    monkeypatch.setattr(hub_routes, "clear_catalog_caches", lambda: calls.append("catalog"))
    monkeypatch.setattr(
        "flocks.tool.device.plugin_index.clear_device_template_cache",
        lambda: calls.append("device"),
    )

    hub_routes._clear_hub_runtime_caches()

    assert calls == ["catalog", "device"]


def test_hub_component_install_stream_reports_child_progress(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)

    response = client.post("/api/hub/plugins/component/soc-workspace/install/stream", json={"scope": "global"})

    assert response.status_code == 200
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert frames[0]["event"] == "start"
    assert frames[0]["type"] == "component"
    assert frames[0]["id"] == "soc-workspace"
    assert [item["status"] for item in frames[0]["items"]] == ["pending", "pending", "pending", "pending"]

    installed_children = {
        (frame["item"]["type"], frame["item"]["id"])
        for frame in frames
        if frame["event"] == "item" and frame["item"]["status"] == "installed"
    }
    assert installed_children == {
        ("webui", "soc_ui"),
        ("tool", "soc_workspace_query"),
        ("workflow", "stream_alert_denoise"),
        ("workflow", "stream_alert_triage"),
    }
    assert frames[-1]["event"] == "complete"
    assert frames[-1]["record"]["id"] == "soc-workspace"


def test_hub_routes_legacy_removed_plugins_return_gone(isolated_hub_env):
    from flocks.server.routes.hub import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)

    response = client.get("/api/hub/plugins/agent/alert-triage-agent")
    assert response.status_code == 410
    assert "removed" in response.json()["detail"]
