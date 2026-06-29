import json

import pytest

from flocks.contracts.webui.store import WebUIPagesStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    return WebUIPagesStore()


def _write_page(root, page_id: str, title: str, order: int = 100) -> None:
    page_dir = root / page_id
    (page_dir / "dist").mkdir(parents=True, exist_ok=True)
    (page_dir / "src").mkdir(parents=True, exist_ok=True)
    (page_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": page_id,
                "title": title,
                "route": f"/contracts/webui/{page_id}",
                "icon": "LayoutDashboard",
                "order": order,
                "enabled": True,
                "placement": "home.after",
                "entry": "src/index.tsx",
                "updatedAt": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (page_dir / "src" / "index.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")


def _read_manifest(root, page_id: str):
    return json.loads((root / page_id / "manifest.json").read_text(encoding="utf-8"))


def test_create_page_scaffold(store: WebUIPagesStore):
    detail = store.create_page(page_id="my-dashboard", title="我的大屏")
    assert detail.manifest.id == "my-dashboard"
    assert detail.manifest.route == "/contracts/webui/my-dashboard"
    assert (store.page_dir("my-dashboard") / "src" / "Page.tsx").is_file()
    assert (store.page_dir("my-dashboard") / "manifest.json").is_file()


def test_list_pages_enabled_only(store: WebUIPagesStore):
    store.create_page(page_id="enabled-page", title="启用页")
    disabled = store.create_page(page_id="disabled-page", title="禁用页")
    store.save_manifest("disabled-page", {**disabled.manifest.model_dump(), "enabled": False})

    all_pages = store.list_pages(enabled_only=False)
    enabled_pages = store.list_pages(enabled_only=True)

    assert {page.id for page in all_pages} == {"enabled-page", "disabled-page"}
    assert [page.id for page in enabled_pages] == ["enabled-page"]


def test_list_pages_scans_user_and_project_roots_with_user_priority(tmp_path):
    user_root = tmp_path / "user" / "contracts" / "webui"
    project_root = tmp_path / "project" / ".flocks" / "plugins" / "contracts" / "webui"
    _write_page(project_root, "shared-page", "Project shared", order=20)
    _write_page(project_root, "project-page", "Project page", order=30)
    _write_page(user_root, "shared-page", "User shared", order=10)
    _write_page(user_root, "user-page", "User page", order=40)

    store = WebUIPagesStore(root=user_root, project_root=project_root)

    pages = store.list_pages()
    assert [page.id for page in pages] == ["shared-page", "project-page", "user-page"]
    assert pages[0].title == "User shared"
    assert store.get_page("shared-page").manifest.title == "User shared"
    assert store.get_page("project-page").manifest.title == "Project page"
    assert store.page_dir("shared-page").is_relative_to(user_root)
    assert store.page_dir("project-page").is_relative_to(project_root)


def test_save_project_root_page_materializes_user_copy(tmp_path):
    user_root = tmp_path / "user" / "contracts" / "webui"
    project_root = tmp_path / "project" / ".flocks" / "plugins" / "contracts" / "webui"
    _write_page(project_root, "project-page", "Project page")

    store = WebUIPagesStore(root=user_root, project_root=project_root)
    store.save_manifest("project-page", {"title": "User override"})
    store.write_build_meta("project-page", store.read_build_meta("project-page").model_copy(update={"status": "ready", "hash": "abc"}))

    assert _read_manifest(project_root, "project-page")["title"] == "Project page"
    assert _read_manifest(user_root, "project-page")["title"] == "User override"
    assert (user_root / "project-page" / "dist" / "meta.json").is_file()
    assert not (project_root / "project-page" / "dist" / "meta.json").is_file()
    assert store.page_dir("project-page").is_relative_to(user_root)


def test_legacy_user_defined_pages_are_migrated_to_contract_root(tmp_path):
    user_root = tmp_path / "user" / "contracts" / "webui"
    legacy_root = tmp_path / "user" / "user_defined_pages"
    _write_page(legacy_root, "legacy-page", "Legacy page")
    legacy_manifest = _read_manifest(legacy_root, "legacy-page")
    legacy_manifest["route"] = "/user-defined-pages/legacy-page"
    (legacy_root / "legacy-page" / "manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")
    (legacy_root / "legacy-page" / "src" / "index.tsx").write_text(
        "import { Card } from '@flocks/user-defined-page-sdk';\n"
        "const sdk = globalThis.__FLOCKS_USER_DEFINED_PAGE_SDK__;\n",
        encoding="utf-8",
    )

    store = WebUIPagesStore(root=user_root, project_root=None, legacy_root=legacy_root)
    pages = store.list_pages()

    assert [page.id for page in pages] == ["legacy-page"]
    assert pages[0].route == "/contracts/webui/legacy-page"
    migrated_source = (user_root / "legacy-page" / "src" / "index.tsx").read_text(encoding="utf-8")
    assert "@flocks/webui-contract-sdk" in migrated_source
    assert "__FLOCKS_WEBUI_CONTRACT_SDK__" in migrated_source
    assert _read_manifest(user_root, "legacy-page")["route"] == "/contracts/webui/legacy-page"
    assert json.loads((user_root / "legacy-page" / "dist" / "meta.json").read_text(encoding="utf-8"))["status"] == "idle"


def test_reject_path_traversal_on_write(store: WebUIPagesStore):
    store.create_page(page_id="safe-page", title="安全页")
    with pytest.raises(ValueError, match="writes are not allowed"):
        store.save_source_file("safe-page", "../escape.tsx", "bad")


def test_allow_page_api_source_files(store: WebUIPagesStore):
    store.create_page(page_id="api-page", title="API 页")
    store.save_source_file("api-page", "api/routes.yaml", "routes: []\n")
    store.save_source_file("api-page", "api/handlers.py", "def ping(ctx, request):\n    return {'ok': True}\n")
    assert store.read_source_file("api-page", "api/routes.yaml").startswith("routes:")
    detail = store.get_page("api-page")
    assert "api/routes.yaml" in detail.sourceFiles
    assert "api/handlers.py" in detail.sourceFiles


def test_reject_unsupported_api_extension(store: WebUIPagesStore):
    store.create_page(page_id="api-ext-page", title="API 后缀页")
    with pytest.raises(ValueError, match="unsupported source file type"):
        store.save_source_file("api-ext-page", "api/secret.txt", "nope")


def test_reject_invalid_page_id(store: WebUIPagesStore):
    with pytest.raises(ValueError, match="invalid page id"):
        store.validate_page_id("../bad")


def test_asset_path_stays_inside_assets_dir(store: WebUIPagesStore):
    store.create_page(page_id="asset-page", title="资源页")
    with pytest.raises(ValueError, match="path traversal is not allowed"):
        store.asset_path("asset-page", "../manifest.json")


def test_manifest_roundtrip(store: WebUIPagesStore):
    store.create_page(page_id="roundtrip", title="原始标题")
    manifest = store.save_manifest(
        "roundtrip",
        {
            "title": "新标题",
            "order": 10,
            "route": "/custom/route",
        },
    )
    assert manifest.title == "新标题"
    assert manifest.order == 10
    assert manifest.route == "/contracts/webui/roundtrip"
    raw = json.loads((store.page_dir("roundtrip") / "manifest.json").read_text(encoding="utf-8"))
    assert raw["route"] == "/contracts/webui/roundtrip"
