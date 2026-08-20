from __future__ import annotations

from pathlib import Path

import pytest


SAMPLE_IR = {
    "name": "flocks-test-route",
    "trigger": {
        "type": "webhook",
        "method": "POST",
        "path": "flocks-test-route",
        "responseMode": "responseNode",
    },
    "steps": [
        {
            "id": "build_response",
            "kind": "code",
            "name": "Build Response",
            "js_code": "return [{ json: { message: 'ok' } }];",
            "next": "respond",
        },
        {
            "id": "respond",
            "kind": "respond_to_webhook",
            "name": "Respond",
            "response_body": "={{ $json }}",
        },
    ],
    "tests": [
        {
            "name": "ok",
            "input": {"name": "Alice"},
            "expect": {"status": 200, "jsonContains": {"message": "ok"}},
        }
    ],
}


class FakeSecrets:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str):
        self.values[key] = value

    def delete(self, key: str):
        return self.values.pop(key, None) is not None


class FakeN8nClient:
    def __init__(self):
        self.deleted: list[str] = []

    def create_workflow(self, payload):
        return {"status": 200, "body": {"id": "wf-route-1", "active": False, "name": payload.get("name")}}

    def activate_workflow(self, workflow_id: str):
        return {"status": 200, "body": {"id": workflow_id, "active": True}}

    def call_webhook(self, webhook_path: str, *, method: str = "POST", payload=None, headers=None, timeout_s=None):
        return {"status": 200, "body": {"message": "ok"}, "headers": {}, "raw": '{"message":"ok"}'}

    def wait_for_recent_execution(self, *, workflow_id: str, since_epoch_s: float, timeout_s: float = 20.0, poll_interval_s: float = 1.0):
        return {"id": "exec-route-1", "workflowId": workflow_id, "status": "success"}

    def get_workflow(self, workflow_id: str):
        return {"status": 200, "body": {"id": workflow_id, "active": True}}

    def list_workflows(self, *, limit: int = 100, cursor: str | None = None):
        return {
            "status": 200,
            "body": {
                "data": [
                    {
                        "id": "wf-discovered-1",
                        "name": "flocks-test-discovered",
                        "active": True,
                        "nodes": [
                            {
                                "type": "n8n-nodes-base.webhook",
                                "parameters": {"path": "flocks-test-discovered", "httpMethod": "POST"},
                            }
                        ],
                    },
                    {"id": "wf-other-1", "name": "not-from-flocks", "active": True, "nodes": []},
                ],
            },
        }


@pytest.fixture(autouse=True)
def n8n_route_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_workspace: Path):
    monkeypatch.setenv("FLOCKS_N8N_STATE_DIR", str(tmp_path / "n8n-state"))
    fake_secrets = FakeSecrets()

    import flocks.integrations.n8n.secrets as n8n_secrets

    monkeypatch.setattr(n8n_secrets, "get_secret_manager", lambda: fake_secrets)
    monkeypatch.setattr(n8n_secrets, "resolve_secret_value", lambda key, secrets=None: fake_secrets.get(key))
    return {"secrets": fake_secrets, "workspace": mock_workspace}


@pytest.mark.asyncio
async def test_n8n_connection_saves_secret_without_returning_plaintext(client, n8n_route_state):
    response = await client.put(
        "/api/integrations/n8n/connection",
        json={
            "baseUrl": "http://localhost:5678/",
            "apiKeySecretRef": "N8N_API_KEY",
            "apiKey": "n8n-secret-value",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["baseUrl"] == "http://localhost:5678"
    assert data["apiKeySecretRef"] == "N8N_API_KEY"
    assert data["apiKeyConfigured"] is True
    assert "n8n-secret-value" not in response.text
    assert n8n_route_state["secrets"].values["N8N_API_KEY"] == "n8n-secret-value"


@pytest.mark.asyncio
async def test_n8n_build_run_can_render_without_publishing(client):
    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={
            "userRequest": "route smoke",
            "ir": SAMPLE_IR,
            "publish": False,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "rendered"
    assert data["workflow"]["name"] == "flocks-test-route"
    assert data["workflowJsonPath"].endswith("/workflow.json")
    assert data["reportPath"].endswith("/report.json")
    assert Path(data["workflowJsonPath"]).is_file()
    assert Path(data["reportPath"]).is_file()

    listed = await client.get("/api/integrations/n8n/build-runs")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["runId"] == data["runId"]


@pytest.mark.asyncio
async def test_n8n_build_run_auto_registers_workflow_record(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={
            "userRequest": "route publish",
            "ir": SAMPLE_IR,
            "publish": True,
            "activate": True,
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "test_passed"
    assert run["recordId"] == "n8n-wf-route-1"

    listed = await client.get("/api/integrations/n8n/workflows")
    assert listed.status_code == 200, listed.text
    records = listed.json()
    assert records[0]["id"] == "n8n-wf-route-1"
    assert records[0]["name"] == "flocks-test-route"
    assert records[0]["remoteStatus"] == "active"
    assert records[0]["testStatus"] == "test_passed"
    assert records[0]["latestExecutionId"] == "exec-route-1"

    detail = await client.get("/api/integrations/n8n/workflows/n8n-wf-route-1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["webhookPath"] == "flocks-test-route"


@pytest.mark.asyncio
async def test_n8n_workflow_record_sync_retry_and_cleanup(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)
    monkeypatch.setattr(
        n8n_routes,
        "cleanup_workflows",
        lambda _client, ids: [{"workflow_id": ids[0], "success": True, "status": 200}],
    )

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "manual n8n",
            "n8nWorkflowId": "manual-1",
            "n8nBaseUrl": "http://localhost:5678",
            "webhookPath": "manual-hook",
            "webhookMethod": "POST",
            "testCases": SAMPLE_IR["tests"],
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    synced = await client.post(f"/api/integrations/n8n/workflows/{record_id}/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["remoteStatus"] == "active"

    retried = await client.post(f"/api/integrations/n8n/workflows/{record_id}/retry-test")
    assert retried.status_code == 200, retried.text
    assert retried.json()["testStatus"] == "test_passed"

    opened = await client.post(f"/api/integrations/n8n/workflows/{record_id}/open-event")
    assert opened.status_code == 200, opened.text
    assert opened.json()["url"].endswith("/workflow/manual-1")

    cleaned = await client.post(f"/api/integrations/n8n/workflows/{record_id}/cleanup")
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["remoteStatus"] == "cleaned"


@pytest.mark.asyncio
async def test_n8n_build_run_records_inactive_when_not_activated(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: FakeN8nClient())

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={
            "userRequest": "route publish inactive",
            "ir": SAMPLE_IR,
            "publish": True,
            "activate": False,
            "waitForExecution": False,
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "published"
    assert run["testResults"] == []
    record_id = run["recordId"]

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["remoteStatus"] == "inactive"
    assert detail.json()["testStatus"] == "not_tested"


@pytest.mark.asyncio
async def test_n8n_discover_registers_flocks_prefixed_remote_workflows(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: FakeN8nClient())

    response = await client.post("/api/integrations/n8n/workflows/discover", json={})

    assert response.status_code == 200, response.text
    records = response.json()
    assert [record["name"] for record in records] == ["flocks-test-discovered"]
    assert records[0]["id"] == "n8n-wf-discovered-1"
    assert records[0]["remoteStatus"] == "active"
    assert records[0]["webhookUrl"] == "http://localhost:5678/webhook/flocks-test-discovered"
    assert records[0]["webhookMethod"] == "POST"
