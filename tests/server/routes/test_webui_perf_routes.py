from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def webui_perf_client() -> AsyncGenerator[AsyncClient, None]:
    from flocks.server.routes.monitoring import router as monitoring_router
    from flocks.server.routes.permission import router as permission_router
    from flocks.server.routes.stats import router as stats_router

    app = FastAPI()
    app.dependency_overrides[require_user] = lambda: AuthUser(
        id="webui-perf-user",
        username="webui-perf-user",
        role="member",
        status="active",
        must_reset_password=False,
    )
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


async def test_stats_summary_starts_independent_sources_concurrently(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import stats as stats_routes

    started: set[str] = set()
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def _count(name: str, value):
        started.add(name)
        if len(started) == 6:
            all_started.set()
        await release.wait()
        return value

    async def _task_dashboard():
        return await _count("dashboard", {})

    async def _count_tools():
        return await _count("tools", 8)

    async def _count_skills():
        return await _count("skills", 7)

    async def _count_agents():
        return await _count("agents", 5)

    async def _count_workflows():
        return await _count("workflows", 6)

    async def _count_models():
        return await _count("models", 9)

    monkeypatch.setattr(stats_routes, "_task_dashboard", _task_dashboard)
    monkeypatch.setattr(stats_routes, "_count_tools", _count_tools)
    monkeypatch.setattr(stats_routes, "_count_skills", _count_skills)
    monkeypatch.setattr(stats_routes, "_count_agents", _count_agents)
    monkeypatch.setattr(stats_routes, "_count_workflows", _count_workflows)
    monkeypatch.setattr(stats_routes, "_count_models", _count_models)

    summary_task = asyncio.create_task(stats_routes.get_system_stats_summary())
    await asyncio.wait_for(all_started.wait(), timeout=1)
    release.set()
    response = await summary_task

    assert started == {"dashboard", "agents", "workflows", "skills", "tools", "models"}
    assert response.tools.total == 8
    assert response.skills.total == 7
    assert response.agents.total == 5


async def test_skill_discovery_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import stats as stats_routes
    from flocks.skill.skill import Skill

    def slow_discover():
        time.sleep(0.06)
        return {"demo": SimpleNamespace(name="demo", category="user")}

    monkeypatch.setattr(Skill, "_discover", slow_discover)
    Skill.clear_cache()
    count_task = asyncio.create_task(stats_routes._count_skills())
    heartbeat_ticks = 0
    while not count_task.done():
        await asyncio.sleep(0.005)
        heartbeat_ticks += 1

    assert await count_task == 1
    assert heartbeat_ticks >= 3
    Skill.clear_cache()


async def test_skill_invalidation_during_discovery_cannot_restore_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.skill.skill import Skill

    discovery_started = threading.Event()
    release_first_discovery = threading.Event()
    discovery_count = 0

    def discover():
        nonlocal discovery_count
        discovery_count += 1
        if discovery_count == 1:
            discovery_started.set()
            assert release_first_discovery.wait(timeout=1)
            name = "stale"
        else:
            name = "fresh"
        return {name: SimpleNamespace(name=name, category="user")}

    monkeypatch.setattr(Skill, "_discover", discover)
    Skill.clear_cache()
    load_task = asyncio.create_task(Skill.all())
    assert await asyncio.to_thread(discovery_started.wait, 1)
    Skill.clear_cache()
    release_first_discovery.set()

    skills = await load_task

    assert [skill.name for skill in skills] == ["fresh"]
    assert list((Skill._cache or {}).keys()) == ["fresh"]
    Skill.clear_cache()


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
    assert metrics_payload["messageRate"] is None
    assert metrics_payload["avgResponseTime"] is None
    assert metrics_payload["activeRequests"] is None
    assert llm_response.json() == []
    assert tool_response.json() == []


async def test_monitoring_tool_rates_use_counter_deltas(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import monitoring as monitoring_routes

    samples = iter([100.0, 101.0, 160.0, 161.0])
    monkeypatch.setattr(monitoring_routes, "_metrics_clock", lambda: next(samples))
    monkeypatch.setattr(monitoring_routes, "_metrics_rate_state", None)

    assert monitoring_routes._sample_tool_rates(10, 2) == (None, None)
    assert monitoring_routes._sample_tool_rates(11, 2) == (None, None)
    assert monitoring_routes._sample_tool_rates(15, 3) == (5.0, 1.0)
    # Additional clients inside the same window see the same sample instead
    # of advancing the global baseline and distorting each other's rates.
    assert monitoring_routes._sample_tool_rates(16, 3) == (5.0, 1.0)


async def test_api_permission_alias_matches_frontend_route(webui_perf_client: AsyncClient):
    response = await webui_perf_client.get("/api/permission")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == []
