"""
Shared extension registration semantics for OSS and Flocks Pro integrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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
    "register_license_checker",
    "register_lifecycle_hook",
    "register_event_hook",
]
