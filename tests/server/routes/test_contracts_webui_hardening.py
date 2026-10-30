"""Regression guards for WebUI page routes found in the 2026-09-14 full test run.

- A malformed page id in the URL is a 400 (invalid page id), never a 500, on every
  page-scoped route (get / put / build / api list / api reload / api dispatch / export).
- An import archive whose root directory is not a valid page id is a 400, not a 500.
- Export skips ``__pycache__``/``*.pyc`` so a page whose API was loaded can be
  imported back (the import allow-list rejects bytecode).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.server.routes import webui as webui_routes
from flocks.contracts.webui.store import WebUIPagesStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
def pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIPagesStore:
    root = tmp_path / "webui-pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore(root=root)
    webui_routes.reset_route_dependencies(store=store)
    yield store
    webui_routes.reset_route_dependencies()


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/contracts/webui/pages/Bad%20Id"),
        ("PUT", "/api/contracts/webui/pages/Bad%20Id"),
        ("POST", "/api/contracts/webui/pages/Bad%20Id/build"),
        ("GET", "/api/contracts/webui/pages/Bad%20Id/api"),
        ("POST", "/api/contracts/webui/pages/Bad%20Id/api/reload"),
        ("GET", "/api/contracts/webui/pages/Bad%20Id/api/hello"),
        ("GET", "/api/contracts/webui/pages/Bad%20Id/export"),
        ("GET", "/api/user-defined-pages/Bad%20Id"),
        ("GET", "/api/user-defined-pages/Bad%20Id/export"),
    ],
)
async def test_invalid_page_id_is_400(client: AsyncClient, pages: WebUIPagesStore, method: str, path: str):
    kwargs = {"json": {"manifest": {}}} if method == "PUT" else {}
    resp = await client.request(method, path, **kwargs)
    assert resp.status_code == 400, (path, resp.status_code, resp.text)
    assert "invalid page id" in resp.json()["message"]


async def test_import_invalid_root_id_is_400(client: AsyncClient, pages: WebUIPagesStore):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("My_Page/manifest.json", json.dumps({"id": "My_Page", "title": "t"}))
        zf.writestr("My_Page/src/index.tsx", "export default () => null")
    resp = await client.post(
        "/api/contracts/webui/pages/import",
        files={"file": ("x.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 400, resp.text
    assert "invalid page id" in resp.json()["message"]


async def test_export_skips_pycache_and_roundtrips(client: AsyncClient, pages: WebUIPagesStore, monkeypatch: pytest.MonkeyPatch):
    pages.create_page(page_id="rt-page", title="RT")
    api_dir = pages.page_dir("rt-page") / "api"
    (api_dir / "handlers.py").write_text("def hello(ctx, request):\n    return {'ok': True}\n", encoding="utf-8")
    (api_dir / "routes.yaml").write_text("routes:\n  - method: GET\n    path: /hello\n    handler: handlers.hello\n", encoding="utf-8")
    cache_dir = api_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "handlers.cpython-312.pyc").write_bytes(b"\x00\x01")

    exported = await client.get("/api/contracts/webui/pages/rt-page/export")
    assert exported.status_code == 200, exported.text
    names = zipfile.ZipFile(io.BytesIO(exported.content)).namelist()
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names), names
    assert "rt-page/api/handlers.py" in names and "rt-page/api/routes.yaml" in names

    # Builder is not needed for the round-trip; keep it inert.
    monkeypatch.setattr(webui_routes._builder, "build", lambda page_id: webui_routes.WebUIPageBuildMeta(status="idle"))
    imported = await client.post(
        "/api/contracts/webui/pages/import",
        params={"overwrite": "true"},
        files={"file": ("rt-page.zip", exported.content, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["manifest"]["id"] == "rt-page"
