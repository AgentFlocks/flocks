"""Node-level debug trace extraction for n8n executions."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional


SENSITIVE_KEY = re.compile(r"(?i)(authorization|api[_-]?key|token|secret|password|cookie|set-cookie)")


def build_deep_debug_report(
    *,
    workflow: Optional[Dict[str, Any]],
    execution: Optional[Dict[str, Any]],
    trigger_input: Any = None,
) -> Dict[str, Any]:
    """Build a Flocks-owned node trace view from n8n execution data.

    The report is intentionally conservative: n8n execution output is marked as
    actual, while node input is either inferred from upstream outputs or marked
    unavailable. Future debug-copy instrumentation can upgrade input source to
    instrumented without changing the product contract.
    """

    if not execution:
        return {
            "mode": "execution_trace",
            "status": "unavailable",
            "nodeTraces": [],
            "limitations": ["n8n execution detail is unavailable"],
        }

    body = _unwrap_body(execution)
    run_data = _extract_run_data(body)
    if not run_data:
        return {
            "mode": "execution_trace",
            "status": "unavailable",
            "executionId": _execution_id(body),
            "nodeTraces": [],
            "limitations": ["n8n execution data does not include node runData"],
        }

    node_types = _workflow_node_types(workflow)
    upstream = _upstream_map(workflow)
    output_summaries: Dict[str, Dict[str, Any]] = {}
    node_traces: List[Dict[str, Any]] = []

    for node_name, runs in run_data.items():
        latest = _latest_run(runs)
        output_summary = _summarize_run_output(latest)
        output_summaries[node_name] = output_summary
        error = _run_error(latest)
        trace = {
            "nodeName": node_name,
            "nodeType": node_types.get(node_name, "unknown"),
            "status": "error" if error else ("success" if output_summary["source"] != "unavailable" else "unknown"),
            "input": _estimated_input_summary(node_name, upstream, output_summaries, trigger_input),
            "output": output_summary,
            "error": error,
        }
        node_traces.append(trace)

    return {
        "mode": "execution_trace",
        "status": "completed",
        "executionId": _execution_id(body),
        "nodeTraces": node_traces,
        "limitations": [
            "node inputs are estimated unless captured by a future debug instrumentation run",
            "raw business payload is not stored by default",
        ],
    }


def _unwrap_body(execution: Dict[str, Any]) -> Dict[str, Any]:
    body = execution.get("body") if isinstance(execution, dict) else None
    if isinstance(body, dict):
        return body
    return execution if isinstance(execution, dict) else {}


def _execution_id(execution: Dict[str, Any]) -> Optional[str]:
    value = execution.get("id")
    return str(value) if value is not None else None


def _extract_run_data(execution: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [
        execution.get("data", {}),
        execution,
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        result_data = candidate.get("resultData")
        if isinstance(result_data, dict) and isinstance(result_data.get("runData"), dict):
            return result_data["runData"]
        if isinstance(candidate.get("runData"), dict):
            return candidate["runData"]
    return {}


def _workflow_node_types(workflow: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(workflow, dict):
        return {}
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return {}
    out: Dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        node_type = node.get("type")
        if name:
            out[str(name)] = str(node_type or "unknown")
    return out


def _upstream_map(workflow: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    if not isinstance(workflow, dict):
        return {}
    connections = workflow.get("connections")
    if not isinstance(connections, dict):
        return {}
    upstream: Dict[str, List[str]] = {}
    for source, source_connections in connections.items():
        if not isinstance(source_connections, dict):
            continue
        for rows in source_connections.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list):
                    continue
                for target in row:
                    if not isinstance(target, dict) or target.get("node") is None:
                        continue
                    upstream.setdefault(str(target["node"]), []).append(str(source))
    return upstream


def _latest_run(runs: Any) -> Dict[str, Any]:
    if isinstance(runs, list) and runs:
        latest = runs[-1]
        return latest if isinstance(latest, dict) else {}
    return runs if isinstance(runs, dict) else {}


def _summarize_run_output(run: Dict[str, Any]) -> Dict[str, Any]:
    data = run.get("data") if isinstance(run, dict) else None
    if not isinstance(data, dict):
        return {"source": "unavailable", "summary": {}, "confidence": 0}
    main = data.get("main")
    items = _flatten_main_items(main)
    if not items:
        return {"source": "actual", "summary": {"itemCount": 0, "fields": []}, "confidence": 0.8}
    return {"source": "actual", "summary": summarize_items(items), "confidence": 0.9}


def _flatten_main_items(main: Any) -> List[Any]:
    items: List[Any] = []
    if not isinstance(main, list):
        return items
    for output in main:
        if isinstance(output, list):
            items.extend(output)
    return items


def _estimated_input_summary(
    node_name: str,
    upstream: Dict[str, List[str]],
    output_summaries: Dict[str, Dict[str, Any]],
    trigger_input: Any,
) -> Dict[str, Any]:
    upstream_nodes = upstream.get(node_name) or []
    upstream_outputs = [
        output_summaries[source]
        for source in upstream_nodes
        if source in output_summaries and output_summaries[source].get("source") != "unavailable"
    ]
    if upstream_outputs:
        return {
            "source": "estimated",
            "summary": {
                "upstreamNodes": upstream_nodes,
                "upstreamOutputs": [item.get("summary", {}) for item in upstream_outputs],
            },
            "confidence": 0.65,
        }
    if trigger_input is not None:
        return {
            "source": "estimated",
            "summary": summarize_items([{"json": trigger_input}]),
            "confidence": 0.55,
        }
    return {"source": "unavailable", "summary": {}, "confidence": 0}


def _run_error(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    error = run.get("error") if isinstance(run, dict) else None
    if not error:
        return None
    if isinstance(error, dict):
        message = error.get("message") or error.get("description") or str(error)
        return {
            "message": str(message),
            "category": _classify_error(str(message)),
            "evidence": _safe_copy(error),
        }
    message = str(error)
    return {"message": message, "category": _classify_error(message), "evidence": message}


def _classify_error(message: str) -> str:
    text = message.lower()
    if "credential" in text or "unauthorized" in text or "401" in text or "403" in text:
        return "credential_error"
    if "expression" in text or "undefined" in text or "cannot read" in text:
        return "expression_error"
    if "timeout" in text or "econn" in text or "network" in text:
        return "network_error"
    return "runtime_error"


def summarize_items(items: Iterable[Any]) -> Dict[str, Any]:
    rows = list(items)
    fields: Dict[str, str] = {}
    samples: Dict[str, Any] = {}
    for item in rows[:20]:
        payload = item.get("json") if isinstance(item, dict) else item
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            key_text = str(key)
            fields.setdefault(key_text, _type_name(value))
            if key_text not in samples:
                samples[key_text] = _sample_value(key_text, value)
    return {
        "itemCount": len(rows),
        "fields": sorted(fields),
        "types": dict(sorted(fields.items())),
        "samples": dict(sorted(samples.items())),
    }


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _sample_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, str):
        return {
            "type": "string",
            "length": len(value),
            "hash": "sha256:" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16],
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "fields": sorted(str(key) for key in value.keys())[:20]}
    return str(type(value).__name__)


def _safe_copy(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key)
            out[key_text] = "<redacted>" if SENSITIVE_KEY.search(key_text) else _safe_copy(item)
        return out
    if isinstance(value, list):
        return [_safe_copy(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:500]
    return copy.deepcopy(value)
