"""Render stable Flocks n8n IR into native n8n workflow JSON."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Tuple

from flocks.integrations.n8n.models import N8nCredentialRef, N8nIR, N8nStep


API_READONLY_FIELDS = frozenset({"id", "versionId", "active", "meta", "createdAt", "updatedAt", "tags"})


def slugify_webhook_path(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "flocks-n8n-webhook"


def slugify_kafka_group_suffix(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_{2,}", "_", text).strip("_")
    return text or "workflow"


def kafka_group_id_for_ir(ir: N8nIR) -> str:
    if ir.trigger.group_id:
        return ir.trigger.group_id
    if ir.trigger.group_prefix:
        prefix = ir.trigger.group_prefix.rstrip("_-")
        return f"{prefix}_n8n_{slugify_kafka_group_suffix(ir.name)}"
    return ""


def node_name(step: N8nStep) -> str:
    if step.name:
        return step.name
    return step.id.replace("_", " ").replace("-", " ").title()


def _node(step: N8nStep, *, index: int) -> Dict[str, Any]:
    name = node_name(step)
    x = 240 + index * 240
    y = 300
    base = {
        "id": str(uuid.uuid4()),
        "name": name,
        "position": [x, y],
    }
    if step.kind == "code":
        js_code = step.js_code or (
            "const input = $input.first().json;\n"
            "return [{ json: { ...input, source: 'n8n' } }];"
        )
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "parameters": {"jsCode": js_code},
        })
    if step.kind == "set":
        assignments = []
        for key, value in step.assignments.items():
            assignments.append({"name": key, "value": value, "type": _assignment_type(value)})
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.set",
            "typeVersion": 3.4,
            "parameters": {"assignments": {"assignments": assignments}, "options": {}},
        })
    if step.kind == "if":
        condition = step.condition or "={{ true }}"
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                    },
                    "conditions": [
                        {
                            "leftValue": condition,
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        })
    if step.kind == "http_request":
        if not step.url:
            raise ValueError(f"http_request step {step.id!r} requires url")
        parameters: Dict[str, Any] = {
            "method": step.method,
            "url": step.url,
            "options": {},
        }
        if step.authentication:
            parameters["authentication"] = step.authentication
        if step.generic_auth_type:
            parameters["genericAuthType"] = step.generic_auth_type
        if step.headers:
            parameters["sendHeaders"] = True
            parameters["headerParameters"] = {
                "parameters": [{"name": key, "value": value} for key, value in step.headers.items()]
            }
        if step.body is not None:
            parameters["sendBody"] = True
            parameters["specifyBody"] = "json"
            parameters["jsonBody"] = step.body if isinstance(step.body, str) else json.dumps(step.body, ensure_ascii=False)
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "parameters": parameters,
        })
    if step.kind == "respond_to_webhook":
        response_body = step.response_body if step.response_body is not None else "={{ $json }}"
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.4,
            "parameters": {
                "respondWith": step.respond_with,
                "responseBody": response_body,
                "options": {},
            },
        })
    if step.kind == "noop":
        return _with_step_credentials(step, {
            **base,
            "type": "n8n-nodes-base.noOp",
            "typeVersion": 1,
            "parameters": {},
        })
    raise ValueError(f"Unsupported step kind: {step.kind}")


def _assignment_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (dict, list)):
        return "object"
    return "string"


def _credential_payload(ref: N8nCredentialRef | None) -> Dict[str, Any] | None:
    if ref is None:
        return None
    payload: Dict[str, Any] = {}
    if ref.id:
        payload["id"] = ref.id
    if ref.name:
        payload["name"] = ref.name
    if ref.type:
        payload["type"] = ref.type
    return payload or None


def _with_step_credentials(step: N8nStep, node: Dict[str, Any]) -> Dict[str, Any]:
    credentials = {
        key: payload
        for key, ref in step.credentials.items()
        if (payload := _credential_payload(ref))
    }
    if credentials:
        node["credentials"] = credentials
    return node


def _trigger_node(ir: N8nIR) -> tuple[str, Dict[str, Any]]:
    if ir.trigger.type == "webhook":
        webhook_path = slugify_webhook_path(ir.trigger.path or ir.name)
        trigger_name = "Webhook"
        return trigger_name, {
            "id": str(uuid.uuid4()),
            "name": trigger_name,
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2.1,
            "position": [0, 300],
            "webhookId": str(uuid.uuid4()),
            "parameters": {
                "httpMethod": ir.trigger.method,
                "path": webhook_path,
                "responseMode": ir.trigger.response_mode,
                "options": {},
            },
        }

    trigger_name = "Kafka Trigger"
    options = dict(ir.trigger.options or {})
    options.setdefault("fromBeginning", ir.trigger.from_beginning)
    options.setdefault("batchSize", ir.trigger.batch_size)
    parameters: Dict[str, Any] = {
        "topic": ir.trigger.topic or "",
        "groupId": kafka_group_id_for_ir(ir),
        "resolveOffset": ir.trigger.resolve_offset,
        "useSchemaRegistry": ir.trigger.use_schema_registry,
        "options": options,
    }
    if ir.trigger.schema_registry_url:
        parameters["schemaRegistryUrl"] = ir.trigger.schema_registry_url
    credentials: Dict[str, Any] = {}
    kafka_credential = _credential_payload(ir.trigger.credential_ref)
    if kafka_credential:
        credentials["kafka"] = kafka_credential
    schema_credential = _credential_payload(ir.trigger.schema_registry_credential_ref)
    if schema_credential:
        credentials["schemaRegistryApi"] = schema_credential
    node: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": trigger_name,
        "type": "n8n-nodes-base.kafkaTrigger",
        "typeVersion": 1.3,
        "position": [0, 300],
        "parameters": parameters,
    }
    if credentials:
        node["credentials"] = credentials
    return trigger_name, node


def _connections(trigger_name: str, steps: List[N8nStep]) -> Dict[str, Any]:
    connections: Dict[str, Any] = {}
    if not steps:
        return connections

    names = {step.id: node_name(step) for step in steps}

    def add(source: str, targets: Iterable[Tuple[str, int]]) -> None:
        rows = []
        for target_id, output_index in targets:
            if target_id not in names:
                continue
            while len(rows) <= output_index:
                rows.append([])
            rows[output_index].append({"node": names[target_id], "type": "main", "index": 0})
        if rows:
            connections[source] = {"main": rows}

    add(trigger_name, [(steps[0].id, 0)])
    for index, step in enumerate(steps):
        source_name = names[step.id]
        if step.kind == "if":
            targets = []
            if step.true_next:
                targets.append((step.true_next, 0))
            if step.false_next:
                targets.append((step.false_next, 1))
            if not targets and index + 1 < len(steps):
                targets.append((steps[index + 1].id, 0))
            add(source_name, targets)
            continue
        next_id = step.next or (steps[index + 1].id if index + 1 < len(steps) else None)
        if next_id:
            add(source_name, [(next_id, 0)])
    return connections


def render_ir_to_workflow(ir_data: N8nIR | Dict[str, Any], *, workflow_id: str | None = None) -> Dict[str, Any]:
    ir = ir_data if isinstance(ir_data, N8nIR) else N8nIR.model_validate(ir_data)
    trigger_name, trigger_node = _trigger_node(ir)
    nodes = [trigger_node]
    nodes.extend(_node(step, index=index + 1) for index, step in enumerate(ir.steps))
    workflow: Dict[str, Any] = {
        "name": ir.name,
        "nodes": nodes,
        "connections": _connections(trigger_name, ir.steps),
        "settings": {"executionOrder": "v1"},
        "active": False,
        "meta": {
            "createdBy": "flocks",
            "flocksIntegration": "n8n_workflow_autobuilder",
        },
    }
    if workflow_id:
        workflow["id"] = workflow_id
    return workflow


def workflow_to_api_create_payload(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Return a payload accepted by n8n Public API create/update endpoints."""

    return {key: value for key, value in workflow.items() if key not in API_READONLY_FIELDS}
