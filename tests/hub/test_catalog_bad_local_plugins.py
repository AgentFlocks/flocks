"""One broken plugin on disk must not take the hub catalog, and the pages built on it, down.

Where a deployment runs the service from the account's home (the enterprise
core does), the project plugin root and the user plugin root are one directory:
every agent and workflow someone wrote there goes through the catalog's
manifest readers. The runtime's own loaders skip a bad one; so must the catalog.
"""

import json
from pathlib import Path

import pytest

from flocks.hub import catalog, local
from flocks.server.routes.hub import _load_scene_suites


@pytest.fixture()
def home_as_project(isolated_hub_env, monkeypatch: pytest.MonkeyPatch):
    home: Path = isolated_hub_env["home"]
    monkeypatch.delenv("FLOCKS_ROOT", raising=False)
    monkeypatch.chdir(home)
    catalog.clear_catalog_caches()
    assert local.install_root("agent", "project").resolve() == local.install_root("agent", "global").resolve()
    yield {"plugins": local.install_root("agent", "project").parent, "data_dir": isolated_hub_env["data_dir"]}
    catalog.clear_catalog_caches()


def _write(path: Path, content: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


def _healthy_neighbours(plugins: Path) -> None:
    _write(plugins / "agents" / "healthy_agent" / "agent.yaml", "name: healthy_agent\ntools: [bash]\n")
    _write(plugins / "workflows" / "healthy_flow" / "workflow.json", json.dumps({"name": "healthy_flow"}))


def _assert_catalog_works(*expected: tuple[str, str]) -> dict[tuple[str, str], object]:
    catalog.clear_catalog_caches()
    entries = {(entry.type, entry.id): entry for entry in catalog.list_catalog()}
    assert ("component", "soc-workspace") in entries
    for key in expected:
        assert key in entries, key
    # What the scene page reads.
    assert "soc-workspace" in {suite.id for suite in _load_scene_suites()}
    return entries


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("name: bad_agent\ntools:\n", id="tools-empty-key"),
        pytest.param("name: bad_agent\ntags:\n", id="tags-empty-key"),
        pytest.param("name: bad_agent\ntools: 3\n", id="tools-a-number"),
        pytest.param("name: bad_agent\ntags: true\n", id="tags-a-bool"),
        pytest.param("name: [bad_agent\n", id="not-yaml"),
        pytest.param("- just\n- a list\n", id="yaml-list"),
    ],
)
def test_an_agent_with_odd_yaml_is_still_listed(home_as_project, content: str):
    plugins = home_as_project["plugins"]
    _healthy_neighbours(plugins)
    _write(plugins / "agents" / "bad_agent" / "agent.yaml", content)

    entries = _assert_catalog_works(("agent", "bad_agent"), ("agent", "healthy_agent"), ("workflow", "healthy_flow"))

    assert entries[("agent", "bad_agent")].state == "installed"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"{broken", id="not-json"),
        pytest.param(b"", id="empty-file"),
        pytest.param(b"[1, 2]", id="json-list"),
        pytest.param(b"\xef\xbb\xbf{\"name\": \"x\"}", id="utf8-bom"),
        pytest.param(b"{\"name\": \"\xd6\xd0\"}", id="gbk"),
    ],
)
def test_a_workflow_with_a_broken_file_is_still_listed(home_as_project, content: bytes):
    plugins = home_as_project["plugins"]
    _healthy_neighbours(plugins)
    _write(plugins / "workflows" / "bad_flow" / "workflow.json", content)

    entries = _assert_catalog_works(("workflow", "bad_flow"), ("agent", "healthy_agent"), ("workflow", "healthy_flow"))

    assert entries[("workflow", "bad_flow")].name == "bad_flow"


def test_a_reader_that_fails_costs_only_that_plugin(home_as_project, monkeypatch: pytest.MonkeyPatch):
    plugins = home_as_project["plugins"]
    _healthy_neighbours(plugins)
    _write(plugins / "agents" / "exploding" / "agent.yaml", "name: exploding\n")
    real = catalog._agent_manifest

    def reader(plugin_id, root):
        if plugin_id == "exploding":
            raise RuntimeError("something nobody thought of")
        return real(plugin_id, root)

    monkeypatch.setattr(catalog, "_agent_manifest", reader)

    entries = _assert_catalog_works(("agent", "healthy_agent"), ("workflow", "healthy_flow"))

    assert ("agent", "exploding") not in entries


def test_a_superseded_scan_is_not_mistaken_for_a_bad_plugin(home_as_project, monkeypatch: pytest.MonkeyPatch):
    def reader(_plugin_id, _root):
        raise catalog.tool_tree.Superseded()

    with pytest.raises(catalog.tool_tree.Superseded):
        catalog._read_local_plugin(reader, "any", Path("any"))


def test_an_install_record_file_that_is_not_an_object_reads_as_no_records(home_as_project):
    _write(home_as_project["data_dir"] / "hub" / "installed.json", "[1]")

    assert local.load_installed_records() == {}
    _assert_catalog_works()
