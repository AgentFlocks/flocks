"""Workflow service lifecycle regression tests."""

from __future__ import annotations

import json
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
    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", fake_write)
    monkeypatch.setattr(center.WorkflowStore, "kv_remove", fake_remove)
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

    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", fake_write)
    monkeypatch.setattr(center.WorkflowStore, "kv_remove", fake_remove)
    monkeypatch.setattr(center, "_stop_and_remove_container", fake_stop_container)

    await center._stop_existing_runtime_for_publish("wf-1")

    assert stopped_containers == ["old-container"]
    assert "workflow_runtime/wf-1" not in store
    assert "workflow_release/wf-1/active" not in store
    assert store["workflow_release/wf-1/rel-old"]["status"] == "inactive"


@pytest.mark.asyncio
async def test_allocate_port_skips_reserved_service_records(monkeypatch) -> None:
    """Port allocation must not reuse API/runtime ports that are only visible in storage."""
    store: dict[str, Any] = {
        "workflow_api_service/wf-api": {
            "workflowId": "wf-api",
            "serviceUrl": "http://127.0.0.1:19000",
            "invokeUrl": "http://127.0.0.1:19000/invoke",
            "status": "running",
        },
        "workflow_runtime/wf-runtime": {
            "workflowId": "wf-runtime",
            "hostPort": 19001,
            "serviceUrl": "http://127.0.0.1:19001",
            "status": "active",
        },
        "workflow_registry/wf-registry": {
            "workflowId": "wf-registry",
            "servicePort": 19002,
            "publishStatus": "active",
        },
    }

    async def fake_list_keys(prefix):
        return [key for key in store if key.startswith(str(prefix))]

    async def fake_read(key):
        return store.get(str(key))

    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_START", "19000")
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_END", "19003")
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)

    assert await center._allocate_port() == 19003


@pytest.mark.asyncio
async def test_allocate_port_reserves_in_flight_allocations(monkeypatch) -> None:
    """Back-to-back allocations in one server process must not race to the same port."""
    center._IN_FLIGHT_PORT_RESERVATIONS.clear()

    async def fake_list_keys(_prefix):
        return []

    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_START", "19000")
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_END", "19001")
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)

    try:
        assert await center._allocate_port() == 19000
        assert await center._allocate_port() == 19001
    finally:
        center._IN_FLIGHT_PORT_RESERVATIONS.clear()


@pytest.mark.asyncio
async def test_allocate_preferred_port_reuses_current_workflow_reservation(monkeypatch) -> None:
    store: dict[str, Any] = {
        "workflow_api_service/wf-current": {
            "workflowId": "wf-current",
            "port": 25000,
            "status": "stopped",
        }
    }

    async def fake_list_keys(prefix):
        return [key for key in store if key.startswith(str(prefix))]

    async def fake_read(key):
        return store.get(str(key))

    center._IN_FLIGHT_PORT_RESERVATIONS.clear()
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_START", "19000")
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PORT_END", "19001")
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)

    try:
        assert await center._allocate_port(25000, workflow_id="wf-current") == 25000
    finally:
        center._IN_FLIGHT_PORT_RESERVATIONS.clear()


@pytest.mark.asyncio
async def test_allocate_preferred_port_rejects_other_workflow_reservation(monkeypatch) -> None:
    store: dict[str, Any] = {
        "workflow_api_service/wf-other": {
            "workflowId": "wf-other",
            "port": 25000,
            "status": "stopped",
        }
    }

    async def fake_list_keys(prefix):
        return [key for key in store if key.startswith(str(prefix))]

    async def fake_read(key):
        return store.get(str(key))

    center._IN_FLIGHT_PORT_RESERVATIONS.clear()
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)

    with pytest.raises(center.WorkflowPortUnavailableError, match="25000"):
        await center._allocate_port(25000, workflow_id="wf-current")


@pytest.mark.asyncio
async def test_allocate_preferred_port_allows_port_bound_by_current_runtime(monkeypatch) -> None:
    async def fake_list_keys(_prefix):
        return []

    center._IN_FLIGHT_PORT_RESERVATIONS.clear()
    monkeypatch.setattr(center.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: False)

    try:
        assert (
            await center._allocate_port(
                25000,
                workflow_id="wf-current",
                allow_bound_port=25000,
            )
            == 25000
        )
    finally:
        center._IN_FLIGHT_PORT_RESERVATIONS.clear()


@pytest.mark.asyncio
async def test_local_port_conflict_keeps_previous_runtime_and_registry(monkeypatch, tmp_path) -> None:
    workflow_id = "wf-local-conflict"
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "id": workflow_id,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    registry = {
        "workflowId": workflow_id,
        "workflowPath": str(workflow_path),
        "publishStatus": "active",
        "servicePort": 25000,
        "serviceUrl": "http://127.0.0.1:25000",
    }
    runtime = {
        "workflowId": workflow_id,
        "releaseId": "rel-old",
        "driver": "local",
        "hostPort": 25000,
        "serviceUrl": "http://127.0.0.1:25000",
    }
    store: dict[str, Any] = {
        f"workflow_registry/{workflow_id}": registry,
        f"workflow_runtime/{workflow_id}": runtime,
    }
    stop_calls: list[str] = []

    async def fake_read(key):
        return store.get(str(key))

    async def fake_write(key, value):
        store[str(key)] = value

    async def reject_port(*_args, **_kwargs):
        raise center.WorkflowPortUnavailableError("port conflict")

    async def fake_stop(_workflow_id):
        stop_calls.append(_workflow_id)
        return True

    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", fake_write)
    monkeypatch.setattr(center, "_allocate_port", reject_port)
    monkeypatch.setattr(center, "_stop_existing_runtime_for_publish", fake_stop)

    with pytest.raises(center.WorkflowPortUnavailableError, match="port conflict"):
        await center.publish_workflow_local(workflow_id, port=25000)

    assert stop_calls == []
    assert store[f"workflow_runtime/{workflow_id}"] is runtime
    assert store[f"workflow_registry/{workflow_id}"] == registry


@pytest.mark.asyncio
async def test_docker_port_conflict_keeps_previous_runtime_and_registry(monkeypatch, tmp_path) -> None:
    workflow_id = "wf-docker-conflict"
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "id": workflow_id,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    release_snapshot = release_dir / "workflow.json"
    registry = {
        "workflowId": workflow_id,
        "workflowPath": str(workflow_path),
        "publishStatus": "active",
        "servicePort": 25000,
        "serviceUrl": "http://127.0.0.1:25000",
    }
    runtime = {
        "workflowId": workflow_id,
        "releaseId": "rel-old",
        "driver": "docker",
        "containerName": "old-container",
        "hostPort": 25000,
        "serviceUrl": "http://127.0.0.1:25000",
    }
    store: dict[str, Any] = {
        f"workflow_registry/{workflow_id}": registry,
        f"workflow_runtime/{workflow_id}": runtime,
    }
    docker_calls: list[Any] = []

    async def fake_read(key):
        return store.get(str(key))

    async def fake_write(key, value):
        store[str(key)] = value

    async def fake_snapshot(*_args, **_kwargs):
        return release_snapshot

    async def no_requirements(*_args, **_kwargs):
        return False

    async def reject_port(*_args, **_kwargs):
        raise center.WorkflowPortUnavailableError("port conflict")

    async def fake_exec_docker(*args, **kwargs):
        docker_calls.append((args, kwargs))
        return ("", "", 0)

    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", fake_write)
    monkeypatch.setattr(center, "_write_release_snapshot", fake_snapshot)
    monkeypatch.setattr(center, "_write_requirements_snapshot", no_requirements)
    monkeypatch.setattr(center, "_service_cache_dir", lambda _name: tmp_path)
    monkeypatch.setattr(center.Config, "get_config_path", lambda: tmp_path / "missing-config")
    monkeypatch.setattr(center, "resolve_python_package_index_url", lambda: None)
    monkeypatch.setattr(center, "_allocate_port", reject_port)
    monkeypatch.setattr(center, "exec_docker", fake_exec_docker)

    with pytest.raises(center.WorkflowPortUnavailableError, match="port conflict"):
        await center._publish_workflow_docker(workflow_id, port=26000)

    assert docker_calls == []
    assert store[f"workflow_runtime/{workflow_id}"] is runtime
    assert store[f"workflow_registry/{workflow_id}"] == registry
    assert not any(key.startswith(f"workflow_release/{workflow_id}/") for key in store)


@pytest.mark.asyncio
async def test_publish_workflow_local_releases_reserved_port_on_spawn_failure(
    monkeypatch,
    tmp_path,
) -> None:
    workflow_id = "wf-local-spawn-fail"
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "id": workflow_id,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    store: dict[str, Any] = {
        f"workflow_registry/{workflow_id}": {
            "workflowId": workflow_id,
            "workflowPath": str(workflow_path),
            "publishStatus": "unpublished",
        },
    }

    async def fake_read(key):
        return store.get(str(key))

    async def fake_write(key, value):
        store[str(key)] = value

    async def fake_stop_existing_runtime_for_publish(_workflow_id):
        return False

    async def fake_write_release_snapshot(_workflow_id, _release_id, _workflow_json):
        return workflow_path

    async def fake_allocate_port(*_args, **_kwargs):
        center._IN_FLIGHT_PORT_RESERVATIONS[19000] = 9999999999.0
        return 19000

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        raise OSError("spawn failed")

    center._IN_FLIGHT_PORT_RESERVATIONS.clear()
    monkeypatch.setattr(center.WorkflowStore, "kv_get", fake_read)
    monkeypatch.setattr(center.WorkflowStore, "kv_put", fake_write)
    monkeypatch.setattr(center, "_stop_existing_runtime_for_publish", fake_stop_existing_runtime_for_publish)
    monkeypatch.setattr(center, "_write_release_snapshot", fake_write_release_snapshot)
    monkeypatch.setattr(center, "_allocate_port", fake_allocate_port)
    monkeypatch.setattr(center, "_is_port_available", lambda _port: True)
    monkeypatch.setattr(center.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    try:
        with pytest.raises(center.WorkflowCenterError, match="spawn failed"):
            await center.publish_workflow_local(workflow_id)

        assert 19000 not in center._IN_FLIGHT_PORT_RESERVATIONS
        assert store[f"workflow_registry/{workflow_id}"]["publishStatus"] == "failed"
    finally:
        center._IN_FLIGHT_PORT_RESERVATIONS.clear()
