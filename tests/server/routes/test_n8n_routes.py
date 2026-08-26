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


KAFKA_IR = {
    "name": "flocks-kafka-route",
    "trigger": {
        "type": "kafka",
        "topic": "security-alerts",
        "groupPrefix": "flocks_kafka",
        "credentialRef": {"name": "Kafka Production"},
        "fromBeginning": False,
        "batchSize": 1,
        "resolveOffset": "onCompletion",
    },
    "steps": [
        {
            "id": "normalize",
            "kind": "code",
            "name": "Normalize",
            "js_code": "return $input.all();",
        },
    ],
    "tests": [],
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
    def __init__(self, workflows=None):
        self.deleted: list[str] = []
        self.workflows = workflows
        self.created_payloads: list[dict] = []
        self.credentials: list[dict] = []
        self.created_credentials: list[dict] = []
        self.active: dict[str, bool] = {}

    def create_workflow(self, payload):
        self.created_payloads.append(payload)
        return {"status": 200, "body": {"id": "wf-route-1", "active": False, "name": payload.get("name")}}

    def activate_workflow(self, workflow_id: str):
        self.active[workflow_id] = True
        return {"status": 200, "body": {"id": workflow_id, "active": True}}

    def deactivate_workflow(self, workflow_id: str):
        self.active[workflow_id] = False
        return {"status": 200, "body": {"id": workflow_id, "active": False}}

    def call_webhook(self, webhook_path: str, *, method: str = "POST", payload=None, headers=None, timeout_s=None):
        return {"status": 200, "body": {"message": "ok"}, "headers": {}, "raw": '{"message":"ok"}'}

    def wait_for_recent_execution(self, *, workflow_id: str, since_epoch_s: float, timeout_s: float = 20.0, poll_interval_s: float = 1.0):
        return {"id": "exec-route-1", "workflowId": workflow_id, "status": "success"}

    def get_workflow(self, workflow_id: str):
        return {"status": 200, "body": {"id": workflow_id, "active": self.active.get(workflow_id, True)}}

    def get_execution(self, execution_id: str, *, include_data: bool = True):
        return {
            "status": 200,
            "body": {
                "id": execution_id,
                "data": {
                    "resultData": {
                        "runData": {
                            "Webhook": [
                                {
                                    "data": {
                                        "main": [[{"json": {"body": {"name": "Alice"}}}]],
                                    },
                                }
                            ],
                            "Build Response": [
                                {
                                    "data": {
                                        "main": [[{"json": {"message": "ok"}}]],
                                    },
                                }
                            ],
                        }
                    }
                },
            },
        }

    def delete_workflow(self, workflow_id: str):
        self.deleted.append(workflow_id)
        return {"status": 200, "body": {"id": workflow_id}}

    def list_workflows(self, *, limit: int = 100, cursor: str | None = None):
        if self.workflows is not None:
            return {"status": 200, "body": {"data": self.workflows}}
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

    def list_credentials(self, *, limit: int = 100, cursor: str | None = None):
        return {"status": 200, "body": {"data": self.credentials}}

    def create_credential(self, payload):
        self.created_credentials.append(payload)
        credential = {"id": f"cred-{len(self.created_credentials)}", "name": payload["name"], "type": payload["type"]}
        self.credentials.append(credential)
        return {"status": 200, "body": credential}

    def get_credential_schema(self, credential_type_name: str):
        if credential_type_name == "kafka":
            properties = {
                "brokers": {"type": "string"},
                "clientId": {"type": "string"},
                "ssl": {"type": "boolean"},
                "authentication": {"type": "boolean"},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "saslMechanism": {"type": "string"},
                "ca": {"type": "string"},
                "cert": {"type": "string"},
                "key": {"type": "string"},
                "allowUnauthorizedCerts": {"type": "boolean"},
            }
        elif credential_type_name == "httpQueryAuth":
            properties = {"name": {"type": "string"}, "value": {"type": "string"}}
        else:
            properties = {"password": {"type": "string"}}
        return {
            "status": 200,
            "body": {
                "type": "object",
                "required": ["name", "value"] if credential_type_name == "httpQueryAuth" else [],
                "properties": properties,
            },
        }


@pytest.fixture(autouse=True)
def n8n_route_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mock_workspace: Path):
    monkeypatch.setenv("FLOCKS_N8N_STATE_DIR", str(tmp_path / "n8n-state"))
    fake_secrets = FakeSecrets()

    import flocks.integrations.n8n.secrets as n8n_secrets
    import flocks.integrations.n8n.credentials as n8n_credentials

    monkeypatch.setattr(n8n_secrets, "get_secret_manager", lambda: fake_secrets)
    monkeypatch.setattr(n8n_secrets, "resolve_secret_value", lambda key, secrets=None: fake_secrets.get(key))
    monkeypatch.setattr(n8n_credentials, "resolve_secret_value", lambda key: fake_secrets.get(key))
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
    assert data["id"] == "default"
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
async def test_n8n_build_run_blocks_flocks_runtime_dependencies(client):
    invalid_ir = {
        "name": "flocks-test-invalid-runtime-route",
        "trigger": {"type": "webhook", "method": "POST", "path": "invalid-runtime-route"},
        "steps": [
            {
                "id": "callback",
                "kind": "http_request",
                "method": "POST",
                "url": "http://localhost:8000/api/mcp/threatbook",
                "headers": {"Authorization": "Bearer {{secrets.THREATBOOK_API_KEY}}"},
                "next": "respond",
            },
            {
                "id": "respond",
                "kind": "respond_to_webhook",
                "name": "Respond",
                "response_body": "={{ $json }}",
            },
        ],
        "tests": [{"name": "blocked", "input": {"ioc": "1.1.1.1"}}],
    }

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={
            "userRequest": "must not call flocks runtime",
            "ir": invalid_ir,
            "publish": True,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "lint_failed"
    codes = {issue["code"] for issue in data["lintIssues"]}
    assert "FLOCKS-RUNTIME-CALLBACK" in codes
    assert "FLOCKS-SECRET-REF" in codes


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
    assert run["connectionId"] == "default"
    assert run["recordId"] == "n8n-default-wf-route-1"

    listed = await client.get("/api/integrations/n8n/workflows")
    assert listed.status_code == 200, listed.text
    records = listed.json()
    assert records[0]["id"] == "n8n-default-wf-route-1"
    assert records[0]["connectionId"] == "default"
    assert records[0]["source"] == "flocks_created"
    assert records[0]["ownership"] == "managed"
    assert records[0]["name"] == "flocks-test-route"
    assert records[0]["remoteStatus"] == "active"
    assert records[0]["testStatus"] == "test_passed"
    assert records[0]["latestExecutionId"] == "exec-route-1"

    detail = await client.get("/api/integrations/n8n/workflows/n8n-default-wf-route-1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["webhookPath"] == "flocks-test-route"


@pytest.mark.asyncio
async def test_n8n_build_run_creates_required_credentials_from_flocks_secret(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    ir = {
        "name": "flocks-test-credential-route",
        "trigger": SAMPLE_IR["trigger"] | {"path": "flocks-test-credential-route"},
        "credentialRequirements": [
            {
                "name": "ThreatBook API",
                "type": "httpQueryAuth",
                "secretRef": "THREATBOOK_API_KEY",
                "data": {"name": "apikey", "value": "{secret}"},
            }
        ],
        "steps": [
            {
                "id": "lookup",
                "kind": "http_request",
                "name": "ThreatBook Lookup",
                "method": "GET",
                "url": "https://api.threatbook.example/v3/scene/ip_reputation",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpQueryAuth",
                "credentials": {"httpQueryAuth": {"name": "ThreatBook API", "type": "httpQueryAuth"}},
                "next": "respond",
            },
            {
                "id": "respond",
                "kind": "respond_to_webhook",
                "name": "Respond",
                "response_body": "={{ $json }}",
            },
        ],
        "tests": SAMPLE_IR["tests"],
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    n8n_route_state["secrets"].set("THREATBOOK_API_KEY", "threatbook-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route credential publish", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "test_passed"
    assert run["credentialResults"] == [
        {"name": "ThreatBook API", "type": "httpQueryAuth", "status": "created", "id": "cred-1"}
    ]
    assert fake_client.created_credentials[0]["data"] == {"name": "apikey", "value": "threatbook-secret-value"}
    published_node = fake_client.created_payloads[0]["nodes"][1]
    assert published_node["credentials"] == {
        "httpQueryAuth": {"id": "cred-1", "name": "ThreatBook API", "type": "httpQueryAuth"}
    }
    assert "threatbook-secret-value" not in response.text


@pytest.mark.asyncio
async def test_n8n_build_run_reuses_existing_required_credentials(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    ir = {
        **KAFKA_IR,
        "credentialRequirements": [
            {
                "name": "Kafka Production",
                "type": "kafka",
                "secretRef": "KAFKA_PASSWORD",
                "data": {"password": "{secret}"},
            }
        ],
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    fake_client.credentials = [{"id": "cred-existing", "name": "Kafka Production", "type": "kafka"}]
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route existing credential", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "published"
    assert run["credentialResults"] == [
        {"name": "Kafka Production", "type": "kafka", "status": "exists", "id": "cred-existing"}
    ]
    assert fake_client.created_credentials == []


@pytest.mark.asyncio
async def test_n8n_build_run_fails_before_publish_when_required_secret_missing(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    ir = {
        **KAFKA_IR,
        "credentialRequirements": [
            {
                "name": "Kafka Production",
                "type": "kafka",
                "secretRef": "KAFKA_PASSWORD",
                "data": {"password": "{secret}"},
            }
        ],
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route missing credential", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "failed"
    assert run["currentStep"] == "credentials"
    assert "KAFKA_PASSWORD" in run["error"]
    assert fake_client.created_credentials == []
    assert fake_client.created_payloads == []


@pytest.mark.asyncio
async def test_n8n_build_run_fails_before_publish_when_credential_permission_missing(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.client import N8nClientError
    from flocks.server.routes import n8n as n8n_routes

    class ForbiddenCredentialClient(FakeN8nClient):
        def create_credential(self, payload):
            raise N8nClientError("n8n HTTP 403 for POST /api/v1/credentials", status=403)

    ir = {
        **KAFKA_IR,
        "credentialRequirements": [
            {
                "name": "Kafka Production",
                "type": "kafka",
                "secretRef": "KAFKA_PASSWORD",
                "data": {"password": "{secret}"},
            }
        ],
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    n8n_route_state["secrets"].set("KAFKA_PASSWORD", "kafka-secret-value")
    fake_client = ForbiddenCredentialClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route forbidden credential", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "failed"
    assert run["currentStep"] == "credentials"
    assert "POST /api/v1/credentials" in run["error"]
    assert fake_client.created_payloads == []
    assert "kafka-secret-value" not in response.text


@pytest.mark.asyncio
async def test_n8n_build_run_creates_kafka_sasl_plaintext_credential_from_flocks_secret(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    ir = {
        **KAFKA_IR,
        "credentialRequirements": [
            {
                "name": "Kafka TDP Flocks",
                "type": "kafka",
                "secretRef": "KAFKA_PASSWORD",
                "data": {
                    "brokers": "10.42.19.106:9093,10.42.112.31:9093,10.42.80.112:9093",
                    "clientId": "flocks-n8n",
                    "ssl": False,
                    "authentication": True,
                    "username": "appId_002074_cn",
                    "password": "{secret}",
                    "saslMechanism": "scram-sha-256",
                },
            }
        ],
        "trigger": {
            **KAFKA_IR["trigger"],
            "credentialRef": {"name": "Kafka TDP Flocks", "type": "kafka"},
        },
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    n8n_route_state["secrets"].set("KAFKA_PASSWORD", "kafka-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route kafka sasl plaintext", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "published"
    assert run["credentialResults"] == [
        {"name": "Kafka TDP Flocks", "type": "kafka", "status": "created", "id": "cred-1"}
    ]
    assert fake_client.created_credentials[0]["data"] == {
        "brokers": "10.42.19.106:9093,10.42.112.31:9093,10.42.80.112:9093",
        "clientId": "flocks-n8n",
        "ssl": False,
        "authentication": True,
        "username": "appId_002074_cn",
        "password": "kafka-secret-value",
        "saslMechanism": "scram-sha-256",
    }
    published_trigger = fake_client.created_payloads[0]["nodes"][0]
    assert published_trigger["credentials"]["kafka"] == {
        "id": "cred-1",
        "name": "Kafka TDP Flocks",
        "type": "kafka",
    }
    assert "kafka-secret-value" not in response.text


@pytest.mark.asyncio
async def test_n8n_build_run_can_publish_kafka_trigger_without_webhook_tests(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={
            "userRequest": "route kafka publish",
            "ir": KAFKA_IR,
            "publish": True,
            "activate": True,
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "published"
    assert run["webhookUrl"] is None
    assert run["testResults"] == []
    assert fake_client.created_payloads[0]["nodes"][0]["type"] == "n8n-nodes-base.kafkaTrigger"

    detail = await client.get(f"/api/integrations/n8n/workflows/{run['recordId']}")
    assert detail.status_code == 200, detail.text
    record = detail.json()
    assert record["triggerType"] == "kafka"
    assert record["kafkaTopic"] == "security-alerts"
    assert record["kafkaGroupId"] == "flocks_kafka_n8n_flocks_kafka_route"
    assert record["kafkaCredentialName"] == "Kafka Production"
    assert record["webhookUrl"] is None
    assert record["testStatus"] == "not_tested"


@pytest.mark.asyncio
async def test_n8n_build_run_blocks_kafka_group_id_outside_declared_prefix(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    ir = {
        **KAFKA_IR,
        "trigger": {
            **KAFKA_IR["trigger"],
            "groupPrefix": "flocks_kafka",
            "groupId": "flocks-n8n-security-alerts",
        },
    }
    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    response = await client.post(
        "/api/integrations/n8n/build-runs",
        json={"userRequest": "route kafka publish bad group", "ir": ir, "publish": True, "activate": True},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "lint_failed"
    assert {issue["code"] for issue in run["lintIssues"] if issue["severity"] == "error"} == {"KAFKA-GROUP-PREFIX"}
    assert fake_client.created_payloads == []


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

    ran = await client.post(
        f"/api/integrations/n8n/workflows/{record_id}/run",
        json={"payload": {"name": "Alice"}, "waitForExecution": True},
    )
    assert ran.status_code == 200, ran.text
    run_record = ran.json()
    assert run_record["latestRunResult"]["success"] is True
    assert run_record["latestRunResult"]["status"] == 200
    assert run_record["latestRunResult"]["responseBodyStored"] is False
    assert "response" not in run_record["latestRunResult"]
    assert run_record["latestExecutionId"] == "exec-route-1"
    assert run_record["latestRunResult"]["deepDebug"]["status"] == "completed"
    assert run_record["deepDebugResults"][0]["nodeTraces"][0]["nodeName"] == "Webhook"

    opened = await client.post(f"/api/integrations/n8n/workflows/{record_id}/open-event")
    assert opened.status_code == 200, opened.text
    assert opened.json()["url"].endswith("/workflow/manual-1")

    cleaned = await client.post(f"/api/integrations/n8n/workflows/{record_id}/cleanup")
    assert cleaned.status_code == 403, cleaned.text


@pytest.mark.asyncio
async def test_n8n_workflow_record_can_activate_and_deactivate_from_flocks(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    fake_client.active["manual-toggle"] = False
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "manual toggle",
            "source": "flocks_created",
            "ownership": "managed",
            "n8nWorkflowId": "manual-toggle",
            "n8nBaseUrl": "http://localhost:5678",
            "webhookPath": "manual-toggle",
            "webhookMethod": "POST",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    activated = await client.post(f"/api/integrations/n8n/workflows/{record_id}/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["remoteStatus"] == "active"

    deactivated = await client.post(f"/api/integrations/n8n/workflows/{record_id}/deactivate")
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["remoteStatus"] == "inactive"


@pytest.mark.asyncio
async def test_n8n_workflow_record_can_toggle_discovered_flocks_record(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    fake_client.active["readonly-toggle"] = False
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "readonly toggle",
            "source": "discovered",
            "ownership": "readonly",
            "n8nWorkflowId": "readonly-toggle",
            "n8nBaseUrl": "http://localhost:5678",
            "webhookPath": "readonly-toggle",
            "webhookMethod": "POST",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    activated = await client.post(f"/api/integrations/n8n/workflows/{record_id}/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["remoteStatus"] == "active"

    deactivated = await client.post(f"/api/integrations/n8n/workflows/{record_id}/deactivate")
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["remoteStatus"] == "inactive"


@pytest.mark.asyncio
async def test_n8n_workflow_record_blocks_activation_when_pre_sync_fails(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.client import N8nClientError
    from flocks.server.routes import n8n as n8n_routes

    class AuthFailedSyncClient(FakeN8nClient):
        def __init__(self):
            super().__init__()
            self.activate_calls = 0

        def get_workflow(self, workflow_id: str):
            raise N8nClientError("n8n HTTP 401 for GET /api/v1/workflows/pre-sync-fail", status=401)

        def activate_workflow(self, workflow_id: str):
            self.activate_calls += 1
            return super().activate_workflow(workflow_id)

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = AuthFailedSyncClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "pre sync fails",
            "source": "flocks_created",
            "ownership": "managed",
            "n8nWorkflowId": "pre-sync-fail",
            "n8nBaseUrl": "http://localhost:5678",
            "webhookPath": "pre-sync-fail",
            "webhookMethod": "POST",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    activated = await client.post(f"/api/integrations/n8n/workflows/{record_id}/activate")
    assert activated.status_code == 409, activated.text
    assert "n8n sync failed before operation" in activated.text
    assert fake_client.activate_calls == 0

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["remoteStatus"] == "auth_error"


@pytest.mark.asyncio
async def test_n8n_workflow_record_delete_removes_local_record_for_any_source(client):
    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "readonly n8n",
            "source": "discovered",
            "ownership": "readonly",
            "n8nWorkflowId": "readonly-1",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    deleted = await client.delete(f"/api/integrations/n8n/workflows/{record_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["success"] is True
    assert deleted.json()["remoteCleanup"] == []

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 404, detail.text


@pytest.mark.asyncio
async def test_n8n_workflow_record_delete_can_remove_remote_workflow(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = FakeN8nClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "remote n8n",
            "source": "discovered",
            "ownership": "readonly",
            "n8nWorkflowId": "remote-delete-1",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    deleted = await client.delete(f"/api/integrations/n8n/workflows/{record_id}?deleteRemote=true")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["remoteCleanup"][0]["success"] is True
    assert fake_client.deleted == ["remote-delete-1"]

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 404, detail.text


@pytest.mark.asyncio
async def test_n8n_workflow_record_delete_remote_blocks_when_pre_sync_fails(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.client import N8nClientError
    from flocks.server.routes import n8n as n8n_routes

    class MissingOnSyncClient(FakeN8nClient):
        def get_workflow(self, workflow_id: str):
            raise N8nClientError("n8n HTTP 404 for GET /api/v1/workflows/pre-delete-fail", status=404)

    cleanup_called = False

    def cleanup_spy(_client, ids):
        nonlocal cleanup_called
        cleanup_called = True
        return [{"workflow_id": ids[0], "success": True, "status": 200}]

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: MissingOnSyncClient())
    monkeypatch.setattr(n8n_routes, "cleanup_workflows", cleanup_spy)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "pre delete fails",
            "source": "discovered",
            "ownership": "readonly",
            "n8nWorkflowId": "pre-delete-fail",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    deleted = await client.delete(f"/api/integrations/n8n/workflows/{record_id}?deleteRemote=true")
    assert deleted.status_code == 409, deleted.text
    assert "n8n sync failed before operation" in deleted.text
    assert cleanup_called is False

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["remoteStatus"] == "missing"


@pytest.mark.asyncio
async def test_n8n_build_run_retry_blocks_when_pre_sync_fails(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.client import N8nClientError
    from flocks.integrations.n8n.state import N8nBuildRunState, N8nStateStore
    from flocks.server.routes import n8n as n8n_routes

    class BuildRunSyncFailedClient(FakeN8nClient):
        def get_workflow(self, workflow_id: str):
            raise N8nClientError("n8n HTTP 401 for GET /api/v1/workflows/build-run-sync-fail", status=401)

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: BuildRunSyncFailedClient())

    store = N8nStateStore()
    store.save_run(
        N8nBuildRunState(
            runId="build-run-sync-fail",
            connectionId="default",
            status="published",
            currentStep="complete",
            ir=SAMPLE_IR,
            workflow={"name": "build-run-sync-fail"},
            n8nWorkflowId="build-run-sync-fail",
        )
    )

    response = await client.post("/api/integrations/n8n/build-runs/build-run-sync-fail/retry-test")
    assert response.status_code == 409, response.text
    assert "n8n sync failed before operation" in response.text

    run = await client.get("/api/integrations/n8n/build-runs/build-run-sync-fail")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "sync_error"
    assert run.json()["currentStep"] == "sync"


@pytest.mark.asyncio
async def test_n8n_workflow_record_delete_treats_remote_404_as_already_deleted(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.client import N8nClientError
    from flocks.server.routes import n8n as n8n_routes

    class MissingRemoteClient(FakeN8nClient):
        def delete_workflow(self, workflow_id: str):
            self.deleted.append(workflow_id)
            raise N8nClientError("missing", status=404)

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = MissingRemoteClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "remote missing n8n",
            "source": "discovered",
            "ownership": "readonly",
            "n8nWorkflowId": "remote-missing-1",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]

    deleted = await client.delete(f"/api/integrations/n8n/workflows/{record_id}?deleteRemote=true")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["remoteCleanup"][0]["status"] == 404
    assert fake_client.deleted == ["remote-missing-1"]

    detail = await client.get(f"/api/integrations/n8n/workflows/{record_id}")
    assert detail.status_code == 404, detail.text


@pytest.mark.asyncio
async def test_n8n_workflow_run_blocks_external_and_requires_webhook(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: FakeN8nClient())

    external = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "external n8n",
            "source": "external",
            "ownership": "readonly",
            "n8nWorkflowId": "external-run-1",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert external.status_code == 200, external.text

    external_run = await client.post(f"/api/integrations/n8n/workflows/{external.json()['id']}/run", json={})
    assert external_run.status_code == 403, external_run.text

    no_webhook = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "manual without webhook",
            "source": "manual",
            "ownership": "managed",
            "n8nWorkflowId": "manual-no-webhook",
            "n8nBaseUrl": "http://localhost:5678",
        },
    )
    assert no_webhook.status_code == 200, no_webhook.text

    no_webhook_run = await client.post(f"/api/integrations/n8n/workflows/{no_webhook.json()['id']}/run", json={})
    assert no_webhook_run.status_code == 400, no_webhook_run.text
    assert "webhook path is missing" in no_webhook_run.text


@pytest.mark.asyncio
async def test_n8n_workflow_run_blocks_inactive_after_pre_sync(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    class InactiveWorkflowClient(FakeN8nClient):
        def __init__(self):
            super().__init__()
            self.active["inactive-run"] = False
            self.webhook_calls = 0

        def call_webhook(self, *args, **kwargs):
            self.webhook_calls += 1
            return super().call_webhook(*args, **kwargs)

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    fake_client = InactiveWorkflowClient()
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: fake_client)

    created = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "inactive run",
            "source": "flocks_created",
            "ownership": "managed",
            "n8nWorkflowId": "inactive-run",
            "n8nBaseUrl": "http://localhost:5678",
            "webhookPath": "inactive-run",
            "webhookMethod": "POST",
        },
    )
    assert created.status_code == 200, created.text

    run_response = await client.post(f"/api/integrations/n8n/workflows/{created.json()['id']}/run", json={})
    assert run_response.status_code == 409, run_response.text
    assert "must be active" in run_response.text
    assert fake_client.webhook_calls == 0


@pytest.mark.asyncio
async def test_n8n_workflow_run_blocks_kafka_records(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(n8n_routes, "_client_for", lambda _base_url, _secret_ref: FakeN8nClient())

    kafka = await client.post(
        "/api/integrations/n8n/workflows",
        json={
            "name": "kafka n8n",
            "source": "flocks_created",
            "ownership": "managed",
            "n8nWorkflowId": "kafka-run-1",
            "n8nBaseUrl": "http://localhost:5678",
            "triggerType": "kafka",
            "kafkaTopic": "security-alerts",
            "kafkaGroupId": "flocks-security-alerts",
        },
    )
    assert kafka.status_code == 200, kafka.text

    run_response = await client.post(f"/api/integrations/n8n/workflows/{kafka.json()['id']}/run", json={})
    assert run_response.status_code == 400, run_response.text
    assert "only webhook n8n workflows can be run from Flocks" in run_response.text


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
    assert records[0]["id"] == "n8n-default-wf-discovered-1"
    assert records[0]["connectionId"] == "default"
    assert records[0]["source"] == "discovered"
    assert records[0]["ownership"] == "readonly"
    assert records[0]["remoteStatus"] == "active"
    assert records[0]["webhookUrl"] == "http://localhost:5678/webhook/flocks-test-discovered"
    assert records[0]["webhookMethod"] == "POST"


@pytest.mark.asyncio
async def test_n8n_discover_registers_flocks_prefixed_kafka_workflows(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    monkeypatch.setattr(
        n8n_routes,
        "_client_for",
        lambda _base_url, _secret_ref: FakeN8nClient(
            workflows=[
                {
                    "id": "wf-kafka-1",
                    "name": "flocks-kafka-discovered",
                    "active": True,
                    "nodes": [
                        {
                            "type": "n8n-nodes-base.kafkaTrigger",
                            "parameters": {"topic": "security-alerts", "groupId": "flocks-security-alerts"},
                            "credentials": {"kafka": {"name": "Kafka Production"}},
                        }
                    ],
                }
            ]
        ),
    )

    response = await client.post("/api/integrations/n8n/workflows/discover", json={})

    assert response.status_code == 200, response.text
    records = response.json()
    assert records[0]["triggerType"] == "kafka"
    assert records[0]["kafkaTopic"] == "security-alerts"
    assert records[0]["kafkaGroupId"] == "flocks-security-alerts"
    assert records[0]["kafkaCredentialName"] == "Kafka Production"
    assert records[0]["webhookUrl"] is None


@pytest.mark.asyncio
async def test_n8n_global_sync_keeps_same_workflow_id_separate_per_connection(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_A_KEY", "n8n-a-secret")
    n8n_route_state["secrets"].set("N8N_B_KEY", "n8n-b-secret")
    first = await client.post(
        "/api/integrations/n8n/connections",
        json={
            "name": "n8n A",
            "baseUrl": "http://n8n-a.local",
            "apiKeySecretRef": "N8N_A_KEY",
            "isDefault": True,
        },
    )
    second = await client.post(
        "/api/integrations/n8n/connections",
        json={
            "name": "n8n B",
            "baseUrl": "http://n8n-b.local",
            "apiKeySecretRef": "N8N_B_KEY",
        },
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_connection = first.json()
    second_connection = second.json()

    def client_for(base_url: str, _secret_ref: str):
        if base_url == "http://n8n-a.local":
            return FakeN8nClient(
                workflows=[
                    {
                        "id": "same-id",
                        "name": "flocks-from-a",
                        "active": True,
                        "nodes": [],
                    }
                ]
            )
        return FakeN8nClient(
            workflows=[
                {
                    "id": "same-id",
                    "name": "flocks-from-b",
                    "active": False,
                    "nodes": [],
                }
            ]
        )

    monkeypatch.setattr(n8n_routes, "_client_for", client_for)

    response = await client.post("/api/integrations/n8n/sync", json={})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["connectionsSuccess"] >= 2
    records = sorted(data["records"], key=lambda item: item["connectionId"])
    same_id_records = [record for record in records if record["n8nWorkflowId"] == "same-id"]
    assert len(same_id_records) == 2
    assert {record["connectionId"] for record in same_id_records} == {first_connection["id"], second_connection["id"]}
    assert {record["name"] for record in same_id_records} == {"flocks-from-a", "flocks-from-b"}


@pytest.mark.asyncio
async def test_n8n_global_sync_does_not_let_second_connection_steal_legacy_record(
    client,
    monkeypatch: pytest.MonkeyPatch,
    n8n_route_state,
):
    from flocks.integrations.n8n.state import N8nStateStore, N8nWorkflowRecord
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_A_KEY", "n8n-a-secret")
    n8n_route_state["secrets"].set("N8N_B_KEY", "n8n-b-secret")
    first = await client.post(
        "/api/integrations/n8n/connections",
        json={
            "name": "n8n A",
            "baseUrl": "http://n8n-a.local",
            "apiKeySecretRef": "N8N_A_KEY",
            "isDefault": True,
        },
    )
    second = await client.post(
        "/api/integrations/n8n/connections",
        json={
            "name": "n8n B",
            "baseUrl": "http://n8n-b.local",
            "apiKeySecretRef": "N8N_B_KEY",
        },
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    second_connection = second.json()

    store = N8nStateStore()
    store.save_record(
        N8nWorkflowRecord(
            id="n8n-same-id",
            name="legacy-from-a",
            connectionId="default",
            connectionName="n8n A",
            source="flocks_created",
            ownership="managed",
            n8nWorkflowId="same-id",
            n8nBaseUrl="http://n8n-a.local",
            apiKeySecretRef="N8N_A_KEY",
            workflowUrl="http://n8n-a.local/workflow/same-id",
        )
    )

    def client_for(base_url: str, _secret_ref: str):
        return FakeN8nClient(
            workflows=[
                {
                    "id": "same-id",
                    "name": "flocks-from-b" if base_url == "http://n8n-b.local" else "legacy-from-a",
                    "active": True,
                    "nodes": [],
                }
            ]
        )

    monkeypatch.setattr(n8n_routes, "_client_for", client_for)

    response = await client.post("/api/integrations/n8n/sync", json={})

    assert response.status_code == 200, response.text
    records = response.json()["records"]
    legacy = next(record for record in records if record["id"] == "n8n-same-id")
    second_record = next(record for record in records if record["connectionId"] == second_connection["id"])
    assert legacy["connectionId"] == "default"
    assert legacy["n8nBaseUrl"] == "http://n8n-a.local"
    assert second_record["id"] != legacy["id"]
    assert second_record["name"] == "flocks-from-b"


@pytest.mark.asyncio
async def test_n8n_global_sync_skips_external_records_by_default(client, monkeypatch: pytest.MonkeyPatch, n8n_route_state):
    from flocks.integrations.n8n.state import N8nStateStore, N8nWorkflowRecord
    from flocks.server.routes import n8n as n8n_routes

    n8n_route_state["secrets"].set("N8N_API_KEY", "n8n-secret-value")
    store = N8nStateStore()
    store.save_record(
        N8nWorkflowRecord(
            id="n8n-default-external-1",
            name="external workflow",
            connectionId="default",
            connectionName="Default n8n",
            source="external",
            ownership="readonly",
            n8nWorkflowId="external-1",
            n8nBaseUrl="http://localhost:5678",
            apiKeySecretRef="N8N_API_KEY",
            workflowUrl="http://localhost:5678/workflow/external-1",
            remoteStatus="active",
        )
    )
    monkeypatch.setattr(
        n8n_routes,
        "_client_for",
        lambda _base_url, _secret_ref: FakeN8nClient(
            workflows=[
                {
                    "id": "external-1",
                    "name": "not-from-flocks",
                    "active": False,
                    "nodes": [],
                }
            ]
        ),
    )

    response = await client.post("/api/integrations/n8n/sync", json={})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["updated"] == 0
    assert data["external"] == 0
    external = next(record for record in data["records"] if record["id"] == "n8n-default-external-1")
    assert external["remoteStatus"] == "active"
    assert external["lastSyncedAt"] is None
