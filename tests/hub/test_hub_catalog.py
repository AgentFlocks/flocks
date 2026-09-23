import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.hub import local
from flocks.hub.catalog import list_catalog, load_manifest, load_taxonomy
from flocks.hub.files import file_tree, read_file_content
from flocks.hub.update_protection import build_plan
from flocks.hub.installer import install_plugin, uninstall_plugin, update_plugin
from flocks.plugin.loader import PluginLoader



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


async def test_chaitin_safeline_repair_release_replaces_broken_handler(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    plugin_id = "chaitin_safeline_waf_v1_0_0"
    install_dir = (
        isolated_hub_env["home"]
        / ".flocks"
        / "plugins"
        / "tools"
        / "device"
        / plugin_id
    )
    install_dir.mkdir(parents=True)
    legacy_handler = install_dir / "chaitin_leichi_waf.handler.py"
    legacy_handler.write_text("def broken(:\n", encoding="utf-8")

    entries = {entry.id: entry for entry in list_catalog(plugin_type="device")}
    assert entries[plugin_id].state == "updateAvailable"
    assert entries[plugin_id].version == "1.0.1"
    assert entries[plugin_id].installedVersion is None

    record = await update_plugin("device", plugin_id)

    repaired_handler = install_dir / "chaitin_safeline_waf.handler.py"
    assert record.version == "1.0.1"
    assert local.get_record("device", plugin_id) == record
    assert not legacy_handler.exists()
    compile(repaired_handler.read_text(encoding="utf-8"), str(repaired_handler), "exec")


async def test_hub_install_runtime_failure_rolls_back_new_plugin(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_refresh(_plugin_type, _changed_path=None):
        raise RuntimeError("import failed")

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", fail_refresh)
    install_dir = (
        isolated_hub_env["home"]
        / ".flocks"
        / "plugins"
        / "tools"
        / "python"
        / "soc_workspace_query"
    )

    with pytest.raises(RuntimeError, match="import failed"):
        await install_plugin("tool", "soc_workspace_query")

    assert not install_dir.exists()
    assert local.get_record("tool", "soc_workspace_query") is None


async def test_hub_update_runtime_failure_restores_previous_plugin(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    install_dir = (
        isolated_hub_env["home"]
        / ".flocks"
        / "plugins"
        / "tools"
        / "python"
        / "soc_workspace_query"
    )
    install_dir.mkdir(parents=True)
    old_handler = install_dir / "old_tool.py"
    old_handler.write_text("OLD = True\n", encoding="utf-8")
    previous_record = local.make_record(
        plugin_type="tool",
        plugin_id="soc_workspace_query",
        version="0.9.0",
        source="bundled:old",
        install_path=install_dir,
        scope="global",
    )
    local.save_installed_record(previous_record)
    refresh_calls = 0

    async def fail_then_recover(_plugin_type, _changed_path=None):
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise RuntimeError("import failed")

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", fail_then_recover)

    with pytest.raises(RuntimeError, match="import failed"):
        await update_plugin("tool", "soc_workspace_query", confirmation_token=build_plan("tool", "soc_workspace_query")["token"], confirm_changes=True)

    assert refresh_calls == 2
    assert old_handler.read_text(encoding="utf-8") == "OLD = True\n"
    assert not (install_dir / "soc_workspace_query.py").exists()
    assert local.get_record("tool", "soc_workspace_query") == previous_record


async def test_hub_webui_runtime_failure_rolls_back_package_and_access_contracts(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_refresh(_plugin_type, _changed_path=None):
        raise RuntimeError("reconcile failed")

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", fail_refresh)
    _patch_webui_bundle_build(monkeypatch)
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"

    with pytest.raises(RuntimeError, match="reconcile failed"):
        await install_plugin("webui", "soc_ui")

    assert not (home_plugins / "contracts" / "webui" / "soc_ui").exists()
    assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()
    assert local.get_record("webui", "soc_ui") is None


def test_catalog_ignores_stale_record_when_legacy_install_is_inferred(
    isolated_hub_env,
):
    plugin_id = "chaitin_safeline_waf_v1_0_0"
    legacy_dir = (
        isolated_hub_env["home"]
        / ".flocks"
        / "plugins"
        / "tools"
        / "device"
        / plugin_id
    )
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.yaml").write_text(
        "name: legacy\nhandler:\n  type: http\n",
        encoding="utf-8",
    )
    stale_record = local.make_record(
        plugin_type="device",
        plugin_id=plugin_id,
        version="1.0.1",
        source="bundled:stale",
        install_path=isolated_hub_env["home"] / "missing",
        scope="global",
    )
    local.save_installed_record(stale_record)

    entries = {entry.id: entry for entry in list_catalog(plugin_type="device")}

    assert entries[plugin_id].state == "updateAvailable"
    assert entries[plugin_id].installedVersion is None
    assert entries[plugin_id].installPath == str(legacy_dir)
    assert local.get_record("device", plugin_id) is None


def test_catalog_uses_webui_workspace_version_for_inferred_installs(
    isolated_hub_env,
):
    from flocks.hub.catalog import clear_catalog_caches

    webui_dir = (
        isolated_hub_env["home"]
        / ".flocks"
        / "plugins"
        / "contracts"
        / "webui"
        / "soc_ui"
    )
    webui_dir.mkdir(parents=True)
    workspace_path = webui_dir / "workspace.json"
    workspace = {
        "id": "soc_ui",
        "version": "1.0.0",
        "title": "SOC 工作区",
        "placement": "sceneWorkspace",
    }
    workspace_path.write_text(json.dumps(workspace), encoding="utf-8")
    clear_catalog_caches()

    bundled_version = load_manifest("webui", "soc_ui").version
    entry = {item.id: item for item in list_catalog(plugin_type="webui")}["soc_ui"]

    assert entry.version == bundled_version
    assert entry.state == "updateAvailable"
    assert entry.installedVersion == "1.0.0"

    workspace["version"] = bundled_version
    workspace_path.write_text(json.dumps(workspace), encoding="utf-8")

    refreshed = {item.id: item for item in list_catalog(plugin_type="webui")}["soc_ui"]

    assert refreshed.state == "installed"
    assert refreshed.installedVersion == bundled_version


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
    # Hub-only packages remain installable; a same-named readonly native Skill
    # (such as ndr-alert-analysis) must not be shadowed by this install path.
    record = await install_plugin("skill", "triaging-security-incident")
    skill_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "triaging-security-incident"
    assert (skill_dir / "SKILL.md").is_file()
    assert record.enabled is True

    removed = await uninstall_plugin("skill", "triaging-security-incident")
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
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    built_pages = _patch_webui_bundle_build(monkeypatch)

    record = await install_plugin("webui", "soc_ui")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    access_dir = home_plugins / "contracts" / "access" / "soc_ui"
    dashboard_manifest = json.loads((webui_dir / "soc_dashboard" / "manifest.json").read_text(encoding="utf-8"))
    workspace_manifest = json.loads((webui_dir / "workspace.json").read_text(encoding="utf-8"))

    assert (webui_dir / "workspace.json").is_file()
    assert (webui_dir / "soc_alerts" / "dist" / "page.js").is_file()
    assert (webui_dir / "soc_alerts" / "dist" / "page.js").read_text(encoding="utf-8") == (
        "// built during hub install: soc-alerts\n"
    )
    assert (access_dir / "soc_alerts_operations.py").is_file()
    assert set(built_pages) == {"soc-alerts", "soc-dashboard", "soc-overview"}
    assert dashboard_manifest["id"] == "soc-dashboard"
    assert workspace_manifest["version"] == record.version == load_manifest("webui", "soc_ui").version
    assert record.installPath == str(webui_dir)

    removed = await uninstall_plugin("webui", "soc_ui")
    assert removed is True
    assert not webui_dir.exists()
    assert not access_dir.exists()


async def test_hub_webui_install_fails_when_bundle_build_fails(
    isolated_hub_env,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
        "threat_severity": "critical",
        "threat_level": "high",
        "threat_phase": "exploit",
        "threat_type": "exploit",
        "threat_result": "success",
        "attack_verdict": "attack_success",
        "attack_success": True,
        "triage_attack_verdict": "attack_failed",
        "triage_attack_success": False,
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
    assert response.body["incidents"][0]["triageAttackVerdict"] == "attack"
    assert response.body["incidents"][0]["triageAttackSuccess"] == "failed"
    assert response.body["incidents"][0]["conclusion"]["verdict"] == "attack"
    assert response.body["incidents"][0]["tableCells"]["threat_result"]["value"] == "success"
    assert response.body["incidents"][0]["tableCells"]["attack_success"]["value"] == "true"
    assert response.body["incidents"][0]["tableCells"]["_source_type"]["value"] == "tdp"
    assert response.body["incidents"][0]["tableCells"]["threat_severity"]["value"] == "critical"
    assert response.body["incidents"][0]["tableCells"]["threat_level"]["value"] == "high"

    filtered = runtime.execute(
        page_id="soc-alerts",
        contract_id="soc.alerts.operations",
        operation_name="list",
        payload={
            "params": {
                "filters": {
                    "threat_severity": ["critical"],
                    "threat_level": ["high"],
                },
                "limit": 10,
            }
        },
        principal=AuthUser(id="u1", username="admin", role="admin"),
    )

    assert filtered.status_code == 200
    assert filtered.body["summary"]["representativeCount"] == 1
    assert [incident["id"] for incident in filtered.body["incidents"]] == ["alert-1"]

    filtered_by_model_result = runtime.execute(
        page_id="soc-alerts",
        contract_id="soc.alerts.operations",
        operation_name="list",
        payload={
            "params": {
                "filters": {
                    "triage_attack_verdict": ["attack"],
                    "triage_attack_success": ["failed"],
                },
                "limit": 10,
            }
        },
        principal=AuthUser(id="u1", username="admin", role="admin"),
    )

    assert filtered_by_model_result.status_code == 200
    assert [incident["id"] for incident in filtered_by_model_result.body["incidents"]] == ["alert-1"]

    for filters in (
        {"threat_severity": ["low"]},
        {"threat_level": ["low"]},
    ):
        excluded = runtime.execute(
            page_id="soc-alerts",
            contract_id="soc.alerts.operations",
            operation_name="list",
            payload={"params": {"filters": filters, "limit": 10}},
            principal=AuthUser(id="u1", username="admin", role="admin"),
        )

        assert excluded.status_code == 200
        assert excluded.body["summary"]["representativeCount"] == 0
        assert excluded.body["incidents"] == []


async def test_hub_installs_soc_workspace_component_children(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    async def fail_tool_refresh(plugin_type, _changed_path=None):
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
    async def noop_refresh(_plugin_type, _changed_path=None):
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


async def test_scene_suite_http_install_uninstall_is_repeatable(isolated_hub_env):
    from httpx import ASGITransport, AsyncClient

    from flocks.contracts.webui.builder import resolve_esbuild_bin
    from flocks.contracts.webui.store import WebUIPagesStore
    from flocks.server.auth import require_admin, require_user
    from flocks.server.routes.hub import router

    if resolve_esbuild_bin() is None:
        pytest.skip("The real WebUI build requires webui/node_modules/.bin/esbuild")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[require_user] = lambda: object()
    endpoint = "/hub/plugins/component/soc-workspace"
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            installed = await client.post(f"{endpoint}/install", json={"scope": "global"}, timeout=30)
            assert installed.status_code == 200, installed.text
            workspace = next(item for item in WebUIPagesStore().list_workspaces() if item.id == "soc_ui")
            assert workspace.enabled is True
            assert workspace.pages
            suites = await client.get("/hub/scene-suites", timeout=30)
            suite = next(item for item in suites.json() if item["id"] == "soc-workspace")
            assert suite["state"] == "installed"
            assert suite["workspaceEnabled"] is True

            removed = await client.delete(endpoint, timeout=30)
            assert removed.status_code == 200, removed.text
            assert removed.json() == {"removed": True}
            assert not local.load_installed_records()
            assert not WebUIPagesStore().list_workspaces()
            assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()

        repeated = await client.delete(endpoint, timeout=30)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == {"removed": False}


@pytest.mark.parametrize(
    ("scenario", "expected_state", "expected_enabled"),
    [
        ("complete", "installed", True),
        ("missing_shell", "partial", True),
        ("missing_webui", "partial", None),
        ("disabled", "installed", False),
        ("available", "available", None),
        ("missing_records", "updateAvailable", True),
        ("child_only", "partial", True),
        ("missing_workflow", "partial", True),
        ("missing_tool", "partial", True),
        ("missing_optional_workflow", "installed", True),
    ],
)
async def test_scene_suite_state_reflects_real_payload_completeness(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
    scenario: str, expected_state: str, expected_enabled: bool | None,
):
    from httpx import ASGITransport, AsyncClient

    from flocks.contracts.webui.store import WebUIPagesStore
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.server.auth import require_user
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    if scenario != "available":
        await install_plugin("component", "soc-workspace")
    preserved_tool = local.get_record("tool", "soc_workspace_query")
    if scenario == "missing_shell":
        shell = local.install_dir("component", "soc-workspace") / "component.json"
        shell.rename(isolated_hub_env["home"] / "component-backup.json")
    elif scenario == "missing_webui":
        local.install_dir("webui", "soc_ui").rename(isolated_hub_env["home"] / "webui-backup")
    elif scenario in {"missing_workflow", "missing_optional_workflow"}:
        local.install_dir("workflow", "stream_alert_triage").rename(isolated_hub_env["home"] / "workflow-backup")
        if scenario == "missing_optional_workflow":
            def manifest_with_optional_workflow(plugin_type, plugin_id):
                manifest = load_manifest(plugin_type, plugin_id)
                if (plugin_type, plugin_id) == ("component", "soc-workspace"):
                    return manifest.model_copy(update={"components": [
                        ref.model_copy(update={"optional": True}) if ref.id == "stream_alert_triage" else ref
                        for ref in manifest.components
                    ]})
                return manifest

            monkeypatch.setattr("flocks.hub.catalog.load_manifest", manifest_with_optional_workflow)
    elif scenario == "missing_tool":
        Path(preserved_tool.installPath).rename(isolated_hub_env["home"] / "tool-backup")
    elif scenario == "disabled":
        WebUIPagesStore().set_workspace_enabled("soc_ui", False)
    elif scenario in {"missing_records", "child_only"}:
        local._record_path().rename(isolated_hub_env["home"] / "installed-backup.json")
        if scenario == "child_only":
            local.install_dir("component", "soc-workspace").rename(isolated_hub_env["home"] / "component-backup")
    clear_catalog_caches()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/hub/scene-suites", timeout=30)
        assert response.status_code == 200
        suite = next(item for item in response.json() if item["id"] == "soc-workspace")
        assert suite["state"] == expected_state
        assert suite["workspaceEnabled"] is expected_enabled
        if scenario in {"available", "missing_shell", "missing_records", "child_only"}:
            assert suite["installedVersion"] is None
        else:
            assert suite["installedVersion"] == load_manifest("component", "soc-workspace").version

        if expected_state == "partial":
            plan = build_plan("component", "soc-workspace")
            await install_plugin("component", "soc-workspace", confirmation_token=plan["token"], confirm_changes=True)
            repaired = await client.get("/hub/scene-suites", timeout=30)
            repaired_suite = next(item for item in repaired.json() if item["id"] == "soc-workspace")
            assert repaired_suite["state"] == "installed"
            assert repaired_suite["workspaceEnabled"] is True
            if scenario not in {"child_only", "missing_tool"}:
                assert local.get_record("tool", "soc_workspace_query") == preserved_tool


@pytest.mark.parametrize("action", ["install", "install/stream", "update", "uninstall"])
@pytest.mark.parametrize("fail_after_change", [False, True])
async def test_scene_suite_mutations_notify_other_sse_subscribers(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
    action: str, fail_after_change: bool,
):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from flocks.server.auth import require_admin
    from flocks.server.routes import hub as hub_routes
    from flocks.server.routes.event import EventBroadcaster

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    if action in {"update", "uninstall"} or fail_after_change:
        await install_plugin("component", "soc-workspace")
    if fail_after_change:
        async def fail_after_payload_change(*args, **kwargs):
            local.install_dir("webui", "soc_ui").rename(isolated_hub_env["home"] / "failed-webui-backup")
            raise RuntimeError("fixture failure after changing installed payload")

        operation = "install_plugin" if action.startswith("install") else f"{action}_plugin"
        monkeypatch.setattr(hub_routes, operation, fail_after_payload_change)

    broadcaster = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", broadcaster)
    published_states = []
    original_publish = broadcaster.publish

    async def publish_and_capture_state(event):
        if event["type"] == "hub.scene_suites.changed":
            suites = await hub_routes.hub_scene_suites()
            published_states.append(next(suite.state for suite in suites if suite.id == "soc-workspace"))
        await original_publish(event)

    monkeypatch.setattr(broadcaster, "publish", publish_and_capture_state)
    subscriber = await broadcaster.subscribe()
    app = FastAPI()
    app.include_router(hub_routes.router)
    app.dependency_overrides[require_admin] = lambda: object()
    endpoint = "/hub/plugins/component/soc-workspace"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            if action == "uninstall":
                response = await client.delete(endpoint, timeout=30)
            else:
                body = {"scope": "global"}
                if action == "update":
                    body.update(confirmationToken=build_plan("component", "soc-workspace")["token"], confirmChanges=True)
                response = await client.post(f"{endpoint}/{action}", json=body, timeout=30)
        assert response.status_code == (422 if fail_after_change and action != "install/stream" else 200)
        if fail_after_change:
            assert "fixture failure after changing installed payload" in response.text
        event = await asyncio.wait_for(subscriber.get(), timeout=1)
        assert event["type"] == "hub.scene_suites.changed"
        assert event["properties"] == {"suiteId": "soc-workspace", "action": action.split("/")[0]}
        assert subscriber.empty()
        assert published_states == ["partial" if fail_after_change else "available" if action == "uninstall" else "installed"]
    finally:
        await broadcaster.unsubscribe(subscriber)


async def test_suite_uninstall_cleans_access_when_required_webui_payload_is_missing(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    await install_plugin("component", "soc-workspace")
    home_plugins = isolated_hub_env["home"] / ".flocks" / "plugins"
    webui_dir = home_plugins / "contracts" / "webui" / "soc_ui"
    webui_dir.rename(isolated_hub_env["home"] / "soc_ui_missing_payload_backup")

    assert await uninstall_plugin("component", "soc-workspace") is True
    assert not local.load_installed_records()
    assert not (home_plugins / "contracts" / "access" / "soc_ui").exists()
    assert await uninstall_plugin("component", "soc-workspace") is False


@pytest.mark.parametrize("encoded_id", ["%2e%2e", "%2e", "%2e%2e%5Csoc_ui", "C%3Asoc_ui"])
async def test_uninstall_rejects_unsafe_plugin_ids_before_any_cleanup(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, encoded_id: str,
):
    from unittest.mock import Mock

    from httpx import ASGITransport, AsyncClient

    from flocks.server.auth import require_admin
    from flocks.server.routes.hub import router

    cleanup = Mock(return_value=False)
    monkeypatch.setattr("flocks.hub.installer._remove_attached_access_contracts", cleanup)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(f"/hub/plugins/webui/{encoded_id}", timeout=30)
    assert response.status_code == 422
    cleanup.assert_not_called()


@pytest.mark.parametrize("target_kind", [
    "parent", "sibling", "outside", "outside_missing", "symlink", "symlink_managed",
])
async def test_uninstall_rejects_record_paths_outside_the_exact_plugin_location(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, target_kind: str,
):
    from unittest.mock import Mock

    from flocks.hub import installer

    plugin_id = "isolated-test"
    expected = local.install_dir("webui", plugin_id)
    expected.parent.mkdir(parents=True, exist_ok=True)
    if target_kind == "parent":
        target = expected.parent
    elif target_kind == "sibling":
        target = expected.parent / "different-plugin"
        target.mkdir()
    else:
        target = isolated_hub_env["home"] / "outside"
        if target_kind != "outside_missing":
            target.mkdir()
        if target_kind == "symlink":
            expected.symlink_to(target, target_is_directory=True)
            target = expected
        elif target_kind == "symlink_managed":
            expected.mkdir()
            alias = target / "managed-alias"
            alias.symlink_to(expected, target_is_directory=True)
            target = alias
    local.save_installed_record(local.make_record(
        plugin_type="webui", plugin_id=plugin_id, version="1.0.0",
        source="test", install_path=target,
    ))
    removal = Mock()
    monkeypatch.setattr(installer.shutil, "rmtree", removal)
    cleanup = Mock(return_value=False)
    monkeypatch.setattr(installer, "_remove_attached_access_contracts", cleanup)
    with pytest.raises(ValueError, match="user-managed|path|location"):
        await uninstall_plugin("webui", plugin_id)
    removal.assert_not_called()
    cleanup.assert_not_called()
    assert target.exists() is (target_kind != "outside_missing")
    assert local.get_record("webui", plugin_id) is not None


@pytest.mark.parametrize("payload_present", [False, True])
async def test_uninstall_rejects_access_symlinks_before_removing_payload(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, payload_present: bool,
):
    from unittest.mock import Mock

    from flocks.hub import installer

    plugin_id = "isolated-test"
    payload = local.install_dir("webui", plugin_id)
    if payload_present:
        payload.mkdir(parents=True)
    outside = isolated_hub_env["home"] / "outside-access"
    outside.mkdir()
    access = local.install_root("webui").parent / "access" / plugin_id
    access.parent.mkdir(parents=True, exist_ok=True)
    access.symlink_to(outside, target_is_directory=True)
    local.save_installed_record(local.make_record(
        plugin_type="webui", plugin_id=plugin_id, version="1.0.0",
        source="test", install_path=payload,
    ))
    removal = Mock()
    monkeypatch.setattr(installer.shutil, "rmtree", removal)
    with pytest.raises(ValueError, match="Access contract path"):
        await uninstall_plugin("webui", plugin_id)
    removal.assert_not_called()
    assert payload.exists() is payload_present
    assert outside.is_dir()
    assert local.get_record("webui", plugin_id) is not None


@pytest.mark.parametrize("owned_by_suite", [False, True])
async def test_scene_suite_infers_orphans_only_from_its_own_workflow_records(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, owned_by_suite: bool,
):
    from flocks.server.routes.hub import hub_scene_suites

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    record = await install_plugin(
        "workflow", "stream_alert_triage",
        installed_by="component:soc-workspace" if owned_by_suite else None,
    )
    suite = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    assert suite.state == ("partial" if owned_by_suite else "available")
    assert suite.workspaceEnabled is None
    if not owned_by_suite:
        assert await uninstall_plugin("component", "soc-workspace") is False
        assert local.get_record("workflow", "stream_alert_triage") == record
        assert Path(record.installPath).is_dir()


@pytest.mark.parametrize(("child_type", "child_id"), [
    ("webui", "soc_ui"),
    ("tool", "soc_workspace_query"),
    ("workflow", "stream_alert_denoise"),
    ("workflow", "stream_alert_triage"),
])
@pytest.mark.parametrize(("update_mode", "version_state"), [
    ("suite", "older"), ("suite", "unrecorded"), ("suite", "current"), ("suite", "newer"),
    ("child", "older"), ("child", "unrecorded"),
])
async def test_suite_update_follows_child_versions_without_bumping_suite(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
    child_type: str, child_id: str, version_state: str, update_mode: str,
):
    from httpx import ASGITransport, AsyncClient

    from flocks.server.auth import require_admin, require_user
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    suite_record = await install_plugin("component", "soc-workspace")
    before_records = local.load_installed_records()
    child_record = local.get_record(child_type, child_id)
    child_dir = Path(child_record.installPath)
    old_file = child_dir / "old-release.txt"
    old_file.write_text("old release payload", encoding="utf-8")
    payload_path = child_dir / {
        "webui": "soc_overview/src/index.tsx",
        "tool": "soc_workspace_query.py",
        "workflow": "workflow.json",
    }[child_type]
    latest_payload = payload_path.read_bytes()
    payload_path.write_bytes(latest_payload + b"\n")
    if version_state == "unrecorded":
        local.remove_installed_record(child_type, child_id)
        if child_type == "webui":
            workspace_file = child_dir / "workspace.json"
            workspace = json.loads(workspace_file.read_text(encoding="utf-8"))
            workspace["version"] = "0.0.1"
            workspace_file.write_text(json.dumps(workspace), encoding="utf-8")
    elif version_state in {"older", "newer"}:
        local.save_installed_record(child_record.model_copy(update={
            "version": "0.0.1" if version_state == "older" else "999.0.0",
        }))
    needs_update = version_state in {"older", "unrecorded"}
    expected_state = "updateAvailable" if needs_update else "installed"
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[require_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/hub/scene-suites")
        suite = next(item for item in response.json() if item["id"] == "soc-workspace")
        assert suite["state"] == expected_state
        assert suite["installedVersion"] == suite["version"] == suite_record.version
        catalog = await client.get("/hub/catalog")
        assert next(item for item in catalog.json() if item["id"] == "soc-workspace")["state"] == expected_state
        assert next(item for item in catalog.json() if item["id"] == child_id)["state"] == expected_state

        target = "component/soc-workspace" if update_mode == "suite" else f"{child_type}/{child_id}"
        preview = await client.post(f"/hub/plugins/{target}/update/preview")
        assert preview.status_code == 200
        assert preview.json()["requiresConfirmation"] is True
        response = await client.post(f"/hub/plugins/{target}/update", json={
            "confirmationToken": preview.json()["token"], "confirmChanges": True,
        })
        assert response.status_code == 200, response.text
        updated = local.get_record(child_type, child_id)
        assert updated.version == ("999.0.0" if version_state == "newer" else child_record.version)
        if version_state != "unrecorded" or update_mode == "suite":
            assert updated.installedBy == child_record.installedBy
        assert old_file.exists() is (not needs_update)
        assert payload_path.read_bytes() == (latest_payload if needs_update else latest_payload + b"\n")
        for key, record in before_records.items():
            if key not in {"component:soc-workspace", f"{child_type}:{child_id}"}:
                assert local.load_installed_records()[key] == record
        response = await client.get("/hub/scene-suites")
        assert next(item for item in response.json() if item["id"] == "soc-workspace")["state"] == "installed"
        catalog = await client.get("/hub/catalog")
        assert next(item for item in catalog.json() if item["id"] == child_id)["state"] == "installed"


@pytest.mark.parametrize(("child_type", "child_id", "payload_file"), [
    ("workflow", "stream_alert_triage", "workflow.json"),
    ("tool", "soc_workspace_query", "soc_workspace_query.py"),
])
@pytest.mark.parametrize("update_mode", ["suite", "child"])
async def test_official_child_update_does_not_copy_project_override(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
    child_type: str, child_id: str, payload_file: str, update_mode: str,
):
    import shutil

    from flocks.hub.catalog import clear_catalog_caches, system_plugin_root

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    await install_plugin("component", "soc-workspace")
    record = local.get_record(child_type, child_id)
    installed_dir = Path(record.installPath)
    official_payload = (installed_dir / payload_file).read_bytes()
    project_dir = local.install_dir(child_type, child_id, "project")
    if child_type == "tool":
        project_dir = local.install_root("tool", "project") / "python" / child_id
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(installed_dir, project_dir)
    (project_dir / payload_file).write_bytes(official_payload + b"\n\n")
    clear_catalog_caches()
    assert system_plugin_root(child_type, child_id) == project_dir
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    (installed_dir / payload_file).write_bytes(official_payload + b"\n")
    if update_mode == "suite":
        await update_plugin("component", "soc-workspace", confirmation_token=build_plan("component", "soc-workspace")["token"], confirm_changes=True)
    else:
        await update_plugin(child_type, child_id, confirmation_token=build_plan(child_type, child_id)["token"], confirm_changes=True)
    assert (installed_dir / payload_file).read_bytes() == official_payload
    assert (project_dir / payload_file).read_bytes() == official_payload + b"\n\n"
    assert local.get_record(child_type, child_id).version == record.version


async def test_updating_a_workflow_notifies_scene_suite_subscribers(isolated_hub_env, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from flocks.server.auth import require_admin
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    await install_plugin("component", "soc-workspace")
    record = local.get_record("workflow", "stream_alert_triage")
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    events = []

    async def capture_event(event_type, properties):
        events.append((event_type, properties))

    monkeypatch.setattr("flocks.server.routes.event.publish_event", capture_event)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/hub/plugins/workflow/stream_alert_triage/update", json={
            "confirmationToken": build_plan("workflow", "stream_alert_triage")["token"], "confirmChanges": True,
        })
        assert response.status_code == 200, response.text
    assert ("hub.scene_suites.changed", {"suiteId": "soc-workspace", "action": "update"}) in events


async def test_suite_child_update_failure_preserves_old_payload_and_update_badge(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.server.routes.hub import hub_scene_suites

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    suite_record = await install_plugin("component", "soc-workspace")
    child_record = local.get_record("workflow", "stream_alert_triage").model_copy(update={"version": "0.0.1"})
    local.save_installed_record(child_record)
    old_payload = Path(child_record.installPath) / "workflow.json"
    old_payload.write_text('{"name": "previous release"}', encoding="utf-8")

    async def fail_workflow_refresh(plugin_type, _changed_path=None):
        if plugin_type == "workflow":
            raise RuntimeError("workflow refresh failed")

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", fail_workflow_refresh)
    with pytest.raises(RuntimeError, match="workflow refresh failed"):
        await update_plugin("component", "soc-workspace", confirmation_token=build_plan("component", "soc-workspace")["token"], confirm_changes=True)
    assert old_payload.read_text(encoding="utf-8") == '{"name": "previous release"}'
    assert local.get_record("workflow", "stream_alert_triage") == child_record
    assert local.get_record("component", "soc-workspace") == suite_record
    clear_catalog_caches()
    suite = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    assert suite.state == "updateAvailable"


async def test_scene_suite_reports_page_package_versions(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    """The suite version can stay put while its page package is behind; the
    manager needs both numbers to explain an "update available" badge."""
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.server.routes.hub import hub_scene_suites

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    await install_plugin("component", "soc-workspace")
    latest_pages = load_manifest("webui", "soc_ui").version
    suite = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    assert suite.state == "installed"
    assert suite.workspaceVersion == latest_pages
    assert suite.workspaceLatestVersion == latest_pages

    # Pages installed by an older build: the suite itself is current, its pages are not.
    local.save_installed_record(local.get_record("webui", "soc_ui").model_copy(update={"version": "0.0.1"}))
    clear_catalog_caches()
    stale = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    assert stale.state == "updateAvailable"
    assert stale.installedVersion == load_manifest("component", "soc-workspace").version
    assert stale.workspaceVersion == "0.0.1"
    assert stale.workspaceLatestVersion == latest_pages


@pytest.mark.parametrize("ownership", ["suite", "independent", "project"])
async def test_suite_uninstall_after_catalog_reconciles_missing_webui_preserves_ownership(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, ownership: str,
):
    from flocks.server.routes.hub import hub_scene_suites

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    if ownership != "suite":
        await install_plugin("webui", "soc_ui", scope="project" if ownership == "project" else "global")
    await install_plugin("component", "soc-workspace")
    record = local.get_record("webui", "soc_ui")
    Path(record.installPath).rename(isolated_hub_env["home"] / "webui-backup")
    access_dir = local.install_root("webui", record.scope).parent / "access" / "soc_ui"
    assert access_dir.is_dir()

    suite = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    assert suite.state == "partial"
    assert local.get_record("webui", "soc_ui") == record
    assert await uninstall_plugin("component", "soc-workspace") is True
    assert access_dir.exists() is (ownership != "suite")
    assert await uninstall_plugin("component", "soc-workspace") is False


@pytest.mark.parametrize("owner", ["component:soc-workspace", "component:other-suite", None])
async def test_scene_suite_with_only_owned_access_left_is_partial(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, owner: str | None,
):
    from flocks.server.routes.hub import hub_scene_suites

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    record = await install_plugin("webui", "soc_ui", installed_by=owner)
    Path(record.installPath).rename(isolated_hub_env["home"] / "webui-backup")
    access = local.install_root("webui").parent / "access" / "soc_ui"
    suite = next(item for item in await hub_scene_suites() if item.id == "soc-workspace")
    owned = owner == "component:soc-workspace"
    assert suite.state == ("partial" if owned else "available")
    assert suite.installedVersion is None
    assert suite.workspaceEnabled is None
    assert await uninstall_plugin("component", "soc-workspace") is owned
    assert access.exists() is not owned


@pytest.mark.parametrize(("plugin_type", "plugin_id"), [("webui", "soc_ui"), ("workflow", "stream_alert_triage")])
async def test_catalog_reconciles_missing_payload_without_retained_access(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch, plugin_type: str, plugin_id: str,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    record = await install_plugin(plugin_type, plugin_id)
    Path(record.installPath).rename(isolated_hub_env["home"] / "payload-backup")
    if plugin_type == "webui":
        access = local.install_root("webui").parent / "access" / plugin_id
        access.rename(isolated_hub_env["home"] / "access-backup")
    entry = next(item for item in list_catalog(plugin_type=plugin_type) if item.id == plugin_id)
    assert entry.state == "available"
    assert entry.installedVersion is None
    assert local.get_record(plugin_type, plugin_id) is None


async def test_webui_uninstall_cleans_access_without_an_install_record(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    record = await install_plugin("webui", "soc_ui")
    Path(record.installPath).rename(isolated_hub_env["home"] / "soc_ui_backup")
    local.remove_installed_record("webui", "soc_ui")
    access_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "contracts" / "access" / "soc_ui"

    assert await uninstall_plugin("webui", "soc_ui") is True
    assert not access_dir.exists()
    assert await uninstall_plugin("webui", "soc_ui") is False


async def test_component_uninstall_preserves_project_webui(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    project_record = await install_plugin("webui", "soc_ui", scope="project")
    await install_plugin("component", "soc-workspace")

    assert await uninstall_plugin("component", "soc-workspace") is True
    assert local.get_record("webui", "soc_ui") == project_record
    assert Path(project_record.installPath).is_dir()
    assert (
        isolated_hub_env["project_dir"] / ".flocks" / "plugins" / "contracts" / "access" / "soc_ui"
    ).is_dir()


async def test_missing_project_webui_uninstall_preserves_access_contracts(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    record = await install_plugin("webui", "soc_ui", scope="project")
    Path(record.installPath).rename(isolated_hub_env["project_dir"] / "soc_ui_backup")

    await uninstall_plugin("webui", "soc_ui")
    assert (
        isolated_hub_env["project_dir"] / ".flocks" / "plugins" / "contracts" / "access" / "soc_ui"
    ).is_dir()


async def test_suite_uninstall_required_child_failure_has_context_and_can_retry(
    isolated_hub_env, monkeypatch: pytest.MonkeyPatch,
):
    from httpx import ASGITransport, AsyncClient

    from flocks.hub import installer
    from flocks.server.auth import require_admin
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)
    await install_plugin("component", "soc-workspace")
    original_uninstall = installer._uninstall_plugin

    async def fail_required_tool(plugin_type, plugin_id):
        if plugin_type == "tool" and plugin_id == "soc_workspace_query":
            raise PermissionError("access denied")
        return await original_uninstall(plugin_type, plugin_id)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    endpoint = "/hub/plugins/component/soc-workspace"
    monkeypatch.setattr(installer, "_uninstall_plugin", fail_required_tool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.delete(endpoint, timeout=30)
        assert failed.status_code == 422
        assert "soc-workspace" in failed.json()["detail"]
        assert "tool/soc_workspace_query" in failed.json()["detail"]
        assert "access denied" in failed.json()["detail"]
        assert local.get_record("component", "soc-workspace") is not None
        assert local.get_record("tool", "soc_workspace_query") is not None

        monkeypatch.setattr(installer, "_uninstall_plugin", original_uninstall)
        retried = await client.delete(endpoint, timeout=30)
        assert retried.status_code == 200, retried.text
        assert retried.json() == {"removed": True}
        assert not local.load_installed_records()


async def test_hub_uninstalls_python_tool_without_record(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    async def noop_refresh(_plugin_type, _changed_path=None):
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
    await install_plugin("skill", "triaging-security-incident")
    skill_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "triaging-security-incident"
    assert (skill_dir / "SKILL.md").is_file()

    import shutil

    shutil.rmtree(skill_dir)
    entries = list_catalog(plugin_type="skill")
    entry = next(item for item in entries if item.id == "triaging-security-incident")
    assert entry.state == "available"
    assert local.get_record("skill", "triaging-security-incident") is None


def test_hub_routes_cover_catalog_files_install_and_uninstall(isolated_hub_env):
    from flocks.auth.context import AuthUser
    from flocks.server.auth import require_admin
    from flocks.server.routes.hub import router

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: AuthUser(
        id="hub-test-admin",
        username="hub-test-admin",
        role="admin",
        status="active",
        must_reset_password=False,
    )
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

    protected = client.post("/api/hub/plugins/skill/ndr-alert-analysis/install", json={"scope": "global"})
    assert protected.status_code == 422 and "read-only" in protected.text

    installed = client.post("/api/hub/plugins/skill/triaging-security-incident/install", json={"scope": "global"})
    assert installed.status_code == 200
    assert installed.json()["id"] == "triaging-security-incident"

    installed_catalog = client.get("/api/hub/catalog", params={"state": "installed"}).json()
    assert any(item["id"] == "triaging-security-incident" for item in installed_catalog)

    removed = client.delete("/api/hub/plugins/skill/triaging-security-incident")
    assert removed.status_code == 200
    available_catalog = client.get("/api/hub/catalog", params={"state": "available"}).json()
    assert any(item["id"] == "triaging-security-incident" for item in available_catalog)


def test_hub_paginated_facets_exclude_their_own_filter():
    from flocks.hub.models import HubCatalogEntry
    from flocks.server.routes import hub as hub_routes

    entries = [
        HubCatalogEntry(
            id="skill-installed",
            type="skill",
            name="Installed skill",
            category="security",
            tags=["triage"],
            useCases=["incident-response"],
            trust="official",
            riskLevel="low",
            state="installed",
            manifestPath="skills/installed.json",
        ),
        HubCatalogEntry(
            id="agent-installed",
            type="agent",
            name="Installed agent",
            category="security",
            tags=["triage"],
            useCases=["incident-response"],
            trust="official",
            riskLevel="low",
            state="installed",
            manifestPath="agents/installed.json",
        ),
        HubCatalogEntry(
            id="skill-available",
            type="skill",
            name="Available skill",
            category="productivity",
            tags=["automation"],
            useCases=["operations"],
            trust="community",
            riskLevel="medium",
            state="available",
            manifestPath="skills/available.json",
        ),
    ]

    facets = hub_routes._build_hub_catalog_facets_for_filters(
        entries,
        {
            "plugin_type": "skill",
            "category": None,
            "tags": None,
            "use_cases": None,
            "state": ["installed"],
            "trust": None,
            "risk": None,
            "q": None,
        },
    )

    assert facets.type == {"skill": 1, "agent": 1}
    assert facets.state == {"installed": 1, "available": 1}
    assert facets.category == {"security": 1}


def test_hub_catalog_prioritizes_updateable_and_installed_entries():
    from flocks.hub.models import HubCatalogEntry
    from flocks.server.routes import hub as hub_routes

    def entry(plugin_id: str, state: str) -> HubCatalogEntry:
        return HubCatalogEntry(
            id=plugin_id,
            type="skill",
            name=plugin_id,
            category="security",
            tags=[],
            useCases=[],
            trust="official",
            riskLevel="low",
            state=state,
            manifestPath=f"skills/{plugin_id}.json",
        )

    prioritized = hub_routes._prioritize_installed_catalog_entries([
        entry("available", "available"),
        entry("installed", "installed"),
        entry("updateable", "updateAvailable"),
        entry("incompatible", "incompatible"),
    ])

    assert [item.id for item in prioritized] == [
        "updateable",
        "installed",
        "available",
        "incompatible",
    ]


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
    from flocks.auth.context import AuthUser
    from flocks.server.auth import require_admin
    from flocks.server.routes.hub import router

    async def noop_refresh(_plugin_type, _changed_path=None):
        return None

    monkeypatch.setattr("flocks.hub.installer._refresh_runtime", noop_refresh)
    _patch_webui_bundle_build(monkeypatch)

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: AuthUser(
        id="hub-test-admin",
        username="hub-test-admin",
        role="admin",
        status="active",
        must_reset_password=False,
    )
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
