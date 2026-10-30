"""A page's server code runs as one snapshot, under whatever policy an extension sets.

The runtime compiles the bytes it read once -- never a compiled cache beside
them, never a module the snapshot does not hold -- so a decision taken over a
digest holds for the code that runs.  A policy over page code may refuse to
run it (before even its module-level code runs) and narrows the secrets a
call reads; without one, every page runs and reads what it declares.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from flocks.contracts.webui.api_code import api_code_digest
from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.extensions import register_page_code_policy, unregister_page_code_policy

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
    values = {"tdp_api_key": "tdp-123", "server_api_token": "root-token", "other_key": "other"}

    class _Manager:
        def get(self, key):
            return values.get(key)

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: _Manager())
    return store, marker


class _Policy:
    """Runs only the digests it was told about; lets each role read what it lists."""

    def __init__(self, *, runnable: Optional[set[str]] = None, by_role: Optional[dict[str, set[str]]] = None) -> None:
        self.runnable = runnable
        self.by_role = by_role or {}
        self.asked: list[tuple[str, str]] = []
        self.declared_seen: list[frozenset[str]] = []

    async def run_refusal(self, page_id: str, code_sha256: str) -> Optional[str]:
        self.asked.append((page_id, code_sha256))
        if self.runnable is None or code_sha256 in self.runnable:
            return None
        return "this page's code is not cleared to run here"

    async def readable_secrets(self, page_id: str, code_sha256: str, declared: frozenset[str], user: Any) -> frozenset[str]:
        self.declared_seen.append(declared)
        # Asks for more than declared: the runtime still hands over declared ones only.
        return frozenset(self.by_role.get(user.get("role"), set()))


@pytest.fixture
def policy():
    registered: list[_Policy] = []

    def register(**kwargs: Any) -> _Policy:
        created = _Policy(**kwargs)
        register_page_code_policy(created)
        registered.append(created)
        return created

    yield register
    for created in registered:
        unregister_page_code_policy(created)


def _client(store: WebUIPagesStore, role: str, runtime: Optional[WebUIPageApiRuntime] = None) -> AsyncClient:
    app = FastAPI()
    runtime = runtime or WebUIPageApiRuntime(store)

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": role})

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _read(store, role: str, key: str, runtime: Optional[WebUIPageApiRuntime] = None) -> dict:
    async with _client(store, role, runtime) as client:
        response = await client.get("/api/contracts/webui/pages/secret-page/api/read", params={"key": key})
    return {"status": response.status_code, **(response.json() if response.status_code == 200 else {"detail": response.json()})}


def _digest(store: WebUIPagesStore) -> str:
    return api_code_digest(store.routes_path("secret-page").parent)


@pytest.mark.asyncio
async def test_without_a_policy_a_page_runs_and_reads_what_it_declares(page):
    store, marker = page
    assert (await _read(store, "member", "tdp_api_key"))["value"] == "tdp-123"
    undeclared = await _read(store, "admin", "other_key")
    assert "refused" in undeclared and "other" not in str(undeclared.get("value", ""))
    assert marker.exists()


@pytest.mark.asyncio
async def test_a_policy_refuses_code_before_any_of_it_runs(page, policy):
    store, marker = page
    clearance = policy(runnable=set())

    refused = await _read(store, "admin", "tdp_api_key")
    assert refused["status"] == 403 and "not cleared to run" in str(refused["detail"])
    assert not marker.exists(), "not even the module-level code ran"
    assert clearance.asked == [("secret-page", _digest(store))], "asked about the code as it is now"

    clearance.runnable = {_digest(store)}
    clearance.by_role = {"admin": {"tdp_api_key"}}
    assert (await _read(store, "admin", "tdp_api_key"))["value"] == "tdp-123"
    assert marker.exists()

    marker.unlink()
    store.save_source_file("secret-page", "api/handlers.py", _handlers(marker) + "\n# changed\n")
    assert (await _read(store, "admin", "tdp_api_key"))["status"] == 403
    assert not marker.exists(), "changed code is a different digest"


@pytest.mark.asyncio
async def test_a_policy_narrows_the_declared_secrets_and_cannot_widen_them(page, policy):
    store, _marker = page
    policy(by_role={"admin": {"tdp_api_key", "server_api_token", "other_key"}, "member": {"tdp_api_key"}})

    assert (await _read(store, "admin", "server_api_token"))["value"] == "root-token"
    assert (await _read(store, "member", "tdp_api_key"))["value"] == "tdp-123"
    member = await _read(store, "member", "server_api_token")
    assert "refused" in member and "root-token" not in str(member)
    widened = await _read(store, "admin", "other_key")
    assert "refused" in widened, "a policy picks among the declared secrets only"


@pytest.mark.asyncio
async def test_the_policy_sees_the_declared_secrets(page, policy):
    store, _marker = page
    clearance = policy(by_role={"admin": {"tdp_api_key"}})
    await _read(store, "admin", "tdp_api_key")
    assert clearance.declared_seen == [frozenset({"tdp_api_key", "server_api_token"})]


@pytest.mark.asyncio
async def test_a_policy_that_raises_refuses_the_call(page, policy):
    store, _marker = page

    class _Raising(_Policy):
        async def readable_secrets(self, page_id, code_sha256, declared, user):
            raise HTTPException(status_code=403, detail="withdrawn")

    raising = _Raising()
    register_page_code_policy(raising)
    try:
        refused = await _read(store, "admin", "tdp_api_key")
    finally:
        unregister_page_code_policy(raising)
    assert refused["status"] == 403 and "withdrawn" in str(refused["detail"])


@pytest.mark.asyncio
async def test_unregistering_another_policy_leaves_the_current_one(page, policy):
    from flocks.contracts.webui.api_code import page_code_policy

    current = policy(runnable=set())
    unregister_page_code_policy(_Policy())
    assert page_code_policy() is current


def test_the_secret_accessor_cannot_be_widened_by_the_handler():
    from flocks.contracts.webui.api_runtime import _SecretAccessor

    accessor = _SecretAccessor("p", frozenset({"a"}))
    with pytest.raises(AttributeError):
        accessor._allowed = frozenset({"a", "b"})
    with pytest.raises(AttributeError):
        accessor.extra = 1


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
async def test_the_code_that_runs_is_the_snapshot(tmp_path, monkeypatch):
    """A cache planted beside the source -- handlers or a helper they import -- never runs."""
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
    digest_before = api_code_digest(api_dir)

    app = FastAPI()
    runtime = WebUIPageApiRuntime(store)

    @app.get("/api/contracts/webui/pages/{page_id}/api/{api_path:path}")
    async def _dispatch(page_id: str, api_path: str, request: Request):
        return await runtime.dispatch(page_id, api_path, request, {"role": "admin"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/contracts/webui/pages/cache-page/api/result")
    assert response.status_code == 200 and response.json() == {"handlers": 1, "helper": "reviewed"}
    assert api_code_digest(api_dir) == digest_before, "a cache is no part of the code's digest"


@pytest.mark.asyncio
async def test_code_changed_after_the_check_is_not_run(page):
    """The check and the load read the same bytes: a change in between is refused, not run."""
    store, marker = page
    runtime = WebUIPageApiRuntime(store)
    checked = _digest(store)
    store.save_source_file("secret-page", "api/handlers.py", _handlers(marker) + "\nCHANGED = True\n")

    with pytest.raises(HTTPException) as refused:
        await runtime._load_page_runtime("secret-page", force_reload=True, expected_sha256=checked)

    assert refused.value.status_code == 403
    assert not marker.exists(), "not even its module-level code ran"


@pytest.mark.asyncio
async def test_a_cached_runtime_of_other_code_is_not_reused_under_a_policy(page, policy):
    store, marker = page
    runtime = WebUIPageApiRuntime(store)
    first = _digest(store)
    clearance = policy(runnable={first}, by_role={"admin": {"tdp_api_key"}})
    assert (await _read(store, "admin", "tdp_api_key", runtime))["value"] == "tdp-123"

    # Same mtimes, other bytes: the cache key alone would hand back the old module.
    api_dir = store.routes_path("secret-page").parent
    handlers = api_dir / "handlers.py"
    stat = handlers.stat()
    handlers.write_text(_handlers(marker).replace("ctx.secrets.get(key)", "'replaced'"), encoding="utf-8")
    import os

    os.utime(handlers, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    clearance.runnable = {first, _digest(store)}
    assert (await _read(store, "admin", "tdp_api_key", runtime))["value"] == "replaced"


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
        # A compiled module with no source beside it: the import system loads it, the snapshot never holds it.
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
    """``from libs import helper`` with no ``libs/__init__.py``."""
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
