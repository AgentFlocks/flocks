"""REA plugin routes: the unified status, the install conversation and the uninstall."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from flocks.rea import runtime, service
from flocks.rea.uninstaller import (
    ReaUninstaller,
    TasksRunning,
    kept_resources,
    related_tasks,
    removable_resources,
)
from flocks.server.auth import require_admin, require_user
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="rea-routes")


class InstallSessionRequest(BaseModel):
    sessionId: str = Field(..., min_length=1, max_length=128)


class UninstallRequest(BaseModel):
    # The user saw the running related tasks and agreed to stop them.
    stopTasks: bool = False


@router.get("/rea/status", summary="REA plugin status")
async def rea_status(request: Request, refresh: bool = Query(False)):
    user = require_user(request)
    return await service.build_status(user, refresh=refresh)


@router.post("/rea/install-session/reserve", summary="Open or create the REA install conversation")
async def rea_reserve_install_session(request: Request):
    """``open`` the account's recent install conversation, ``create`` one, or ``pending`` (another tab creates it)."""

    user = require_admin(request)
    looked_at = service._now_ms()
    live = await service.live_install_session(user)
    decision = await runtime.run_in_thread(service.reserve_install_session, user, live, looked_at=looked_at)
    return {"decision": decision, "sessionId": live if decision == "open" else None}


@router.post("/rea/install-session", summary="Record the conversation that installs REA")
async def rea_install_session(body: InstallSessionRequest, request: Request):
    """Lets the next click reopen this conversation instead of starting a second one."""

    from flocks.server.routes.session import _require_session_write_access
    from flocks.session.session import Session

    user = require_admin(request)
    session = await Session.get_by_id(body.sessionId)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _require_session_write_access(session, user)
    await runtime.run_in_thread(service.record_install_session, session.id, user)
    log.info("rea.install_session.recorded", {"session_id": session.id})
    return await service.build_status(user)


@router.get("/rea/uninstall/preview", summary="What an REA uninstall removes, keeps and stops")
async def rea_uninstall_preview(request: Request):
    require_admin(request)
    return {
        "state": service.install_state(),
        "removes": removable_resources(),
        "keeps": kept_resources(),
        "tasks": await related_tasks(),
    }


@router.post("/rea/uninstall", summary="Uninstall REA")
async def rea_uninstall(body: UninstallRequest, request: Request):
    """Starts the uninstall; 409 ``tasks_running`` while related work runs and ``stopTasks`` is not set."""

    user = require_admin(request)
    try:
        await ReaUninstaller.start(
            stop_tasks=body.stopTasks,
            user_id=getattr(user, "id", None),
            previous_state=service.install_state(),
        )
    except TasksRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "tasks_running",
                "message": "Related tasks are running; confirm stopping them to uninstall.",
                "tasks": exc.tasks,
            },
        ) from exc
    log.info("rea.uninstall.started", {"stop_tasks": body.stopTasks})
    return await service.build_status(user)
