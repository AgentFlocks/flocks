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
ContextSink = Callable[[dict[str, Any]], None]


@dataclass(eq=False, slots=True)
class ExecutionLifecycleScope:
    """Opaque, generic correlation scope for one nested execution lifetime.

    The scope carries no authorization semantics.  It lets extensions pair
    their own setup and cleanup across nested generic lifecycle adapters
    without relying on hook output that another extension may replace.
    """

    parent: ExecutionLifecycleScope | None
    cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)
    closed: bool = False


_current_execution_lifecycle_scope: ContextVar[ExecutionLifecycleScope | None] = (
    ContextVar("flocks_execution_lifecycle_scope", default=None)
)

# Extension-provided context is a neutral, opaque carrier.  Flocks never
# assigns policy meaning to its keys or values; it only keeps the paired
# before-stage context available while an effect creates child work.
_current_execution_context: ContextVar[dict[str, Any]] = ContextVar(
    "flocks_execution_context", default={}
)


def current_execution_lifecycle_scope() -> ExecutionLifecycleScope | None:
    """Return the opaque scope for the current generic execution, if any."""
    return _current_execution_lifecycle_scope.get()


def current_execution_context() -> dict[str, Any]:
    """Return a copy of the current opaque execution context carrier."""
    return dict(_current_execution_context.get())


@contextmanager
def execution_context_scope(
    context: Mapping[str, Any] | None,
    *,
    inherit: bool = True,
):
    """Temporarily expose extension-owned context as an opaque carrier.

    This adapter intentionally neither validates nor interprets the mapping.
    It allows an ingress hook to associate opaque state with child work after
    the authentication effect itself has completed.  Set ``inherit=False`` at
    an ownership boundary so one request's opaque context cannot authorize
    unrelated queued work.
    """
    inherited = current_execution_context() if inherit else {}
    supplied = dict(context) if isinstance(context, Mapping) else {}
    token = _current_execution_context.set({**inherited, **supplied})
    try:
        yield
    finally:
        _current_execution_context.reset(token)


def is_execution_lifecycle_scope_active(scope: object) -> bool:
    """Whether an opaque scope is an ancestor of the current execution."""
    if not isinstance(scope, ExecutionLifecycleScope):
        return False
    current = current_execution_lifecycle_scope()
    matched = False
    while current is not None:
        if current.closed:
            return False
        if current is scope:
            matched = True
        current = current.parent
    return matched


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
        # Tasks spawned inside this scope inherit its ContextVar value.  The
        # shared closed flag makes an inherited scope inert once its owner has
        # exited, even though that child task holds an older Context snapshot.
        scope.closed = True
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
    if ctx.execution_stop_requested:
        return ExecutionStopped(
            ctx.execution_stop_detail or "operation stopped by extension"
        )
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


def _terminal_outcome(
    status: str,
    *,
    executed: bool,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Return neutral terminal facts without exposing an effect result body."""
    outcome: dict[str, Any] = {
        "status": status,
        "success": status == "success",
        "executed": executed,
    }
    if error is not None:
        outcome["error_type"] = type(error).__name__
    return outcome


async def execute_with_hooks(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    before: StageRunner | None = None,
    after: StageRunner | None = None,
    subject_sink: SubjectSink | None = None,
    context_sink: ContextSink | None = None,
    reuse_execution_scope: bool = False,
) -> T:
    """Run an operation between generic before/after lifecycle stages."""
    if before is None:
        before = HookPipeline.run_action_before
    if after is None:
        after = HookPipeline.run_action_after
    with execution_lifecycle_scope(reuse_current=reuse_execution_scope):
        return await _execute_with_hooks_in_scope(
            payload,
            effect,
            before=before,
            after=after,
            subject_sink=subject_sink,
            context_sink=context_sink,
        )


async def _execute_with_hooks_in_scope(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    before: StageRunner,
    after: StageRunner,
    subject_sink: SubjectSink | None,
    context_sink: ContextSink | None,
) -> T:
    """Execute one lifecycle operation while its neutral scope is active."""
    from flocks.plugin import PluginLoader

    if PluginLoader.has_runtime_critical_entrypoint_failure():
        stopped = ExecutionStopped("critical plugin entrypoint failure")
        after_ctx = await after({
            **payload,
            "outcome": "stopped",
            "terminal_outcome": _terminal_outcome(
                "stopped", executed=False, error=stopped
            ),
            "error": stopped,
        })
        raise_if_execution_stopped(after_ctx)
        raise stopped

    try:
        before_ctx = await before(payload)
    except BaseException as exc:
        after_ctx = await after({
            **payload,
            "outcome": "error",
            "terminal_outcome": _terminal_outcome(
                "error", executed=False, error=exc
            ),
            "error": exc,
        })
        raise_if_execution_stopped(after_ctx)
        raise
    stopped = execution_stop_error(before_ctx)
    before_context = before_ctx.output.get("context")
    inherited_context = current_execution_context()
    effective_context = (
        {**inherited_context, **before_context}
        if isinstance(before_context, Mapping)
        else inherited_context
    )

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
            _after_payload({
                **payload,
                "outcome": "stopped",
                "terminal_outcome": _terminal_outcome(
                    "stopped", executed=False, error=stopped
                ),
                "error": stopped,
            })
        )
        raise_if_execution_stopped(after_ctx)
        raise stopped

    subject = subject_from_hook_context(before_ctx)
    if subject is not None and subject_sink is not None:
        subject_sink(subject)
    subject_token = set_current_subject(subject) if subject is not None else None
    context_token = _current_execution_context.set(effective_context)
    try:
        result = await effect()
    except Exception as exc:
        if subject_token is not None:
            reset_current_subject(subject_token)
        _current_execution_context.reset(context_token)
        after_ctx = await after(
            _after_payload({
                **payload,
                "outcome": "error",
                "terminal_outcome": _terminal_outcome(
                    "error", executed=True, error=exc
                ),
                "error": exc,
            })
        )
        raise_if_execution_stopped(after_ctx)
        raise
    except BaseException as exc:
        if subject_token is not None:
            reset_current_subject(subject_token)
        _current_execution_context.reset(context_token)
        after_ctx = await after(
            _after_payload({
                **payload,
                "outcome": "error",
                "terminal_outcome": _terminal_outcome(
                    "error", executed=True, error=exc
                ),
                "error": exc,
            })
        )
        raise_if_execution_stopped(after_ctx)
        raise

    if subject_token is not None:
        reset_current_subject(subject_token)
    _current_execution_context.reset(context_token)
    after_ctx = await after(
        _after_payload({
            **payload,
            "outcome": "success",
            "terminal_outcome": _terminal_outcome("success", executed=True),
            "result": result,
        })
    )
    raise_if_execution_stopped(after_ctx)
    after_subject = subject_from_hook_context(after_ctx)
    if after_subject is not None and subject_sink is not None:
        subject_sink(after_subject)
    after_context = after_ctx.output.get("context")
    if isinstance(after_context, Mapping) and context_sink is not None:
        context_sink(dict(after_context))
    return result
