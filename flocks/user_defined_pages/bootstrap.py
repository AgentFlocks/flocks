"""Startup reconciliation for user-defined pages."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flocks.user_defined_pages.api_runtime import UserDefinedPageApiRuntime
from flocks.user_defined_pages.builder import RUNTIME_NAME, RUNTIME_VERSION, UserDefinedPagesBuilder
from flocks.user_defined_pages.store import UserDefinedPagesStore
from flocks.utils.log import Log

log = Log.create(service="user-defined-pages-bootstrap")

_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}


async def reconcile_user_defined_pages(
    *,
    store: Optional[UserDefinedPagesStore] = None,
    builder: Optional[UserDefinedPagesBuilder] = None,
    runtime: Optional[UserDefinedPageApiRuntime] = None,
) -> None:
    store = store or UserDefinedPagesStore()
    builder = builder or UserDefinedPagesBuilder(store)
    runtime = runtime or UserDefinedPageApiRuntime(store)
    store.ensure_root()

    for page in store.list_pages(enabled_only=False):
        page_id = page.id
        page_dir = store.page_dir(page_id)
        if not page_dir.is_dir():
            continue

        try:
            manifest = store.get_page(page_id).manifest
        except Exception as exc:
            log.warning("user_defined_pages.bootstrap.skip_invalid_manifest", {"pageId": page_id, "error": str(exc)})
            continue
        if not manifest.enabled:
            continue

        try:
            if _should_rebuild_page(store, page_id):
                meta = builder.build(page_id)
                if meta.status != "ready":
                    log.warning(
                        "user_defined_pages.bootstrap.rebuild_failed",
                        {"pageId": page_id, "error": meta.error or "build failed"},
                    )
        except Exception as exc:
            log.warning("user_defined_pages.bootstrap.rebuild_error", {"pageId": page_id, "error": str(exc)})

        try:
            if store.routes_path(page_id).is_file():
                # Warm up page API runtime so restart/upgrade immediately serves APIs.
                await runtime.reload_page(page_id)
        except Exception as exc:
            log.warning("user_defined_pages.bootstrap.api_preload_failed", {"pageId": page_id, "error": str(exc)})


def _should_rebuild_page(store: UserDefinedPagesStore, page_id: str) -> bool:
    bundle_path = store.bundle_path(page_id)
    build_meta = store.read_build_meta(page_id)
    if not bundle_path.is_file():
        return True
    if build_meta.status == "failed":
        return True
    if build_meta.runtime != RUNTIME_NAME or build_meta.runtimeVersion != RUNTIME_VERSION:
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
