import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from flocks.server.app import app
from flocks.server.auth import require_admin
from flocks.server.routes import user_defined_pages as user_defined_pages_routes
from flocks.user_defined_pages.builder import UserDefinedPagesBuilder
from flocks.user_defined_pages.models import UserDefinedPageBuildMeta
from flocks.user_defined_pages.store import UserDefinedPagesStore


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
def user_defined_pages_env(tmp_path, monkeypatch):
    root = tmp_path / "user_defined_pages"
    monkeypatch.setenv("FLOCKS_USER_DEFINED_PAGES_ROOT", str(root))
    store = UserDefinedPagesStore()
    builder = UserDefinedPagesBuilder(store)
    user_defined_pages_routes.reset_route_dependencies(store=store, builder=builder)
    return store


@pytest.mark.asyncio
async def test_create_and_list_user_defined_pages(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    create_resp = await client.post(
        "/api/user-defined-pages",
        json={"id": "dash-1", "title": "仪表盘"},
    )
    assert create_resp.status_code == 201, create_resp.text
    data = create_resp.json()
    assert data["manifest"]["id"] == "dash-1"

    list_resp = await client.get("/api/user-defined-pages", params={"enabledOnly": True})
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["title"] == "仪表盘"
    assert items[0]["route"] == "/soc/pages/dash-1"


@pytest.mark.asyncio
async def test_save_source_triggers_build_and_event(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    await client.post("/api/user-defined-pages", json={"id": "live-page", "title": "实时页"})
    source = user_defined_pages_env.read_source_file("live-page", "src/Page.tsx")

    with patch("flocks.server.routes.user_defined_pages._builder.build") as build_mock:
        build_mock.return_value = UserDefinedPageBuildMeta(
            status="ready",
            hash="abc123",
            builtAt=1,
            error=None,
        )
        with patch("flocks.server.routes.user_defined_pages.publish_event", new_callable=AsyncMock) as publish_mock:
            save_resp = await client.put(
                "/api/user-defined-pages/live-page",
                json={"sourcePath": "src/Page.tsx", "sourceContent": source},
            )

    assert save_resp.status_code == 200, save_resp.text
    body = save_resp.json()
    assert body["build"]["status"] == "ready"
    publish_mock.assert_any_await("user_defined_pages.updated", {"id": "live-page", "hash": "abc123"})


@pytest.mark.asyncio
async def test_bundle_endpoint_available_after_create(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    await client.post("/api/user-defined-pages", json={"id": "empty-page", "title": "空页面"})
    bundle_resp = await client.get("/api/user-defined-pages/empty-page/bundle.js")
    assert bundle_resp.status_code == 200
    assert "application/javascript" in bundle_resp.headers.get("content-type", "")
    assert "content-disposition" not in bundle_resp.headers
    assert bundle_resp.text.strip()


@pytest.mark.asyncio
async def test_reject_invalid_page_id_on_create(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    resp = await client.post("/api/user-defined-pages", json={"id": "../bad", "title": "坏页面"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_required_for_create(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    from fastapi import HTTPException, Request

    def _deny_admin(_request: Request):
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")

    app.dependency_overrides[require_admin] = _deny_admin
    try:
        resp = await client.post("/api/user-defined-pages", json={"id": "denied-page", "title": "禁止"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.mark.asyncio
async def test_admin_required_for_build_and_api_reload(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    from fastapi import HTTPException, Request

    await client.post("/api/user-defined-pages", json={"id": "admin-guard-page", "title": "权限页"})
    user_defined_pages_env.save_source_file(
        "admin-guard-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /x\n    handler: handlers.x\n",
    )
    user_defined_pages_env.save_source_file(
        "admin-guard-page",
        "api/handlers.py",
        "def x(ctx, request):\n    return {'ok': True}\n",
    )

    def _deny_admin(_request: Request):
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")

    app.dependency_overrides[require_admin] = _deny_admin
    try:
        build_resp = await client.post("/api/user-defined-pages/admin-guard-page/build")
        assert build_resp.status_code == 403

        reload_resp = await client.post("/api/user-defined-pages/admin-guard-page/api/reload")
        assert reload_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.mark.asyncio
async def test_page_api_routes_reload_and_dispatch(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    await client.post("/api/user-defined-pages", json={"id": "api-page", "title": "接口页"})
    user_defined_pages_env.save_source_file(
        "api-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /stats\n    handler: handlers.get_stats\n",
    )
    user_defined_pages_env.save_source_file(
        "api-page",
        "api/handlers.py",
        "def get_stats(ctx, request):\n    return {'ok': True}\n",
    )

    list_resp = await client.get("/api/user-defined-pages/api-page/api")
    assert list_resp.status_code == 200
    assert list_resp.json()[0]["path"] == "/stats"

    reload_resp = await client.post("/api/user-defined-pages/api-page/api/reload")
    assert reload_resp.status_code == 200
    assert reload_resp.json()["routes"][0]["handler"] == "handlers.get_stats"

    dispatch_resp = await client.get("/api/user-defined-pages/api-page/api/stats")
    assert dispatch_resp.status_code == 200
    assert dispatch_resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_export_and_import_user_defined_page(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    await client.post("/api/user-defined-pages", json={"id": "backup-page", "title": "备份页"})
    await client.put(
        "/api/user-defined-pages/backup-page",
        json={"sourcePath": "src/Page.tsx", "sourceContent": "export default function Page(){return <div>backup</div>;}"},
    )

    export_resp = await client.get("/api/user-defined-pages/backup-page/export")
    assert export_resp.status_code == 200
    assert export_resp.headers.get("content-type", "").startswith("application/zip")

    import_resp = await client.post(
        "/api/user-defined-pages/import?overwrite=true",
        files={"file": ("backup-page.zip", export_resp.content, "application/zip")},
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["manifest"]["id"] == "backup-page"


@pytest.mark.asyncio
async def test_import_normalizes_manifest_identity(client: AsyncClient, user_defined_pages_env: UserDefinedPagesStore):
    archive = _make_page_archive(
        "fixed-page",
        {
            "id": "wrong-page",
            "title": "导入页",
            "route": "/user-defined-pages/wrong-page",
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
        files={"file": ("fixed-page.zip", archive, "application/zip")},
    )

    assert import_resp.status_code == 200, import_resp.text
    body = import_resp.json()
    assert body["manifest"]["id"] == "fixed-page"
    assert body["manifest"]["route"] == "/soc/pages/fixed-page"

    list_resp = await client.get("/api/user-defined-pages")
    assert list_resp.status_code == 200
    assert list_resp.json()[0]["id"] == "fixed-page"
    assert list_resp.json()[0]["route"] == "/soc/pages/fixed-page"


@pytest.mark.asyncio
async def test_import_rejects_archives_with_too_many_files(
    client: AsyncClient,
    user_defined_pages_env: UserDefinedPagesStore,
    monkeypatch,
):
    monkeypatch.setattr(user_defined_pages_routes, "MAX_IMPORT_FILES", 1)
    archive = _make_page_archive(
        "too-many",
        {
            "id": "too-many",
            "title": "过多文件",
            "route": "/user-defined-pages/too-many",
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
        files={"file": ("too-many.zip", archive, "application/zip")},
    )

    assert import_resp.status_code == 400
    assert "too many files" in import_resp.text
