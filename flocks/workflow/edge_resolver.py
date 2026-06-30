"""Workflow edge selection and input mapping."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from .models import Edge, Node


_logger = logging.getLogger("flocks.workflow.edge_resolver")
_BROADCAST_NODE_TYPES = {"python", "tool", "llm", "http_request", "subworkflow"}


class EdgeResolver:
    """Resolve selected edges and downstream inputs for one workflow run."""

    def __init__(
        self,
        *,
        dataflow_mode: Literal["legacy", "vertex_cache"],
        trace: bool = False,
    ) -> None:
        self.dataflow_mode = dataflow_mode
        self.trace = trace

    def resolve(
        self,
        *,
        node: Node,
        node_inputs: Dict[str, Any],
        node_outputs: Dict[str, Any],
        edges: List[Edge],
    ) -> list[tuple[Edge, Dict[str, Any]]]:
        if self.dataflow_mode == "vertex_cache":
            scopes = [node_outputs, node_inputs]
            selected = self.select_edges_from_scopes(node, scopes, edges)
            return [(edge, self.build_downstream_inputs_from_scopes(scopes, edge)) for edge in selected]

        upstream = dict(node_inputs)
        upstream.update(node_outputs)
        selected = self.select_edges(node, upstream, edges)
        return [(edge, self.build_downstream_inputs(upstream, edge)) for edge in selected]

    def select_edges(self, node: Node, payload: Dict[str, Any], edges: List[Edge]) -> List[Edge]:
        if not edges:
            return []
        if node.type in _BROADCAST_NODE_TYPES:
            return list(edges)
        key = node.select_key or "result"
        value = self.get_by_path(payload, key)
        return self._select_by_label(value, edges)

    def select_edges_from_scopes(
        self,
        node: Node,
        scopes: List[Dict[str, Any]],
        edges: List[Edge],
    ) -> List[Edge]:
        if not edges:
            return []
        if node.type in _BROADCAST_NODE_TYPES:
            return list(edges)
        key = node.select_key or "result"
        found, value = self.try_get_by_path_from_scopes(scopes, key)
        return self._select_by_label(value if found else None, edges)

    def build_downstream_inputs(self, upstream: Dict[str, Any], edge: Edge) -> Dict[str, Any]:
        if edge.mapping:
            out: Dict[str, Any] = {}
            for dst, src in edge.mapping.items():
                found, value = self.try_get_by_path(upstream, src)
                if found:
                    out[dst] = value
                elif self.trace:
                    self._log_missing_mapping(edge, dst, src, list(upstream.keys())[:10])
        else:
            out = dict(upstream)
        if edge.const:
            out.update(edge.const)
        return out

    def build_downstream_inputs_from_scopes(
        self,
        scopes: List[Dict[str, Any]],
        edge: Edge,
    ) -> Dict[str, Any]:
        if edge.mapping:
            out: Dict[str, Any] = {}
            for dst, src in edge.mapping.items():
                found, value = self.try_get_by_path_from_scopes(scopes, src)
                if found:
                    out[dst] = value
                elif self.trace:
                    available_keys: list[str] = []
                    for scope in scopes:
                        available_keys.extend(str(key) for key in list(scope.keys())[:10])
                    self._log_missing_mapping(edge, dst, src, available_keys[:10])
        else:
            # Compatibility fallback for opt-in workflows that have not yet
            # enabled strict edge mappings. This preserves legacy input shape,
            # but does not get the memory benefits of vertex-cache dataflow.
            out = {}
            for scope in reversed(scopes):
                out.update(scope)
        if edge.const:
            out.update(edge.const)
        return out

    def try_get_by_path_from_scopes(
        self,
        scopes: List[Dict[str, Any]],
        path: str,
    ) -> tuple[bool, Any]:
        if str(path or "").strip() == "$":
            return True, self._merge_scopes(scopes)
        for scope in scopes:
            found, value = self.try_get_by_path(scope, path)
            if found:
                return True, value
        return False, None

    def _merge_scopes(self, scopes: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for scope in reversed(scopes):
            out.update(scope)
        return out

    def try_get_by_path(self, data: Any, path: str) -> tuple[bool, Any]:
        if path is None:
            return False, None
        path = str(path).strip()
        if not path:
            return False, None
        if path == "$":
            return True, data
        if path.startswith("$."):
            path = path[2:]
        cur: Any = data
        for part in path.split("."):
            if isinstance(cur, dict):
                if part in cur:
                    cur = cur[part]
                else:
                    return False, None
            elif isinstance(cur, list):
                try:
                    idx = int(part)
                except Exception:
                    return False, None
                if 0 <= idx < len(cur):
                    cur = cur[idx]
                else:
                    return False, None
            else:
                return False, None
        return True, cur

    def get_by_path(self, data: Any, path: str) -> Any:
        found, value = self.try_get_by_path(data, path)
        return value if found else None

    def _select_by_label(self, value: Any, edges: List[Edge]) -> List[Edge]:
        selected_label: Optional[str]
        if value is None:
            selected_label = None
        elif isinstance(value, bool):
            selected_label = "true" if value else "false"
        elif isinstance(value, str):
            selected_label = value
        else:
            selected_label = str(value)
        matched = [e for e in edges if e.label == selected_label] if selected_label is not None else []
        if matched:
            return matched
        defaults = [e for e in edges if e.label is None]
        return defaults[:1] if defaults else []

    def _log_missing_mapping(self, edge: Edge, dst: str, src: str, available_keys: list[Any]) -> None:
        _logger.warning(
            "wf.edge.mapping.none_value",
            extra={
                "edge_from": edge.from_,
                "edge_to": edge.to,
                "dst_key": dst,
                "src_path": src,
                "available_keys": available_keys,
                "dataflow_mode": self.dataflow_mode,
            },
        )
