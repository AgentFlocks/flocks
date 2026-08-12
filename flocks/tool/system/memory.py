"""Persistent Memory search for agents."""

from typing import Dict, List, Optional

from flocks.tool.registry import (
    ToolRegistry,
    ToolCategory,
    ToolParameter,
    ParameterType,
    ToolResult,
    ToolContext,
)
from flocks.session import Session
from flocks.session.features.memory import SessionMemory
from flocks.memory.types import MemorySource
from flocks.utils.log import Log

log = Log.create(service="tool.memory")

_session_memory_cache: Dict[str, SessionMemory] = {}


async def _get_session_memory(ctx: ToolContext) -> tuple[Optional[SessionMemory], Optional[ToolResult]]:
    """Resolve and initialize SessionMemory for the current context.

    Returns (memory, None) on success, or (None, error_result) on failure.
    Reuses cached SessionMemory instances keyed by session_id.
    """
    cached = _session_memory_cache.get(ctx.session_id)
    if cached and cached._initialized:
        return cached, None

    session = await Session.get_by_id(ctx.session_id)
    if not session:
        return None, ToolResult(success=False, error="Session not found")

    from flocks.project.instance import Instance

    memory = SessionMemory(
        session_id=session.id,
        project_id=session.project_id,
        workspace_dir=(
            session.directory
            or ctx.extra.get("workspace_dir")
            or Instance.get_directory()
            or "."
        ),
        enabled=session.memory_enabled,
    )

    if not memory.enabled:
        return None, ToolResult(success=False, error="Memory is disabled for this session")

    ok = await memory.initialize()
    if not ok:
        return None, ToolResult(success=False, error="Memory initialization failed")

    _session_memory_cache[ctx.session_id] = memory
    return memory, None


def evict_session_memory(session_id: str) -> None:
    """Remove a cached SessionMemory entry (call on session close)."""
    _session_memory_cache.pop(session_id, None)


@ToolRegistry.register_function(
    name="memory_search",
    description=(
        "Search USER, Global, Daily, and current Project Memory, plus optional "
        "readable Session History from the current project."
    ),
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description="Natural language search query.",
            required=True,
        ),
        ToolParameter(
            name="max_results",
            type=ParameterType.INTEGER,
            description="Maximum number of results to return (default: 6).",
            required=False,
        ),
        ToolParameter(
            name="min_score",
            type=ParameterType.NUMBER,
            description="Minimum similarity score 0-1 (default: 0.35).",
            required=False,
        ),
        ToolParameter(
            name="sources",
            type=ParameterType.ARRAY,
            description="Sources to search: ['memory', 'session'] (default: ['memory']).",
            required=False,
        ),
    ],
)
async def memory_search_tool(
    ctx: ToolContext,
    query: str,
    max_results: Optional[int] = None,
    min_score: Optional[float] = None,
    sources: Optional[List[str]] = None,
) -> ToolResult:
    memory, err = await _get_session_memory(ctx)
    if err:
        return err

    try:
        source_enums: Optional[List[MemorySource]] = None
        if sources:
            source_enums = [MemorySource(s) for s in sources]

        results = await memory.search(
            query=query,
            max_results=max_results,
            min_score=min_score,
            sources=source_enums,
        )

        formatted = [
            {
                "path": r.path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "score": round(r.score, 4),
                "snippet": r.snippet,
                "source": r.source.value,
                "citation": r.citation,
            }
            for r in results
        ]

        return ToolResult(
            success=True,
            output={
                "results": formatted,
                "count": len(formatted),
                "query": query,
            },
        )
    except Exception as e:
        log.error("memory_search.failed", {"error": str(e)})
        return ToolResult(success=False, error=f"Memory search failed: {str(e)}")
