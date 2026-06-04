"""Unified trigger event mapping, filtering, and dispatch helpers."""

from __future__ import annotations

import ast
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .models import TriggerDefinition, TriggerEvent, TriggerEventSource

DispatchExecutor = Callable[[Dict[str, Any]], Awaitable[Any]]


class TriggerDispatchError(Exception):
    """Raised when a trigger event cannot be dispatched."""


class TriggerExpressionEvaluator(ast.NodeVisitor):
    """Very small safe evaluator for trigger filter expressions."""

    def __init__(self, variables: Dict[str, Any]) -> None:
        self._variables = variables

    def visit_Expression(self, node: ast.Expression) -> Any:  # noqa: N802
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:  # noqa: N802
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:  # noqa: N802
        if node.id not in self._variables:
            raise TriggerDispatchError(f"Unknown name in trigger filter: {node.id}")
        return self._variables[node.id]

    def visit_List(self, node: ast.List) -> Any:  # noqa: N802
        return [self.visit(elt) for elt in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> Any:  # noqa: N802
        return tuple(self.visit(elt) for elt in node.elts)

    def visit_Dict(self, node: ast.Dict) -> Any:  # noqa: N802
        return {self.visit(key): self.visit(value) for key, value in zip(node.keys, node.values)}

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:  # noqa: N802
        if isinstance(node.op, ast.And):
            return all(self.visit(value) for value in node.values)
        if isinstance(node.op, ast.Or):
            return any(self.visit(value) for value in node.values)
        raise TriggerDispatchError("Unsupported boolean operator in trigger filter")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:  # noqa: N802
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not operand
        raise TriggerDispatchError("Unsupported unary operator in trigger filter")

    def visit_Compare(self, node: ast.Compare) -> Any:  # noqa: N802
        left = self.visit(node.left)
        for operator, comparator_node in zip(node.ops, node.comparators):
            right = self.visit(comparator_node)
            if isinstance(operator, ast.Eq):
                ok = left == right
            elif isinstance(operator, ast.NotEq):
                ok = left != right
            elif isinstance(operator, ast.In):
                ok = left in right
            elif isinstance(operator, ast.NotIn):
                ok = left not in right
            elif isinstance(operator, ast.Gt):
                ok = left > right
            elif isinstance(operator, ast.GtE):
                ok = left >= right
            elif isinstance(operator, ast.Lt):
                ok = left < right
            elif isinstance(operator, ast.LtE):
                ok = left <= right
            else:
                raise TriggerDispatchError("Unsupported compare operator in trigger filter")
            if not ok:
                return False
            left = right
        return True

    def visit_Attribute(self, node: ast.Attribute) -> Any:  # noqa: N802
        value = self.visit(node.value)
        if isinstance(value, dict):
            return value.get(node.attr)
        return getattr(value, node.attr, None)

    def visit_Subscript(self, node: ast.Subscript) -> Any:  # noqa: N802
        value = self.visit(node.value)
        key = self.visit(node.slice)
        try:
            return value[key]
        except Exception as exc:  # pragma: no cover - defensive branch
            raise TriggerDispatchError(f"Invalid trigger filter subscript access: {exc}") from exc

    def generic_visit(self, node: ast.AST) -> Any:  # noqa: D401
        raise TriggerDispatchError(f"Unsupported syntax in trigger filter: {type(node).__name__}")


def _tokenize_path(path: str) -> List[Any]:
    tokens: List[Any] = []
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            i += 1
            continue
        if ch == "[":
            end = path.find("]", i)
            if end < 0:
                raise TriggerDispatchError(f"Invalid mapping path: {path}")
            raw = path[i + 1 : end].strip()
            if raw.isdigit():
                tokens.append(int(raw))
            else:
                tokens.append(raw.strip("'\""))
            i = end + 1
            continue
        start = i
        while i < len(path) and path[i] not in ".[":
            i += 1
        tokens.append(path[start:i])
    return [token for token in tokens if token not in ("$", "")]


def lookup_mapping_path(data: Any, path: str) -> Any:
    raw = (path or "").strip()
    if raw in {"", "$"}:
        return data
    candidate = raw[2:] if raw.startswith("$.") else raw
    value = data
    for token in _tokenize_path(candidate):
        if isinstance(token, int):
            if not isinstance(value, list):
                return None
            if token < 0 or token >= len(value):
                return None
            value = value[token]
            continue
        if isinstance(value, dict):
            value = value.get(token)
        else:
            value = getattr(value, token, None)
        if value is None:
            return None
    return value


def build_trigger_event(
    *,
    workflow_id: str,
    trigger: TriggerDefinition,
    body: Any = None,
    headers: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    path_params: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    raw: Any = None,
    delivery_id: Optional[str] = None,
) -> TriggerEvent:
    resolved_source = source
    if not resolved_source:
        src = trigger.source or {}
        if isinstance(src, dict):
            resolved_source = (
                src.get("path")
                or src.get("topic")
                or src.get("event")
                or src.get("adapterId")
                or trigger.type
            )
    return TriggerEvent(
        source=TriggerEventSource(
            workflowId=workflow_id,
            triggerId=trigger.id or "",
            triggerType=trigger.type,
            source=str(resolved_source or trigger.type),
            deliveryId=delivery_id or uuid.uuid4().hex,
            receivedAt=int(time.time() * 1000),
        ),
        body=body,
        headers=headers or {},
        query=query or {},
        pathParams=path_params or {},
        payload=body,
        raw=raw if raw is not None else body,
    )


def event_to_context(event: TriggerEvent) -> Dict[str, Any]:
    payload = event.model_dump(mode="json", exclude_none=True)
    return {
        "event": payload,
        "body": payload.get("body"),
        "headers": payload.get("headers") or {},
        "query": payload.get("query") or {},
        "pathParams": payload.get("pathParams") or {},
        "payload": payload.get("payload"),
        "raw": payload.get("raw"),
    }


def evaluate_trigger_filter(trigger: TriggerDefinition, event: TriggerEvent) -> Tuple[bool, Optional[str]]:
    filter_spec = trigger.filter
    if filter_spec is None:
        return True, None
    expr = (filter_spec.expr or "").strip()
    if not expr:
        return True, None
    ctx = event_to_context(event)
    try:
        parsed = ast.parse(expr, mode="eval")
        matched = bool(TriggerExpressionEvaluator(ctx).visit(parsed))
    except Exception as exc:
        return False, str(exc)
    return matched, None


def preview_trigger_mapping(trigger: TriggerDefinition, event: TriggerEvent) -> Dict[str, Any]:
    ctx = event_to_context(event)
    mapped: Dict[str, Any] = dict(trigger.inputs or {})
    for dst_key, src_path in (trigger.mapping or {}).items():
        mapped[dst_key] = lookup_mapping_path(ctx, src_path)
    mapped["_flocks"] = {
        "trigger": {
            "id": event.source.triggerId,
            "type": event.source.triggerType,
            "source": event.source.source,
            "deliveryId": event.source.deliveryId,
            "receivedAt": event.source.receivedAt,
            "attempt": event.source.attempt,
        }
    }
    mapped.setdefault("_trigger", trigger.type)
    return mapped


class EventDispatcher:
    """Dispatch trigger events through filtering and mapping."""

    async def dispatch(
        self,
        *,
        trigger: TriggerDefinition,
        event: TriggerEvent,
        executor: DispatchExecutor,
    ) -> Dict[str, Any]:
        matched, filter_error = evaluate_trigger_filter(trigger, event)
        mapped_inputs = preview_trigger_mapping(trigger, event)
        if filter_error:
            raise TriggerDispatchError(filter_error)
        if not matched:
            return {
                "matched": False,
                "inputs": mapped_inputs,
                "executed": False,
            }
        result = await executor(mapped_inputs)
        return {
            "matched": True,
            "inputs": mapped_inputs,
            "executed": True,
            "result": result,
        }

