"""Repair-context construction and sanitization for n8n workflow debugging."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict


SENSITIVE_KEYS = re.compile(r"(?i)(authorization|api[_-]?key|token|secret|password|cookie|set-cookie)")


def sanitize_for_repair(value: Any, *, max_string: int = 2000) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if SENSITIVE_KEYS.search(str(key)):
                out[key] = "<redacted>"
            else:
                out[key] = sanitize_for_repair(item, max_string=max_string)
        return out
    if isinstance(value, list):
        return [sanitize_for_repair(item, max_string=max_string) for item in value[:50]]
    if isinstance(value, str):
        text = value
        text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]{12,}", "Bearer <redacted>", text)
        text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^&\s]{8,})", r"\1=<redacted>", text)
        text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]", r"\1='<redacted>'", text)
        return text[:max_string] + ("..." if len(text) > max_string else "")
    return copy.deepcopy(value)


def build_repair_context(
    *,
    user_request: str,
    ir: Dict[str, Any],
    workflow: Dict[str, Any],
    lint_issues: list[Dict[str, Any]],
    test_results: list[Dict[str, Any]],
    iteration: int,
) -> Dict[str, Any]:
    return sanitize_for_repair(
        {
            "iteration": iteration,
            "user_request": user_request,
            "ir": ir,
            "workflow_summary": {
                "name": workflow.get("name"),
                "nodes": [
                    {
                        "name": node.get("name"),
                        "type": node.get("type"),
                        "parameters": node.get("parameters"),
                    }
                    for node in workflow.get("nodes", [])
                    if isinstance(node, dict)
                ],
                "connections": workflow.get("connections"),
            },
            "lint_issues": lint_issues,
            "test_results": test_results,
            "repair_rules": [
                "Do not remove core business steps only to pass one sample.",
                "Do not hard-code a test response unless the user explicitly requested a constant response.",
                "Do not introduce plaintext credentials.",
                "Keep the IR as the source of business intent.",
            ],
        }
    )
