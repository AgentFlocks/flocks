"""
Workflow management routes

Provides API endpoints for workflow CRUD, execution, history, and AI generation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Any, Dict, Literal
from fastapi import APIRouter, Body, HTTPException, Request, status, Query
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
from flocks.workflow.workflow_lint import lint_workflow
from flocks.workflow.compiler import compile_workflow
from flocks.workflow.fs_store import (
    find_workspace_root as _find_workspace_root,
    read_workflow_dir as _read_workflow_dir,
    read_workflow_from_fs as shared_read_workflow_from_fs,
    workflow_scan_dirs as _all_scan_dirs,
)
from flocks.ingest.kafka.constants import WORKFLOW_KAFKA_CONFIG_PREFIX
from flocks.ingest.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.workflow.execution_store import (
    compact_history_for_storage,
    compact_outputs_for_storage,
    compact_step_for_storage,
    create_execution_record,
    normalize_execution_status as _normalize_execution_status,
    record_execution_result as _record_execution_result,
    resolve_execution_outcome as _resolve_execution_outcome,
    workflow_execution_key as _workflow_execution_key,
)
from flocks.workflow.io import load_workflow, dump_workflow
from flocks.workflow.tool_context import build_workflow_tool_context
from flocks.workflow.tools import get_tool_registry
from flocks.workflow.visibility import is_hidden_workflow_data
from flocks.workflow.triggers import (
    TriggerDefinition,
    TriggerEvent,
    build_trigger_event,
    preview_trigger_mapping,
    set_workflow_json_triggers,
    workflow_json_declares_triggers,
    workflow_trigger_definitions_from_json,
)
from flocks.workflow.triggers.dispatcher import evaluate_trigger_filter
from flocks.workflow.triggers.runtime import default_runtime as default_trigger_runtime
from flocks.workflow.triggers.compat import (
    kafka_trigger_to_legacy_config,
    legacy_kafka_trigger_from_config,
    legacy_schedule_trigger_from_config,
    legacy_syslog_trigger_from_config,
    schedule_trigger_to_legacy_config,
    syslog_trigger_to_legacy_config,
)
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.server.routes.event import publish_event
from flocks.tool import ToolContext
from flocks.utils.log import Log


router = APIRouter()
webhook_router = APIRouter()
log = Log.create(service="workflow-routes")

_LEGACY_SINGLETON_TRIGGER_TYPES = frozenset({"schedule", "kafka", "syslog"})
_WORKFLOW_INTEGRATION_CONFIG_VERSION = 1
_WORKFLOW_INTEGRATION_CONFIG_KIND = "workflow.integration-config"
_WORKFLOW_INTEGRATION_CONFIG_PREFIX = "workflow_integration_config/"
_WORKFLOW_CENTER_REGISTRY_PREFIX = "workflow_registry/"
_WORKFLOW_CENTER_RELEASE_PREFIX = "workflow_release/"
_WORKFLOW_CENTER_RUNTIME_PREFIX = "workflow_runtime/"
_WORKFLOW_CENTER_LOCAL_PID_PREFIX = "workflow_local_pid/"
_WORKFLOW_POLLER_CONFIG_PREFIX = "workflow_poller_config/"
_WORKFLOW_CONFIG_TRIGGER_TYPES = frozenset({
    "manual",
    "schedule",
    "webhook",
    "syslog",
    "kafka",
    "internal_event",
    "custom_webhook",
    "custom_adapter",
    "plugin",
    "api",
    "publish",
    "api_service",
    "service",
})
_WORKFLOW_CONFIG_SECRET_KEYS = frozenset({"apikey", "password", "token", "secret"})
_WORKFLOW_CONFIG_SECRET_REF_KEYS = frozenset({"secretref", "secretreference"})


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
    source: Optional[Literal["project", "global"]] = Field(
        "global",
        description="Storage location: 'project' or 'global'; defaults to global user storage",
    )


class WorkflowUpdateRequest(BaseModel):
    """Request to update a workflow"""
    model_config = ConfigDict(populate_by_name=True)
    
    name: Optional[str] = Field(None, description="Workflow name")
    description: Optional[str] = Field(None, description="Workflow description")
    category: Optional[str] = Field(None, description="Workflow category")
    workflow_json: Optional[Dict[str, Any]] = Field(None, alias="workflowJson", description="Workflow JSON")
    markdown_content: Optional[str] = Field(
        None,
        alias="markdownContent",
        description="Human-editable workflow.md content",
    )
    edit_markdown_content: Optional[str] = Field(
        None,
        alias="editMarkdownContent",
        description="Legacy alias for markdownContent",
    )
    status: Optional[Literal["draft", "active", "archived"]] = Field(None, description="Status")


class WorkflowResponse(BaseModel):
    """Workflow response"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Workflow ID")
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Description")
    markdownContent: Optional[str] = Field(None, description="Workflow markdown documentation content")
    editMarkdownContent: Optional[str] = Field(None, description="Human-editable workflow markdown document content")
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
    triggerId: Optional[str] = Field(None, description="Trigger ID")
    triggerType: Optional[str] = Field(None, description="Trigger type")
    deliveryId: Optional[str] = Field(None, description="Trigger delivery ID")
    attempt: Optional[int] = Field(None, description="Trigger attempt")
    triggerSource: Optional[str] = Field(None, description="Trigger source")
    currentNodeId: Optional[str] = Field(None, description="Current running node ID")
    currentNodeType: Optional[str] = Field(None, description="Current running node type")
    currentPhase: Optional[str] = Field(None, description="Current execution phase")
    currentStepIndex: Optional[int] = Field(None, description="Current step index")


class WorkflowCenterPublishRequest(BaseModel):
    """Request to publish a workflow as an API service."""

    image: Optional[str] = Field(None, description="Docker image used to run service")
    driver: Optional[Literal["local", "docker"]] = Field(
        None,
        description="Service driver. Defaults to FLOCKS_WORKFLOW_SERVICE_DRIVER or local.",
    )


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


def _existing_workflow_dir(workflow_id: str) -> Optional[Path]:
    """Return the highest-priority existing directory for a workflow."""
    result: Optional[Path] = None
    for root, _source in _all_scan_dirs():
        wf_dir = root / workflow_id
        if (wf_dir / "workflow.json").is_file():
            result = wf_dir
    return result


def _workflow_config_dir(workflow_id: str, workflow_data: Optional[Dict[str, Any]] = None) -> Path:
    """Return the directory where workflow-local config.json should be written."""
    existing = _existing_workflow_dir(workflow_id)
    if existing is not None:
        return existing
    if workflow_data and workflow_data.get("source") == "global":
        return _global_workflow_dir(workflow_id)
    return _workflow_dir(workflow_id)


def _workflow_integration_config_key(workflow_id: str) -> str:
    """Storage key for the publish/integration template used by the UI."""
    return f"{_WORKFLOW_INTEGRATION_CONFIG_PREFIX}{workflow_id}"


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
    """Build a real ToolContext for workflow execution."""
    return await build_workflow_tool_context(
        workflow_id=workflow_id,
        action_name=action_name,
        session_id=session_id,
        message_id=message_id,
        agent=agent,
        event_publish_callback=publish_event,
    )


def _write_workflow_to_fs(
    workflow_id: str,
    workflow_json: Dict[str, Any],
    meta: Dict[str, Any],
    markdown_content: Optional[str] = None,
    edit_markdown_content: Optional[str] = None,
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

    meta_to_save = {
        k: v
        for k, v in meta.items()
        if k not in ("workflowJson", "markdownContent", "editMarkdownContent", "stats", "source")
    }
    with open(wf_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta_to_save, f, ensure_ascii=False, indent=2)

    if markdown_content is None and edit_markdown_content is not None:
        markdown_content = edit_markdown_content

    if markdown_content is not None:
        with open(wf_dir / "workflow.md", "w", encoding="utf-8") as f:
            f.write(markdown_content)

    legacy_edit_file = wf_dir / "workflow.edit.md"
    if legacy_edit_file.exists():
        legacy_edit_file.unlink()


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


async def _remove_storage_key_if_exists(key: str) -> None:
    try:
        await Storage.remove(key)
    except Storage.NotFoundError:
        pass
    except Exception as exc:
        log.warning("workflow.delete.storage_key_remove_failed", {"key": key, "error": str(exc)})


async def _remove_storage_prefix(prefix: str) -> None:
    try:
        keys = await Storage.list(prefix)
    except Exception as exc:
        log.warning("workflow.delete.storage_prefix_list_failed", {"prefix": prefix, "error": str(exc)})
        return

    for key in keys:
        try:
            await Storage.remove(key)
        except Storage.NotFoundError:
            pass
        except Exception as exc:
            log.warning("workflow.delete.storage_key_remove_failed", {"key": key, "error": str(exc)})


async def _stop_workflow_runtime_resources(workflow_id: str) -> None:
    for exec_id, active in list(_active_workflow_executions.items()):
        if active.workflow_id == workflow_id:
            active.cancel_event.set()

    try:
        await stop_workflow_service(workflow_id)
    except Exception as exc:
        log.debug("workflow.delete.stop_service_ignored", {"id": workflow_id, "error": str(exc)})

    try:
        await default_trigger_runtime.restart_workflow(workflow_id, {"triggers": []})
    except Exception as exc:
        log.debug("workflow.delete.stop_triggers_ignored", {"id": workflow_id, "error": str(exc)})


async def _cleanup_workflow_storage(workflow_id: str) -> None:
    await _remove_storage_key_if_exists(_workflow_stats_key(workflow_id))
    await _remove_storage_key_if_exists(_workflow_integration_config_key(workflow_id))
    await _remove_storage_key_if_exists(_api_service_key(workflow_id))
    await _remove_storage_key_if_exists(_syslog_config_key(workflow_id))
    await _remove_storage_key_if_exists(_kafka_config_key(workflow_id))
    await _remove_storage_key_if_exists(f"{_WORKFLOW_POLLER_CONFIG_PREFIX}{workflow_id}")
    await _remove_storage_key_if_exists(f"{_WORKFLOW_CENTER_REGISTRY_PREFIX}{workflow_id}")
    await _remove_storage_key_if_exists(f"{_WORKFLOW_CENTER_RUNTIME_PREFIX}{workflow_id}")
    await _remove_storage_key_if_exists(f"{_WORKFLOW_CENTER_LOCAL_PID_PREFIX}{workflow_id}")
    await _remove_storage_prefix(f"{_WORKFLOW_CENTER_RELEASE_PREFIX}{workflow_id}/")

    try:
        exec_keys = await Storage.list("workflow_execution/")
        for key in exec_keys:
            try:
                exec_data = await Storage.read(key)
                if isinstance(exec_data, dict) and exec_data.get("workflowId") == workflow_id:
                    await Storage.remove(key)
            except Exception:
                pass
    except Exception:
        pass

    service_dir = Config.get_data_path() / "workflow-services" / "workflows" / workflow_id
    if service_dir.is_dir():
        shutil.rmtree(service_dir, ignore_errors=True)


def _scan_workflow_base_dir(base_dir: Path, source: str) -> Dict[str, Dict[str, Any]]:
    """Scan a single workflow base directory and return {id: data} dict."""
    results: Dict[str, Dict[str, Any]] = {}
    if not base_dir.is_dir():
        return results
    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir():
            continue
        data = _read_workflow_dir(entry, entry.name, source)
        if data is not None and not is_hidden_workflow_data(data):
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


async def sync_workflows_from_filesystem() -> int:
    """Best-effort startup sync for filesystem-backed workflows.

    The filesystem is the source of truth for workflow definitions. Startup only
    needs to migrate any legacy Storage-only records to disk and report how many
    workflows are currently discoverable from the configured workflow roots.
    """
    await _migrate_storage_to_filesystem()
    return len(_list_workflows_from_fs())


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


def _syslog_config_key(workflow_id: str) -> str:
    return f"{WORKFLOW_SYSLOG_CONFIG_PREFIX}{workflow_id}"


async def _read_legacy_trigger_defs(workflow_id: str) -> List[TriggerDefinition]:
    triggers: List[TriggerDefinition] = []
    for key, converter in (
        (_kafka_config_key(workflow_id), legacy_kafka_trigger_from_config),
        (f"workflow_poller_config/{workflow_id}", legacy_schedule_trigger_from_config),
        (_syslog_config_key(workflow_id), legacy_syslog_trigger_from_config),
    ):
        try:
            config = await Storage.read(key)
        except Exception:
            config = None
        trigger = converter(config)
        if trigger is not None:
            triggers.append(trigger)
    return triggers


async def _get_workflow_trigger_defs(
    workflow_id: str,
    workflow_data: Optional[Dict[str, Any]] = None,
) -> List[TriggerDefinition]:
    data = workflow_data or _read_workflow_from_fs(workflow_id)
    if not data:
        return []
    workflow_json = data.get("workflowJson") or {}
    triggers = workflow_trigger_definitions_from_json(workflow_json)
    # Once the workflow JSON explicitly declares a trigger list, it becomes the
    # single source of truth, even when the list is empty.
    if workflow_json_declares_triggers(workflow_json):
        return triggers
    return await _read_legacy_trigger_defs(workflow_id)


def _trigger_to_api_dict(trigger: TriggerDefinition) -> Dict[str, Any]:
    return trigger.model_dump(mode="json", by_alias=True, exclude_none=True)


def _drop_none_values(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _normalized_config_key(key: Any) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


def _sanitize_workflow_config_secrets(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_workflow_config_secrets(item) for item in value]
    if not isinstance(value, dict):
        return value

    sanitized: Dict[str, Any] = {}
    for key, nested in value.items():
        normalized_key = _normalized_config_key(key)
        is_secret_key = (
            normalized_key in _WORKFLOW_CONFIG_SECRET_KEYS
            or normalized_key.endswith(("apikey", "password", "token", "secret"))
        ) and normalized_key not in _WORKFLOW_CONFIG_SECRET_REF_KEYS
        if is_secret_key:
            if nested not in (None, ""):
                configured_key = "apiKeyConfigured" if normalized_key == "apikey" else f"{key}Configured"
                sanitized[configured_key] = True
            continue
        sanitized[str(key)] = _sanitize_workflow_config_secrets(nested)
    return sanitized


def _normalize_workflow_integration_config_template(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be a JSON object")

    payload = _sanitize_workflow_config_secrets(config)
    payload.pop("runtime", None)

    kind = payload.get("kind", _WORKFLOW_INTEGRATION_CONFIG_KIND)
    if kind != _WORKFLOW_INTEGRATION_CONFIG_KIND:
        raise HTTPException(
            status_code=422,
            detail=f"config.kind must be {_WORKFLOW_INTEGRATION_CONFIG_KIND}",
        )
    payload["kind"] = _WORKFLOW_INTEGRATION_CONFIG_KIND

    version = payload.get("version", _WORKFLOW_INTEGRATION_CONFIG_VERSION)
    if not isinstance(version, int):
        raise HTTPException(status_code=422, detail="config.version must be an integer")
    payload["version"] = version

    workflow = payload.get("workflow") or {}
    if not isinstance(workflow, dict):
        raise HTTPException(status_code=422, detail="config.workflow must be an object")
    if workflow.get("id") not in (None, workflow_id):
        raise HTTPException(status_code=409, detail="config.workflow.id does not match the route workflow id")
    workflow["id"] = workflow_id
    workflow.setdefault("name", workflow_data.get("name") or workflow_id)
    if workflow_data.get("category") is not None:
        workflow.setdefault("category", workflow_data.get("category"))
    if workflow_data.get("source") is not None:
        workflow.setdefault("source", workflow_data.get("source"))
    payload["workflow"] = workflow

    publish = payload.get("publish", {})
    if publish is None:
        publish = {}
    if not isinstance(publish, dict):
        raise HTTPException(status_code=422, detail="config.publish must be an object")
    payload["publish"] = publish

    if "triggers" not in payload and isinstance(payload.get("integrations"), list):
        payload["triggers"] = payload["integrations"]
    triggers = payload.get("triggers", [])
    if triggers is None:
        triggers = []
    if not isinstance(triggers, list):
        raise HTTPException(status_code=422, detail="config.triggers must be an array")
    for index, trigger in enumerate(triggers):
        if not isinstance(trigger, dict):
            raise HTTPException(status_code=422, detail=f"config.triggers[{index}] must be an object")
        trigger_type = str(trigger.get("type") or "").strip()
        if not trigger_type:
            raise HTTPException(status_code=422, detail=f"config.triggers[{index}].type is required")
        normalized_type = trigger_type.lower().replace("-", "_")
        if normalized_type not in _WORKFLOW_CONFIG_TRIGGER_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported config trigger type: {trigger_type}",
            )
        trigger["type"] = normalized_type
    payload["triggers"] = triggers
    payload["updatedAt"] = int(time.time() * 1000)
    return payload


def _auth_for_config(auth: Optional[Any]) -> Optional[Dict[str, Any]]:
    if auth is None:
        return None
    if hasattr(auth, "model_dump"):
        auth_payload = auth.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(auth, dict):
        auth_payload = dict(auth)
    else:
        return None

    if "apiKey" in auth_payload:
        auth_payload.pop("apiKey", None)
        auth_payload["apiKeyConfigured"] = True
    return auth_payload


def _trigger_for_config(workflow_id: str, trigger: TriggerDefinition) -> Dict[str, Any]:
    payload = _trigger_to_api_dict(trigger)
    if trigger.auth is not None:
        payload["auth"] = _auth_for_config(trigger.auth)

    if trigger.type in ("webhook", "custom_webhook"):
        payload["invoke"] = {
            "method": str((trigger.source or {}).get("method") or "POST").upper(),
            "path": f"/webhook/workflows/{workflow_id}/{trigger.id}",
        }
    return payload


def _publish_for_config(service: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    service = service if isinstance(service, dict) else None
    status_value = str(service.get("status") or "stopped") if service else "stopped"
    return _drop_none_values(
        {
            "type": "api_service",
            "enabled": bool(service) and status_value not in {"stopped", "unpublished"},
            "status": status_value,
            "driver": service.get("driver") if service else None,
            "serviceUrl": service.get("serviceUrl") if service else None,
            "invokeUrl": service.get("invokeUrl") if service else None,
            "containerName": service.get("containerName") if service else None,
            "publishedAt": service.get("publishedAt") if service else None,
            "stoppedAt": service.get("stoppedAt") if service else None,
            "apiKeyConfigured": bool(service and service.get("apiKey")),
        }
    )


async def _build_workflow_integration_config(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    *,
    triggers: Optional[List[TriggerDefinition]] = None,
    service: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trigger_defs = triggers
    if trigger_defs is None:
        trigger_defs = await _get_workflow_trigger_defs(workflow_id, workflow_data)
    if service is None:
        service = await Storage.read(_api_service_key(workflow_id))
    now_ms = int(time.time() * 1000)
    return {
        "version": _WORKFLOW_INTEGRATION_CONFIG_VERSION,
        "kind": "workflow.integration-config",
        "workflow": _drop_none_values(
            {
                "id": workflow_id,
                "name": workflow_data.get("name") or workflow_id,
                "category": workflow_data.get("category"),
                "source": workflow_data.get("source"),
            }
        ),
        "updatedAt": now_ms,
        "publish": _publish_for_config(service),
        "triggers": [_trigger_for_config(workflow_id, trigger) for trigger in trigger_defs],
    }


async def _build_workflow_integration_runtime(
    workflow_id: str,
    workflow_data: Dict[str, Any],
) -> Dict[str, Any]:
    triggers = await _get_workflow_trigger_defs(workflow_id, workflow_data)
    service = await Storage.read(_api_service_key(workflow_id))
    statuses: Dict[str, Dict[str, Any]] = {}
    try:
        statuses = {
            item.get("triggerId"): item
            for item in await default_trigger_runtime.get_workflow_trigger_statuses(
                workflow_id,
                set_workflow_json_triggers(workflow_data.get("workflowJson") or {}, triggers),
            )
            if item.get("triggerId")
        }
    except Exception as exc:
        log.warning("workflow.config.runtime_status_failed", {
            "id": workflow_id,
            "error": str(exc),
        })

    return {
        "publish": _publish_for_config(service),
        "triggers": [
            {
                "trigger": _trigger_to_api_dict(trigger),
                "status": statuses.get(trigger.id),
            }
            for trigger in triggers
        ],
    }


async def _write_workflow_integration_config(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    *,
    triggers: Optional[List[TriggerDefinition]] = None,
    service: Optional[Dict[str, Any]] = None,
) -> tuple[Path, Dict[str, Any]]:
    config_dir = _workflow_config_dir(workflow_id, workflow_data)
    config_dir.mkdir(parents=True, exist_ok=True)
    config = await _build_workflow_integration_config(
        workflow_id,
        workflow_data,
        triggers=triggers,
        service=service,
    )
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path, config


async def _read_stored_workflow_integration_config(workflow_id: str) -> Optional[Dict[str, Any]]:
    stored = await Storage.read(_workflow_integration_config_key(workflow_id))
    return stored if isinstance(stored, dict) else None


async def _read_file_workflow_integration_config(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    config_path: Path,
) -> Optional[Dict[str, Any]]:
    if not config_path.is_file():
        return None
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise HTTPException(status_code=422, detail="workflow config file must contain a JSON object")
    return _normalize_workflow_integration_config_template(workflow_id, workflow_data, raw_config)


async def _load_workflow_integration_config_template(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    config_path: Path,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Load publish template from Storage first, then migrate legacy config.json."""
    stored = await _read_stored_workflow_integration_config(workflow_id)
    if stored is not None:
        return stored, "storage"

    file_config = await _read_file_workflow_integration_config(workflow_id, workflow_data, config_path)
    if file_config is not None:
        await Storage.write(_workflow_integration_config_key(workflow_id), file_config)
        log.info("workflow.config.migrated_from_file", {
            "id": workflow_id,
            "path": str(config_path),
            "storage_key": _workflow_integration_config_key(workflow_id),
        })
        return file_config, "file_migrated"

    return None, "missing"


def _replace_or_append_trigger(
    triggers: List[TriggerDefinition],
    trigger: TriggerDefinition,
) -> List[TriggerDefinition]:
    updated = [existing for existing in triggers if existing.id != trigger.id]
    updated.append(trigger)
    return updated


def _disable_legacy_trigger_of_type(
    workflow_id: str,
    trigger_type: str,
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    now_ms = int(time.time() * 1000)
    if trigger_type == "kafka":
        return (
            _kafka_config_key(workflow_id),
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
        )
    if trigger_type == "schedule":
        return (
            f"workflow_poller_config/{workflow_id}",
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
        )
    if trigger_type == "syslog":
        return (
            _syslog_config_key(workflow_id),
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
        )
    return None, None


async def _sync_trigger_legacy_state(workflow_id: str, trigger: TriggerDefinition) -> Optional[Dict[str, Any]]:
    if trigger.type == "kafka":
        config = kafka_trigger_to_legacy_config(workflow_id, trigger)
        await Storage.write(_kafka_config_key(workflow_id), config)
        from flocks.ingest.kafka.manager import default_manager as _kafka_default_manager

        return await _kafka_default_manager.restart_workflow(workflow_id)
    if trigger.type == "schedule":
        config = schedule_trigger_to_legacy_config(workflow_id, trigger)
        await Storage.write(f"workflow_poller_config/{workflow_id}", config)
        from flocks.workflow.poller_manager import default_manager as _poller_default_manager

        return await _poller_default_manager.restart_workflow(workflow_id)
    if trigger.type == "syslog":
        config = syslog_trigger_to_legacy_config(workflow_id, trigger)
        await Storage.write(_syslog_config_key(workflow_id), config)
        from flocks.ingest.syslog.manager import default_manager as _syslog_default_manager

        return await _syslog_default_manager.restart_workflow(workflow_id)
    return await default_trigger_runtime.get_trigger_status(workflow_id, trigger)


async def _remove_legacy_trigger_state(workflow_id: str, trigger: TriggerDefinition) -> None:
    """Remove legacy trigger configs so deleted unified triggers do not reappear."""
    if trigger.type == "kafka":
        try:
            from flocks.ingest.kafka.manager import default_manager as _kafka_default_manager

            await _kafka_default_manager.stop_workflow(workflow_id)
        except Exception:
            pass
        try:
            await Storage.remove(_kafka_config_key(workflow_id))
        except Storage.NotFoundError:
            pass
        return
    if trigger.type == "schedule":
        try:
            from flocks.workflow.poller_manager import default_manager as _poller_default_manager

            await _poller_default_manager.stop_workflow(workflow_id)
        except Exception:
            pass
        try:
            await Storage.remove(f"workflow_poller_config/{workflow_id}")
        except Storage.NotFoundError:
            pass
        return
    if trigger.type == "syslog":
        try:
            from flocks.ingest.syslog.manager import default_manager as _syslog_default_manager

            await _syslog_default_manager.stop_workflow(workflow_id)
        except Exception:
            pass
        try:
            await Storage.remove(_syslog_config_key(workflow_id))
        except Storage.NotFoundError:
            pass


async def _persist_workflow_triggers(
    workflow_id: str,
    workflow_data: Dict[str, Any],
    triggers: List[TriggerDefinition],
) -> Dict[str, Any]:
    workflow_json = workflow_data.get("workflowJson") or {}
    updated_json = set_workflow_json_triggers(workflow_json, triggers)
    data = dict(workflow_data)
    data["workflowJson"] = updated_json
    data["updatedAt"] = int(time.time() * 1000)
    is_global = data.get("source") == "global"
    _write_workflow_to_fs(
        workflow_id,
        updated_json,
        data,
        data.get("markdownContent"),
        global_store=is_global,
    )
    return data


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

    def _write_progress(update_fields: Dict[str, Any]) -> None:
        # Called from the workflow-engine worker thread on every step
        # start/complete.  Step events for a single execution are issued
        # serially by the engine, so no extra lock is needed beyond the
        # caller's invariant — but we must still tolerate transient
        # ``Storage.read`` failures (e.g. SQLite contention) without
        # corrupting ``current`` with a non-dict result.
        try:
            current = asyncio.run_coroutine_threadsafe(
                Storage.read(exec_key), loop
            ).result(timeout=5)
            if not isinstance(current, dict):
                # Execution record was trimmed mid-run or never persisted;
                # rebuild a minimal payload so the write still goes through.
                current = {"id": exec_id, "workflowId": workflow_id}
            current.update(update_fields)
            asyncio.run_coroutine_threadsafe(
                Storage.write(exec_key, current), loop
            ).result(timeout=5)
        except Exception as exc:
            log.warning("workflow.step_progress.write_failed", {
                "exec_id": exec_id,
                "error": str(exc),
            })

    def _on_step_start(_run_id, step_index, node, _inputs):
        _write_progress({
            "currentNodeId": getattr(node, "id", None),
            "currentNodeType": getattr(node, "type", None),
            "currentPhase": "running",
            "currentStepIndex": step_index,
        })
        return step_index

    def _on_step_complete(step_result) -> None:
        # Compact each step's outputs *before* appending so the running
        # ``step_history`` (and every subsequent ``_write_progress`` snapshot
        # that ships it to SQLite) stays bounded, even when a workflow node
        # returns tens of thousands of alerts that are already persisted to
        # JSONL on disk.
        step_dict = compact_step_for_storage(step_result.model_dump(mode="json"))
        step_history.append(step_dict)
        _write_progress({
            "executionLog": list(step_history),
            "currentNodeId": step_dict.get("node_id"),
            "currentNodeType": step_dict.get("node_type") or step_dict.get("type"),
            "currentPhase": "running",
            "currentStepIndex": len(step_history),
        })

    try:
        result: RunWorkflowResult = await asyncio.to_thread(
            run_workflow,
            workflow=workflow_json,
            inputs=req.inputs or {},
            timeout_s=req.timeout_s,
            trace=req.trace,
            on_step_start=_on_step_start,
            on_step_complete=_on_step_complete,
            cancel=cancel_event.is_set,
            tool_context=tool_context,
        )

        duration = time.time() - start_time
        current_data = await Storage.read(exec_key)
        if not isinstance(current_data, dict):
            # Defensive: the execution record could be missing if it was
            # trimmed/cleaned up mid-run.  Rebuild a baseline so the final
            # status write still succeeds rather than blowing up.
            current_data = {"id": exec_id, "workflowId": workflow_id}
        status_value, error_message = _resolve_execution_outcome(result)
        if cancel_event.is_set() and status_value == "success":
            status_value = "cancelled"
            error_message = error_message or f"Run cancelled: run_id={result.run_id or exec_id}"
        # ``result.history`` is the engine-side authoritative history (not
        # yet compacted), while ``step_history`` was already compacted in
        # ``_on_step_complete``.  Prefer the former when available, then
        # run it through ``compact_history_for_storage`` so the persisted
        # row stays small in either branch.
        current_data.update({
            "outputResults": compact_outputs_for_storage(result.outputs),
            "status": status_value,
            "finishedAt": int(time.time() * 1000),
            "duration": duration,
            "executionLog": compact_history_for_storage(result.history) or list(step_history),
            "errorMessage": error_message,
            "currentNodeId": result.last_node_id,
            "currentNodeType": current_data.get("currentNodeType"),
            "currentPhase": status_value,
            "currentStepIndex": result.steps,
        })

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
        if not isinstance(current_data, dict):
            current_data = {"id": exec_id, "workflowId": workflow_id}
        current_data.update({
            "status": "cancelled" if cancel_event.is_set() else "error",
            "finishedAt": int(time.time() * 1000),
            "duration": duration,
            "errorMessage": str(exc),
            "executionLog": list(step_history),
            "currentPhase": "cancelled" if cancel_event.is_set() else "error",
        })
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

        source = req.source or "global"
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

        _write_workflow_to_fs(workflow_id, req.workflow_json, meta, global_store=(source == "global"))

        stats = await _get_workflow_stats(workflow_id)
        data = {
            **meta,
            "workflowJson": req.workflow_json,
            "markdownContent": None,
            "editMarkdownContent": None,
            "stats": stats,
            "source": source,
        }

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
        if req.markdown_content is not None:
            markdown_content = req.markdown_content
        elif req.edit_markdown_content is not None:
            markdown_content = req.edit_markdown_content
        data["updatedAt"] = int(time.time() * 1000)

        is_global = data.get("source") == "global"
        _write_workflow_to_fs(
            workflow_id,
            workflow_json,
            data,
            markdown_content,
            global_store=is_global,
        )

        stats = await _get_workflow_stats(workflow_id)
        data["workflowJson"] = workflow_json
        data["markdownContent"] = markdown_content
        data["editMarkdownContent"] = markdown_content
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

        # Stop runtime resources before removing the filesystem source of truth.
        await _stop_workflow_runtime_resources(workflow_id)

        # Remove from filesystem (source of truth)
        _delete_workflow_from_fs(workflow_id)

        from flocks.hub import local as hub_local

        try:
            hub_local.remove_installed_record("workflow", workflow_id)
        except Exception as exc:
            log.warning("workflow.delete.hub_record_remove_failed", {"id": workflow_id, "error": str(exc)})

        await _cleanup_workflow_storage(workflow_id)

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

        exec_data = await create_execution_record(
            workflow_id,
            input_params=req.inputs or {},
        )
        exec_id = str(exec_data["id"])
        
        cancel_event = threading.Event()
        task = asyncio.create_task(
            _run_workflow_execution_task(
                workflow_id=workflow_id,
                workflow_json=workflow_json,
                req=req,
                exec_id=exec_id,
                cancel_event=cancel_event,
                tool_context=tool_context,
            ),
            name=f"workflow-run-{exec_id}",
        )
        _active_workflow_executions[exec_id] = ActiveWorkflowExecution(
            workflow_id=workflow_id,
            task=task,
            cancel_event=cancel_event,
        )
        # Guarantee cleanup of the registry entry even when the task is
        # cancelled or fails before reaching its own ``finally`` block (e.g.
        # if the event loop is shutting down).  This prevents the ``Active*``
        # map from growing forever when tasks are abandoned.
        def _cleanup_active(_t: asyncio.Task, _eid: str = exec_id) -> None:
            _active_workflow_executions.pop(_eid, None)
        task.add_done_callback(_cleanup_active)

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
        exec_data.update({
            "currentPhase": "cancelling",
            "errorMessage": exec_data.get("errorMessage") or "Cancellation requested",
        })
        await Storage.write(_workflow_execution_key(exec_id), exec_data)
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
async def workflow_center_publish(workflow_id: str, req: Optional[WorkflowCenterPublishRequest] = None):
    """Publish workflow as an API service."""
    try:
        result = await publish_workflow(
            workflow_id,
            image=req.image if req else None,
            driver=req.driver if req else None,
        )
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
    """Proxy invoke request to active published workflow service.

    Also records execution stats (callCount / successCount / errorCount) so
    that the UI invocation counter is updated for every published-service call,
    not just agent-driven /run calls.
    """
    started = time.time()
    exec_data = await create_execution_record(
        workflow_id,
        input_params=req.inputs or {},
    )
    exec_id = str(exec_data["id"])
    try:
        result = await invoke_published_workflow(
            workflow_id,
            inputs=req.inputs,
            timeout_s=req.timeout_s,
            request_id=req.request_id,
        )
        duration = time.time() - started
        raw_status = result.get("status", "SUCCEEDED") if isinstance(result, dict) else "SUCCEEDED"
        status_value = _normalize_execution_status(raw_status)
        success = status_value == "success"
        # workflow_center_invoke proxies to an external published service; no
        # step callbacks run locally so executionLog stays as the empty list
        # set by create_execution_record.  We still run compact_history here
        # as a forward-compatible guard in case a future code path populates it.
        exec_data.update({
            "outputResults": compact_outputs_for_storage(
                result.get("outputs", {}) if isinstance(result, dict) else {}
            ),
            "executionLog": compact_history_for_storage(exec_data.get("executionLog")),
            "status": status_value,
            "finishedAt": int(time.time() * 1000),
            "duration": duration,
            "currentPhase": status_value,
        })
        await _record_execution_result(workflow_id, exec_id, exec_data)
        return result
    except (WorkflowNotFoundError, WorkflowNotPublishedError) as e:
        duration = time.time() - started
        exec_data.update({"status": "error", "finishedAt": int(time.time() * 1000),
                          "duration": duration, "errorMessage": str(e)})
        await _record_execution_result(workflow_id, exec_id, exec_data)
        raise HTTPException(status_code=404, detail=str(e))
    except WorkflowCenterError as e:
        duration = time.time() - started
        exec_data.update({"status": "error", "finishedAt": int(time.time() * 1000),
                          "duration": duration, "errorMessage": str(e)})
        await _record_execution_result(workflow_id, exec_id, exec_data)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        duration = time.time() - started
        exec_data.update({"status": "error", "finishedAt": int(time.time() * 1000),
                          "duration": duration, "errorMessage": str(e)})
        await _record_execution_result(workflow_id, exec_id, exec_data)
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
    trigger_id: Optional[str] = Query(None, alias="triggerId"),
    trigger_type: Optional[str] = Query(None, alias="triggerType"),
):
    """
    Get workflow execution history

    Returns list of recent executions for this workflow.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        # 单次查询批量读取所有 execution 记录，避免 N 次单独 read 导致超长耗时
        all_entries = await Storage.list_entries("workflow_execution/")
        executions = []
        for _key, exec_data in all_entries:
            try:
                if not isinstance(exec_data, dict):
                    continue
                if exec_data.get("workflowId") != workflow_id:
                    continue
                if trigger_id and exec_data.get("triggerId") != trigger_id:
                    continue
                if trigger_type and exec_data.get("triggerType") != trigger_type:
                    continue
                executions.append(WorkflowExecutionResponse(**exec_data))
            except Exception as e:
                log.warning("workflow.history.skip", {"key": _key, "error": str(e)})
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

        _write_workflow_to_fs(workflow_id, workflow_json, meta, global_store=True)

        stats = await _get_workflow_stats(workflow_id)
        data = {
            **meta,
            "workflowJson": workflow_json,
            "markdownContent": None,
            "editMarkdownContent": None,
            "stats": stats,
            "source": "global",
        }

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
_KAFKA_CONFIG_PREFIX = WORKFLOW_KAFKA_CONFIG_PREFIX
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
    driver: Optional[Literal["local", "docker"]] = None


class KafkaConfigRequest(BaseModel):
    """Per-workflow Kafka consumer configuration."""

    enabled: bool = False
    inputBroker: Optional[str] = None
    inputTopic: Optional[str] = None
    inputGroupId: Optional[str] = None
    inputKey: str = "kafka_message"
    autoOffsetReset: str = "latest"
    inputs: Dict[str, Any] = Field(default_factory=dict)


def _strip_execution_only_comments(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_execution_only_comments(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_execution_only_comments(nested)
        for key, nested in value.items()
        if not str(key).startswith("_comment")
    }


class TriggerEventPayloadRequest(BaseModel):
    """Sample event payload for trigger preview/testing."""

    model_config = ConfigDict(populate_by_name=True)

    body: Any = None
    headers: Dict[str, Any] = Field(default_factory=dict)
    query: Dict[str, Any] = Field(default_factory=dict)
    path_params: Dict[str, Any] = Field(default_factory=dict, alias="pathParams")


class TriggerPreviewResponse(BaseModel):
    """Preview result for trigger mapping and filtering."""

    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    triggerId: str
    triggerType: str
    matched: bool
    inputs: Dict[str, Any] = Field(default_factory=dict)
    filterError: Optional[str] = None


class TriggerSaveResponse(BaseModel):
    """Persisted trigger definition with runtime status."""

    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    trigger: Dict[str, Any]
    status: Optional[Dict[str, Any]] = None


class WorkflowPollerConfigRequest(BaseModel):
    """Per-workflow background poller configuration."""

    enabled: bool = False
    intervalSeconds: int = Field(30, ge=1)
    timeoutSeconds: int = Field(7200, ge=1)
    noOverlap: bool = True
    inputs: Dict[str, Any] = Field(default_factory=dict)


class SyslogConfigRequest(BaseModel):
    """Per-workflow syslog listener configuration (experimental)."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    protocol: str = "udp"
    host: str = "0.0.0.0"
    port: int = Field(5140, ge=1, le=65535, description="Listener port (1-65535)")
    msg_format: str = Field("auto", alias="format")
    input_key: str = Field("syslog_message", alias="inputKey")


@router.post("/workflow/{workflow_id}/publish")
async def publish_workflow_as_api(
    workflow_id: str,
    req: Optional[WorkflowCenterPublishRequest] = None,
):
    """
    Publish workflow as an API service.

    Writes the workflow JSON to disk, registers it with the workflow center,
    starts the selected runtime, and returns the service URL and generated API key.
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

        # Use center.py to publish the selected runtime.
        active_record = await publish_workflow(
            workflow_id,
            image=req.image if req else None,
            driver=req.driver if req else None,
        )

        # Preserve existing API key across re-publishes so callers don't break
        existing_service = await Storage.read(_api_service_key(workflow_id)) or {}
        api_key = existing_service.get("apiKey") or (uuid.uuid4().hex + uuid.uuid4().hex)

        service_url = active_record.get("serviceUrl", "")
        invoke_url = f"{service_url}/invoke"
        container_name = active_record.get("containerName", "")
        driver = active_record.get("driver") or (req.driver if req else None)

        service_info = {
            "workflowId": workflow_id,
            "workflowName": data["name"],
            "serviceUrl": service_url,
            "invokeUrl": invoke_url,
            "apiKey": api_key,
            "status": "running",
            "publishedAt": now_ms,
            "containerName": container_name,
            "driver": driver,
        }
        await Storage.write(_api_service_key(workflow_id), service_info)

        log.info("workflow.api.published", {"id": workflow_id, "url": service_url})
        return service_info
    except HTTPException:
        raise
    except WorkflowCenterError as e:
        log.error("workflow.publish.center_error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"发布失败: {str(e)}")
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
        if isinstance(service, dict) and service.get("status") == "running":
            try:
                health = await get_workflow_health(workflow_id)
            except Exception:
                health = {}
            if health and not health.get("ok"):
                service["status"] = "error" if health.get("published") else "stopped"
                service["health"] = health
                await Storage.write(_api_service_key(workflow_id), service)
        return service  # None / null if not found
    except Exception as e:
        log.error("workflow.service.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get service info: {str(e)}")


@router.get("/workflow/{workflow_id}/config")
async def get_workflow_config(workflow_id: str):
    """Read workflow publish template from Storage, migrating config.json if needed."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    config_path = _workflow_config_dir(workflow_id, data) / "config.json"
    runtime = await _build_workflow_integration_runtime(workflow_id, data)
    try:
        config, source = await _load_workflow_integration_config_template(workflow_id, data, config_path)
        if config is not None:
            return {
                "exists": True,
                "path": str(config_path),
                "storageKey": _workflow_integration_config_key(workflow_id),
                "source": source,
                "config": config,
                "runtime": runtime,
            }

        config = await _build_workflow_integration_config(workflow_id, data)
        return {
            "exists": False,
            "path": str(config_path),
            "storageKey": _workflow_integration_config_key(workflow_id),
            "source": "generated",
            "config": config,
            "runtime": runtime,
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("workflow.config.get.error", {"id": workflow_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"Failed to read workflow config: {str(exc)}")


@router.put("/workflow/{workflow_id}/config")
async def update_workflow_config(
    workflow_id: str,
    config: Dict[str, Any] = Body(...),
):
    """Update the publish template in Storage without mutating runtime state."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    try:
        normalized_config = _normalize_workflow_integration_config_template(workflow_id, data, config)
        config_path = _workflow_config_dir(workflow_id, data) / "config.json"
        await Storage.write(_workflow_integration_config_key(workflow_id), normalized_config)
        log.info("workflow.config.updated", {
            "id": workflow_id,
            "storage_key": _workflow_integration_config_key(workflow_id),
        })
        return {
            "ok": True,
            "exists": True,
            "path": str(config_path),
            "storageKey": _workflow_integration_config_key(workflow_id),
            "source": "storage",
            "config": normalized_config,
            "runtime": await _build_workflow_integration_runtime(workflow_id, data),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("workflow.config.update.error", {"id": workflow_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"Failed to update workflow config: {str(exc)}")


@router.post("/workflow/{workflow_id}/config/sync")
async def sync_workflow_config(workflow_id: str):
    """Ensure a publish template exists in Storage, migrating config.json if present."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    try:
        config_path = _workflow_config_dir(workflow_id, data) / "config.json"
        config, source = await _load_workflow_integration_config_template(workflow_id, data, config_path)
        if config is not None:
            return {
                "ok": True,
                "path": str(config_path),
                "exists": True,
                "storageKey": _workflow_integration_config_key(workflow_id),
                "source": source,
                "config": config,
                "runtime": await _build_workflow_integration_runtime(workflow_id, data),
            }

        config = await _build_workflow_integration_config(workflow_id, data)
        await Storage.write(_workflow_integration_config_key(workflow_id), config)
        log.info("workflow.config.synced", {
            "id": workflow_id,
            "storage_key": _workflow_integration_config_key(workflow_id),
        })
        return {
            "ok": True,
            "path": str(config_path),
            "exists": True,
            "storageKey": _workflow_integration_config_key(workflow_id),
            "source": "storage",
            "config": config,
            "runtime": await _build_workflow_integration_runtime(workflow_id, data),
        }
    except Exception as exc:
        log.error("workflow.config.sync.error", {"id": workflow_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"Failed to sync workflow config: {str(exc)}")


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


def _find_trigger_or_404(triggers: List[TriggerDefinition], trigger_id: str) -> TriggerDefinition:
    trigger = next((item for item in triggers if item.id == trigger_id), None)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return trigger


def _validate_trigger_type_constraints(triggers: List[TriggerDefinition]) -> None:
    singleton_ids_by_type: Dict[str, List[str]] = {}
    for trigger in triggers:
        if trigger.type not in _LEGACY_SINGLETON_TRIGGER_TYPES:
            continue
        singleton_ids_by_type.setdefault(trigger.type, []).append(trigger.id or "")

    duplicates = {
        trigger_type: trigger_ids
        for trigger_type, trigger_ids in singleton_ids_by_type.items()
        if len(trigger_ids) > 1
    }
    if not duplicates:
        return

    first_type = sorted(duplicates)[0]
    trigger_ids = [trigger_id for trigger_id in duplicates[first_type] if trigger_id]
    detail = (
        f"Only one {first_type} trigger is supported per workflow; "
        f"found: {', '.join(trigger_ids) or 'multiple triggers'}"
    )
    raise HTTPException(status_code=409, detail=detail)


@router.get("/workflow/{workflow_id}/triggers")
async def list_workflow_triggers(workflow_id: str):
    """List unified triggers for a workflow with runtime status."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    triggers = await _get_workflow_trigger_defs(workflow_id, data)
    statuses = {
        item.get("triggerId"): item
        for item in await default_trigger_runtime.get_workflow_trigger_statuses(
            workflow_id,
            set_workflow_json_triggers(data.get("workflowJson") or {}, triggers),
        )
    }
    return [
        {
            "trigger": _trigger_to_api_dict(trigger),
            "status": statuses.get(trigger.id),
        }
        for trigger in triggers
    ]


@router.post("/workflow/{workflow_id}/triggers", response_model=TriggerSaveResponse)
async def create_workflow_trigger(workflow_id: str, trigger: TriggerDefinition):
    """Create or replace a unified trigger definition."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    existing = await _get_workflow_trigger_defs(workflow_id, data)
    updated = _replace_or_append_trigger(existing, trigger)
    _validate_trigger_type_constraints(updated)
    persisted = await _persist_workflow_triggers(workflow_id, data, updated)
    await default_trigger_runtime.restart_workflow(workflow_id, persisted.get("workflowJson") or {})
    status = await default_trigger_runtime.get_trigger_status(workflow_id, trigger)
    return TriggerSaveResponse(trigger=_trigger_to_api_dict(trigger), status=status)


@router.put("/workflow/{workflow_id}/triggers/{trigger_id}", response_model=TriggerSaveResponse)
async def update_workflow_trigger(workflow_id: str, trigger_id: str, trigger: TriggerDefinition):
    """Update a unified trigger definition."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    existing = await _get_workflow_trigger_defs(workflow_id, data)
    _find_trigger_or_404(existing, trigger_id)
    updated_trigger = trigger.model_copy(update={"id": trigger_id})
    updated = _replace_or_append_trigger(existing, updated_trigger)
    _validate_trigger_type_constraints(updated)
    persisted = await _persist_workflow_triggers(workflow_id, data, updated)
    await default_trigger_runtime.restart_workflow(workflow_id, persisted.get("workflowJson") or {})
    status = await default_trigger_runtime.get_trigger_status(workflow_id, updated_trigger)
    return TriggerSaveResponse(trigger=_trigger_to_api_dict(updated_trigger), status=status)


@router.delete("/workflow/{workflow_id}/triggers/{trigger_id}")
async def delete_workflow_trigger(workflow_id: str, trigger_id: str):
    """Delete a unified trigger definition."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    existing = await _get_workflow_trigger_defs(workflow_id, data)
    trigger = _find_trigger_or_404(existing, trigger_id)
    remaining = [item for item in existing if item.id != trigger_id]
    persisted = await _persist_workflow_triggers(workflow_id, data, remaining)
    await _remove_legacy_trigger_state(workflow_id, trigger)
    await default_trigger_runtime.restart_workflow(workflow_id, persisted.get("workflowJson") or {})
    return {"ok": True, "triggerId": trigger_id}


@router.get("/workflow/{workflow_id}/triggers/{trigger_id}/status")
async def get_workflow_trigger_status(workflow_id: str, trigger_id: str):
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    triggers = await _get_workflow_trigger_defs(workflow_id, data)
    trigger = _find_trigger_or_404(triggers, trigger_id)
    return await default_trigger_runtime.get_trigger_status(workflow_id, trigger)


@router.post("/workflow/{workflow_id}/triggers/{trigger_id}/preview-mapping", response_model=TriggerPreviewResponse)
async def preview_workflow_trigger_mapping(
    workflow_id: str,
    trigger_id: str,
    payload: TriggerEventPayloadRequest,
):
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    triggers = await _get_workflow_trigger_defs(workflow_id, data)
    trigger = _find_trigger_or_404(triggers, trigger_id)
    event = build_trigger_event(
        workflow_id=workflow_id,
        trigger=trigger,
        body=payload.body,
        headers=payload.headers,
        query=payload.query,
        path_params=payload.path_params,
    )
    matched, filter_error = evaluate_trigger_filter(trigger, event)
    return TriggerPreviewResponse(
        triggerId=trigger.id or trigger_id,
        triggerType=trigger.type,
        matched=matched,
        inputs=preview_trigger_mapping(trigger, event),
        filterError=filter_error,
    )


@router.post("/workflow/{workflow_id}/triggers/{trigger_id}/test")
async def test_workflow_trigger(
    workflow_id: str,
    trigger_id: str,
    payload: TriggerEventPayloadRequest,
):
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    workflow_json = data.get("workflowJson") or {}
    triggers = await _get_workflow_trigger_defs(workflow_id, data)
    trigger = _find_trigger_or_404(triggers, trigger_id)
    event = build_trigger_event(
        workflow_id=workflow_id,
        trigger=trigger,
        body=payload.body,
        headers=payload.headers,
        query=payload.query,
        path_params=payload.path_params,
    )
    result = await default_trigger_runtime.dispatch_event(
        workflow_id=workflow_id,
        workflow_json=workflow_json,
        trigger=trigger,
        event=event,
    )
    return {
        "ok": True,
        "trigger": _trigger_to_api_dict(trigger),
        **result,
    }


@router.get("/workflow-trigger-plugins")
async def list_workflow_trigger_plugins():
    return default_trigger_runtime.list_plugin_specs()


def _resolve_trigger_secret(secret_ref: Optional[str]) -> Optional[str]:
    if not secret_ref:
        return None
    try:
        from flocks.security import get_secret_manager

        return get_secret_manager().get(secret_ref)
    except Exception:
        return None


def _normalize_hmac_signature(signature: Optional[str]) -> Optional[str]:
    if not signature:
        return None
    value = signature.strip()
    if value.lower().startswith("sha256="):
        return value.split("=", 1)[1].strip()
    return value


def _authorize_webhook_trigger(
    trigger: TriggerDefinition,
    headers: Dict[str, str],
    query: Dict[str, str],
    *,
    raw_body: bytes,
) -> None:
    auth = trigger.auth
    if auth is None or auth.type in {"none", ""}:
        return
    if auth.type == "api_key":
        expected = auth.apiKey or _resolve_trigger_secret(auth.secretRef)
        if not expected:
            raise HTTPException(status_code=401, detail="Webhook trigger API key is not configured")
        header_name = (auth.headerName or "x-api-key").lower()
        actual = headers.get(header_name) or query.get(auth.queryParam or "api_key")
        if actual != expected:
            raise HTTPException(status_code=401, detail="Invalid webhook API key")
        return
    if auth.type == "hmac":
        expected = _resolve_trigger_secret(auth.secretRef)
        if not expected:
            raise HTTPException(status_code=401, detail="Webhook trigger secret is not configured")
        signature = _normalize_hmac_signature(headers.get((auth.headerName or "x-flocks-signature").lower()))
        expected_signature = hmac.new(
            expected.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        return
    raise HTTPException(status_code=400, detail=f"Unsupported webhook auth type: {auth.type}")


@webhook_router.post("/webhook/workflows/{workflow_id}/{trigger_id}")
async def invoke_workflow_webhook_trigger(
    workflow_id: str,
    trigger_id: str,
    request: Request,
):
    """Invoke a webhook/custom_webhook trigger and dispatch the workflow."""
    data = _read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    workflow_json = data.get("workflowJson") or {}
    triggers = await _get_workflow_trigger_defs(workflow_id, data)
    trigger = _find_trigger_or_404(triggers, trigger_id)
    if trigger.type not in {"webhook", "custom_webhook"}:
        raise HTTPException(status_code=400, detail=f"Trigger is not a webhook trigger: {trigger_id}")
    if not trigger.enabled:
        raise HTTPException(status_code=403, detail=f"Trigger is disabled: {trigger_id}")

    headers = {key.lower(): value for key, value in request.headers.items()}
    query = {key: value for key, value in request.query_params.items()}
    raw_body = await request.body()
    _authorize_webhook_trigger(trigger, headers, query, raw_body=raw_body)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        body = raw_body.decode("utf-8", errors="replace")

    event = build_trigger_event(
        workflow_id=workflow_id,
        trigger=trigger,
        body=body,
        headers=headers,
        query=query,
        path_params={"workflow_id": workflow_id, "trigger_id": trigger_id},
        raw=body,
        source=(trigger.source or {}).get("path") or str(request.url.path),
    )
    result = await default_trigger_runtime.dispatch_event(
        workflow_id=workflow_id,
        workflow_json=workflow_json,
        trigger=trigger,
        event=event,
    )
    return {
        "ok": True,
        "matched": result.get("matched", True),
        "executed": result.get("executed", False),
        "inputs": result.get("inputs", {}),
        "deliveryId": event.source.deliveryId,
    }


@router.post("/workflow/{workflow_id}/kafka-config")
async def save_kafka_config(workflow_id: str, req: KafkaConfigRequest):
    """
    Save Kafka input configuration for a workflow.

    When ``enabled`` is true this also (re)starts the Kafka consumer and blocks
    until it has either connected to the broker or failed.  Connection failures
    are surfaced as ``409 Conflict`` so the UI can show an actionable error
    instead of falsely claiming the consumer is running.
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        config = {
            "workflowId": workflow_id,
            "enabled": req.enabled,
            "inputBroker": req.inputBroker,
            "inputTopic": req.inputTopic,
            "inputGroupId": req.inputGroupId,
            "inputKey": req.inputKey,
            "autoOffsetReset": req.autoOffsetReset,
            "inputs": _strip_execution_only_comments(req.inputs),
            "updatedAt": int(time.time() * 1000),
        }
        await Storage.write(_kafka_config_key(workflow_id), config)
        unified_trigger = TriggerDefinition.model_validate(
            {
                "id": "kafka-default",
                "type": "kafka",
                "enabled": req.enabled,
                "source": {
                    "inputBroker": req.inputBroker or "",
                    "inputTopic": req.inputTopic or "",
                    "inputGroupId": req.inputGroupId or "",
                    "autoOffsetReset": req.autoOffsetReset,
                },
                "mapping": {
                    req.inputKey or "kafka_message": "$.body",
                },
                "inputs": _strip_execution_only_comments(req.inputs),
                "updatedAt": config["updatedAt"],
            }
        )
        triggers = await _get_workflow_trigger_defs(workflow_id, data)
        updated_triggers = _replace_or_append_trigger(triggers, unified_trigger)
        _validate_trigger_type_constraints(updated_triggers)
        await _persist_workflow_triggers(
            workflow_id,
            data,
            updated_triggers,
        )

        from flocks.ingest.kafka.manager import default_manager as _kafka_default_manager

        status = await _kafka_default_manager.restart_workflow(workflow_id)
        state = (status or {}).get("state")
        if req.enabled and state == "failed":
            err = (status or {}).get("error") or "consumer_connect_failed"
            raise HTTPException(
                status_code=409,
                detail=f"Kafka consumer failed to start: {err}",
            )
        return {"ok": True, "consumer": status}
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
        if config is None:
            data = _read_workflow_from_fs(workflow_id)
            if data:
                triggers = await _get_workflow_trigger_defs(workflow_id, data)
                trigger = next((item for item in triggers if item.type == "kafka"), None)
                if trigger is not None:
                    config = kafka_trigger_to_legacy_config(workflow_id, trigger)
        return config  # None / null if not configured
    except Exception as e:
        log.error("workflow.kafka_config.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get Kafka config: {str(e)}")


@router.get("/workflow/{workflow_id}/kafka-status")
async def get_kafka_status(workflow_id: str):
    """Return the *runtime* status of the Kafka consumer for a workflow.

    Reflects the actual connection state (connecting/running/failed/stopped) and
    queue depth so the UI can show whether a saved-but-not-yet-connected
    consumer is actually running.  The persisted config only captures *intent*.
    """
    try:
        from flocks.ingest.kafka.manager import default_manager as _kafka_default_manager

        return _kafka_default_manager.get_consumer_status(workflow_id)
    except Exception as e:
        log.error("workflow.kafka_status.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get Kafka status: {str(e)}")


@router.post("/workflow/{workflow_id}/poller-config")
async def save_workflow_poller_config(workflow_id: str, req: WorkflowPollerConfigRequest):
    """Save background poller configuration for a workflow."""
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        config = {
            "workflowId": workflow_id,
            "enabled": req.enabled,
            "intervalSeconds": req.intervalSeconds,
            "timeoutSeconds": req.timeoutSeconds,
            "noOverlap": req.noOverlap,
            "inputs": req.inputs,
            "updatedAt": int(time.time() * 1000),
        }
        await Storage.write(f"workflow_poller_config/{workflow_id}", config)
        unified_trigger = TriggerDefinition.model_validate(
            {
                "id": "schedule-default",
                "type": "schedule",
                "enabled": req.enabled,
                "source": {
                    "mode": "interval",
                    "intervalSeconds": req.intervalSeconds,
                },
                "runtime": {
                    "timeoutSeconds": req.timeoutSeconds,
                    "noOverlap": req.noOverlap,
                },
                "inputs": req.inputs,
                "updatedAt": config["updatedAt"],
            }
        )
        triggers = await _get_workflow_trigger_defs(workflow_id, data)
        updated_triggers = _replace_or_append_trigger(triggers, unified_trigger)
        _validate_trigger_type_constraints(updated_triggers)
        await _persist_workflow_triggers(
            workflow_id,
            data,
            updated_triggers,
        )

        from flocks.workflow.poller_manager import default_manager as _poller_default_manager

        poller_status = await _poller_default_manager.restart_workflow(workflow_id)
        if req.enabled and (poller_status or {}).get("state") == "failed":
            err = (poller_status or {}).get("error") or "poller_start_failed"
            raise HTTPException(
                status_code=409,
                detail=f"Workflow poller failed to start: {err}",
            )
        return {"ok": True, "status": poller_status}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.poller_config.save.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to save poller config: {str(e)}")


@router.get("/workflow/{workflow_id}/poller-config")
async def get_workflow_poller_config(workflow_id: str):
    """Get saved poller configuration for a workflow."""
    try:
        config = await Storage.read(f"workflow_poller_config/{workflow_id}")
        if config is None:
            data = _read_workflow_from_fs(workflow_id)
            if data:
                triggers = await _get_workflow_trigger_defs(workflow_id, data)
                trigger = next((item for item in triggers if item.type == "schedule"), None)
                if trigger is not None:
                    config = schedule_trigger_to_legacy_config(workflow_id, trigger)
        return config
    except Exception as e:
        log.error("workflow.poller_config.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get poller config: {str(e)}")


@router.get("/workflow/{workflow_id}/poller-status")
async def get_workflow_poller_status(workflow_id: str):
    """Return the runtime status of a workflow poller."""
    try:
        from flocks.workflow.poller_manager import default_manager as _poller_default_manager

        return _poller_default_manager.get_status(workflow_id)
    except Exception as e:
        log.error("workflow.poller_status.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get poller status: {str(e)}")


@router.post("/workflow/{workflow_id}/poller-run-once")
async def run_workflow_poller_once(workflow_id: str):
    """Trigger one immediate poller execution for a workflow."""
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        from flocks.workflow.poller_manager import default_manager as _poller_default_manager

        poller_status = await _poller_default_manager.run_once(workflow_id)
        return {"ok": True, "status": poller_status}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.poller_run_once.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to run workflow poller once: {str(e)}")


@router.post("/workflow/{workflow_id}/syslog-config")
async def save_syslog_config(workflow_id: str, req: SyslogConfigRequest):
    """
    Save syslog listener configuration for a workflow.

    When ``enabled`` is true, this also (re)starts the UDP/TCP listener and
    blocks until the underlying socket has either bound successfully or the
    bind has failed (e.g. ``EADDRINUSE``, invalid host).  Bind failures are
    surfaced as ``409 Conflict`` so the UI can show an actionable error
    instead of falsely claiming "Listening".
    """
    try:
        data = _read_workflow_from_fs(workflow_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

        config = {
            "workflowId": workflow_id,
            "enabled": req.enabled,
            "protocol": req.protocol,
            "host": req.host,
            "port": req.port,
            "format": req.msg_format,
            "inputKey": req.input_key,
            "updatedAt": int(time.time() * 1000),
        }
        await Storage.write(_syslog_config_key(workflow_id), config)
        unified_trigger = TriggerDefinition.model_validate(
            {
                "id": "syslog-default",
                "type": "syslog",
                "enabled": req.enabled,
                "source": {
                    "protocol": req.protocol,
                    "host": req.host,
                    "port": req.port,
                    "format": req.msg_format,
                },
                "mapping": {
                    req.input_key or "syslog_message": "$.body",
                },
                "updatedAt": config["updatedAt"],
            }
        )
        triggers = await _get_workflow_trigger_defs(workflow_id, data)
        updated_triggers = _replace_or_append_trigger(triggers, unified_trigger)
        _validate_trigger_type_constraints(updated_triggers)
        await _persist_workflow_triggers(
            workflow_id,
            data,
            updated_triggers,
        )

        from flocks.ingest.syslog.manager import default_manager as _syslog_default_manager

        status = await _syslog_default_manager.restart_workflow(workflow_id)
        state = (status or {}).get("state")
        if req.enabled and state == "failed":
            err = (status or {}).get("error") or "listener_bind_failed"
            raise HTTPException(
                status_code=409,
                detail=f"Syslog listener failed to bind: {err}",
            )
        return {"ok": True, "listener": status}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.syslog_config.save.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to save syslog config: {str(e)}")


@router.get("/workflow/{workflow_id}/syslog-config")
async def get_syslog_config(workflow_id: str):
    """Get saved syslog configuration for a workflow."""
    try:
        config = await Storage.read(_syslog_config_key(workflow_id))
        if config is None:
            data = _read_workflow_from_fs(workflow_id)
            if data:
                triggers = await _get_workflow_trigger_defs(workflow_id, data)
                trigger = next((item for item in triggers if item.type == "syslog"), None)
                if trigger is not None:
                    config = syslog_trigger_to_legacy_config(workflow_id, trigger)
        return config
    except Exception as e:
        log.error("workflow.syslog_config.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get syslog config: {str(e)}")


@router.get("/workflow/{workflow_id}/syslog-status")
async def get_syslog_status(workflow_id: str):
    """Return the *runtime* status of the syslog listener for a workflow.

    This reflects the actual bind state (binding/listening/failed/stopped) and
    queue depth, so the UI can show whether a saved-but-not-yet-bound listener
    is actually running.  The persisted config (``/syslog-config``) only
    captures *intent*, which is why the UI must consult this endpoint to
    truthfully render "Listening".
    """
    try:
        from flocks.ingest.syslog.manager import default_manager as _syslog_default_manager

        return _syslog_default_manager.get_listener_status(workflow_id)
    except Exception as e:
        log.error("workflow.syslog_status.get.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get syslog status: {str(e)}")


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

        meta = {
            k: v
            for k, v in data.items()
            if k not in ("workflowJson", "markdownContent", "editMarkdownContent", "stats", "source")
        }
        meta["updatedAt"] = int(time.time() * 1000)
        markdown_content = data.get("markdownContent")
        is_global = data.get("source") == "global"
        _write_workflow_to_fs(
            workflow_id,
            workflow_json,
            meta,
            markdown_content,
            global_store=is_global,
        )

        log.info("workflow.sample_inputs.saved", {"id": workflow_id})
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("workflow.sample_inputs.save.error", {"id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to save sample inputs: {str(e)}")
