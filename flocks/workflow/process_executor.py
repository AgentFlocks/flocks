"""Process-based workflow executor."""

from __future__ import annotations

import asyncio
import errno
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
_SERIALIZED_EXTRA_KEYS = frozenset(
    {
        "main_session_key",
        "project_id",
        "projectId",
        "sandbox",
        "sandbox_elevated",
        "workflowAction",
        "workflowId",
        "workspace_dir",
    }
)
_SERIALIZED_EXTRA_MAX_BYTES = 64 * 1024


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
            limit=_subprocess_stream_limit(request.limits),
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        stderr_task = asyncio.create_task(
            _drain_stderr(proc, max_bytes=request.limits.stdout_max_bytes),
            name=f"wf-worker-stderr-{request.request_id}",
        )
        try:
            await _write_stdin_message(proc, request.to_dict())
        except OSError as exc:
            if not _is_pipe_closed_error(exc):
                raise
            await _wait_for_process_exit(proc, timeout_s=1.0)
            stderr = await stderr_task
            return RunWorkflowResult(
                status="FAILED",
                error=f"WorkerPipeClosed: {exc} stderr={stderr[-1000:]}",
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
                except OSError as exc:
                    if not _is_pipe_closed_error(exc):
                        raise
                    break
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
                try:
                    proc.stdin.close()
                except OSError as exc:
                    if not _is_pipe_closed_error(exc):
                        raise
            if proc.returncode is None:
                _terminate(proc)
                await _kill_after_grace(proc, request.limits.cancel_grace_s)
            if not stderr_task.done():
                stderr_task.cancel()


async def run_workflow_process(
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
    **_: Any,
) -> RunWorkflowResult:
    executor = ProcessWorkflowExecutor()
    workflow_payload = _workflow_to_payload(workflow)
    workflow_id_value = str(workflow_id or workflow_payload.get("id") or workflow_payload.get("name") or "workflow")
    return await executor.run(
        workflow=workflow_payload,
        inputs=inputs,
        workflow_id=workflow_id_value,
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


def _serialize_tool_context(tool_context: Any, *, workflow_id: Optional[str]) -> Dict[str, Any]:
    if tool_context is None:
        workspace_dir = os.getcwd()
        return {
            "workflow_id": workflow_id,
            "session_id": None,
            "message_id": None,
            "agent": None,
            "workspace_dir": workspace_dir,
            "action_name": "run",
            "extra": {
                "workspace_dir": workspace_dir,
                "workflowId": workflow_id,
                "workflowAction": "run",
            },
        }
    extra = getattr(tool_context, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
    serialized_extra = _serialize_context_extra(extra)
    workspace_dir = serialized_extra.get("workspace_dir") or extra.get("workspace_dir") or os.getcwd()
    action_name = serialized_extra.get("workflowAction") or extra.get("workflowAction") or "run"
    serialized_extra.setdefault("workspace_dir", workspace_dir)
    serialized_extra.setdefault("workflowId", workflow_id)
    serialized_extra.setdefault("workflowAction", action_name)
    return {
        "workflow_id": workflow_id,
        "session_id": getattr(tool_context, "session_id", None),
        "message_id": getattr(tool_context, "message_id", None),
        "agent": getattr(tool_context, "agent", None),
        "workspace_dir": workspace_dir,
        "action_name": action_name,
        "call_id": getattr(tool_context, "call_id", None),
        "extra": serialized_extra,
    }


def _serialize_context_extra(extra: Dict[str, Any]) -> Dict[str, Any]:
    serialized: Dict[str, Any] = {}
    for key in _SERIALIZED_EXTRA_KEYS:
        if key not in extra:
            continue
        value = _json_safe_value(extra[key])
        if value is not _UNSERIALIZABLE:
            serialized[key] = value

    try:
        encoded = json.dumps(serialized, ensure_ascii=False, default=str).encode("utf-8")
    except Exception:
        return {}
    if len(encoded) <= _SERIALIZED_EXTRA_MAX_BYTES:
        return serialized

    # Keep the minimum context needed for deterministic workspace/session
    # resolution when optional payloads such as sandbox env are too large.
    return {
        key: value
        for key, value in serialized.items()
        if key in {"workspace_dir", "main_session_key", "workflowId", "workflowAction"}
    }


_UNSERIALIZABLE = object()


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            safe_item = _json_safe_value(item)
            if safe_item is not _UNSERIALIZABLE:
                result[key] = safe_item
        return result
    if isinstance(value, (list, tuple)):
        result_list: list[Any] = []
        for item in value:
            safe_item = _json_safe_value(item)
            if safe_item is not _UNSERIALIZABLE:
                result_list.append(safe_item)
        return result_list
    return _UNSERIALIZABLE


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
        await _write_stdin_message(proc, message)
    except OSError as exc:
        if not _is_pipe_closed_error(exc):
            raise
        return


async def _write_stdin_message(proc: asyncio.subprocess.Process, message: Dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    proc.stdin.write(_encode_stdin_message(message))
    await proc.stdin.drain()


def _encode_stdin_message(message: Dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
    return f"Content-Length: {len(payload)}\n\n".encode("ascii") + payload


def _is_pipe_closed_error(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
        return True
    if not isinstance(exc, OSError):
        return False
    return getattr(exc, "errno", None) in {errno.EPIPE, errno.ECONNRESET} or getattr(exc, "winerror", None) == 109


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


def _subprocess_stream_limit(limits: WorkflowWorkerLimits) -> int:
    max_event_bytes = max(
        int(limits.result_max_bytes or 0),
        int(limits.stdout_max_bytes or 0),
        64 * 1024,
    )
    return max_event_bytes + 64 * 1024


async def _drain_stderr(proc: asyncio.subprocess.Process, *, max_bytes: int) -> str:
    if proc.stderr is None:
        return ""
    chunks: list[str] = []
    max_capture = max(int(max_bytes or 0), 0) or 64 * 1024
    while True:
        try:
            line = await proc.stderr.readline()
        except OSError as exc:
            if _is_pipe_closed_error(exc):
                break
            raise
        if not line:
            break
        chunks.append(line.decode("utf-8", errors="replace"))
        if sum(len(chunk.encode("utf-8")) for chunk in chunks) > max_capture:
            chunks = ["".join(chunks)[-max_capture:]]
    return "".join(chunks)


async def _worker_rss_bytes(pid: Optional[int]) -> Optional[int]:
    if not pid:
        return None
    if sys.platform == "win32":
        return _windows_process_rss_bytes(pid)
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


def _windows_process_rss_bytes(pid: int) -> Optional[int]:
    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        process_query_information = 0x0400
        process_vm_read = 0x0010
        process = ctypes.windll.kernel32.OpenProcess(
            process_query_information | process_vm_read,
            False,
            int(pid),
        )
        if not process:
            return None
        try:
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
            if not ctypes.windll.psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            ):
                return None
            return int(counters.WorkingSetSize)
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
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
