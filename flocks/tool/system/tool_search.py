"""
Tool search / discovery helper.

Lets the model search available tools and immediately add the returned matches
to the current session's callable tool set.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flocks.tool.catalog import normalize_tool_search_query, search_tool_catalog
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.session.callable_state import add_session_callable_tools


DESCRIPTION = """Search available tools by task intent, keyword, category, or exact names.

Use this tool when you need to discover a tool that is not already exposed in
the current turn. Search by user goal, capability, or keyword. Matching tools
returned here are added to the current session callable tool set immediately.
If you already know the needed tool names, prefer one exact batch query such as
`select:websearch,webfetch,skill` instead of multiple separate searches.
IMPORTANT: search query must be in English.
"""


async def _build_device_tool_hints(matches: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build per-tool device discovery hints for device-backed tools only."""
    device_tools = [
        match for match in matches
        if (match.get("source") or "").lower() == "device" and match.get("name")
    ]
    if not device_tools:
        return {}

    try:
        from flocks.tool.device.store import list_devices, list_groups

        devices = await list_devices()
        groups = await list_groups()
    except Exception:
        return {}

    group_names = {group.id: group.name for group in groups}
    tool_hints: Dict[str, Dict[str, Any]] = {}

    for match in device_tools:
        storage_key = str(match.get("provider") or "")
        candidates = []
        enabled_candidates = []

        for device in devices:
            if device.storage_key != storage_key:
                continue
            candidate = {
                "device_id": device.id,
                "name": device.name,
                "group_id": device.group_id,
                "group_name": group_names.get(device.group_id, device.group_id),
                "enabled": bool(device.enabled),
            }
            candidates.append(candidate)
            if device.enabled:
                enabled_candidates.append(candidate)

        if not enabled_candidates:
            ambiguity = "none"
        elif len(enabled_candidates) == 1:
            ambiguity = "single"
        else:
            ambiguity = "multiple"

        tool_hints[str(match["name"])] = {
            "toolSetId": storage_key,
            "requiresDeviceId": len(enabled_candidates) != 1,
            "ambiguity": ambiguity,
            "candidateDevices": enabled_candidates or candidates,
        }

    return tool_hints

@ToolRegistry.register_function(
    name="tool_search",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description=(
                "Search query describing the capability or task intent. "
                "Use select:tool_a,tool_b to expose multiple known tools in one call."
            ),
            required=False,
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="Optional category filter such as file, search, code, terminal, system, browser, custom",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Maximum number of matching tools to return",
            required=False,
            default=8,
        ),
    ],
)
async def tool_search(
    ctx: ToolContext,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 8,
) -> ToolResult:
    limit = max(1, min(limit or 8, 20))
    matches, matched_tags = search_tool_catalog(query, category=category, limit=limit)
    device_hints = await _build_device_tool_hints(matches)
    enriched_matches: List[Dict[str, Any]] = []
    for match in matches:
        enriched = dict(match)
        hint = device_hints.get(str(match.get("name", "")))
        if hint:
            enriched.update(hint)
        enriched_matches.append(enriched)
    normalized_query = normalize_tool_search_query(query or "")
    callable_candidates = [match["name"] for match in enriched_matches]
    callable_tools = await add_session_callable_tools(ctx.session_id, callable_candidates)
    if ctx.event_publish_callback:
        await ctx.event_publish_callback("runtime.tool_discovery", {
            "sessionID": ctx.session_id,
            "query": query or "",
            "normalizedQuery": normalized_query,
            "category": category,
            "returnedToolCount": len(matches),
            "callableToolCount": len(callable_tools),
            "callableToolNames": sorted(callable_candidates),
            "matchedTags": matched_tags,
            "deviceAwareToolCount": len(device_hints),
        })

    return ToolResult(
        success=True,
        output={
            "query": query or "",
            "normalizedQuery": normalized_query,
            "category": category,
            "count": len(matches),
            "matchedTags": matched_tags,
            "callableToolNames": sorted(callable_candidates),
            "callableToolCount": len(callable_tools),
            "deviceAwareToolCount": len(device_hints),
            # Legacy compatibility keys.
            "discoveredToolNames": sorted(callable_candidates),
            "discoveredToolCount": len(callable_tools),
            "matches": enriched_matches,
        },
    )
