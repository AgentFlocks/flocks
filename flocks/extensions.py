"""
Shared extension registration semantics for OSS and Flocks Pro integrations.
"""

from __future__ import annotations

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


_shutdown_hooks: list[tuple[str, Callable[[], Any], float]] = []


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

    threading.Thread(target=run, name="flocks-shutdown-hook", daemon=True).start()
    return future


async def _call_shutdown_hook(hook: Callable[[], Any]) -> None:
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


async def run_shutdown_hooks() -> None:
    """Called by the server lifespan. Each hook runs as its own task with a hard
    time limit, so a failing, slow or self-cancelling hook never stops the hooks
    or the shutdown steps after it. Cancelling the shutdown itself still
    propagates (and cancels the running hook)."""
    import asyncio

    from flocks.utils.log import Log

    log = Log.create(service="extensions")
    for name, hook, timeout_seconds in list(_shutdown_hooks):
        task = asyncio.ensure_future(_call_shutdown_hook(hook))
        try:
            done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_retrieve_late_outcome)
            raise
        if not done:
            # Not awaited after cancelling: a hook that ignores cancellation
            # must not hold up the rest of the shutdown.
            task.cancel()
            task.add_done_callback(_retrieve_late_outcome)
            log.warning("extensions.shutdown_hook.timeout", {"name": name, "timeout_seconds": timeout_seconds})
        elif task.cancelled():
            # The hook cancelled something of its own and let that escape.
            log.warning("extensions.shutdown_hook.cancelled", {"name": name})
        elif task.exception() is not None:
            log.warning("extensions.shutdown_hook.failed", {"name": name, "error": str(task.exception())})


__all__ = [
    "ExtensionOptions",
    "FailPolicy",
    "ensure_callable_methods",
    "handler_name",
    "normalize_fail_policy",
    "normalize_timeout",
    "register_audit_sink",
    "register_auth_backend",
    "register_http_hook",
    "register_hub_root",
    "register_license_checker",
    "register_lifecycle_hook",
    "register_event_hook",
    "register_public_path",
    "register_shutdown_hook",
    "run_shutdown_hooks",
    "unregister_hub_root",
]
