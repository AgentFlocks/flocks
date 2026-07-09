"""
Lightweight WebUI summary stats.

The home page needs counts, not the full agent/workflow/skill/tool payloads.
Keeping this aggregation on the server avoids serialising and parsing large
lists on every first paint.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from flocks.agent.registry import Agent
from flocks.server.routes.provider import list_providers
from flocks.server.routes.workflow import _list_workflows_from_fs, _migrate_storage_to_filesystem
from flocks.skill.skill import Skill
from flocks.task.manager import TaskManager
from flocks.tool.registry import ToolRegistry
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="stats-routes")


class CountResponse(BaseModel):
    total: int


class TaskStatsResponse(BaseModel):
    week: int
    scheduledActive: int


class SystemHealthResponse(BaseModel):
    status: Literal["healthy", "warning", "error"]
    message: str


class SystemStatsResponse(BaseModel):
    tasks: TaskStatsResponse
    agents: CountResponse
    workflows: CountResponse
    skills: CountResponse
    tools: CountResponse
    models: CountResponse
    system: SystemHealthResponse


async def _safe_count(name: str, loader: Callable[[], Awaitable[int]], failures: list[str]) -> int:
    try:
        return await loader()
    except Exception as exc:
        failures.append(name)
        log.warning("stats.summary.source_failed", {"source": name, "error": str(exc)})
        return 0


def _should_count_agent(agent: Any) -> bool:
    if getattr(agent, "hidden", False):
        return False
    if getattr(agent, "mode", None) == "primary":
        return True
    tags = getattr(agent, "tags", None)
    return not isinstance(tags, list) or "system" not in tags


async def _task_dashboard() -> dict[str, Any]:
    return await TaskManager.dashboard()


async def _safe_dashboard(failures: list[str]) -> dict[str, Any]:
    try:
        return await _task_dashboard()
    except Exception as exc:
        failures.append("tasks")
        log.warning("stats.summary.source_failed", {"source": "tasks", "error": str(exc)})
        return {}


async def _count_agents() -> int:
    agents = await Agent.list()
    return sum(1 for agent in agents if _should_count_agent(agent))


async def _count_workflows() -> int:
    await _migrate_storage_to_filesystem()
    return len(_list_workflows_from_fs())


async def _count_skills() -> int:
    skills = await Skill.all()
    return sum(1 for skill in skills if getattr(skill, "category", None) != "system")


async def _count_tools() -> int:
    await ToolRegistry.init_async()
    return len(ToolRegistry.all_tool_ids())


async def _count_models() -> int:
    providers = await list_providers()
    connected = set(providers.connected or [])
    return sum(
        len(provider.models or {})
        for provider in providers.all
        if provider.id in connected
    )


@router.get("/summary", response_model=SystemStatsResponse)
async def get_system_stats_summary() -> SystemStatsResponse:
    failures: list[str] = []
    dashboard, agents, workflows, skills, tools, models = await asyncio.gather(
        _safe_dashboard(failures),
        _safe_count("agents", _count_agents, failures),
        _safe_count("workflows", _count_workflows, failures),
        _safe_count("skills", _count_skills, failures),
        _safe_count("tools", _count_tools, failures),
        _safe_count("models", _count_models, failures),
    )

    system_status: Literal["healthy", "warning"] = "warning" if failures else "healthy"
    message = "部分统计加载失败" if failures else "所有服务运行正常"

    return SystemStatsResponse(
        tasks=TaskStatsResponse(
            week=int(dashboard.get("completed_week") or 0) + int(dashboard.get("failed_week") or 0),
            scheduledActive=int(dashboard.get("scheduled_active") or 0),
        ),
        agents=CountResponse(total=agents),
        workflows=CountResponse(total=workflows),
        skills=CountResponse(total=skills),
        tools=CountResponse(total=tools),
        models=CountResponse(total=models),
        system=SystemHealthResponse(status=system_status, message=message),
    )
