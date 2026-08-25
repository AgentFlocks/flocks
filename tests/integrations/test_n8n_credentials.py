from __future__ import annotations

import pytest

from flocks.integrations.n8n.client import N8nClientError
from flocks.integrations.n8n.credentials import attach_credential_ids_to_workflow, ensure_n8n_credentials
from flocks.integrations.n8n.models import N8nCredentialRequirement


class FakeCredentialClient:
    def __init__(self):
        self.credentials = [{"id": "cred-existing", "name": "ThreatBook API", "type": "httpQueryAuth"}]
        self.created_payloads: list[dict] = []

    def list_credentials(self, *, limit: int = 100, cursor: str | None = None):
        return {"status": 200, "body": {"data": self.credentials}}

    def create_credential(self, payload: dict):
        self.created_payloads.append(payload)
        return {"status": 200, "body": {"id": "cred-created"}}

    def get_credential_schema(self, credential_type_name: str):
        return {
            "status": 200,
            "body": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
        }


def test_ensure_n8n_credentials_refuses_to_update_existing_credentials() -> None:
    client = FakeCredentialClient()
    requirement = N8nCredentialRequirement(
        name="ThreatBook API",
        type="httpQueryAuth",
        updateExisting=True,
        data={"name": "apikey", "value": "{secret}"},
    )

    with pytest.raises(N8nClientError, match="updating existing n8n credential"):
        ensure_n8n_credentials(client, [requirement])  # type: ignore[arg-type]

    assert client.created_payloads == []


def test_ensure_n8n_credentials_validates_against_n8n_2354_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeCredentialClient()
    client.credentials = []
    monkeypatch.setattr("flocks.integrations.n8n.credentials.resolve_secret_value", lambda _key: "secret-value")
    requirement = N8nCredentialRequirement(
        name="ThreatBook API",
        type="httpQueryAuth",
        secretRef="THREATBOOK_API_KEY",
        data={"name": "apikey", "value": "{secret}"},
    )

    result = ensure_n8n_credentials(client, [requirement])  # type: ignore[arg-type]

    assert result == [{"name": "ThreatBook API", "type": "httpQueryAuth", "status": "created", "id": "cred-created"}]
    assert client.created_payloads == [
        {"name": "ThreatBook API", "type": "httpQueryAuth", "data": {"name": "apikey", "value": "secret-value"}}
    ]


def test_ensure_n8n_credentials_blocks_data_not_supported_by_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeCredentialClient()
    client.credentials = []
    monkeypatch.setattr("flocks.integrations.n8n.credentials.resolve_secret_value", lambda _key: "secret-value")
    requirement = N8nCredentialRequirement(
        name="ThreatBook API",
        type="httpQueryAuth",
        secretRef="THREATBOOK_API_KEY",
        data={"name": "apikey", "value": "{secret}", "unexpected": "bad"},
    )

    with pytest.raises(N8nClientError, match="field 'unexpected' is not supported"):
        ensure_n8n_credentials(client, [requirement])  # type: ignore[arg-type]

    assert client.created_payloads == []


def test_attach_credential_ids_to_workflow_leaves_original_untouched() -> None:
    workflow = {
        "nodes": [
            {
                "name": "Lookup",
                "credentials": {"httpQueryAuth": {"name": "ThreatBook API", "type": "httpQueryAuth"}},
            }
        ]
    }

    updated = attach_credential_ids_to_workflow(
        workflow,
        [{"name": "ThreatBook API", "type": "httpQueryAuth", "status": "exists", "id": "cred-existing"}],
    )

    assert updated["nodes"][0]["credentials"]["httpQueryAuth"]["id"] == "cred-existing"
    assert "id" not in workflow["nodes"][0]["credentials"]["httpQueryAuth"]
