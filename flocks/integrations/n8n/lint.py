"""Static validation for generated n8n workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlparse

from flocks.integrations.n8n.renderer import API_READONLY_FIELDS


SUPPORTED_NODE_TYPES = {
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.kafkaTrigger",
    "n8n-nodes-base.code",
    "n8n-nodes-base.set",
    "n8n-nodes-base.if",
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.respondToWebhook",
    "n8n-nodes-base.noOp",
}

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:sk|pk|n8n|tb|ak)-[A-Za-z0-9._~+/=-]{10,}"),
)

FLOCKS_SECRET_REF_PATTERNS = (
    re.compile(r"\{secret\}"),
    re.compile(r"\{secret:[^}]+\}"),
    re.compile(r"\{\{\s*secrets\.[^}]+\}\}"),
)

FLOCKS_RUNTIME_TEXT_PATTERNS = (
    re.compile(r"(?i)\bflocks[_-]?mcp\b"),
    re.compile(r"(?i)\bmcp[_-]?(?:tool|server|call|query)\b"),
)

FLOCKS_RUNTIME_API_PATHS = (
    "/api/mcp",
    "/api/tools",
    "/api/workflows",
    "/api/sessions",
    "/api/integrations/n8n",
)

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "x-apikey",
    "api-key",
    "apikey",
    "token",
}

SENSITIVE_QUERY_PARAM_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "token",
    "key",
    "secret",
    "password",
}


@dataclass(frozen=True)
class N8nLintIssue:
    code: str
    severity: str
    message: str
    path: str = "$"

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
        }


def _node_names(workflow: Dict[str, Any]) -> set[str]:
    return {str(node.get("name")) for node in workflow.get("nodes", []) if isinstance(node, dict)}


def _iter_nodes(workflow: Dict[str, Any]) -> Iterable[tuple[int, Dict[str, Any]]]:
    for index, node in enumerate(workflow.get("nodes", [])):
        if isinstance(node, dict):
            yield index, node


def _looks_like_expression(value: Any) -> bool:
    return not isinstance(value, str) or not value.startswith("=") or "{{" in value


def _credential_label(credentials: Any, key: str) -> Optional[str]:
    if not isinstance(credentials, dict):
        return None
    value = credentials.get(key)
    if not isinstance(value, dict):
        return None
    credential_id = value.get("id")
    credential_name = value.get("name")
    if credential_id:
        return str(credential_id)
    if credential_name:
        return str(credential_name)
    return None


def _iter_scalar_values(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_scalar_values(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_scalar_values(item, f"{path}[{index}]")
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        yield path, value


def _is_expression(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith("=")


def _looks_like_flocks_runtime_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.startswith("="):
        return any(marker in text.lower() for marker in FLOCKS_RUNTIME_API_PATHS)
    parsed = urlparse(text)
    if not parsed.scheme and not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if "flocks" in host:
        return True
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} and parsed.port in {8000, 5173}:
        return True
    return any(path.startswith(marker) for marker in FLOCKS_RUNTIME_API_PATHS)


def _contains_literal_secret_query_param(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.strip().startswith("="):
        return False
    parsed = urlparse(value.strip())
    if not parsed.query:
        return False
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.strip().lower()
        text = item.strip()
        if normalized_key in SENSITIVE_QUERY_PARAM_NAMES and text and not text.startswith("{{") and len(text) >= 8:
            return True
    return False


def _runtime_policy_issues(workflow: Dict[str, Any]) -> List[N8nLintIssue]:
    issues: List[N8nLintIssue] = []
    for path, value in _iter_scalar_values(workflow):
        if not isinstance(value, str):
            continue
        for pattern in FLOCKS_SECRET_REF_PATTERNS:
            if pattern.search(value):
                issues.append(
                    N8nLintIssue(
                        "FLOCKS-SECRET-REF",
                        "error",
                        "n8n workflow JSON must not contain Flocks secret placeholders; use n8n credentials instead",
                        path,
                    )
                )
                break
        if ".parameters." not in path:
            continue
        for pattern in FLOCKS_RUNTIME_TEXT_PATTERNS:
            if pattern.search(value):
                issues.append(
                    N8nLintIssue(
                        "FLOCKS-RUNTIME-REF",
                        "error",
                        "n8n workflows must run independently and cannot call Flocks MCP/runtime tools",
                        path,
                    )
                )
                break
    return issues


def lint_workflow(
    workflow: Dict[str, Any] | str,
    *,
    for_api_create: bool = False,
    require_tests: bool = False,
    tests: Optional[List[Dict[str, Any]]] = None,
) -> List[N8nLintIssue]:
    issues: List[N8nLintIssue] = []
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except json.JSONDecodeError as exc:
            return [N8nLintIssue("JSON-001", "error", f"Invalid JSON: {exc}")]
    if not isinstance(workflow, dict):
        return [N8nLintIssue("WF-001", "error", "Workflow must be an object")]
    issues.extend(_runtime_policy_issues(workflow))

    if for_api_create:
        for key in sorted(API_READONLY_FIELDS):
            if key in workflow:
                issues.append(N8nLintIssue("API-READONLY", "error", f"Field {key!r} is read-only for n8n API create", f"$.{key}"))

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        issues.append(N8nLintIssue("NODE-001", "error", "Workflow must contain at least one node", "$.nodes"))
        return issues

    names = _node_names(workflow)
    webhook_count = 0
    kafka_count = 0
    respond_count = 0
    for index, node in _iter_nodes(workflow):
        path = f"$.nodes[{index}]"
        node_type = node.get("type")
        if node_type not in SUPPORTED_NODE_TYPES:
            issues.append(N8nLintIssue("NODE-TYPE", "error", f"Unsupported node type: {node_type}", path + ".type"))
        if not node.get("name"):
            issues.append(N8nLintIssue("NODE-NAME", "error", "Node name is required", path + ".name"))
        params = node.get("parameters")
        if not isinstance(params, dict):
            issues.append(N8nLintIssue("NODE-PARAMS", "error", "Node parameters must be an object", path + ".parameters"))
            continue
        if node_type == "n8n-nodes-base.webhook":
            webhook_count += 1
            if not params.get("path"):
                issues.append(N8nLintIssue("WEBHOOK-PATH", "error", "Webhook path is required", path + ".parameters.path"))
            if params.get("responseMode") == "responseNode":
                pass
        if node_type == "n8n-nodes-base.kafkaTrigger":
            kafka_count += 1
            topic = str(params.get("topic") or "").strip()
            group_id = str(params.get("groupId") or "").strip()
            options = params.get("options") if isinstance(params.get("options"), dict) else {}
            if not topic:
                issues.append(N8nLintIssue("KAFKA-TOPIC", "error", "Kafka Trigger requires topic", path + ".parameters.topic"))
            if not group_id:
                issues.append(N8nLintIssue("KAFKA-GROUP", "error", "Kafka Trigger requires groupId", path + ".parameters.groupId"))
            elif not group_id.startswith("flocks-"):
                issues.append(
                    N8nLintIssue(
                        "KAFKA-GROUP-NAME",
                        "warning",
                        "Kafka groupId should start with flocks- to avoid consuming with unrelated groups",
                        path + ".parameters.groupId",
                    )
                )
            if not _credential_label(node.get("credentials"), "kafka"):
                issues.append(N8nLintIssue("KAFKA-CREDENTIAL", "error", "Kafka Trigger requires a kafka credential name or id", path + ".credentials.kafka"))
            batch_size = options.get("batchSize", params.get("batchSize"))
            if batch_size is not None:
                try:
                    if int(batch_size) < 1:
                        issues.append(N8nLintIssue("KAFKA-BATCH", "error", "Kafka batchSize must be greater than zero", path + ".parameters.options.batchSize"))
                except (TypeError, ValueError):
                    issues.append(N8nLintIssue("KAFKA-BATCH", "error", "Kafka batchSize must be numeric", path + ".parameters.options.batchSize"))
            if options.get("fromBeginning") is True:
                issues.append(
                    N8nLintIssue(
                        "KAFKA-FROM-BEGINNING",
                        "warning",
                        "fromBeginning=true can replay historical Kafka messages when the consumer group is new",
                        path + ".parameters.options.fromBeginning",
                    )
                )
            if params.get("useSchemaRegistry") and not (
                params.get("schemaRegistryUrl") or _credential_label(node.get("credentials"), "schemaRegistryApi")
            ):
                issues.append(
                    N8nLintIssue(
                        "KAFKA-SCHEMA",
                        "error",
                        "Schema Registry mode requires schemaRegistryUrl or schemaRegistryApi credential",
                        path + ".parameters.useSchemaRegistry",
                    )
                )
        if node_type == "n8n-nodes-base.respondToWebhook":
            respond_count += 1
            body = params.get("responseBody")
            if body is not None and not _looks_like_expression(body):
                issues.append(N8nLintIssue("EXPR-001", "warning", "Expression should contain {{ ... }}", path + ".parameters.responseBody"))
        if node_type == "n8n-nodes-base.httpRequest" and not params.get("url"):
            issues.append(N8nLintIssue("HTTP-URL", "error", "HTTP Request node requires url", path + ".parameters.url"))
        if node_type == "n8n-nodes-base.httpRequest":
            url = params.get("url")
            if _looks_like_flocks_runtime_url(url):
                issues.append(
                    N8nLintIssue(
                        "FLOCKS-RUNTIME-CALLBACK",
                        "error",
                        "HTTP Request nodes must not call Flocks; move the capability into n8n-native nodes or an external API",
                        path + ".parameters.url",
                    )
                )
            if _contains_literal_secret_query_param(url):
                issues.append(
                    N8nLintIssue(
                        "SECRET-URL",
                        "error",
                        "Sensitive URL query parameters must use n8n credentials, not literal values",
                        path + ".parameters.url",
                    )
                )
            headers = params.get("headerParameters")
            header_rows = headers.get("parameters") if isinstance(headers, dict) else []
            if isinstance(header_rows, list):
                for header_index, header in enumerate(header_rows):
                    if not isinstance(header, dict):
                        continue
                    header_name = str(header.get("name") or "").strip().lower()
                    header_value = header.get("value")
                    if header_name in SENSITIVE_HEADER_NAMES and header_value and not _is_expression(header_value):
                        issues.append(
                            N8nLintIssue(
                                "SECRET-HEADER",
                                "error",
                                "Sensitive HTTP headers must use n8n credentials or expressions, not literal values",
                                f"{path}.parameters.headerParameters.parameters[{header_index}].value",
                            )
                        )
        if node_type == "n8n-nodes-base.code":
            code = str(params.get("jsCode") or "")
            if not code.strip():
                issues.append(N8nLintIssue("CODE-EMPTY", "error", "Code node requires jsCode", path + ".parameters.jsCode"))
            for pattern in SECRET_PATTERNS:
                if pattern.search(code):
                    issues.append(N8nLintIssue("SECRET-CODE", "error", "Potential secret literal in Code node", path + ".parameters.jsCode"))

    if webhook_count and not respond_count:
        issues.append(N8nLintIssue("WEBHOOK-RESPOND", "error", "Webhook workflows must include Respond to Webhook"))
    if kafka_count and respond_count and not webhook_count:
        issues.append(N8nLintIssue("KAFKA-RESPOND", "error", "Kafka workflows must not include Respond to Webhook without a Webhook trigger"))

    connections = workflow.get("connections")
    if not isinstance(connections, dict):
        issues.append(N8nLintIssue("CONN-001", "error", "connections must be an object", "$.connections"))
    else:
        for source, value in connections.items():
            if source not in names:
                issues.append(N8nLintIssue("CONN-SOURCE", "error", f"Connection source does not exist: {source}", f"$.connections.{source}"))
            for target in _connection_targets(value):
                if target not in names:
                    issues.append(N8nLintIssue("CONN-TARGET", "error", f"Connection target does not exist: {target}", f"$.connections.{source}"))

    if require_tests and webhook_count and not tests:
        issues.append(N8nLintIssue("TEST-001", "error", "At least one test case is required", "$.tests"))
    if kafka_count and tests:
        issues.append(
            N8nLintIssue(
                "KAFKA-TESTS-IGNORED",
                "warning",
                "Kafka workflow tests are not executed by the webhook test runner",
                "$.tests",
            )
        )
    return issues


def _connection_targets(value: Any) -> Iterable[str]:
    if not isinstance(value, dict):
        return []
    rows = value.get("main")
    if not isinstance(rows, list):
        return []
    targets: list[str] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        for edge in row:
            if isinstance(edge, dict) and edge.get("node"):
                targets.append(str(edge["node"]))
    return targets


def has_errors(issues: Iterable[N8nLintIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)
