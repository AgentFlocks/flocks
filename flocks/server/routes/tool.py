"""
Tool routes - API endpoints for tool management and execution
"""

import asyncio
from dataclasses import dataclass
import threading
import time
from typing import Annotated, List, Optional, Dict, Any, Literal, Sequence
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from flocks.server.auth import require_admin
from flocks.server.routes._timing import log_route_timing
from flocks.utils.log import Log
from flocks.config.config_writer import ConfigWriter
from flocks.permission.next import DeniedError, PermissionNext
from flocks.tool.registry import (
    ToolRegistry,
    ToolRefreshError,
    ToolInfo,
    ToolSchema,
    ToolResult,
    ToolCategory,
    ToolContext,
)


router = APIRouter()
log = Log.create(service="tool-routes")


# Request/Response Models

class ToolInfoResponse(BaseModel):
    """Tool information response"""
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    description_cn: Optional[str] = Field(None, description="Chinese UI description")
    category: str = Field(..., description="Tool category")
    source: str = Field("builtin", description="Tool source: builtin, mcp, api, device, custom")
    source_name: Optional[str] = Field(None, description="Source detail, e.g. MCP server name or API module name")
    vendor: Optional[str] = Field(None, description="Manufacturer key for device tools (e.g. threatbook, qianxin, sangfor, qingteng)")
    parameters: List[Dict[str, Any]] = Field(default_factory=list, description="Tool parameters")
    parameters_count: int = Field(0, description="Number of tool parameters")
    enabled: bool = Field(True, description="Effective enabled state (overlay applied, ANDed with API service flag)")
    enabled_default: bool = Field(True, description="Factory default from the YAML/registration source (no overlay)")
    enabled_customized: bool = Field(False, description="True if a user setting is recorded in flocks.json tool_settings")
    requires_confirmation: bool = Field(False, description="Requires confirmation")


class ToolSchemaResponse(BaseModel):
    """Tool schema response"""
    name: str = Field(..., description="Tool name")
    schema_: Dict[str, Any] = Field(..., alias="schema", description="JSON Schema")


class ToolUpdateRequest(BaseModel):
    """Tool update request"""
    enabled: bool = Field(..., description="Enable or disable the tool")


class ToolExecuteRequest(BaseModel):
    """Tool execution request"""
    model_config = {"populate_by_name": True}
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


class ToolExecuteResponse(BaseModel):
    """Tool execution response"""
    success: bool = Field(..., description="Execution successful")
    output: Any = Field(None, description="Output data")
    error: Optional[str] = Field(None, description="Error message")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")


class BatchToolCall(BaseModel):
    """Single tool call in batch"""
    name: str = Field(..., description="Tool name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class BatchExecuteRequest(BaseModel):
    """Batch tool execution request"""
    model_config = {"populate_by_name": True}
    calls: List[BatchToolCall] = Field(..., description="Tool calls to execute")
    parallel: bool = Field(True, description="Execute in parallel")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


class BatchExecuteResponse(BaseModel):
    """Batch tool execution response"""
    results: List[ToolExecuteResponse] = Field(..., description="Execution results")


class ToolListFacets(BaseModel):
    """Facet counts for server-side tool list filtering."""
    category: Dict[str, int] = Field(default_factory=dict)
    source: Dict[str, int] = Field(default_factory=dict)
    source_groups: Dict[str, int] = Field(default_factory=dict)
    source_name: Dict[str, int] = Field(default_factory=dict)
    enabled: Dict[str, int] = Field(default_factory=dict)


class ToolListPageResponse(BaseModel):
    """Paginated tool list response."""
    items: List[ToolInfoResponse] = Field(default_factory=list)
    total: int = Field(0)
    offset: int = Field(0)
    limit: int = Field(25)
    facets: ToolListFacets = Field(default_factory=ToolListFacets)


# Helper: determine tool source

_BUILTIN_CATEGORIES = {
    ToolCategory.FILE, ToolCategory.TERMINAL, ToolCategory.BROWSER,
    ToolCategory.CODE, ToolCategory.SEARCH, ToolCategory.SYSTEM,
}

_DIRECT_HTTP_BLOCKED_MESSAGE = (
    "Direct HTTP tool execution is disabled for local or permission-gated tools. "
    "Use a session-backed request (provide sessionID/messageID) or run the tool via the normal agent/session flow."
)
_VERIFIED_CONTEXT_REQUIRED_MESSAGE = (
    "Direct HTTP execution for local or permission-gated tools requires a verified "
    "session-backed request with both sessionID and messageID."
)


@dataclass(frozen=True)
class ToolListIndexItem:
    name: str
    description: str
    description_cn: Optional[str]
    category: str
    source: str
    source_name: Optional[str]
    vendor: Optional[str]
    parameters_count: int
    enabled: bool
    enabled_default: bool
    enabled_customized: bool
    requires_confirmation: bool


_TOOL_SUMMARY_CACHE_TTL_SECONDS = 5.0
_tool_summary_cache_lock = threading.Lock()
_tool_summary_cache_key: tuple[int, tuple[str, ...]] | None = None
_tool_summary_cache_expires_at = 0.0
_tool_summary_cache_items: tuple[ToolListIndexItem, ...] = ()


def _invalidate_tool_summary_cache() -> None:
    """Clear the lightweight list snapshot used by ``GET /api/tools/page``."""
    global _tool_summary_cache_key, _tool_summary_cache_expires_at, _tool_summary_cache_items
    with _tool_summary_cache_lock:
        _tool_summary_cache_key = None
        _tool_summary_cache_expires_at = 0.0
        _tool_summary_cache_items = ()


def _tool_summary_cache_current_key() -> tuple[int, tuple[str, ...]]:
    return ToolRegistry.snapshot_identity()


def _get_tool_summary_items() -> List[ToolListIndexItem]:
    """Return cached, lightweight tool summaries for list filtering."""
    global _tool_summary_cache_key, _tool_summary_cache_expires_at, _tool_summary_cache_items

    now = time.monotonic()
    cache_key = _tool_summary_cache_current_key()
    with _tool_summary_cache_lock:
        if (
            _tool_summary_cache_items
            and _tool_summary_cache_key == cache_key
            and now < _tool_summary_cache_expires_at
        ):
            return list(_tool_summary_cache_items)

        items = tuple(_build_tool_index_item(t) for t in ToolRegistry.list_tools())
        _tool_summary_cache_key = cache_key
        _tool_summary_cache_expires_at = now + _TOOL_SUMMARY_CACHE_TTL_SECONDS
        _tool_summary_cache_items = items
        return list(items)


def _get_tool_source(tool_info: ToolInfo) -> tuple:
    """
    Determine tool source type and source name.
    
    Returns:
        (source, source_name) tuple where source is one of:
        'builtin', 'mcp', 'api', 'device', 'plugin_yaml', 'plugin_py', 'custom'
    """
    # Use ToolInfo.source field if explicitly set
    if tool_info.source == "api":
        return "api", tool_info.provider
    if tool_info.source == "device":
        return "device", tool_info.provider
    if tool_info.source == "plugin_yaml":
        return "plugin_yaml", tool_info.provider
    if tool_info.source == "plugin_py":
        return "plugin_py", None

    # Check MCP source
    try:
        from flocks.mcp import MCP
        if MCP.is_mcp_tool(tool_info.name):
            source_info = MCP.get_tool_source(tool_info.name)
            server_name = source_info.mcp_server if source_info else None
            return "mcp", server_name
    except Exception as e:
        log.debug("tool.source_check.mcp_error", {"tool": tool_info.name, "error": str(e)})
    
    # Check if from dynamic/generated module (API tools)
    for module_name, tool_names in ToolRegistry.get_dynamic_tools_by_module().items():
        if tool_info.name in tool_names:
            friendly_name = module_name.rsplit(".", 1)[-1] if "." in module_name else module_name
            return "api", friendly_name
    
    # Builtin tools: recognized by non-CUSTOM categories
    if tool_info.category in _BUILTIN_CATEGORIES:
        return "builtin", "Flocks"
    
    # Default: custom
    return "custom", None


def _build_tool_response(t: ToolInfo, *, include_parameters: bool = True) -> ToolInfoResponse:
    """Build ToolInfoResponse with source info and overlay metadata."""
    source, source_name = _get_tool_source(t)
    setting = ConfigWriter.get_tool_setting(t.name) or {}
    customized = "enabled" in setting
    enabled_default = _get_default_enabled(t)
    parameters = [p.model_dump() for p in t.parameters] if include_parameters else []
    return ToolInfoResponse(
        name=t.name,
        description=t.description,
        description_cn=t.description_cn,
        category=t.category.value,
        source=source,
        source_name=source_name,
        vendor=t.vendor,
        parameters=parameters,
        parameters_count=len(t.parameters),
        enabled=_get_effective_tool_enabled(t, source=source, source_name=source_name),
        enabled_default=enabled_default,
        enabled_customized=customized,
        requires_confirmation=t.requires_confirmation,
    )


def _build_tool_index_item(t: ToolInfo) -> ToolListIndexItem:
    """Build the lightweight index row used by the paged list endpoint."""
    source, source_name = _get_tool_source(t)
    setting = ConfigWriter.get_tool_setting(t.name) or {}
    return ToolListIndexItem(
        name=t.name,
        description=t.description,
        description_cn=t.description_cn,
        category=t.category.value,
        source=source,
        source_name=source_name,
        vendor=t.vendor,
        parameters_count=len(t.parameters),
        enabled=_get_effective_tool_enabled(t, source=source, source_name=source_name),
        enabled_default=_get_default_enabled(t),
        enabled_customized="enabled" in setting,
        requires_confirmation=t.requires_confirmation,
    )


def _tool_index_item_to_response(item: ToolListIndexItem) -> ToolInfoResponse:
    return ToolInfoResponse(
        name=item.name,
        description=item.description,
        description_cn=item.description_cn,
        category=item.category,
        source=item.source,
        source_name=item.source_name,
        vendor=item.vendor,
        parameters=[],
        parameters_count=item.parameters_count,
        enabled=item.enabled,
        enabled_default=item.enabled_default,
        enabled_customized=item.enabled_customized,
        requires_confirmation=item.requires_confirmation,
    )


def _split_csv_filter(value: Optional[str]) -> Optional[set[str]]:
    if value is None:
        return None
    parts = {part.strip() for part in value.split(",") if part and part.strip()}
    return parts or None


def _matches_tool_query(tool: ToolInfoResponse | ToolListIndexItem, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join([
        tool.name,
        tool.description,
        tool.description_cn or "",
        tool.category,
        tool.source,
        tool.source_name or "",
        tool.vendor or "",
    ]).lower()
    return query in haystack


def _build_tool_facets(items: Sequence[ToolInfoResponse | ToolListIndexItem]) -> ToolListFacets:
    facets = ToolListFacets()
    for item in items:
        facets.category[item.category] = facets.category.get(item.category, 0) + 1
        facets.source[item.source] = facets.source.get(item.source, 0) + 1
        source_name = item.source_name or "Flocks"
        facets.source_name[source_name] = facets.source_name.get(source_name, 0) + 1
        enabled_key = str(item.enabled).lower()
        facets.enabled[enabled_key] = facets.enabled.get(enabled_key, 0) + 1
    return facets


def _build_source_group_counts(
    items: Sequence[ToolInfoResponse | ToolListIndexItem],
) -> Dict[str, int]:
    groups: Dict[str, set[str]] = {}
    for item in items:
        if not item.source_name:
            continue
        groups.setdefault(item.source, set()).add(item.source_name)
    return {source: len(source_names) for source, source_names in groups.items()}


def _sort_tool_items(
    items: Sequence[ToolInfoResponse | ToolListIndexItem],
    sort_by: Literal["category", "source", "source_name", "enabled", "name"],
    sort_dir: Literal["asc", "desc"],
) -> List[ToolInfoResponse | ToolListIndexItem]:
    reverse = sort_dir == "desc"

    def sort_key(item: ToolInfoResponse):
        if sort_by == "enabled":
            return 0 if item.enabled else 1
        if sort_by == "source_name":
            return (item.source_name or "Flocks").lower()
        return getattr(item, sort_by)

    return sorted(items, key=sort_key, reverse=reverse)


def _filter_tool_items(
    items: Sequence[ToolInfoResponse | ToolListIndexItem],
    *,
    category_filter: Optional[set[str]],
    source_filter: Optional[set[str]],
    source_name_filter: Optional[set[str]],
    enabled_filter: Optional[set[str]],
    query: str,
    include_category: bool = True,
    include_source: bool = True,
    include_source_name: bool = True,
    include_enabled: bool = True,
) -> List[ToolInfoResponse | ToolListIndexItem]:
    result = list(items)
    if include_category and category_filter:
        result = [tool for tool in result if tool.category in category_filter]
    if include_source and source_filter:
        result = [tool for tool in result if tool.source in source_filter]
    if include_source_name and source_name_filter:
        result = [tool for tool in result if (tool.source_name or "Flocks") in source_name_filter]
    if include_enabled and enabled_filter:
        result = [tool for tool in result if str(tool.enabled).lower() in enabled_filter]
    if query:
        result = [tool for tool in result if _matches_tool_query(tool, query)]
    return result


def _requires_session_backed_context(tool_info: ToolInfo) -> bool:
    """Return True when a tool must be anchored to a real session/message context."""
    source, _ = _get_tool_source(tool_info)
    return source == "builtin"


async def _validate_verified_session_message_context(
    *,
    requires_verified_context: bool,
    session_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate that session/message context exists and the message belongs to the session."""
    effective_session_id = str(session_id or "").strip() or None
    effective_message_id = str(message_id or "").strip() or None
    needs_verified_context = requires_verified_context or bool(effective_session_id or effective_message_id)

    if not needs_verified_context:
        return None, None

    if not effective_session_id or not effective_message_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_VERIFIED_CONTEXT_REQUIRED_MESSAGE,
        )

    from flocks.session.message import Message
    from flocks.session.session import Session

    session = await Session.get_by_id(effective_session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {effective_session_id}",
        )

    message = await Message.get(effective_session_id, effective_message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {effective_message_id} not found in session {effective_session_id}",
        )

    return effective_session_id, effective_message_id


async def _validate_session_message_context(
    *,
    tool_info: ToolInfo,
    session_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate context for a single tool execution request."""
    return await _validate_verified_session_message_context(
        requires_verified_context=_requires_session_backed_context(tool_info),
        session_id=session_id,
        message_id=message_id,
    )


def _build_http_tool_context(
    *,
    tool_name: str,
    tool_info: ToolInfo,
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
) -> ToolContext:
    """Create a safe ToolContext for HTTP-triggered execution."""
    agent_name = agent or "rex"
    effective_message_id = message_id or f"http-tool:{tool_name}"

    if session_id:
        async def permission_callback(request) -> None:
            metadata = dict(request.metadata or {})
            metadata.setdefault("messageID", effective_message_id)
            metadata.setdefault("route", "tool.execute")
            await PermissionNext.ask(
                session_id=session_id,
                permission=request.permission,
                patterns=list(request.patterns or []),
                ruleset=[],
                metadata=metadata,
                always=list(request.always or []),
                tool={"name": tool_name},
            )

        return ToolContext(
            session_id=session_id,
            message_id=effective_message_id,
            agent=agent_name,
            permission_callback=permission_callback,
        )

    if _requires_session_backed_context(tool_info):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_DIRECT_HTTP_BLOCKED_MESSAGE,
        )

    async def deny_permission_callback(request) -> None:
        raise PermissionError(
            "This HTTP execution context cannot auto-approve tool permissions."
        )

    return ToolContext(
        session_id="http-tool",
        message_id=effective_message_id,
        agent=agent_name,
        permission_callback=deny_permission_callback,
    )


def _permission_denied_http_error(exc: Exception) -> HTTPException:
    """Normalize permission failures into a consistent 403 response."""
    detail = str(exc).strip() or _DIRECT_HTTP_BLOCKED_MESSAGE
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _execute_with_http_context(
    *,
    tool_name: str,
    tool_info: ToolInfo,
    params: Dict[str, Any],
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
) -> ToolResult:
    """Execute a tool using an HTTP-safe ToolContext."""
    validated_session_id, validated_message_id = await _validate_session_message_context(
        tool_info=tool_info,
        session_id=session_id,
        message_id=message_id,
    )
    ctx = _build_http_tool_context(
        tool_name=tool_name,
        tool_info=tool_info,
        session_id=validated_session_id,
        message_id=validated_message_id,
        agent=agent,
    )
    try:
        return await ToolRegistry.execute(tool_name=tool_name, ctx=ctx, **params)
    except (DeniedError, PermissionError) as exc:
        raise _permission_denied_http_error(exc) from exc


async def _execute_batch_with_http_context(
    *,
    calls: List[BatchToolCall],
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
    parallel: bool,
) -> List[ToolResult]:
    """Execute batch calls with a per-tool HTTP context."""

    async def run_call(call: BatchToolCall) -> ToolResult:
        tool = ToolRegistry.get(call.name)
        if tool is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tool not found: {call.name}",
            )
        return await _execute_with_http_context(
            tool_name=call.name,
            tool_info=tool.info,
            params=call.params,
            session_id=session_id,
            message_id=message_id,
            agent=agent,
        )

    if parallel:
        return await asyncio.gather(*(run_call(call) for call in calls))

    results: List[ToolResult] = []
    for call in calls:
        results.append(await run_call(call))
    return results

def _get_default_enabled(t: ToolInfo) -> bool:
    """Return the registration-time default for ``enabled``.

    Prefers :meth:`ToolRegistry.get_default_enabled` (a snapshot taken
    before sync/overlay mutate ``info.enabled`` in place).  Falls back to
    the YAML file when the snapshot is missing (e.g. a tool registered
    after init), then to the live value as the very last resort.
    """
    snapshot = ToolRegistry.get_default_enabled(t.name)
    if snapshot is not None:
        return snapshot
    try:
        from flocks.tool.tool_loader import read_yaml_tool
        raw = read_yaml_tool(t.name)
    except Exception:
        raw = None
    if isinstance(raw, dict) and "enabled" in raw:
        return bool(raw["enabled"])
    return t.enabled


def _service_allows_enable(t: ToolInfo) -> bool:
    """Return True when the API service backing ``t`` (if any) is enabled.

    Mirrors the gate in :meth:`ToolRegistry._apply_tool_settings` so that
    HTTP mutations stay consistent with what the registry would compute
    on its next reload: an overlay can never *open* a tool whose service
    is currently disabled.
    """
    if not t.provider:
        return True
    svc = ConfigWriter.get_api_service_raw(t.provider) or {}
    return bool(svc.get("enabled", False))


def _get_effective_tool_enabled(
    tool_info: ToolInfo,
    *,
    source: Optional[str] = None,
    source_name: Optional[str] = None,
) -> bool:
    """Compute tool enabled state without mutating the registry object."""
    if source is None:
        source, source_name = _get_tool_source(tool_info)
    if source not in ("api", "device") or not source_name:
        return tool_info.enabled
    from flocks.server.routes.provider import _get_api_service_enabled

    return tool_info.enabled and _get_api_service_enabled(source_name)


def _set_global_tool_enabled(tool: Any, desired: bool) -> bool:
    """Persist and apply the global enabled state for a registry tool."""
    default = _get_default_enabled(tool.info)
    # Service gate: only matters when the user is trying to enable.
    # Disabling is always honoured.
    service_ok = _service_allows_enable(tool.info)
    new_enabled = desired and service_ok

    if desired == default:
        removed = ConfigWriter.delete_tool_setting(tool.info.name)
        log.info("tool.updated.reset_to_default", {
            "name": tool.info.name,
            "enabled": new_enabled,
            "default": default,
            "removed_overlay": removed,
        })
    else:
        ConfigWriter.set_tool_setting(tool.info.name, {"enabled": desired})
        log.info("tool.updated", {
            "name": tool.info.name,
            "enabled": new_enabled,
            "requested": desired,
            "blocked_by_service": desired and not service_ok,
            "native": tool.info.native,
            "store": "overlay",
        })

    tool.info.enabled = new_enabled
    return new_enabled


# Routes

@router.get(
    "",
    response_model=List[ToolInfoResponse],
    summary="List all tools",
)
async def list_tools(
    category: Optional[str] = None,
    source: Optional[str] = None,
):
    """
    List all available tools
    
    Args:
        category: Optional category filter (file, terminal, browser, etc.)
        source: Optional source filter (builtin, mcp, api, custom)
        
    Returns:
        List of tool information
    """
    # Initialize registry if needed
    started_at = time.perf_counter()
    await ToolRegistry.init_async()
    
    # Parse category filter
    cat_filter = None
    if category:
        try:
            cat_filter = ToolCategory(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category: {category}"
            )
    
    tools = ToolRegistry.list_tools(category=cat_filter)
    result = [_build_tool_response(t) for t in tools]
    
    # Apply source filter if specified
    if source:
        result = [t for t in result if t.source == source]

    log_route_timing(log, "tools.list.complete", started_at=started_at, extra={
        "count": len(result),
        "category": category,
        "source": source,
    })
    return result


@router.get(
    "/page",
    response_model=ToolListPageResponse,
    summary="List tools with server-side pagination",
)
async def list_tools_page(
    category: Optional[str] = None,
    source: Optional[str] = None,
    source_name: Optional[str] = None,
    enabled: Optional[str] = None,
    q: Optional[str] = None,
    sort_by: Literal["category", "source", "source_name", "enabled", "name"] = "source",
    sort_dir: Literal["asc", "desc"] = "asc",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    """
    List tools with server-side search/filter/sort/pagination.

    The paged list omits full parameter definitions and exposes
    `parameters_count`; call `GET /api/tools/{tool_name}` for details.
    """
    started_at = time.perf_counter()
    await ToolRegistry.init_async()

    category_filter = _split_csv_filter(category)
    source_filter = _split_csv_filter(source)
    source_name_filter = _split_csv_filter(source_name)
    enabled_filter = _split_csv_filter(enabled)
    query = (q or "").strip().lower()

    all_items = _get_tool_summary_items()

    result = _filter_tool_items(
        all_items,
        category_filter=category_filter,
        source_filter=source_filter,
        source_name_filter=source_name_filter,
        enabled_filter=enabled_filter,
        query=query,
    )
    source_facet_items = _filter_tool_items(
        all_items,
        category_filter=category_filter,
        source_filter=source_filter,
        source_name_filter=source_name_filter,
        enabled_filter=enabled_filter,
        query=query,
        include_source=False,
    )
    facets = ToolListFacets(
        category=_build_tool_facets(_filter_tool_items(
            all_items,
            category_filter=category_filter,
            source_filter=source_filter,
            source_name_filter=source_name_filter,
            enabled_filter=enabled_filter,
            query=query,
            include_category=False,
        )).category,
        source=_build_tool_facets(source_facet_items).source,
        source_groups=_build_source_group_counts(source_facet_items),
        source_name=_build_tool_facets(_filter_tool_items(
            all_items,
            category_filter=category_filter,
            source_filter=source_filter,
            source_name_filter=source_name_filter,
            enabled_filter=enabled_filter,
            query=query,
            include_source_name=False,
        )).source_name,
        enabled=_build_tool_facets(_filter_tool_items(
            all_items,
            category_filter=category_filter,
            source_filter=source_filter,
            source_name_filter=source_name_filter,
            enabled_filter=enabled_filter,
            query=query,
            include_enabled=False,
        )).enabled,
    )
    result = _sort_tool_items(result, sort_by, sort_dir)
    total = len(result)
    items = [
        _tool_index_item_to_response(item) if isinstance(item, ToolListIndexItem) else item
        for item in result[offset:offset + limit]
    ]

    log_route_timing(log, "tools.list_page.complete", started_at=started_at, extra={
        "count": len(items),
        "total": total,
        "category": category,
        "source": source,
        "q": q,
        "offset": offset,
        "limit": limit,
    })
    return ToolListPageResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        facets=facets,
    )


@router.get(
    "/{tool_name}",
    response_model=ToolInfoResponse,
    summary="Get tool details",
)
async def get_tool(tool_name: str):
    """
    Get tool information by name
    
    Args:
        tool_name: Tool name
        
    Returns:
        Tool information
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )

    return _build_tool_response(tool.info)


@router.patch(
    "/{tool_name}",
    response_model=ToolInfoResponse,
    summary="Update tool settings",
)
async def update_tool(
    tool_name: str,
    request: ToolUpdateRequest,
    device_id: Annotated[
        Optional[str],
        Query(
            description=(
                "设备实例 UUID。提供时仅修改该设备的工具开关（per-device 覆盖），"
                "不影响其他同版本设备；省略时修改全局 tool_settings（影响所有设备）。"
            ),
        ),
    ] = None,
    _admin: object = Depends(require_admin),
):
    """
    Update tool settings (e.g., enable or disable).

    **Global mode** (``device_id`` omitted):
    Persists to ``flocks.json`` → ``tool_settings.<tool_name>.enabled``.
    Affects all device instances that share this tool.

    **Per-device mode** (``device_id`` provided):
    Persists to the SQLite ``device_tool_settings`` table (one row per
    device_id × tool_name).  Only affects tool execution when ``device_id``
    is explicitly targeted, allowing Device A and Device B (same plugin
    version, different names) to carry independent disabled overrides.
    ``enabled=true`` clears the per-device disable and follows the global
    tool setting; if the global tool is disabled, it is enabled first. Rows
    are removed automatically via ON DELETE CASCADE when the parent device row
    is deleted.

    Two behaviours of note (global mode only):

    * If ``request.enabled`` matches the registration-time default we
      *delete* the overlay entry instead of writing one — the tool is
      back to "no customisation", and the UI's "已自定义" badge clears.
    * Asking to enable a tool whose API service is currently disabled
      still persists the overlay (so the intent survives the service
      being re-enabled later) but does not flip the in-memory
      ``info.enabled`` flag, mirroring the gate in
      :meth:`ToolRegistry._apply_tool_settings`.
    """
    await ToolRegistry.init_async()

    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}",
        )

    desired = bool(request.enabled)

    # --- Per-device mode ---
    if device_id:
        from flocks.tool.device.store import (
            delete_device_tool_setting,
            set_device_tool_enabled,
        )

        if desired:
            if not tool.info.enabled:
                _set_global_tool_enabled(tool, True)
            removed = await delete_device_tool_setting(device_id, tool_name)
            log.info("tool.device.updated.reset_to_global", {
                "name": tool_name,
                "device_id": device_id,
                "removed_override": removed,
                "enabled_global": tool.info.enabled,
            })
        else:
            await set_device_tool_enabled(device_id, tool_name, False)
            log.info("tool.device.updated", {
                "name": tool_name,
                "device_id": device_id,
                "enabled": False,
            })
        # The in-memory ToolInfo.enabled reflects global state. Per-device
        # enabled=True is not a supported override; switch-on means clear the
        # per-device disable and follow the global tool setting.
        _invalidate_tool_summary_cache()
        return _build_tool_response(tool.info)

    # --- Global mode (original behaviour) ---
    _set_global_tool_enabled(tool, desired)
    _invalidate_tool_summary_cache()
    return _build_tool_response(tool.info)


@router.post(
    "/{tool_name}/reset",
    response_model=ToolInfoResponse,
    summary="Reset a tool to its YAML/registration default",
)
async def reset_tool_setting(tool_name: str, _admin: object = Depends(require_admin)):
    """Remove the user setting for ``tool_name`` and restore the default.

    Restores the registration-time ``enabled`` value from the registry's
    snapshot (or the YAML file as a fallback) and re-applies the same
    service gate as :meth:`ToolRegistry._apply_tool_settings`, so the
    HTTP layer never leaves the in-memory state in a position the
    registry would refuse on its next reload.
    """
    ToolRegistry.init()

    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}",
        )

    removed = ConfigWriter.delete_tool_setting(tool_name)
    default = _get_default_enabled(tool.info)
    new_enabled = default and _service_allows_enable(tool.info)
    tool.info.enabled = new_enabled
    _invalidate_tool_summary_cache()

    log.info("tool.setting.reset", {
        "name": tool_name,
        "removed": removed,
        "default": default,
        "restored_enabled": new_enabled,
    })
    return _build_tool_response(tool.info)


@router.get(
    "/{tool_name}/schema",
    response_model=ToolSchemaResponse,
    summary="Get tool schema",
)
async def get_tool_schema(tool_name: str):
    """
    Get JSON Schema for a tool
    
    Args:
        tool_name: Tool name
        
    Returns:
        Tool JSON Schema
    """
    ToolRegistry.init()
    
    schema = ToolRegistry.get_schema(tool_name)
    if not schema:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )
    
    return ToolSchemaResponse(
        name=tool_name,
        schema=schema.to_json_schema(),
    )


@router.post(
    "/{tool_name}/execute",
    response_model=ToolExecuteResponse,
    summary="Execute a tool",
)
async def execute_tool(tool_name: str, request: ToolExecuteRequest):
    """
    Execute a tool with given parameters
    
    Args:
        tool_name: Tool name
        request: Execution parameters
        
    Returns:
        Execution result
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )

    if not _get_effective_tool_enabled(tool.info):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tool is disabled: {tool_name}"
        )
    
    log.info("tool.execute.request", {
        "tool": tool_name,
        "params": list(request.params.keys()),
        "session": request.session_id,
    })

    result = await _execute_with_http_context(
        tool_name=tool_name,
        tool_info=tool.info,
        params=request.params,
        session_id=request.session_id,
        message_id=request.message_id,
        agent=request.agent,
    )
    
    return ToolExecuteResponse(
        success=result.success,
        output=result.output,
        error=result.error,
        metadata=result.metadata,
    )


@router.post(
    "/batch",
    response_model=BatchExecuteResponse,
    summary="Execute multiple tools",
)
async def execute_batch(request: BatchExecuteRequest):
    """
    Execute multiple tools in batch
    
    Args:
        request: Batch execution request
        
    Returns:
        List of execution results
    """
    ToolRegistry.init()
    
    # Validate all tools exist
    for call in request.calls:
        tool = ToolRegistry.get(call.name)
        if not tool:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tool not found: {call.name}"
            )
    
    log.info("tool.batch.request", {
        "count": len(request.calls),
        "parallel": request.parallel,
        "session": request.session_id,
    })

    requires_verified_context = False
    for call in request.calls:
        tool = ToolRegistry.get(call.name)
        if tool and _requires_session_backed_context(tool.info):
            requires_verified_context = True
            break

    validated_session_id, validated_message_id = await _validate_verified_session_message_context(
        requires_verified_context=requires_verified_context,
        session_id=request.session_id,
        message_id=request.message_id,
    )

    try:
        results = await _execute_batch_with_http_context(
            calls=request.calls,
            session_id=validated_session_id,
            message_id=validated_message_id,
            agent=request.agent,
            parallel=request.parallel,
        )
    except (DeniedError, PermissionError) as exc:
        raise _permission_denied_http_error(exc) from exc
    
    return BatchExecuteResponse(
        results=[
            ToolExecuteResponse(
                success=r.success,
                output=r.output,
                error=r.error,
                metadata=r.metadata,
            )
            for r in results
        ]
    )


class RefreshResponse(BaseModel):
    """Tool refresh response"""
    status: Literal["success", "partial", "error"] = Field(..., description="Operation status")
    tool_count: int = Field(..., description="Total registered tool count after refresh")
    message: str = Field("", description="Human-readable summary")
    stages: Dict[str, Literal["success", "error"]] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Refresh all plugin and dynamic tools",
)
async def refresh_tools(_admin: object = Depends(require_admin)):
    """
    Reload all plugin tools (YAML + Python) and dynamically generated tools
    from disk without restarting the service.

    This is the batch counterpart to the single-tool ``/{name}/reload`` endpoint.
    """
    started_at = time.perf_counter()
    ToolRegistry.init()

    errors: list[str] = []
    stages: dict[str, Literal["success", "error"]] = {}

    for stage, refresh in (
        ("dynamic", ToolRegistry.refresh_dynamic_tools),
        ("plugin", ToolRegistry.refresh_plugin_tools),
    ):
        try:
            refresh()
            stages[stage] = "success"
        except ToolRefreshError as exc:
            stages[stage] = "error"
            stage_errors = [f"{stage}: {error}" for error in exc.errors]
            errors.extend(stage_errors)
            log.error(f"tools.refresh.{stage}_error", {"errors": exc.errors})
        except Exception as exc:
            stages[stage] = "error"
            errors.append(f"{stage}: {exc}")
            log.error(f"tools.refresh.{stage}_error", {"error": str(exc)})

    _invalidate_tool_summary_cache()
    tool_count = len(ToolRegistry.all_tool_ids())
    log_route_timing(log, "tools.refresh.done", started_at=started_at, extra={
        "tool_count": tool_count,
        "errors": len(errors),
    })

    failed_stages = sum(status == "error" for status in stages.values())
    if failed_stages == 0:
        outcome: Literal["success", "partial", "error"] = "success"
        message = f"All tools refreshed successfully ({tool_count} tools registered)"
    elif failed_stages == len(stages):
        outcome = "error"
        message = f"Tool refresh failed: {'; '.join(errors)}"
    else:
        outcome = "partial"
        message = f"Tool refresh completed with errors: {'; '.join(errors)}"

    return RefreshResponse(
        status=outcome,
        tool_count=tool_count,
        message=message,
        stages=stages,
        errors=errors,
    )


# =============================================================================
# WebUI Enhancement Routes
# =============================================================================

class ToolTestRequest(BaseModel):
    """Request to test a tool"""
    model_config = {"populate_by_name": True}
    params: Dict[str, Any] = Field(default_factory=dict, description="Test parameters")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


@router.post(
    "/{name}/test",
    response_model=ToolExecuteResponse,
    summary="Test tool",
)
async def test_tool(name: str, request: ToolTestRequest):
    """
    Test a tool
    
    Executes the tool with provided test parameters and returns the result.
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {name}"
        )
    
    log.info("tool.test", {"name": name, "params": request.params})

    tool_request = ToolExecuteRequest(
        params=request.params,
        sessionID=request.session_id,
        messageID=request.message_id,
        agent=request.agent,
    )

    # Execute tool
    try:
        result = await _execute_with_http_context(
            tool_name=name,
            tool_info=tool.info,
            params=tool_request.params,
            session_id=tool_request.session_id,
            message_id=tool_request.message_id,
            agent=tool_request.agent,
        )
        return ToolExecuteResponse(
            success=result.success,
            output=result.output,
            error=result.error,
            metadata=result.metadata,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("tool.test.error", {"name": name, "error": str(e)})
        return ToolExecuteResponse(
            success=False,
            output=None,
            error=str(e),
            metadata={},
        )


# =============================================================================
# Test Fixtures Route
# =============================================================================


class FixtureItemResponse(BaseModel):
    """One predeclared test sample for a tool."""
    label: str = Field(..., description="Default (English) sample name for the UI drop-down")
    label_cn: Optional[str] = Field(None, description="Optional Chinese override; WebUI picks it when locale is zh-*")
    params: Dict[str, Any] = Field(default_factory=dict, description="Call params, verbatim")
    tags: List[str] = Field(default_factory=list, description="Semantic labels (smoke, ip, …)")
    has_assertion: bool = Field(False, description="Whether the sample declares a pass/fail assertion")


@router.get(
    "/{name}/fixtures",
    response_model=List[FixtureItemResponse],
    summary="List declared test fixtures for a tool",
)
async def list_tool_fixtures(name: str) -> List[FixtureItemResponse]:
    """Return predeclared test samples sourced from the tool's provider ``_test.yaml``.

    Returns an empty list when no manifest exists or no fixtures are declared.
    """
    from flocks.tool.probe_loader import get_tool_fixtures_by_tool_name

    return [
        FixtureItemResponse(
            label=f.label,
            label_cn=f.label_cn,
            params=f.params,
            tags=list(f.tags),
            has_assertion=bool(f.assertion),
        )
        for f in get_tool_fixtures_by_tool_name(name)
    ]


# =============================================================================
# Plugin Tool CRUD Routes
# =============================================================================

class CreateToolRequest(BaseModel):
    """Request to create a YAML plugin tool"""
    name: str = Field(..., description="Tool name (snake_case)")
    description: str = Field("", description="Tool description")
    category: str = Field("custom", description="Tool category")
    provider: Optional[str] = Field(None, description="Provider name for grouping")
    enabled: bool = Field(True, description="Is tool enabled")
    requires_confirmation: bool = Field(False, description="Requires user confirmation")
    inputSchema: Optional[Dict[str, Any]] = Field(None, description="MCP-compatible JSON Schema")
    parameters: Optional[List[Dict[str, Any]]] = Field(None, description="Simplified parameter list")
    handler: Dict[str, Any] = Field(..., description="Handler config (type: http|script)")
    response: Optional[Dict[str, Any]] = Field(None, description="Response processing config")


class UpdateToolRequest(BaseModel):
    """Request to update a YAML plugin tool"""
    description: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    enabled: Optional[bool] = Field(None)
    requires_confirmation: Optional[bool] = Field(None)
    inputSchema: Optional[Dict[str, Any]] = Field(None)
    parameters: Optional[List[Dict[str, Any]]] = Field(None)
    handler: Optional[Dict[str, Any]] = Field(None)
    response: Optional[Dict[str, Any]] = Field(None)


class PluginToolListResponse(BaseModel):
    """Response listing YAML plugin tools"""
    tools: List[Dict[str, Any]] = Field(default_factory=list)


@router.post(
    "",
    response_model=ToolInfoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a YAML plugin tool",
)
async def create_tool(request: CreateToolRequest, _admin: object = Depends(require_admin)):
    """
    Create a new tool via YAML plugin.

    The tool is written to ``~/.flocks/plugins/tools/api/`` (or a provider
    subdirectory ``api/{provider}/`` if specified), then loaded into the
    ToolRegistry immediately.
    """
    from flocks.tool.tool_loader import (
        create_yaml_tool,
        yaml_to_tool,
        TOOL_TYPE_API,
    )

    ToolRegistry.init()

    data: Dict[str, Any] = {
        "name": request.name,
        "description": request.description,
        "category": request.category,
        "enabled": request.enabled,
        "requires_confirmation": request.requires_confirmation,
        "handler": request.handler,
    }
    if request.inputSchema:
        data["inputSchema"] = request.inputSchema
    if request.parameters:
        data["parameters"] = request.parameters
    if request.response:
        data["response"] = request.response
    if request.provider:
        data["provider"] = request.provider

    try:
        yaml_path = create_yaml_tool(data, provider=request.provider, tool_type=TOOL_TYPE_API)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        log.error("tool.create.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

    try:
        tool = yaml_to_tool(data, yaml_path)
        if not tool.info.source:
            tool.info.source = "plugin_yaml"
        if request.provider:
            tool.info.provider = request.provider
        ToolRegistry.register(tool)
        if tool.info.name not in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.append(tool.info.name)
        _invalidate_tool_summary_cache()
    except Exception as e:
        log.error("tool.create.register_error", {"error": str(e), "name": request.name})
        raise HTTPException(
            status_code=500,
            detail=f"Tool file created but failed to register: {e}",
        )

    if request.provider and request.enabled:
        from flocks.server.routes.provider import (
            APIServiceUpdateRequest,
            update_api_service,
        )

        await update_api_service(
            request.provider,
            APIServiceUpdateRequest(enabled=True),
        )

    return _build_tool_response(tool.info)


@router.put(
    "/{name}",
    response_model=ToolInfoResponse,
    summary="Update a YAML plugin tool",
)
async def update_plugin_tool(name: str, request: UpdateToolRequest, _admin: object = Depends(require_admin)):
    """
    Update an existing YAML plugin tool.

    Only YAML-based plugin tools can be updated. Built-in and MCP tools
    cannot be modified through this endpoint.
    """
    from flocks.tool.tool_loader import (
        find_yaml_tool,
        update_yaml_tool,
        yaml_to_tool,
        _read_yaml_raw,
    )

    ToolRegistry.init()

    if not find_yaml_tool(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"YAML plugin tool not found: {name}",
        )

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No updates provided",
        )

    try:
        if not update_yaml_tool(name, updates):
            raise HTTPException(status_code=500, detail=f"Failed to update YAML for tool {name}")
    except HTTPException:
        raise
    except Exception as e:
        log.error("tool.update.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))

    # Reload tool into registry
    try:
        yaml_path = find_yaml_tool(name)
        if yaml_path:
            raw = _read_yaml_raw(yaml_path)
            tool = yaml_to_tool(raw, yaml_path)
            if not tool.info.source:
                tool.info.source = "plugin_yaml"
            ToolRegistry.register(tool)
            _invalidate_tool_summary_cache()
            return _build_tool_response(tool.info)
    except Exception as e:
        log.error("tool.update.reload_error", {"error": str(e), "name": name})

    existing = ToolRegistry.get(name)
    if existing:
        _invalidate_tool_summary_cache()
        return _build_tool_response(existing.info)
    raise HTTPException(status_code=500, detail="Tool updated but reload failed")


@router.delete(
    "/{name}",
    summary="Delete a plugin tool",
)
async def delete_tool(name: str, _admin: object = Depends(require_admin)):
    """
    Delete a plugin tool.

    Supports YAML plugin tools and Python plugin tools. Built-in and MCP
    tools cannot be removed through this endpoint.
    """
    from flocks.tool.tool_loader import delete_yaml_tool, delete_python_tool, find_yaml_tool

    ToolRegistry.init()

    deleted = False
    if find_yaml_tool(name):
        try:
            deleted = delete_yaml_tool(name)
        except Exception as e:
            log.error("tool.delete.error", {"error": str(e), "name": name})
            raise HTTPException(status_code=500, detail=str(e))
    else:
        try:
            deleted = delete_python_tool(name)
        except Exception as e:
            log.error("tool.delete.error", {"error": str(e), "name": name})
            raise HTTPException(status_code=500, detail=str(e))

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin tool not found: {name}",
        )

    # The file deletion is already committed at this point. Keep the Hub
    # installation record consistent even when the in-memory refresh fails.
    cleanup_errors: List[str] = []
    try:
        ToolRegistry.refresh_plugin_tools()
    except Exception as e:
        refresh_error = str(e)
        cleanup_errors.append(f"registry refresh: {refresh_error}")
        log.warning("tool.delete.refresh_failed", {
            "error": refresh_error,
            "name": name,
        })
    _invalidate_tool_summary_cache()

    from flocks.hub import local as hub_local

    try:
        hub_local.remove_installed_record("tool", name)
    except Exception as e:
        hub_error = str(e)
        cleanup_errors.append(f"Hub record cleanup: {hub_error}")
        log.warning("tool.delete.hub_cleanup_failed", {
            "error": hub_error,
            "name": name,
        })

    if cleanup_errors:
        return {
            "status": "partial",
            "message": f"Tool {name} deleted, but cleanup was incomplete",
            "errors": cleanup_errors,
        }

    return {"status": "success", "message": f"Tool {name} deleted"}


@router.post(
    "/{name}/reload",
    response_model=ToolInfoResponse,
    summary="Reload a YAML plugin tool",
)
async def reload_tool(name: str, _admin: object = Depends(require_admin)):
    """
    Hot-reload a single YAML plugin tool.

    Re-reads the YAML file from disk and re-registers the tool
    in the ToolRegistry without restarting the service.
    """
    from flocks.tool.tool_loader import find_yaml_tool, yaml_to_tool, _read_yaml_raw

    ToolRegistry.init()

    yaml_path = find_yaml_tool(name)
    if not yaml_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"YAML plugin tool not found: {name}",
        )

    try:
        raw = _read_yaml_raw(yaml_path)
        tool = yaml_to_tool(raw, yaml_path)
        if not tool.info.source:
            tool.info.source = "plugin_yaml"
        ToolRegistry.register(tool)
        _invalidate_tool_summary_cache()
        log.info("tool.reloaded", {"name": name})
        return _build_tool_response(tool.info)
    except Exception as e:
        log.error("tool.reload.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/plugin/list",
    response_model=PluginToolListResponse,
    summary="List YAML plugin tools",
)
async def list_plugin_tools():
    """
    List all YAML plugin tools with metadata.

    Returns tools discovered from ``~/.flocks/plugins/tools/`` including
    provider subdirectories.
    """
    from flocks.tool.tool_loader import list_yaml_tools

    try:
        tools = list_yaml_tools()
        return PluginToolListResponse(tools=tools)
    except Exception as e:
        log.error("tool.plugin.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))
