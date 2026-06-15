from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from flocks.server.routes import workflow as workflow_routes
from flocks.workflow import fs_store


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
async def test_workflow_config_response_keeps_template_separate_from_runtime(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "wf-1"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "workflow.integration-config",
                "workflow": {"id": "wf-1"},
                "triggers": [
                    {
                        "id": "syslog-default",
                        "type": "syslog",
                        "enabled": False,
                    },
                    {
                        "id": "api-default",
                        "type": "api",
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow_routes,
        "_workflow_config_dir",
        lambda _workflow_id, _workflow_data=None: config_dir,
    )
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "name": "demo",
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "syslog-default",
                        "type": "syslog",
                        "enabled": True,
                    }
                ],
            },
        } if workflow_id == "wf-1" else None,
    )

    stored_writes: dict[str, Any] = {}

    async def _fake_read(key: Any, _model: Any = None) -> dict[str, Any] | None:
        if key == workflow_routes._api_service_key("wf-1"):
            return {
                "workflowId": "wf-1",
                "status": "stopped",
                "driver": "local",
            }
        return None

    async def _fake_write(key: Any, value: Any) -> None:
        stored_writes[str(key)] = value

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "workflowId": "wf-1",
                "triggerId": "syslog-default",
                "triggerType": "syslog",
                "state": "listening",
            }
        ]

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_write)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    response = await client.get("/api/workflow/wf-1/config")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert body["source"] == "file_migrated"
    assert stored_writes[workflow_routes._workflow_integration_config_key("wf-1")] == body["config"]
    assert body["config"]["triggers"][0]["enabled"] is False
    assert body["config"]["triggers"][1]["type"] == "api"
    assert body["runtime"]["publish"]["status"] == "stopped"
    assert body["runtime"]["publish"]["enabled"] is False
    assert body["runtime"]["triggers"][0]["trigger"]["enabled"] is True
    assert body["runtime"]["triggers"][0]["status"]["state"] == "listening"


@pytest.mark.asyncio
async def test_workflow_config_prefers_storage_over_config_file(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "wf-1"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "workflow.integration-config",
                "workflow": {"id": "wf-1"},
                "triggers": [{"id": "file-syslog", "type": "syslog", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )

    stored_config = {
        "version": 1,
        "kind": "workflow.integration-config",
        "workflow": {"id": "wf-1"},
        "updatedAt": 1,
        "publish": {"type": "api_service"},
        "triggers": [{"id": "storage-api", "type": "api", "enabled": True}],
    }
    write_calls: list[tuple[Any, Any]] = []

    monkeypatch.setattr(
        workflow_routes,
        "_workflow_config_dir",
        lambda _workflow_id, _workflow_data=None: config_dir,
    )
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "name": "demo",
            "workflowJson": {"start": "n1", "nodes": [], "edges": [], "triggers": []},
        } if workflow_id == "wf-1" else None,
    )

    async def _fake_read(key: Any, _model: Any = None) -> dict[str, Any] | None:
        if key == workflow_routes._workflow_integration_config_key("wf-1"):
            return stored_config
        return None

    async def _fake_write(key: Any, value: Any) -> None:
        write_calls.append((key, value))

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_write)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    response = await client.get("/api/workflow/wf-1/config")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "storage"
    assert body["config"]["triggers"] == [{"id": "storage-api", "type": "api", "enabled": True}]
    assert all(key != workflow_routes._workflow_integration_config_key("wf-1") for key, _value in write_calls)


@pytest.mark.asyncio
async def test_update_workflow_config_writes_template_without_mutating_runtime(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workflow_id = "wf-1"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [{"id": "syslog-default", "type": "syslog", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "meta.json").write_text(
        json.dumps(
            {
                "name": "Demo Workflow",
                "category": "default",
                "source": "project",
                "status": "draft",
                "createdAt": 1,
                "updatedAt": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    original_storage_read = workflow_routes.Storage.read
    stored_writes: dict[str, Any] = {}

    async def _fake_storage_read(key: Any, *args: Any, **kwargs: Any) -> Any:
        if key == workflow_routes._api_service_key(workflow_id):
            return {
                "workflowId": workflow_id,
                "workflowName": "Demo Workflow",
                "serviceUrl": "http://127.0.0.1:19000",
                "invokeUrl": "http://127.0.0.1:19000/invoke",
                "apiKey": "runtime-secret",
                "status": "running",
                "driver": "local",
                "publishedAt": 123,
            }
        return await original_storage_read(key, *args, **kwargs)

    async def _fake_storage_write(key: Any, value: Any) -> None:
        stored_writes[str(key)] = value

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "workflowId": workflow_id,
                "triggerId": "syslog-default",
                "triggerType": "syslog",
                "state": "listening",
            }
        ]

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_storage_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_storage_write)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    response = await client.put(
        f"/api/workflow/{workflow_id}/config",
        json={
            "version": 1,
            "kind": "workflow.integration-config",
            "workflow": {"id": workflow_id},
            "runtime": {"publish": {"enabled": False}},
            "publish": {
                "type": "api_service",
                "enabled": False,
                "apiKey": "template-secret",
            },
            "triggers": [
                {
                    "id": "api-default",
                    "type": "api",
                    "source": {
                        "method": "POST",
                        "path": f"/api/workflow/{workflow_id}/run",
                        "client_secret": "nested-secret",
                        "secretRef": "workflow/api-key",
                    },
                },
                {
                    "id": "syslog-default",
                    "type": "syslog",
                    "enabled": False,
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    config_path = workflow_dir / "config.json"
    assert body["path"] == str(config_path)
    assert body["source"] == "storage"
    assert not config_path.exists()
    written = stored_writes[workflow_routes._workflow_integration_config_key(workflow_id)]
    assert written == body["config"]
    assert "runtime" not in written
    assert written["workflow"]["id"] == workflow_id
    assert written["workflow"]["name"] == "Demo Workflow"
    assert written["publish"]["enabled"] is False
    assert written["publish"]["apiKeyConfigured"] is True
    assert "apiKey" not in written["publish"]
    assert written["triggers"][0]["type"] == "api"
    assert written["triggers"][0]["source"]["client_secretConfigured"] is True
    assert written["triggers"][0]["source"]["secretRef"] == "workflow/api-key"
    assert "client_secret" not in written["triggers"][0]["source"]
    assert body["runtime"]["publish"]["enabled"] is True
    assert body["runtime"]["publish"]["apiKeyConfigured"] is True
    assert body["runtime"]["triggers"][0]["status"]["state"] == "listening"


@pytest.mark.asyncio
async def test_delete_workflow_service_removes_runtime_service_record(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-service-delete"
    await workflow_routes.Storage.write(
        workflow_routes._api_service_key(workflow_id),
        {
            "workflowId": workflow_id,
            "workflowName": "Demo Workflow",
            "serviceUrl": "http://127.0.0.1:19000",
            "invokeUrl": "http://127.0.0.1:19000/invoke",
            "apiKey": "runtime-secret",
            "status": "running",
            "driver": "local",
            "publishedAt": 123,
        },
    )
    stopped: list[str] = []

    async def _fake_stop_service(wid: str) -> dict[str, Any]:
        stopped.append(wid)
        return {"workflowId": wid, "status": "stopped"}

    monkeypatch.setattr(workflow_routes, "stop_workflow_service", _fake_stop_service)

    response = await client.delete(f"/api/workflow/{workflow_id}/service")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "workflowId": workflow_id}
    assert stopped == [workflow_id]
    assert await workflow_routes.Storage.read(workflow_routes._api_service_key(workflow_id)) is None


@pytest.mark.asyncio
async def test_update_workflow_config_rejects_mismatched_workflow_id(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {
            "id": workflow_id,
            "name": "demo",
            "workflowJson": {"start": "n1", "nodes": [], "edges": []},
        } if workflow_id == "wf-1" else None,
    )

    response = await client.put(
        "/api/workflow/wf-1/config",
        json={
            "version": 1,
            "kind": "workflow.integration-config",
            "workflow": {"id": "other-workflow"},
            "publish": {},
            "triggers": [],
        },
    )

    assert response.status_code == 409, response.text
    assert "does not match" in response.json()["message"]


@pytest.mark.asyncio
async def test_delete_workflow_cleans_directory_and_storage(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workflow_id = "wf-delete"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps({"start": "n1", "nodes": [], "edges": []}),
        encoding="utf-8",
    )
    (workflow_dir / "meta.json").write_text(
        json.dumps({"name": "Delete Me", "category": "default", "status": "draft"}),
        encoding="utf-8",
    )
    service_dir = workflow_routes.Config.get_data_path() / "workflow-services" / "workflows" / workflow_id
    service_dir.mkdir(parents=True)
    (service_dir / "snapshot.json").write_text("{}", encoding="utf-8")

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    storage_keys = [
        workflow_routes._workflow_stats_key(workflow_id),
        workflow_routes._workflow_integration_config_key(workflow_id),
        workflow_routes._api_service_key(workflow_id),
        workflow_routes._syslog_config_key(workflow_id),
        workflow_routes._kafka_config_key(workflow_id),
        f"workflow_poller_config/{workflow_id}",
        f"workflow_registry/{workflow_id}",
        f"workflow_runtime/{workflow_id}",
        f"workflow_local_pid/{workflow_id}",
        f"workflow_release/{workflow_id}/active",
        f"workflow_release/{workflow_id}/rel-1",
        workflow_routes._workflow_execution_key("exec-delete"),
    ]
    for key in storage_keys:
        payload = {"workflowId": workflow_id}
        if key == workflow_routes._workflow_execution_key("exec-delete"):
            payload = {"id": "exec-delete", "workflowId": workflow_id}
        await workflow_routes.Storage.write(key, payload)

    stopped: list[Any] = []

    async def _fake_stop_service(wid: str) -> dict[str, Any]:
        stopped.append(("service", wid))
        return {"workflowId": wid, "status": "stopped"}

    async def _fake_restart_workflow(wid: str, workflow_json: dict[str, Any]) -> dict[str, Any]:
        stopped.append(("triggers", wid, workflow_json))
        return {"syslog": {"state": "stopped"}}

    monkeypatch.setattr(workflow_routes, "stop_workflow_service", _fake_stop_service)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(restart_workflow=_fake_restart_workflow),
    )

    response = await client.delete(f"/api/workflow/{workflow_id}")

    assert response.status_code == 204, response.text
    assert not workflow_dir.exists()
    assert not service_dir.exists()
    assert ("service", workflow_id) in stopped
    assert ("triggers", workflow_id, {"triggers": []}) in stopped
    for key in storage_keys:
        assert await workflow_routes.Storage.read(key) is None
    assert await workflow_routes.Storage.list(f"workflow_release/{workflow_id}/") == []


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


@pytest.mark.asyncio
async def test_sync_workflow_config_writes_publish_and_trigger_capabilities(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workflow_id = "wf-1"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [
                    {
                        "id": "hook-default",
                        "type": "custom_webhook",
                        "enabled": True,
                        "source": {"method": "POST"},
                        "auth": {"type": "api_key", "apiKey": "demo-secret"},
                        "mapping": {"payload": "$.body"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "meta.json").write_text(
        json.dumps(
            {
                "name": "Demo Workflow",
                "description": None,
                "category": "default",
                "status": "draft",
                "createdBy": None,
                "createdAt": 1,
                "updatedAt": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    original_storage_read = workflow_routes.Storage.read
    stored_writes: dict[str, Any] = {}

    async def _fake_storage_read(key: Any, *args: Any, **kwargs: Any) -> Any:
        if key == workflow_routes._api_service_key(workflow_id):
            return {
                "workflowId": workflow_id,
                "workflowName": "Demo Workflow",
                "serviceUrl": "http://127.0.0.1:19000",
                "invokeUrl": "http://127.0.0.1:19000/invoke",
                "apiKey": "service-secret",
                "status": "running",
                "driver": "local",
                "publishedAt": 123,
            }
        return await original_storage_read(key, *args, **kwargs)

    async def _fake_storage_write(key: Any, value: Any) -> None:
        stored_writes[str(key)] = value

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_storage_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_storage_write)

    response = await client.post(f"/api/workflow/{workflow_id}/config/sync")

    assert response.status_code == 200, response.text
    config_path = workflow_dir / "config.json"
    assert response.json()["path"] == str(config_path)
    assert response.json()["source"] == "storage"
    assert not config_path.exists()
    config = stored_writes[workflow_routes._workflow_integration_config_key(workflow_id)]
    assert response.json()["config"] == config
    assert config["kind"] == "workflow.integration-config"
    assert config["publish"]["enabled"] is True
    assert config["publish"]["apiKeyConfigured"] is True
    assert "apiKey" not in config["publish"]
    assert config["triggers"][0]["invoke"]["path"] == "/webhook/workflows/wf-1/hook-default"
    assert config["triggers"][0]["auth"]["apiKeyConfigured"] is True
    assert "apiKey" not in config["triggers"][0]["auth"]


@pytest.mark.asyncio
async def test_persist_workflow_triggers_does_not_overwrite_config_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workflow_id = "wf-1"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    config_path = workflow_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "workflow.integration-config",
                "workflow": {"id": workflow_id},
                "publish": {"type": "api_service"},
                "triggers": [],
            }
        ),
        encoding="utf-8",
    )
    before = config_path.read_text(encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    async def _fake_storage_read(_key: Any, *_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_storage_read)

    workflow_data = {
        "id": workflow_id,
        "source": "project",
        "name": "Demo Workflow",
        "description": None,
        "category": "default",
        "status": "draft",
        "createdBy": None,
        "createdAt": 1,
        "updatedAt": 1,
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
            "edges": [],
        },
    }
    trigger = workflow_routes.TriggerDefinition.model_validate(
        {
            "id": "schedule-default",
            "type": "schedule",
            "enabled": True,
            "source": {"intervalSeconds": 60},
            "runtime": {"noOverlap": True},
        }
    )

    await workflow_routes._persist_workflow_triggers(workflow_id, workflow_data, [trigger])

    assert config_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_sync_workflow_config_preserves_existing_template(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workflow_id = "wf-1"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                "edges": [],
                "triggers": [{"id": "syslog-default", "type": "syslog", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )
    template = {
        "version": 1,
        "kind": "workflow.integration-config",
        "workflow": {"id": workflow_id},
        "publish": {"type": "api_service"},
        "triggers": [],
    }
    config_path = workflow_dir / "config.json"
    config_path.write_text(json.dumps(template), encoding="utf-8")
    before = config_path.read_text(encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(fs_store, "_workspace_root", None)

    response = await client.post(f"/api/workflow/{workflow_id}/config/sync")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "file_migrated"
    assert body["storageKey"] == workflow_routes._workflow_integration_config_key(workflow_id)
    assert body["config"]["workflow"]["id"] == workflow_id
    assert body["config"]["publish"] == {"type": "api_service"}
    assert body["config"]["triggers"] == []
    assert config_path.read_text(encoding="utf-8") == before
    stored = await workflow_routes.Storage.read(workflow_routes._workflow_integration_config_key(workflow_id))
    assert stored == body["config"]
