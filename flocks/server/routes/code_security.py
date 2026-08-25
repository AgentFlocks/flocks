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


class CreateScanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str = Field(alias="workspaceId")
    target_path: str = Field(".", alias="targetPath")
    model: str | None = None
    include_paths: list[str] = Field(default_factory=lambda: ["."], alias="includePaths")
    exclude_patterns: list[str] = Field(default_factory=list, alias="excludePatterns")
    max_file_bytes: int = Field(1_048_576, alias="maxFileBytes", ge=1, le=50 * 1024 * 1024)
    dynamic_enabled: bool = Field(False, alias="dynamicEnabled")
    dynamic_confirmed: bool = Field(False, alias="dynamicConfirmed")
    coverage_policy: str = Field(
        "evidence_backed_partial",
        alias="coveragePolicy",
        pattern="^(evidence_backed_partial|exhaustive)$",
    )
    verification_votes: int = Field(1, alias="verificationVotes", ge=1, le=5)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=256)


def _service_types() -> tuple[Any, Any, Any, Any]:
    try:
        module = importlib.import_module("flocks_code_security.service")
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "code_security_unavailable", "message": "Code security plugin is unavailable"},
        ) from exc
    return (
        module.get_audit_service(),
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
) -> Any:
    _service, AuditCaller, _StartScanRequest, _AuditServiceError = _service_types()
    return AuditCaller(
        subject=str(user.id),
        source=source,
        is_admin=user.role == "admin",
        workspace_ref=workspace_ref,
        authorized_root=authorized_root,
    )


def _map_service_error(exc: Exception, service_error_type: type[Exception]) -> HTTPException:
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


def _web_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Adapt the shared snake-case service model to the WebUI DTO."""
    scan = detail["scan"]
    return {
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
        "phaseRuns": detail["phase_runs"],
        "workers": detail["workers"],
        "artifacts": detail["artifacts"],
        "latestEventSeq": scan["latest_event_seq"],
        "serverTime": detail["server_time"],
        "workspaceUrl": detail["workspace_url"],
    }


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
    service, _AuditCaller, StartScanRequest, AuditServiceError = _service_types()
    try:
        detail = await service.start_scan(
            StartScanRequest(
                target_path=target,
                model=payload.model,
                include_paths=tuple(payload.include_paths),
                exclude_patterns=tuple(payload.exclude_patterns),
                max_file_bytes=payload.max_file_bytes,
                dynamic_enabled=payload.dynamic_enabled,
                coverage_policy=payload.coverage_policy,
                verification_votes=payload.verification_votes,
                idempotency_key=payload.idempotency_key,
            ),
            _caller(user, workspace_ref=project.id, authorized_root=root),
        )
        return _web_detail(detail)
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/scans")
async def list_scans(
    request: Request,
    status_filter: list[str] | None = Query(None, alias="status"),
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return await service.list_scans(
            _caller(user),
            statuses=set(status_filter or []),
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/scans/{scan_id}")
async def get_scan(request: Request, scan_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return _web_detail(await service.get_scan(scan_id, _caller(user)))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


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
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return await service.list_events(
            scan_id,
            _caller(user),
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
            recent=recent,
        )
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/scans/{scan_id}/phases")
async def get_phases(request: Request, scan_id: str):
    detail = await get_scan(request, scan_id)
    return {"items": detail["phaseRuns"], "serverTime": detail["serverTime"]}


@router.get("/scans/{scan_id}/artifacts")
async def get_artifacts(request: Request, scan_id: str):
    detail = await get_scan(request, scan_id)
    return {"items": detail["artifacts"]}


@router.get("/scans/{scan_id}/artifacts/{kind}")
async def get_artifact(request: Request, scan_id: str, kind: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return await service.get_artifact(scan_id, kind, _caller(user))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.get("/scans/{scan_id}/evidence/{evidence_id}")
async def get_evidence(request: Request, scan_id: str, evidence_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return await service.get_evidence(scan_id, evidence_id, _caller(user))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(request: Request, scan_id: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        return _web_detail(await service.cancel_scan(scan_id, _caller(user)))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(request: Request, scan_id: str):
    user = require_admin(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        await service.delete_scan(scan_id, _caller(user))
    except Exception as exc:
        raise _map_service_error(exc, AuditServiceError) from exc
    return Response(status_code=204)


@router.get("/scans/{scan_id}/downloads/{artifact_name}")
async def download_artifact(request: Request, scan_id: str, artifact_name: str):
    user = require_user(request)
    service, _AuditCaller, _StartScanRequest, AuditServiceError = _service_types()
    try:
        filename, contents = await service.download_artifact(
            scan_id,
            artifact_name,
            _caller(user),
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
