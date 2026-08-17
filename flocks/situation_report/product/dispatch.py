"""Narrow adapter between the ordinary prompt route and report runtime."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import HTTPException, status

from flocks.agent.registry import Agent
from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, AuthUser
from flocks.input.events import UserInputEvent
from flocks.session.execution_mode import SessionExecutionMode
from flocks.session.session import SessionInfo
from flocks.utils.log import Log

from .orchestrator import ALLOWED_PRODUCT_MODELS, PRODUCTION_AGENT, run_managed_report_turn
from .policy import decide_report_prompt
from .project_workspace import ReportProjectError, require_report_project


log = Log.create(service="situation-report-dispatch")


async def dispatch_product_prompt(
    *,
    session: SessionInfo,
    request: Any,
    event: UserInputEvent,
    current_user: AuthUser | None,
    working_directory: str,
    is_running: Callable[[str], bool],
    is_chain_active: Callable[[str], bool],
    set_chain_active: Callable[[str, bool], None],
    schedule_background: Callable[..., None],
    generic_runner: Callable[[str, SessionInfo, UserInputEvent, str], Awaitable[None]],
) -> dict[str, str]:
    """Validate and schedule one service-authenticated report turn."""

    session_id = session.id
    if current_user is None or current_user.id != API_TOKEN_SERVICE_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The production report Agent is restricted to the API-token service identity",
        )
    try:
        await require_report_project(session)
    except ReportProjectError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if request.execution_mode != SessionExecutionMode.BUILD:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Production report tasks only support build execution mode",
        )

    decision = decide_report_prompt(request.parts, session_id=session_id)
    if decision.kind == "execute":
        if await Agent.get(PRODUCTION_AGENT) is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Production situation-report Agent is unavailable",
            )
        if (
            request.model is not None
            and (
                request.model.providerID,
                request.model.modelID,
            )
            not in ALLOWED_PRODUCT_MODELS
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Production report tasks only support the configured report-generation models",
            )
    if is_running(session_id) or is_chain_active(session_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Another report task is already running for this Session",
        )

    async def run() -> None:
        try:
            await run_managed_report_turn(
                session=session,
                event=event,
                decision=decision,
                working_directory=working_directory,
                generic_runner=generic_runner,
            )
        except Exception as exc:
            log.error(
                "situation_report.turn.failed",
                {"sessionID": session_id, "error": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            set_chain_active(session_id, False)

    set_chain_active(session_id, True)
    schedule_background(
        run(),
        session_id=session_id,
        action="situation_report.prompt_async",
    )
    return {"status": "accepted", "sessionID": session_id}
