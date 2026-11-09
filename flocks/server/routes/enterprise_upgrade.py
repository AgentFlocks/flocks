"""
Routes of the Flocks Pro page: where this open install stands, the application for a licence when
it has none it can go by and the sync of the one it has (flocks.updater.enterprise_application), and
the move to the enterprise edition (flocks.updater.enterprise).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from flocks.server.auth import require_admin
from flocks.updater import enterprise, enterprise_application
from flocks.updater.installation_status import scrub_credentials

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
# Upgrades whose page went away: held until they end (the page reads them from /progress).
_running: set[asyncio.Task] = set()


@router.get("", summary="这台机器和企业版：老企业版、怎么升级")
async def get_situation(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await asyncio.to_thread(enterprise.situation)


@router.post("/check", summary="向 Console 要这份授权的新企业版安装包")
async def check_offer(request: Request) -> dict[str, Any]:
    require_admin(request)
    if not enterprise.available():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这台机器已经是企业版")
    try:
        offer = await enterprise.fetch_offer()
    except enterprise.EnterpriseUpgradeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"offer": enterprise.offer_dict(offer)}


@router.post("/start", summary="下载、校验并运行新企业版安装包（SSE 进度）")
async def start_upgrade(request: Request) -> StreamingResponse:
    """Server-Sent Events, each a JSON object with a ``stage``: downloading (with received and
    total), downloaded, installing, then done (with ``state``) or error (with ``message``)."""
    require_admin(request)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    last_sent = 0.0

    async def progress(event: dict[str, Any]) -> None:
        nonlocal last_sent
        # A download reports every chunk; the page needs about one a second.
        now = time.monotonic()
        if event.get("stage") == "downloading" and "received" in event and now - last_sent < 1.0:
            return
        last_sent = now
        await queue.put(event)

    async def run() -> None:
        try:
            if not enterprise.available():
                raise enterprise.EnterpriseUpgradeError("这台机器已经是企业版")
            state = await enterprise.upgrade(progress)
            await queue.put({"stage": "done", "state": state})
        except enterprise.EnterpriseUpgradeError as exc:
            await queue.put({"stage": "error", "message": scrub_credentials(str(exc))})
        except Exception as exc:  # reported, not raised into a stream already started
            enterprise.log.error("enterprise.upgrade_failed", {"error": scrub_credentials(str(exc))})
            await queue.put({"stage": "error", "message": f"升级没有完成：{scrub_credentials(str(exc))}"})
        finally:
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(run())
        _running.add(task)
        task.add_done_callback(_running.discard)
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


class ApplicationForm(BaseModel):
    """The application form, checked as the open edition's page of 2026.10.19 checked it."""

    license_type: Literal["poc", "commercial"]
    company: str = Field(min_length=1, max_length=200)
    applicant_name: str = Field(min_length=1, max_length=100)
    applicant_email: str = Field(min_length=1, max_length=200)
    applicant_phone: str = Field(min_length=1, max_length=40)
    sales_rep_name: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("company", "applicant_name", "applicant_email", "applicant_phone", "sales_rep_name", "notes")
    @classmethod
    def strip(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @field_validator("company", "applicant_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("required")
        return value

    @field_validator("applicant_email")
    @classmethod
    def email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email")
        return value

    @field_validator("applicant_phone")
    @classmethod
    def phone(cls, value: str) -> str:
        digits = len(re.sub(r"\D", "", value))
        if (
            not re.fullmatch(r"[+\d\s()-]+", value)
            or value.count("+") > 1
            or ("+" in value and not value.startswith("+"))
            or not 6 <= digits <= 15
        ):
            raise ValueError("invalid phone")
        return value


# Console's application ids (req_..., or a uuid from before).
_REQUEST_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")


def _administrator(request: Request):
    """require_admin, run before the form is read: others learn nothing of what it checks."""
    return require_admin(request)


def _refused(exc: enterprise.EnterpriseUpgradeError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/applications", summary="这台机器发过的企业版授权申请（审核中的向 Console 刷新）")
async def list_applications(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await enterprise_application.applications()


@router.post("/applications", summary="申请企业版授权，交给 Console 审核")
async def submit_application(form: ApplicationForm, user=Depends(_administrator)) -> dict[str, Any]:
    try:
        return {"application": await enterprise_application.apply(form.model_dump(), user.username)}
    except enterprise.EnterpriseUpgradeError as exc:
        raise _refused(exc) from exc


@router.post("/applications/{request_id}/withdraw", summary="撤回还没启用的申请")
async def withdraw_application(request: Request, request_id: str = _REQUEST_ID) -> dict[str, Any]:
    require_admin(request)
    try:
        return {"application": await enterprise_application.withdraw(request_id)}
    except enterprise.EnterpriseUpgradeError as exc:
        raise _refused(exc) from exc


@router.post("/applications/{request_id}/take", summary="启用审核通过的授权：写进这台机器，之后可以升级到企业版")
async def take_licence(request: Request, request_id: str = _REQUEST_ID) -> dict[str, Any]:
    require_admin(request)
    try:
        application = await enterprise_application.take(request_id)
    except enterprise.EnterpriseUpgradeError as exc:
        raise _refused(exc) from exc
    return {"application": application, "situation": await asyncio.to_thread(enterprise.situation)}


@router.post("/licence/sync", summary="向 Console 同步这台机器的授权：续期、额度变更落到本机")
async def sync_licence(request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return await enterprise_application.sync_licence()
    except enterprise.EnterpriseUpgradeError as exc:
        raise _refused(exc) from exc


@router.get("/progress", summary="上一次从页面启动的升级现在到哪了")
async def get_progress(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"upgrade": await asyncio.to_thread(enterprise.read_state)}
