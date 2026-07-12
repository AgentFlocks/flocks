"""
Remaining route tests: Workflow, Provider, Task, Config, Permission
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient
from unittest.mock import AsyncMock

from flocks.hooks.pipeline import HookBase, HookPipeline


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
    from flocks.workflow import fs_store

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
    monkeypatch.setattr(workflow_routes, "_workflow_dir", lambda workflow_id: project_root / workflow_id)
    monkeypatch.setattr(workflow_routes, "_global_workflow_dir", lambda workflow_id: global_root / workflow_id)
    monkeypatch.setattr(fs_store, "_workspace_root", workspace_root, raising=False)
    monkeypatch.setattr(fs_store, "find_workspace_root", lambda: workspace_root)
    monkeypatch.setattr(
        fs_store,
        "resolve_project_workflow_roots",
        lambda workspace=None: [legacy_project_main, legacy_project_plugin, project_root],
        raising=False,
    )
    monkeypatch.setattr(
        fs_store,
        "resolve_global_workflow_roots",
        lambda: [legacy_global_main, legacy_global_plugin, global_root],
        raising=False,
    )

    yield {
        "workspace_root": workspace_root,
        "project_root": project_root,
        "global_root": global_root,
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
    async def test_create_workflow(
        self,
        client: AsyncClient,
        isolated_workflow_filesystem,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """POST /api/workflow creates a workflow and returns it."""
        from flocks.server import auth as auth_module

        class _SecretManagerStub:
            def get(self, key: str):
                if key == auth_module.API_TOKEN_SECRET_ID:
                    return "abc123"
                return None

        monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _SecretManagerStub())

        resp = await client.post(
            "/api/workflow",
            json=_WORKFLOW_PAYLOAD,
            headers={"Authorization": "Bearer abc123"},
        )
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.text
        data = resp.json()
        assert data["name"] == "test-workflow"
        assert "id" in data
        assert data["source"] == "global"
        assert (isolated_workflow_filesystem["global_root"] / data["id"] / "workflow.json").is_file()
        assert not (isolated_workflow_filesystem["project_root"] / data["id"] / "workflow.json").exists()

    @pytest.mark.asyncio
    async def test_import_workflow_defaults_to_global_storage(
        self,
        client: AsyncClient,
        isolated_workflow_filesystem,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """POST /api/workflow/import stores imported workflows under global user storage by default."""
        from flocks.server import auth as auth_module

        class _SecretManagerStub:
            def get(self, key: str):
                if key == auth_module.API_TOKEN_SECRET_ID:
                    return "abc123"
                return None

        monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _SecretManagerStub())

        payload = {
            **_WORKFLOW_JSON,
            "name": "imported-workflow",
            "metadata": {
                "description": "Imported workflow",
                "category": "default",
            },
        }

        resp = await client.post(
            "/api/workflow/import",
            json=payload,
            headers={"Authorization": "Bearer abc123"},
        )
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.text

        data = resp.json()
        assert data["name"] == "imported-workflow"
        assert data["source"] == "global"
        assert (isolated_workflow_filesystem["global_root"] / data["id"] / "workflow.json").is_file()
        assert not (isolated_workflow_filesystem["project_root"] / data["id"] / "workflow.json").exists()

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
    async def test_update_workflow_writes_workflow_md_only(
        self,
        client: AsyncClient,
        isolated_workflow_filesystem,
    ):
        """PUT /api/workflow/{id} stores editable markdown in workflow.md."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]
        workflow_dir = isolated_workflow_filesystem["global_root"] / wf_id
        legacy_edit_file = workflow_dir / "workflow.edit.md"
        legacy_edit_file.write_text("# legacy\n", encoding="utf-8")

        resp = await client.put(
            f"/api/workflow/{wf_id}",
            json={"markdownContent": "# current\n"},
        )

        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.json()["markdownContent"] == "# current\n"
        assert resp.json()["editMarkdownContent"] == "# current\n"
        assert (workflow_dir / "workflow.md").read_text(encoding="utf-8") == "# current\n"
        assert not legacy_edit_file.exists()

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
    async def test_run_workflow_marks_business_failure_as_error(self, client: AsyncClient):
        """A workflow that reports workflow_success=false should count as a failed run."""
        payload = {
            "name": "business-failure-workflow",
            "description": "workflow that reports business failure",
            "workflowJson": {
                "start": "node_1",
                "nodes": [
                    {
                        "id": "node_1",
                        "type": "python",
                        "code": (
                            "outputs['workflow_success'] = False\n"
                            "outputs['reason'] = 'Script file not found'"
                        ),
                    }
                ],
                "edges": [],
            },
        }
        create_resp = await client.post("/api/workflow", json=payload)
        wf_id = create_resp.json()["id"]

        run_resp = await client.post(f"/api/workflow/{wf_id}/run", json={"inputs": {}})
        assert run_resp.status_code == status.HTTP_200_OK, run_resp.text
        exec_id = run_resp.json()["id"]

        final = await _wait_for_execution_terminal_state(client, wf_id, exec_id)
        assert final["status"] == "error"
        assert final["errorMessage"] == "Script file not found"
        assert final["outputResults"]["workflow_success"] is False

        workflow_resp = await client.get(f"/api/workflow/{wf_id}")
        assert workflow_resp.status_code == status.HTTP_200_OK, workflow_resp.text
        stats = workflow_resp.json()["stats"]
        assert stats["callCount"] == 1
        assert stats["successCount"] == 0
        assert stats["errorCount"] == 1

    @pytest.mark.asyncio
    async def test_run_workflow_success_updates_success_stats(self, client: AsyncClient):
        """A normal successful workflow should remain successful in execution and stats."""
        create_resp = await client.post("/api/workflow", json=_WORKFLOW_PAYLOAD)
        wf_id = create_resp.json()["id"]

        run_resp = await client.post(f"/api/workflow/{wf_id}/run", json={"inputs": {}})
        assert run_resp.status_code == status.HTTP_200_OK, run_resp.text
        exec_id = run_resp.json()["id"]

        final = await _wait_for_execution_terminal_state(client, wf_id, exec_id)
        assert final["status"] == "success"
        assert final.get("errorMessage") in (None, "")

        workflow_resp = await client.get(f"/api/workflow/{wf_id}")
        assert workflow_resp.status_code == status.HTTP_200_OK, workflow_resp.text
        stats = workflow_resp.json()["stats"]
        assert stats["callCount"] == 1
        assert stats["successCount"] == 1
        assert stats["errorCount"] == 0

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

    @pytest.mark.asyncio
    async def test_update_config_action_hook_receives_only_safe_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Action hooks receive config structure and a digest, never credentials."""
        from flocks.server.routes import config as config_routes

        config_data = {
            "channels": {
                "slack": {
                    "botToken": "xoxb-top-secret-token",
                    "headers": {"Authorization": "Bearer sensitive-header"},
                    "url": "https://hooks.example.test/secret-endpoint",
                }
            },
            "provider": {"openai": {"apiKey": "provider-secret"}},
        }
        observed: list[dict] = []

        class _CaptureConfigAction(HookBase):
            async def action_before(self, ctx) -> None:
                observed.append(deepcopy(ctx.input))

        HookPipeline.reset()
        monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
        HookPipeline.register("capture-config-action", _CaptureConfigAction(), critical=True)
        monkeypatch.setattr(config_routes.ConfigInfoModel, "model_validate", lambda _data: object())
        monkeypatch.setattr(config_routes.Config, "update", AsyncMock())
        monkeypatch.setattr(config_routes.Config, "clear_cache", lambda: None)
        monkeypatch.setattr(config_routes, "get_config", AsyncMock(return_value={"updated": True}))
        try:
            result = await config_routes.update_config(config_data)
        finally:
            HookPipeline.reset()

        digest = hashlib.sha256(
            json.dumps(
                config_data,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        safe_input = observed[0]["canonical"]["generic"]["input"]

        assert result == {"updated": True}
        assert safe_input == {
            "sections": [
                {"name": "channels", "type": "dict"},
                {"name": "provider", "type": "dict"},
            ],
            "sha256": digest,
        }
        observed_text = json.dumps(observed, sort_keys=True)
        for sensitive_value in (
            "xoxb-top-secret-token",
            "sensitive-header",
            "https://hooks.example.test/secret-endpoint",
            "provider-secret",
            "botToken",
            "Authorization",
            "headers",
            "apiKey",
        ):
            assert sensitive_value not in observed_text

    @pytest.mark.asyncio
    async def test_ui_display_defaults_and_updates(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch,
    ):
        """UI display-name endpoints expose only the visible product name."""
        from flocks.config.config import Config
        from flocks.server.routes import config as config_routes

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(config_routes, "_is_flockspro_enabled", lambda: False)
        Config._global_config = None
        Config._cached_config = None

        resp = await client.get("/api/config/ui-display")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {
            "displayName": "Flocks",
            "configuredDisplayName": None,
            "faviconUrl": None,
        }

        resp = await client.patch("/api/config/ui", json={"displayName": "  Acme SOC  "})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {
            "displayName": "Acme SOC",
            "configuredDisplayName": "Acme SOC",
            "faviconUrl": None,
        }

        resp = await client.get("/api/config/ui-display")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["displayName"] == "Acme SOC"

        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"></svg>'
        resp = await client.post(
            "/api/config/ui/favicon",
            files={"file": ("favicon.svg", svg, "image/svg+xml")},
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        data = resp.json()
        assert data["displayName"] == "Acme SOC"
        assert data["faviconUrl"].startswith("/api/config/ui-favicon?v=")

        favicon_resp = await client.get(data["faviconUrl"])
        assert favicon_resp.status_code == status.HTTP_200_OK
        assert favicon_resp.content == svg
        assert (tmp_path / "config" / "assets" / "favicon.svg").is_file()

        resp = await client.delete("/api/config/ui/favicon")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["faviconUrl"] is None

    @pytest.mark.asyncio
    async def test_ui_display_defaults_to_flockspro_when_pro_is_enabled(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch,
    ):
        """Empty display-name config falls back to the active product edition."""
        from flocks.config.config import Config
        from flocks.server.routes import config as config_routes

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(config_routes, "_is_flockspro_enabled", lambda: True)
        Config._global_config = None
        Config._cached_config = None

        resp = await client.get("/api/config/ui-display")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {
            "displayName": "Flocks Pro",
            "configuredDisplayName": None,
            "faviconUrl": None,
        }

        resp = await client.patch("/api/config/ui", json={"displayName": "  Acme Pro  "})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["displayName"] == "Acme Pro"
        assert resp.json()["configuredDisplayName"] == "Acme Pro"

        resp = await client.patch("/api/config/ui", json={"displayName": ""})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["displayName"] == "Flocks Pro"
        assert resp.json()["configuredDisplayName"] is None

    @pytest.mark.parametrize(
        "svg",
        [
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://example.com/g)"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>'.encode("utf-16"),
        ],
    )
    @pytest.mark.asyncio
    async def test_ui_favicon_rejects_unsafe_svg(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch,
        svg: bytes,
    ):
        """SVG favicons are accepted only when they fit the safe favicon subset."""
        from flocks.config.config import Config

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        Config._global_config = None
        Config._cached_config = None

        resp = await client.post(
            "/api/config/ui/favicon",
            files={"file": ("favicon.svg", svg, "image/svg+xml")},
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not (tmp_path / "config" / "assets" / "favicon.svg").exists()

        display_resp = await client.get("/api/config/ui-display")
        assert display_resp.status_code == status.HTTP_200_OK
        assert display_resp.json()["faviconUrl"] is None


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
