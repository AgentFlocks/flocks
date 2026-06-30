"""Startup reconciliation for WebUI page plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.builder import RUNTIME_NAME, RUNTIME_VERSION, SDK_IMPORT_NAME, WebUIPageBuilder
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.utils.log import Log

log = Log.create(service="webui-pages-bootstrap")

_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}


async def reconcile_webui_pages(
    *,
    store: Optional[WebUIPagesStore] = None,
    builder: Optional[WebUIPageBuilder] = None,
    runtime: Optional[WebUIPageApiRuntime] = None,
) -> None:
    store = store or WebUIPagesStore()
    builder = builder or WebUIPageBuilder(store)
    runtime = runtime or WebUIPageApiRuntime(store)
    store.ensure_root()

    for page in store.list_pages(enabled_only=False):
        page_id = page.id
        page_dir = store.page_dir(page_id)
        if not page_dir.is_dir():
            continue

        try:
            manifest = store.get_page(page_id).manifest
        except Exception as exc:
            log.warning("webui_pages.bootstrap.skip_invalid_manifest", {"pageId": page_id, "error": str(exc)})
            continue
        if not manifest.enabled:
            continue

        try:
            if _should_rebuild_page(store, page_id):
                meta = builder.build(page_id)
                if meta.status != "ready":
                    log.warning(
                        "webui_pages.bootstrap.rebuild_failed",
                        {"pageId": page_id, "error": meta.error or "build failed"},
                    )
        except Exception as exc:
            log.warning("webui_pages.bootstrap.rebuild_error", {"pageId": page_id, "error": str(exc)})

        try:
            if store.routes_path(page_id).is_file():
                # Warm up page API runtime so restart/upgrade immediately serves APIs.
                await runtime.reload_page(page_id)
        except Exception as exc:
            log.warning("webui_pages.bootstrap.api_preload_failed", {"pageId": page_id, "error": str(exc)})


def _should_rebuild_page(store: WebUIPagesStore, page_id: str) -> bool:
    bundle_path = store.bundle_path(page_id)
    build_meta = store.read_build_meta(page_id)
    if not bundle_path.is_file():
        return True
    if build_meta.status != "ready":
        return True
    if build_meta.runtime != RUNTIME_NAME or build_meta.runtimeVersion != RUNTIME_VERSION:
        return True
    if build_meta.sdkImport != SDK_IMPORT_NAME:
        return True
    return _sources_newer_than_bundle(store.page_dir(page_id), bundle_path)


def _sources_newer_than_bundle(page_dir: Path, bundle_path: Path) -> bool:
    bundle_mtime = bundle_path.stat().st_mtime_ns
    for path in (page_dir / "src").rglob("*"):
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        if path.stat().st_mtime_ns > bundle_mtime:
            return True
    return False
