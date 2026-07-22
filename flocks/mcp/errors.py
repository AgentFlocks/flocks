"""Helpers for classifying MCP protocol errors."""

from __future__ import annotations

from mcp.types import METHOD_NOT_FOUND


def is_method_not_found_error(exc: BaseException) -> bool:
    """Return whether an exception chain contains JSON-RPC method-not-found."""
    return _contains_method_not_found(exc, seen=set())


def _contains_method_not_found(exc: BaseException, *, seen: set[int]) -> bool:
    exc_id = id(exc)
    if exc_id in seen:
        return False
    seen.add(exc_id)

    error = getattr(exc, "error", None)
    if getattr(error, "code", None) == METHOD_NOT_FOUND:
        return True

    if isinstance(exc, BaseExceptionGroup):
        if any(_contains_method_not_found(child, seen=seen) for child in exc.exceptions):
            return True

    cause = exc.__cause__
    if cause is not None and _contains_method_not_found(cause, seen=seen):
        return True
    context = exc.__context__
    if context is not None and _contains_method_not_found(context, seen=seen):
        return True

    message = str(exc).strip().lower()
    return message == "method not found" or message.endswith(": method not found")
