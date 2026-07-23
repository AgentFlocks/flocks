from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import pytest

from flocks.server.routes import workflow as workflow_routes


def _workflow_data(workflow_id: str) -> dict[str, Any]:
    return {
        "id": workflow_id,
        "name": workflow_id,
        "category": "default",
        "workflowJson": {"start": "n1", "nodes": [], "edges": []},
        "status": "active",
        "source": "global",
        "createdAt": 1,
        "updatedAt": 2,
    }


async def _noop_migrate() -> None:
    return None


async def _zero_stats(_workflow_id: str) -> dict[str, Any]:
    return {"callCount": 0}


async def _unconfigured_status(
    _workflow_id: str,
    _workflow_data: dict[str, Any],
) -> workflow_routes.WorkflowIntegrationStatusResponse:
    return workflow_routes.WorkflowIntegrationStatusResponse(
        api=workflow_routes.WorkflowCapabilityStatusResponse(configured=False, state="unconfigured"),
        trigger=workflow_routes.WorkflowTriggerCapabilityStatusResponse(
            configured=False,
            state="unconfigured",
        ),
    )


def _patch_list_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    workflows: list[dict[str, Any]],
    *,
    stats: Callable[[str], Awaitable[dict[str, Any]]] = _zero_stats,
) -> None:
    monkeypatch.setattr(workflow_routes, "_migrate_storage_to_filesystem", _noop_migrate)
    monkeypatch.setattr(workflow_routes, "_list_workflows_from_fs", lambda: workflows)
    monkeypatch.setattr(workflow_routes, "_get_workflow_stats", stats)
    monkeypatch.setattr(workflow_routes, "_get_workflow_integration_status", _unconfigured_status)


@pytest.mark.asyncio
async def test_list_workflow_summaries_includes_integration_status_without_full_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_data("wf-list")
    workflow["name"] = "Listed Workflow"
    _patch_list_dependencies(monkeypatch, [workflow])

    async def running_status(
        _workflow_id: str,
        _workflow_data: dict[str, Any],
    ) -> workflow_routes.WorkflowIntegrationStatusResponse:
        return workflow_routes.WorkflowIntegrationStatusResponse(
            api=workflow_routes.WorkflowCapabilityStatusResponse(configured=True, state="running"),
            trigger=workflow_routes.WorkflowTriggerCapabilityStatusResponse(
                configured=False,
                state="unconfigured",
            ),
        )

    monkeypatch.setattr(workflow_routes, "_get_workflow_integration_status", running_status)

    items = await workflow_routes.list_workflow_summaries(category=None, status=None, exclude_id=None)

    assert items[0].integrationStatus.model_dump() == {
        "api": {"configured": True, "state": "running"},
        "trigger": {"configured": False, "state": "unconfigured", "count": 0, "items": []},
    }
    assert items[0].nodeCount == 0
    assert not hasattr(items[0], "workflowJson")


@pytest.mark.asyncio
async def test_list_workflow_summaries_enriches_multiple_items_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def blocking_stats(_workflow_id: str) -> dict[str, Any]:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return {"callCount": 0}

    _patch_list_dependencies(
        monkeypatch,
        [_workflow_data("wf-one"), _workflow_data("wf-two")],
        stats=blocking_stats,
    )

    list_task = asyncio.create_task(
        workflow_routes.list_workflow_summaries(category=None, status=None, exclude_id=None)
    )
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()

    items = await list_task

    assert {item.id for item in items} == {"wf-one", "wf-two"}


@pytest.mark.asyncio
async def test_full_workflow_list_does_not_load_integration_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_data("wf-full")
    workflow["name"] = "Full Workflow"
    _patch_list_dependencies(monkeypatch, [workflow])

    async def unexpected_integration_status(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("full workflow list must not load card integration status")

    monkeypatch.setattr(workflow_routes, "_get_workflow_integration_status", unexpected_integration_status)

    items = await workflow_routes.list_workflows(category=None, status=None, exclude_id=None)

    assert items[0].id == "wf-full"
    assert items[0].integrationStatus is None
