import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from flocks.server.app import app
from flocks.server.auth import require_admin
from flocks.server.routes import webui as webui_routes
from flocks.contracts.webui.builder import WebUIPageBuilder
from flocks.contracts.webui.models import WebUIPageBuildMeta
from flocks.contracts.webui.store import WebUIPagesStore


def _make_page_archive(page_id: str, manifest: dict, extra_files: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    files = {
        "manifest.json": json.dumps(manifest),
        "src/index.tsx": "export default function Page(){return <div>ok</div>;}",
    }
    if extra_files:
        files.update(extra_files)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for relative_path, content in files.items():
            zf.writestr(f"{page_id}/{relative_path}", content)
    return buffer.getvalue()


@pytest.fixture
def webui_pages_env(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    builder = WebUIPageBuilder(store)
    webui_routes.reset_route_dependencies(store=store, builder=builder)
    return store


@pytest.mark.asyncio
async def test_create_and_list_webui_pages(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    create_resp = await client.post(
        "/api/contracts/webui/pages",
        json={"id": "dash-1", "title": "仪表盘"},
    )
    assert create_resp.status_code == 201, create_resp.text
    data = create_resp.json()
    assert data["manifest"]["id"] == "dash-1"

    list_resp = await client.get("/api/contracts/webui/pages", params={"enabledOnly": True})
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["title"] == "仪表盘"
    assert items[0]["route"] == "/contracts/webui/dash-1"


@pytest.mark.asyncio
async def test_legacy_user_defined_pages_api_alias_uses_contract_routes(
    client: AsyncClient,
    webui_pages_env: WebUIPagesStore,
):
    create_resp = await client.post(
        "/api/user-defined-pages",
        json={"id": "legacy-dash", "title": "旧页面"},
    )
    assert create_resp.status_code == 201, create_resp.text
    assert create_resp.json()["manifest"]["route"] == "/contracts/webui/legacy-dash"

    list_resp = await client.get("/api/user-defined-pages", params={"enabledOnly": True})
    assert list_resp.status_code == 200
    assert list_resp.json()[0]["route"] == "/contracts/webui/legacy-dash"


@pytest.mark.asyncio
async def test_legacy_user_defined_pages_runtime_aliases(
    client: AsyncClient,
    webui_pages_env: WebUIPagesStore,
):
    create_resp = await client.post(
        "/api/user-defined-pages",
        json={"id": "legacy-runtime", "title": "旧运行时"},
    )
    assert create_resp.status_code == 201, create_resp.text

    bundle_path = webui_pages_env.bundle_path("legacy-runtime")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text("export default function Page(){return null;}", encoding="utf-8")
    webui_pages_env.write_build_meta(
        "legacy-runtime",
        WebUIPageBuildMeta(status="ready", hash="legacy-hash", builtAt=1),
    )
    bundle_resp = await client.get("/api/user-defined-pages/legacy-runtime/bundle.js")
    assert bundle_resp.status_code == 200
    assert "application/javascript" in bundle_resp.headers.get("content-type", "")

    asset_path = webui_pages_env.asset_path("legacy-runtime", "logo.txt")
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text("asset-ok", encoding="utf-8")
    asset_resp = await client.get("/api/user-defined-pages/legacy-runtime/assets/logo.txt")
    assert asset_resp.status_code == 200
    assert asset_resp.text == "asset-ok"

    webui_pages_env.save_source_file(
        "legacy-runtime",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /stats\n    handler: handlers.get_stats\n",
    )
    webui_pages_env.save_source_file(
        "legacy-runtime",
        "api/handlers.py",
        "def get_stats(ctx, request):\n    return {'ok': True}\n",
    )
    list_api_resp = await client.get("/api/user-defined-pages/legacy-runtime/api")
    assert list_api_resp.status_code == 200
    assert list_api_resp.json()[0]["path"] == "/stats"
    reload_resp = await client.post("/api/user-defined-pages/legacy-runtime/api/reload")
    assert reload_resp.status_code == 200
    dispatch_resp = await client.get("/api/user-defined-pages/legacy-runtime/api/stats")
    assert dispatch_resp.status_code == 200
    assert dispatch_resp.json()["ok"] is True

    export_resp = await client.get("/api/user-defined-pages/legacy-runtime/export")
    assert export_resp.status_code == 200
    assert export_resp.headers.get("content-type", "").startswith("application/zip")

    archive = _make_page_archive(
        "legacy-imported",
        {
            "id": "legacy-imported",
            "title": "旧导入",
            "route": "/user-defined-pages/legacy-imported",
            "icon": "LayoutDashboard",
            "order": 10,
            "enabled": True,
            "placement": "home.after",
            "entry": "src/index.tsx",
            "updatedAt": 1,
        },
    )
    import_resp = await client.post(
        "/api/user-defined-pages/import",
        files={"file": ("legacy-imported.zip", archive, "application/zip")},
    )
    assert import_resp.status_code == 200, import_resp.text
    assert import_resp.json()["manifest"]["route"] == "/contracts/webui/legacy-imported"


@pytest.mark.asyncio
async def test_save_source_triggers_build_and_event(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    await client.post("/api/contracts/webui/pages", json={"id": "live-page", "title": "实时页"})
    source = webui_pages_env.read_source_file("live-page", "src/Page.tsx")

    with patch("flocks.server.routes.webui._builder.build") as build_mock:
        build_mock.return_value = WebUIPageBuildMeta(
            status="ready",
            hash="abc123",
            builtAt=1,
            error=None,
        )
        with patch("flocks.server.routes.webui.publish_event", new_callable=AsyncMock) as publish_mock:
            save_resp = await client.put(
                "/api/contracts/webui/pages/live-page",
                json={"sourcePath": "src/Page.tsx", "sourceContent": source},
            )

    assert save_resp.status_code == 200, save_resp.text
    body = save_resp.json()
    assert body["build"]["status"] == "ready"
    publish_mock.assert_any_await("contracts.webui.pages.updated", {"id": "live-page", "hash": "abc123"})


@pytest.mark.asyncio
async def test_bundle_endpoint_available_after_create(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    await client.post("/api/contracts/webui/pages", json={"id": "empty-page", "title": "空页面"})
    bundle_path = webui_pages_env.bundle_path("empty-page")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text("export default function Page(){return null;}", encoding="utf-8")
    webui_pages_env.write_build_meta(
        "empty-page",
        WebUIPageBuildMeta(status="ready", hash="test-hash", builtAt=1),
    )
    bundle_resp = await client.get("/api/contracts/webui/pages/empty-page/bundle.js")
    assert bundle_resp.status_code == 200
    assert "application/javascript" in bundle_resp.headers.get("content-type", "")
    assert "content-disposition" not in bundle_resp.headers
    assert bundle_resp.text.strip()


@pytest.mark.asyncio
async def test_reject_invalid_page_id_on_create(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    resp = await client.post("/api/contracts/webui/pages", json={"id": "../bad", "title": "坏页面"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_required_for_create(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    from fastapi import HTTPException, Request

    def _deny_admin(_request: Request):
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")

    app.dependency_overrides[require_admin] = _deny_admin
    try:
        resp = await client.post("/api/contracts/webui/pages", json={"id": "denied-page", "title": "禁止"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.mark.asyncio
async def test_admin_required_for_build_and_api_reload(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    from fastapi import HTTPException, Request

    await client.post("/api/contracts/webui/pages", json={"id": "admin-guard-page", "title": "权限页"})
    webui_pages_env.save_source_file(
        "admin-guard-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /x\n    handler: handlers.x\n",
    )
    webui_pages_env.save_source_file(
        "admin-guard-page",
        "api/handlers.py",
        "def x(ctx, request):\n    return {'ok': True}\n",
    )

    def _deny_admin(_request: Request):
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")

    app.dependency_overrides[require_admin] = _deny_admin
    try:
        build_resp = await client.post("/api/contracts/webui/pages/admin-guard-page/build")
        assert build_resp.status_code == 403

        reload_resp = await client.post("/api/contracts/webui/pages/admin-guard-page/api/reload")
        assert reload_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.mark.asyncio
async def test_page_api_routes_reload_and_dispatch(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    await client.post("/api/contracts/webui/pages", json={"id": "api-page", "title": "接口页"})
    webui_pages_env.save_source_file(
        "api-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /stats\n    handler: handlers.get_stats\n",
    )
    webui_pages_env.save_source_file(
        "api-page",
        "api/handlers.py",
        "def get_stats(ctx, request):\n    return {'ok': True}\n",
    )

    list_resp = await client.get("/api/contracts/webui/pages/api-page/api")
    assert list_resp.status_code == 200
    assert list_resp.json()[0]["path"] == "/stats"

    reload_resp = await client.post("/api/contracts/webui/pages/api-page/api/reload")
    assert reload_resp.status_code == 200
    assert reload_resp.json()["routes"][0]["handler"] == "handlers.get_stats"

    dispatch_resp = await client.get("/api/contracts/webui/pages/api-page/api/stats")
    assert dispatch_resp.status_code == 200
    assert dispatch_resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_export_and_import_webui_page(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    await client.post("/api/contracts/webui/pages", json={"id": "backup-page", "title": "备份页"})
    await client.put(
        "/api/contracts/webui/pages/backup-page",
        json={"sourcePath": "src/Page.tsx", "sourceContent": "export default function Page(){return <div>backup</div>;}"},
    )

    export_resp = await client.get("/api/contracts/webui/pages/backup-page/export")
    assert export_resp.status_code == 200
    assert export_resp.headers.get("content-type", "").startswith("application/zip")

    import_resp = await client.post(
        "/api/contracts/webui/pages/import?overwrite=true",
        files={"file": ("backup-page.zip", export_resp.content, "application/zip")},
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["manifest"]["id"] == "backup-page"


@pytest.mark.asyncio
async def test_import_normalizes_manifest_identity(client: AsyncClient, webui_pages_env: WebUIPagesStore):
    archive = _make_page_archive(
        "fixed-page",
        {
            "id": "wrong-page",
            "title": "导入页",
            "route": "/contracts/webui/wrong-page",
            "icon": "LayoutDashboard",
            "order": 10,
            "enabled": True,
            "placement": "home.after",
            "entry": "src/index.tsx",
            "updatedAt": 1,
        },
    )

    import_resp = await client.post(
        "/api/contracts/webui/pages/import",
        files={"file": ("fixed-page.zip", archive, "application/zip")},
    )

    assert import_resp.status_code == 200, import_resp.text
    body = import_resp.json()
    assert body["manifest"]["id"] == "fixed-page"
    assert body["manifest"]["route"] == "/contracts/webui/fixed-page"

    list_resp = await client.get("/api/contracts/webui/pages")
    assert list_resp.status_code == 200
    assert list_resp.json()[0]["id"] == "fixed-page"
    assert list_resp.json()[0]["route"] == "/contracts/webui/fixed-page"


@pytest.mark.asyncio
async def test_import_rejects_archives_with_too_many_files(
    client: AsyncClient,
    webui_pages_env: WebUIPagesStore,
    monkeypatch,
):
    monkeypatch.setattr(webui_routes, "MAX_IMPORT_FILES", 1)
    archive = _make_page_archive(
        "too-many",
        {
            "id": "too-many",
            "title": "过多文件",
            "route": "/contracts/webui/too-many",
            "icon": "LayoutDashboard",
            "order": 10,
            "enabled": True,
            "placement": "home.after",
            "entry": "src/index.tsx",
            "updatedAt": 1,
        },
    )

    import_resp = await client.post(
        "/api/contracts/webui/pages/import",
        files={"file": ("too-many.zip", archive, "application/zip")},
    )

    assert import_resp.status_code == 400
    assert "too many files" in import_resp.text
