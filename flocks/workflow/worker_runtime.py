"""Subprocess runtime for one process-isolated workflow run."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import resource
import signal
import sys
import threading
import time
import traceback
from types import FrameType
from typing import Any, Dict, Optional
import uuid

from flocks.tool import ToolContext
from flocks.workflow.events import workflow_event
from flocks.workflow.runner import RunWorkflowResult, run_workflow
from flocks.workflow.tool_context import build_workflow_tool_context


_cancel_requested = False
_ORIGINAL_STDOUT = sys.stdout
_ABORT_EVENT = asyncio.Event()
_CONTROL_REQUEST_ID = ""
_PENDING_CONTROL: dict[str, concurrent.futures.Future[Dict[str, Any]]] = {}
_PENDING_LOCK = threading.Lock()


def _handle_signal(_signum: int, _frame: Optional[FrameType]) -> None:
    global _cancel_requested
    _cancel_requested = True
    _ABORT_EVENT.set()


def _cancelled() -> bool:
    return _cancel_requested


def _write_event(event: Dict[str, Any], *, max_bytes: Optional[int] = None) -> None:
    encoded = json.dumps(event, ensure_ascii=False, default=str)
    if max_bytes is not None and len(encoded.encode("utf-8")) > max_bytes:
        fallback = workflow_event(
            "run_failed",
            str(event.get("request_id") or ""),
            status="FAILED",
            error=f"ResultTooLarge: event exceeds {max_bytes} bytes",
        )
        encoded = json.dumps(fallback, ensure_ascii=False, default=str)
    print(encoded, file=_ORIGINAL_STDOUT, flush=True)


async def _build_tool_context(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    workspace_dir = str(payload.get("workspace_dir") or "").strip()
    if workspace_dir:
        try:
            os.chdir(workspace_dir)
        except Exception:
            pass
    workflow_id = str(payload.get("workflow_id") or payload.get("id") or "")
    action_name = str(payload.get("action_name") or "run")
    session_id = payload.get("session_id")
    message_id = payload.get("message_id")
    agent = str(payload.get("agent") or "rex")
    try:
        tool_context = await build_workflow_tool_context(
            workflow_id=workflow_id,
            action_name=action_name,
            session_id=session_id,
            message_id=message_id,
            agent=agent,
        )
        _install_control_bridge(tool_context)
        return tool_context
    except Exception:
        return ToolContext(
            session_id=str(session_id or f"workflow-{workflow_id}"),
            message_id=str(message_id or f"workflow-{workflow_id}-{action_name}"),
            agent=agent,
            abort_event=_ABORT_EVENT,
            permission_callback=_permission_callback,
            event_publish_callback=_event_publish_callback,
            extra={
                "workspace_dir": workspace_dir or os.getcwd(),
                "main_session_key": str(session_id or ""),
                "workflowId": workflow_id,
                "workflowAction": action_name,
            },
        )


def _install_control_bridge(tool_context: ToolContext) -> None:
    tool_context._abort_event = _ABORT_EVENT
    tool_context._permission_callback = _permission_callback
    tool_context.event_publish_callback = _event_publish_callback


async def _permission_callback(request: Any) -> None:
    await _request_parent(
        "permission_request",
        permission=str(getattr(request, "permission", "")),
        patterns=list(getattr(request, "patterns", []) or []),
        always=list(getattr(request, "always", []) or []),
        metadata=getattr(request, "metadata", {}) if isinstance(getattr(request, "metadata", {}), dict) else {},
    )


async def _event_publish_callback(event_name: str, payload: Dict[str, Any]) -> None:
    await _request_parent(
        "event_publish",
        event_name=str(event_name or ""),
        payload=payload if isinstance(payload, dict) else {},
    )


async def _request_parent(event_type: str, **payload: Any) -> None:
    control_id = uuid.uuid4().hex
    future: concurrent.futures.Future[Dict[str, Any]] = concurrent.futures.Future()
    with _PENDING_LOCK:
        _PENDING_CONTROL[control_id] = future
    try:
        _write_event(
            workflow_event(
                event_type,
                _CONTROL_REQUEST_ID,
                control_id=control_id,
                **payload,
            )
        )
        response = await asyncio.wrap_future(future)
        if not response.get("ok", False):
            raise RuntimeError(str(response.get("error") or f"{event_type} rejected"))
    finally:
        with _PENDING_LOCK:
            _PENDING_CONTROL.pop(control_id, None)


def _start_control_reader() -> None:
    thread = threading.Thread(target=_read_control_responses, name="workflow-control-reader", daemon=True)
    thread.start()


def _read_control_responses() -> None:
    while True:
        raw = sys.stdin.readline()
        if not raw:
            return
        try:
            message = json.loads(raw)
        except Exception:
            continue
        if not isinstance(message, dict):
            continue
        control_id = str(message.get("control_id") or "")
        if not control_id:
            continue
        with _PENDING_LOCK:
            future = _PENDING_CONTROL.get(control_id)
        if future is None or future.done():
            continue
        future.set_result(message)


async def _run(request: Dict[str, Any]) -> None:
    global _CONTROL_REQUEST_ID
    request_id = str(request.get("request_id") or "")
    _CONTROL_REQUEST_ID = request_id
    _start_control_reader()
    limits = request.get("limits") if isinstance(request.get("limits"), dict) else {}
    result_max_bytes = int(limits.get("result_max_bytes") or 8 * 1024 * 1024)
    soft_memory_budget_mb = int(limits.get("soft_memory_budget_mb") or 0)

    def _check_soft_memory_budget() -> None:
        if soft_memory_budget_mb <= 0:
            return
        rss_mb = _current_rss_mb()
        if rss_mb is not None and rss_mb > soft_memory_budget_mb:
            raise MemoryError(f"SoftMemoryBudgetExceeded: worker rss {rss_mb:.1f}MB exceeded {soft_memory_budget_mb}MB")

    _write_event(
        workflow_event(
            "run_started",
            request_id,
            workflow_id=request.get("workflow_id"),
            started_at=int(time.time() * 1000),
        )
    )

    def _on_step_start(run_id: Optional[str], step: int, node: Any, inputs: Dict[str, Any]) -> int:
        _check_soft_memory_budget()
        _write_event(
            workflow_event(
                "step_started",
                request_id,
                run_id=run_id,
                step=step,
                node={"id": getattr(node, "id", None), "type": getattr(node, "type", None)},
                inputs=inputs if isinstance(inputs, dict) else {},
            ),
            max_bytes=result_max_bytes,
        )
        return step

    def _on_step_complete(step_result: Any) -> None:
        _check_soft_memory_budget()
        step_payload = step_result.model_dump(mode="json") if hasattr(step_result, "model_dump") else step_result
        _write_event(
            workflow_event(
                "step_completed",
                request_id,
                step_result=step_payload if isinstance(step_payload, dict) else {},
            ),
            max_bytes=result_max_bytes,
        )

    try:
        tool_context = await _build_tool_context(request.get("tool_context"))
        _check_soft_memory_budget()
        kwargs: Dict[str, Any] = {
            "workflow": request.get("workflow") or {},
            "inputs": request.get("inputs") or {},
            "timeout_s": request.get("timeout_s"),
            "trace": bool(request.get("trace")),
            "ensure_requirements": bool(request.get("ensure_requirements", True)),
            "history_mode": str(request.get("history_mode") or "summary"),
            "retain_history": bool(request.get("retain_history", False)),
            "on_step_start": _on_step_start,
            "on_step_complete": _on_step_complete,
            "cancel": _cancelled,
            "tool_context": tool_context,
        }
        if request.get("use_llm") is not None:
            kwargs["use_llm"] = bool(request.get("use_llm"))

        with contextlib.redirect_stdout(sys.stderr):
            result: RunWorkflowResult = run_workflow(**kwargs)
        _check_soft_memory_budget()
        event_type = "run_cancelled" if result.status == "CANCELLED" else "run_finished"
        _write_event(
            workflow_event(
                event_type,
                request_id,
                result={
                    "status": result.status,
                    "run_id": result.run_id,
                    "steps": result.steps,
                    "last_node_id": result.last_node_id,
                    "outputs": result.outputs,
                    "history": result.history,
                    "error": result.error,
                },
            ),
            max_bytes=result_max_bytes,
        )
    except Exception as exc:
        _write_event(
            workflow_event(
                "run_failed",
                request_id,
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            ),
            max_bytes=result_max_bytes,
        )


def _current_rss_mb() -> Optional[float]:
    try:
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if sys.platform == "darwin":
        return rss / 1024 / 1024
    return rss / 1024


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    raw = sys.stdin.readline()
    if not raw:
        return 2
    try:
        request = json.loads(raw)
    except Exception as exc:
        _write_event(workflow_event("run_failed", "", status="FAILED", error=f"InvalidRequest: {exc}"))
        return 2
    asyncio.run(_run(request if isinstance(request, dict) else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
