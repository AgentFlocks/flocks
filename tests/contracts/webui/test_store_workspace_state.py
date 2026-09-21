"""Turning a scene workspace off hides it and its pages without touching plugin files."""

import json

import pytest

from flocks.contracts.webui.store import WebUIPagesStore


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def workspace_store(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    _write(
        root / "scene_ui" / "workspace.json",
        {
            "id": "scene_ui",
            "version": "1.0.0",
            "title": "场景工作区",
            "icon": "ShieldCheck",
            "order": 10,
            "enabled": True,
            "placement": "sceneWorkspace",
            "defaultPageId": "scene-overview",
        },
    )
    _write(
        root / "scene_ui" / "scene_overview" / "manifest.json",
        {
            "id": "scene-overview",
            "title": "总览",
            "route": "/contracts/webui/scene-overview",
            "icon": "Shield",
            "order": 10,
            "enabled": True,
            "placement": "home.after",
            "entry": "src/index.tsx",
            "updatedAt": 0,
        },
    )
    return WebUIPagesStore()


def test_workspace_starts_enabled(workspace_store: WebUIPagesStore):
    workspaces = workspace_store.list_workspaces()
    assert [item.id for item in workspaces] == ["scene_ui"]
    assert workspaces[0].enabled is True
    assert [page.id for page in workspaces[0].pages] == ["scene-overview"]


def test_disable_hides_workspace_and_its_pages(workspace_store: WebUIPagesStore):
    updated = workspace_store.set_workspace_enabled("scene_ui", False)

    assert updated.enabled is False
    assert [page.enabled for page in updated.pages] == [False]
    assert workspace_store.list_workspaces(enabled_only=True) == []
    assert workspace_store.list_pages(enabled_only=True) == []
    # The page is still listed when the caller wants everything, just disabled.
    assert [(page.id, page.enabled) for page in workspace_store.list_pages()] == [("scene-overview", False)]


def test_enable_restores_workspace(workspace_store: WebUIPagesStore):
    workspace_store.set_workspace_enabled("scene_ui", False)
    restored = workspace_store.set_workspace_enabled("scene_ui", True)

    assert restored.enabled is True
    assert [item.id for item in workspace_store.list_workspaces(enabled_only=True)] == ["scene_ui"]
    assert [page.id for page in workspace_store.list_pages(enabled_only=True)] == ["scene-overview"]


def test_state_is_kept_out_of_the_plugin_files(workspace_store: WebUIPagesStore, tmp_path):
    workspace_store.set_workspace_enabled("scene_ui", False)

    manifest = json.loads((tmp_path / "webui_pages" / "scene_ui" / "workspace.json").read_text(encoding="utf-8"))
    assert manifest["enabled"] is True, "the suite's own manifest must stay untouched"
    state = json.loads((tmp_path / "webui_pages" / ".workspace-state.json").read_text(encoding="utf-8"))
    assert state == {"enabled": {"scene_ui": False}}


def test_unknown_workspace_id_is_rejected(workspace_store: WebUIPagesStore):
    with pytest.raises(FileNotFoundError):
        workspace_store.set_workspace_enabled("missing_ui", False)
