from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from flocks.server.routes import workflow as workflow_routes
from flocks.tool.registry import PermissionRequest, ToolContext, ToolRegistry


def _output_json(result) -> dict[str, Any]:
    assert isinstance(result.output, str)
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


@pytest.fixture
def workflow_config_route_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, dict[str, Any]]:
    workflow_id = "wf-1"
    config_dir = tmp_path / workflow_id
    config_dir.mkdir()
    stored: dict[str, dict[str, Any]] = {}

    workflow_data = {
        "id": workflow_id,
        "name": "Demo Workflow",
        "category": "default",
        "source": "project",
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
            "edges": [],
            "triggers": [
                {
                    "id": "syslog-default",
                    "type": "syslog",
                    "enabled": True,
                    "source": {"host": "0.0.0.0", "port": 514, "protocol": "udp"},
                    "mapping": {"syslog_message": "$.body"},
                }
            ],
        },
    }

    async def _fake_get_config(
        requested_workflow_id: str,
        *,
        kind: str = "workflow.integration-config",
    ) -> dict[str, Any] | None:
        if kind != "workflow.integration-config":
            return None
        return stored.get(requested_workflow_id)

    async def _fake_put_config(
        requested_workflow_id: str,
        config: dict[str, Any],
        *,
        kind: str | None = None,
    ) -> None:
        assert kind in (None, "workflow.integration-config")
        stored[requested_workflow_id] = config

    async def _fake_kv_get(_key: Any) -> None:
        return None

    async def _fake_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda requested_workflow_id: workflow_data if requested_workflow_id == workflow_id else None,
    )
    monkeypatch.setattr(
        workflow_routes,
        "_workflow_config_dir",
        lambda _workflow_id, _workflow_data=None: config_dir,
    )
    monkeypatch.setattr(workflow_routes.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(workflow_routes.WorkflowStore, "put_config", _fake_put_config)
    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_get", _fake_kv_get)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=_fake_statuses),
    )

    return stored


def test_workflow_config_manage_is_registered_as_builtin_tool() -> None:
    ToolRegistry.init()

    tool = ToolRegistry.get("workflow_config_manage")

    assert tool is not None
    assert tool.info.native is True
    assert tool.info.category.value == "system"
    assert "workflow_id" in tool.info.get_schema().properties


@pytest.mark.asyncio
async def test_workflow_config_manage_get_reads_file_fallback_without_migration(
    workflow_config_route_fakes: dict[str, dict[str, Any]],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "wf-1" / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "workflow.integration-config",
                "workflow": {"id": "wf-1"},
                "publish": {"type": "api_service"},
                "triggers": [{"id": "api-default", "type": "api", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )

    result = await ToolRegistry.execute(
        "workflow_config_manage",
        action="get",
        workflow_id="wf-1",
    )

    assert result.success is True, result.error
    output = _output_json(result)
    assert output["source"] == "file_fallback"
    assert output["stored"] is False
    assert output["config"]["triggers"][0]["id"] == "api-default"
    assert workflow_config_route_fakes == {}


@pytest.mark.asyncio
async def test_workflow_config_manage_put_normalizes_and_masks_secrets(
    workflow_config_route_fakes: dict[str, dict[str, Any]],
) -> None:
    permissions: list[PermissionRequest] = []

    async def _permission_callback(request: PermissionRequest) -> None:
        permissions.append(request)

    ctx = ToolContext(
        session_id="test-session",
        message_id="test-message",
        permission_callback=_permission_callback,
    )

    result = await ToolRegistry.execute(
        "workflow_config_manage",
        ctx=ctx,
        action="put",
        workflow_id="wf-1",
        config={
            "version": 1,
            "kind": "workflow.integration-config",
            "workflow": {"id": "wf-1"},
            "publish": {"type": "api_service", "enabled": True, "apiKey": "secret-value"},
            "triggers": [
                {
                    "id": "api-default",
                    "type": "api",
                    "enabled": True,
                    "auth": {"type": "api_key", "apiKey": "trigger-secret"},
                }
            ],
        },
    )

    assert result.success is True, result.error
    assert permissions
    assert permissions[0].permission == "workflow_config"
    assert permissions[0].metadata["action"] == "put"
    assert "diff" in permissions[0].metadata

    output = _output_json(result)
    written = workflow_config_route_fakes["wf-1"]
    assert written == output["config"]
    assert written["workflow"]["name"] == "Demo Workflow"
    assert written["publish"]["apiKeyConfigured"] is True
    assert "apiKey" not in written["publish"]
    assert written["triggers"][0]["auth"]["apiKeyConfigured"] is True
    assert "apiKey" not in written["triggers"][0]["auth"]
    assert output["source"] == "storage"


@pytest.mark.asyncio
async def test_workflow_config_manage_diff_does_not_write_config(
    workflow_config_route_fakes: dict[str, dict[str, Any]],
) -> None:
    result = await ToolRegistry.execute(
        "workflow_config_manage",
        action="diff",
        workflow_id="wf-1",
        config={
            "version": 1,
            "kind": "workflow.integration-config",
            "workflow": {"id": "wf-1"},
            "publish": {"type": "api_service"},
            "triggers": [{"id": "api-default", "type": "api", "enabled": True}],
        },
    )

    assert result.success is True, result.error
    output = _output_json(result)
    assert output["changed"] is True
    assert "api-default" in output["diff"]
    assert workflow_config_route_fakes == {}


@pytest.mark.asyncio
async def test_workflow_config_manage_sync_writes_missing_config(
    workflow_config_route_fakes: dict[str, dict[str, Any]],
) -> None:
    permissions: list[PermissionRequest] = []

    async def _permission_callback(request: PermissionRequest) -> None:
        permissions.append(request)

    ctx = ToolContext(
        session_id="test-session",
        message_id="test-message",
        permission_callback=_permission_callback,
    )

    result = await ToolRegistry.execute(
        "workflow_config_manage",
        ctx=ctx,
        action="sync",
        workflow_id="wf-1",
    )

    assert result.success is True, result.error
    assert permissions[0].permission == "workflow_config"
    output = _output_json(result)
    assert output["source"] == "storage"
    assert workflow_config_route_fakes["wf-1"]["kind"] == "workflow.integration-config"
    assert workflow_config_route_fakes["wf-1"]["triggers"][0]["type"] == "syslog"
