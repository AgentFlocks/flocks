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


@pytest.mark.parametrize("field,value", [("misfirePolicy", "invalid"), ("maxCatchUp", 0),
                                         ("maxCatchUp", 101), ("misfireGraceSeconds", -1)])
async def test_invalid_backlog_policy_rejected_at_create_and_update(client, field, value):
    payload = {"title": "policy validation", "type": "scheduled", "cron": "* * * * *", field: value}
    assert (await client.post("/api/task-schedulers", json=payload)).status_code == 422
    scheduler = await _cron_scheduler()
    assert (await client.put(f"/api/task-schedulers/{scheduler.id}", json={field: value})).status_code == 422


async def test_backlog_policy_create_update_round_trip(client):
    response = await client.post("/api/task-schedulers", json={
        "title": "bounded catch up", "type": "scheduled", "cron": "* * * * *",
        "misfirePolicy": "catch_up", "misfireGraceSeconds": 120, "maxCatchUp": 2,
    })
    assert response.status_code == 201
    scheduler = response.json()
    assert scheduler["trigger"]["misfirePolicy"] == "catch_up"
    assert scheduler["trigger"]["maxCatchUp"] == 2
    response = await client.put(f"/api/task-schedulers/{scheduler['id']}", json={"misfirePolicy": "skip"})
    assert response.status_code == 200
    trigger = response.json()["trigger"]
    assert trigger["misfirePolicy"] == "skip" and trigger["maxCatchUp"] == 2
    assert trigger["misfireGraceSeconds"] == 120
