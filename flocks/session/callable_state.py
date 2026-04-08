"""
Session-scoped callable tool storage.

This is the single runtime source of truth for which tools are callable within
the current session.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set

from flocks.storage.storage import Storage


_CALLABLE_PREFIX = "session_callable_tools:"
_cache: Dict[str, Set[str]] = {}


def _normalize_tool_names(tool_names: Iterable[str]) -> Set[str]:
    return {
        str(name).strip()
        for name in tool_names
        if str(name).strip()
    }


async def get_session_callable_tools(session_id: str) -> Set[str]:
    if session_id in _cache:
        return set(_cache[session_id])

    stored = await Storage.get(f"{_CALLABLE_PREFIX}{session_id}")
    if isinstance(stored, dict):
        names = set(str(name) for name in stored.get("tools", []) if name)
    elif isinstance(stored, list):
        names = set(str(name) for name in stored if name)
    else:
        names = set()

    _cache[session_id] = set(names)
    return set(names)


async def set_session_callable_tools(session_id: str, tool_names: Iterable[str]) -> Set[str]:
    normalized = set(sorted(_normalize_tool_names(tool_names)))
    _cache[session_id] = normalized
    await Storage.set(
        f"{_CALLABLE_PREFIX}{session_id}",
        {"tools": sorted(normalized)},
        "session_callable_tools",
    )
    return set(normalized)


async def add_session_callable_tools(session_id: str, tool_names: Iterable[str]) -> Set[str]:
    current = await get_session_callable_tools(session_id)
    current.update(_normalize_tool_names(tool_names))
    return await set_session_callable_tools(session_id, current)


async def initialize_session_callable_tools(
    session_id: str,
    base_tool_names: Iterable[str],
    *,
    always_load_tool_names: Optional[Iterable[str]] = None,
) -> Set[str]:
    combined = set(_normalize_tool_names(base_tool_names))
    combined.update(_normalize_tool_names(always_load_tool_names or []))
    return await set_session_callable_tools(session_id, combined)


async def clear_session_callable_tools(session_id: str) -> None:
    _cache.pop(session_id, None)
    await Storage.delete(f"{_CALLABLE_PREFIX}{session_id}")


async def session_can_call_tool(session_id: str, tool_name: str) -> bool:
    return tool_name in await get_session_callable_tools(session_id)
