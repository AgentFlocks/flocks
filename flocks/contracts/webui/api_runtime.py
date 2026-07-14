"""Page-scoped API runtime for WebUI page plugins."""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import inspect
import json
import sys
import sysconfig
import threading
import time
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
_IMPORT_GUARD_LOCK = threading.RLock()


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
        runtime = await self._load_page_runtime(page_id, force_reload=False)
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
        runtime = await self._load_page_runtime(page_id, force_reload=True)
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

        runtime = await self._load_page_runtime(page_id, force_reload=False)
        normalized_path = "/" + api_path.strip("/")
        key = (request.method.upper(), normalized_path)
        entry = runtime.routes.get(key)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page api route not found")

        ctx = self._create_context(page_id, user)
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

    async def _load_page_runtime(self, page_id: str, *, force_reload: bool) -> _PageRuntime:
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
            ):
                return cached

            runtime = self._compile_runtime(page_id, routes_path, handlers_path, routes_mtime_ns, handlers_mtime_ns)
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
    ) -> _PageRuntime:
        try:
            routes_text = routes_path.read_text(encoding="utf-8")
            raw = yaml.safe_load(routes_text) or {}
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"invalid routes.yaml: {exc}") from exc

        route_items = raw.get("routes")
        if not isinstance(route_items, list):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="routes.yaml must contain a routes list")

        module_name = f"flocks_webui_page_{page_id}_{int(time.time() * 1000)}"
        spec = importlib.util.spec_from_file_location(module_name, handlers_path)
        if spec is None or spec.loader is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to load handlers.py")
        module = importlib.util.module_from_spec(spec)
        with _IMPORT_GUARD_LOCK:
            original_import = builtins.__import__
            guarded_import = self._create_guarded_import(
                api_root=handlers_path.parent,
                original_import=original_import,
            )
            try:
                builtins.__import__ = guarded_import  # type: ignore[assignment]
                spec.loader.exec_module(module)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"failed to load handlers.py: {exc}",
                ) from exc
            finally:
                builtins.__import__ = original_import  # type: ignore[assignment]

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
        )

    def _create_guarded_import(self, *, api_root: Path, original_import):
        api_root_resolved = api_root.resolve()
        owner_thread_id = threading.get_ident()

        def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if threading.get_ident() != owner_thread_id:
                return original_import(name, globals, locals, fromlist, level)
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
            return True
        if str(origin_path).startswith(str(_STDLIB_DIR)):
            return True
        return False

    async def _invoke_handler(self, handler: Any, ctx: Any, request: Request) -> Any:
        result = handler(ctx, request)
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

    def _create_context(self, page_id: str, user: Any) -> Any:
        logger = Log.create(service=f"webui-page-api:{page_id}")
        return SimpleNamespace(
            page_id=page_id,
            user=user,
            secrets=_SecretAccessor(),
            logger=logger,
            cache=None,
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


class _SecretAccessor:
    def get(self, key: str, default: Any = None) -> Any:
        from flocks.security import get_secret_manager

        value = get_secret_manager().get(key)
        return default if value is None else value


_runtime: Optional[WebUIPageApiRuntime] = None


def get_api_runtime() -> WebUIPageApiRuntime:
    global _runtime
    if _runtime is None:
        _runtime = WebUIPageApiRuntime()
    return _runtime
