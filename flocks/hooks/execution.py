"""Generic lifecycle adapter for extension-provided execution controls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

from flocks.hooks.pipeline import HookContext, HookPipeline
from flocks.identity import Subject, reset_current_subject, set_current_subject


T = TypeVar("T")
StageRunner = Callable[[dict[str, Any]], Awaitable[HookContext]]
SubjectSink = Callable[[Subject], None]


@dataclass(eq=False, slots=True)
class ExecutionLifecycleScope:
    """Opaque, generic correlation scope for one nested execution lifetime.

    The scope carries no authorization semantics.  It lets extensions pair
    their own setup and cleanup across nested generic lifecycle adapters
    without relying on hook output that another extension may replace.
    """

    parent: ExecutionLifecycleScope | None
    cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)


_current_execution_lifecycle_scope: ContextVar[ExecutionLifecycleScope | None] = (
    ContextVar("flocks_execution_lifecycle_scope", default=None)
)


def current_execution_lifecycle_scope() -> ExecutionLifecycleScope | None:
    """Return the opaque scope for the current generic execution, if any."""
    return _current_execution_lifecycle_scope.get()


def is_execution_lifecycle_scope_active(scope: object) -> bool:
    """Whether an opaque scope is an ancestor of the current execution."""
    if not isinstance(scope, ExecutionLifecycleScope):
        return False
    current = current_execution_lifecycle_scope()
    while current is not None:
        if current is scope:
            return True
        current = current.parent
    return False


def register_execution_lifecycle_cleanup(callback: Callable[[], None]) -> bool:
    """Register generic best-effort cleanup when the current scope exits."""
    scope = current_execution_lifecycle_scope()
    if scope is None:
        return False
    scope.cleanup_callbacks.append(callback)
    return True


@contextmanager
def execution_lifecycle_scope(*, reuse_current: bool = False):
    """Provide a neutral execution scope, optionally reusing an outer scope."""
    existing = current_execution_lifecycle_scope()
    if reuse_current and existing is not None:
        yield existing
        return

    scope = ExecutionLifecycleScope(parent=existing)
    token = _current_execution_lifecycle_scope.set(scope)
    try:
        yield scope
    finally:
        for callback in reversed(scope.cleanup_callbacks):
            try:
                callback()
            except Exception:
                # Generic cleanup is isolated; extension-specific failure
                # handling belongs to the extension that registered it.
                continue
        _current_execution_lifecycle_scope.reset(token)


class ExecutionStopped(RuntimeError):
    """Raised when a lifecycle hook requests that an operation stop."""


def raise_if_execution_stopped(ctx: HookContext) -> None:
    """Apply the one built-in lifecycle control without interpreting hook data."""
    error = execution_stop_error(ctx)
    if error is not None:
        raise error


def execution_stop_error(ctx: HookContext) -> ExecutionStopped | None:
    """Return the generic stop error requested by a lifecycle hook, if any."""
    execution = ctx.output.get("execution")
    if not isinstance(execution, dict) or execution.get("stop") is not True:
        return None
    detail = execution.get("detail")
    message = str(detail) if detail is not None else "operation stopped by extension"
    return ExecutionStopped(message)


def subject_from_hook_context(ctx: HookContext) -> Subject | None:
    """Read opaque context metadata without interpreting it as authorization.

    Any hook can provide this structurally valid value.  Flocks deliberately
    does not assign hook trust or interpret role, tenant, permission, or other
    attributes; the carrier is limited to execution-local observability and
    invocation context.
    """

    context = ctx.output.get("context")
    if not isinstance(context, Mapping):
        return None
    value = context.get("subject")
    if not isinstance(value, Mapping):
        return None
    try:
        return Subject.model_validate(value)
    except Exception:
        return None


async def execute_with_hooks(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    before: StageRunner = HookPipeline.run_action_before,
    after: StageRunner = HookPipeline.run_action_after,
    subject_sink: SubjectSink | None = None,
    reuse_execution_scope: bool = False,
) -> T:
    """Run an operation between generic before/after lifecycle stages."""
    with execution_lifecycle_scope(reuse_current=reuse_execution_scope):
        return await _execute_with_hooks_in_scope(
            payload,
            effect,
            before=before,
            after=after,
            subject_sink=subject_sink,
        )


async def _execute_with_hooks_in_scope(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    before: StageRunner,
    after: StageRunner,
    subject_sink: SubjectSink | None,
) -> T:
    """Execute one lifecycle operation while its neutral scope is active."""
    from flocks.plugin import PluginLoader

    if PluginLoader.has_runtime_critical_entrypoint_failure():
        stopped = ExecutionStopped("critical plugin entrypoint failure")
        after_ctx = await after({**payload, "outcome": "stopped", "error": stopped})
        raise_if_execution_stopped(after_ctx)
        raise stopped

    try:
        before_ctx = await before(payload)
    except BaseException as exc:
        after_ctx = await after({**payload, "outcome": "error", "error": exc})
        raise_if_execution_stopped(after_ctx)
        raise
    stopped = execution_stop_error(before_ctx)
    before_context = before_ctx.output.get("context")

    def _after_payload(data: dict[str, Any]) -> dict[str, Any]:
        """Carry opaque hook context to the paired after lifecycle stage."""
        if not isinstance(before_context, Mapping):
            return data
        existing_context = data.get("context")
        if isinstance(existing_context, Mapping):
            return {
                **data,
                "context": {**existing_context, **before_context},
            }
        return {**data, "context": before_context}

    if stopped is not None:
        after_ctx = await after(
            _after_payload({**payload, "outcome": "stopped", "error": stopped})
        )
        raise_if_execution_stopped(after_ctx)
        raise stopped

    subject = subject_from_hook_context(before_ctx)
    if subject is not None and subject_sink is not None:
        subject_sink(subject)
    subject_token = set_current_subject(subject) if subject is not None else None
    try:
        result = await effect()
    except Exception as exc:
        if subject_token is not None:
            reset_current_subject(subject_token)
        after_ctx = await after(
            _after_payload({**payload, "outcome": "error", "error": exc})
        )
        raise_if_execution_stopped(after_ctx)
        raise
    except BaseException as exc:
        if subject_token is not None:
            reset_current_subject(subject_token)
        after_ctx = await after(
            _after_payload({**payload, "outcome": "error", "error": exc})
        )
        raise_if_execution_stopped(after_ctx)
        raise

    if subject_token is not None:
        reset_current_subject(subject_token)
    after_ctx = await after(
        _after_payload({**payload, "outcome": "success", "result": result})
    )
    raise_if_execution_stopped(after_ctx)
    after_subject = subject_from_hook_context(after_ctx)
    if after_subject is not None and subject_sink is not None:
        subject_sink(after_subject)
    return result
