"""Regression tests for task scheduler/execution route validation.

Covers: blank title rejected, unknown timezone rejected, partial trigger
updates keep the existing cron, and cancel/retry on the wrong state answer 409
instead of silently returning the untouched record.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from flocks.task.manager import TaskManager
from flocks.task.models import (
    ExecutionTriggerType,
    SchedulerMode,
    TaskStatus,
    TaskTrigger,
)
from flocks.task.store import TaskStore


async def _cron_scheduler():
    return await TaskManager.create_scheduler(
        title="cron",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 6 * * *", timezone="Asia/Shanghai"),
    )


@pytest.mark.asyncio
async def test_create_scheduler_rejects_blank_title(client):
    for title in ("", "   "):
        response = await client.post("/api/task-schedulers", json={"title": title, "type": "queued"})
        assert response.status_code == 422, response.text
        assert "title" in response.text


@pytest.mark.asyncio
async def test_update_scheduler_rejects_blank_title(client):
    scheduler = await _cron_scheduler()
    response = await client.put(f"/api/task-schedulers/{scheduler.id}", json={"title": " "})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_create_scheduler_rejects_unknown_timezone(client):
    response = await client.post(
        "/api/task-schedulers",
        json={"title": "tz", "type": "scheduled", "cron": "0 6 * * *", "timezone": "Mars/Olympus"},
    )
    assert response.status_code == 422, response.text
    assert "Invalid timezone" in response.text


@pytest.mark.asyncio
async def test_update_scheduler_rejects_unknown_timezone(client):
    scheduler = await _cron_scheduler()
    response = await client.put(f"/api/task-schedulers/{scheduler.id}", json={"timezone": "Nowhere/Town"})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_update_scheduler_partial_trigger_keeps_existing_cron(client):
    scheduler = await _cron_scheduler()

    # runOnce=false without re-sending cron used to answer 422.
    response = await client.put(f"/api/task-schedulers/{scheduler.id}", json={"runOnce": False})
    assert response.status_code == 200, response.text
    assert response.json()["trigger"]["cron"] == "0 6 * * *"
    assert response.json()["mode"] == "cron"

    # timezone-only update recomputes nextRun in the new zone.
    response = await client.put(f"/api/task-schedulers/{scheduler.id}", json={"timezone": "UTC"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trigger"]["timezone"] == "UTC"
    assert body["trigger"]["cron"] == "0 6 * * *"
    next_run = datetime.fromisoformat(body["trigger"]["nextRun"].replace("Z", "+00:00"))
    assert next_run.astimezone(timezone.utc).strftime("%H:%M") == "06:00"


@pytest.mark.asyncio
async def test_cancel_finished_execution_returns_409(client):
    scheduler = await _cron_scheduler()
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False
    )
    execution.status = TaskStatus.COMPLETED
    execution.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(execution)

    response = await client.post(f"/api/task-executions/{execution.id}/cancel")
    assert response.status_code == 409, response.text
    stored = await TaskManager.get_execution(execution.id)
    assert stored is not None and stored.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_retry_non_failed_execution_returns_409(client):
    scheduler = await _cron_scheduler()
    execution = await TaskManager.create_execution_from_scheduler(
        scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False
    )
    execution.status = TaskStatus.COMPLETED
    execution.completed_at = datetime.now(timezone.utc)
    await TaskStore.update_execution(execution)

    response = await client.post(f"/api/task-executions/{execution.id}/retry")
    assert response.status_code == 409, response.text

    execution.status = TaskStatus.FAILED
    await TaskStore.update_execution(execution)
    response = await client.post(f"/api/task-executions/{execution.id}/retry")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
