"""Development-only WebUI helpers for managed situation-report Sessions."""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from flocks.server.auth import require_user
from flocks.session.policy import SessionPolicy
from flocks.session.session import Session
from flocks.situation_report.product.contracts import ReportAction, build_report_prompt_text
from flocks.situation_report.product.debug_datasets import (
    DebugDatasetError,
    list_debug_datasets,
    load_debug_dataset,
)
from flocks.situation_report.product.files import atomic_write_json, read_json, session_root
from flocks.situation_report.product.project_workspace import require_report_project
from flocks.situation_report.product.session_state import ensure_session_state
from flocks.situation_report.product.webui_debug import (
    build_webui_debug_synchronizer,
    is_webui_debug_session,
    publish_webui_debug_report,
    seed_webui_debug_dataset,
    webui_debug_enabled,
)


router = APIRouter()


class DebugPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["generate", "modify", "regenerate"]
    instruction: str = Field(min_length=1, max_length=20_000)
    language: Literal["zh-CN", "en-US"] = "zh-CN"


class DebugPromptResponse(BaseModel):
    sessionID: str
    agent: str
    operation: Literal["generate", "modify", "regenerate"]
    requestID: str
    generationID: str
    baseBackendReportVersion: int | None
    prompt: str
    displayText: str


class DebugSessionStateResponse(BaseModel):
    sessionID: str
    reportExists: bool
    allowedOperations: list[Literal["generate", "modify", "regenerate"]]
    datasetID: str | None = None
    language: Literal["zh-CN", "en-US"] | None = None


class DebugDatasetSummary(BaseModel):
    datasetID: str
    name: str
    description: str
    language: Literal["zh-CN", "en-US"]
    sourceSessionID: str | None
    capturedAt: str
    materialCount: int
    materialDetailCount: int
    sourceCounts: dict[str, int]


class DebugDatasetListResponse(BaseModel):
    items: list[DebugDatasetSummary]


class DebugDatasetBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetID: str = Field(min_length=1, max_length=128)


class DebugDatasetBindResponse(BaseModel):
    sessionID: str
    datasetID: str
    templateVersion: int
    materialVersion: int
    materialCount: int
    materialDetailCount: int
    language: Literal["zh-CN", "en-US"]


def _debug_identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


async def _require_debug_session(session_id: str, request: Request):
    current_user = require_user(request)
    if current_user.role != "admin" or not webui_debug_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    session = await Session.get_by_id_unfiltered(session_id)
    if session is None or not is_webui_debug_session(session):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug report Session not found")
    if not SessionPolicy.can_write(session, current_user) or session.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Debug report Session is not writable")
    return session


def _require_debug_admin(request: Request) -> None:
    current_user = require_user(request)
    if current_user.role != "admin" or not webui_debug_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get(
    "/debug/datasets",
    response_model=DebugDatasetListResponse,
    summary="List validated frozen situation-report debug datasets",
)
async def get_debug_datasets(request: Request) -> DebugDatasetListResponse:
    _require_debug_admin(request)
    try:
        manifests = list_debug_datasets()
    except DebugDatasetError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Frozen debug datasets are unavailable: {exc}",
        ) from exc
    return DebugDatasetListResponse(
        items=[
            DebugDatasetSummary(
                datasetID=value.dataset_id,
                name=value.name,
                description=value.description,
                language=value.language,
                sourceSessionID=value.source_session_id,
                capturedAt=value.captured_at,
                materialCount=value.material_count,
                materialDetailCount=value.material_detail_count,
                sourceCounts=value.source_counts,
            )
            for value in manifests
        ]
    )


@router.get(
    "/debug/session/{session_id}/state",
    response_model=DebugSessionStateResponse,
    summary="Read one development WebUI report Session state",
)
async def get_debug_session_state(
    session_id: str,
    request: Request,
) -> DebugSessionStateResponse:
    session = await _require_debug_session(session_id, request)
    try:
        await require_report_project(session)
        state = ensure_session_state(session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Debug report state is unavailable: {exc}",
        ) from exc

    current = dict(state.report_state or {})
    report_exists = bool(current.get("currentFlocksReportVersion"))
    binding_path = session_root(session_id) / "debug" / "dataset.json"
    binding = read_json(binding_path) if binding_path.is_file() else {}
    return DebugSessionStateResponse(
        sessionID=session_id,
        reportExists=report_exists,
        allowedOperations=(
            ["modify", "regenerate"]
            if report_exists
            else ["generate"]
        ),
        datasetID=binding.get("datasetID"),
        language=binding.get("language"),
    )


@router.post(
    "/debug/session/{session_id}/dataset",
    response_model=DebugDatasetBindResponse,
    summary="Bind one frozen dataset to a new development report Session",
)
async def bind_debug_dataset(
    session_id: str,
    body: DebugDatasetBindRequest,
    request: Request,
) -> DebugDatasetBindResponse:
    session = await _require_debug_session(session_id, request)
    try:
        await require_report_project(session)
        state = ensure_session_state(session_id)
        current = dict(state.report_state or {})
        root = session_root(session_id)
        binding_path = root / "debug" / "dataset.json"
        if binding_path.is_file():
            existing = read_json(binding_path)
            if existing.get("datasetID") != body.datasetID:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This debug Session is already bound to another frozen dataset",
                )
            return DebugDatasetBindResponse.model_validate(
                {**existing, "sessionID": session_id}
            )
        runs_dir = root / "runs"
        if current.get("currentFlocksReportVersion") or (
            runs_dir.is_dir() and any(runs_dir.iterdir())
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A frozen dataset can only be bound before the first report task",
            )
        dataset = load_debug_dataset(body.datasetID)
        installed = await seed_webui_debug_dataset(
            session_id=session_id,
            dataset=dataset,
        )
        atomic_write_json(binding_path, installed)
        return DebugDatasetBindResponse(
            sessionID=session_id,
            **installed,
        )
    except HTTPException:
        raise
    except DebugDatasetError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Frozen debug dataset is invalid: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Frozen debug dataset could not be installed: {exc}",
        ) from exc


@router.post(
    "/debug/session/{session_id}/prepare",
    response_model=DebugPromptResponse,
    summary="Prepare one development WebUI report prompt",
)
async def prepare_debug_prompt(
    session_id: str,
    body: DebugPromptRequest,
    request: Request,
) -> DebugPromptResponse:
    session = await _require_debug_session(session_id, request)
    try:
        await require_report_project(session)
        state = ensure_session_state(session_id)
        current = dict(state.report_state or {})
        if not (session_root(session_id) / "debug" / "dataset.json").is_file():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bind a validated frozen dataset before starting a report task",
            )
        synchronizer = build_webui_debug_synchronizer()
        latest = await synchronizer.get_latest(
            session_id=session_id,
            known_report_version=max(
                int(current.get("syncedBackendReportVersion") or 0),
                int(current.get("observedBackendReportVersion") or 0),
            ),
            known_template_version=int(current.get("templateVersion") or 0),
            known_material_version=int(current.get("materialVersion") or 0),
            request_id=_debug_identifier("reqcheck"),
        )
        has_local_report = bool(current.get("currentFlocksReportVersion"))
        if has_local_report and not latest.report.exists:
            output_path = session_root(session_id) / str(current.get("currentOutputPath") or "")
            await publish_webui_debug_report(session_id=session_id, report_path=output_path)
            latest = await synchronizer.get_latest(
                session_id=session_id,
                known_report_version=0,
                known_template_version=int(current.get("templateVersion") or 0),
                known_material_version=int(current.get("materialVersion") or 0),
                request_id=_debug_identifier("reqcheck"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Debug report resources are unavailable: {exc}",
        ) from exc

    if body.operation == "generate":
        if latest.report.exists or has_local_report:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The initial report already exists; use modify or regenerate",
            )
        base_version = None
    else:
        if not latest.report.exists or latest.report.version is None or not has_local_report:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Generate the initial report before modify or regenerate",
            )
        base_version = latest.report.version

    request_id = _debug_identifier("req")
    generation_id = _debug_identifier("gen")
    action = ReportAction(
        name=f"situation_report.{body.operation}",
        version="1",
        requestID=request_id,
        generationID=generation_id,
        baseBackendReportVersion=base_version,
        language=body.language if body.operation == "generate" else None,
    )
    display_text = body.instruction.strip()
    return DebugPromptResponse(
        sessionID=session_id,
        agent="situation-report-product",
        operation=body.operation,
        requestID=request_id,
        generationID=generation_id,
        baseBackendReportVersion=base_version,
        prompt=build_report_prompt_text(action=action, user_instruction=display_text),
        displayText=display_text,
    )
