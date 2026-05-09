"""Shared helpers for workflow execution history persistence."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from flocks.session.recorder import Recorder
from flocks.storage.storage import Storage
from flocks.workflow.runner import RunWorkflowResult


def workflow_execution_key(exec_id: str) -> str:
    """Return the storage key for one workflow execution."""
    return f"workflow_execution/{exec_id}"


def normalize_execution_status(status: str) -> str:
    """Map runner status values to API status values."""
    normalized = (status or "").strip().upper()
    if normalized == "SUCCEEDED":
        return "success"
    if normalized == "FAILED":
        return "error"
    if normalized == "TIMED_OUT":
        return "timeout"
    if normalized == "CANCELLED":
        return "cancelled"
    return (status or "error").strip().lower() or "error"


def _extract_business_failure_message(outputs: Dict[str, Any]) -> Optional[str]:
    """Return a user-facing failure reason from workflow outputs."""
    for key in ("reason", "error_message", "errorMessage", "message"):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_execution_outcome(result: RunWorkflowResult) -> tuple[str, Optional[str]]:
    """Resolve API execution status from runner status and workflow outputs."""
    status_value = normalize_execution_status(result.status)
    error_message = result.error

    if status_value != "success" or not isinstance(result.outputs, dict):
        return status_value, error_message

    if result.outputs.get("workflow_success") is False:
        return (
            "error",
            error_message
            or _extract_business_failure_message(result.outputs)
            or "Workflow reported business failure.",
        )

    return status_value, error_message


def build_initial_execution_record(
    workflow_id: str,
    *,
    input_params: Optional[Dict[str, Any]] = None,
    exec_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the initial running execution payload."""
    return {
        "id": exec_id or str(uuid.uuid4()),
        "workflowId": workflow_id,
        "inputParams": input_params or {},
        "status": "running",
        "startedAt": int(time.time() * 1000),
        "executionLog": [],
        "currentPhase": "queued",
        "currentStepIndex": 0,
    }


async def create_execution_record(
    workflow_id: str,
    *,
    input_params: Optional[Dict[str, Any]] = None,
    exec_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and persist a running workflow execution record."""
    exec_data = build_initial_execution_record(
        workflow_id,
        input_params=input_params,
        exec_id=exec_id,
    )
    await Storage.write(workflow_execution_key(exec_data["id"]), exec_data)
    return exec_data


async def record_execution_result(
    workflow_id: str,
    exec_id: str,
    exec_data: Dict[str, Any],
) -> None:
    """Persist the final execution record and audit trail."""
    await Storage.write(workflow_execution_key(exec_id), exec_data)
    try:
        await Recorder.record_workflow_execution(
            exec_id=exec_id,
            workflow_id=workflow_id,
            run_result=exec_data,
        )
    except Exception:
        pass
