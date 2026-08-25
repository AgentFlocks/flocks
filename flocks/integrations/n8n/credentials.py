"""Credential provisioning helpers for n8n workflow publishing."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from flocks.integrations.n8n.client import N8nClient, N8nClientError
from flocks.integrations.n8n.models import N8nCredentialRequirement
from flocks.security import resolve_secret_value


SECRET_SENTINEL = "{secret}"


def ensure_n8n_credentials(
    client: N8nClient,
    requirements: Iterable[N8nCredentialRequirement],
) -> List[Dict[str, Any]]:
    """Create missing n8n credentials from Flocks secrets without returning secret values."""

    rows = list(requirements)
    if not rows:
        return []

    existing = _credential_index(client)
    results: List[Dict[str, Any]] = []
    for requirement in rows:
        existing_credential = existing.get((requirement.name, requirement.type))
        if existing_credential and not requirement.update_existing:
            existing_id = existing_credential.get("id")
            if not existing_id:
                raise N8nClientError(f"n8n credential {requirement.name!r} did not include an id")
            results.append(
                {
                    "name": requirement.name,
                    "type": requirement.type,
                    "status": "exists",
                    "id": str(existing_id),
                }
            )
            continue
        if existing_credential and requirement.update_existing:
            raise N8nClientError(
                f"updating existing n8n credential {requirement.name!r} is not supported; "
                "set updateExisting=false or rotate it in n8n"
            )

        secret_value = _resolve_required_secret(requirement)
        credential_data = _materialize_credential_data(requirement.data, secret_value)
        _validate_credential_data(client, requirement, credential_data)
        payload = {
            "name": requirement.name,
            "type": requirement.type,
            "data": credential_data,
        }
        created_response = client.create_credential(payload)
        created = created_response.get("body") if isinstance(created_response.get("body"), dict) else {}
        created_id = created.get("id") if isinstance(created, dict) else None
        if not created_id:
            raise N8nClientError(f"n8n create credential response did not contain id for {requirement.name!r}")
        results.append(
            {
                "name": requirement.name,
                "type": requirement.type,
                "status": "created",
                "id": str(created_id),
            }
        )
    return results


def attach_credential_ids_to_workflow(workflow: Dict[str, Any], credentials: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Return workflow JSON with resolved n8n credential ids attached by name/type."""

    resolved = {
        (str(row.get("name") or ""), str(row.get("type") or "")): str(row.get("id") or "")
        for row in credentials
        if row.get("name") and row.get("type") and row.get("id")
    }
    if not resolved:
        return workflow

    updated = deepcopy(workflow)
    for node in updated.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_credentials = node.get("credentials")
        if not isinstance(node_credentials, dict):
            continue
        for credential_ref in node_credentials.values():
            if not isinstance(credential_ref, dict):
                continue
            credential_name = str(credential_ref.get("name") or "")
            credential_type = str(credential_ref.get("type") or "")
            credential_id = resolved.get((credential_name, credential_type))
            if credential_id:
                credential_ref["id"] = credential_id
    return updated


def _credential_index(client: N8nClient) -> Dict[tuple[str, str], Dict[str, Any]]:
    index: Dict[tuple[str, str], Dict[str, Any]] = {}
    cursor: Optional[str] = None
    for _ in range(20):
        response = client.list_credentials(limit=100, cursor=cursor)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        rows = body.get("data") if isinstance(body, dict) else []
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            credential_type = row.get("type")
            if isinstance(name, str) and isinstance(credential_type, str):
                index[(name, credential_type)] = row
        cursor = body.get("nextCursor") if isinstance(body.get("nextCursor"), str) else None
        if not cursor:
            break
    return index


def _validate_credential_data(client: N8nClient, requirement: N8nCredentialRequirement, data: Dict[str, Any]) -> None:
    response = client.get_credential_schema(requirement.type)
    body = response.get("body")
    schema = body.get("data", body) if isinstance(body, dict) else {}
    if not isinstance(schema, dict):
        raise N8nClientError(f"n8n credential schema response is invalid for {requirement.type!r}")

    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and _is_missing(data.get(key)):
                raise N8nClientError(f"n8n credential {requirement.name!r} is missing required field {key!r}")

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key in data:
            if key not in properties:
                raise N8nClientError(
                    f"n8n credential {requirement.name!r} field {key!r} is not supported by type {requirement.type!r}"
                )


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _resolve_required_secret(requirement: N8nCredentialRequirement) -> Optional[str]:
    if not requirement.secret_ref:
        return None
    value = resolve_secret_value(requirement.secret_ref)
    if value is None:
        raise N8nClientError(f"missing Flocks secret {requirement.secret_ref!r} for n8n credential {requirement.name!r}")
    return value


def _materialize_credential_data(value: Any, secret_value: Optional[str]) -> Any:
    if isinstance(value, str):
        if value == SECRET_SENTINEL:
            if secret_value is None:
                raise N8nClientError("credential data uses {secret} but no secretRef was provided")
            return secret_value
        return value
    if isinstance(value, dict):
        return {key: _materialize_credential_data(item, secret_value) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize_credential_data(item, secret_value) for item in value]
    return value
