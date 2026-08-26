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
from flocks.situation_report.product.files import session_root
from flocks.situation_report.product.project_workspace import require_report_project
from flocks.situation_report.product.session_state import ensure_session_state
from flocks.situation_report.product.webui_debug import (
    build_webui_debug_synchronizer,
    is_webui_debug_session,
    publish_webui_debug_report,
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


def _debug_identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


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
    current_user = require_user(request)
    if current_user.role != "admin" or not webui_debug_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    session = await Session.get_by_id_unfiltered(session_id)
    if session is None or not is_webui_debug_session(session):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug report Session not found")
    if not SessionPolicy.can_write(session, current_user) or session.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Debug report Session is not writable")
    try:
        await require_report_project(session)
        state = ensure_session_state(session_id)
        current = dict(state.report_state or {})
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
