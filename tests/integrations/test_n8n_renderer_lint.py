from __future__ import annotations

from flocks.integrations.n8n.lint import lint_workflow
from flocks.integrations.n8n.client import N8nClient, N8nConfig
from flocks.integrations.n8n.models import N8nIR
from flocks.integrations.n8n.renderer import render_ir_to_workflow, workflow_to_api_create_payload
from flocks.integrations.n8n.repair import build_repair_context


def _sample_ir() -> dict:
    return {
        "name": "flocks-test-hello",
        "trigger": {"type": "webhook", "method": "POST", "path": "hello test"},
        "steps": [
            {
                "id": "build_response",
                "kind": "code",
                "js_code": (
                    "const body = $input.first().json.body || {}; "
                    "return [{ json: { message: `Hello ${body.name || 'World'}`, source: 'n8n' } }];"
                ),
            },
            {
                "id": "respond",
                "kind": "respond_to_webhook",
                "response_body": "={{ $json }}",
            },
        ],
        "tests": [
            {
                "name": "hello",
                "input": {"name": "Alice"},
                "expect": {"status": 200, "jsonContains": {"source": "n8n"}},
            }
        ],
    }


def _sample_kafka_ir() -> dict:
    return {
        "name": "flocks-kafka-alerts",
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
            }
        ],
        "tests": [],
    }


def test_render_ir_to_workflow_and_lint_success() -> None:
    workflow = render_ir_to_workflow(N8nIR.model_validate(_sample_ir()), workflow_id="wf-cli-id")

    assert workflow["id"] == "wf-cli-id"
    assert workflow["nodes"][0]["parameters"]["path"] == "hello-test"
    assert [node["type"] for node in workflow["nodes"]] == [
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.code",
        "n8n-nodes-base.respondToWebhook",
    ]

    issues = lint_workflow(workflow, require_tests=True, tests=_sample_ir()["tests"])
    assert [issue.to_dict() for issue in issues if issue.severity == "error"] == []


def test_render_kafka_trigger_to_workflow_and_lint_success_without_webhook_tests() -> None:
    workflow = render_ir_to_workflow(N8nIR.model_validate(_sample_kafka_ir()))

    trigger = workflow["nodes"][0]
    assert trigger["type"] == "n8n-nodes-base.kafkaTrigger"
    assert trigger["typeVersion"] == 1.3
    assert trigger["parameters"]["topic"] == "security-alerts"
    assert trigger["parameters"]["groupId"] == "flocks_kafka_n8n_flocks_kafka_alerts"
    assert trigger["parameters"]["resolveOffset"] == "onCompletion"
    assert trigger["parameters"]["options"]["fromBeginning"] is False
    assert trigger["parameters"]["options"]["batchSize"] == 1
    assert trigger["credentials"]["kafka"]["name"] == "Kafka Production"

    issues = lint_workflow(workflow, require_tests=True, tests=[], kafka_group_prefixes=["flocks_kafka"])
    assert [issue.to_dict() for issue in issues if issue.severity == "error"] == []


def test_render_kafka_trigger_accepts_explicit_group_id_and_on_success() -> None:
    ir = _sample_kafka_ir()
    ir["trigger"]["groupId"] = "flocks_kafka_prod_consumer"
    ir["trigger"]["resolveOffset"] = "onSuccess"

    workflow = render_ir_to_workflow(N8nIR.model_validate(ir))
    trigger = workflow["nodes"][0]

    assert trigger["parameters"]["groupId"] == "flocks_kafka_prod_consumer"
    assert trigger["parameters"]["resolveOffset"] == "onSuccess"
    assert [issue for issue in lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"]) if issue.severity == "error"] == []


def test_lint_catches_invalid_kafka_trigger() -> None:
    workflow = render_ir_to_workflow(_sample_kafka_ir())
    trigger = workflow["nodes"][0]
    trigger["parameters"]["topic"] = ""
    trigger["parameters"]["groupId"] = ""
    trigger.pop("credentials", None)

    issues = lint_workflow(workflow, require_tests=True, tests=[])
    codes = {issue.code for issue in issues if issue.severity == "error"}

    assert {"KAFKA-TOPIC", "KAFKA-GROUP", "KAFKA-CREDENTIAL"} <= codes


def test_lint_blocks_invalid_kafka_offset_and_group_prefix() -> None:
    workflow = render_ir_to_workflow(_sample_kafka_ir())
    trigger = workflow["nodes"][0]
    trigger["parameters"]["groupId"] = "flocks-n8n-security-alerts"
    trigger["parameters"]["resolveOffset"] = "latest"

    issues = lint_workflow(workflow, require_tests=True, tests=[], kafka_group_prefixes=["flocks_kafka"])
    codes = {issue.code for issue in issues if issue.severity == "error"}

    assert "KAFKA-GROUP-PREFIX" in codes
    assert "KAFKA-RESOLVE-OFFSET" in codes


def test_render_http_request_with_n8n_credentials() -> None:
    workflow = render_ir_to_workflow(
        {
            "name": "flocks-test-http-credential",
            "trigger": {"type": "webhook", "method": "POST", "path": "http-credential"},
            "steps": [
                {
                    "id": "lookup",
                    "kind": "http_request",
                    "method": "GET",
                    "url": "https://api.example.com/ioc/{{$json.body.ioc}}",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "credentials": {
                        "httpHeaderAuth": {
                            "name": "ThreatBook API",
                            "type": "httpHeaderAuth",
                        }
                    },
                    "next": "respond",
                },
                {
                    "id": "respond",
                    "kind": "respond_to_webhook",
                    "response_body": "={{ $json }}",
                },
            ],
            "tests": [{"name": "lookup", "input": {"ioc": "1.1.1.1"}}],
        }
    )

    node = workflow["nodes"][1]

    assert node["parameters"]["authentication"] == "genericCredentialType"
    assert node["parameters"]["genericAuthType"] == "httpHeaderAuth"
    assert node["credentials"]["httpHeaderAuth"]["name"] == "ThreatBook API"
    assert [issue for issue in lint_workflow(workflow, require_tests=True, tests=[{"name": "lookup"}]) if issue.severity == "error"] == []


def test_lint_blocks_flocks_runtime_callbacks_and_secret_placeholders() -> None:
    workflow = render_ir_to_workflow(
        {
            "name": "flocks-test-invalid-runtime",
            "trigger": {"type": "webhook", "method": "POST", "path": "invalid-runtime"},
            "steps": [
                {
                    "id": "callback",
                    "kind": "http_request",
                    "method": "POST",
                    "url": "http://localhost:8000/api/mcp/threatbook",
                    "headers": {
                        "Authorization": "Bearer {{secrets.THREATBOOK_API_KEY}}",
                    },
                    "body": {"apikey": "{secret}"},
                    "next": "respond",
                },
                {
                    "id": "respond",
                    "kind": "respond_to_webhook",
                    "response_body": "={{ $json }}",
                },
            ],
            "tests": [{"name": "blocked", "input": {"ioc": "1.1.1.1"}}],
        }
    )

    codes = {issue.code for issue in lint_workflow(workflow, require_tests=True, tests=[{"name": "blocked"}])}

    assert "FLOCKS-RUNTIME-CALLBACK" in codes
    assert "FLOCKS-SECRET-REF" in codes


def test_lint_blocks_literal_sensitive_http_headers() -> None:
    workflow = render_ir_to_workflow(
        {
            "name": "flocks-test-literal-secret",
            "trigger": {"type": "webhook", "method": "POST", "path": "literal-secret"},
            "steps": [
                {
                    "id": "lookup",
                    "kind": "http_request",
                    "method": "GET",
                    "url": "https://api.example.com/ioc",
                    "headers": {"X-API-Key": "plain-secret-value-12345"},
                    "next": "respond",
                },
                {
                    "id": "respond",
                    "kind": "respond_to_webhook",
                    "response_body": "={{ $json }}",
                },
            ],
            "tests": [{"name": "blocked", "input": {"ioc": "1.1.1.1"}}],
        }
    )

    codes = {issue.code for issue in lint_workflow(workflow, require_tests=True, tests=[{"name": "blocked"}])}

    assert "SECRET-HEADER" in codes


def test_lint_blocks_literal_secret_query_params() -> None:
    workflow = render_ir_to_workflow(
        {
            "name": "flocks-test-literal-secret-url",
            "trigger": {"type": "webhook", "method": "POST", "path": "literal-secret-url"},
            "steps": [
                {
                    "id": "lookup",
                    "kind": "http_request",
                    "method": "GET",
                    "url": "https://api.example.com/ioc?apikey=plain-secret-value-12345",
                    "next": "respond",
                },
                {
                    "id": "respond",
                    "kind": "respond_to_webhook",
                    "response_body": "={{ $json }}",
                },
            ],
            "tests": [{"name": "blocked", "input": {"ioc": "1.1.1.1"}}],
        }
    )

    codes = {issue.code for issue in lint_workflow(workflow, require_tests=True, tests=[{"name": "blocked"}])}

    assert "SECRET-URL" in codes


def test_lint_blocks_unavailable_code_modules_for_n8n_2354() -> None:
    workflow = render_ir_to_workflow(
        {
            "name": "flocks-test-local-file-code",
            "trigger": {"type": "webhook", "method": "POST", "path": "local-file-code"},
            "steps": [
                {
                    "id": "write_file",
                    "kind": "code",
                    "js_code": "const fs = require('fs'); fs.writeFileSync('/tmp/out.json', '{}'); return $input.all();",
                    "next": "respond",
                },
                {
                    "id": "respond",
                    "kind": "respond_to_webhook",
                    "response_body": "={{ $json }}",
                },
            ],
            "tests": [{"name": "blocked", "input": {"name": "Alice"}}],
        }
    )

    codes = {issue.code for issue in lint_workflow(workflow, require_tests=True, tests=[{"name": "blocked"}])}

    assert "CODE-MODULE-UNAVAILABLE" in codes


def test_render_convert_to_file_and_write_file_nodes_and_lint_success() -> None:
    ir = _sample_kafka_ir()
    ir["steps"] = [
        {
            "id": "convert_triage_result",
            "kind": "convert_to_file",
            "name": "Convert Triage Result",
            "outputFileName": "triage.json",
            "dataPropertyName": "data",
            "formatJson": True,
            "next": "write_triage_result",
        },
        {
            "id": "write_triage_result",
            "kind": "write_file",
            "name": "Write Triage Result",
            "fileName": "/home/node/flocks_workspace/tdp_alerts/triage.jsonl",
            "dataPropertyName": "data",
            "append": True,
        }
    ]

    workflow = render_ir_to_workflow(N8nIR.model_validate(ir))
    convert_node = workflow["nodes"][1]
    write_node = workflow["nodes"][2]

    assert convert_node["type"] == "n8n-nodes-base.convertToFile"
    assert convert_node["typeVersion"] == 1.1
    assert convert_node["parameters"] == {
        "operation": "toJson",
        "mode": "once",
        "binaryPropertyName": "data",
        "options": {"encoding": "utf8", "format": True, "fileName": "triage.json"},
    }
    assert write_node["type"] == "n8n-nodes-base.readWriteFile"
    assert write_node["typeVersion"] == 1.1
    assert write_node["parameters"] == {
        "operation": "write",
        "fileName": "/home/node/flocks_workspace/tdp_alerts/triage.jsonl",
        "dataPropertyName": "data",
        "options": {"append": True},
    }
    assert workflow["connections"]["Convert Triage Result"]["main"][0][0]["node"] == "Write Triage Result"
    assert [issue for issue in lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"]) if issue.severity == "error"] == []


def test_legacy_write_binary_file_ir_renders_read_write_file_node() -> None:
    ir = _sample_kafka_ir()
    ir["steps"] = [
        {
            "id": "write_triage_result",
            "kind": "write_binary_file",
            "name": "Write Triage Result",
            "fileName": "/home/node/flocks_workspace/tdp_alerts/triage.jsonl",
            "dataPropertyName": "data",
        }
    ]

    workflow = render_ir_to_workflow(N8nIR.model_validate(ir))
    node = workflow["nodes"][1]

    assert node["type"] == "n8n-nodes-base.readWriteFile"
    assert node["parameters"]["operation"] == "write"
    assert node["parameters"]["fileName"] == "/home/node/flocks_workspace/tdp_alerts/triage.jsonl"
    assert node["parameters"]["dataPropertyName"] == "data"
    assert [issue for issue in lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"]) if issue.severity == "error"] == []


def test_lint_catches_invalid_file_write_node() -> None:
    ir = _sample_kafka_ir()
    ir["steps"] = [
        {
            "id": "write_triage_result",
            "kind": "write_file",
            "fileName": "/tmp/triage.jsonl",
            "dataPropertyName": "data",
        }
    ]
    workflow = render_ir_to_workflow(N8nIR.model_validate(ir))
    node = workflow["nodes"][1]
    node["parameters"]["fileName"] = ""
    node["parameters"]["dataPropertyName"] = ""

    codes = {issue.code for issue in lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"]) if issue.severity == "error"}

    assert {"FILE-WRITE-PATH", "FILE-WRITE-DATA"} <= codes


def test_lint_warns_about_tilde_write_file_path() -> None:
    ir = _sample_kafka_ir()
    ir["steps"] = [
        {
            "id": "write_triage_result",
            "kind": "write_file",
            "fileName": "~/.flocks/workspace/tdp_alerts/triage.jsonl",
            "dataPropertyName": "data",
        }
    ]

    workflow = render_ir_to_workflow(N8nIR.model_validate(ir))
    issues = lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"])

    assert "FILE-WRITE-TILDE" in {issue.code for issue in issues if issue.severity == "warning"}
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_lint_warns_about_legacy_native_write_binary_file_node() -> None:
    workflow = render_ir_to_workflow(_sample_kafka_ir())
    node = workflow["nodes"][1]
    node["type"] = "n8n-nodes-base.writeBinaryFile"
    node["typeVersion"] = 1
    node["parameters"] = {
        "fileName": "/home/node/flocks_workspace/tdp_alerts/triage.jsonl",
        "dataPropertyName": "data",
    }

    issues = lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"])

    assert "FILE-WRITE-LEGACY" in {issue.code for issue in issues if issue.severity == "warning"}
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_lint_catches_invalid_convert_to_file_node() -> None:
    workflow = render_ir_to_workflow(
        {
            **_sample_kafka_ir(),
            "steps": [
                {
                    "id": "convert",
                    "kind": "convert_to_file",
                    "dataPropertyName": "data",
                }
            ],
        }
    )
    node = workflow["nodes"][1]
    node["parameters"]["operation"] = "toText"
    node["parameters"]["mode"] = "bad"
    node["parameters"]["binaryPropertyName"] = ""

    codes = {issue.code for issue in lint_workflow(workflow, kafka_group_prefixes=["flocks_kafka"]) if issue.severity == "error"}

    assert {"CONVERT-FILE-OP", "CONVERT-FILE-MODE", "CONVERT-FILE-BINARY"} <= codes


def test_lint_allows_mcp_text_in_non_runtime_metadata() -> None:
    workflow = render_ir_to_workflow(_sample_ir())
    workflow["name"] = "flocks-test-mcp-migration"

    codes = {issue.code for issue in lint_workflow(workflow, require_tests=True, tests=_sample_ir()["tests"])}

    assert "FLOCKS-RUNTIME-REF" not in codes


def test_api_payload_removes_readonly_fields() -> None:
    workflow = render_ir_to_workflow(_sample_ir(), workflow_id="wf-cli-id")
    payload = workflow_to_api_create_payload(workflow)

    for key in ("id", "versionId", "active", "meta", "tags"):
        assert key not in payload
    assert payload["name"] == "flocks-test-hello"

    issues = lint_workflow(payload, for_api_create=True)
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_lint_catches_bad_connection_and_api_readonly() -> None:
    workflow = render_ir_to_workflow(_sample_ir(), workflow_id="wf-cli-id")
    workflow["connections"]["Missing"] = {"main": [[{"node": "Nope", "type": "main", "index": 0}]]}

    issues = lint_workflow(workflow, for_api_create=True)
    codes = {issue.code for issue in issues}

    assert "API-READONLY" in codes
    assert "CONN-SOURCE" in codes
    assert "CONN-TARGET" in codes


def test_repair_context_redacts_secrets() -> None:
    context = build_repair_context(
        user_request="make workflow",
        ir=_sample_ir(),
        workflow={
            "name": "wf",
            "nodes": [
                {
                    "name": "Code",
                    "type": "n8n-nodes-base.code",
                    "parameters": {"jsCode": "const token='abc123456789secret';"},
                }
            ],
            "connections": {},
        },
        lint_issues=[],
        test_results=[
            {
                "headers": {"Authorization": "Bearer abc123456789secret"},
                "error": "token=abc123456789secret",
            }
        ],
        iteration=1,
    )

    rendered = str(context)
    assert "token=<redacted>" in rendered
    assert "Authorization': '<redacted>'" in rendered
    assert "abc123456789secret" not in rendered


def test_get_webhook_payload_becomes_query_string() -> None:
    calls = []

    class CapturingClient(N8nClient):
        def _request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"status": 200, "body": {}}

    CapturingClient(N8nConfig()).call_webhook("demo", method="GET", payload={"name": "Alice"})

    assert calls[0][0] == "GET"
    assert calls[0][1] == "/webhook/demo?name=Alice"
    assert calls[0][2]["body"] is None
