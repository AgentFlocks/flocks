from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def webui_perf_client() -> AsyncGenerator[AsyncClient, None]:
    from flocks.server.routes.monitoring import router as monitoring_router
    from flocks.server.routes.permission import router as permission_router
    from flocks.server.routes.stats import router as stats_router

    app = FastAPI()
    app.include_router(stats_router, prefix="/api/stats")
    app.include_router(monitoring_router, prefix="/api/monitoring")
    app.include_router(permission_router, prefix="/api/permission")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_stats_summary_returns_lightweight_counts(
    webui_perf_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import stats as stats_routes

    async def _task_dashboard():
        return {"completed_week": 2, "failed_week": 1, "scheduled_active": 4}

    async def _count_agents():
        return 5

    async def _count_workflows():
        return 6

    async def _count_skills():
        return 7

    async def _count_tools():
        return 8

    async def _count_models():
        return 9

    monkeypatch.setattr(stats_routes, "_task_dashboard", _task_dashboard)
    monkeypatch.setattr(stats_routes, "_count_agents", _count_agents)
    monkeypatch.setattr(stats_routes, "_count_workflows", _count_workflows)
    monkeypatch.setattr(stats_routes, "_count_skills", _count_skills)
    monkeypatch.setattr(stats_routes, "_count_tools", _count_tools)
    monkeypatch.setattr(stats_routes, "_count_models", _count_models)

    response = await webui_perf_client.get("/api/stats/summary")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {
        "tasks": {"week": 3, "scheduledActive": 4},
        "agents": {"total": 5},
        "workflows": {"total": 6},
        "skills": {"total": 7},
        "tools": {"total": 8},
        "models": {"total": 9},
        "system": {"status": "healthy", "message": "所有服务运行正常"},
    }


async def test_monitoring_routes_back_existing_webui_calls(webui_perf_client: AsyncClient):
    status_response = await webui_perf_client.get("/api/monitoring/status")
    metrics_response = await webui_perf_client.get("/api/monitoring/metrics")
    llm_response = await webui_perf_client.get("/api/monitoring/performance/llm")
    tool_response = await webui_perf_client.get("/api/monitoring/performance/tool")

    assert status_response.status_code == status.HTTP_200_OK, status_response.text
    assert metrics_response.status_code == status.HTTP_200_OK, metrics_response.text
    assert llm_response.status_code == status.HTTP_200_OK, llm_response.text
    assert tool_response.status_code == status.HTTP_200_OK, tool_response.text

    status_payload = status_response.json()
    assert status_payload["status"] == "healthy"
    assert "uptime" in status_payload
    assert "mcpServers" in status_payload

    metrics_payload = metrics_response.json()
    assert "messageRate" in metrics_payload
    assert "toolCallRate" in metrics_payload
    assert llm_response.json() == []
    assert tool_response.json() == []


async def test_api_permission_alias_matches_frontend_route(webui_perf_client: AsyncClient):
    response = await webui_perf_client.get("/api/permission")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == []
