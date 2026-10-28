"""Static checks for executable node code; never execute workflow Python."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import WorkflowValidationError
from .models import Workflow


def python_syntax_errors(workflow: Workflow | Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    nodes = workflow.nodes if isinstance(workflow, Workflow) else workflow.get("nodes", [])
    for node in nodes:
        node_type = node.get("type", "python") if isinstance(node, Mapping) else node.type
        code = node.get("code") if isinstance(node, Mapping) else node.code
        node_id = node.get("id", "<unknown>") if isinstance(node, Mapping) else node.id
        # Branch/loop nodes select an edge; their optional code is not executed.
        # A logic node may already contain generated Python, which is executed.
        if node_type not in {"python", "logic"} or not code:
            continue
        try:
            compile(code, f"<workflow-node:{node_id}>", "exec", dont_inherit=True)
        except SyntaxError as exc:
            errors.append(
                {
                    "kind": "python_syntax_error",
                    "severity": "error",
                    "node_id": node_id,
                    "line": exc.lineno,
                    "column": exc.offset,
                    "message": f"Node {node_id!r}, line {exc.lineno}: {exc.msg}",
                }
            )
    return errors


def validate_workflow_python(workflow: Workflow | Mapping[str, Any]) -> None:
    errors = python_syntax_errors(workflow)
    if errors:
        raise WorkflowValidationError(
            "Workflow Python syntax validation failed: "
            + "; ".join(item["message"] for item in errors[:5])
        )
