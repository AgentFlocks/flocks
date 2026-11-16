from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from flocks.server.routes import workflow as workflow_routes


@pytest.mark.asyncio
async def test_save_poller_config_restarts_manager(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict[str, Any]]] = []

    async def _fake_put_config(workflow_id: str, config: dict[str, Any], *, kind: str | None = None) -> None:
        writes.append((f"{kind}/{workflow_id}", config))

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        assert workflow_id == "wf-1"
        return {"workflowId": workflow_id, "state": "running", "lastStatus": None}

    async def _fake_persist(
        _workflow_id: str,
        workflow_data: dict[str, Any],
        _triggers: list[Any],
    ) -> dict[str, Any]:
        return workflow_data

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: (
            {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None
        ),
    )
    monkeypatch.setattr(workflow_routes.WorkflowStore, "put_config", _fake_put_config)
    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(restart_workflow=_fake_restart),
    )

    response = await client.post(
        "/api/workflow/wf-1/poller-config",
        json={
            "enabled": True,
            "intervalSeconds": 45,
            "timeoutSeconds": 3600,
            "noOverlap": True,
            "inputs": {"persist_triage_output": True},
        },
    )

    assert response.status_code == 200, response.text
    poller_writes = [(key, value) for key, value in writes if key == "workflow_poller_config/wf-1"]
    assert poller_writes
    key, payload = poller_writes[0]
    assert key == "workflow_poller_config/wf-1"
    assert payload["enabled"] is True
    assert payload["intervalSeconds"] == 45
    assert payload["timeoutSeconds"] == 3600
    assert payload["inputs"] == {"persist_triage_output": True}


@pytest.mark.asyncio
async def test_save_poller_config_preserves_cron_schedule(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict[str, Any]]] = []
    persisted_sources: list[dict[str, Any]] = []

    async def _fake_put_config(workflow_id: str, config: dict[str, Any], *, kind: str | None = None) -> None:
        writes.append((f"{kind}/{workflow_id}", config))

    async def _fake_persist(
        _workflow_id: str,
        _workflow_data: dict[str, Any],
        triggers: list[Any],
    ) -> None:
        persisted_sources.extend(dict(trigger.source or {}) for trigger in triggers if trigger.type == "schedule")

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        assert workflow_id == "wf-1"
        return {"workflowId": workflow_id, "state": "running", "cronExpression": "*/10 * * * *"}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: (
            {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None
        ),
    )
    monkeypatch.setattr(workflow_routes.WorkflowStore, "put_config", _fake_put_config)
    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(restart_workflow=_fake_restart),
    )

    response = await client.post(
        "/api/workflow/wf-1/poller-config",
        json={
            "enabled": True,
            "intervalSeconds": 300,
            "cronExpression": "*/10 * * * *",
            "timeoutSeconds": 3600,
            "noOverlap": True,
            "inputs": {"source": "cron"},
        },
    )

    assert response.status_code == 200, response.text
    poller_payload = next(value for key, value in writes if key == "workflow_poller_config/wf-1")
    assert poller_payload["cronExpression"] == "*/10 * * * *"
    assert persisted_sources == [
        {
            "mode": "cron",
            "intervalSeconds": 300,
            "cron": "*/10 * * * *",
        }
    ]


@pytest.mark.asyncio
async def test_get_poller_config_returns_saved_data(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_config(workflow_id: str, *, kind: str = "workflow.integration-config") -> dict[str, Any] | None:
        if workflow_id != "wf-1" or kind != "workflow_poller_config":
            return None
        return {
            "workflowId": "wf-1",
            "enabled": True,
            "intervalSeconds": 30,
            "timeoutSeconds": 7200,
            "noOverlap": True,
            "inputs": {"dedup_source_workflow_name": "stream_alert_denoise_gt_fast"},
        }

    monkeypatch.setattr(workflow_routes.WorkflowStore, "get_config", _fake_get_config)

    response = await client.get("/api/workflow/wf-1/poller-config")
    assert response.status_code == 200, response.text
    assert response.json()["workflowId"] == "wf-1"
    assert response.json()["intervalSeconds"] == 30


@pytest.mark.asyncio
async def test_get_poller_status_returns_runtime_snapshot(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(
            get_status=lambda workflow_id: {
                "workflowId": workflow_id,
                "state": "running",
                "lastStatus": "success",
                "selectedCount": 12,
            },
        ),
    )

    response = await client.get("/api/workflow/wf-1/poller-status")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert response.json()["selectedCount"] == 12


@pytest.mark.asyncio
async def test_run_poller_once_returns_latest_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run_once(workflow_id: str) -> dict[str, Any]:
        return {
            "workflowId": workflow_id,
            "state": "stopped",
            "lastStatus": "success",
            "selectedCount": 5,
        }

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: (
            {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None
        ),
    )
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(run_once=_fake_run_once),
    )

    response = await client.post("/api/workflow/wf-1/poller-run-once")
    assert response.status_code == 200, response.text
    assert response.json()["status"]["lastStatus"] == "success"
    assert response.json()["status"]["selectedCount"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "trigger_type", "manager_path", "body"),
    [
        ("poller-config", "schedule", "flocks.workflow.poller_manager.default_manager", {"enabled": True, "intervalSeconds": 60}),
        ("kafka-config", "kafka", "flocks.ingest.kafka.manager.default_manager", {"enabled": False, "inputTopic": "alerts"}),
        ("syslog-config", "syslog", "flocks.ingest.syslog.manager.default_manager", {"enabled": False, "port": 5514}),
    ],
)
async def test_legacy_config_save_edits_the_existing_trigger_in_place(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    trigger_type: str,
    manager_path: str,
    body: dict[str, Any],
) -> None:
    """A custom trigger id survives the legacy save path instead of a 409 (FL-BUG-009)."""
    persisted: list[list[Any]] = []
    custom_id = f"autotest-{trigger_type}"

    async def _fake_put_config(workflow_id: str, config: dict[str, Any], *, kind: str | None = None) -> None:
        return None

    async def _fake_persist(_workflow_id: str, workflow_data: dict[str, Any], triggers: list[Any]) -> dict[str, Any]:
        persisted.append(list(triggers))
        return workflow_data

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        return {"workflowId": workflow_id, "state": "running"}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "workflowJson": {
                "start": "n1",
                "nodes": [],
                "edges": [],
                "triggers": [{"id": custom_id, "type": trigger_type, "enabled": True}],
            }
        },
    )
    monkeypatch.setattr(workflow_routes.WorkflowStore, "put_config", _fake_put_config)
    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(manager_path, SimpleNamespace(restart_workflow=_fake_restart))

    response = await client.post(f"/api/workflow/wf-1/{endpoint}", json=body)

    assert response.status_code == 200, response.text
    same_type = [trigger for trigger in persisted[0] if trigger.type == trigger_type]
    assert [trigger.id for trigger in same_type] == [custom_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "trigger_type", "manager_path", "settings"),
    [
        ("kafka-config", "kafka", "flocks.ingest.kafka.manager.default_manager", {"inputTopic": "alerts"}),
        ("poller-config", "schedule", "flocks.workflow.poller_manager.default_manager", {"intervalSeconds": 60}),
        ("syslog-config", "syslog", "flocks.ingest.syslog.manager.default_manager", {"port": 5514}),
    ],
)
@pytest.mark.parametrize(
    "concurrency",
    [
        None,
        {"policy": "allow", "maxParallel": 6, "queueSize": 1000},
        {"policy": "queue", "maxParallel": 3, "queueSize": 250},
    ],
)
async def test_legacy_config_stop_start_preserves_concurrency(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    trigger_type: str,
    manager_path: str,
    settings: dict[str, Any],
    concurrency: dict[str, Any] | None,
) -> None:
    from flocks.workflow.triggers.models import TriggerDefinition

    expected = concurrency or {"policy": "allow", "maxParallel": 1, "queueSize": 100}
    trigger_id = f"custom-{trigger_type}"
    preserved_fields = {
        "name": "Alert input",
        "description": "Keep fields absent from the config form",
        "filter": {"expr": "$.body.severity == 'high'"},
        "testSamples": [{"name": "sample", "payload": {"severity": "high"}, "headers": {}, "query": {}}],
    }
    triggers = [] if concurrency is None else [
        {"id": trigger_id, "type": trigger_type, "enabled": True,
         "concurrency": concurrency, **preserved_fields}
    ]
    data = {"workflowJson": {
        "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
        "edges": [],
        "triggers": triggers,
    }}
    restart_states: list[bool] = []

    def _fake_write(workflow_id: str, workflow_json: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        # Exercise real trigger serialization/persistence, then reload it on restart.
        data["workflowJson"] = workflow_json

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        saved = data["workflowJson"]["triggers"]
        assert len(saved) == 1
        trigger = TriggerDefinition.model_validate(saved[0])
        assert trigger.concurrency.model_dump() == expected
        if concurrency is not None:
            assert trigger.id == trigger_id
            for field, value in preserved_fields.items():
                assert saved[0][field] == value
        restart_states.append(trigger.enabled)
        return {"workflowId": workflow_id, "state": "running" if trigger.enabled else "stopped"}

    monkeypatch.setattr(workflow_routes, "_read_workflow_from_fs", lambda workflow_id: data)
    monkeypatch.setattr(workflow_routes, "_write_workflow_to_fs", _fake_write)
    monkeypatch.setattr(manager_path, SimpleNamespace(restart_workflow=_fake_restart))

    for enabled in (False, True, False, True):
        response = await client.post(
            f"/api/workflow/wf-1/{endpoint}", json={**settings, "enabled": enabled},
        )
        assert response.status_code == 200, response.text
        assert data["workflowJson"]["triggers"][0]["concurrency"] == expected

    assert restart_states == [False, True, False, True]
