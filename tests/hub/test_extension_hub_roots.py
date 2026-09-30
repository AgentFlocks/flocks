"""Extension hub roots join the catalog and may replace bundled placeholders."""

import json
from pathlib import Path

import pytest

from flocks.extensions import register_hub_root, unregister_hub_root
from flocks.hub.catalog import list_catalog, load_index, load_manifest, manifest_path
from flocks.hub.files import plugin_root
from flocks.server.routes.hub import _suite_workspace_id

ROOT_NAME = "test-extension"


def _manifest(plugin_id: str, plugin_type: str, *, components=(), entrypoints=(), edition="pro") -> dict:
    folder = f"plugins/{plugin_type}s/{plugin_id}"
    return {
        "schemaVersion": "hub.plugin.v1",
        "id": plugin_id,
        "type": plugin_type,
        "name": plugin_id,
        "description": f"{plugin_id} from the extension",
        "version": "2.0.0",
        "author": "Tests",
        "license": "MIT",
        "category": "workflow-automation",
        "trust": "official",
        "edition": edition,
        "source": {"kind": "bundled", "path": folder},
        "compatibility": {"flocks": ">=0.8.0", "os": ["darwin", "linux", "windows"]},
        "entrypoints": list(entrypoints),
        "components": list(components),
    }


def _index_entry(manifest: dict) -> dict:
    return {
        "id": manifest["id"],
        "type": manifest["type"],
        "name": manifest["name"],
        "version": manifest["version"],
        "trust": "official",
        "edition": manifest["edition"],
        "manifestPath": f"{manifest['source']['path']}/manifest.json",
    }


def _write(root: Path, manifest: dict, extra_files: dict[str, str] | None = None) -> None:
    folder = root / manifest["source"]["path"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, content in (extra_files or {}).items():
        (folder / name).write_text(content, encoding="utf-8")


@pytest.fixture()
def extension_root(tmp_path: Path):
    root = tmp_path / "extension-hub"
    suite = _manifest(
        "code-audit-workspace",
        "component",
        components=[{"type": "webui", "id": "ext_demo_ui"}],
        entrypoints=["component.json"],
    )
    webui = _manifest("ext_demo_ui", "webui", entrypoints=["workspace.json"])
    _write(root, suite, {"component.json": json.dumps({"id": suite["id"], "components": suite["components"]})})
    _write(root, webui, {"workspace.json": json.dumps({"id": "ext_demo_ui", "placement": "sceneWorkspace"})})
    (root / "index.json").write_text(
        json.dumps({"schemaVersion": "hub.index.v1", "plugins": [_index_entry(suite), _index_entry(webui)]}),
        encoding="utf-8",
    )
    yield root
    unregister_hub_root(ROOT_NAME)


def test_bundled_placeholder_is_used_without_extension():
    assert _suite_workspace_id("code-audit-workspace") == "code_audit_ui"
    assert all(item.id != "ext_demo_ui" for item in load_index().plugins)


def test_extension_replaces_placeholder_and_adds_its_packages(extension_root: Path):
    register_hub_root(ROOT_NAME, extension_root)

    by_key = {(item.type, item.id): item for item in load_index().plugins}
    assert by_key[("component", "code-audit-workspace")].version == "2.0.0"
    assert ("webui", "ext_demo_ui") in by_key
    # The bundled placeholder page stays listed; only the suite points elsewhere.
    assert ("webui", "code_audit_ui") in by_key

    assert manifest_path("component", "code-audit-workspace").is_relative_to(extension_root)
    assert [ref.id for ref in load_manifest("component", "code-audit-workspace").components] == ["ext_demo_ui"]
    assert plugin_root("webui", "ext_demo_ui", prefer_bundled=True) == (extension_root / "plugins/webuis/ext_demo_ui").resolve()
    assert _suite_workspace_id("code-audit-workspace") == "ext_demo_ui"

    catalog = {(entry.type, entry.id): entry for entry in list_catalog()}
    assert catalog[("component", "code-audit-workspace")].version == "2.0.0"
    assert catalog[("webui", "ext_demo_ui")].edition == "pro"


def test_unregistering_restores_the_bundled_catalog(extension_root: Path):
    register_hub_root(ROOT_NAME, extension_root)
    assert unregister_hub_root(ROOT_NAME)

    assert manifest_path("component", "code-audit-workspace").name == "manifest.json"
    assert not manifest_path("component", "code-audit-workspace").is_relative_to(extension_root)
    assert _suite_workspace_id("code-audit-workspace") == "code_audit_ui"


def test_invalid_extension_index_is_ignored(tmp_path: Path):
    broken = tmp_path / "broken-hub"
    broken.mkdir()
    (broken / "index.json").write_text("{not json", encoding="utf-8")
    register_hub_root(ROOT_NAME, broken)
    try:
        assert _suite_workspace_id("code-audit-workspace") == "code_audit_ui"
    finally:
        unregister_hub_root(ROOT_NAME)


def test_indexed_manifest_cannot_escape_its_root(tmp_path: Path):
    root = tmp_path / "escaping-hub"
    root.mkdir()
    (root / "index.json").write_text(
        json.dumps({
            "schemaVersion": "hub.index.v1",
            "plugins": [{"id": "escape_ui", "type": "webui", "name": "x", "manifestPath": "../outside/manifest.json"}],
        }),
        encoding="utf-8",
    )
    register_hub_root(ROOT_NAME, root)
    try:
        with pytest.raises(ValueError):
            manifest_path("webui", "escape_ui")
    finally:
        unregister_hub_root(ROOT_NAME)
