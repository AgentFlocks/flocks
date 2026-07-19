"""Generic lifecycle adapter for extension-provided execution controls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from flocks.hooks.pipeline import HookContext, HookPipeline


T = TypeVar("T")
StageRunner = Callable[[dict[str, Any]], Awaitable[HookContext]]


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


async def execute_with_hooks(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    before: StageRunner = HookPipeline.run_action_before,
    after: StageRunner = HookPipeline.run_action_after,
) -> T:
    """Run an operation between generic before/after lifecycle stages."""
    before_ctx = await before(payload)
    stopped = execution_stop_error(before_ctx)
    if stopped is not None:
        after_ctx = await after({**payload, "outcome": "stopped", "error": stopped})
        raise_if_execution_stopped(after_ctx)
        raise stopped

    try:
        result = await effect()
    except Exception as exc:
        after_ctx = await after({**payload, "outcome": "error", "error": exc})
        raise_if_execution_stopped(after_ctx)
        raise

    after_ctx = await after({**payload, "outcome": "success", "result": result})
    raise_if_execution_stopped(after_ctx)
    return result
