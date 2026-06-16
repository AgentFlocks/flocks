import json

import pytest

from flocks.user_defined_pages.store import UserDefinedPagesStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "user_defined_pages"
    monkeypatch.setenv("FLOCKS_USER_DEFINED_PAGES_ROOT", str(root))
    return UserDefinedPagesStore()


def test_create_page_scaffold(store: UserDefinedPagesStore):
    detail = store.create_page(page_id="my-dashboard", title="我的大屏")
    assert detail.manifest.id == "my-dashboard"
    assert detail.manifest.route == "/soc/pages/my-dashboard"
    assert (store.page_dir("my-dashboard") / "src" / "Page.tsx").is_file()
    assert (store.page_dir("my-dashboard") / "manifest.json").is_file()


def test_list_pages_enabled_only(store: UserDefinedPagesStore):
    store.create_page(page_id="enabled-page", title="启用页")
    disabled = store.create_page(page_id="disabled-page", title="禁用页")
    store.save_manifest("disabled-page", {**disabled.manifest.model_dump(), "enabled": False})

    all_pages = store.list_pages(enabled_only=False)
    enabled_pages = store.list_pages(enabled_only=True)

    assert {page.id for page in all_pages} == {"enabled-page", "disabled-page"}
    assert [page.id for page in enabled_pages] == ["enabled-page"]


def test_reject_path_traversal_on_write(store: UserDefinedPagesStore):
    store.create_page(page_id="safe-page", title="安全页")
    with pytest.raises(ValueError, match="writes are not allowed"):
        store.save_source_file("safe-page", "../escape.tsx", "bad")


def test_allow_page_api_source_files(store: UserDefinedPagesStore):
    store.create_page(page_id="api-page", title="API 页")
    store.save_source_file("api-page", "api/routes.yaml", "routes: []\n")
    store.save_source_file("api-page", "api/handlers.py", "def ping(ctx, request):\n    return {'ok': True}\n")
    assert store.read_source_file("api-page", "api/routes.yaml").startswith("routes:")
    detail = store.get_page("api-page")
    assert "api/routes.yaml" in detail.sourceFiles
    assert "api/handlers.py" in detail.sourceFiles


def test_reject_unsupported_api_extension(store: UserDefinedPagesStore):
    store.create_page(page_id="api-ext-page", title="API 后缀页")
    with pytest.raises(ValueError, match="unsupported source file type"):
        store.save_source_file("api-ext-page", "api/secret.txt", "nope")


def test_reject_invalid_page_id(store: UserDefinedPagesStore):
    with pytest.raises(ValueError, match="invalid page id"):
        store.validate_page_id("../bad")


def test_asset_path_stays_inside_assets_dir(store: UserDefinedPagesStore):
    store.create_page(page_id="asset-page", title="资源页")
    with pytest.raises(ValueError, match="path traversal is not allowed"):
        store.asset_path("asset-page", "../manifest.json")


def test_manifest_roundtrip(store: UserDefinedPagesStore):
    store.create_page(page_id="roundtrip", title="原始标题")
    manifest = store.save_manifest("roundtrip", {"title": "新标题", "order": 10})
    assert manifest.title == "新标题"
    assert manifest.order == 10
    raw = json.loads((store.page_dir("roundtrip") / "manifest.json").read_text(encoding="utf-8"))
    assert raw["route"] == "/soc/pages/roundtrip"
