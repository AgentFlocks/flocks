"""Neutral lifecycle wrapping for mutating HTTP route effects.

The adapter intentionally supplies only route facts and argument shape.  It
does not classify risk, authorize callers, or interpret route data; extensions
such as FlocksPro own those decisions.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Dict, Mapping

from fastapi import APIRouter, HTTPException

from flocks.auth.context import get_current_auth_user
from flocks.hooks.execution import ExecutionStopped, execute_with_hooks


_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _argument_shape(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return stable, non-secret structural facts for a route invocation."""
    fields: Dict[str, list[str]] = {}
    for name, value in arguments.items():
        model_fields = getattr(value, "model_fields", None)
        if isinstance(model_fields, dict):
            fields[name] = sorted(str(field) for field in model_fields)
    return {
        "argument_names": sorted(str(name) for name in arguments),
        "model_fields": fields,
    }


def _audit_actor(arguments: Mapping[str, Any]) -> Dict[str, str]:
    """Return trusted actor hints for lifecycle audit correlation."""
    user = get_current_auth_user()
    if user is not None:
        actor_id = str(user.id or "").strip()
        actor_name = str(user.username or "").strip()
        if actor_id and actor_name:
            return {"id": actor_id, "name": actor_name}
    for value in arguments.values():
        if isinstance(value, Mapping):
            actor_id = str(value.get("id") or "").strip()
            actor_name = str(value.get("username") or value.get("name") or "").strip()
        else:
            actor_id = str(getattr(value, "id", "") or "").strip()
            actor_name = str(
                getattr(value, "username", "") or getattr(value, "name", "") or ""
            ).strip()
        if actor_id and actor_name:
            return {"id": actor_id, "name": actor_name}
    return {}


def action_operation_payload(
    domain: str,
    endpoint: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Build an opaque, data-minimized action payload for extensions."""
    try:
        arguments = dict(inspect.signature(endpoint).bind_partial(*args, **kwargs).arguments)
    except TypeError:
        arguments = dict(kwargs)
    action_id = endpoint.__name__
    resource_id = action_id.removeprefix(f"{domain}_") or action_id
    operation = f"{domain}.{action_id}"
    actor = _audit_actor(arguments)
    return {
        "operation": operation,
        "action": operation,
        "entry": "http_control_plane",
        "execution_domain": "control_plane",
        "resource": {"type": domain, "id": resource_id},
        "action_input": _argument_shape(arguments),
        "tool_context_extra": {
            "execution_context": {
                "audit_actor": actor,
            }
        },
    }


def wrap_action_endpoint(domain: str, endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap one mutating FastAPI endpoint in the generic action lifecycle."""

    @wraps(endpoint)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return await execute_with_hooks(
                action_operation_payload(domain, endpoint, args, kwargs),
                lambda: endpoint(*args, **kwargs),
            )
        except ExecutionStopped as exc:
            raise HTTPException(
                status_code=403,
                detail="Action stopped by extension",
            ) from exc

    return _wrapped


class ActionLifecycleRouter(APIRouter):
    """Router that wraps every mutating endpoint with neutral lifecycle hooks."""

    def __init__(self, *, lifecycle_domain: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lifecycle_domain = lifecycle_domain

    def api_route(self, path: str, *args: Any, **kwargs: Any):
        methods = kwargs.get("methods") or []
        is_mutating = bool(
            {str(method).upper() for method in methods} & _MUTATING_METHODS
        )
        base_decorator = super().api_route(path, *args, **kwargs)

        def _decorate(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            wrapped = (
                wrap_action_endpoint(self.lifecycle_domain, endpoint)
                if is_mutating
                else endpoint
            )
            base_decorator(wrapped)
            return wrapped

        return _decorate


__all__ = [
    "ActionLifecycleRouter",
    "action_operation_payload",
    "wrap_action_endpoint",
]
