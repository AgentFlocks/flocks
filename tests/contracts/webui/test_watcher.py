import json

from flocks.contracts.webui.store import WebUIPagesStore
from flocks.contracts.webui import watcher as watcher_module
from flocks.contracts.webui.watcher import WebUIPagesWatcher, _PendingAction


class _RuntimeStub:
    async def reload_page(self, _page_id: str):
        return [{"method": "GET", "path": "/stats", "handler": "handlers.stats"}]


class _BuilderStub:
    def build(self, _page_id: str):
        raise AssertionError("build should not be called for api-only change")


def test_watcher_api_change_uses_main_loop_bridge(monkeypatch):
    emitted: list[tuple[str, dict]] = []
    bridge_calls: list[str] = []

    def _bridge(coro, *, timeout_seconds=5.0):
        bridge_calls.append("called")
        coro.close()
        return [{"method": "GET", "path": "/stats", "handler": "handlers.stats"}]

    def _emit(event_type: str, properties: dict):
        emitted.append((event_type, properties))

    monkeypatch.setattr(watcher_module, "_run_on_main_loop_sync", _bridge)
    monkeypatch.setattr(watcher_module, "_publish_event_sync", _emit)

    watcher = WebUIPagesWatcher(builder=_BuilderStub(), api_runtime=_RuntimeStub())
    watcher._pending_pages["demo-page"] = _PendingAction(api_changed=True)
    watcher._run_pending_builds()

    assert bridge_calls == ["called"]
    assert emitted[0][0] == "contracts.webui.pages.api_changed"
    assert emitted[0][1]["id"] == "demo-page"


def test_watcher_classifies_nested_page_api_change(tmp_path):
    root = tmp_path / "webui_pages"
    page_dir = root / "soc_ui" / "soc_alerts"
    (page_dir / "api").mkdir(parents=True)
    (page_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": "soc-alerts",
                "title": "SOC Alerts",
                "route": "/contracts/webui/soc-alerts",
                "icon": "AlertTriangle",
                "order": 20,
                "enabled": True,
                "placement": "home.after",
                "entry": "src/index.tsx",
                "updatedAt": 0,
            }
        ),
        encoding="utf-8",
    )

    store = WebUIPagesStore(root=root, project_root=None, legacy_root=None)
    watcher = WebUIPagesWatcher(store=store, builder=_BuilderStub(), api_runtime=_RuntimeStub())

    page_id, pending = watcher._classify_event(
        page_dir / "api" / "handlers.py",
        root,
        event_type="modified",
        is_directory=False,
    )

    assert page_id == "soc-alerts"
    assert pending.api_changed


def test_watcher_classifies_workspace_manifest_change(tmp_path):
    root = tmp_path / "webui_pages"
    workspace_dir = root / "soc_ui"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "workspace.json").write_text(
        json.dumps(
            {
                "id": "soc_ui",
                "title": "SOC 工作区",
                "icon": "ShieldCheck",
                "order": 10,
                "enabled": True,
                "placement": "sceneWorkspace",
            }
        ),
        encoding="utf-8",
    )

    store = WebUIPagesStore(root=root, project_root=None, legacy_root=None)
    watcher = WebUIPagesWatcher(store=store, builder=_BuilderStub(), api_runtime=_RuntimeStub())

    page_id, pending = watcher._classify_event(
        workspace_dir / "workspace.json",
        root,
        event_type="modified",
        is_directory=False,
    )

    assert page_id == "soc_ui"
    assert pending.manifest_changed
