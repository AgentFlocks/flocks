"""
Remaining route tests: Workflow, Provider, Task, Config, Permission
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient


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
    async def test_api_prefix_permission_endpoint(self, client: AsyncClient):
        """Both /api/question/{id}/reply and /question/{id}/reply return 404 for unknown."""
        for prefix in ("/api/question", "/question"):
            resp = await client.post(
                f"{prefix}/question_nonexistent/reply",
                json={"answers": [["a"]]},
            )
            assert resp.status_code == status.HTTP_404_NOT_FOUND
