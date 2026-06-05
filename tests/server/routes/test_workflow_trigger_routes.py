from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from flocks.server.routes import workflow as workflow_routes


@pytest.mark.asyncio
async def test_list_workflow_triggers_returns_unified_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "schedule-default",
                        "type": "schedule",
                        "enabled": True,
                        "source": {"intervalSeconds": 60},
                    }
                ],
            },
        } if workflow_id == "wf-1" else None,
    )

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "workflowId": "wf-1",
                "triggerId": "schedule-default",
                "triggerType": "schedule",
                "state": "running",
            }
        ]

    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    response = await client.get("/api/workflow/wf-1/triggers")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["trigger"]["id"] == "schedule-default"
    assert body[0]["status"]["state"] == "running"


@pytest.mark.asyncio
async def test_list_workflow_triggers_respects_explicit_empty_trigger_list(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [],
            },
        } if workflow_id == "wf-1" else None,
    )

    async def _fake_legacy_triggers(_workflow_id: str) -> list[Any]:
        return [
            workflow_routes.TriggerDefinition.model_validate(
                {
                    "id": "schedule-default",
                    "type": "schedule",
                    "enabled": True,
                    "source": {"intervalSeconds": 30},
                }
            )
        ]

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(workflow_routes, "_read_legacy_trigger_defs", _fake_legacy_triggers)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    response = await client.get("/api/workflow/wf-1/triggers")

    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
async def test_preview_trigger_mapping_returns_mapped_inputs(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "hook-default",
                        "type": "custom_webhook",
                        "enabled": True,
                        "mapping": {"alert_data": "$.body.data[0]"},
                        "filter": {"expr": "body.data[0].severity == 'high'"},
                    }
                ],
            },
        },
    )

    response = await client.post(
        "/api/workflow/wf-1/triggers/hook-default/preview-mapping",
        json={"body": {"data": [{"severity": "high"}]}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] is True
    assert body["inputs"]["alert_data"] == {"severity": "high"}
    assert body["inputs"]["_flocks"]["trigger"]["id"] == "hook-default"


@pytest.mark.asyncio
async def test_create_workflow_trigger_persists_and_restarts_runtime(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_payloads: list[dict[str, Any]] = []

    base_workflow = {
        "id": "wf-1",
        "name": "demo",
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
            "edges": [],
        },
    }

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: base_workflow if workflow_id == "wf-1" else None,
    )

    async def _fake_persist(workflow_id: str, workflow_data: dict[str, Any], triggers: list[Any]) -> dict[str, Any]:
        stored_payloads.append(
            {
                "workflow_id": workflow_id,
                "trigger_ids": [trigger.id for trigger in triggers],
            }
        )
        return {
            **workflow_data,
            "workflowJson": {
                **workflow_data["workflowJson"],
                "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
            },
        }

    runtime_calls: list[str] = []

    async def _fake_restart(workflow_id: str, workflow_json: dict[str, Any]) -> dict[str, Any]:
        runtime_calls.append(f"restart:{workflow_id}:{len(workflow_json.get('triggers', []))}")
        return {}

    async def _fake_status(workflow_id: str, trigger: Any) -> dict[str, Any]:
        return {"workflowId": workflow_id, "triggerId": trigger.id, "state": "ready"}

    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(restart_workflow=_fake_restart, get_trigger_status=_fake_status),
    )

    response = await client.post(
        "/api/workflow/wf-1/triggers",
        json={
            "id": "hook-default",
            "type": "custom_webhook",
            "enabled": True,
            "source": {"path": "/alerts/demo", "method": "POST"},
            "mapping": {"payload": "$.body"},
        },
    )

    assert response.status_code == 200, response.text
    assert stored_payloads[0]["trigger_ids"] == ["hook-default"]
    assert runtime_calls == ["restart:wf-1:1"]
    assert response.json()["status"]["state"] == "ready"


@pytest.mark.asyncio
async def test_create_workflow_trigger_rejects_multiple_legacy_singletons(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "schedule-default",
                        "type": "schedule",
                        "enabled": True,
                        "source": {"intervalSeconds": 60},
                    }
                ],
            },
        } if workflow_id == "wf-1" else None,
    )

    response = await client.post(
        "/api/workflow/wf-1/triggers",
        json={
            "id": "schedule-extra",
            "type": "schedule",
            "enabled": True,
            "source": {"intervalSeconds": 300},
        },
    )

    assert response.status_code == 409, response.text
    assert "Only one schedule trigger" in response.json()["message"]


@pytest.mark.asyncio
async def test_delete_workflow_trigger_removes_definition_and_restarts_runtime(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_payloads: list[dict[str, Any]] = []

    base_workflow = {
        "id": "wf-1",
        "name": "demo",
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
            "edges": [],
            "triggers": [
                {
                    "id": "hook-default",
                    "type": "custom_webhook",
                    "enabled": True,
                    "source": {"path": "/alerts/demo", "method": "POST"},
                    "mapping": {"payload": "$.body"},
                }
            ],
        },
    }

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: base_workflow if workflow_id == "wf-1" else None,
    )

    async def _fake_persist(workflow_id: str, workflow_data: dict[str, Any], triggers: list[Any]) -> dict[str, Any]:
        stored_payloads.append(
            {
                "workflow_id": workflow_id,
                "trigger_ids": [trigger.id for trigger in triggers],
            }
        )
        return {
            **workflow_data,
            "workflowJson": {
                **workflow_data["workflowJson"],
                "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
            },
        }

    runtime_calls: list[str] = []

    async def _fake_restart(workflow_id: str, workflow_json: dict[str, Any]) -> dict[str, Any]:
        runtime_calls.append(f"restart:{workflow_id}:{len(workflow_json.get('triggers', []))}")
        return {}

    async def _fake_remove_legacy(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(workflow_routes, "_remove_legacy_trigger_state", _fake_remove_legacy)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(restart_workflow=_fake_restart),
    )

    response = await client.delete("/api/workflow/wf-1/triggers/hook-default")

    assert response.status_code == 200, response.text
    assert stored_payloads[0]["trigger_ids"] == []
    assert runtime_calls == ["restart:wf-1:0"]
    assert response.json() == {"ok": True, "triggerId": "hook-default"}


@pytest.mark.asyncio
async def test_webhook_route_authorizes_and_dispatches_trigger(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "hook-default",
                        "type": "custom_webhook",
                        "enabled": True,
                        "auth": {"type": "api_key", "apiKey": "demo-secret"},
                        "mapping": {"payload": "$.body"},
                        "source": {"path": "/webhook/workflows/wf-1/hook-default"},
                    }
                ],
            },
        },
    )

    async def _fake_dispatch_event(**kwargs: Any) -> dict[str, Any]:
        event = kwargs["event"]
        return {
            "matched": True,
            "executed": True,
            "inputs": {"payload": event.body},
            "result": {"triggerId": kwargs["trigger"].id},
        }

    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(dispatch_event=_fake_dispatch_event),
    )

    response = await client.post(
        "/webhook/workflows/wf-1/hook-default",
        headers={"x-api-key": "demo-secret"},
        json={"severity": "high"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["executed"] is True
    assert body["inputs"]["payload"] == {"severity": "high"}
    assert isinstance(body["deliveryId"], str)
    assert "result" not in body


@pytest.mark.asyncio
async def test_webhook_route_rejects_disabled_trigger(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "hook-default",
                        "type": "custom_webhook",
                        "enabled": False,
                        "auth": {"type": "api_key", "apiKey": "demo-secret"},
                    }
                ],
            },
        },
    )

    response = await client.post(
        "/webhook/workflows/wf-1/hook-default",
        headers={"x-api-key": "demo-secret"},
        json={"severity": "high"},
    )

    assert response.status_code == 403, response.text
    assert "disabled" in response.json()["message"]


@pytest.mark.asyncio
async def test_webhook_route_validates_hmac_signature(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "hook-default",
                        "type": "custom_webhook",
                        "enabled": True,
                        "auth": {
                            "type": "hmac",
                            "secretRef": "secret://demo-hook",
                            "headerName": "x-signature",
                        },
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(workflow_routes, "_resolve_trigger_secret", lambda _ref: "demo-secret")

    async def _fake_dispatch_event(**_kwargs: Any) -> dict[str, Any]:
        return {"matched": True, "executed": True, "inputs": {}}

    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(dispatch_event=_fake_dispatch_event),
    )

    payload = b'{"severity":"high"}'
    signature = workflow_routes.hmac.new(
        b"demo-secret",
        payload,
        workflow_routes.hashlib.sha256,
    ).hexdigest()

    ok_response = await client.post(
        "/webhook/workflows/wf-1/hook-default",
        headers={"x-signature": f"sha256={signature}", "content-type": "application/json"},
        content=payload,
    )
    assert ok_response.status_code == 200, ok_response.text

    bad_response = await client.post(
        "/webhook/workflows/wf-1/hook-default",
        headers={"x-signature": "sha256=bad-signature", "content-type": "application/json"},
        content=payload,
    )
    assert bad_response.status_code == 401, bad_response.text
