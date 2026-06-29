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
