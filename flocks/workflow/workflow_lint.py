"""Workflow lints and best-effort static checks."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Set

from .models import Node, Workflow


_OUTPUTS_SUBSCRIPT_RE = re.compile(r"""outputs\[\s*['"](?P<key>[^'"]+)['"]\s*\]""")
_CN_OUTPUT_LINE_RE = re.compile(r"输出[:：]\s*([^\n。；;]+)")
_CN_BULLET_KEY_RE = re.compile(r"^\s*[-*]\s*(?P<key>[A-Za-z0-9_\-]+)\s*[:：]\s*")
_CN_SECTION_OUTPUT_RE = re.compile(r"^\s*输出要求\s*[:：]?\s*$")

# Patterns that indicate an "expensive" node (LLM call / file write).
_EXPENSIVE_CALL_RE = re.compile(r"""llm\.ask\s*\(|tool\.run\s*\(\s*['"]write['"]""")


def _split_keys(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = re.split(r"[，,、\s]+", raw)
    return [p.strip() for p in parts if p and p.strip()]


def estimate_node_output_keys(node: Node) -> Set[str]:
    keys: set[str] = set()
    if node.type == "python" and node.code:
        for m in _OUTPUTS_SUBSCRIPT_RE.finditer(node.code):
            k = (m.group("key") or "").strip()
            if k:
                keys.add(k)
        return keys
    if node.type == "logic" and node.description:
        desc = node.description
        m = _CN_OUTPUT_LINE_RE.search(desc)
        if m:
            keys.update(_split_keys((m.group(1) or "").strip()))
        lines = desc.splitlines()
        in_output_section = False
        for ln in lines:
            if _CN_SECTION_OUTPUT_RE.match(ln):
                in_output_section = True
                continue
            if in_output_section:
                if not ln.strip():
                    continue
                bm = _CN_BULLET_KEY_RE.match(ln)
                if bm:
                    k = (bm.group("key") or "").strip()
                    if k:
                        keys.add(k)
                    continue
                break
    if node.type == "tool":
        keys.add(node.output_key or "result")
    if node.type == "llm":
        keys.add(node.output_key or "result")
    if node.type == "http_request":
        keys.add(node.response_key or "response")
        keys.add("status_code")
    if node.type == "subworkflow":
        keys.add(node.output_key or "output")
    return keys


def _strict_edge_mapping_enabled(workflow: Workflow) -> bool:
    metadata = workflow.metadata if isinstance(workflow.metadata, dict) else {}
    candidates = [
        metadata.get("strict_edge_mapping"),
        metadata.get("strictEdgeMapping"),
    ]
    for section_key in ("runtime", "runtime_defaults", "runtimeDefaults"):
        section = metadata.get(section_key)
        if isinstance(section, dict):
            candidates.extend(
                [
                    section.get("strict_edge_mapping"),
                    section.get("strictEdgeMapping"),
                ]
            )
    for value in candidates:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
    return False


def lint_implicit_full_payload_edges(workflow: Workflow) -> List[Dict[str, Any]]:
    nodes = workflow.nodes_by_id()
    strict = _strict_edge_mapping_enabled(workflow)
    severity = "error" if strict else "warning"
    results: list[dict[str, Any]] = []
    for edge in workflow.edges:
        if edge.mapping:
            continue
        upstream = nodes.get(edge.from_)
        downstream = nodes.get(edge.to)
        results.append(
            {
                "kind": "implicit_full_payload_edge",
                "severity": severity,
                "edge_from": edge.from_,
                "edge_to": edge.to,
                "upstream_type": getattr(upstream, "type", None),
                "downstream_type": getattr(downstream, "type", None),
                "strict_edge_mapping": strict,
                "message": (
                    f"edge {edge.from_!r}->{edge.to!r} has no mapping; the full upstream "
                    "payload will be passed to the downstream node"
                ),
            }
        )
    return results


def lint_workflow_mappings(workflow: Workflow) -> List[Dict[str, Any]]:
    nodes = workflow.nodes_by_id()
    warnings: list[dict[str, Any]] = []
    for e in workflow.edges:
        if not e.mapping:
            continue
        upstream = nodes.get(e.from_)
        upstream_out = estimate_node_output_keys(upstream) if upstream is not None else set()
        for dst, src in e.mapping.items():
            src_path = "" if src is None else str(src).strip()
            if not src_path or src_path == "$":
                continue
            if src_path.startswith("$."):
                src_path = src_path[2:]
            top_key = src_path.split(".", 1)[0] if src_path else ""
            if top_key and upstream_out and top_key not in upstream_out:
                warnings.append(
                    {
                        "kind": "mapping_src_key_not_in_upstream_outputs",
                        "edge_from": e.from_,
                        "edge_to": e.to,
                        "dst_key": dst,
                        "src_path": src,
                        "upstream_type": getattr(upstream, "type", None),
                        "estimated_upstream_output_keys": sorted(upstream_out)[:50],
                        "message": (
                            f"edge.mapping maps src {src!r} but upstream node {e.from_!r} "
                            "does not appear to write that key to outputs; mapping may produce missing value"
                        ),
                    }
                )
            if dst == src and not (e.const or {}):
                warnings.append(
                    {
                        "kind": "scheme_a_suggest_omit_identity_mapping",
                        "severity": "warning",
                        "edge_from": e.from_,
                        "edge_to": e.to,
                        "dst_key": dst,
                        "src_path": src,
                        "message": (
                            "edge.mapping is an identity mapping. Scheme A recommends omitting mapping "
                            "to pass through the full payload and reduce missing-key issues."
                        ),
                    }
                )
    return warnings


# ---------------------------------------------------------------------------
# Join-safety checks
# ---------------------------------------------------------------------------


def _is_node_expensive(node: Node) -> bool:
    """Heuristic: does this node contain LLM calls or file-write tool calls?"""
    code = node.code or ""
    desc = node.description or ""
    return bool(_EXPENSIVE_CALL_RE.search(code) or _EXPENSIVE_CALL_RE.search(desc))


def _build_branch_exclusive_groups(workflow: Workflow) -> Dict[str, Set[str]]:
    """Return {branch_node_id: set_of_direct_target_ids} for branch/loop nodes.

    Edges from the same branch/loop with different labels are mutually exclusive
    at runtime (only one label fires), so their targets form an exclusive group.
    """
    nodes = workflow.nodes_by_id()
    groups: Dict[str, Set[str]] = {}
    for e in workflow.edges:
        src = nodes.get(e.from_)
        if src and src.type in ("branch", "loop") and e.label is not None:
            groups.setdefault(e.from_, set()).add(e.to)
    return groups


def lint_join_requirements(workflow: Workflow) -> List[Dict[str, Any]]:
    """Check nodes with multiple incoming edges that may need ``join=true``.

    Rules:
    - If a node has >=2 incoming edges from **non-exclusive** sources and
      ``join`` is not set, emit an **error** (the node will execute multiple
      times which is almost always unintended).
    - "Exclusive" means all incoming sources are targets of the same
      ``branch``/``loop`` node with different labels (only one fires at runtime).
    """
    nodes = workflow.nodes_by_id()
    exclusive_groups = _build_branch_exclusive_groups(workflow)
    results: List[Dict[str, Any]] = []

    # incoming_from: node_id -> list of source node ids
    incoming: Dict[str, List[str]] = {n.id: [] for n in workflow.nodes}
    for e in workflow.edges:
        incoming.setdefault(e.to, []).append(e.from_)

    for nid, sources in incoming.items():
        if len(sources) < 2:
            continue
        node = nodes.get(nid)
        if node is None:
            continue
        if getattr(node, "join", False):
            continue  # already has join, OK

        unique_sources = set(sources)

        # Check whether *all* sources come from the same branch's exclusive
        # fan-out edges.  This requires two things:
        #   1. All sources are direct targets of the same branch node.
        #   2. No source appears more than once (no duplicate edge from same
        #      branch to the same target via different labels -- rare but
        #      possible).
        is_exclusive = False
        for _branch_id, targets in exclusive_groups.items():
            if unique_sources.issubset(targets):
                is_exclusive = True
                break

        if not is_exclusive:
            results.append(
                {
                    "kind": "multi_incoming_no_join",
                    "severity": "error",
                    "node_id": nid,
                    "sources": sorted(sources),
                    "message": (
                        f"Node {nid!r} has {len(sources)} incoming edges from "
                        f"non-exclusive sources {sorted(unique_sources)} but join=false. "
                        "This will cause the node to execute multiple times. "
                        "Set join=true on this node or restructure edges."
                    ),
                }
            )
    return results


def lint_expensive_node_multi_trigger(workflow: Workflow) -> List[Dict[str, Any]]:
    """Detect expensive nodes (LLM / write) reachable via multiple non-exclusive paths.

    Even if an expensive node has only one direct incoming edge, it may still
    be triggered multiple times if it sits downstream of a fan-out that does
    not converge through a join.  This check handles the simpler case:
    expensive node with >=2 incoming edges and no join.
    """
    nodes = workflow.nodes_by_id()
    exclusive_groups = _build_branch_exclusive_groups(workflow)
    results: List[Dict[str, Any]] = []

    incoming: Dict[str, List[str]] = {n.id: [] for n in workflow.nodes}
    for e in workflow.edges:
        incoming.setdefault(e.to, []).append(e.from_)

    for nid, sources in incoming.items():
        if len(sources) < 2:
            continue
        node = nodes.get(nid)
        if node is None:
            continue
        if getattr(node, "join", False):
            continue
        if not _is_node_expensive(node):
            continue

        unique_sources = set(sources)
        is_exclusive = False
        for _branch_id, targets in exclusive_groups.items():
            if unique_sources.issubset(targets):
                is_exclusive = True
                break

        if not is_exclusive:
            results.append(
                {
                    "kind": "expensive_node_multi_trigger",
                    "severity": "error",
                    "node_id": nid,
                    "sources": sorted(sources),
                    "message": (
                        f"Expensive node {nid!r} (contains LLM/write calls) has "
                        f"{len(sources)} non-exclusive incoming edges but join=false. "
                        "This may cause costly duplicate execution. "
                        "Add a join node before this expensive node."
                    ),
                }
            )
    return results


# ---------------------------------------------------------------------------
# SW-001 / SW-002: Sub-workflow lint rules
# ---------------------------------------------------------------------------


def lint_subworkflow_depth(workflow: Workflow) -> List[Dict[str, Any]]:
    """SW-001: A workflow that is itself a sub-workflow must not nest further sub-workflows.

    This is a static check that detects if the given workflow contains
    ``subworkflow`` nodes.  The caller is expected to provide the context
    (i.e. whether this workflow is being used as a sub-workflow).
    Returns errors for each ``subworkflow`` node found so the caller can
    decide severity based on nesting context.
    """
    results: List[Dict[str, Any]] = []
    for node in workflow.nodes:
        if node.type == "subworkflow":
            results.append(
                {
                    "kind": "SW-001",
                    "severity": "error",
                    "node_id": node.id,
                    "message": (
                        f"Node {node.id!r} is a subworkflow node. "
                        "Sub-workflows cannot nest further sub-workflows (max depth=1)."
                    ),
                }
            )
    return results


def lint_subworkflow_ids(
    workflow: Workflow,
    known_workflow_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """SW-002: Every subworkflow node's workflow_id must reference an existing workflow.

    If ``known_workflow_ids`` is None the check is skipped (IDs unknown at
    static-analysis time).  Pass a set of known IDs to enable full validation.
    """
    results: List[Dict[str, Any]] = []
    if known_workflow_ids is None:
        return results
    for node in workflow.nodes:
        if node.type == "subworkflow":
            wid = node.workflow_id or ""
            if not wid:
                results.append(
                    {
                        "kind": "SW-002",
                        "severity": "error",
                        "node_id": node.id,
                        "message": f"subworkflow node {node.id!r} has no workflow_id set.",
                    }
                )
            elif wid not in known_workflow_ids:
                results.append(
                    {
                        "kind": "SW-002",
                        "severity": "error",
                        "node_id": node.id,
                        "workflow_id": wid,
                        "message": (
                            f"subworkflow node {node.id!r} references workflow_id={wid!r} "
                            "which was not found in the known workflow registry."
                        ),
                    }
                )
    return results


# ---------------------------------------------------------------------------
# Unified lint entry-point
# ---------------------------------------------------------------------------


def lint_workflow(
    workflow: Workflow,
    *,
    known_workflow_ids: Optional[Set[str]] = None,
    is_sub_workflow: bool = False,
) -> List[Dict[str, Any]]:
    """Run all lint checks and return combined results.

    Each item is a dict with at least ``kind``, ``severity``, and ``message``.
    ``severity`` is one of ``"error"`` or ``"warning"``.

    Args:
        workflow: The workflow to lint.
        known_workflow_ids: If provided, SW-002 checks whether referenced
            subworkflow IDs exist in this set.
        is_sub_workflow: If True, SW-001 is activated to disallow nested
            subworkflow nodes.
    """
    results: List[Dict[str, Any]] = []
    results.extend(lint_implicit_full_payload_edges(workflow))
    # Existing mapping checks (warnings)
    for item in lint_workflow_mappings(workflow):
        item.setdefault("severity", "warning")
        results.append(item)
    # Join safety (errors)
    results.extend(lint_join_requirements(workflow))
    # Expensive node multi-trigger (errors)
    results.extend(lint_expensive_node_multi_trigger(workflow))
    # SW-001: sub-workflow nesting depth
    if is_sub_workflow:
        results.extend(lint_subworkflow_depth(workflow))
    # SW-002: subworkflow_id existence
    results.extend(lint_subworkflow_ids(workflow, known_workflow_ids=known_workflow_ids))
    return results
