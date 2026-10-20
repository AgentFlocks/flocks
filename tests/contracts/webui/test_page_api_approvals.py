"""An administrator approves a page's server code and the secrets it may read (V1.2 3.3 / 3.6, G07).

A secret reaches a handler only where three things agree: the page declares it,
an administrator approved it for exactly this code, and for the caller's role.
On a managed install the page's code does not run at all -- not even its
module-level code -- until an administrator approved it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from flocks.contracts.webui import approvals
from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.store import WebUIPagesStore

_ROUTES = (
    "routes:\n  - method: GET\n    path: /read\n    handler: handlers.read\n"
    "secrets:\n  - tdp_api_key\n  - server_api_token\n"
)


def _handlers(marker: Path) -> str:
    # Module-level code: it runs on import, before any handler is called.
    return (
        f"open({str(marker)!r}, 'a').write('imported\\n')\n"
        "def read(ctx, request):\n"
        "    key = request.query_params.get('key')\n"
        "    try:\n"
        "        return {'value': ctx.secrets.get(key)}\n"
        "    except PermissionError as exc:\n"
        "        return {'refused': str(exc)}\n"
    )


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(tmp_path / "webui_pages"))
    store = WebUIPagesStore()
    store.create_page(page_id="secret-page", title="密钥页面")
    marker = tmp_path / "imported.txt"
    store.save_source_file("secret-page", "api/routes.yaml", _ROUTES)
    store.save_source_file("secret-page", "api/handlers.py", _handlers(marker))
    values = {"tdp_api_key": "tdp-123", "server_api_token": "root-token"}

    class _Manager:
        def get(self, key):
            return values.get(key)

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: _Manager())
    return store, marker


def _client(store: WebUIPagesStore, role: str) -> AsyncClient:
    app = FastAPI()
    runtime = WebUIPageApiRuntime(store)

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": role})

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _read(store, role: str, key: str) -> dict:
    async with _client(store, role) as client:
        response = await client.get("/api/contracts/webui/pages/secret-page/api/read", params={"key": key})
    return {"status": response.status_code, **(response.json() if response.status_code == 200 else {"detail": response.json()})}


def _digest(store: WebUIPagesStore) -> str:
    return approvals.api_code_digest(store.routes_path("secret-page").parent)


@pytest.mark.asyncio
async def test_a_declared_secret_is_read_only_once_approved_and_only_for_the_approved_roles(page):
    store, _marker = page
    for role in ("admin", "member"):
        refused = await _read(store, role, "tdp_api_key")
        assert "refused" in refused and "tdp-123" not in str(refused), "declared is not approved"

    await approvals.set_approval(
        "secret-page", code_sha256=_digest(store), secret_roles={"tdp_api_key": ["admin", "member"], "server_api_token": ["admin"]},
        handlers_enabled=False, approved_by="admin",
    )

    assert (await _read(store, "member", "tdp_api_key"))["value"] == "tdp-123"
    assert (await _read(store, "admin", "server_api_token"))["value"] == "root-token"
    member = await _read(store, "member", "server_api_token")
    assert "refused" in member and "root-token" not in str(member), "approved for administrators only"


@pytest.mark.asyncio
async def test_changed_code_loses_its_approval(page):
    store, marker = page
    await approvals.set_approval(
        "secret-page", code_sha256=_digest(store), secret_roles={"tdp_api_key": ["admin"]},
        handlers_enabled=True, approved_by="admin",
    )
    assert (await _read(store, "admin", "tdp_api_key"))["value"] == "tdp-123"

    store.save_source_file("secret-page", "api/helper.py", "LEAK = True\n")  # any file under api/ counts
    refused = await _read(store, "admin", "tdp_api_key")
    assert "refused" in refused and "tdp-123" not in str(refused)


@pytest.mark.asyncio
async def test_a_managed_install_runs_no_page_code_an_administrator_did_not_approve(page, monkeypatch):
    store, marker = page
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")

    blocked = await _read(store, "admin", "tdp_api_key")
    assert blocked["status"] == 403 and "not been approved" in str(blocked["detail"])
    assert not marker.exists(), "not even the module-level code ran"

    # Approved for its secrets but not for running: still nothing runs.
    await approvals.set_approval(
        "secret-page", code_sha256=_digest(store), secret_roles={"tdp_api_key": ["admin"]},
        handlers_enabled=False, approved_by="admin",
    )
    assert (await _read(store, "admin", "tdp_api_key"))["status"] == 403 and not marker.exists()

    await approvals.set_approval(
        "secret-page", code_sha256=_digest(store), secret_roles={"tdp_api_key": ["admin"]},
        handlers_enabled=True, approved_by="admin",
    )
    assert (await _read(store, "admin", "tdp_api_key"))["value"] == "tdp-123"
    assert marker.exists()

    marker.unlink()
    store.save_source_file("secret-page", "api/handlers.py", _handlers(marker) + "\n# changed\n")
    assert (await _read(store, "admin", "tdp_api_key"))["status"] == 403
    assert not marker.exists(), "changed code is not run until approved again"


def test_the_secret_accessor_cannot_be_widened_by_the_handler():
    from flocks.contracts.webui.api_runtime import _SecretAccessor

    accessor = _SecretAccessor("p", frozenset({"a"}))
    with pytest.raises(AttributeError):
        accessor._allowed = frozenset({"a", "b"})
    with pytest.raises(AttributeError):
        accessor.extra = 1


@pytest.mark.asyncio
async def test_the_approval_endpoints_review_exactly_the_code_and_secrets_declared(page, monkeypatch):
    from flocks.server.routes import webui as webui_routes

    store, _marker = page
    monkeypatch.setattr(webui_routes, "_store", store)
    monkeypatch.setattr(webui_routes, "_api_runtime", WebUIPageApiRuntime(store))
    monkeypatch.setattr(webui_routes, "require_user", lambda request: type("U", (), {"username": "root", "role": "admin"})())
    request = Request({"type": "http", "method": "PUT", "path": "/", "headers": []})

    state = await webui_routes.get_webui_page_api_approval("secret-page")
    assert state["declaredSecrets"] == ["server_api_token", "tdp_api_key"]
    assert state["approval"] is None and state["approvalCurrent"] is False
    digest = state["codeSha256"]

    Approval = webui_routes.WebUIPageApprovalRequest
    with pytest.raises(HTTPException) as stale:
        await webui_routes.approve_webui_page_api("secret-page", Approval(codeSha256="0" * 64), request)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as undeclared:
        await webui_routes.approve_webui_page_api(
            "secret-page", Approval(codeSha256=digest, secrets={"other_key": ["admin"]}), request,
        )
    assert undeclared.value.status_code == 400 and "other_key" in undeclared.value.detail
    with pytest.raises(HTTPException) as role:
        await webui_routes.approve_webui_page_api(
            "secret-page", Approval(codeSha256=digest, secrets={"tdp_api_key": ["owner"]}), request,
        )
    assert role.value.status_code == 400

    approved = await webui_routes.approve_webui_page_api(
        "secret-page", Approval(codeSha256=digest, secrets={"tdp_api_key": ["admin"]}, handlersEnabled=True), request,
    )
    assert approved["approvalCurrent"] is True and approved["approval"]["approvedBy"] == "root"
    assert approved["approval"]["secrets"] == {"tdp_api_key": ["admin"]}

    assert (await webui_routes.revoke_webui_page_api_approval("secret-page"))["revoked"] is True
    assert (await webui_routes.get_webui_page_api_approval("secret-page"))["approval"] is None


def _plant_bytecode(source_path: Path, source: str) -> None:
    """A compiled cache beside *source_path*, from other code, that the import system would accept."""
    import importlib.util
    import marshal

    stat = source_path.stat()
    cache = Path(importlib.util.cache_from_source(str(source_path)))
    cache.parent.mkdir(exist_ok=True)
    code = compile(source, str(source_path), "exec")
    header = importlib.util.MAGIC_NUMBER + (0).to_bytes(4, "little")
    header += int(stat.st_mtime).to_bytes(4, "little") + (stat.st_size & 0xFFFFFFFF).to_bytes(4, "little")
    cache.write_bytes(header + marshal.dumps(code))


@pytest.mark.asyncio
async def test_the_code_that_runs_is_the_code_that_was_reviewed(tmp_path, monkeypatch):
    """A cache planted beside the reviewed source -- handlers or a helper they import -- never runs."""
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(tmp_path / "webui_pages"))
    store = WebUIPagesStore()
    store.create_page(page_id="cache-page", title="缓存页面")
    store.save_source_file("cache-page", "api/routes.yaml", "routes:\n  - method: GET\n    path: /result\n    handler: handlers.result\n")
    store.save_source_file("cache-page", "api/handlers.py", (
        "import sys\n"
        "sys.path.insert(0, __file__.rsplit('/', 1)[0])\n"
        "import helper\n"
        "def result(ctx, request):\n"
        "    return {'handlers': 1, 'helper': helper.VALUE}\n"
    ))
    store.save_source_file("cache-page", "api/helper.py", "VALUE = 'reviewed'\n")
    api_dir = store.routes_path("cache-page").parent
    _plant_bytecode(api_dir / "handlers.py", "import helper\ndef result(ctx, request):\n    return {'handlers': 9, 'helper': helper.VALUE}\n")
    _plant_bytecode(api_dir / "helper.py", "VALUE = 'planted'\n")
    digest_before = approvals.api_code_digest(api_dir)

    app = FastAPI()
    runtime = WebUIPageApiRuntime(store)

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/contracts/webui/pages/cache-page/api/result")
    assert response.status_code == 200 and response.json() == {"handlers": 1, "helper": "reviewed"}
    assert approvals.api_code_digest(api_dir) == digest_before, "a cache is no part of the reviewed code"


@pytest.mark.asyncio
async def test_code_changed_after_the_approval_check_is_not_run(page, monkeypatch):
    """The check and the load read the same bytes: a change in between is refused, not run."""
    store, marker = page
    runtime = WebUIPageApiRuntime(store)
    approved = _digest(store)
    store.save_source_file("secret-page", "api/handlers.py", _handlers(marker) + "\nCHANGED = True\n")

    with pytest.raises(HTTPException) as refused:
        await runtime._load_page_runtime("secret-page", force_reload=True, approved=approved)

    assert refused.value.status_code == 403
    assert not marker.exists(), "not even its module-level code ran"


async def _page_result(tmp_path, monkeypatch, files: dict[str, str], *, plant: dict[str, str] | None = None,
                       sourceless: dict[str, str] | None = None) -> tuple[int, object]:
    """Serve a page made of *files* (relative to its api/ directory) and GET its /result route."""
    import importlib.util
    import marshal

    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(tmp_path / "webui_pages"))
    store = WebUIPagesStore()
    store.create_page(page_id="module-page", title="模块页面")
    store.save_source_file("module-page", "api/routes.yaml", "routes:\n  - method: GET\n    path: /result\n    handler: handlers.result\n")
    for relative, source in files.items():
        store.save_source_file("module-page", f"api/{relative}", source)
    api_dir = store.routes_path("module-page").parent
    for relative, source in (plant or {}).items():
        _plant_bytecode(api_dir / relative, source)
    for relative, source in (sourceless or {}).items():
        # A compiled module with no source beside it: the import system loads it, a review never saw it.
        code = compile(source, str(api_dir / relative), "exec")
        header = importlib.util.MAGIC_NUMBER + (0).to_bytes(12, "little")
        (api_dir / relative).write_bytes(header + marshal.dumps(code))
    runtime = WebUIPageApiRuntime(store)
    app = FastAPI()

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/contracts/webui/pages/module-page/api/result")
    return response.status_code, response.json()


_ON_PATH = "import sys\nsys.path.insert(0, __file__.rsplit('/', 1)[0])\n"


@pytest.mark.asyncio
async def test_a_directory_without_init_comes_from_the_snapshot_not_a_planted_cache(tmp_path, monkeypatch):
    """The reviewer's case: ``from libs import helper`` with no ``libs/__init__.py``."""
    marker = tmp_path / "planted-ran.txt"
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": _ON_PATH + "from libs import helper\ndef result(ctx, request):\n    return {'helper': helper.VALUE}\n",
        "libs/helper.py": "VALUE = 'reviewed'\n",
    }, plant={"libs/helper.py": f"open({str(marker)!r}, 'w').write('ran')\nVALUE = 'planted'\n"})
    assert (status_code, body) == (200, {"helper": "reviewed"})
    assert not marker.exists(), "the planted cache never ran"


@pytest.mark.asyncio
async def test_a_module_in_the_api_directory_the_snapshot_does_not_hold_is_not_run(tmp_path, monkeypatch):
    marker = tmp_path / "sourceless-ran.txt"
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": _ON_PATH + "import compiled_only\ndef result(ctx, request):\n    return {'value': compiled_only.VALUE}\n",
    }, sourceless={"compiled_only.pyc": f"open({str(marker)!r}, 'w').write('ran')\nVALUE = 'unreviewed'\n"})
    assert status_code == 500 and "disallowed import" in str(body)
    assert not marker.exists(), "refused before any of it ran"


@pytest.mark.asyncio
async def test_a_package_imports_its_own_modules_relatively(tmp_path, monkeypatch):
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": "import pkg\nfrom pkg.deep.leaf import LEAF\ndef result(ctx, request):\n"
                       "    return {'value': pkg.VALUE, 'other': pkg.other.NAME, 'leaf': LEAF}\n",
        "pkg/__init__.py": "from .helper import VALUE\nfrom . import other\n",
        "pkg/helper.py": "VALUE = 'pkg'\n",
        "pkg/other.py": "NAME = 'other'\n",
        "pkg/deep/__init__.py": "",
        "pkg/deep/leaf.py": "from ..helper import VALUE\nLEAF = VALUE + '-leaf'\n",
    })
    assert (status_code, body) == (200, {"value": "pkg", "other": "other", "leaf": "pkg-leaf"})


@pytest.mark.asyncio
async def test_modules_that_import_each_other_load(tmp_path, monkeypatch):
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": "import a, b\ndef result(ctx, request):\n    return {'total': a.total(), 'back': b.back()}\n",
        "a.py": "import b\nA = 1\ndef total():\n    return A + b.B\n",
        "b.py": "import a\nB = 2\ndef back():\n    return a.A\n",
    })
    assert (status_code, body) == (200, {"total": 3, "back": 1})


@pytest.mark.asyncio
async def test_a_module_that_failed_is_loaded_again_not_left_half_made(tmp_path, monkeypatch):
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": "try:\n    import flaky\nexcept RuntimeError:\n    pass\nimport flaky\n"
                       "def result(ctx, request):\n    return {'loaded': flaky.LOADED}\n",
        "box.py": "n = 0\n",
        "flaky.py": "import box\nbox.n += 1\nif box.n == 1:\n    raise RuntimeError('first time')\nLOADED = box.n\n",
    })
    assert (status_code, body) == (200, {"loaded": 2})


@pytest.mark.asyncio
async def test_a_relative_import_does_not_leave_the_page(tmp_path, monkeypatch):
    status_code, body = await _page_result(tmp_path, monkeypatch, {
        "handlers.py": "import helper\ndef result(ctx, request):\n    return {}\n",
        "helper.py": "from .. import server\n",
    })
    assert status_code == 500 and "relative import beyond" in str(body)
