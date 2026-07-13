"""
Update routes — check version and apply self-upgrade via SSE stream
"""

import asyncio
import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from flocks.server.auth import require_admin
from flocks.updater import check_update, perform_update, detect_deploy_mode
from flocks.updater.models import VersionInfo
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="update-routes")

_UPDATE_CHECK_CACHE_TTL_SECONDS = 600.0
_UPDATE_CHECK_ERROR_CACHE_TTL_SECONDS = 60.0
_update_check_cache: dict[tuple[str, str], tuple[float, VersionInfo]] = {}
_update_check_inflight: dict[tuple[str, str], asyncio.Task[VersionInfo]] = {}
_update_check_lock = asyncio.Lock()


def _update_cache_key(locale: str | None, edition: str) -> tuple[str, str]:
    return (locale or "", edition)


def clear_update_check_cache() -> None:
    _update_check_cache.clear()
    _update_check_inflight.clear()


async def _run_update_check_for_cache(
    key: tuple[str, str],
    *,
    locale: str | None,
    edition: Literal["flocks", "flockspro"],
) -> VersionInfo:
    current_task = asyncio.current_task()
    try:
        info = await check_update(locale=locale, force_console_manifest=(edition == "flockspro"))
        ttl = _UPDATE_CHECK_ERROR_CACHE_TTL_SECONDS if info.error else _UPDATE_CHECK_CACHE_TTL_SECONDS
        async with _update_check_lock:
            if _update_check_inflight.get(key) is current_task:
                _update_check_cache[key] = (time.monotonic() + ttl, info.model_copy(deep=True))
        return info
    finally:
        async with _update_check_lock:
            if _update_check_inflight.get(key) is current_task:
                _update_check_inflight.pop(key, None)


async def _check_update_cached(
    *,
    locale: str | None,
    edition: Literal["flocks", "flockspro"],
    force: bool,
) -> VersionInfo:
    key = _update_cache_key(locale, edition)
    now = time.monotonic()
    task: asyncio.Task[VersionInfo]
    async with _update_check_lock:
        if not force:
            cached = _update_check_cache.get(key)
            if cached and cached[0] > now:
                return cached[1].model_copy(deep=True)

        # A forced check bypasses only the completed-result cache. Reuse any
        # same-key request already reaching the upstream service so concurrent
        # manual refreshes cannot fan out into an update-check storm.
        existing_task = _update_check_inflight.get(key)
        if existing_task is None:
            task = asyncio.create_task(
                _run_update_check_for_cache(key, locale=locale, edition=edition)
            )
            _update_check_inflight[key] = task
        else:
            task = existing_task

    info = await asyncio.shield(task)
    return info.model_copy(deep=True)


@router.get(
    "/check",
    response_model=VersionInfo,
    summary="Check for new version",
)
async def check_version(
    request: Request,
    locale: str | None = Query(
        default=None,
        description="Optional UI locale hint used to choose region-appropriate upgrade mirrors.",
    ),
    edition: Literal["flocks", "flockspro"] = Query(
        default="flocks",
        description="Version channel to check. flockspro checks the Console Pro bundle manifest.",
    ),
    force: Annotated[bool, Query(
        description="Bypass the short server-side cache for an explicit manual check.",
    )] = False,
) -> VersionInfo:
    if edition == "flockspro":
        require_admin(request)
    return await _check_update_cached(locale=locale, edition=edition, force=force)


@router.post(
    "/apply",
    summary="Apply upgrade",
    description=(
        "Download the latest release source archive, back up the current "
        "version, replace source files, sync dependencies, and restart. "
        "Progress is streamed via SSE. "
        "If target_version is provided, upgrade directly to that version."
    ),
)
async def apply_update(
    request: Request,
    target_version: str | None = Query(
        default=None,
        description="Target version (e.g. 2026.03.24). Omit to auto-detect the latest.",
    ),
    locale: str | None = Query(
        default=None,
        description="Optional UI locale hint used to choose region-appropriate upgrade mirrors.",
    ),
    edition: Literal["flocks", "flockspro"] = Query(
        default="flocks",
        description="Version channel to apply. flockspro applies the Console Pro bundle.",
    ),
):
    """
    Stream upgrade progress as Server-Sent Events (text/event-stream).

    Each event is a JSON-serialised UpdateProgress object:
        data: {"stage": "fetching", "message": "...", "success": null}
    """

    require_admin(request)

    async def _error(msg: str):
        yield f"data: {json.dumps({'stage': 'error', 'message': msg, 'success': False})}\n\n"

    if detect_deploy_mode() == "docker":
        return StreamingResponse(
            _error(
                "In-place upgrade is not supported in Docker deployments. "
                "Please pull the latest image and restart the container."
            ),
            media_type="text/event-stream",
        )

    zipball_url: str | None = None
    tarball_url: str | None = None
    bundle_sha256: str | None = None
    bundle_format: str | None = None

    if target_version:
        version_to_apply = target_version
    else:
        info = await check_update(locale=locale, force_console_manifest=(edition == "flockspro"))
        if info.error:
            return StreamingResponse(_error(info.error), media_type="text/event-stream")
        if not info.has_update or not info.latest_version:
            async def _no_update():
                yield f"data: {json.dumps({'stage': 'done', 'message': f'Already up to date v{info.current_version}', 'success': True})}\n\n"
            return StreamingResponse(_no_update(), media_type="text/event-stream")
        version_to_apply = info.latest_version
        zipball_url = info.zipball_url
        tarball_url = info.tarball_url
        bundle_sha256 = info.bundle_sha256
        bundle_format = info.bundle_format

    log.info("update.apply.start", {"target": version_to_apply})

    async def _stream():
        gen = perform_update(
            version_to_apply,
            zipball_url=zipball_url,
            tarball_url=tarball_url,
            bundle_sha256=bundle_sha256,
            bundle_format=bundle_format,
            locale=locale,
            force_console_manifest=(edition == "flockspro"),
        )
        try:
            async for progress in gen:
                yield f"data: {progress.model_dump_json()}\n\n"
                await asyncio.sleep(0)
        except (asyncio.CancelledError, GeneratorExit):
            log.warning("update.apply.stream_disconnected", {
                "target": version_to_apply,
            })
        except Exception as exc:
            log.error("update.apply.failed", {"error": str(exc)})
            yield f"data: {json.dumps({'stage': 'error', 'message': 'An unexpected error occurred during the upgrade. Please check the server logs for details.', 'success': False})}\n\n"
        finally:
            await gen.aclose()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
