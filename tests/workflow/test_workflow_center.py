"""Tests for workflow center skill registry and docker publish flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.storage.storage import Storage
from flocks.config.config import Config
from flocks.workflow import center


def test_docker_proxy_env_value_rewrites_loopback_proxy() -> None:
    """Loopback proxies must point at the host from inside Docker containers."""
    assert (
        center._docker_proxy_env_value("http://127.0.0.1:7897")
        == "http://host.docker.internal:7897"
    )
    assert (
        center._docker_proxy_env_value("https://localhost:7897")
        == "https://host.docker.internal:7897"
    )
    assert center._docker_proxy_env_value("http://proxy.example:8080") == "http://proxy.example:8080"


@pytest.mark.asyncio
async def test_wait_docker_service_healthy_fails_fast_when_container_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead container should surface logs instead of waiting for health retries."""

    def fake_json_get(*_args, **_kwargs):
        raise OSError("connection refused")

    async def fake_container_state(_container_name: str) -> dict[str, bool]:
        return {"exists": True, "running": False}

    async def fake_logs_tail(_container_name: str, *, lines: int = 80) -> str:
        return "pip install failed"

    async def fail_sleep(_interval: float) -> None:
        raise AssertionError("health check should not sleep after container exit")

    monkeypatch.setattr(center, "_json_get", fake_json_get)
    monkeypatch.setattr(center, "docker_container_state", fake_container_state)
    monkeypatch.setattr(center, "_docker_logs_tail", fake_logs_tail)
    monkeypatch.setattr(center.asyncio, "sleep", fail_sleep)

    with pytest.raises(center.WorkflowCenterError, match="pip install failed"):
        await center._wait_docker_service_healthy(
            "http://127.0.0.1:19000",
            "flocks-wf-dead",
            retries=10,
            interval_s=2,
        )


def _workflow_payload(name: str) -> dict:
    return {
        "id": f"{name}-id",
        "name": name,
        "start": "n1",
        "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }


@pytest.fixture
async def isolated_storage(tmp_path: Path):
    """Initialize isolated storage database for workflow center tests."""
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(tmp_path / "workflow-center.db")
    yield
    Storage._initialized = False
    Storage._db_path = None


@pytest.mark.asyncio
async def test_scan_skill_workflows_is_idempotent(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan should register workflows once and detect fingerprint changes."""
    wf_dir = tmp_path / ".flocks" / "workflow" / "demo"
    wf_dir.mkdir(parents=True)
    workflow_path = wf_dir / "workflow.json"
    workflow_path.write_text(json.dumps(_workflow_payload("demo")), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(center, "resolve_global_workflow_roots", lambda: [])

    first = await center.scan_skill_workflows()
    assert len(first) == 1
    assert first[0]["sourceType"] == "project"
    assert first[0]["draftChanged"] is False

    second = await center.scan_skill_workflows()
    assert len(second) == 1
    assert second[0]["draftChanged"] is False

    workflow_path.write_text(json.dumps(_workflow_payload("demo-v2")), encoding="utf-8")
    third = await center.scan_skill_workflows()
    assert len(third) == 1
    assert third[0]["draftChanged"] is True


@pytest.mark.asyncio
async def test_scan_skill_workflows_skips_hidden_templates(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hidden workflow templates should not enter prompt-visible registry entries."""
    visible_dir = tmp_path / ".flocks" / "plugins" / "workflows" / "visible"
    hidden_dir = tmp_path / ".flocks" / "plugins" / "workflows" / "__hidden_template"
    visible_dir.mkdir(parents=True)
    hidden_dir.mkdir(parents=True)
    (visible_dir / "workflow.json").write_text(
        json.dumps(_workflow_payload("visible")),
        encoding="utf-8",
    )
    (hidden_dir / "workflow.json").write_text(
        json.dumps(_workflow_payload("hidden-template")),
        encoding="utf-8",
    )
    (hidden_dir / "meta.json").write_text(
        json.dumps({"hidden": True, "templateOnly": True}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(center, "resolve_global_workflow_roots", lambda: [])

    scanned = await center.scan_skill_workflows(tmp_path)

    assert [item["name"] for item in scanned] == ["visible"]


@pytest.mark.asyncio
async def test_publish_invoke_stop_workflow_service(
    tmp_path: Path,
    isolated_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish should create runtime records and allow invoke/stop."""
    wf_dir = tmp_path / ".flocks" / "workflow" / "publishable"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.json").write_text(
        json.dumps(_workflow_payload("publishable")),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(center, "resolve_global_workflow_roots", lambda: [])
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_DRIVER", "docker")
    monkeypatch.setenv("FLOCKS_WORKFLOW_SERVICE_PIP_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    scanned = await center.scan_skill_workflows()
    workflow_id = scanned[0]["workflowId"]

    docker_calls = []
    json_post_calls = []
    port_requests = []

    async def fake_exec_docker(args, allow_failure=False, **_kwargs):
        docker_calls.append((args, allow_failure))
        return ("container-abc\n", "", 0)

    async def fake_allocate_port(*args, **kwargs) -> int:
        port_requests.append((args, kwargs))
        return 19123

    async def fake_wait_docker_service_healthy(*_args, **_kwargs) -> bool:
        return True

    def fake_json_post(*args, **kwargs):
        json_post_calls.append((args, kwargs))
        return {"status": "SUCCEEDED", "outputs": {"answer": 42}, "run_id": "run-1"}

    monkeypatch.setattr(center, "exec_docker", fake_exec_docker)
    monkeypatch.setattr(center, "_allocate_port", fake_allocate_port)
    monkeypatch.setattr(center, "_wait_docker_service_healthy", fake_wait_docker_service_healthy)
    monkeypatch.setattr(center, "_json_post", fake_json_post)

    published = await center.publish_workflow(workflow_id)
    assert published["status"] == "active"
    assert published["hostPort"] == 19123
    assert len(published["apiKey"]) == 64
    registry = await center.WorkflowStore.kv_get(center._registry_key(workflow_id))
    assert registry["servicePort"] == 19123

    invoked = await center.invoke_published_workflow(workflow_id, inputs={"k": "v"})
    assert invoked["status"] == "SUCCEEDED"
    assert invoked["outputs"] == {"answer": 42}
    assert invoked["workflowId"] == workflow_id

    stopped = await center.stop_workflow_service(workflow_id)
    assert stopped["status"] == "stopped"
    assert stopped["stopped"] is True
    registry = await center.WorkflowStore.kv_get(center._registry_key(workflow_id))
    assert registry["servicePort"] == 19123

    republished = await center.publish_workflow(workflow_id)
    assert republished["hostPort"] == 19123
    assert port_requests[-1][0][0] == 19123
    await center.stop_workflow_service(workflow_id)

    assert any(call[0][:3] == ["run", "-d", "--name"] for call in docker_calls)
    run_call = next(call for call in docker_calls if call[0][:3] == ["run", "-d", "--name"])
    run_args = " ".join(run_call[0])
    assert "python -m pip install uv" in run_args
    assert (
        "uv pip install --system -r /runtime/requirements.txt" in run_args
        or "uv pip install --system /app" in run_args
    )
    assert "/runtime" in run_call[0]
    assert "-w" in run_call[0]
    assert "/runtime" in run_call[0][run_call[0].index("-w") + 1]
    assert "-e" in run_call[0]
    assert any(arg.endswith(":/root/.cache/pip") for arg in run_call[0])
    assert any(arg.endswith(":/root/.cache/uv") for arg in run_call[0])
    assert "UV_CACHE_DIR=/root/.cache/uv" in run_call[0]
    assert f"FLOCKS_WORKFLOW_SERVICE_API_KEY={published['apiKey']}" in run_call[0]
    assert "PIP_INDEX_URL=https://mirror.example/simple" in run_call[0]
    assert "UV_DEFAULT_INDEX=https://mirror.example/simple" in run_call[0]
    assert "HTTP_PROXY=http://host.docker.internal:7897" in run_call[0]
    assert "HTTP_PROXY=http://127.0.0.1:7897" not in run_call[0]
    assert "--add-host" in run_call[0]
    assert "host.docker.internal:host-gateway" in run_call[0]
    assert json_post_calls
    assert json_post_calls[0][0][0] == "http://127.0.0.1:19123/invoke"
    assert json_post_calls[0][0][3] == {"x-api-key": published["apiKey"]}
    assert any(call[0][:2] == ["rm", "-f"] for call in docker_calls)
