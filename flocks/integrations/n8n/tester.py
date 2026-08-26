"""Runtime testing helpers for n8n webhook workflows."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from flocks.integrations.n8n.client import N8nClient, N8nClientError
from flocks.integrations.n8n.deep_debug import build_deep_debug_report
from flocks.integrations.n8n.models import N8nTestCase


@dataclass
class N8nTestResult:
    name: str
    success: bool
    status: Optional[int] = None
    response: Any = None
    error: Optional[str] = None
    assertions: List[Dict[str, Any]] = field(default_factory=list)
    execution: Optional[Dict[str, Any]] = None
    deep_debug: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "status": self.status,
            "response": self.response,
            "error": self.error,
            "assertions": self.assertions,
            "execution": self.execution,
            "deepDebug": self.deep_debug,
        }


def run_webhook_tests(
    client: N8nClient,
    *,
    webhook_path: str,
    tests: Iterable[N8nTestCase | Dict[str, Any]],
    method: str = "POST",
    workflow_id: Optional[str] = None,
    workflow: Optional[Dict[str, Any]] = None,
    wait_for_execution: bool = False,
) -> List[N8nTestResult]:
    results: List[N8nTestResult] = []
    for raw_case in tests:
        case = raw_case if isinstance(raw_case, N8nTestCase) else N8nTestCase.model_validate(raw_case)
        started = time.time()
        try:
            response = client.call_webhook(
                webhook_path,
                method=case.method or method,
                payload=case.input,
                headers=case.headers,
            )
            assertions = _assert_response(response, case)
            execution = None
            if wait_for_execution and workflow_id:
                execution = client.wait_for_recent_execution(workflow_id=workflow_id, since_epoch_s=started)
            deep_debug = _load_deep_debug_report(client, workflow=workflow, execution=execution, trigger_input=case.input)
            results.append(
                N8nTestResult(
                    name=case.name,
                    success=all(item["success"] for item in assertions),
                    status=response["status"],
                    response=response["body"],
                    assertions=assertions,
                    execution=execution,
                    deep_debug=deep_debug,
                )
            )
        except N8nClientError as exc:
            results.append(
                N8nTestResult(
                    name=case.name,
                    success=False,
                    status=exc.status,
                    response=_parse_body(exc.body),
                    error=str(exc),
                )
            )
    return results


def _load_deep_debug_report(
    client: N8nClient,
    *,
    workflow: Optional[Dict[str, Any]],
    execution: Optional[Dict[str, Any]],
    trigger_input: Any,
) -> Optional[Dict[str, Any]]:
    if not execution or execution.get("id") is None:
        return None
    try:
        detailed = client.get_execution(str(execution["id"]), include_data=True)
        return build_deep_debug_report(workflow=workflow, execution=detailed, trigger_input=trigger_input)
    except Exception as exc:
        return {
            "mode": "execution_trace",
            "status": "unavailable",
            "executionId": str(execution.get("id")),
            "nodeTraces": [],
            "limitations": [f"failed to load n8n execution detail: {exc}"],
        }


def _parse_body(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _assert_response(response: Dict[str, Any], case: N8nTestCase) -> List[Dict[str, Any]]:
    body = response.get("body")
    assertions = []
    expected_status = case.expect.status
    if expected_status is not None:
        assertions.append(
            {
                "type": "status",
                "expected": expected_status,
                "actual": response.get("status"),
                "success": response.get("status") == expected_status,
            }
        )
    for key, expected in case.expect.json_contains.items():
        actual = body.get(key) if isinstance(body, dict) else None
        assertions.append(
            {
                "type": "jsonContains",
                "path": key,
                "expected": expected,
                "actual": actual,
                "success": actual == expected,
            }
        )
    if case.expect.text_contains:
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
        assertions.append(
            {
                "type": "textContains",
                "expected": case.expect.text_contains,
                "actual": text,
                "success": case.expect.text_contains in text,
            }
        )
    return assertions
