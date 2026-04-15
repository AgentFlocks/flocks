"""
Workflow management routes

Provides API endpoints for workflow CRUD, execution, history, and AI generation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Any, Dict, Literal
from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field, ConfigDict
import uuid

from flocks.workflow.models import Workflow, Node, Edge
from flocks.workflow.runner import run_workflow, RunWorkflowResult
from flocks.workflow.center import (
    WorkflowCenterError,
    WorkflowNotFoundError,
    WorkflowNotPublishedError,
    get_workflow_health,
    invoke_published_workflow,
    list_registry_entries,
    list_workflow_releases,
    publish_workflow,
    scan_skill_workflows,
    stop_workflow_service,
)
from flocks.session.recorder import Recorder
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.workflow.workflow_lint import lint_workflow
from flocks.workflow.compiler import compile_workflow
from flocks.workflow.fs_store import (
    find_workspace_root as _find_workspace_root,
    read_workflow_dir as _read_workflow_dir,
    read_workflow_from_fs as shared_read_workflow_from_fs,
    workflow_scan_dirs as _all_scan_dirs,
)
from flocks.workflow.io import load_workflow, dump_workflow
from flocks.workflow.tools import get_tool_registry
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.server.routes.event import publish_event
from flocks.tool import ToolContext
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="workflow-routes")


@dataclass
class ActiveWorkflowExecution:
    """Tracks an in-flight workflow execution that can be cancelled."""
    workflow_id: str
    task: asyncio.Task[Any]
    cancel_event: threading.Event


_active_workflow_executions: Dict[str, ActiveWorkflowExecution] = {}


# =============================================================================
# Request/Response Models
# =============================================================================

class WorkflowCreateRequest(BaseModel):
    """Request to create a workflow"""
    model_config = ConfigDict(populate_by_name=True)
    
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Workflow description")
    category: Optional[str] = Field("default", description="Workflow category")
    workflow_json: Dict[str, Any] = Field(..., alias="workflowJson", description="Workflow JSON definition")
    created_by: Optional[str] = Field(None, alias="createdBy", description="Creator")


class WorkflowUpdateRequest(BaseModel):
    """Request to update a workflow"""
    model_config = ConfigDict(populate_by_name=True)
    
    name: Optional[str] = Field(None, description="Workflow name")
    description: Optional[str] = Field(None, description="Workflow description")
    category: Optional[str] = Field(None, description="Workflow category")
    workflow_json: Optional[Dict[str, Any]] = Field(None, alias="workflowJson", description="Workflow JSON")
    status: Optional[Literal["draft", "active", "archived"]] = Field(None, description="Status")


class WorkflowResponse(BaseModel):
    """Workflow response"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Workflow ID")
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Description")
    markdownContent: Optional[str] = Field(None, description="Workflow markdown documentation content")
    category: str = Field("default", description="Category")
    workflowJson: Dict[str, Any] = Field(..., description="Workflow JSON")
    status: str = Field("draft", description="Status")
    source: Optional[str] = Field(None, description="Storage location: 'project' or 'global'")
    createdBy: Optional[str] = Field(None, description="Creator")
    createdAt: int = Field(..., description="Created timestamp (ms)")
    updatedAt: int = Field(..., description="Updated timestamp (ms)")
    stats: Dict[str, Any] = Field(default_factory=dict, description="Statistics")


class WorkflowRunRequest(BaseModel):
    """Request to run a workflow"""
    model_config = ConfigDict(populate_by_name=True)
    
    inputs: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Input parameters")
    timeout_s: Optional[float] = Field(None, alias="timeoutS", description="Timeout in seconds")
    trace: bool = Field(False, description="Enable tracing")
    session_id: Optional[str] = Field(None, alias="sessionId", description="Optional parent session ID")
    message_id: Optional[str] = Field(None, alias="messageId", description="Optional parent message ID")
    agent: Optional[str] = Field(None, description="Optional agent name for tool context")


class WorkflowExecutionResponse(BaseModel):
    """Workflow execution response"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Execution ID")
    workflowId: str = Field(..., description="Workflow ID")
    inputParams: Dict[str, Any] = Field(default_factory=dict, description="Input parameters")
    outputResults: Optional[Dict[str, Any]] = Field(None, description="Output results")
    status: str = Field(..., description="Status: running/success/error/timeout/cancelled")
    startedAt: int = Field(..., description="Start timestamp (ms)")
    finishedAt: Optional[int] = Field(None, description="Finish timestamp (ms)")
    duration: Optional[float] = Field(None, description="Duration (seconds)")
    executionLog: List[Dict[str, Any]] = Field(default_factory=list, description="Execution log")
    errorMessage: Optional[str] = Field(None, description="Error message")


class WorkflowCenterPublishRequest(BaseModel):
    """Request to publish a workflow as a Docker service."""

    image: Optional[str] = Field(None, description="Docker image used to run service")


class WorkflowCenterInvokeRequest(BaseModel):
    """Request to invoke a published workflow service."""

    model_config = ConfigDict(populate_by_name=True)
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Workflow invoke inputs")
    timeout_s: Optional[float] = Field(None, alias="timeoutS", description="Invoke timeout (seconds)")
    request_id: Optional[str] = Field(None, alias="requestId", description="Caller request id")


class WorkflowStatsResponse(BaseModel):
    """Workflow statistics response"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    workflowId: Optional[str] = Field(None, description="Workflow ID (null for aggregate)")
    callCount: int = Field(0, description="Total calls")
    successCount: int = Field(0, description="Successful calls")
    errorCount: int = Field(0, description="Failed calls")
    totalRuntime: float = Field(0.0, description="Total runtime (seconds)")
    avgRuntime: float = Field(0.0, description="Average runtime (seconds)")
    thumbsUp: int = Field(0, description="Thumbs up count")
    thumbsDown: int = Field(0, description="Thumbs down count")


# =============================================================================
# Filesystem Helpers (Single Source of Truth)
# =============================================================================


def _workflow_dir(workflow_id: str) -> Path:
    """Return the project-level directory for a workflow."""
    return _find_workspace_root() / ".flocks" / "plugins" / "workflows" / workflow_id


def _global_workflow_dir(workflow_id: str) -> Path:
    """Return the global-level directory for a workflow (~/.flocks/plugins/workflows/<id>/)."""
    return Path.home() / ".flocks" / "plugins" / "workflows" / workflow_id


def _read_workflow_from_fs(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Read workflow data from the filesystem.

    Search order (lowest → highest priority), same roots as
    resolve_global_workflow_roots / resolve_project_workflow_roots; per-id dir
    is ``<root>/<id>/`` with ``workflow.json`` inside.
    """
    return shared_read_workflow_from_fs(workflow_id)


async def _build_workflow_tool_context(
    *,
    workflow_id: str,
    action_name: str,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    agent: Optional[str] = None,
) -> ToolContext:
    """Build a real ToolContext for workflow execution.

    Prefer the caller-provided session/message. When absent, create a temporary
    parent session and synthetic user message so workflow-internal tools such as
    `task` can resolve a valid parent session.
    """
    effective_session_id = str(session_id or "").strip()
    effective_message_id = str(message_id or "").strip()
    effective_agent = str(agent or "").strip()

    workspace_dir = os.getcwd()
    project_id = "default"
    try:
        from flocks.project.instance import Instance

        workspace_dir = str(getattr(Instance, "directory", None) or workspace_dir)
        project = getattr(Instance, "project", None)
        if project is not None and getattr(project, "id", None):
            project_id = str(project.id)
    except Exception:
        workspace_dir = str(_find_workspace_root())

    parent_session = None
    if effective_session_id:
        parent_session = await Session.get_by_id(effective_session_id)
        if not parent_session:
            raise HTTPException(status_code=400, detail=f"Parent session not found: {effective_session_id}")
        workspace_dir = str(getattr(parent_session, "directory", None) or workspace_dir)
        if getattr(parent_session, "project_id", None):
            project_id = str(parent_session.project_id)
        if not effective_agent:
            effective_agent = str(getattr(parent_session, "agent", None) or "rex")
    else:
        parent_session = await Session.create(
            project_id=project_id,
            directory=workspace_dir,
            title=f"Workflow {action_name}: {workflow_id}",
            agent=effective_agent or "rex",
            category="task",
            metadata={
                "workflowTempParent": True,
                "hideFromSessionManager": True,
                "workflowId": workflow_id,
                "workflowAction": action_name,
            },
        )
        effective_session_id = parent_session.id
        workspace_dir = str(getattr(parent_session, "directory", None) or workspace_dir)
        if not effective_agent:
            effective_agent = str(getattr(parent_session, "agent", None) or "rex")

    if not effective_message_id:
        message = await Message.create(
            session_id=effective_session_id,
            role=MessageRole.USER,
            content=f"[Workflow {action_name}] {workflow_id}",
            agent=effective_agent or "rex",
            synthetic=True,
        )
        effective_message_id = message.id

    return ToolContext(
        session_id=effective_session_id,
        message_id=effective_message_id,
        agent=effective_agent or "rex",
        event_publish_callback=publish_event,
        extra={
            "workspace_dir": workspace_dir,
            "main_session_key": effective_session_id,
        },
    )


def _write_workflow_to_fs(
    workflow_id: str,
    workflow_json: Dict[str, Any],
    meta: Dict[str, Any],
    markdown_content: Optional[str] = None,
    *,
    global_store: bool = False,
) -> None:
    """Write workflow definition and metadata to the filesystem.

    When *global_store* is True the workflow is written under
    ``~/.flocks/plugins/workflows/<id>/`` instead of the project directory.
    """
    wf_dir = _global_workflow_dir(workflow_id) if global_store else _workflow_dir(workflow_id)
    wf_dir.mkdir(parents=True, exist_ok=True)

    with open(wf_dir / "workflow.json", "w", encoding="utf-8") as f:
        json.dump(workflow_json, f, ensure_ascii=False, indent=2)

    meta_to_save = {k: v for k, v in meta.items() if k not in ("workflowJson", "markdownContent", "stats", "source")}
    with open(wf_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta_to_save, f, ensure_ascii=False, indent=2)

    if markdown_content is not None:
        with open(wf_dir / "workflow.md", "w", encoding="utf-8") as f:
            f.write(markdown_content)


def _delete_workflow_from_fs(workflow_id: str) -> bool:
    """Remove a workflow directory from all known locations (primary + legacy plugins).

    Returns True if at least one directory was deleted.
    """
    deleted = False
    for root, _source in _all_scan_dirs():
        wf_dir = root / workflow_id
        if wf_dir.is_dir():
            shutil.rmtree(wf_dir)
            log.info("workflow.fs.deleted", {"id": workflow_id, "dir": str(wf_dir)})
            deleted = True
    return deleted


def _scan_workflow_base_dir(base_dir: Path, source: str) -> Dict[str, Dict[str, Any]]:
    """Scan a single workflow base directory and return {id: data} dict."""
    results: Dict[str, Dict[str, Any]] = {}
    if not base_dir.is_dir():
        return results
    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir():
            continue
        data = _read_workflow_dir(entry, entry.name, source)
        if data is not None:
            results[entry.name] = data
    return results


def _list_workflows_from_fs() -> List[Dict[str, Any]]:
    """Scan global and project workflow directories and return merged list.

    Scan order matches *_all_scan_dirs()* (lowest -> highest priority): each
    root from *resolve_global_workflow_roots* then each from
    *resolve_project_workflow_roots(workspace)*; under each root, immediate
    subdirectories with *workflow.json* are workflows.

    Later entries override earlier ones when the workflow directory name (*id*)
    is the same.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for scan_path, source in _all_scan_dirs():
        by_id.update(_scan_workflow_base_dir(scan_path, source))
    return list(by_id.values())


async def _migrate_storage_to_filesystem() -> None:
    """One-time migration: move Storage-only workflow definitions to the filesystem.

    After this migration the filesystem is the sole source of truth for workflow
    definitions. Storage is retained only for stats and execution history.

    Uses a marker file (.flocks/.storage_migrated) so the migration is safe
    across multiple workers / process restarts.
    """
    marker = _find_workspace_root() / ".flocks" / ".storage_migrated"
    if marker.exists():
        return

    try:
        keys = await Storage.list_keys("workflow/")
        migrated = 0
        for key in keys:
            remainder = key.removeprefix("workflow/")
            if "/" in remainder:
                continue
            workflow_id = remainder
            if not workflow_id:
                continue

            wf_dir = _workflow_dir(workflow_id)
            if (wf_dir / "workflow.json").is_file():
                continue  # already on the filesystem

            try:
                data = await Storage.read(key)
                if not data:
                    continue
                workflow_json = data.get("workflowJson", {})
                meta = {
                    "id": workflow_id,
                    "name": data.get("name", workflow_id),
                    "description": data.get("description"),
                    "category": data.get("category", "default"),
                    "status": data.get("status", "draft"),
                    "createdBy": data.get("createdBy"),
                    "createdAt": data.get("createdAt", int(time.time() * 1000)),
                    "updatedAt": data.get("updatedAt", int(time.time() * 1000)),
                }
                markdown_content = data.get("markdownContent")
                _write_workflow_to_fs(workflow_id, workflow_json, meta, markdown_content)
                migrated += 1
                log.info("workflow.migration.migrated", {"id": workflow_id})
            except Exception as exc:
                log.warning("workflow.migration.skip", {"key": key, "error": str(exc)})

        if migrated:
            log.info("workflow.migration.done", {"migrated": migrated})
        # Mark migration as completed so other workers skip it
        try:
            marker.touch()
        except Exception:
            pass
    except Exception as exc:
        log.warning("workflow.migration.failed", {"error": str(exc)})


# =============================================================================
# Storage Helpers (Stats & Execution only)
# =============================================================================

def _workflow_stats_key(workflow_id: str) -> str:
    return f"workflow/{workflow_id}/stats"


def _workflow_execution_key(exec_id: str) -> str:
    return f"workflow_execution/{exec_id}"


def _normalize_execution_status(status: str) -> str:
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


def _resolve_execution_outcome(result: RunWorkflowResult) -> tuple[str, Optional[str]]:
    """Resolve API execution status from runner status and workflow outputs."""
    status_value = _normalize_execution_status(result.status)
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


async def _record_execution_result(workflow_id: str, exec_id: str, exec_data: Dict[str, Any]) -> None:
    """Persist the final execution record and audit trail."""
    await Storage.write(_workflow_execution_key(exec_id), exec_data)
    try:
        await Recorder.record_workflow_execution(
            exec_id=exec_id,
            workflow_id=workflow_id,
            run_result=exec_data,
        )
    except Exception:
        pass


async def _run_workflow_execution_task(
    *,
    workflow_id: str,
    workflow_json: Dict[str, Any],
    req: WorkflowRunRequest,
    exec_id: str,
    cancel_event: threading.Event,
    tool_context: Optional[ToolContext] = None,
) -> None:
    """Execute a workflow in the background and keep the execution record updated."""
    exec_key = _workflow_execution_key(exec_id)
    start_time = time.time()
    step_history: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    def _on_step_complete(step_result) -> None:
        step_dict = step_result.model_dump(mode="json")
        step_history.append(step_dict)
        try:
            current = asyncio.run_coroutine_threadsafe(Storage.read(exec_key), loop).result(timeout=5)
            update = {
                **current,
                "executionLog": list(step_history),
            }
            asyncio.run_coroutine_threadsafe(Storage.write(exec_key, update), loop).result(timeout=5)
        except Exception as exc:
            log.warning("workflow.step_progress.write_failed", {
                "exec_id": exec_id,
                "error": str(exc),
            })

    try:
        result: RunWorkflowResult = await asyncio.to_thread(
            run_workflow,
            workflow=workflow_json,
            inputs=req.inputs or {},
            timeout_s=req.timeout_s,
            trace=req.trace,
            on_step_complete=_on_step_complete,
            cancel=cancel_event.is_set,
            tool_context=tool_context,
        )

        duration = time.time() - start_time
        current_data = await Storage.read(exec_key)
        status_value, error_message = _resolve_execution_outcome(result)
        current_data.update({
            "outputResults": result.outputs,
            "status": status_value,
            "finishedAt": int(time.time() * 1000),
            "duration": duration,
            "executionLog": result.history or list(step_history),
            "errorMessage": error_message,
        })

        if status_value == "success":
            await _update_workflow_stats(workflow_id, True, duration)
        elif status_value in {"error", "timeout"}:
            await _update_workflow_stats(workflow_id, False, duration)

        await _record_execution_result(workflow_id, exec_id, current_data)
        log.info("workflow.executed", {
            "id": workflow_id,
            "exec_id": exec_id,
            "status": status_value,
            "duration": duration,
        })
    except Exception as exc:
        duration = time.time() - start_time
        current_data = await Storage.read(exec_key)
        current_data.update({
            "status": "cancelled" if cancel_event.is_set() else "error",
            "finishedAt": int(time.time() * 1000),
            "duration": duration,
            "errorMessage": str(exc),
            "executionLog": list(step_history),
        })
        if current_data["status"] == "error":
            await _update_workflow_stats(workflow_id, False, duration)
        await _record_execution_result(workflow_id, exec_id, current_data)
        log.error("workflow.execute.error", {
            "id": workflow_id,
            "exec_id": exec_id,
            "error": str(exc),
        })
    finally:
        _active_workflow_executions.pop(exec_id, None)


_DEFAULT_STATS: Dict[str, Any] = {
    "callCount": 0,
    "successCount": 0,
    "errorCount": 0,
    "totalRuntime": 0.0,
    "avgRuntime": 0.0,
    "thumbsUp": 0,
    "thumbsDown": 0,
}


def _compute_avg_runtime(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure avgRuntime is computed and present in stats dict."""
    call_count = stats.get("callCount", 0)
    total = stats.get("totalRuntime", 0.0)
    stats["avgRuntime"] = (total / call_count) if call_count > 0 else 0.0
    return stats


async def _get_workflow_stats(workflow_id: str) -> Dict[str, Any]:
    """Get workflow statistics"""
    try:
        data = await Storage.read(_workflow_stats_key(workflow_id))
        if data is None:
            return dict(_DEFAULT_STATS)
        return _compute_avg_runtime(data)
    except Exception:
        return dict(_DEFAULT_STATS)


async def _update_workflow_stats(workflow_id: str, success: bool, duration: float) -> None:
    """Update workflow statistics"""
    stats = await _get_workflow_stats(workflow_id)
    stats["callCount"] += 1
    if success:
        stats["successCount"] += 1
    else:
        stats["errorCount"] += 1
    stats["totalRuntime"] += duration
    await Storage.write(_workflow_stats_key(workflow_id), stats)


# =============================================================================
# API Endpoints - Workflow CRUD
# =============================================================================

@router.get("/workflow", response_model=List[WorkflowResponse])
async def list_workflows(
    category: Optional[str] = Query(None, description="Filter by category"),
    status: Optional[str] = Query(None, description="Filter by status"),
    exclude_id: Optional[str] = Query(None, alias="excludeId", description="Exclude workflow by ID (e.g. exclude self when selecting sub-workflows)"),
):
    """
    Get workflow list

    Reads directly from the filesystem (.flocks/workflow/). Runs a one-time
    migration on first call to move any Storage-only workflows to the filesystem.
    """
    try:
        await _migrate_storage_to_filesystem()

        all_data = _list_workflows_from_fs()
        workflows = []

        for data in all_data:
            try:
                if category and data.get("category") != category:
                    continue
                if status and data.get("status") != status:
                    continue
                if exclude_id and data.get("id") == exclude_id:
                    continue

                workflow_id = data["id"]
                stats = await _get_workflow_stats(workflow_id)
                data["stats"] = stats

                workflows.append(WorkflowResponse(**data))
            except Exception as e:
                log.warning("workflow.list.skip", {"id": data.get("id"), "error": str(e)})
                continue

        workflows.sort(key=lambda w: w.updatedAt, reverse=True)

        log.info("workflow.list", {"count": len(workflows), "category": category, "status": status, "exclude_id": exclude_id})
        return workflows
    except Exception as e:
        log.error("workflow.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list workflows: {str(e)}")


@router.post("/workflow", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(req: WorkflowCreateRequest):
    """
    Create a new workflow

    Validates the workflow JSON and writes it to the filesystem as the source
    of truth. Stats are initialised in Storage on first access.
    """
    try:
        try:
            Workflow.from_dict(req.workflow_json)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid workflow JSON: {str(e)}")

        workflow_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)

        meta = {
            "id": workflow_id,
            "name": req.name,
            "description": req.description,
            "category": req.category or "default",
            "status": "draft",
            "createdBy": req.created_by,
            "createdAt": now_ms,
            "updatedAt": now_ms,
        }

        _write_workflow_to_fs(workflow_id, req.workflow_json, meta)

        stats = await _get_workflow_stats(workflow_id)
        data = {**meta, "workflowJson": req.workflow_json, "markdownContent": None, "stats": stats}

        log.info("workflow.created", {"id": workflow_id, "name": req.name})
        await publish_event("workflow.created", {"id": workflow_id, "name": req.name})
        return WorkflowResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.create.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to create workflow: {str(e)}")


@router.get("/workflow/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    """
    Get workflow details

    Reads directly from the filesystem. AI edits to workflow.json or workflow.md
    are always reflected immediately without any sync step.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        stats = await _get_workflow_stats(workflow_id)
        data["stats"] = stats

        return WorkflowResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get workflow: {str(e)}")


@router.put("/workflow/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(workflow_id: str, req: WorkflowUpdateRequest):
    """
    Update workflow

    Reads from the filesystem, applies changes, and writes back. Both the
    workflow definition (workflow.json) and metadata (meta.json) are updated
    atomically within the same directory.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]
        markdown_content = data.get("markdownContent")

        if req.name is not None:
            data["name"] = req.name
        if req.description is not None:
            data["description"] = req.description
        if req.category is not None:
            data["category"] = req.category
        if req.status is not None:
            data["status"] = req.status
        if req.workflow_json is not None:
            try:
                Workflow.from_dict(req.workflow_json)
                workflow_json = req.workflow_json
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid workflow JSON: {str(e)}")

        data["updatedAt"] = int(time.time() * 1000)

        is_global = data.get("source") == "global"
        _write_workflow_to_fs(workflow_id, workflow_json, data, markdown_content, global_store=is_global)

        stats = await _get_workflow_stats(workflow_id)
        data["workflowJson"] = workflow_json
        data["stats"] = stats

        log.info("workflow.updated", {"id": workflow_id})
        await publish_event("workflow.updated", {"id": workflow_id, "name": data.get("name")})
        return WorkflowResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.update.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to update workflow: {str(e)}")


@router.delete("/workflow/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: str):
    """
    Delete workflow

    Removes the workflow directory from the filesystem (source of truth) and
    cleans up associated runtime data (stats, execution history) from Storage.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        # Remove from filesystem (source of truth)
        _delete_workflow_from_fs(workflow_id)

        # Clean up runtime data from Storage
        try:
            await Storage.remove(_workflow_stats_key(workflow_id))
        except Storage.NotFoundError:
            pass

        try:
            exec_keys = await Storage.list("workflow_execution/")
            for key in exec_keys:
                try:
                    exec_data = await Storage.read(key)
                    if exec_data.get("workflowId") == workflow_id:
                        await Storage.remove(key)
                except Exception:
                    pass
        except Exception:
            pass

        log.info("workflow.deleted", {"id": workflow_id})
        await publish_event("workflow.deleted", {"id": workflow_id})
        return None
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.delete.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to delete workflow: {str(e)}")


# =============================================================================
# API Endpoints - Workflow Operations
# =============================================================================

@router.post("/workflow/{workflow_id}/run", response_model=WorkflowExecutionResponse)
async def run_workflow_endpoint(workflow_id: str, req: WorkflowRunRequest):
    """
    Execute workflow
    
    Runs the workflow with provided inputs and returns execution results.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]
        tool_context = await _build_workflow_tool_context(
            workflow_id=workflow_id,
            action_name="run",
            session_id=req.session_id,
            message_id=req.message_id,
            agent=req.agent,
        )

        # Create execution record
        exec_id = str(uuid.uuid4())
        start_ms = int(time.time() * 1000)
        
        exec_data = {
            "id": exec_id,
            "workflowId": workflow_id,
            "inputParams": req.inputs or {},
            "status": "running",
            "startedAt": start_ms,
            "executionLog": [],
        }
        
        # Save initial execution record
        await Storage.write(_workflow_execution_key(exec_id), exec_data)
        
        cancel_event = threading.Event()
        task = asyncio.create_task(
            _run_workflow_execution_task(
                workflow_id=workflow_id,
                workflow_json=workflow_json,
                req=req,
                exec_id=exec_id,
                cancel_event=cancel_event,
                tool_context=tool_context,
            )
        )
        _active_workflow_executions[exec_id] = ActiveWorkflowExecution(
            workflow_id=workflow_id,
            task=task,
            cancel_event=cancel_event,
        )

        log.info("workflow.execution.started", {
            "id": workflow_id,
            "exec_id": exec_id,
        })
        return WorkflowExecutionResponse(**exec_data)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.run.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to run workflow: {str(e)}")


@router.post("/workflow/{workflow_id}/history/{exec_id}/cancel")
async def cancel_workflow_execution(workflow_id: str, exec_id: str):
    """Request cooperative cancellation of a running workflow execution."""
    try:
        exec_data = await Storage.read(_workflow_execution_key(exec_id))
        if exec_data.get("workflowId") != workflow_id:
            raise HTTPException(status_code=404, detail="Execution not found for this workflow")

        active = _active_workflow_executions.get(exec_id)
        if active is None:
            return {
                "status": "ignored",
                "message": f"Execution {exec_id} is already {exec_data.get('status', 'completed')}",
                "executionId": exec_id,
            }

        if active.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="Execution not found for this workflow")

        active.cancel_event.set()
        log.info("workflow.execution.cancel_requested", {
            "id": workflow_id,
            "exec_id": exec_id,
        })
        return {
            "status": "accepted",
            "message": f"Cancellation requested for execution {exec_id}",
            "executionId": exec_id,
        }
    except Storage.NotFoundError:
        raise HTTPException(status_code=404, detail=f"Execution not found: {exec_id}")
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.execution.cancel.error", {
            "id": workflow_id,
            "exec_id": exec_id,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=f"Failed to cancel execution: {str(e)}")


@router.post("/workflow/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    """
    Validate workflow
    
    Lints the workflow and returns validation errors/warnings.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]

        try:
            workflow = Workflow.from_dict(workflow_json)
            # Run lint checks (errors + warnings)
            lint_results = lint_workflow(workflow)
            lint_errors = [r for r in lint_results if r.get("severity") == "error"]

            log.info("workflow.validated", {"id": workflow_id, "issues": len(lint_results), "errors": len(lint_errors)})
            return {
                "valid": len(lint_errors) == 0,
                "issues": lint_results,
            }
        except Exception as e:
            return {
                "valid": False,
                "issues": [{"type": "error", "message": str(e)}],
            }
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.validate.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to validate workflow: {str(e)}")




# =============================================================================
# API Endpoints - Workflow Center (Skill -> Register -> Publish Service)
# =============================================================================

@router.post("/workflow-center/scan-workflows")
async def workflow_center_scan_workflows():
    """Scan .flocks/workflow and register discovered workflows."""
    try:
        items = await scan_skill_workflows()
        return {"count": len(items), "items": items}
    except Exception as e:
        log.error("workflow.center.scan.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to scan skill workflows: {str(e)}")


@router.post("/workflow-center/scan-skill", deprecated=True)
async def workflow_center_scan_skill_alias():
    """Backward-compatible alias for scan-workflows."""
    return await workflow_center_scan_workflows()


@router.get("/workflow-center")
async def workflow_center_list():
    """List workflow center registry entries."""
    try:
        items = await list_registry_entries()
        return {"count": len(items), "items": items}
    except Exception as e:
        log.error("workflow.center.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list workflow center entries: {str(e)}")


@router.post("/workflow-center/{workflow_id}/publish")
async def workflow_center_publish(workflow_id: str, req: WorkflowCenterPublishRequest):
    """Publish workflow as dockerized service."""
    try:
        result = await publish_workflow(workflow_id, image=req.image)
        return result
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("workflow.center.publish.error", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to publish workflow: {str(e)}")


@router.post("/workflow-center/{workflow_id}/stop")
async def workflow_center_stop(workflow_id: str):
    """Stop published workflow docker service."""
    try:
        result = await stop_workflow_service(workflow_id)
        return result
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("workflow.center.stop.error", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to stop workflow service: {str(e)}")


@router.post("/workflow-center/{workflow_id}/invoke")
async def workflow_center_invoke(workflow_id: str, req: WorkflowCenterInvokeRequest):
    """Proxy invoke request to active published workflow service."""
    try:
        return await invoke_published_workflow(
            workflow_id,
            inputs=req.inputs,
            timeout_s=req.timeout_s,
            request_id=req.request_id,
        )
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowNotPublishedError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("workflow.center.invoke.error", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to invoke workflow service: {str(e)}")


@router.get("/workflow-center/{workflow_id}/health")
async def workflow_center_health(workflow_id: str):
    """Get published workflow service health."""
    try:
        return await get_workflow_health(workflow_id)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("workflow.center.health.error", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get workflow health: {str(e)}")


@router.get("/workflow-center/{workflow_id}/releases")
async def workflow_center_releases(workflow_id: str):
    """List workflow release history."""
    try:
        items = await list_workflow_releases(workflow_id)
        return {"count": len(items), "items": items}
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("workflow.center.releases.error", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list workflow releases: {str(e)}")


# =============================================================================
# API Endpoints - Workflow History
# =============================================================================

@router.get("/workflow/{workflow_id}/history", response_model=List[WorkflowExecutionResponse])
async def get_workflow_history(
    workflow_id: str,
    limit: int = Query(50, ge=1, le=100, description="Max results"),
):
    """
    Get workflow execution history
    
    Returns list of recent executions for this workflow.
    """
    try:
        if not _read_workflow_from_fs(workflow_id):
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        all_exec_keys = await Storage.list("workflow_execution/")
        executions = []
        
        for key in all_exec_keys:
            try:
                exec_data = await Storage.read(key)
                if exec_data.get("workflowId") == workflow_id:
                    executions.append(WorkflowExecutionResponse(**exec_data))
            except Exception as e:
                log.warning("workflow.history.skip", {"key": key, "error": str(e)})
                continue
        
        # Sort by start time (newest first) and limit
        executions.sort(key=lambda e: e.startedAt, reverse=True)
        executions = executions[:limit]
        
        log.info("workflow.history", {"id": workflow_id, "count": len(executions)})
        return executions
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.history.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get workflow history: {str(e)}")


@router.get("/workflow/{workflow_id}/history/{exec_id}", response_model=WorkflowExecutionResponse)
async def get_execution_details(workflow_id: str, exec_id: str):
    """
    Get execution details
    
    Returns detailed information about a specific workflow execution.
    """
    try:
        exec_data = await Storage.read(_workflow_execution_key(exec_id))
        
        # Verify workflow ID matches
        if exec_data.get("workflowId") != workflow_id:
            raise HTTPException(status_code=404, detail="Execution not found for this workflow")
        
        return WorkflowExecutionResponse(**exec_data)
    except Storage.NotFoundError:
        raise HTTPException(status_code=404, detail=f"Execution not found: {exec_id}")
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.execution.get.error", {"id": exec_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get execution: {str(e)}")


# =============================================================================
# API Endpoints - Workflow Statistics
# =============================================================================

@router.get("/workflow/stats", response_model=WorkflowStatsResponse)
async def get_aggregate_stats():
    """
    Get aggregate workflow statistics
    
    Returns statistics across all workflows.
    """
    try:
        aggregate = {
            "workflowId": None,
            "callCount": 0,
            "successCount": 0,
            "errorCount": 0,
            "totalRuntime": 0.0,
            "avgRuntime": 0.0,
            "thumbsUp": 0,
            "thumbsDown": 0,
        }

        all_workflows = _list_workflows_from_fs()
        workflow_count = 0
        for wf in all_workflows:
            try:
                stats = await _get_workflow_stats(wf["id"])
                aggregate["callCount"] += stats.get("callCount", 0)
                aggregate["successCount"] += stats.get("successCount", 0)
                aggregate["errorCount"] += stats.get("errorCount", 0)
                aggregate["totalRuntime"] += stats.get("totalRuntime", 0.0)
                aggregate["thumbsUp"] += stats.get("thumbsUp", 0)
                aggregate["thumbsDown"] += stats.get("thumbsDown", 0)
                workflow_count += 1
            except Exception:
                continue

        if aggregate["callCount"] > 0:
            aggregate["avgRuntime"] = aggregate["totalRuntime"] / aggregate["callCount"]

        log.info("workflow.stats.aggregate", {"workflows": workflow_count})
        return WorkflowStatsResponse(**aggregate)
    except Exception as e:
        log.error("workflow.stats.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")


@router.get("/workflow/{workflow_id}/stats", response_model=WorkflowStatsResponse)
async def get_workflow_stats_endpoint(workflow_id: str):
    """
    Get workflow statistics
    
    Returns statistics for a specific workflow.
    """
    try:
        if not _read_workflow_from_fs(workflow_id):
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        stats = await _get_workflow_stats(workflow_id)
        
        # Calculate average runtime
        avg_runtime = 0.0
        if stats["callCount"] > 0:
            avg_runtime = stats["totalRuntime"] / stats["callCount"]
        
        result = {
            "workflowId": workflow_id,
            "callCount": stats["callCount"],
            "successCount": stats["successCount"],
            "errorCount": stats["errorCount"],
            "totalRuntime": stats["totalRuntime"],
            "avgRuntime": avg_runtime,
            "thumbsUp": stats["thumbsUp"],
            "thumbsDown": stats["thumbsDown"],
        }
        
        return WorkflowStatsResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.stats.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get workflow statistics: {str(e)}")


# =============================================================================
# API Endpoints - Import/Export
# =============================================================================

@router.post("/workflow/import", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def import_workflow(workflow_json: Dict[str, Any]):
    """
    Import workflow

    Imports a workflow from a JSON definition and writes it to the filesystem.
    """
    try:
        try:
            Workflow.from_dict(workflow_json)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid workflow JSON: {str(e)}")

        name = workflow_json.get("name", "Imported Workflow")
        description = workflow_json.get("metadata", {}).get("description")
        category = workflow_json.get("metadata", {}).get("category", "default")

        workflow_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)

        meta = {
            "id": workflow_id,
            "name": name,
            "description": description,
            "category": category,
            "status": "draft",
            "createdBy": None,
            "createdAt": now_ms,
            "updatedAt": now_ms,
        }

        _write_workflow_to_fs(workflow_id, workflow_json, meta)

        stats = await _get_workflow_stats(workflow_id)
        data = {**meta, "workflowJson": workflow_json, "markdownContent": None, "stats": stats}

        log.info("workflow.imported", {"id": workflow_id, "name": name})
        await publish_event("workflow.created", {"id": workflow_id, "name": name})
        return WorkflowResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.import.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to import workflow: {str(e)}")


@router.get("/workflow/{workflow_id}/export")
async def export_workflow(workflow_id: str):
    """
    Export workflow
    
    Exports workflow as JSON for download/sharing.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]

        if "metadata" not in workflow_json:
            workflow_json["metadata"] = {}
        workflow_json["metadata"]["exportedFrom"] = "flocks"
        workflow_json["metadata"]["exportedAt"] = int(time.time() * 1000)
        workflow_json["name"] = data["name"]
        
        log.info("workflow.exported", {"id": workflow_id})
        return workflow_json
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.export.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to export workflow: {str(e)}")


# =============================================================================
# API Endpoints - Publish / API Service
# =============================================================================

_API_SERVICE_PREFIX = "workflow_api_service/"
_KAFKA_CONFIG_PREFIX = "workflow_kafka_config/"
_REGISTRY_PREFIX_MAIN = "workflow_registry/"


def _api_service_key(workflow_id: str) -> str:
    return f"{_API_SERVICE_PREFIX}{workflow_id}"


def _kafka_config_key(workflow_id: str) -> str:
    return f"{_KAFKA_CONFIG_PREFIX}{workflow_id}"


class WorkflowServiceResponse(BaseModel):
    workflowId: str
    workflowName: str
    serviceUrl: str
    invokeUrl: str
    apiKey: str
    status: str
    publishedAt: int
    containerName: Optional[str] = None


class KafkaConfigRequest(BaseModel):
    inputBroker: Optional[str] = None
    inputTopic: Optional[str] = None
    inputGroupId: Optional[str] = None
    outputBroker: Optional[str] = None
    outputTopic: Optional[str] = None


@router.post("/workflow/{workflow_id}/publish")
async def publish_workflow_as_api(workflow_id: str):
    """
    Publish workflow as Docker API service.

    Writes the workflow JSON to disk, registers it with the workflow center,
    starts a Docker container, and returns the service URL and generated API key.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]

        # Write workflow JSON to a stable path that center.py can read
        service_dir = Config.get_data_path() / "workflow-services" / "workflows" / workflow_id
        service_dir.mkdir(parents=True, exist_ok=True)
        workflow_path = service_dir / "workflow.json"
        workflow_path.write_text(json.dumps(workflow_json), encoding="utf-8")

        fp = hashlib.sha256(workflow_path.read_bytes()).hexdigest()
        now_ms = int(time.time() * 1000)

        existing_registry = await Storage.read(f"{_REGISTRY_PREFIX_MAIN}{workflow_id}") or {}
        registry_entry = {
            "workflowId": workflow_id,
            "name": data["name"],
            "sourceType": "main_storage",
            "workflowPath": str(workflow_path),
            "fingerprint": fp,
            "publishStatus": "unpublished",
            "registeredAt": existing_registry.get("registeredAt", now_ms),
            "updatedAt": now_ms,
        }
        await Storage.write(f"{_REGISTRY_PREFIX_MAIN}{workflow_id}", registry_entry)

        # Use center.py to publish the Docker container
        active_record = await publish_workflow(workflow_id)

        # Preserve existing API key across re-publishes so callers don't break
        existing_service = await Storage.read(_api_service_key(workflow_id)) or {}
        api_key = existing_service.get("apiKey") or (uuid.uuid4().hex + uuid.uuid4().hex)

        service_url = active_record.get("serviceUrl", "")
        invoke_url = f"{service_url}/invoke"
        container_name = active_record.get("containerName", "")

        service_info = {
            "workflowId": workflow_id,
            "workflowName": data["name"],
            "serviceUrl": service_url,
            "invokeUrl": invoke_url,
            "apiKey": api_key,
            "status": "running",
            "publishedAt": now_ms,
            "containerName": container_name,
        }
        await Storage.write(_api_service_key(workflow_id), service_info)

        log.info("workflow.api.published", {"id": workflow_id, "url": service_url})
        return service_info
    except HTTPException:
        raise
    except WorkflowCenterError as e:
        log.error("workflow.publish.center_error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"发布失败（Docker）: {str(e)}")
    except Exception as e:
        log.error("workflow.publish.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to publish workflow: {str(e)}")


@router.post("/workflow/{workflow_id}/unpublish")
async def unpublish_workflow_api(workflow_id: str):
    """
    Stop a published workflow API service.
    """
    try:
        existing = await Storage.read(_api_service_key(workflow_id))
        if not existing:
            raise HTTPException(status_code=404, detail="No published service found for this workflow")

        try:
            await stop_workflow_service(workflow_id)
        except (WorkflowNotFoundError, WorkflowNotPublishedError):
            pass

        existing["status"] = "stopped"
        existing["stoppedAt"] = int(time.time() * 1000)
        await Storage.write(_api_service_key(workflow_id), existing)

        log.info("workflow.api.unpublished", {"id": workflow_id})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.unpublish.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to stop workflow service: {str(e)}")


@router.get("/workflow/{workflow_id}/service")
async def get_workflow_service(workflow_id: str):
    """
    Get published API service info for a workflow.
    Returns null if not published.
    """
    try:
        service = await Storage.read(_api_service_key(workflow_id))
        return service  # None / null if not found
    except Exception as e:
        log.error("workflow.service.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get service info: {str(e)}")


@router.get("/workflow-services")
async def list_workflow_services():
    """
    List all published workflow API services.
    """
    try:
        keys = await Storage.list_keys(_API_SERVICE_PREFIX)
        services = []
        for key in keys:
            entry = await Storage.read(key)
            if entry:
                services.append(entry)
        services.sort(key=lambda s: s.get("publishedAt", 0), reverse=True)
        return services
    except Exception as e:
        log.error("workflow.services.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list services: {str(e)}")


@router.post("/workflow/{workflow_id}/kafka-config")
async def save_kafka_config(workflow_id: str, req: KafkaConfigRequest):
    """
    Save Kafka input/output configuration for a workflow.
    (Kafka integration is experimental; this stores config for future use.)
    """
    try:
        if not _read_workflow_from_fs(workflow_id):
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        config = {
            "workflowId": workflow_id,
            "inputBroker": req.inputBroker,
            "inputTopic": req.inputTopic,
            "inputGroupId": req.inputGroupId,
            "outputBroker": req.outputBroker,
            "outputTopic": req.outputTopic,
            "updatedAt": int(time.time() * 1000),
        }
        await Storage.write(_kafka_config_key(workflow_id), config)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.kafka_config.save.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to save Kafka config: {str(e)}")


@router.get("/workflow/{workflow_id}/kafka-config")
async def get_kafka_config(workflow_id: str):
    """
    Get saved Kafka configuration for a workflow.
    """
    try:
        config = await Storage.read(_kafka_config_key(workflow_id))
        return config  # None / null if not configured
    except Exception as e:
        log.error("workflow.kafka_config.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get Kafka config: {str(e)}")


# =============================================================================
# API Endpoints - Run Single Node
# =============================================================================

class RunNodeRequest(BaseModel):
    """Request to execute a single workflow node."""
    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(..., description="Node ID to execute")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Input data for the node")
    session_id: Optional[str] = Field(None, alias="sessionId", description="Optional parent session ID")
    message_id: Optional[str] = Field(None, alias="messageId", description="Optional parent message ID")
    agent: Optional[str] = Field(None, description="Optional agent name for tool context")


class RunNodeResponse(BaseModel):
    """Response from executing a single workflow node."""
    model_config = ConfigDict(populate_by_name=True)

    node_id: str
    outputs: Dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    error: Optional[str] = None
    traceback: Optional[str] = None
    duration_ms: Optional[float] = None
    success: bool = True


@router.post("/workflow/{workflow_id}/run-node", response_model=RunNodeResponse)
async def run_single_node(workflow_id: str, req: RunNodeRequest):
    """
    Execute a single workflow node in isolation.

    Runs one node with the provided inputs and returns its outputs.
    Intended for step-by-step testing and debugging by agents.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data["workflowJson"]
        tool_context = await _build_workflow_tool_context(
            workflow_id=workflow_id,
            action_name=f"run-node:{req.node_id}",
            session_id=req.session_id,
            message_id=req.message_id,
            agent=req.agent,
        )

        try:
            from flocks.workflow.models import Workflow as WfModel
            from flocks.workflow.engine import WorkflowEngine
            from flocks.workflow.repl_runtime import PythonExecRuntime

            wf = WfModel.from_dict(workflow_json)
            engine = WorkflowEngine(
                wf,
                runtime=PythonExecRuntime(tool_registry=get_tool_registry(tool_context=tool_context)),
            )

            step_result = await asyncio.to_thread(engine.run_node, req.node_id, req.inputs)

            log.info("workflow.run_node", {
                "workflow_id": workflow_id,
                "node_id": req.node_id,
                "success": step_result.error is None,
                "duration_ms": step_result.duration_ms,
            })

            return RunNodeResponse(
                node_id=step_result.node_id,
                outputs=step_result.outputs,
                stdout=step_result.stdout or "",
                error=step_result.error,
                traceback=step_result.traceback,
                duration_ms=step_result.duration_ms,
                success=step_result.error is None,
            )
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Node not found: {e}")
        except Exception as e:
            log.error("workflow.run_node.error", {"workflow_id": workflow_id, "node_id": req.node_id, "error": str(e)})
            return RunNodeResponse(
                node_id=req.node_id,
                outputs={},
                error=str(e),
                success=False,
            )
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.run_node.fatal", {"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to run node: {str(e)}")


# =============================================================================
# API Endpoints - Sample Inputs
# =============================================================================

class SampleInputsRequest(BaseModel):
    """Request to save sample inputs for a workflow."""
    model_config = ConfigDict(populate_by_name=True)

    sampleInputs: Dict[str, Any] = Field(default_factory=dict, description="Sample input data")


@router.get("/workflow/{workflow_id}/sample-inputs")
async def get_sample_inputs(workflow_id: str):
    """
    Get saved sample inputs for a workflow.

    Sample inputs are stored in workflowJson.metadata.sampleInputs and used
    to pre-populate the Run tab test form.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data.get("workflowJson", {})
        metadata = workflow_json.get("metadata") or {}
        sample_inputs = metadata.get("sampleInputs", {})
        return {"sampleInputs": sample_inputs}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.sample_inputs.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get sample inputs: {str(e)}")


@router.post("/workflow/{workflow_id}/sample-inputs")
async def save_sample_inputs(workflow_id: str, req: SampleInputsRequest):
    """
    Save sample inputs for a workflow.

    Persists sample inputs into workflowJson.metadata.sampleInputs so they
    survive server restarts and are available for pre-filling the Run tab.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        workflow_json = data.get("workflowJson", {})
        if "metadata" not in workflow_json or workflow_json["metadata"] is None:
            workflow_json["metadata"] = {}
        workflow_json["metadata"]["sampleInputs"] = req.sampleInputs

        meta = {k: v for k, v in data.items() if k not in ("workflowJson", "markdownContent", "stats", "source")}
        meta["updatedAt"] = int(time.time() * 1000)
        markdown_content = data.get("markdownContent")
        is_global = data.get("source") == "global"
        _write_workflow_to_fs(workflow_id, workflow_json, meta, markdown_content, global_store=is_global)

        log.info("workflow.sample_inputs.saved", {"id": workflow_id})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.sample_inputs.save.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to save sample inputs: {str(e)}")
