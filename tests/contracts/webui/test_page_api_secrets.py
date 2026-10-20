"""``ctx.secrets`` gives a page handler the secrets its routes.yaml lists, and
nothing else."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.store import WebUIPagesStore


@pytest.fixture
def runtime_store(tmp_path, monkeypatch):
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    store.create_page(page_id="secret-page", title="密钥页面")
    return store


@pytest.fixture
def secret_store(monkeypatch):
    values = {"tdp_api_key": "tdp-123", "server_api_token": "root-token"}

    class _Manager:
        def get(self, key):
            return values.get(key)

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: _Manager())
    return values


def _app(store: WebUIPagesStore) -> FastAPI:
    app = FastAPI()
    runtime = WebUIPageApiRuntime(store)

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    return app


_HANDLERS = (
    "def read(ctx, request):\n"
    "    key = request.query_params.get('key')\n"
    "    try:\n"
    "        return {'value': ctx.secrets.get(key)}\n"
    "    except PermissionError as exc:\n"
    "        return {'refused': str(exc)}\n"
)


@pytest.mark.asyncio
async def test_page_reads_only_the_secrets_it_declares(runtime_store, secret_store) -> None:
    runtime_store.save_source_file(
        "secret-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /read\n    handler: handlers.read\nsecrets:\n  - tdp_api_key\n",
    )
    runtime_store.save_source_file("secret-page", "api/handlers.py", _HANDLERS)
    # Declared is not enough: an administrator approves it for this code (test_page_api_approvals).
    from flocks.contracts.webui import approvals

    await approvals.set_approval(
        "secret-page",
        code_sha256=approvals.api_code_digest(runtime_store.routes_path("secret-page").parent),
        secret_roles={"tdp_api_key": ["admin"]},
        handlers_enabled=False,
        approved_by="admin",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime_store)), base_url="http://test") as client:
        declared = await client.get("/api/contracts/webui/pages/secret-page/api/read", params={"key": "tdp_api_key"})
        undeclared = await client.get(
            "/api/contracts/webui/pages/secret-page/api/read", params={"key": "server_api_token"}
        )

    assert declared.json() == {"value": "tdp-123"}
    body = undeclared.json()
    assert "value" not in body and "server_api_token" in body["refused"]
    assert "root-token" not in undeclared.text


@pytest.mark.asyncio
async def test_page_without_a_secrets_list_reads_none(runtime_store, secret_store) -> None:
    runtime_store.save_source_file(
        "secret-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /read\n    handler: handlers.read\n",
    )
    runtime_store.save_source_file("secret-page", "api/handlers.py", _HANDLERS)

    async with AsyncClient(transport=ASGITransport(app=_app(runtime_store)), base_url="http://test") as client:
        response = await client.get("/api/contracts/webui/pages/secret-page/api/read", params={"key": "tdp_api_key"})

    assert "refused" in response.json()
    assert "tdp-123" not in response.text


@pytest.mark.asyncio
async def test_malformed_secrets_list_is_a_load_error(runtime_store, secret_store) -> None:
    runtime_store.save_source_file(
        "secret-page",
        "api/routes.yaml",
        "routes:\n  - method: GET\n    path: /read\n    handler: handlers.read\nsecrets: tdp_api_key\n",
    )
    runtime_store.save_source_file("secret-page", "api/handlers.py", _HANDLERS)

    async with AsyncClient(transport=ASGITransport(app=_app(runtime_store)), base_url="http://test") as client:
        response = await client.get("/api/contracts/webui/pages/secret-page/api/read", params={"key": "tdp_api_key"})

    assert response.status_code == 500
    assert "secrets must be a list" in response.text
