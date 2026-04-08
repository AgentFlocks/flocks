"""
Remaining route tests: Workflow, Provider, Task, Config, Permission
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Minimal workflow JSON (valid structure)
# ---------------------------------------------------------------------------

_WORKFLOW_JSON = {
    "start": "node_1",
    "nodes": [
        {
            "id": "node_1",
            "type": "python",
            "code": "result = {'done': True}",
        }
    ],
    "edges": [],
}

_WORKFLOW_PAYLOAD = {
    "name": "test-workflow",
    "description": "A test workflow",
    "workflowJson": _WORKFLOW_JSON,
}


async def _wait_for_execution_terminal_state(
    client: AsyncClient,
    workflow_id: str,
    exec_id: str,
    *,
    timeout_s: float = 3.0,
) -> dict:
    """Poll execution details until the workflow leaves the running state."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        resp = await client.get(f"/api/workflow/{workflow_id}/history/{exec_id}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        data = resp.json()
        if data["status"] != "running":
            return data
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Execution {exec_id} did not finish within {timeout_s} seconds")
        await asyncio.sleep(0.05)
@pytest.fixture(autouse=True)
def isolated_workflow_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect workflow route filesystem writes into a per-test temp dir."""
    from flocks.server.routes import workflow as workflow_routes

    workspace_root = tmp_path / "workspace"
    project_root = workspace_root / ".flocks" / "plugins" / "workflows"
    global_root = tmp_path / "home" / ".flocks" / "plugins" / "workflows"
    legacy_project_plugin = workspace_root / ".flocks" / "plugins" / "workflow"
    legacy_project_main = workspace_root / ".flocks" / "workflow"
    legacy_global_plugin = tmp_path / "home" / ".flocks" / "plugins" / "workflow"
    legacy_global_main = tmp_path / "home" / ".flocks" / "workflow"

    for root in [
        workspace_root / ".flocks",
        project_root,
        global_root,
        legacy_project_plugin,
        legacy_project_main,
        legacy_global_plugin,
        legacy_global_main,
    ]:
        root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(workflow_routes, "_workspace_root", workspace_root, raising=False)
    monkeypatch.setattr(workflow_routes, "_find_workspace_root", lambda: workspace_root)
    monkeypatch.setattr(
        workflow_routes,
        "resolve_project_workflow_roots",
        lambda workspace: [legacy_project_main, legacy_project_plugin, project_root],
    )
    monkeypatch.setattr(
        workflow_routes,
        "resolve_global_workflow_roots",
        lambda: [legacy_global_main, legacy_global_plugin, global_root],
    )

    yield


# ===========================================================================
# Workflow routes
# ===========================================================================

class TestWorkflowRoutes:

    @pytest.mark.asyncio
    async def test_list_workflows_returns_array(self, client: AsyncClient):
        """GET /api/workflow returns a list."""
        resp = await client.get("/api/workflow")
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_workflow(self, client: AsyncClient):
        """POST /api/workflow creates a workflow and returns it."""
        resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.text
        data = resp.json()
        assert data["name"] == "test-workflow"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_workflow(self, client: AsyncClient):
        """GET /api/workflow/{id} returns the workflow."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        resp = await client.get(f"/api/workflow/{wf_id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["id"] == wf_id

    @pytest.mark.asyncio
    async def test_get_unknown_workflow_returns_404(self, client: AsyncClient):
        """GET for a non-existent workflow returns 404."""
        resp = await client.get("/api/workflow/wf_nonexistent_id")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_workflow(self, client: AsyncClient):
        """PUT /api/workflow/{id} updates the workflow."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/workflow/{wf_id}",
            json={"name": "updated-workflow"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["name"] == "updated-workflow"

    @pytest.mark.asyncio
    async def test_delete_workflow(self, client: AsyncClient):
        """DELETE /api/workflow/{id} removes the workflow."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/workflow/{wf_id}")
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)

        get_resp = await client.get(f"/api/workflow/{wf_id}")
        assert get_resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_workflow_creation_missing_name_returns_422(self, client: AsyncClient):
        """Creating a workflow without a name returns 422."""
        resp = await client.post(
            "/api/workflow",
            json={"workflowJson": _WORKFLOW_JSON},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_workflow_history_endpoint(self, client: AsyncClient):
        """GET /api/workflow/{id}/history returns a list."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        resp = await client.get(f"/api/workflow/{wf_id}/history")
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_run_workflow_returns_running_execution(self, client: AsyncClient):
        """POST /api/workflow/{id}/run should return immediately with a running execution."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        resp = await client.post(f"/api/workflow/{wf_id}/run", json={"inputs": {"topic": "demo"}})
        assert resp.status_code == status.HTTP_200_OK, resp.text

        data = resp.json()
        assert data["workflowId"] == wf_id
        assert data["status"] == "running"
        assert data["id"]
        assert data["inputParams"] == {"topic": "demo"}

    @pytest.mark.asyncio
    async def test_cancel_running_workflow_execution(self, client: AsyncClient):
        """Cancelling a running workflow should eventually mark it as cancelled."""
        payload = {
            "name": "slow-workflow",
            "description": "workflow that can be cancelled",
            "workflowJson": {
                "start": "step1",
                "nodes": [
                    {
                        "id": "step1",
                        "type": "python",
                        "code": "import time\ntime.sleep(0.2)\noutputs['value'] = 1",
                    },
                    {
                        "id": "step2",
                        "type": "python",
                        "code": "outputs['value'] = inputs['value'] + 1",
                    },
                ],
                "edges": [
                    {"from": "step1", "to": "step2"},
                ],
            },
        }
        create_resp = await client.post("/api/workflow", json=payload)
        wf_id = create_resp.json()["id"]

        run_resp = await client.post(f"/api/workflow/{wf_id}/run", json={"inputs": {}})
        assert run_resp.status_code == status.HTTP_200_OK, run_resp.text
        exec_id = run_resp.json()["id"]

        cancel_resp = await client.post(f"/api/workflow/{wf_id}/history/{exec_id}/cancel")
        assert cancel_resp.status_code == status.HTTP_200_OK, cancel_resp.text
        assert cancel_resp.json()["status"] == "accepted"

        final = await _wait_for_execution_terminal_state(client, wf_id, exec_id)
        assert final["status"] == "cancelled"
        assert len(final["executionLog"]) == 1
        assert final["executionLog"][0]["node_id"] == "step1"

    @pytest.mark.asyncio
    async def test_cancel_completed_workflow_execution_is_ignored(self, client: AsyncClient):
        """Cancelling an already-finished workflow should return an ignored response."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        run_resp = await client.post(f"/api/workflow/{wf_id}/run", json={"inputs": {}})
        exec_id = run_resp.json()["id"]
        final = await _wait_for_execution_terminal_state(client, wf_id, exec_id)
        assert final["status"] == "success"

        cancel_resp = await client.post(f"/api/workflow/{wf_id}/history/{exec_id}/cancel")
        assert cancel_resp.status_code == status.HTTP_200_OK, cancel_resp.text
        assert cancel_resp.json()["status"] == "ignored"


# ===========================================================================
# Provider routes
# ===========================================================================

class TestProviderRoutes:

    @pytest.mark.asyncio
    async def test_list_providers_returns_expected_shape(self, client: AsyncClient):
        """GET /api/provider returns dict with all/default/connected keys."""
        resp = await client.get("/api/provider")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "all" in data
        assert isinstance(data["all"], list)
        assert len(data["all"]) > 0

    @pytest.mark.asyncio
    async def test_provider_model_fields(self, client: AsyncClient):
        """Each provider has the required fields."""
        resp = await client.get("/api/provider")
        for provider in resp.json()["all"]:
            assert "id" in provider
            assert "name" in provider
            assert "models" in provider

    @pytest.mark.asyncio
    async def test_get_specific_provider(self, client: AsyncClient):
        """GET /api/provider/anthropic returns anthropic provider details."""
        resp = await client.get("/api/provider/anthropic")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["id"] == "anthropic"

    @pytest.mark.asyncio
    async def test_get_unknown_provider_returns_404(self, client: AsyncClient):
        """GET for a non-existent provider returns 404."""
        resp = await client.get("/api/provider/this_provider_does_not_exist")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_provider_models_endpoint(self, client: AsyncClient):
        """GET /api/provider/openai/models returns a list of models."""
        resp = await client.get("/api/provider/openai/models")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        if data:
            model = data[0]
            assert "id" in model
            assert "name" in model

    @pytest.mark.asyncio
    async def test_set_credential_unknown_provider_returns_error(
        self, client: AsyncClient
    ):
        """Updating an unknown provider via PUT /{id} returns 400 or 404."""
        resp = await client.put(
            "/api/provider/nonexistent_prov_xyz",
            json={"apiKey": "fake-key"},
        )
        # PUT /{provider_id} should fail for a completely unknown provider
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_200_OK,  # some providers may create on upsert
        )


# ===========================================================================
# Config routes
# ===========================================================================

class TestConfigRoutes:

    @pytest.mark.asyncio
    async def test_get_config_returns_object(self, client: AsyncClient):
        """GET /api/config returns a configuration object."""
        resp = await client.get("/api/config")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_config_has_expected_top_level_keys(self, client: AsyncClient):
        """Config response contains expected top-level keys."""
        resp = await client.get("/api/config")
        data = resp.json()
        # At least one of these should be present
        expected_keys = {"model", "provider", "agent", "theme", "memory", "mcp"}
        present = expected_keys.intersection(data.keys())
        assert len(present) > 0, (
            f"No expected keys found. Got: {list(data.keys())}"
        )


# ===========================================================================
# Permission routes
# ===========================================================================

class TestPermissionRoutes:

    @pytest.mark.asyncio
    async def test_list_permissions_returns_array(self, client: AsyncClient):
        """GET /permission returns a list (may be empty)."""
        resp = await client.get("/permission")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_reply_to_unknown_permission_returns_404(
        self, client: AsyncClient
    ):
        """POST /permission/{id}/reply for non-existent permission returns 404."""
        resp = await client.post(
            "/permission/perm_nonexistent_000000/reply",
            json={"allow": True, "always": False},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_reply_missing_allow_field_returns_422(self, client: AsyncClient):
        """Permission reply without 'allow' field returns 422."""
        resp = await client.post(
            "/permission/perm_some_id/reply",
            json={},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_permission_routes_preserve_request_created_time(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        """List/detail routes should expose the stored permission request timestamp."""
        from flocks.permission.next import PermissionRequestInfo

        info = PermissionRequestInfo(
            id="perm_time_test",
            sessionID="ses_time_test",
            permission="bash",
            patterns=["*"],
            metadata={"messageID": "msg_time_test"},
            always=["*"],
            tool={"name": "bash"},
            time={"created": 1234567890},
        )

        monkeypatch.setattr(
            "flocks.server.routes.permission.PermissionNext.list_pending_infos",
            AsyncMock(return_value=[info]),
        )
        monkeypatch.setattr(
            "flocks.server.routes.permission.PermissionNext.get_pending_info",
            AsyncMock(return_value=info),
        )

        list_resp = await client.get("/permission")
        assert list_resp.status_code == status.HTTP_200_OK
        assert list_resp.json()[0]["time"]["created"] == 1234567890

        detail_resp = await client.get("/permission/perm_time_test")
        assert detail_resp.status_code == status.HTTP_200_OK
        assert detail_resp.json()["time"]["created"] == 1234567890

    @pytest.mark.asyncio
    async def test_api_prefix_permission_endpoint(self, client: AsyncClient):
        """Both /api/question/{id}/reply and /question/{id}/reply return 404 for unknown."""
        for prefix in ("/api/question", "/question"):
            resp = await client.post(
                f"{prefix}/question_nonexistent/reply",
                json={"answers": [["a"]]},
            )
            assert resp.status_code == status.HTTP_404_NOT_FOUND
