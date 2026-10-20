"""Page-scoped API runtime for WebUI page plugins."""

from __future__ import annotations

import asyncio
import builtins
import contextvars
import importlib.machinery
import importlib.util
import inspect
import json
import sys
import sysconfig
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Optional

import yaml
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from flocks.contracts.webui.models import WebUIPageApiMeta
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.utils.log import Log

log = Log.create(service="webui-page-api-runtime")

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_TIMEOUT_MS = 5000
_MAX_TIMEOUT_MS = 30000
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_REQUEST_BODY_BYTES = 1_000_000
_CLIENT_CLOSED_REQUEST_STATUS = 499
_STDLIB_DIR = Path(sysconfig.get_paths()["stdlib"]).resolve()
_SYNC_HANDLER_WORKERS = 8
_sync_handler_executor = ThreadPoolExecutor(
    max_workers=_SYNC_HANDLER_WORKERS, thread_name_prefix="webui-page-api"
)
_sync_handler_slots = threading.BoundedSemaphore(_SYNC_HANDLER_WORKERS)


@dataclass(frozen=True)
class _RouteSpec:
    method: str
    path: str
    handler_name: str
    timeout_ms: int
    description: str


@dataclass(frozen=True)
class _RouteEntry:
    spec: _RouteSpec
    handler: Any


@dataclass
class _PageRuntime:
    page_id: str
    routes: dict[tuple[str, str], _RouteEntry]
    module: ModuleType
    routes_mtime_ns: int
    handlers_mtime_ns: int
    loaded_at: int
    #: Secret ids the page declares (the ``secrets`` list in ``api/routes.yaml``).
    #: A handler reads one only where an administrator approved it for the
    #: caller's role (flocks.contracts.webui.approvals).
    secrets: frozenset[str] = frozenset()
    #: Digest of the server code this runtime was compiled from; an approval
    #: holds for exactly this code.
    code_sha256: str = ""


def _inside(location: Any, root: Path) -> bool:
    try:
        return Path(str(location)).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _regular_module_elsewhere(name: str, root: Path) -> bool:
    """Whether a top-level ``name`` is a regular module outside ``root``.

    A directory without ``__init__.py`` does not hide such a module in Python
    (namespace portions come last), and it does not here either.
    """
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or spec.origin in {None, "namespace"}:
        return False
    if spec.origin in {"built-in", "frozen"}:
        return True
    return not _inside(spec.origin, root)


class _PageModules:
    """A page's own Python modules, compiled from the snapshot an approval was checked against.

    They are put together here, never through the import system, which would
    take a compiled cache planted next to the source or a module the snapshot
    does not hold. The import statement's rules still hold: packages with and
    without ``__init__.py``, relative imports, modules that import each other.
    """

    def __init__(self, snapshot: dict[str, bytes], api_root: Path, prefix: str) -> None:
        self._snapshot = snapshot
        self._root = api_root
        self._prefix = prefix
        self._modules: dict[str, ModuleType] = {}
        self._top: Optional[ModuleType] = None
        self.builtins: dict[str, Any] = {}

    def _source_of(self, dotted: str) -> Optional[tuple[str, bool]]:
        base = dotted.replace(".", "/")
        if f"{base}/__init__.py" in self._snapshot:
            return f"{base}/__init__.py", True
        if f"{base}.py" in self._snapshot:
            return f"{base}.py", False
        return None

    def _found(self, dotted: str) -> Optional[tuple[Optional[str], bool]]:
        """(source file, is package) of a page module; no source for a directory without ``__init__.py``."""
        found = self._source_of(dotted)
        if found is not None:
            return found
        base = dotted.replace(".", "/") + "/"
        if any(path.startswith(base) and path.endswith(".py") for path in self._snapshot):
            return None, True
        return None

    def provides(self, name: str) -> bool:
        top = name.split(".", 1)[0]
        found = self._found(top)
        if found is None:
            return False
        return found[0] is not None or not _regular_module_elsewhere(top, self._root)

    def _top_module(self) -> ModuleType:
        """What ``from . import x`` in a top-level page module names: the page's api directory."""
        if self._top is None:
            self._top = ModuleType(self._prefix)
            self._top.__path__ = []
            self._top.__dict__["__flocks_page_snapshot__"] = True
        return self._top

    def _new_module(self, name: str, relative: Optional[str], *, is_package: bool) -> ModuleType:
        path = self._root / relative if relative is not None else None
        module = ModuleType(name)
        if path is not None:
            module.__file__ = str(path)
        module.__spec__ = importlib.machinery.ModuleSpec(
            name, loader=None, origin=str(path) if path is not None else None, is_package=is_package,
        )
        module.__package__ = name if is_package else name.rpartition(".")[0]
        if is_package:
            module.__path__ = []  # submodules come from the snapshot only
        module.__dict__["__builtins__"] = self.builtins
        module.__dict__["__flocks_page_snapshot__"] = True
        return module

    def _run(self, module: ModuleType, relative: str) -> None:
        code = compile(self._snapshot[relative], str(self._root / relative), "exec", dont_inherit=True)
        exec(code, module.__dict__)

    def execute(self, name: str, relative: str, *, is_package: bool) -> ModuleType:
        module = self._new_module(name, relative, is_package=is_package)
        self._run(module, relative)
        return module

    def _load(self, dotted: str) -> ModuleType:
        if dotted in self._modules:
            return self._modules[dotted]
        parent = self._load(dotted.rsplit(".", 1)[0]) if "." in dotted else self._top_module()
        found = self._found(dotted)
        if found is None:
            raise ModuleNotFoundError(f"No module named {dotted!r} in the page's api directory")
        relative, is_package = found
        module = self._new_module(f"{self._prefix}.{dotted}", relative, is_package=is_package)
        # As the import system does: known before its code runs, so modules
        # that import each other find each other; forgotten if it fails.
        attribute = dotted.rsplit(".", 1)[-1]
        self._modules[dotted] = module
        setattr(parent, attribute, module)
        try:
            if relative is not None:
                self._run(module, relative)
        except BaseException:
            self._modules.pop(dotted, None)
            if getattr(parent, attribute, None) is module:
                delattr(parent, attribute)
            raise
        return module

    def _with_fromlist(self, dotted: str, module: ModuleType, fromlist: Any) -> None:
        for item in fromlist or ():
            if item == "*" or hasattr(module, item):
                continue
            submodule = f"{dotted}.{item}" if dotted else item
            if self._found(submodule) is not None:
                self._load(submodule)

    def import_(self, name: str, fromlist: Any) -> ModuleType:
        """What ``import name`` / ``from name import ...`` gives, as the import statement expects."""
        module = self._load(name)
        self._with_fromlist(name, module, fromlist)
        if fromlist:
            return module
        return self._load(name.split(".", 1)[0])

    def import_relative(self, name: str, globals: Any, level: int, fromlist: Any) -> ModuleType:
        """``from .name import ...`` in a page module: resolved inside the page, from the snapshot."""
        scope = globals if isinstance(globals, dict) else {}
        package = scope.get("__package__")
        if package is None:
            package = getattr(scope.get("__spec__"), "parent", None)
        absolute = importlib.util.resolve_name("." * level + name, package or "")
        if absolute == self._prefix:
            dotted = ""
        elif absolute.startswith(self._prefix + "."):
            dotted = absolute[len(self._prefix) + 1:]
        else:
            raise ImportError(f"attempted relative import beyond the page's api directory: {absolute}")
        module = self._load(dotted) if dotted else self._top_module()
        self._with_fromlist(dotted, module, fromlist)
        return module

    def found_inside(self, name: str) -> Optional[str]:
        """Where the import system would load ``name`` from inside the page's directory, if it would.

        Asked before anything is imported: a module there that the snapshot
        does not hold (a compiled file without source, an extension module)
        is not the reviewed code and must not run at all. Each package on the
        way is checked before the next lookup imports it.
        """
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            prefix = ".".join(parts[:end])
            module = sys.modules.get(prefix)
            try:
                spec = getattr(module, "__spec__", None) if module is not None else importlib.util.find_spec(prefix)
            except (ImportError, ValueError, AttributeError):
                return None  # the import itself reports it
            if spec is None:
                return None
            locations = [] if spec.origin in {None, "built-in", "frozen", "namespace"} else [spec.origin]
            locations += list(spec.submodule_search_locations or ())
            for location in locations:
                if _inside(location, self._root):
                    return str(location)
        return None


class WebUIPageApiRuntime:
    """Load and dispatch api/routes.yaml + api/handlers.py for a page."""

    def __init__(self, store: Optional[WebUIPagesStore] = None) -> None:
        self._store = store or WebUIPagesStore()
        self._cache: dict[str, _PageRuntime] = {}
        self._lock = asyncio.Lock()

    def clear_page(self, page_id: str) -> None:
        page_id = self._store.validate_page_id(page_id)
        self._cache.pop(page_id, None)

    async def list_routes(self, page_id: str) -> list[dict[str, str]]:
        approved = await self._require_runnable(page_id)
        runtime = await self._load_page_runtime(page_id, force_reload=False, approved=approved)
        return [
            {
                "method": entry.spec.method,
                "path": entry.spec.path,
                "handler": entry.spec.handler_name,
                "description": entry.spec.description,
            }
            for entry in runtime.routes.values()
        ]

    async def reload_page(self, page_id: str) -> list[dict[str, str]]:
        approved = await self._require_runnable(page_id)
        runtime = await self._load_page_runtime(page_id, force_reload=True, approved=approved)
        return [
            {
                "method": entry.spec.method,
                "path": entry.spec.path,
                "handler": entry.spec.handler_name,
                "description": entry.spec.description,
            }
            for entry in runtime.routes.values()
        ]

    async def dispatch(self, page_id: str, api_path: str, request: Request, user: Any) -> Response:
        page_id = self._store.validate_page_id(page_id)
        if not self._store.page_dir(page_id).is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"page not found: {page_id}")
        try:
            await self._guard_request_size(request)
        except ClientDisconnect:
            log.info(
                "webui_pages.api.client_disconnected",
                {"pageId": page_id, "method": request.method, "path": request.url.path},
            )
            return Response(status_code=_CLIENT_CLOSED_REQUEST_STATUS)

        # Loading runs the page's module code: on a managed install, only code
        # an administrator approved gets that far.
        approved = await self._require_runnable(page_id)
        runtime = await self._load_page_runtime(page_id, force_reload=False, approved=approved)
        normalized_path = "/" + api_path.strip("/")
        key = (request.method.upper(), normalized_path)
        entry = runtime.routes.get(key)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page api route not found")

        ctx = self._create_context(page_id, user, await self._readable_secrets(runtime, user))
        try:
            result = await asyncio.wait_for(
                self._invoke_handler(entry.handler, ctx, request),
                timeout=entry.spec.timeout_ms / 1000,
            )
        except asyncio.TimeoutError as exc:
            await self._mark_failed(page_id, "page api handler timed out")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="page api handler timed out") from exc
        except HTTPException:
            raise
        except Exception as exc:
            await self._mark_failed(page_id, f"handler execution failed: {exc}")
            log.warning("webui_pages.api.handler_failed", {"pageId": page_id, "error": str(exc)})
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="page api execution failed") from exc

        try:
            response = self._normalize_response(result)
        except ValueError as exc:
            await self._mark_failed(page_id, str(exc))
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

        return response

    async def _require_runnable(self, page_id: str) -> Optional[str]:
        """On a managed install, run a page's server code only as an administrator approved it.

        Checked against the files as they are now, before anything of them is
        imported: module-level code runs on import.  Returns the approved
        digest (None off a managed install): loading checks it again against
        the very bytes it is about to execute.
        """
        from flocks.contracts.webui.approvals import api_code_digest, get_approval
        from flocks.updater.deploy import declared_managed

        if not declared_managed():
            return None
        api_dir = self._store.routes_path(page_id).parent
        if not api_dir.is_dir():
            return None  # nothing to run; dispatch answers "not configured"
        approval = await get_approval(page_id)
        digest = await asyncio.to_thread(api_code_digest, api_dir)
        if approval is None or not approval.handlers_enabled or approval.code_sha256 != digest:
            log.warning(
                "webui_pages.api.code_not_approved",
                {"pageId": page_id, "approved": approval is not None, "codeSha256": digest},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this page's server code has not been approved by an administrator",
            )
        return approval.code_sha256

    async def _readable_secrets(self, runtime: _PageRuntime, user: Any) -> frozenset[str]:
        """What this call may read: declared, approved for this code, and for the caller's role.

        On a managed install the handlers do not run at all until an
        administrator approved this code: an interface cannot keep code running
        in this process away from the rest of it, so whether it runs is a
        person's decision.
        """
        from flocks.contracts.webui.approvals import get_approval
        from flocks.updater.deploy import declared_managed

        approval = await get_approval(runtime.page_id)
        current = approval if approval is not None and approval.code_sha256 == runtime.code_sha256 else None
        if declared_managed() and (current is None or not current.handlers_enabled):
            log.warning(
                "webui_pages.api.code_not_approved",
                {"pageId": runtime.page_id, "approved": approval is not None, "codeSha256": runtime.code_sha256},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this page's server code has not been approved by an administrator",
            )
        if current is None:
            return frozenset()
        return runtime.secrets & current.secrets_for(_caller_role(user))

    async def _guard_request_size(self, request: Request) -> None:
        header_value = request.headers.get("content-length")
        if header_value:
            try:
                content_length = int(header_value)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid content-length header")
            if content_length > _MAX_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="request body is too large",
                )

        # Enforce upper bound even when content-length is missing/spoofed.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="request body is too large",
                )
        request._body = bytes(body)  # type: ignore[attr-defined]

    async def _load_page_runtime(self, page_id: str, *, force_reload: bool, approved: Optional[str] = None) -> _PageRuntime:
        page_id = self._store.validate_page_id(page_id)
        async with self._lock:
            routes_path = self._store.routes_path(page_id)
            handlers_path = self._store.api_handlers_path(page_id)
            if not routes_path.is_file() or not handlers_path.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page api is not configured")

            routes_mtime_ns = routes_path.stat().st_mtime_ns
            handlers_mtime_ns = handlers_path.stat().st_mtime_ns
            cached = self._cache.get(page_id)
            if (
                not force_reload
                and cached is not None
                and cached.routes_mtime_ns == routes_mtime_ns
                and cached.handlers_mtime_ns == handlers_mtime_ns
                and (approved is None or cached.code_sha256 == approved)
            ):
                return cached

            runtime = self._compile_runtime(
                page_id, routes_path, handlers_path, routes_mtime_ns, handlers_mtime_ns, approved=approved,
            )
            self._cache[page_id] = runtime
            self._store.write_api_meta(
                page_id,
                WebUIPageApiMeta(
                    status="ready",
                    loadedAt=runtime.loaded_at,
                    error=None,
                    routes=[
                        {
                            "method": entry.spec.method,
                            "path": entry.spec.path,
                            "handler": entry.spec.handler_name,
                        }
                        for entry in runtime.routes.values()
                    ],
                ),
            )
            return runtime

    def _compile_runtime(
        self,
        page_id: str,
        routes_path: Path,
        handlers_path: Path,
        routes_mtime_ns: int,
        handlers_mtime_ns: int,
        *,
        approved: Optional[str] = None,
    ) -> _PageRuntime:
        from flocks.contracts.webui.approvals import api_code_snapshot, snapshot_digest

        # One read of the page's code: the digest an approval is held against
        # and the source compiled below are the same bytes.
        snapshot = api_code_snapshot(handlers_path.parent)
        code_sha256 = snapshot_digest(snapshot)
        if approved is not None and code_sha256 != approved:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this page's server code has not been approved by an administrator",
            )
        try:
            routes_text = snapshot.get("routes.yaml", b"").decode("utf-8")
            raw = yaml.safe_load(routes_text) or {}
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"invalid routes.yaml: {exc}") from exc

        route_items = raw.get("routes")
        if not isinstance(route_items, list):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="routes.yaml must contain a routes list")
        declared_secrets = raw.get("secrets", [])  # secret-guard: allow 误报：字段名含 secret
        if not isinstance(declared_secrets, list) or not all(
            isinstance(item, str) and item.strip() for item in declared_secrets
        ):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="routes.yaml secrets must be a list of secret ids",
            )

        module_name = f"flocks_webui_page_{page_id}_{int(time.time() * 1000)}"
        if "handlers.py" not in snapshot:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to load handlers.py")
        page_builtins_module = ModuleType("builtins")
        page_builtins_module.__dict__.update(vars(builtins))
        page_modules = _PageModules(snapshot, handlers_path.parent, module_name)
        guarded_import = self._create_guarded_import(
            api_root=handlers_path.parent,
            original_import=builtins.__import__,
            builtins_module=page_builtins_module,
            page_modules=page_modules,
        )
        page_builtins_module.__dict__["__import__"] = guarded_import
        page_modules.builtins = dict(vars(page_builtins_module))
        try:
            module = page_modules.execute(module_name, "handlers.py", is_package=False)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to load handlers.py: {exc}",
            ) from exc

        routes: dict[tuple[str, str], _RouteEntry] = {}
        for item in route_items:
            if not isinstance(item, dict):
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="invalid route item")
            method = str(item.get("method", "")).upper().strip()
            path = str(item.get("path", "")).strip()
            handler_name = str(item.get("handler", "")).strip()
            description = str(item.get("description", "")).strip()
            timeout_ms_raw = item.get("timeoutMs", _DEFAULT_TIMEOUT_MS)

            if method not in _ALLOWED_METHODS:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"unsupported method: {method}")
            if not path.startswith("/") or ".." in path or "//" in path:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"invalid route path: {path}")
            normalized_path = "/" + path.strip("/")
            if not handler_name:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="route handler is required")

            timeout_ms = _DEFAULT_TIMEOUT_MS
            try:
                timeout_ms = min(max(int(timeout_ms_raw), 1), _MAX_TIMEOUT_MS)
            except Exception:
                pass

            callable_name = handler_name.split(".", 1)[1] if handler_name.startswith("handlers.") else handler_name
            handler = getattr(module, callable_name, None)
            if handler is None or not callable(handler):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"handler not found: {handler_name}",
                )

            key = (method, normalized_path)
            routes[key] = _RouteEntry(
                spec=_RouteSpec(
                    method=method,
                    path=normalized_path,
                    handler_name=handler_name,
                    timeout_ms=timeout_ms,
                    description=description,
                ),
                handler=handler,
            )

        return _PageRuntime(
            page_id=page_id,
            routes=routes,
            module=module,
            routes_mtime_ns=routes_mtime_ns,
            handlers_mtime_ns=handlers_mtime_ns,
            loaded_at=int(time.time() * 1000),
            secrets=frozenset(item.strip() for item in declared_secrets),  # secret-guard: allow 误报：字段名含 secret
            code_sha256=code_sha256,
        )

    def _create_guarded_import(
        self, *, api_root: Path, original_import, builtins_module: ModuleType, page_modules: "Optional[_PageModules]" = None,
    ):
        api_root_resolved = api_root.resolve()

        def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name == "builtins":
                return builtins_module
            if page_modules is not None:
                # The page's own modules come from the reviewed snapshot,
                # never through the import system (which would take a
                # compiled cache planted next to the source).
                if level > 0:
                    return page_modules.import_relative(name, globals, level, fromlist)
                if page_modules.provides(name):
                    return page_modules.import_(name, fromlist)
                inside = page_modules.found_inside(name)
                if inside is not None:
                    raise ImportError(f"disallowed import outside page api directory: {inside}")
            module = original_import(name, globals, locals, fromlist, level)
            modules_to_check = [module]
            if fromlist:
                for item in fromlist:
                    if item == "*":
                        continue
                    sub_name = f"{module.__name__}.{item}"
                    maybe_sub = sys.modules.get(sub_name)
                    if maybe_sub is not None:
                        modules_to_check.append(maybe_sub)
            for mod in modules_to_check:
                if not self._is_allowed_import_module(mod, api_root=api_root_resolved):
                    origin = getattr(mod, "__file__", None) or getattr(getattr(mod, "__spec__", None), "origin", None)
                    raise ImportError(f"disallowed import outside page api directory: {origin or mod.__name__}")
            return module

        return _guarded_import

    def _is_allowed_import_module(self, module: ModuleType, *, api_root: Path) -> bool:
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None)
        if origin in {None, "built-in", "frozen"}:
            return True
        try:
            origin_path = Path(str(origin)).resolve()
        except Exception:
            return False
        if str(origin_path).startswith(str(api_root)):
            # Only through the snapshot: a module of the page's directory the
            # import system loaded (from source or a cache beside it) was not
            # the reviewed code.
            return bool(getattr(module, "__flocks_page_snapshot__", False))
        if str(origin_path).startswith(str(_STDLIB_DIR)):
            return True
        return False

    async def _invoke_handler(self, handler: Any, ctx: Any, request: Request) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler(ctx, request)
        # Timed-out threads cannot be forcibly stopped. Keep their slots until
        # the work actually finishes so retries cannot grow an unbounded queue.
        if not _sync_handler_slots.acquire(blocking=False):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="page api synchronous handlers are busy",
                headers={"Retry-After": "1"},
            )
        try:
            future = _sync_handler_executor.submit(
                contextvars.copy_context().run, handler, ctx, request
            )
        except BaseException:
            _sync_handler_slots.release()
            raise
        future.add_done_callback(lambda _: _sync_handler_slots.release())
        result = await asyncio.wrap_future(future)
        if inspect.isawaitable(result):
            return await result
        return result

    def _normalize_response(self, result: Any) -> Response:
        if isinstance(result, Response):
            return result
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > _MAX_RESPONSE_BYTES:
            raise ValueError("response body is too large")
        return JSONResponse(content=result)

    def _create_context(self, page_id: str, user: Any, secrets: frozenset[str] = frozenset()) -> Any:
        from flocks.db import sync_query

        logger = Log.create(service=f"webui-page-api:{page_id}")
        return SimpleNamespace(
            page_id=page_id,
            user=user,
            secrets=_SecretAccessor(page_id, secrets),  # secret-guard: allow 误报：字段名含 secret
            logger=logger,
            cache=None,
            database=SimpleNamespace(connect=sync_query.connect, identity=sync_query.identity),
        )

    async def _mark_failed(self, page_id: str, error: str) -> None:
        self._store.write_api_meta(
            page_id,
            WebUIPageApiMeta(
                status="failed",
                loadedAt=int(time.time() * 1000),
                error=(error or "page api runtime failed")[:2000],
                routes=[],
            ),
        )


class SecretNotDeclared(PermissionError):
    """A page handler asked for a secret it may not read on this call: not declared
    in its routes.yaml, not approved by an administrator for this code, or not for
    the caller's role."""


def _caller_role(user: Any) -> Optional[str]:
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    return role if isinstance(role, str) else None


class _SecretAccessor:
    """``ctx.secrets``: the secrets this call may read, and only those.

    A page plugin is extension code.  Handing every handler the whole secret
    store would make any page able to read every credential the install holds,
    so a page lists the secret ids it needs (``secrets:`` in ``api/routes.yaml``),
    an administrator approves them for the page's current code and for caller
    roles, and a call gets what both allow.  Values are never logged.
    """

    __slots__ = ("_page_id", "_allowed")

    def __init__(self, page_id: str = "", allowed: frozenset[str] = frozenset()) -> None:
        object.__setattr__(self, "_page_id", page_id)
        object.__setattr__(self, "_allowed", frozenset(allowed))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ctx.secrets is read-only")

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._allowed:
            log.warning(
                "webui_pages.api.secret_refused",
                {"pageId": self._page_id, "secret": key},
            )
            raise SecretNotDeclared(
                f"page {self._page_id or '?'} may not read secret {key!r} on this call; "
                "a page lists it under 'secrets' in api/routes.yaml and an administrator approves it"
            )
        from flocks.security import get_secret_manager

        value = get_secret_manager().get(key)
        return default if value is None else value


_runtime: Optional[WebUIPageApiRuntime] = None


def get_api_runtime() -> WebUIPageApiRuntime:
    global _runtime
    if _runtime is None:
        _runtime = WebUIPageApiRuntime()
    return _runtime
