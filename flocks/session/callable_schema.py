"""
Callable schema resolution for a session.

This module turns the current session callable tool set into concrete tool infos
and the function schema exposed to the model for the current turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Set

from flocks.tool.catalog import get_always_load_tool_names
from flocks.session.callable_state import (
    get_session_callable_tools,
    initialize_session_callable_tools,
)
from flocks.identity import get_current_subject
from flocks.tool.registry import ToolRegistry


@dataclass
class CallableSchemaResult:
    tool_infos: List[Any]
    metadata: Dict[str, Any]


CallableToolResolver = Callable[
    [List[Any], Mapping[str, Any]],
    Awaitable[List[Any]] | List[Any],
]
_callable_tool_resolvers: dict[str, CallableToolResolver] = {}


def register_callable_tool_resolver(name: str, resolver: CallableToolResolver) -> None:
    """Register a declarative, subset-only callable-tool resolver."""
    _callable_tool_resolvers[name] = resolver


def unregister_callable_tool_resolver(name: str) -> None:
    _callable_tool_resolvers.pop(name, None)


async def _apply_callable_tool_resolvers(
    tool_infos: List[Any],
    context: Mapping[str, Any],
) -> List[Any]:
    """Apply resolvers as an intersection; they can never expand candidates."""
    allowed = {tool.name for tool in tool_infos}
    resolved = list(tool_infos)
    for resolver in _callable_tool_resolvers.values():
        candidate = resolver(list(resolved), context)
        if hasattr(candidate, "__await__"):
            candidate = await candidate
        if not isinstance(candidate, list):
            continue
        candidate_names = {
            tool.name for tool in candidate
            if getattr(tool, "name", None) in allowed
        }
        resolved = [tool for tool in resolved if tool.name in candidate_names]
    return resolved


def resolve_callable_tool_infos(tool_names: Iterable[str]) -> tuple[List[Any], int]:
    callable_names = set(tool_names)
    tool_infos: List[Any] = []
    enabled_count = 0

    for tool_info in ToolRegistry.list_tools():
        if tool_info.name in {"invalid", "_noop"} or not getattr(tool_info, "enabled", True):
            continue
        enabled_count += 1
        if tool_info.name in callable_names:
            tool_infos.append(tool_info)

    return tool_infos, enabled_count


async def _resolve_dynamic_always_load_tool_names() -> Set[str]:
    """Return runtime-only always-load tools.

    Device management should be available without an extra ``tool_search`` hop
    when the workspace has at least one enabled device, but we do not want to
    expose it in sessions that have no security devices.
    """
    dynamic_names: Set[str] = set()
    candidate_names = ("device_manage",)

    for name in candidate_names:
        try:
            tool = ToolRegistry.get(name)
        except Exception:
            continue
        if tool is not None and getattr(tool.info, "enabled", True):
            dynamic_names.add(name)

    if not dynamic_names:
        return dynamic_names

    try:
        from flocks.tool.device.store import list_devices

        devices = await list_devices()
    except Exception:
        return dynamic_names

    return dynamic_names if any(device.enabled for device in devices) else set()


async def list_session_callable_tool_infos(
    session_id: str,
    declared_tool_names: Optional[Iterable[str]] = None,
    *,
    agent: str | None = None,
    step: int = 0,
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
) -> CallableSchemaResult:
    callable_tool_names = await get_session_callable_tools(session_id)
    always_load_names = get_always_load_tool_names() | await _resolve_dynamic_always_load_tool_names()

    if not callable_tool_names:
        base_tools = list(declared_tool_names) if declared_tool_names is not None else []
        callable_tool_names = await initialize_session_callable_tools(
            session_id,
            base_tools,
            always_load_tool_names=always_load_names,
        )

    effective_callable_names = set(callable_tool_names) | always_load_names
    tool_infos, enabled_count = resolve_callable_tool_infos(effective_callable_names)

    projection_payload: Dict[str, Any] = {
        "session_id": session_id,
        "step": step,
        "candidates": [
            {
                "name": tool_info.name,
                "description": tool_info.description,
                "category": getattr(tool_info.category, "value", tool_info.category),
                "source": tool_info.source,
            }
            for tool_info in tool_infos
        ],
    }
    if agent:
        projection_payload["agent"] = agent
    try:
        from flocks.session.execution_profile import get_session_execution_profile

        profile = await get_session_execution_profile(session_id)
        if isinstance(profile, dict):
            projection_payload["session_execution_profile"] = profile
    except Exception:
        pass
    subject = get_current_subject()
    if subject is not None:
        # Subject is an opaque extension carrier.  Flocks deliberately keeps
        # its attributes nested and does not interpret them as policy fields.
        projection_payload["subject"] = subject.model_dump()
    tool_infos = await _apply_callable_tool_resolvers(tool_infos, projection_payload)

    metadata = {
        "enabledToolCount": enabled_count,
        "callableToolCount": len(callable_tool_names),
        "alwaysLoadToolCount": len(always_load_names),
        "callableToolNames": sorted(callable_tool_names),
        "alwaysLoadToolNames": sorted(always_load_names),
    }

    if event_publish_callback:
        await event_publish_callback("runtime.tool_selection", {
            "sessionID": session_id,
            "step": step,
            **metadata,
        })

    return CallableSchemaResult(tool_infos=tool_infos, metadata=metadata)
