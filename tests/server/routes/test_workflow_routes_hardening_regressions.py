"""Regression tests for workflow route defects found during the 2026-09 test pass.

Each test pins one fixed behaviour of ``flocks/server/routes/workflow.py``:

* path-traversal workflow ids never reach the filesystem (``..`` used to rmtree
  the parent of the workflow root),
* validation gaps (empty name, cron expressions, syslog protocol),
* ``PUT`` keeps ``metadata.runtime`` defaults when the client omits metadata,
* ``import`` runs the same strict/schema lint as ``create``,
* webhook secrets are write-only in responses and survive round-trips,
* webhook replay protection and 400/403 mappings for dispatch errors,
* ``GET /workflow/stats`` is not shadowed by ``/workflow/{workflow_id}``,
* executions left ``running`` by a previous process are finalized at startup,
* non-admin users never see a published service's plaintext API key.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import flocks.server.routes.workflow as workflow_module
from flocks.config.config import get_user_plugins_root
from flocks.workflow import fs_store
from flocks.workflow.store import WorkflowStore


def _wf_json(**extra: Any) -> dict[str, Any]:
    payload = {
        "start": "a",
        "nodes": [{"id": "a", "type": "python", "code": "outputs['r'] = inputs.get('x', 0) + 1"}],
        "edges": [],
    }
    payload.update(extra)
    return payload


def _global_workflows_root() -> Path:
    """The user-level workflow root: ``<FLOCKS_ROOT>/plugins/workflows``.

    Guarded so a broken fixture can never point the tests at the real home or
    the real ``~/.flocks``: every route test in this module creates/deletes
    directories here.
    """
    root = get_user_plugins_root() / "workflows"
    assert str(root).startswith(str(Path(os.environ["G4A_TEST_HOME_ROOT"]))), root
    return root


def _write_workflow(workflow_id: str, workflow_json: dict[str, Any]) -> Path:
    base = _global_workflows_root() / workflow_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "workflow.json").write_text(json.dumps(workflow_json), encoding="utf-8")
    (base / "meta.json").write_text(
        json.dumps({"id": workflow_id, "name": workflow_id, "category": "default", "status": "draft", "createdAt": 1, "updatedAt": 1}),
        encoding="utf-8",
    )
    return base


@pytest.fixture(autouse=True)
def _isolated_home_and_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both workflow roots at ``tmp_path``.

    ``tests/server/conftest.py`` isolates ``FLOCKS_ROOT`` only; the workflow
    routes resolve the user-level root through ``Path.home()`` and the
    project-level root by walking up from ``cwd``. Without this fixture the
    tests below would create and delete directories inside the real
    ``~/.flocks/plugins`` and the repository's ``.flocks/plugins``.
    """
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir()
    (project / ".flocks").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("G4A_TEST_HOME_ROOT", str(tmp_path))
    monkeypatch.chdir(project)
    monkeypatch.setattr(fs_store, "_workspace_root", None)
    monkeypatch.setattr(workflow_module, "publish_event", AsyncMock(return_value=None))
    assert Path.home() == home
    assert fs_store.find_workspace_root() == project.resolve()
    # tests/server/conftest.py points FLOCKS_ROOT at tmp_path/.flocks; the
    # user-level workflow root must follow it, never ``~/.flocks``.
    assert get_user_plugins_root() == Path(os.environ["FLOCKS_ROOT"]) / "plugins"


@pytest.fixture
def quiet_trigger_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trigger saves restart legacy managers; keep them inert in unit tests."""
    monkeypatch.setattr(
        workflow_module,
        "default_trigger_runtime",
        SimpleNamespace(
            restart_workflow=AsyncMock(return_value={}),
            get_trigger_status=AsyncMock(return_value={"state": "ready"}),
            get_workflow_trigger_statuses=AsyncMock(return_value=[]),
            dispatch_event=AsyncMock(return_value={"matched": True, "executed": True, "inputs": {}}),
        ),
    )


# ---------------------------------------------------------------------------
# workflow id safety (DELETE /api/workflow/.. wiped the workflow root's parent)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_id", ["..", ".", "../x", "a/b", "a\\b", "x\x00y", " "])
def test_unsafe_workflow_ids_are_rejected(bad_id: str) -> None:
    assert fs_store.is_safe_workflow_id(bad_id) is False
    assert fs_store.read_workflow_from_fs(bad_id) is None


def test_safe_workflow_ids_are_accepted() -> None:
    assert fs_store.is_safe_workflow_id("3fa1c2d4-0000-4000-8000-000000000000")
    assert fs_store.is_safe_workflow_id("my_workflow.v2")


@pytest.mark.asyncio
async def test_delete_dotdot_never_touches_parent_directory(client: AsyncClient) -> None:
    root = _global_workflows_root()
    keep = _write_workflow("keep-me", _wf_json())
    # The dangerous shape: the parent of the workflow root happens to hold a workflow.json.
    (root.parent / "workflow.json").write_text(json.dumps(_wf_json()), encoding="utf-8")

    response = await client.delete("/api/workflow/%2e%2e")

    assert response.status_code == 404
    assert keep.is_dir() and (keep / "workflow.json").is_file()
    assert (root.parent / "workflow.json").is_file()
    assert workflow_module._delete_workflow_from_fs("..") is False


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_workflow_rejects_empty_name(client: AsyncClient) -> None:
    response = await client.post("/api/workflow", json={"name": "", "workflowJson": _wf_json()})
    assert response.status_code == 422
    assert ["body", "name"] in [d["loc"] for d in response.json()["details"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [" ", "\t ", " 　\n"])
async def test_create_workflow_rejects_whitespace_only_name(client: AsyncClient, blank: str) -> None:
    response = await client.post("/api/workflow", json={"name": blank, "workflowJson": _wf_json()})
    assert response.status_code == 422, response.text
    assert ["body", "name"] in [d["loc"] for d in response.json()["details"]]
    listed = await client.get("/api/workflow")
    assert all(item["name"].strip() for item in listed.json())


@pytest.mark.asyncio
async def test_update_workflow_rejects_whitespace_only_name(client: AsyncClient) -> None:
    created = await client.post("/api/workflow", json={"name": "named-flow", "workflowJson": _wf_json()})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]

    rejected = await client.put(f"/api/workflow/{workflow_id}", json={"name": " \t"})
    assert rejected.status_code == 422, rejected.text
    current = await client.get(f"/api/workflow/{workflow_id}")
    assert current.json()["name"] == "named-flow"

    renamed = await client.put(f"/api/workflow/{workflow_id}", json={"name": "renamed flow"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "renamed flow"


@pytest.mark.asyncio
async def test_update_workflow_keeps_runtime_defaults_when_metadata_omitted(client: AsyncClient) -> None:
    created = await client.post("/api/workflow", json={"name": "keep-runtime", "workflowJson": _wf_json()})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert created.json()["workflowJson"]["metadata"]["runtime"]["strict_edge_mapping"] is True

    two_nodes = {
        "start": "a",
        "nodes": [{"id": "a", "type": "python", "code": "outputs['r']=1"}, {"id": "b", "type": "python", "code": "y=1"}],
        "edges": [{"from": "a", "to": "b"}],
    }
    rejected = await client.put(f"/api/workflow/{workflow_id}", json={"workflowJson": two_nodes})
    assert rejected.status_code == 400
    assert rejected.json()["message"].startswith("Workflow strict edge mapping failed")

    two_nodes["edges"] = [{"from": "a", "to": "b", "mapping": {"v": "$.r"}}]
    accepted = await client.put(f"/api/workflow/{workflow_id}", json={"workflowJson": two_nodes})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["workflowJson"]["metadata"]["runtime"] == {
        "strict_edge_mapping": True,
        "dataflow_mode": "vertex_cache",
    }


@pytest.mark.asyncio
async def test_import_workflow_runs_strict_edge_mapping_lint(client: AsyncClient) -> None:
    body = {
        "start": "a",
        "nodes": [{"id": "a", "type": "python", "code": "x=1"}, {"id": "b", "type": "python", "code": "y=1"}],
        "edges": [{"from": "a", "to": "b"}],
        "metadata": {"runtime": {"strict_edge_mapping": True}},
    }
    response = await client.post("/api/workflow/import", json=body)
    assert response.status_code == 400
    assert response.json()["message"].startswith("Workflow strict edge mapping failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "  \t", None])
async def test_import_workflow_with_blank_name_uses_default_name(client: AsyncClient, blank) -> None:
    response = await client.post("/api/workflow/import", json={**_wf_json(), "name": blank})
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Imported Workflow"


@pytest.mark.asyncio
async def test_save_poller_config_rejects_invalid_cron_without_persisting(client: AsyncClient) -> None:
    _write_workflow("wf-cron", _wf_json())
    response = await client.post("/api/workflow/wf-cron/poller-config", json={"enabled": True, "cronExpression": "not a cron"})
    assert response.status_code == 400
    assert "Invalid cron expression" in response.json()["message"]
    assert await WorkflowStore.get_config("wf-cron", kind="workflow_poller_config") is None


@pytest.mark.asyncio
async def test_create_schedule_trigger_rejects_invalid_cron(client: AsyncClient, quiet_trigger_runtime: None) -> None:
    _write_workflow("wf-trig-cron", _wf_json())
    response = await client.post(
        "/api/workflow/wf-trig-cron/triggers",
        json={"id": "schedule-default", "type": "schedule", "source": {"mode": "cron", "cron": "bad cron"}},
    )
    assert response.status_code == 400
    listed = await client.get("/api/workflow/wf-trig-cron/triggers")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_syslog_config_rejects_unknown_protocol(client: AsyncClient) -> None:
    _write_workflow("wf-syslog", _wf_json())
    response = await client.post("/api/workflow/wf-syslog/syslog-config", json={"enabled": False, "protocol": "sctp", "port": 15140})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_workflow_files_live_under_flocks_root_not_home(client: AsyncClient) -> None:
    """FLOCKS_ROOT isolation used to be ignored by the workflow routes, which
    resolved ``Path.home() / ".flocks"`` directly and wrote into the default
    instance (this is how a test run once wiped the real ``~/.flocks/plugins``)."""
    flocks_root = Path(os.environ["FLOCKS_ROOT"])
    created = await client.post("/api/workflow", json={"name": "root-bound", "workflowJson": _wf_json()})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]

    on_disk = flocks_root / "plugins" / "workflows" / workflow_id / "workflow.json"
    assert on_disk.is_file()
    assert not (Path.home() / ".flocks").exists()
    assert workflow_module._global_workflow_dir(workflow_id) == on_disk.parent

    deleted = await client.delete(f"/api/workflow/{workflow_id}")
    assert deleted.status_code == 204
    assert not on_disk.parent.exists()
    assert not (Path.home() / ".flocks").exists()


# ---------------------------------------------------------------------------
# webhook triggers: write-only secrets, replay protection, dispatch errors
# ---------------------------------------------------------------------------
WEBHOOK = {
    "id": "tw1",
    "type": "webhook",
    "enabled": True,
    "source": {"path": "/hook", "method": "POST"},
    "auth": {"type": "api_key", "apiKey": "K123"},
    "mapping": {"x": "$.body.x"},
}


@pytest.mark.asyncio
async def test_trigger_api_key_is_write_only_and_survives_round_trip(client: AsyncClient, quiet_trigger_runtime: None) -> None:
    _write_workflow("wf-secret", _wf_json())
    created = await client.post("/api/workflow/wf-secret/triggers", json=WEBHOOK)
    assert created.status_code == 200, created.text
    assert "apiKey" not in created.json()["trigger"]["auth"]
    assert created.json()["trigger"]["auth"]["apiKeyConfigured"] is True

    listed = await client.get("/api/workflow/wf-secret/triggers")
    auth = listed.json()[0]["trigger"]["auth"]
    assert "apiKey" not in auth and auth["apiKeyConfigured"] is True

    # A client that resends the redacted record must not lose the stored key.
    resend = dict(listed.json()[0]["trigger"], name="renamed")
    updated = await client.put("/api/workflow/wf-secret/triggers/tw1", json=resend)
    assert updated.status_code == 200, updated.text
    stored = json.loads((_global_workflows_root() / "wf-secret" / "workflow.json").read_text())
    assert stored["triggers"][0]["auth"]["apiKey"] == "K123"

    # An explicit new key still replaces it.
    rotated = await client.put("/api/workflow/wf-secret/triggers/tw1", json=dict(WEBHOOK, auth={"type": "api_key", "apiKey": "NEW"}))
    assert rotated.status_code == 200
    stored = json.loads((_global_workflows_root() / "wf-secret" / "workflow.json").read_text())
    assert stored["triggers"][0]["auth"]["apiKey"] == "NEW"


@pytest.mark.asyncio
async def test_webhook_replays_run_again_when_the_store_records_no_deliveries(client: AsyncClient, quiet_trigger_runtime: None) -> None:
    _write_workflow("wf-replay", _wf_json())
    assert (await client.post("/api/workflow/wf-replay/triggers", json=WEBHOOK)).status_code == 200
    headers = {"x-api-key": "K123", "X-Flocks-Delivery-Id": "d-1"}

    first = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 1}, headers=headers)
    second = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 1}, headers=headers)
    assert [r.json()["duplicate"] for r in (first, second)] == [False, False]
    assert [r.json()["deliveryId"] for r in (first, second)] == ["d-1", "d-1"]
    assert workflow_module.default_trigger_runtime.dispatch_event.await_count == 2


@pytest.mark.asyncio
async def test_webhook_duplicate_delivery_is_not_executed_twice(
    client: AsyncClient, quiet_trigger_runtime: None, recording_webhook_store: dict[str, tuple[str, str]],
) -> None:
    _write_workflow("wf-replay", _wf_json())
    assert (await client.post("/api/workflow/wf-replay/triggers", json=WEBHOOK)).status_code == 200
    headers = {"x-api-key": "K123"}

    first = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 1}, headers=headers)
    second = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 1}, headers=headers)
    assert first.status_code == 200 and first.json()["executed"] is True
    assert second.status_code == 200
    assert second.json()["duplicate"] is True and second.json()["executed"] is False
    assert second.json()["deliveryId"] == first.json()["deliveryId"]
    assert workflow_module.default_trigger_runtime.dispatch_event.await_count == 1

    # A client-supplied delivery id keys the replay window instead of the body digest.
    a = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 2}, headers={**headers, "X-Flocks-Delivery-Id": "d-1"})
    b = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 3}, headers={**headers, "X-Flocks-Delivery-Id": "d-1"})
    c = await client.post("/webhook/workflows/wf-replay/tw1", json={"x": 2}, headers={**headers, "X-Flocks-Delivery-Id": "d-2"})
    assert a.json()["executed"] is True and a.json()["deliveryId"] == "d-1"
    assert b.json()["duplicate"] is True
    assert c.json()["executed"] is True


@pytest.mark.asyncio
async def test_webhook_api_key_compare_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.workflow.triggers import TriggerDefinition

    calls: list[tuple[bytes, bytes]] = []
    real = workflow_module.hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(workflow_module.hmac, "compare_digest", _spy)
    trigger = TriggerDefinition.model_validate(WEBHOOK)
    with pytest.raises(workflow_module.HTTPException) as exc_info:
        workflow_module._authorize_webhook_trigger(trigger, {"x-api-key": "K124"}, {}, raw_body=b"")
    assert exc_info.value.status_code == 401
    assert calls == [(b"K124", b"K123")]


@pytest.mark.asyncio
async def test_preview_and_test_map_dispatch_errors_to_400(client: AsyncClient, quiet_trigger_runtime: None) -> None:
    from flocks.workflow.triggers.dispatcher import TriggerDispatchError

    _write_workflow("wf-dispatch", _wf_json())
    bad_path = dict(WEBHOOK, mapping={"a": "$.body[0"})
    assert (await client.post("/api/workflow/wf-dispatch/triggers", json=bad_path)).status_code == 200
    preview = await client.post("/api/workflow/wf-dispatch/triggers/tw1/preview-mapping", json={"body": [1]})
    assert preview.status_code == 400 and "Invalid mapping path" in preview.json()["message"]

    workflow_module.default_trigger_runtime.dispatch_event = AsyncMock(side_effect=TriggerDispatchError("bad("))
    tested = await client.post("/api/workflow/wf-dispatch/triggers/tw1/test", json={"body": {}})
    assert tested.status_code == 400 and "Trigger filter error" in tested.json()["message"]


@pytest.mark.asyncio
async def test_delete_schedule_trigger_clears_legacy_poller_config(client: AsyncClient, quiet_trigger_runtime: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_workflow("wf-del-sched", _wf_json())
    schedule = {"id": "schedule-default", "type": "schedule", "enabled": False, "source": {"mode": "interval", "intervalSeconds": 3600}}
    assert (await client.post("/api/workflow/wf-del-sched/triggers", json=schedule)).status_code == 200

    async def _restart_writes_disabled_stub(workflow_id: str, workflow_json: dict[str, Any]) -> dict[str, Any]:
        # The real runtime re-syncs legacy configs on restart; make sure the
        # route removes the deleted trigger's record *after* that happens.
        await WorkflowStore.put_config(workflow_id, {"workflowId": workflow_id, "enabled": False}, kind="workflow_poller_config")
        return {}

    workflow_module.default_trigger_runtime.restart_workflow = _restart_writes_disabled_stub
    deleted = await client.delete("/api/workflow/wf-del-sched/triggers/schedule-default")
    assert deleted.status_code == 200
    config = await client.get("/api/workflow/wf-del-sched/poller-config")
    assert config.status_code == 200 and config.json() is None


# ---------------------------------------------------------------------------
# route ordering, startup finalization, service key redaction
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_aggregate_stats_route_is_not_shadowed(client: AsyncClient) -> None:
    _write_workflow("stats", _wf_json())  # a workflow literally named "stats" must not capture the route
    response = await client.get("/api/workflow/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["workflowId"] is None and "callCount" in body and "workflowJson" not in body


@pytest.mark.asyncio
async def test_run_rejects_invalid_definition_before_creating_execution(client: AsyncClient) -> None:
    _write_workflow("wf-bad-def", {"start": "a", "nodes": [{"id": "a", "type": "shell", "code": "ls"}], "edges": []})
    started = await client.post("/api/workflow/wf-bad-def/run", json={})
    assert started.status_code == 400, started.text
    assert "shell" in started.json()["message"]
    assert await WorkflowStore.list_executions("wf-bad-def") == []


@pytest.mark.asyncio
async def test_startup_marks_orphaned_running_executions_as_interrupted() -> None:
    from flocks.workflow import ownership

    await WorkflowStore.init()
    # Written by a process that is gone.
    await WorkflowStore.upsert_execution({"id": "orphan-1", "workflowId": "w", "status": "running", "startedAt": 1_000, "inputParams": {}, "executionLog": []})
    await WorkflowStore.upsert_execution({"id": "live-1", "workflowId": "w", "status": "running", "startedAt": 1_500, "inputParams": {}, "executionLog": []})
    ownership.start_renewal({"id": "live-1"})
    await WorkflowStore.upsert_execution({"id": "done-1", "workflowId": "w", "status": "success", "startedAt": 2_000, "inputParams": {}, "executionLog": []})

    marked = await workflow_module.mark_interrupted_executions()

    assert marked == 1
    orphan = await WorkflowStore.get_execution("orphan-1")
    assert orphan["status"] == "error" and orphan["currentPhase"] == "error"
    assert "interrupted" in orphan["errorMessage"].lower() and orphan["finishedAt"]
    # A run this process started is still going: recovery leaves it alone.
    assert (await WorkflowStore.get_execution("live-1"))["status"] == "running"
    assert (await WorkflowStore.get_execution("done-1"))["status"] == "success"
    assert await workflow_module.mark_interrupted_executions() == 0
    await ownership.stop_renewal("live-1")


@pytest.mark.asyncio
async def test_service_api_key_is_redacted_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.auth.context import AuthUser

    service = {"workflowId": "w", "status": "running", "apiKey": "secret-key", "serviceUrl": "http://127.0.0.1:19000"}
    runtime = {"status": "active", "serviceUrl": "http://127.0.0.1:19000", "driver": "local", "hostPort": 19000}

    async def _kv_get(key: str):
        return {"workflow_api_service/w": service, "workflow_runtime/w": runtime}.get(key)

    monkeypatch.setattr(workflow_module.WorkflowStore, "kv_get", _kv_get)
    monkeypatch.setattr(workflow_module.WorkflowStore, "kv_list_keys", AsyncMock(return_value=["workflow_api_service/w"]))

    def _request(role: str) -> Any:
        user = AuthUser(id="u", username="u", role=role, status="active", must_reset_password=False)
        return SimpleNamespace(state=SimpleNamespace(auth_user=user))

    member = await workflow_module.get_workflow_service("w", _request("member"))
    admin = await workflow_module.get_workflow_service("w", _request("admin"))
    assert "apiKey" not in member and member["apiKeyConfigured"] is True
    assert admin["apiKey"] == "secret-key"
    listed = await workflow_module.list_workflow_services(_request("member"))
    assert all("apiKey" not in item for item in listed)
    # In-process callers without a request keep the full record.
    assert (await workflow_module.get_workflow_service("w"))["apiKey"] == "secret-key"
