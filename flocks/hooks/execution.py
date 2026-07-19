"""Generic lifecycle adapter for extension-provided execution controls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from flocks.hooks.pipeline import HookContext, HookPipeline
from flocks.identity import Subject, reset_current_subject, set_current_subject


T = TypeVar("T")
StageRunner = Callable[[dict[str, Any]], Awaitable[HookContext]]
SubjectSink = Callable[[Subject], None]


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
) -> T:
    """Run an operation between generic before/after lifecycle stages."""
    from flocks.plugin import PluginLoader

    if PluginLoader.has_runtime_critical_entrypoint_failure():
        stopped = ExecutionStopped("critical plugin entrypoint failure")
        after_ctx = await after({**payload, "outcome": "stopped", "error": stopped})
        raise_if_execution_stopped(after_ctx)
        raise stopped

    before_ctx = await before(payload)
    stopped = execution_stop_error(before_ctx)
    if stopped is not None:
        after_ctx = await after({**payload, "outcome": "stopped", "error": stopped})
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
        after_ctx = await after({**payload, "outcome": "error", "error": exc})
        raise_if_execution_stopped(after_ctx)
        raise
    except BaseException:
        if subject_token is not None:
            reset_current_subject(subject_token)
        raise

    if subject_token is not None:
        reset_current_subject(subject_token)
    after_ctx = await after({**payload, "outcome": "success", "result": result})
    raise_if_execution_stopped(after_ctx)
    return result
