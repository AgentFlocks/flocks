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
