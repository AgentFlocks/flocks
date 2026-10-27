"""
Shared registration semantics for every Flocks extension point.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


class FailPolicy(str, Enum):
    """How an extension failure should affect the caller."""

    ISOLATE = "isolate"
    PROPAGATE = "propagate"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class ExtensionOptions:
    """Common registration fields shared by hook-like extension points."""

    name: str
    priority: int = 100
    timeout_seconds: Optional[float] = None
    fail_policy: FailPolicy = FailPolicy.ISOLATE

    @property
    def critical(self) -> bool:
        return self.fail_policy in {FailPolicy.PROPAGATE, FailPolicy.FAIL_CLOSED}


def normalize_fail_policy(
    fail_policy: FailPolicy | str | None = None,
    *,
    critical: bool = False,
) -> FailPolicy:
    """Normalize failure policy; ``critical`` is a compatibility alias."""

    if fail_policy is None:
        return FailPolicy.FAIL_CLOSED if critical else FailPolicy.ISOLATE
    try:
        return FailPolicy(fail_policy)
    except ValueError as exc:
        raise ValueError(f"无效失败策略: {fail_policy}") from exc


def normalize_timeout(timeout_seconds: Optional[float]) -> Optional[float]:
    if timeout_seconds is None:
        return None
    timeout = float(timeout_seconds)
    if timeout <= 0:
        return None
    return timeout


def handler_name(handler: Callable[..., Any], explicit_name: Optional[str] = None) -> str:
    if explicit_name:
        return explicit_name
    module = getattr(handler, "__module__", "")
    qualname = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return str(handler)


def ensure_callable_methods(target: Any, method_names: Iterable[str], *, label: str) -> None:
    """Conservative contract check used at extension registration time."""

    missing = [name for name in method_names if not callable(getattr(target, name, None))]
    if missing:
        target_name = getattr(target, "__name__", str(target))
        raise ValueError(f"{label} 接口不完整: {target_name} 缺少 {', '.join(missing)}")


def register_auth_backend(backend: Any) -> None:
    from flocks.auth import register_backend

    register_backend(backend)


def register_license_checker(checker: Any) -> None:
    from flocks.license import register_checker

    register_checker(checker)


def register_audit_sink(sink: Any) -> None:
    from flocks.audit import register_sink

    register_sink(sink)


def register_project_deletion_guard(name: str, guard: Any) -> None:
    from flocks.project.lifecycle import register_project_deletion_guard as register

    register(name, guard)


def register_hub_root(name: str, root: Path) -> None:
    """Add an extension hub root whose entries join (and override) the catalog."""
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.hub.paths import register_hub_root as _register

    _register(name, root)
    clear_catalog_caches()


def unregister_hub_root(name: str) -> bool:
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.hub.paths import unregister_hub_root as _unregister

    removed = _unregister(name)
    if removed:
        clear_catalog_caches()
    return removed


def register_http_hook(
    hook: Callable[..., Any],
    *,
    name: Optional[str] = None,
    priority: int = 100,
    timeout_seconds: Optional[float] = None,
    fail_policy: FailPolicy | str | None = None,
    critical: bool = False,
) -> None:
    from flocks.server.app import register_http_middleware

    register_http_middleware(
        hook,
        name=name,
        priority=priority,
        timeout_seconds=timeout_seconds,
        fail_policy=fail_policy,
        critical=critical,
    )


def register_lifecycle_hook(
    name: str,
    hook: Any,
    *,
    priority: int = 0,
    timeout_seconds: Optional[float] = None,
    fail_policy: FailPolicy | str | None = None,
    critical: bool = False,
) -> None:
    from flocks.hooks.pipeline import HookPipeline

    HookPipeline.register(
        name,
        hook,
        order=priority,
        timeout_seconds=timeout_seconds,
        fail_policy=fail_policy,
        critical=critical,
    )


def register_content_source(
    source: Any,
    *,
    priority: int = 100,
    exclusive_kinds: Optional[Iterable[str]] = None,
) -> None:
    """Register a source for Agent/Skill/Workflow/tool content.

    Lower ``priority`` is consulted first.  Naming a kind in *exclusive_kinds*
    stops lower-priority sources from serving that kind at all, which is how a
    deployment keeps local files from shadowing content it manages centrally.
    """

    from flocks.content import register_source

    register_source(source, priority=priority, exclusive_kinds=exclusive_kinds)


def register_hub_package_provider(provider: Any) -> None:
    """Register a provider of hub plugins of an extension's own.

    The catalog lists the manifests the provider gives; an install, update or
    update preview draws the plugin's files from the provider, which stages
    them for that operation only.  See :mod:`flocks.hub.providers` for the
    interface a provider implements.
    """

    from flocks.hub.catalog import clear_catalog_caches
    from flocks.hub.providers import register_provider

    register_provider(provider)
    clear_catalog_caches()


def unregister_hub_package_provider(provider: Any) -> None:
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.hub.providers import unregister_provider

    unregister_provider(provider)
    clear_catalog_caches()


def register_hub_access_policy(policy: Any) -> None:
    """Register a policy over hub content and the pages it ships.

    The policy may refuse installs, add what it knows about each scene suite
    to the suite listing, refuse to serve pages, and narrow the page and
    workspace listings.  See :mod:`flocks.hub.access` for the methods (all
    optional) a policy implements.
    """
    from flocks.hub.access import register_policy

    register_policy(policy)


def unregister_hub_access_policy(policy: Any) -> None:
    from flocks.hub.access import unregister_policy

    unregister_policy(policy)


def register_page_code_policy(policy: Any) -> None:
    """Set the policy over WebUI page plugins' server code.

    The policy may refuse to run a page's code (asked before any of it is
    imported, with the digest of exactly the code that would run) and picks
    which of the secrets the page declares a call may read.  Without one,
    every page's handlers run and read what they declare.  See
    :mod:`flocks.contracts.webui.api_code` for the interface.
    """
    from flocks.contracts.webui.api_code import register_page_code_policy as _register

    _register(policy)


def unregister_page_code_policy(policy: Any) -> None:
    from flocks.contracts.webui.api_code import unregister_page_code_policy as _unregister

    _unregister(policy)


def register_response_transform(
    transform: Any,
    *,
    max_pending_chars: Optional[int] = None,
) -> None:
    """Register the transform applied to model responses before delivery.

    Installed transforms see the response ahead of the UI, of persistence and
    of any tool that consumes it, which is what makes them usable for
    processing that has to be in place before anything downstream reads the
    text.  Only the processed result reaches the caller.
    """

    from flocks.transform import register_response_transform as _register

    _register(transform, max_pending_chars=max_pending_chars)


def register_route_provider(
    router: Any,
    *,
    name: str,
    prefix: str = "",
    tags: Optional[Iterable[str]] = None,
) -> None:
    """Register an extension's HTTP routes.

    Order-independent: routers registered before the application finishes
    building are installed once it has, and later ones are installed on the
    spot.
    """

    from flocks.server.route_extensions import register_route_provider as _register

    _register(router, name=name, prefix=prefix, tags=list(tags) if tags else None)


def register_branding_provider(provider: Any) -> None:
    """Register the source of the product name shown in the interface.

    The default name lives in the open product.  A deployment that ships its
    own branding registers a provider rather than the core asking who is
    installed, which is the kind of question that puts a specific product's
    name into open source.

    *provider* exposes ``display_name() -> str | None``; returning ``None``
    keeps the default.
    """

    from flocks.server.branding import register_branding_provider as _register

    _register(provider)


def register_event_hook(
    event_key: str,
    handler: Callable[..., Any],
    *,
    name: Optional[str] = None,
    priority: int = 100,
    timeout_seconds: Optional[float] = None,
    fail_policy: FailPolicy | str | None = None,
    critical: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    from flocks.hooks.registry import register_hook

    hook_metadata = dict(metadata or {})
    hook_metadata.update({
        "name": name or handler_name(handler),
        "priority": priority,
        "timeout_seconds": timeout_seconds,
        "fail_policy": normalize_fail_policy(fail_policy, critical=critical).value,
    })
    register_hook(event_key, handler, hook_metadata)


def register_public_path(pattern: Any) -> None:
    """Let an extension serve an endpoint that authenticates its own requests.

    Paths that ``pattern`` matches in full skip the session / API-token guard,
    so the handler must verify every request itself (signed webhooks).
    """
    from flocks.server.auth import register_public_path_pattern

    register_public_path_pattern(pattern)


def register_http_private_path_provider(name: str, provider: Callable[[Path], bool]) -> None:
    """Deny an extension's private files before HTTP allow-path checks.

    A synchronous predicate receives a resolved absolute Path. Return True
    to deny; errors or a non-boolean result also deny. This applies to direct
    reads, downloads and search/list results, without changing internal tools.
    """
    from flocks.utils.http_file_read_guard import register_http_private_path_provider as _register

    _register(name, provider)


def unregister_http_private_path_provider(name: str) -> bool:
    from flocks.utils.http_file_read_guard import unregister_http_private_path_provider as _unregister

    return _unregister(name)


_serving = False


def mark_serving(active: bool = True) -> None:
    """Called by the server: true as its lifespan starts, before any extension loads; false once it ends."""
    global _serving
    _serving = bool(active)


def serving() -> bool:
    """Whether this process is serving now (not a CLI command that loaded the same extensions)."""
    return _serving


_error_responses: dict[type, Callable[[Any, BaseException], Any]] = {}


def register_error_response(exc_type: type, handler: Callable[[Any, BaseException], Any]) -> None:
    """Answer ``exc_type`` with ``handler(request, exc)``'s response instead of a bare 500.

    Asked for an error a route did not handle, raised directly or behind
    another one (``raise ... from``), so a store wrapping a driver error still
    gets the extension's answer. The handler may be a coroutine; returning
    None leaves the 500. One handler per type; registering again replaces it.
    """
    _error_responses[exc_type] = handler


def unregister_error_response(exc_type: type) -> None:
    _error_responses.pop(exc_type, None)


#: Errors already put to the handlers: the inner middleware asks first and the
#: catch-all may see the same error again; a handler is asked once per error.
_asked: "weakref.WeakSet[BaseException]" = weakref.WeakSet()


async def error_response(request: Any, exc: BaseException) -> Any:
    """An extension's response for ``exc``, or None when none answers it (asked once per error)."""
    import inspect

    try:
        if exc in _asked:
            return None
        _asked.add(exc)
    except TypeError:  # an error that cannot be weakly referenced is simply asked again
        pass
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        for exc_type, handler in list(_error_responses.items()):
            if isinstance(current, exc_type):
                result = handler(request, current)
                return await result if inspect.isawaitable(result) else result
        current = current.__cause__ or current.__context__
    return None


_startup_hooks: list[tuple[str, Callable[[], Any], float]] = []
_shutdown_hooks: list[tuple[str, Callable[[], Any], float]] = []


def register_startup_hook(
    name: str,
    hook: Callable[[], Any],
    *,
    timeout_seconds: float = 30.0,
) -> None:
    """Run ``hook`` once the server has started, to start an extension's
    background service (e.g. scheduled audits) on the server's event
    loop. Re-registering a name replaces it.

    The hooks run in the background after storage, auth and the installed
    plugins are ready, so a slow hook never delays serving requests. They are
    called the same way as shutdown hooks and should return once their service
    is running. A hook registered after the server started runs at its next start.

    Prefer a coroutine function: if the server stops while it runs, it is
    cancelled and given up to its timeout to clean up before the shutdown hooks
    run. A plain callable runs on a daemon thread that nothing can stop.
    """
    _startup_hooks[:] = [entry for entry in _startup_hooks if entry[0] != name]
    _startup_hooks.append((name, hook, timeout_seconds))


def register_shutdown_hook(
    name: str,
    hook: Callable[[], Any],
    *,
    timeout_seconds: float = 30.0,
) -> None:
    """Run ``hook`` when the server shuts down, before its sessions, MCP
    connections and storage close. Re-registering a name replaces it.

    Coroutine functions run on the event loop. Other callables run on a daemon
    thread, and an awaitable they return is then awaited on the loop; the
    timeout covers both. A hook still running at its timeout is cancelled and
    shutdown moves on without waiting for it.
    """
    _shutdown_hooks[:] = [entry for entry in _shutdown_hooks if entry[0] != name]
    _shutdown_hooks.append((name, hook, timeout_seconds))


def _discard(result: Any) -> None:
    # A coroutine nobody will await: close it instead of leaking a warning.
    import inspect

    if inspect.iscoroutine(result):
        result.close()


def _run_on_daemon_thread(func: Callable[[], Any]) -> Any:
    """Run ``func`` off the loop. A daemon thread rather than the default
    executor: a stuck hook must not hold up interpreter exit either."""
    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(result: Any, error: BaseException | None) -> None:
        if future.done():  # timed out meanwhile
            _discard(result)
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def run() -> None:
        try:
            outcome: tuple[Any, BaseException | None] = (func(), None)
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting task
            outcome = (None, exc)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:  # the loop closed after a timeout
            _discard(outcome[0])

    threading.Thread(target=run, name="flocks-extension-hook", daemon=True).start()
    return future


async def _call_hook(hook: Callable[[], Any]) -> None:
    import inspect

    if inspect.iscoroutinefunction(hook):
        await hook()
        return
    result = await _run_on_daemon_thread(hook)
    if inspect.isawaitable(result):
        await result


def _retrieve_late_outcome(task: Any) -> None:
    # Read the outcome of a hook nobody waits for any more, so a late failure
    # does not surface as "Task exception was never retrieved".
    if not task.cancelled():
        task.exception()


async def _run_hooks(
    hooks: list[tuple[str, Callable[[], Any], float]],
    stage: str,
    *,
    settle_on_cancel: bool = False,
) -> None:
    """Each hook runs as its own task with a hard time limit, so a failing,
    slow or self-cancelling hook never stops the hooks or the steps after it.
    Cancelling the caller itself still propagates (and cancels the running hook);
    with ``settle_on_cancel`` it first waits, within the hook's time limit, for
    the hook to finish cleaning up."""
    import asyncio

    from flocks.utils.log import Log

    log = Log.create(service="extensions")
    for name, hook, timeout_seconds in list(hooks):
        task = asyncio.ensure_future(_call_hook(hook))
        try:
            done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            task.cancel()
            # Before the wait below: a second cancellation must not leave its outcome unread.
            task.add_done_callback(_retrieve_late_outcome)
            if settle_on_cancel:
                await asyncio.wait({task}, timeout=timeout_seconds)
            raise
        if not done:
            # Not awaited after cancelling: a hook that ignores cancellation
            # must not hold up the hooks and steps after it.
            task.cancel()
            task.add_done_callback(_retrieve_late_outcome)
            log.warning(f"extensions.{stage}_hook.timeout", {"name": name, "timeout_seconds": timeout_seconds})
        elif task.cancelled():
            # The hook cancelled something of its own and let that escape.
            log.warning(f"extensions.{stage}_hook.cancelled", {"name": name})
        elif task.exception() is not None:
            log.warning(f"extensions.{stage}_hook.failed", {"name": name, "error": str(task.exception())})


async def run_startup_hooks() -> None:
    """Called by the server lifespan, in the background, once startup is ready
    (and by the standalone workflow service once its plugins and MCP are up).

    The lifespan cancels it when the server stops early. A hook still starting
    its service then gets up to its timeout to stop before the shutdown hooks
    run; cleanup that takes longer, or a second cancellation, can still overlap
    them."""
    await _run_hooks(_startup_hooks, "startup", settle_on_cancel=True)


async def run_shutdown_hooks() -> None:
    """Called by the server lifespan before sessions, MCP and storage close."""
    await _run_hooks(_shutdown_hooks, "shutdown")


__all__ = [
    "ExtensionOptions",
    "FailPolicy",
    "ensure_callable_methods",
    "handler_name",
    "normalize_fail_policy",
    "normalize_timeout",
    "register_audit_sink",
    "register_project_deletion_guard",
    "register_auth_backend",
    "register_branding_provider",
    "register_content_source",
    "register_error_response",
    "register_http_hook",
    "register_hub_access_policy",
    "register_hub_root",
    "register_license_checker",
    "register_response_transform",
    "register_route_provider",
    "register_hub_package_provider",
    "unregister_hub_package_provider",
    "register_lifecycle_hook",
    "register_page_code_policy",
    "unregister_page_code_policy",
    "register_event_hook",
    "register_public_path",
    "register_http_private_path_provider",
    "unregister_http_private_path_provider",
    "register_shutdown_hook",
    "register_startup_hook",
    "mark_serving",
    "serving",
    "run_shutdown_hooks",
    "run_startup_hooks",
    "error_response",
    "unregister_error_response",
    "unregister_hub_access_policy",
    "unregister_hub_root",
]
