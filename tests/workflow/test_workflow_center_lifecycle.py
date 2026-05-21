"""Workflow service lifecycle regression tests."""

from __future__ import annotations

from typing import Any

import pytest

from flocks.workflow import center


@pytest.mark.asyncio
async def test_stop_workflow_service_uses_persisted_runtime_driver(monkeypatch) -> None:
    """Stopping must follow the published instance driver, not current env."""
    store: dict[str, Any] = {
        "workflow_registry/wf-1": {
            "workflowId": "wf-1",
            "publishStatus": "active",
            "serviceUrl": "http://127.0.0.1:19000",
        },
        "workflow_runtime/wf-1": {
            "workflowId": "wf-1",
            "releaseId": "rel-1",
            "driver": "docker",
            "containerName": "flocks-wf-wf-1-rel-1",
        },
        "workflow_release/wf-1/active": {
            "workflowId": "wf-1",
            "releaseId": "rel-1",
            "driver": "docker",
            "containerName": "flocks-wf-wf-1-rel-1",
        },
        "workflow_release/wf-1/rel-1": {
            "workflowId": "wf-1",
            "releaseId": "rel-1",
            "status": "active",
        },
    }
    stopped_containers: list[str] = []

    async def fake_read(key):
        return store.get(str(key))

    async def fake_write(key, value):
        store[str(key)] = value

    async def fake_remove(key):
        store.pop(str(key), None)
        return True

    async def fake_stop_container(container_name: str) -> bool:
        stopped_containers.append(container_name)
        return True

    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_DRIVER", "local")
    monkeypatch.setattr(center.Storage, "read", fake_read)
    monkeypatch.setattr(center.Storage, "write", fake_write)
    monkeypatch.setattr(center.Storage, "remove", fake_remove)
    monkeypatch.setattr(center, "_stop_and_remove_container", fake_stop_container)

    result = await center.stop_workflow_service("wf-1")

    assert result["driver"] == "docker"
    assert stopped_containers == ["flocks-wf-wf-1-rel-1"]
    assert "workflow_runtime/wf-1" not in store
    assert "workflow_release/wf-1/active" not in store
    assert store["workflow_registry/wf-1"]["publishStatus"] == "stopped"
    assert store["workflow_release/wf-1/rel-1"]["status"] == "inactive"


@pytest.mark.asyncio
async def test_publish_cleanup_uses_previous_runtime_driver(monkeypatch) -> None:
    """Republish cleanup must stop the previous runtime even after driver switches."""
    store: dict[str, Any] = {
        "workflow_registry/wf-1": {"workflowId": "wf-1", "publishStatus": "publishing"},
        "workflow_runtime/wf-1": {
            "workflowId": "wf-1",
            "releaseId": "rel-old",
            "driver": "docker",
            "containerName": "old-container",
        },
        "workflow_release/wf-1/active": {
            "workflowId": "wf-1",
            "releaseId": "rel-old",
            "driver": "docker",
            "containerName": "old-container",
        },
        "workflow_release/wf-1/rel-old": {
            "workflowId": "wf-1",
            "releaseId": "rel-old",
            "status": "active",
        },
    }
    stopped_containers: list[str] = []

    async def fake_read(key):
        return store.get(str(key))

    async def fake_write(key, value):
        store[str(key)] = value

    async def fake_remove(key):
        store.pop(str(key), None)
        return True

    async def fake_stop_container(container_name: str) -> bool:
        stopped_containers.append(container_name)
        return True

    monkeypatch.setattr(center.Storage, "read", fake_read)
    monkeypatch.setattr(center.Storage, "write", fake_write)
    monkeypatch.setattr(center.Storage, "remove", fake_remove)
    monkeypatch.setattr(center, "_stop_and_remove_container", fake_stop_container)

    await center._stop_existing_runtime_for_publish("wf-1")

    assert stopped_containers == ["old-container"]
    assert "workflow_runtime/wf-1" not in store
    assert "workflow_release/wf-1/active" not in store
    assert store["workflow_release/wf-1/rel-old"]["status"] == "inactive"
