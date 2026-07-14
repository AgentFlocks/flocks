import asyncio
import builtins
import threading

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport

from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.store import WebUIPagesStore


@pytest.fixture
def runtime_store(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    store.create_page(page_id="runtime-page", title="运行时页面")
    return store


@pytest.fixture
def runtime_app():
    return FastAPI()


@pytest.mark.asyncio
async def test_api_runtime_dispatch_sync_and_async(runtime_store: WebUIPagesStore, runtime_app: FastAPI):
    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: GET\n"
            "    path: /stats\n"
            "    handler: handlers.get_stats\n"
            "  - method: POST\n"
            "    path: /ack\n"
            "    handler: handlers.ack\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        (
            "def get_stats(ctx, request):\n"
            "    return {'ok': True, 'pageId': ctx.page_id}\n\n"
            "async def ack(ctx, request):\n"
            "    body = await request.json()\n"
            "    return {'acked': body.get('id')}\n"
        ),
    )
    runtime = WebUIPageApiRuntime(runtime_store)

    @runtime_app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _get_dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    @runtime_app.post("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _post_dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        resp_get = await client.get("/api/contracts/webui/pages/runtime-page/api/stats")
        assert resp_get.status_code == 200
        assert resp_get.json()["pageId"] == "runtime-page"

        resp_post = await client.post("/api/contracts/webui/pages/runtime-page/api/ack", json={"id": "a-1"})
        assert resp_post.status_code == 200
        assert resp_post.json() == {"acked": "a-1"}


@pytest.mark.asyncio
async def test_api_runtime_timeout_and_reload(runtime_store: WebUIPagesStore, runtime_app: FastAPI):
    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: GET\n"
            "    path: /slow\n"
            "    handler: handlers.slow\n"
            "    timeoutMs: 5\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        (
            "import asyncio\n"
            "async def slow(ctx, request):\n"
            "    await asyncio.sleep(0.05)\n"
            "    return {'ok': True}\n"
        ),
    )
    runtime = WebUIPageApiRuntime(runtime_store)

    @runtime_app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        timeout_resp = await client.get("/api/contracts/webui/pages/runtime-page/api/slow")
        assert timeout_resp.status_code == 504

    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: GET\n"
            "    path: /slow\n"
            "    handler: handlers.fast\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        "def fast(ctx, request):\n    return {'ok': True}\n",
    )
    routes = await runtime.reload_page("runtime-page")
    assert routes[0]["handler"] == "handlers.fast"

    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        ok_resp = await client.get("/api/contracts/webui/pages/runtime-page/api/slow")
        assert ok_resp.status_code == 200
        assert ok_resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_api_runtime_rejects_oversized_request_body(runtime_store: WebUIPagesStore, runtime_app: FastAPI):
    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: POST\n"
            "    path: /echo\n"
            "    handler: handlers.echo\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        (
            "async def echo(ctx, request):\n"
            "    body = await request.body()\n"
            "    return {'size': len(body)}\n"
        ),
    )
    runtime = WebUIPageApiRuntime(runtime_store)

    @runtime_app.post("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    payload = "x" * 1_000_001
    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        resp = await client.post("/api/contracts/webui/pages/runtime-page/api/echo", content=payload)
        assert resp.status_code == 413


@pytest.mark.asyncio
async def test_api_runtime_treats_client_disconnect_as_closed_request(runtime_store: WebUIPagesStore):
    runtime = WebUIPageApiRuntime(runtime_store)

    async def receive():
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/contracts/webui/pages/runtime-page/api/echo",
            "raw_path": b"/api/contracts/webui/pages/runtime-page/api/echo",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
    )

    response = await runtime.dispatch("runtime-page", "echo", request, {"role": "admin"})

    assert response.status_code == 499


@pytest.mark.asyncio
async def test_api_runtime_blocks_non_local_imports(runtime_store: WebUIPagesStore, runtime_app: FastAPI):
    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: GET\n"
            "    path: /unsafe\n"
            "    handler: handlers.unsafe\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        (
            "from flocks.server import app\n"
            "def unsafe(ctx, request):\n"
            "    return {'ok': True}\n"
        ),
    )
    runtime = WebUIPageApiRuntime(runtime_store)

    @runtime_app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://test") as client:
        resp = await client.get("/api/contracts/webui/pages/runtime-page/api/unsafe")
        assert resp.status_code == 500


def test_api_runtime_import_guard_does_not_leak_to_other_threads(
    runtime_store: WebUIPagesStore,
    monkeypatch: pytest.MonkeyPatch,
):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(builtins, "_flocks_page_import_started", started, raising=False)
    monkeypatch.setattr(builtins, "_flocks_page_import_release", release, raising=False)
    runtime_store.save_source_file(
        "runtime-page",
        "api/routes.yaml",
        (
            "routes:\n"
            "  - method: GET\n"
            "    path: /waiting\n"
            "    handler: handlers.waiting\n"
        ),
    )
    runtime_store.save_source_file(
        "runtime-page",
        "api/handlers.py",
        (
            "import builtins\n"
            "builtins._flocks_page_import_started.set()\n"
            "builtins._flocks_page_import_release.wait(timeout=2)\n"
            "def waiting(ctx, request):\n"
            "    return {'ok': True}\n"
        ),
    )
    runtime = WebUIPageApiRuntime(runtime_store)
    reload_errors = []

    def _reload_page() -> None:
        try:
            asyncio.run(runtime.reload_page("runtime-page"))
        except Exception as exc:
            reload_errors.append(exc)

    reload_thread = threading.Thread(target=_reload_page)
    reload_thread.start()
    try:
        assert started.wait(timeout=2)
        imported = builtins.__import__(
            "flocks.config.api_versioning",
            globals(),
            locals(),
            ("discover_api_service_descriptors",),
            0,
        )
        assert imported.__name__ == "flocks.config.api_versioning"
    finally:
        release.set()
        reload_thread.join(timeout=2)

    assert not reload_thread.is_alive()
    assert reload_errors == []
