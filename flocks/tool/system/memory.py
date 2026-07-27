"""
Memory tools for agents.

Expose memory_search/memory_get/memory_write via ToolRegistry.
"""

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
        workspace_dir=Instance.get_directory() or session.directory,
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
    description="Search project memory using a natural language query.",
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


@ToolRegistry.register_function(
    name="memory_get",
    description="Retrieve memory file content by path, optionally filtered by line range.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="Memory file path relative to memory root.",
            required=True,
        ),
        ToolParameter(
            name="from_line",
            type=ParameterType.INTEGER,
            description="Starting line number (1-based).",
            required=False,
        ),
        ToolParameter(
            name="lines",
            type=ParameterType.INTEGER,
            description="Number of lines to return.",
            required=False,
        ),
    ],
)
async def memory_get_tool(
    ctx: ToolContext,
    path: str,
    from_line: Optional[int] = None,
    lines: Optional[int] = None,
) -> ToolResult:
    memory, err = await _get_session_memory(ctx)
    if err:
        return err

    manager = memory.get_manager()
    if not manager:
        return ToolResult(success=False, error="Memory manager not available")

    try:
        output = await manager.read_file(
            rel_path=path,
            from_line=from_line,
            lines=lines,
        )
        return ToolResult(success=True, output=output)
    except FileNotFoundError:
        return ToolResult(success=False, error=f"File not found: {path}")
    except Exception as e:
        log.error("memory_get.failed", {"path": path, "error": str(e)})
        return ToolResult(success=False, error=f"Memory get failed: {str(e)}")


@ToolRegistry.register_function(
    name="memory_write",
    description="Write content to memory files for long-term recall.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description="Content to write to memory.",
            required=True,
        ),
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="Target path relative to memory root (default: YYYY-MM-DD.md).",
            required=False,
        ),
        ToolParameter(
            name="append",
            type=ParameterType.BOOLEAN,
            description="Append to existing file (default: true).",
            required=False,
        ),
    ],
)
async def memory_write_tool(
    ctx: ToolContext,
    content: str,
    path: Optional[str] = None,
    append: Optional[bool] = True,
) -> ToolResult:
    memory, err = await _get_session_memory(ctx)
    if err:
        return err

    try:
        written_path = await memory.write(
            content=content,
            path=path,
            append=bool(append),
        )
        return ToolResult(
            success=True,
            output={
                "path": written_path,
                "length": len(content),
                "append": bool(append),
            },
        )
    except Exception as e:
        log.error("memory_write.failed", {"error": str(e)})
        return ToolResult(success=False, error=f"Memory write failed: {str(e)}")
