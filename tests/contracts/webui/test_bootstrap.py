import pytest

from flocks.contracts.webui.bootstrap import reconcile_webui_pages
from flocks.contracts.webui.store import WebUIPagesStore


class _BuilderStub:
    def __init__(self):
        self.calls: list[str] = []

    def build(self, page_id: str):
        self.calls.append(page_id)
        return type("Meta", (), {"status": "ready", "error": None})


class _RuntimeStub:
    def __init__(self):
        self.calls: list[str] = []

    async def reload_page(self, page_id: str):
        self.calls.append(page_id)
        return []


@pytest.mark.asyncio
async def test_reconcile_rebuilds_missing_bundle_and_preloads_api(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    store.create_page(page_id="boot-page", title="启动页")
    store.save_source_file("boot-page", "api/routes.yaml", "routes: []\n")
    store.save_source_file("boot-page", "api/handlers.py", "def noop(ctx, request):\n    return {}\n")

    builder = _BuilderStub()
    runtime = _RuntimeStub()
    await reconcile_webui_pages(store=store, builder=builder, runtime=runtime)

    assert builder.calls == ["boot-page"]
    assert runtime.calls == ["boot-page"]
