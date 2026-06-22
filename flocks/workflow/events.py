"""JSONL event helpers for process-isolated workflow execution."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
from typing import Any, Dict, Literal, Optional


WorkflowWorkerEventType = Literal[
    "run_started",
    "step_started",
    "step_completed",
    "run_finished",
    "run_failed",
    "run_cancelled",
    "worker_heartbeat",
    "permission_request",
    "event_publish",
]


@dataclass
class WorkflowWorkerLimits:
    memory_limit_mb: int = field(default_factory=lambda: _default_memory_limit_mb())
    soft_memory_budget_mb: int = 0
    stdout_max_bytes: int = 1024 * 1024
    result_max_bytes: int = 8 * 1024 * 1024
    cancel_grace_s: float = 2.0

    def __post_init__(self) -> None:
        if self.soft_memory_budget_mb <= 0 and self.memory_limit_mb > 0:
            self.soft_memory_budget_mb = max(int(self.memory_limit_mb * 0.75), 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_limit_mb": self.memory_limit_mb,
            "soft_memory_budget_mb": self.soft_memory_budget_mb,
            "stdout_max_bytes": self.stdout_max_bytes,
            "result_max_bytes": self.result_max_bytes,
            "cancel_grace_s": self.cancel_grace_s,
        }


def _default_memory_limit_mb() -> int:
    total_mb = _detect_total_memory_mb()
    if total_mb <= 0:
        return 1024
    return max(int(total_mb * 0.8), 1)


def _detect_total_memory_mb() -> int:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
        total_bytes = page_size * page_count
        if total_bytes > 0:
            return total_bytes // (1024 * 1024)
    except (AttributeError, OSError, ValueError):
        pass
    return _detect_windows_total_memory_mb()


def _detect_windows_total_memory_mb() -> int:
    try:

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0
        return int(status.ullTotalPhys) // (1024 * 1024)
    except (AttributeError, OSError, ValueError):
        return 0


@dataclass
class WorkflowWorkerRequest:
    request_id: str
    workflow_id: str
    workflow: Dict[str, Any]
    inputs: Dict[str, Any] = field(default_factory=dict)
    timeout_s: Optional[float] = None
    trace: bool = False
    use_llm: Optional[bool] = None
    history_mode: str = "summary"
    retain_history: bool = False
    ensure_requirements: bool = True
    tool_context: Optional[Dict[str, Any]] = None
    limits: WorkflowWorkerLimits = field(default_factory=WorkflowWorkerLimits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workflow_id": self.workflow_id,
            "workflow": self.workflow,
            "inputs": self.inputs,
            "timeout_s": self.timeout_s,
            "trace": self.trace,
            "use_llm": self.use_llm,
            "history_mode": self.history_mode,
            "retain_history": self.retain_history,
            "ensure_requirements": self.ensure_requirements,
            "tool_context": self.tool_context,
            "limits": self.limits.to_dict(),
        }


def workflow_event(
    event_type: WorkflowWorkerEventType,
    request_id: str,
    **payload: Any,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {"type": event_type, "request_id": request_id}
    event.update(payload)
    return event
