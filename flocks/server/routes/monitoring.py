"""
Lightweight WebUI monitoring routes.

These endpoints back the existing Monitoring page without forcing the page to
poll heavier session/tool list APIs. Detailed historical sampling can be added
later without changing the frontend contract.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

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
    activeSessions: int
    activeAgents: int
    mcpServers: dict[str, str]
    timestamp: int


class MetricsSnapshotResponse(BaseModel):
    timestamp: int
    messageRate: float
    toolCallRate: float
    errorRate: float
    avgResponseTime: float
    activeRequests: int


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


async def _active_session_count() -> int:
    cached_sessions = getattr(Session, "_all_sessions_cache", None)
    if not cached_sessions:
        return 0
    return sum(1 for session in cached_sessions if getattr(session, "status", None) == "active")


async def _active_agent_count() -> int:
    agents = getattr(agent_registry, "_agents_ref", None)
    if not agents:
        return 0
    return sum(1 for agent in agents.values() if not getattr(agent, "hidden", False))


async def _mcp_server_statuses() -> dict[str, str]:
    try:
        statuses = await MCP.status()
    except Exception as exc:
        log.warning("monitoring.mcp.status_failed", {"error": str(exc)})
        return {}
    return {
        name: str(info.status.value if hasattr(info.status, "value") else info.status)
        for name, info in statuses.items()
    }


@router.get("/status", response_model=SystemStatusResponse)
async def get_status() -> SystemStatusResponse:
    return SystemStatusResponse(
        status="healthy",
        uptime=max(0, int(time.monotonic() - _started_at)),
        activeSessions=await _active_session_count(),
        activeAgents=await _active_agent_count(),
        mcpServers=await _mcp_server_statuses(),
        timestamp=_timestamp_ms(),
    )


@router.get("/metrics", response_model=MetricsSnapshotResponse)
async def get_metrics() -> MetricsSnapshotResponse:
    metrics = get_monitor().get_metrics().get("global", {})
    total_calls = float(metrics.get("total_calls") or 0)
    failed_calls = float(metrics.get("failed_parses") or 0)
    return MetricsSnapshotResponse(
        timestamp=_timestamp_ms(),
        messageRate=0.0,
        toolCallRate=total_calls,
        errorRate=failed_calls,
        avgResponseTime=0.0,
        activeRequests=0,
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
