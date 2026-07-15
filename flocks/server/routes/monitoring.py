"""
Lightweight WebUI monitoring routes.

These endpoints back the existing Monitoring page without forcing the page to
poll heavier session/tool list APIs. Detailed historical sampling can be added
later without changing the frontend contract.
"""

from __future__ import annotations

import time
import threading
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from flocks.agent import registry as agent_registry
from flocks.mcp import MCP
from flocks.session.session import Session
from flocks.utils.log import Log
from flocks.utils.monitor import get_monitor

router = APIRouter()
log = Log.create(service="monitoring-routes")
_started_at = time.monotonic()


class SystemStatusResponse(BaseModel):
    status: Literal["healthy", "degraded", "down"]
    uptime: int
    activeSessions: int | None
    activeAgents: int | None
    mcpServers: dict[str, str]
    timestamp: int


class MetricsSnapshotResponse(BaseModel):
    timestamp: int
    messageRate: float | None = Field(
        default=None,
        description="Messages per minute. None because message throughput is not collected yet.",
    )
    toolCallRate: float | None = Field(
        default=None,
        description="Parsed tool calls per minute.",
    )
    errorRate: float | None = Field(
        default=None,
        description="System-wide error rate. None until a global error metric is available.",
    )
    toolParseFailureRate: float | None = Field(
        default=None,
        description="Failed tool-call parses per minute.",
    )
    avgResponseTime: float | None = Field(
        default=None,
        description="Average response time in milliseconds. None until latency is collected.",
    )
    activeRequests: int | None = Field(
        default=None,
        description="Active request count. None until request concurrency is collected.",
    )


class PerformanceDataResponse(BaseModel):
    category: Literal["llm", "tool", "api"]
    name: str
    avgDuration: float
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    count: int
    errors: int


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


async def _active_session_count() -> int | None:
    cached_sessions = getattr(Session, "_all_sessions_cache", None)
    if cached_sessions is None:
        return None
    return sum(1 for session in cached_sessions if getattr(session, "status", None) == "active")


async def _active_agent_count() -> int | None:
    agents = getattr(agent_registry, "_agents_ref", None)
    if agents is None:
        return None
    return sum(1 for agent in agents.values() if not getattr(agent, "hidden", False))


async def _mcp_server_statuses() -> tuple[dict[str, str], bool]:
    try:
        statuses, initialized = MCP.status_snapshot()
    except Exception as exc:
        log.warning("monitoring.mcp.status_failed", {"error": str(exc)})
        return {}, False
    return ({
        name: str(info.status.value if hasattr(info.status, "value") else info.status)
        for name, info in statuses.items()
    }, initialized)


_metrics_sample_lock = threading.Lock()
_METRICS_RATE_WINDOW_SECONDS = 60.0
_metrics_rate_state: tuple[
    float,
    float,
    float,
    float | None,
    float | None,
] | None = None


def _metrics_clock() -> float:
    return time.monotonic()


def _sample_tool_rates(total_calls: float, failed_calls: float) -> tuple[float | None, float | None]:
    """Return a stable process-wide rate snapshot for the current time window."""
    global _metrics_rate_state

    now = _metrics_clock()
    with _metrics_sample_lock:
        state = _metrics_rate_state
        if state is None:
            _metrics_rate_state = (now, total_calls, failed_calls, None, None)
            return None, None

        sampled_at, previous_calls, previous_failures, call_rate, failure_rate = state
        elapsed_seconds = now - sampled_at
        counters_reset = total_calls < previous_calls or failed_calls < previous_failures
        if counters_reset:
            _metrics_rate_state = (now, total_calls, failed_calls, None, None)
            return None, None
        if elapsed_seconds < _METRICS_RATE_WINDOW_SECONDS:
            return call_rate, failure_rate

        elapsed_minutes = elapsed_seconds / 60
        call_rate = (total_calls - previous_calls) / elapsed_minutes
        failure_rate = (failed_calls - previous_failures) / elapsed_minutes
        _metrics_rate_state = (now, total_calls, failed_calls, call_rate, failure_rate)
        return call_rate, failure_rate


@router.get("/status", response_model=SystemStatusResponse)
async def get_status() -> SystemStatusResponse:
    mcp_servers, mcp_status_available = await _mcp_server_statuses()
    unhealthy_mcp = any(
        status.strip().lower() not in {"connected", "disabled"}
        for status in mcp_servers.values()
    )
    return SystemStatusResponse(
        status="degraded" if not mcp_status_available or unhealthy_mcp else "healthy",
        uptime=max(0, int(time.monotonic() - _started_at)),
        activeSessions=await _active_session_count(),
        activeAgents=await _active_agent_count(),
        mcpServers=mcp_servers,
        timestamp=_timestamp_ms(),
    )


@router.get("/metrics", response_model=MetricsSnapshotResponse)
async def get_metrics() -> MetricsSnapshotResponse:
    metrics = get_monitor().get_metrics().get("global", {})
    total_calls = float(metrics.get("total_calls") or 0)
    failed_calls = float(metrics.get("failed_parses") or 0)
    tool_call_rate, tool_parse_failure_rate = _sample_tool_rates(total_calls, failed_calls)
    return MetricsSnapshotResponse(
        timestamp=_timestamp_ms(),
        messageRate=None,
        toolCallRate=tool_call_rate,
        errorRate=None,
        toolParseFailureRate=tool_parse_failure_rate,
        avgResponseTime=None,
        activeRequests=None,
    )


@router.get("/metrics/history")
async def get_metrics_history(
    duration: int = Query(300, ge=1),
    interval: int = Query(5, ge=1),
) -> dict[str, Any]:
    return {"duration": duration, "interval": interval, "items": []}


@router.get("/performance", response_model=list[PerformanceDataResponse])
async def get_performance(
    category: Literal["llm", "tool", "api"] | None = None,
) -> list[PerformanceDataResponse]:
    return []


@router.get("/performance/llm", response_model=list[PerformanceDataResponse])
async def get_llm_performance() -> list[PerformanceDataResponse]:
    return []


@router.get("/performance/tool", response_model=list[PerformanceDataResponse])
async def get_tool_performance() -> list[PerformanceDataResponse]:
    return []


@router.get("/api-stats")
async def get_api_stats() -> dict[str, Any]:
    return {"items": []}


@router.get("/api-stats/history")
async def get_api_stats_history(duration: int = Query(300, ge=1)) -> dict[str, Any]:
    return {"duration": duration, "items": []}


@router.get("/events")
async def get_events(
    level: Literal["info", "warn", "error"] | None = None,
    service: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return []
