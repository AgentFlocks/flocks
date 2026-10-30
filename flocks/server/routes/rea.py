"""REA plugin routes: the unified status and the uninstall."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from flocks.rea import service
from flocks.rea.installer import ReaInstallError
from flocks.rea.uninstaller import (
    OperationRunning,
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


class UninstallRequest(BaseModel):
    # The user saw the running related tasks and agreed to stop them.
    stopTasks: bool = False


@router.get("/rea/status", summary="REA plugin status")
async def rea_status(request: Request, refresh: bool = Query(False)):
    user = require_user(request)
    return await service.build_status(user, refresh=refresh)


@router.get("/rea/uninstall/preview", summary="What an REA uninstall removes, keeps and stops")
async def rea_uninstall_preview(request: Request):
    require_admin(request)
    await service.sync_with_host()
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
    await service.sync_with_host()
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
    except OperationRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "operation_running",
                "message": "Another Flocks process on this host is installing or uninstalling REA; try again when it finished.",
            },
        ) from exc
    except ReaInstallError as exc:
        # A managed install: REA comes and goes with the deployment's release.
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    log.info("rea.uninstall.started", {"stop_tasks": body.stopTasks})
    return await service.build_status(user)
