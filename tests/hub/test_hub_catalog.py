import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.hub import local
from flocks.hub.catalog import list_catalog, load_manifest, load_taxonomy
from flocks.hub.files import file_tree, read_file_content
from flocks.hub.installer import install_plugin, uninstall_plugin


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
    yield {"home": home, "config_dir": config_dir, "data_dir": data_dir}
    Skill.clear_cache()


def test_bundled_hub_catalog_loads():
    entries = list_catalog()
    assert entries
    assert {entry.type for entry in entries} >= {"skill", "agent", "tool", "workflow"}


def test_project_builtin_plugins_are_listed_as_installed():
    entries = list_catalog()
    by_key = {(entry.type, entry.id): entry for entry in entries}

    assert by_key[("skill", "tdp-use")].state == "installed"
    assert by_key[("skill", "tdp-use")].native is True
    assert by_key[("agent", "ndr-analyst")].state == "installed"
    assert by_key[("workflow", "tdp_alert_triage")].state == "installed"
    assert by_key[("tool", "tdp_api")].state == "installed"

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


async def test_catalog_clears_stale_tool_record_after_tool_files_deleted(isolated_hub_env):
    await install_plugin("tool", "hub_echo")
    tool_dir = isolated_hub_env["home"] / ".flocks" / "plugins" / "tools" / "hub_echo"
    assert (tool_dir / "hub_echo.yaml").is_file()

    (tool_dir / "hub_echo.yaml").unlink()
    (tool_dir / "hub_echo.handler.py").unlink()
    entries = list_catalog(plugin_type="tool")
    entry = next(item for item in entries if item.id == "hub_echo")
    assert entry.state == "available"
    assert local.get_record("tool", "hub_echo") is None


def test_hub_routes_cover_catalog_files_install_and_uninstall(isolated_hub_env):
    from flocks.server.routes.hub import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)

    catalog = client.get("/api/hub/catalog").json()
    assert any(item["id"] == "ndr-alert-analysis" for item in catalog)

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
