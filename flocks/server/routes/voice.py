"""Authenticated voice input, available in the open-source runtime."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel, ConfigDict, SecretStr, StrictBool, ValidationError

from flocks.server.auth import apply_auth_for_request, clear_auth_context, require_admin, require_user
from flocks.voice import diagnostics
from flocks.voice.settings import MAX_CONFIG_BYTES, RESOURCE_ID, read_settings, update_settings
from flocks.voice.stream import serve

router = APIRouter(tags=["Voice"])


class VoiceSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr | None = None
    enabled: StrictBool | None = None
    hotwords: list[str] | None = None
    capture_mode: str | None = None
    recognition_scene: str | None = None


async def _public_status(*, admin: bool = False) -> dict:
    settings = await asyncio.to_thread(read_settings)
    return settings.public(admin=admin)


@router.get("/status")
async def voice_status(request: Request) -> dict:
    require_user(request)
    return await _public_status()


@router.get("/config")
@router.get("/settings", include_in_schema=False)
async def voice_settings(request: Request) -> dict:
    require_admin(request)
    return await _public_status(admin=True)


@router.put("/config")
@router.patch("/settings", include_in_schema=False)
async def patch_voice_settings(request: Request) -> dict:
    require_admin(request)
    # Core's generic validation handler logs invalid inputs. Parse this private
    # credential payload here so neither a 422 response nor its log can echo it.
    raw = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_CONFIG_BYTES:
                    raise HTTPException(status_code=413, detail="语音配置请求过大")
    except TimeoutError:
        raise HTTPException(status_code=408, detail="语音配置请求超时") from None
    try:
        payload = VoiceSettingsPayload.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="语音配置格式无效") from None
    changes = payload.model_dump(exclude_unset=True)
    if payload.api_key is not None:
        changes["api_key"] = payload.api_key.get_secret_value()
    try:
        settings = await asyncio.to_thread(update_settings, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="语音配置暂时无法保存，请稍后重试") from exc
    return settings.public(admin=True)


async def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        # Command-line bearer clients have no Origin; browser cookie sessions must.
        return not websocket.cookies
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        return False
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    if websocket.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        scheme = "https"
    if origin == f"{scheme}://{websocket.headers.get('host', '')}":
        return True
    # Match the same explicit origins used by core HTTP CORS, including dev WebUI.
    from flocks.server.app import _read_cors_config

    origins, _regex = await asyncio.to_thread(_read_cors_config)
    return origin in origins


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket) -> None:
    token = None
    try:
        try:
            _blocked, token, _user = await apply_auth_for_request(websocket)
            require_user(websocket)
            if not await _origin_allowed(websocket):
                raise HTTPException(status_code=403, detail="语音连接来源不允许")
            settings = await asyncio.to_thread(read_settings)
            if not settings.enabled or not settings.api_key:
                raise HTTPException(status_code=403, detail="请先配置并开启语音输入")
        except HTTPException as exc:
            await websocket.close(code=4401 if exc.status_code == 401 else 4403)
            return
        await websocket.accept()

        async def still_allowed() -> bool:
            current = await asyncio.to_thread(read_settings)
            return current.enabled and current.api_key == settings.api_key

        options: dict = {"hotwords": settings.hotwords, "context": settings.recognition_context}
        # Off unless an operator created the marker file on the server.
        recorder = await asyncio.to_thread(
            diagnostics.start_session, websocket.query_params, websocket.headers.get("user-agent"),
            {
                "resource_id": RESOURCE_ID, "capture_mode": settings.capture_mode,
                "recognition_scene": settings.recognition_scene,
                "hotwords": len(settings.hotwords), "context_chars": len(settings.recognition_context),
            },
        )
        if recorder is not None:
            options["diagnostics"] = recorder
        await serve(websocket, settings.api_key, authorize=still_allowed, **options)
    finally:
        if token is not None:
            clear_auth_context(token)
