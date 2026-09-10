"""Authenticated HTTP adapter for the code-security audit service."""

from __future__ import annotations

import importlib
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from flocks.project.project import Project
from flocks.server.auth import require_admin, require_user


router = APIRouter(prefix="/code-security/v1")


def _batch_task(request: Request) -> Path:
    from flocks.security.batch import resolve_batch, resolve_task
    # Local CLI batches have no user ownership binding. Expose only to administrators.
    require_admin(request)
    try:
        return resolve_task(resolve_batch(request.path_params["batch_id"]), request.path_params["task_id"])
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(404, detail="Batch task not found") from exc


def _scope_links(value: Any, request: Request | None) -> Any:
    if request is None or not getattr(request, "path_params", {}).get("batch_id"):
        return value
    batch_id, task_id = request.path_params["batch_id"], request.path_params["task_id"]
    if isinstance(value, dict):
        return {key: _scope_links(item, request) for key, item in value.items()}
    if isinstance(value, list):
        return [_scope_links(item, request) for item in value]
    if isinstance(value, str) and value.startswith("/api/code-security/v1/scans/"):
        return value.replace("/v1/scans/", f"/v1/batches/{batch_id}/tasks/{task_id}/scans/", 1)
    if isinstance(value, str) and value.startswith("/contracts/webui/workspaces/code_security/"):
        return value + f"&batch_id={batch_id}&task_id={task_id}"
    return value


@router.get("/batches")
async def list_batches(request: Request):
    require_admin(request)
    from flocks.security.batch import registry_root, resolve_batch, read_json
    items = []
    for path in sorted(registry_root().glob("batch_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
        try:
            root = resolve_batch(path.stem)
            config = read_json(root / "batch.json")
            items.append({"batch_id": config["batch_id"], "created_at": config["created_at"],
                          "task_count": len(config["tasks"])})
        except (OSError, ValueError, KeyError):
            continue
    return {"items": items}


@router.get("/batch-records")
async def list_batch_records(request: Request):
    """Read only batch indexes; detail requests still use each task's isolated store."""
    require_admin(request)
    import asyncio
    from flocks.security.batch import registry_root, resolve_batch, read_json, batch_status

    def collect():
        items = []
        for path in registry_root().glob("batch_*.json"):
            try:
                root = resolve_batch(path.stem)
                config = read_json(root / "batch.json")
                for task in batch_status(root)["tasks"]:
                    status = task["status"]
                    items.append({
                        "scan_id": f"{config['batch_id']}:{task['task_id']}",
                        "batch_id": config["batch_id"], "task_id": task["task_id"],
                        "audit_scan_id": task.get("scan_id"),
                        "display_name": task["task_id"],
                        "lifecycle_status": {"pending": "preparing", "timed_out": "failed"}.get(status, status),
                        "dynamic_enabled": False, "created_at": config["created_at"],
                        "failure_summary": task.get("error") or task.get("cleanup_error"),
                    })
            except (OSError, ValueError, KeyError):
                continue
        return {"items": items}

    return await asyncio.to_thread(collect)


@router.get("/batches/{batch_id}")
async def get_batch(request: Request, batch_id: str):
    require_admin(request)
    from flocks.security.batch import batch_status, resolve_batch
    import asyncio
    try:
        return await asyncio.to_thread(batch_status, resolve_batch(batch_id))
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(404, detail="Batch not found") from exc


@router.post("/batches/{batch_id}/tasks/{task_id}/cancel")
async def cancel_batch_task(request: Request):
    from flocks.security.batch import request_cancel
    task_dir = _batch_task(request)
    request_cancel(task_dir)
    return {"status": "cancellation_requested"}


class CreateScanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str = Field(alias="workspaceId")
    target_path: str = Field(".", alias="targetPath")
    scan_mode: str = Field("standard", alias="scanMode", pattern="^(standard|cybergym_level1)$")
    cybergym_manifest: dict[str, Any] | None = Field(None, alias="cybergymManifest")
    model: str | None = None
    include_paths: list[str] = Field(default_factory=lambda: ["."], alias="includePaths")
    exclude_patterns: list[str] = Field(default_factory=list, alias="excludePatterns")
    max_file_bytes: int | None = Field(None, alias="maxFileBytes", ge=1)
    max_total_bytes: int | None = Field(None, alias="maxTotalBytes", ge=1)
    copy_source: bool = Field(True, alias="copySource")
    cleanup_intermediates: bool = Field(False, alias="cleanupIntermediates", strict=True)
    dynamic_enabled: bool = Field(False, alias="dynamicEnabled")
    poc_enabled: bool = Field(False, alias="pocEnabled")
    dynamic_confirmed: bool = Field(False, alias="dynamicConfirmed")
    coverage_policy: str = Field(
        "evidence_backed_partial",
        alias="coveragePolicy",
        pattern="^(evidence_backed_partial|exhaustive)$",
    )
    verification_votes: int = Field(1, alias="verificationVotes", ge=1, le=5)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=256)


def _service_types(request: Request | None = None) -> tuple[Any, Any, Any, Any]:
    try:
        module = importlib.import_module("flocks_code_security.service")
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "code_security_unavailable", "message": "Code security plugin is unavailable"},
        ) from exc
    if request is not None and getattr(request, "path_params", {}).get("batch_id"):
        # Never switch process-wide environment or the plugin singleton for an HTTP request.
        from types import SimpleNamespace
        from flocks_code_security.store import ScanStore
        from flocks_code_security.source import AuditSourceRepository
        task_dir = _batch_task(request)
        database = task_dir / "data/code-security/data/code-security.db"
        if not database.is_file() or database.resolve() != database.absolute():
            raise HTTPException(404, detail={"code": "task_not_prepared", "message": "Task scan is not prepared yet"})
        store = ScanStore(database, read_only=True)
        service = module.AuditService(
            SimpleNamespace(store=store, source=AuditSourceRepository(store)), read_only=True,
        )
    else:
        service = module.get_audit_service()
    return (
        service,
        module.AuditCaller,
        module.StartScanRequest,
        module.AuditServiceError,
    )


def _caller(
    user: Any,
    *,
    source: str = "webui",
    workspace_ref: str | None = None,
    authorized_root: Path | None = None,
    caller_type: Any,
) -> Any:
    return caller_type(
        subject=str(user.id),
        source=source,
        is_admin=user.role == "admin",
        workspace_ref=workspace_ref,
        authorized_root=authorized_root,
    )


def _map_service_error(exc: Exception, service_error_type: type[Exception]) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    request_id = f"req_{uuid4().hex}"
    if isinstance(exc, service_error_type):
        return HTTPException(
            status_code=int(getattr(exc, "status_code", 400)),
            detail={
                "code": str(getattr(exc, "code", "audit_request_failed")),
                "message": str(exc),
                "requestId": request_id,
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "audit_request_failed",
            "message": "Code security request failed",
            "requestId": request_id,
        },
    )


def _web_detail(detail: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    """Adapt the shared snake-case service model to the WebUI DTO."""
    scan = detail["scan"]
    return _scope_links({
        "schemaVersion": detail["schema_version"],
        "scan": scan,
        "target": detail["target"],
        "timing": {
            "startedAt": scan["started_at"],
            "finishedAt": scan["finished_at"],
            "elapsedMs": scan["elapsed_ms"],
        },
        "counts": detail["counts"],
        "findingSummary": detail["finding_summary"],
        "coverageSummary": detail["coverage_summary"],
        "dynamicValidation": detail["dynamic_validation"],
        "cybergym": detail.get("cybergym"),
        "pocGeneration": detail.get("poc_generation"),
        "phaseRuns": detail["phase_runs"],
        "workers": detail["workers"],
        "artifacts": detail["artifacts"],
        "latestEventSeq": scan["latest_event_seq"],
        "serverTime": detail["server_time"],
        "workspaceUrl": detail["workspace_url"],
    }, request)


def _resolve_target(root: Path, relative_path: str) -> Path:
    value = (relative_path or ".").strip().replace("\\", "/")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsafe_target_scope", "message": "Target path must stay inside the selected workspace"},
        )
    target = (root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsafe_target_scope", "message": "Target path escaped the selected workspace"},
        ) from exc
    if not target.is_dir():
        raise HTTPException(
            status_code=400, detail={"code": "target_not_directory", "message": "Target path is not a directory"}
        )
    return target


@router.post("/scans")
async def create_scan(request: Request, payload: CreateScanRequest):
    user = require_admin(request)
    if payload.dynamic_enabled and not payload.dynamic_confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "dynamic_confirmation_required",
                "message": "Dynamic validation requires explicit confirmation",
                "requestId": f"req_{uuid4().hex}",
            },
        )
    project = await Project.get(payload.workspace_id, owner_id=user.id)
    if project is None or project.path_status != "available":
        raise HTTPException(
            status_code=404, detail={"code": "workspace_not_found", "message": "Workspace is unavailable"}
        )
    root = Path(project.worktree).expanduser().resolve()
    target = _resolve_target(root, payload.target_path)
    service, _AuditCaller, StartScanRequest, AuditServiceError = _service_types(request)
    try:
        detail = await service.start_scan(
            StartScanRequest(
                target_path=target,
                scan_mode=payload.scan_mode,
                cybergym_manifest=payload.cybergym_manifest,
                model=payload.model,
                include_paths=tuple(payload.include_paths),
                exclude_patterns=tuple(payload.exclude_patterns),
                max_file_bytes=payload.max_file_bytes,
                max_total_bytes=payload.max_total_bytes,
                copy_source=payload.copy_source,
                cleanup_intermediates=payload.cleanup_intermediates,
                dynamic_enabled=payload.dynamic_enabled,
                poc_enabled=payload.poc_enabled,
                coverage_policy=payload.coverage_policy,
                verification_votes=payload.verification_votes,
                idempotency_key=payload.idempotency_key,
            ),
            _caller(user, workspace_ref=project.id, authorized_root=root, caller_type=_AuditCaller),
        )
        return _web_detail(detail, request)
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/batches/{batch_id}/tasks/{task_id}/scans")
@router.get("/scans")
async def list_scans(
    request: Request,
    status_filter: list[str] | None = Query(None, alias="status"),
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    user = require_user(request)
    if getattr(request, "path_params", {}).get("batch_id"):
        task_dir = _batch_task(request)
        if not (task_dir / "data/code-security/data/code-security.db").is_file():
            return {"items": [], "next_cursor": None}
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        return await service.list_scans(
            _caller(user, caller_type=_AuditCaller),
            statuses=set(status_filter or []),
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}")
@router.get("/scans/{scan_id}")
async def get_scan(request: Request, scan_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        return _web_detail(await service.get_scan(scan_id, _caller(user, caller_type=_AuditCaller)), request)
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/events")
@router.get("/scans/{scan_id}/events")
async def get_events(
    request: Request,
    scan_id: str,
    after_seq: int = Query(0, ge=0),
    before_seq: int | None = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=200),
    recent: bool = False,
):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        return await service.list_events(
            scan_id,
            _caller(user, caller_type=_AuditCaller),
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
            recent=recent,
        )
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/phases")
@router.get("/scans/{scan_id}/phases")
async def get_phases(request: Request, scan_id: str):
    detail = await get_scan(request, scan_id)
    return {"items": detail["phaseRuns"], "serverTime": detail["serverTime"]}


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/artifacts")
@router.get("/scans/{scan_id}/artifacts")
async def get_artifacts(request: Request, scan_id: str):
    detail = await get_scan(request, scan_id)
    return {"items": detail["artifacts"]}


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/artifacts/{kind}")
@router.get("/scans/{scan_id}/artifacts/{kind}")
async def get_artifact(request: Request, scan_id: str, kind: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        return _scope_links(await service.get_artifact(scan_id, kind, _caller(user, caller_type=_AuditCaller)), request)
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/evidence/{evidence_id}")
@router.get("/scans/{scan_id}/evidence/{evidence_id}")
async def get_evidence(request: Request, scan_id: str, evidence_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        return await service.get_evidence(scan_id, evidence_id, _caller(user, caller_type=_AuditCaller))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.post("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/cancel")
@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(request: Request, scan_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        if getattr(request, "path_params", {}).get("batch_id"):
            from flocks.security.batch import request_cancel, read_json, task_running
            task_dir = _batch_task(request)
            current = read_json(task_dir / "current.json")
            if current.get("scan_id") != scan_id or not task_running(task_dir):
                raise HTTPException(409, detail="This task attempt is no longer running")
            request_cancel(task_dir)
            detail = await service.get_scan(scan_id, _caller(user, caller_type=_AuditCaller))
            detail["scan"]["can_cancel"] = False
            return _web_detail(detail, request)
        return _web_detail(await service.cancel_scan(scan_id, _caller(user, caller_type=_AuditCaller)), request)
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(request: Request, scan_id: str):
    user = require_admin(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        await service.delete_scan(scan_id, _caller(user, caller_type=_AuditCaller))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc
    return Response(status_code=204)


@router.get("/batches/{batch_id}/tasks/{task_id}/scans/{scan_id}/downloads/{artifact_name}")
@router.get("/scans/{scan_id}/downloads/{artifact_name}")
async def download_artifact(request: Request, scan_id: str, artifact_name: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types(request)
    try:
        filename, contents = await service.download_artifact(
            scan_id,
            artifact_name,
            _caller(user, caller_type=_AuditCaller),
        )
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc
    return Response(
        content=contents,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
