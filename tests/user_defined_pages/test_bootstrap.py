import pytest

from flocks.user_defined_pages.bootstrap import reconcile_user_defined_pages
from flocks.user_defined_pages.store import UserDefinedPagesStore


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
    root = tmp_path / "user_defined_pages"
    monkeypatch.setenv("FLOCKS_USER_DEFINED_PAGES_ROOT", str(root))
    store = UserDefinedPagesStore()
    store.create_page(page_id="boot-page", title="启动页")
    store.save_source_file("boot-page", "api/routes.yaml", "routes: []\n")
    store.save_source_file("boot-page", "api/handlers.py", "def noop(ctx, request):\n    return {}\n")

    builder = _BuilderStub()
    runtime = _RuntimeStub()
    await reconcile_user_defined_pages(store=store, builder=builder, runtime=runtime)

    assert builder.calls == ["boot-page"]
    assert runtime.calls == ["boot-page"]
