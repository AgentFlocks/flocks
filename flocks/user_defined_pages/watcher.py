"""Watch ~/.flocks/plugins/user_defined_pages for changes and trigger rebuilds."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from flocks.user_defined_pages.api_runtime import UserDefinedPageApiRuntime
from flocks.user_defined_pages.builder import UserDefinedPagesBuilder
from flocks.user_defined_pages.store import UserDefinedPagesStore
from flocks.server.routes.event import publish_event
from flocks.utils.log import Log

log = Log.create(service="user-defined-pages-watcher")

_DEBOUNCE_SECONDS = 0.8
_RELOAD_EVENT_TYPES = frozenset({"modified", "created", "deleted", "moved"})

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the FastAPI event loop for cross-thread SSE publishing."""
    global _main_loop
    _main_loop = loop


def _publish_event_sync(event_type: str, properties: dict) -> None:
    if _main_loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            publish_event(event_type, properties),
            _main_loop,
        )
    except Exception as exc:
        log.warning("user_defined_pages.event.publish_failed", {"type": event_type, "error": str(exc)})


def _run_on_main_loop_sync(coro: Coroutine[Any, Any, Any], *, timeout_seconds: float = 5.0) -> Any:
    if _main_loop is None:
        raise RuntimeError("main event loop is not ready")
    future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError("main loop task timed out") from exc


@dataclass
class _PendingAction:
    manifest_changed: bool = False
    source_changed: bool = False
    api_changed: bool = False
    page_removed: bool = False


class UserDefinedPagesWatcher:
    """Debounced filesystem watcher for user-defined pages."""

    def __init__(
        self,
        *,
        store: Optional[UserDefinedPagesStore] = None,
        builder: Optional[UserDefinedPagesBuilder] = None,
        api_runtime: Optional[UserDefinedPageApiRuntime] = None,
        on_build_complete: Optional[Callable[[str, bool, Optional[str]], None]] = None,
    ) -> None:
        self._store = store or UserDefinedPagesStore()
        self._builder = builder or UserDefinedPagesBuilder(self._store)
        self._api_runtime = api_runtime or UserDefinedPageApiRuntime(self._store)
        self._on_build_complete = on_build_complete
        self._observer: Optional[object] = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._pending_pages: dict[str, _PendingAction] = {}

    def start(self) -> None:
        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warning(
                "user_defined_pages.watcher.watchdog_missing",
                {"msg": "watchdog not installed, user defined pages watcher disabled"},
            )
            return

        root = self._store.ensure_root()
        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if getattr(event, "event_type", "") not in _RELOAD_EVENT_TYPES:
                    return
                src = Path(getattr(event, "src_path", ""))
                event_type = getattr(event, "event_type", "")
                action = watcher._classify_event(src, root, event_type=event_type, is_directory=event.is_directory)
                if action is None:
                    return
                page_id, pending = action
                watcher._schedule(page_id, pending)

        handler = _Handler()
        observer = Observer()
        observer.schedule(handler, str(root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("user_defined_pages.watcher.started", {"directory": str(root)})

    def stop(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            self._pending_pages.clear()
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[union-attr]
                self._observer.join(timeout=2)  # type: ignore[union-attr]
            except Exception:
                pass
            self._observer = None
            log.info("user_defined_pages.watcher.stopped")

    def _classify_event(
        self,
        src: Path,
        root: Path,
        *,
        event_type: str,
        is_directory: bool,
    ) -> Optional[tuple[str, _PendingAction]]:
        try:
            rel = src.resolve().relative_to(root.resolve())
        except Exception:
            return None
        if not rel.parts:
            return None
        page_id = rel.parts[0]
        if is_directory and len(rel.parts) == 1 and event_type == "deleted":
            return page_id, _PendingAction(page_removed=True)
        if len(rel.parts) < 2:
            return None
        rel_str = str(Path(*rel.parts[1:])).replace("\\", "/")
        if rel_str == "manifest.json":
            return page_id, _PendingAction(manifest_changed=True)
        if rel_str.startswith("src/") and rel.suffix in {".ts", ".tsx", ".js", ".jsx", ".css"}:
            return page_id, _PendingAction(source_changed=True)
        if rel_str == "api/routes.yaml" or (rel_str.startswith("api/") and rel.suffix == ".py"):
            return page_id, _PendingAction(api_changed=True)
        return None

    def _schedule(self, page_id: str, update: _PendingAction) -> None:
        with self._lock:
            pending = self._pending_pages.get(page_id, _PendingAction())
            pending.manifest_changed = pending.manifest_changed or update.manifest_changed
            pending.source_changed = pending.source_changed or update.source_changed
            pending.api_changed = pending.api_changed or update.api_changed
            pending.page_removed = pending.page_removed or update.page_removed
            self._pending_pages[page_id] = pending
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(_DEBOUNCE_SECONDS, self._run_pending_builds)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _run_pending_builds(self) -> None:
        with self._lock:
            pages = dict(self._pending_pages)
            self._pending_pages.clear()

        for page_id, pending in pages.items():
            if pending.page_removed:
                self._api_runtime.clear_page(page_id)
                _publish_event_sync("user_defined_pages.nav_changed", {"id": page_id})
                continue

            if pending.source_changed:
                try:
                    meta = self._builder.build(page_id)
                    if meta.status == "ready":
                        _publish_event_sync("user_defined_pages.updated", {"id": page_id, "hash": meta.hash})
                        _publish_event_sync("user_defined_pages.nav_changed", {"id": page_id})
                    else:
                        _publish_event_sync(
                            "user_defined_pages.build_failed",
                            {"id": page_id, "error": meta.error or "build failed"},
                        )
                    if self._on_build_complete:
                        self._on_build_complete(page_id, meta.status == "ready", meta.error)
                except Exception as exc:
                    _publish_event_sync(
                        "user_defined_pages.build_failed",
                        {"id": page_id, "error": str(exc)},
                    )
                    log.warning("user_defined_pages.watcher.build_failed", {"pageId": page_id, "error": str(exc)})

            if pending.api_changed:
                try:
                    routes = _run_on_main_loop_sync(self._api_runtime.reload_page(page_id))
                    _publish_event_sync("user_defined_pages.api_changed", {"id": page_id, "routes": routes})
                except Exception as exc:
                    _publish_event_sync("user_defined_pages.api_failed", {"id": page_id, "error": str(exc)})
                    log.warning("user_defined_pages.watcher.api_reload_failed", {"pageId": page_id, "error": str(exc)})

            if pending.manifest_changed and not pending.source_changed:
                _publish_event_sync("user_defined_pages.nav_changed", {"id": page_id})


_watcher: Optional[UserDefinedPagesWatcher] = None


def get_watcher() -> UserDefinedPagesWatcher:
    global _watcher
    if _watcher is None:
        _watcher = UserDefinedPagesWatcher()
    return _watcher


def start_watcher() -> None:
    get_watcher().start()


def stop_watcher() -> None:
    global _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None
