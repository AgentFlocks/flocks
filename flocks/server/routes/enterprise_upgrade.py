"""
Routes of the Flocks Pro page: where this open install stands, and the move to the enterprise
edition (flocks.updater.enterprise).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from flocks.server.auth import require_admin
from flocks.updater import enterprise

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
            await queue.put({"stage": "error", "message": str(exc)})
        except Exception as exc:  # reported, not raised into a stream already started
            enterprise.log.error("enterprise.upgrade_failed", {"error": str(exc)})
            await queue.put({"stage": "error", "message": f"升级没有完成：{exc}"})
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


@router.get("/progress", summary="上一次从页面启动的升级现在到哪了")
async def get_progress(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"upgrade": await asyncio.to_thread(enterprise.read_state)}
