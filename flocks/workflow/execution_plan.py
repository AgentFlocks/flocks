"""Reusable static workflow execution plan.

The plan intentionally contains only immutable-ish workflow structure and
derived metadata. Per-run state such as node outputs, history, joins, and
runtime globals must stay in ``WorkflowExecutionState`` / runtime instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from .models import Edge, Node, Workflow
from .requirements import requirements_from_workflow_metadata
from .workflow_lint import lint_workflow


def resolve_workflow_dataflow_mode(workflow_metadata: Optional[Dict[str, Any]]) -> Literal["legacy", "vertex_cache"]:
    """Resolve workflow dataflow mode from metadata.

    Missing metadata intentionally stays legacy so historical workflow files run
    with their original full-payload edge semantics.
    """
    if not isinstance(workflow_metadata, dict):
        return "legacy"

    candidates: list[Any] = [
        workflow_metadata.get("dataflow_mode"),
        workflow_metadata.get("dataflowMode"),
    ]
    for section_key in ("runtime", "runtime_defaults", "runtimeDefaults"):
        section = workflow_metadata.get(section_key)
        if isinstance(section, dict):
            candidates.extend(
                [
                    section.get("dataflow_mode"),
                    section.get("dataflowMode"),
                ]
            )

    for value in candidates:
        normalized = str(value or "").strip().lower().replace("-", "_")
        if normalized in {"vertex_cache", "vertex", "cache"}:
            return "vertex_cache"
        if normalized in {"legacy", "classic", "default"}:
            return "legacy"
    return "legacy"


def _incoming_edges_by_node(workflow: Workflow) -> Dict[str, List[str]]:
    incoming_from: Dict[str, List[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        incoming_from.setdefault(edge.to, []).append(edge.from_)
    for node_id in incoming_from:
        incoming_from[node_id].sort()
    return incoming_from


@dataclass(frozen=True)
class WorkflowExecutionPlan:
    """Precomputed static workflow graph data safe to reuse across runs."""

    workflow: Workflow
    workflow_path: Optional[str]
    use_llm: Optional[bool]
    lint_results: tuple[Dict[str, Any], ...]
    requirements: tuple[str, ...]
    dataflow_mode: Literal["legacy", "vertex_cache"]
    nodes_by_id: Dict[str, Node]
    adjacency: Dict[str, List[Edge]]
    incoming_from: Dict[str, List[str]]


def build_workflow_execution_plan(
    workflow: Workflow,
    *,
    workflow_path: Optional[str] = None,
    use_llm: Optional[bool] = None,
) -> WorkflowExecutionPlan:
    """Build reusable static execution data for a workflow."""
    return WorkflowExecutionPlan(
        workflow=workflow,
        workflow_path=workflow_path,
        use_llm=use_llm,
        lint_results=tuple(lint_workflow(workflow)),
        requirements=tuple(requirements_from_workflow_metadata(workflow.metadata)),
        dataflow_mode=resolve_workflow_dataflow_mode(workflow.metadata),
        nodes_by_id=workflow.nodes_by_id(),
        adjacency=workflow.adjacency(),
        incoming_from=_incoming_edges_by_node(workflow),
    )
