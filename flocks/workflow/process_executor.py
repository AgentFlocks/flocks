"""Process-based workflow executor."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Optional

from flocks.tool import PermissionRequest
from flocks.workflow.events import WorkflowWorkerLimits, WorkflowWorkerRequest
from flocks.workflow.io import load_workflow
from flocks.workflow.models import Workflow
from flocks.workflow.runner import RunWorkflowResult


StepStartHook = Callable[[Optional[str], int, Any, Dict[str, Any]], Any]
StepCompleteHook = Callable[[Any], Any]
WorkerEventHook = Callable[[Dict[str, Any]], Awaitable[None] | None]


class StepResultEvent(dict):
    """Dict-compatible step result with the model_dump API used by old callbacks."""

    def model_dump(self, mode: str = "json", **_: Any) -> Dict[str, Any]:
        del mode
        return dict(self)


@dataclass
class ProcessWorkflowExecutor:
    limits: WorkflowWorkerLimits = field(default_factory=WorkflowWorkerLimits)

    async def run(
        self,
        *,
        workflow: Any,
        inputs: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        trace: bool = False,
        use_llm: Optional[bool] = None,
        ensure_requirements: bool = True,
        history_mode: str = "summary",
        retain_history: bool = False,
        tool_context: Optional[Any] = None,
        cancel: Optional[Callable[[], bool]] = None,
        on_step_start: Optional[StepStartHook] = None,
        on_step_complete: Optional[StepCompleteHook] = None,
        on_event: Optional[WorkerEventHook] = None,
    ) -> RunWorkflowResult:
        workflow_payload = _workflow_to_payload(workflow)
        resolved_workflow_id = str(
            workflow_id or workflow_payload.get("id") or workflow_payload.get("name") or "workflow"
        )
        request_id = uuid.uuid4().hex
        request = WorkflowWorkerRequest(
            request_id=request_id,
            workflow_id=resolved_workflow_id,
            workflow=workflow_payload,
            inputs=inputs or {},
            timeout_s=timeout_s,
            trace=trace,
            use_llm=use_llm,
            history_mode=history_mode,
            retain_history=retain_history,
            ensure_requirements=ensure_requirements,
            tool_context=_serialize_tool_context(tool_context, workflow_id=resolved_workflow_id),
            limits=self.limits,
        )
        return await self._run_request(
            request,
            parent_tool_context=tool_context,
            cancel=cancel,
            on_step_start=on_step_start,
            on_step_complete=on_step_complete,
            on_event=on_event,
        )

    async def _run_request(
        self,
        request: WorkflowWorkerRequest,
        *,
        parent_tool_context: Optional[Any],
        cancel: Optional[Callable[[], bool]],
        on_step_start: Optional[StepStartHook],
        on_step_complete: Optional[StepCompleteHook],
        on_event: Optional[WorkerEventHook],
    ) -> RunWorkflowResult:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "flocks.workflow.worker_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write((json.dumps(request.to_dict(), ensure_ascii=False, default=str) + "\n").encode())
        await proc.stdin.drain()

        stderr_task = asyncio.create_task(
            _drain_stderr(proc, max_bytes=request.limits.stdout_max_bytes),
            name=f"wf-worker-stderr-{request.request_id}",
        )
        started = time.monotonic()
        last_rss_check = 0.0
        final_result: Optional[RunWorkflowResult] = None
        killed_reason: Optional[str] = None

        try:
            while True:
                if cancel is not None and _safe_bool(cancel):
                    killed_reason = "RunCancelledError: cancellation requested"
                    _terminate(proc)
                if request.timeout_s and time.monotonic() - started > float(request.timeout_s):
                    killed_reason = f"RunTimeoutError: timeout_s={request.timeout_s}"
                    _terminate(proc)
                now = time.monotonic()
                if now - last_rss_check >= 1.0:
                    last_rss_check = now
                    rss = await _worker_rss_bytes(proc.pid)
                    memory_limit = max(int(request.limits.memory_limit_mb), 0) * 1024 * 1024
                    if memory_limit and rss is not None and rss > memory_limit:
                        killed_reason = (
                            f"MemoryLimitExceeded: worker rss {rss} exceeded {request.limits.memory_limit_mb}MB"
                        )
                        _terminate(proc)

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.2)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue
                if not line:
                    if proc.returncode is not None:
                        break
                    continue
                event = _parse_event(line)
                if not event:
                    continue
                await _dispatch_event(
                    event,
                    proc=proc,
                    parent_tool_context=parent_tool_context,
                    on_event=on_event,
                    on_step_start=on_step_start,
                    on_step_complete=on_step_complete,
                )
                final_result = _result_from_event(event) or final_result
                if final_result is not None and event.get("type") in {"run_finished", "run_failed", "run_cancelled"}:
                    break

            if killed_reason is not None:
                await _kill_after_grace(proc, request.limits.cancel_grace_s)
                if "cancel" in killed_reason.lower():
                    status = "CANCELLED"
                elif "timeout" in killed_reason.lower():
                    status = "TIMED_OUT"
                else:
                    status = "FAILED"
                return RunWorkflowResult(status=status, error=killed_reason)
            await proc.wait()
            if final_result is not None:
                return final_result
            stderr = await stderr_task
            return RunWorkflowResult(
                status="FAILED",
                error=f"WorkerCrashed: exit_code={proc.returncode} stderr={stderr[-1000:]}",
            )
        finally:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            if proc.returncode is None:
                _terminate(proc)
                await _kill_after_grace(proc, request.limits.cancel_grace_s)
            if not stderr_task.done():
                stderr_task.cancel()


async def run_workflow_process(
    *,
    workflow: Any,
    inputs: Optional[Dict[str, Any]] = None,
    timeout_s: Optional[float] = None,
    trace: bool = False,
    use_llm: Optional[bool] = None,
    ensure_requirements: bool = True,
    history_mode: str = "summary",
    retain_history: bool = False,
    tool_context: Optional[Any] = None,
    cancel: Optional[Callable[[], bool]] = None,
    on_step_start: Optional[StepStartHook] = None,
    on_step_complete: Optional[StepCompleteHook] = None,
    on_event: Optional[WorkerEventHook] = None,
    **_: Any,
) -> RunWorkflowResult:
    executor = ProcessWorkflowExecutor()
    workflow_payload = _workflow_to_payload(workflow)
    return await executor.run(
        workflow=workflow_payload,
        inputs=inputs,
        workflow_id=str(workflow_payload.get("id") or workflow_payload.get("name") or "workflow"),
        timeout_s=timeout_s,
        trace=trace,
        use_llm=use_llm,
        ensure_requirements=ensure_requirements,
        history_mode=history_mode,
        retain_history=retain_history,
        tool_context=tool_context,
        cancel=cancel,
        on_step_start=on_step_start,
        on_step_complete=on_step_complete,
        on_event=on_event,
    )


def _workflow_to_payload(workflow: Any) -> Dict[str, Any]:
    if isinstance(workflow, Workflow):
        return workflow.to_dict()
    if isinstance(workflow, dict):
        return workflow
    if isinstance(workflow, (str, Path)):
        return load_workflow(Path(workflow)).to_dict()
    if hasattr(workflow, "to_dict"):
        payload = workflow.to_dict()
        if isinstance(payload, dict):
            return payload
    raise TypeError("workflow must be a dict, Workflow model, or workflow file path")


def _serialize_tool_context(tool_context: Any, *, workflow_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if tool_context is None:
        return None
    extra = getattr(tool_context, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
    return {
        "workflow_id": workflow_id,
        "session_id": getattr(tool_context, "session_id", None),
        "message_id": getattr(tool_context, "message_id", None),
        "agent": getattr(tool_context, "agent", None),
        "workspace_dir": extra.get("workspace_dir") or os.getcwd(),
        "action_name": extra.get("workflowAction") or "run",
    }


async def _dispatch_event(
    event: Dict[str, Any],
    *,
    proc: asyncio.subprocess.Process,
    parent_tool_context: Optional[Any],
    on_event: Optional[WorkerEventHook],
    on_step_start: Optional[StepStartHook],
    on_step_complete: Optional[StepCompleteHook],
) -> None:
    event_type = event.get("type")
    if event_type == "permission_request":
        await _handle_permission_request(proc, event, parent_tool_context)
        return
    if event_type == "event_publish":
        await _handle_event_publish(proc, event, parent_tool_context)
        return

    if on_event is not None:
        maybe = on_event(event)
        if hasattr(maybe, "__await__"):
            await maybe  # type: ignore[misc]
    if event_type == "step_started" and on_step_start is not None:
        node_data = event.get("node") if isinstance(event.get("node"), dict) else {}
        node = SimpleNamespace(
            id=node_data.get("id"),
            type=node_data.get("type"),
        )
        await asyncio.to_thread(
            on_step_start,
            event.get("run_id"),
            int(event.get("step") or 0),
            node,
            event.get("inputs") or {},
        )
    elif event_type == "step_completed" and on_step_complete is not None:
        step_result = event.get("step_result") if isinstance(event.get("step_result"), dict) else {}
        await asyncio.to_thread(on_step_complete, StepResultEvent(step_result))


async def _handle_permission_request(
    proc: asyncio.subprocess.Process,
    event: Dict[str, Any],
    parent_tool_context: Optional[Any],
) -> None:
    control_id = str(event.get("control_id") or "")
    callback = getattr(parent_tool_context, "_permission_callback", None)
    try:
        if callback is not None:
            request = PermissionRequest(
                permission=str(event.get("permission") or ""),
                patterns=list(event.get("patterns") or []),
                always=list(event.get("always") or []),
                metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
            )
            maybe = callback(request)
            if hasattr(maybe, "__await__"):
                await maybe
        await _send_control_response(proc, "permission_response", control_id, ok=True)
    except Exception as exc:
        await _send_control_response(
            proc,
            "permission_response",
            control_id,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _handle_event_publish(
    proc: asyncio.subprocess.Process,
    event: Dict[str, Any],
    parent_tool_context: Optional[Any],
) -> None:
    control_id = str(event.get("control_id") or "")
    callback = getattr(parent_tool_context, "event_publish_callback", None)
    try:
        if callback is not None:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            maybe = callback(str(event.get("event_name") or ""), payload)
            if hasattr(maybe, "__await__"):
                await maybe
        await _send_control_response(proc, "event_publish_response", control_id, ok=True)
    except Exception as exc:
        await _send_control_response(
            proc,
            "event_publish_response",
            control_id,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _send_control_response(
    proc: asyncio.subprocess.Process,
    response_type: str,
    control_id: str,
    *,
    ok: bool,
    error: Optional[str] = None,
) -> None:
    if proc.stdin is None or proc.stdin.is_closing() or proc.returncode is not None:
        return
    message = {"type": response_type, "control_id": control_id, "ok": ok}
    if error:
        message["error"] = error
    try:
        proc.stdin.write((json.dumps(message, ensure_ascii=False, default=str) + "\n").encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        return


def _result_from_event(event: Dict[str, Any]) -> Optional[RunWorkflowResult]:
    event_type = event.get("type")
    if event_type in {"run_finished", "run_cancelled"}:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        return RunWorkflowResult(
            status=str(result.get("status") or ("CANCELLED" if event_type == "run_cancelled" else "FAILED")),
            run_id=result.get("run_id"),
            steps=int(result.get("steps") or 0),
            last_node_id=result.get("last_node_id"),
            outputs=result.get("outputs") if isinstance(result.get("outputs"), dict) else {},
            history=result.get("history") if isinstance(result.get("history"), list) else [],
            error=result.get("error"),
        )
    if event_type == "run_failed":
        return RunWorkflowResult(
            status=str(event.get("status") or "FAILED"),
            error=str(event.get("error") or "Worker failed"),
        )
    return None


def _parse_event(line: bytes) -> Optional[Dict[str, Any]]:
    try:
        event = json.loads(line.decode("utf-8"))
    except Exception:
        return None
    return event if isinstance(event, dict) else None


async def _drain_stderr(proc: asyncio.subprocess.Process, *, max_bytes: int) -> str:
    if proc.stderr is None:
        return ""
    chunks: list[str] = []
    max_capture = max(int(max_bytes or 0), 0) or 64 * 1024
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        chunks.append(line.decode("utf-8", errors="replace"))
        if sum(len(chunk.encode("utf-8")) for chunk in chunks) > max_capture:
            chunks = ["".join(chunks)[-max_capture:]]
    return "".join(chunks)


async def _worker_rss_bytes(pid: Optional[int]) -> Optional[int]:
    if not pid:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            "-o",
            "rss=",
            "-p",
            str(pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode().strip()
        if not text:
            return None
        return int(text.splitlines()[-1].strip()) * 1024
    except Exception:
        return None


def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return


async def _kill_after_grace(proc: asyncio.subprocess.Process, grace_s: float) -> None:
    if proc.returncode is not None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=max(grace_s, 0.1))
        return
    except asyncio.TimeoutError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    await proc.wait()


def _safe_bool(fn: Callable[[], bool]) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False
