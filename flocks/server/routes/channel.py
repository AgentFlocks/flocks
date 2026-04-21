"""
Channel HTTP routes: webhook callbacks, health/status, and outbound send APIs.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from flocks.channel.gateway.manager import default_manager
from flocks.channel.registry import default_registry
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="channel.routes")


class SendMessageRequest(BaseModel):
    channel_id: str
    to: str
    text: str
    account_id: Optional[str] = None
    media_url: Optional[str] = None
    reply_to_id: Optional[str] = None
    session_id: Optional[str] = None


class SessionSendRequest(BaseModel):
    session_id: str
    text: str
    channel_type: Optional[str] = None
    media_url: Optional[str] = None


@router.post("/send")
async def channel_send(req: SendMessageRequest):
    """向指定渠道的指定目标（chat_id / user_id）主动发送消息。"""
    from flocks.channel.base import OutboundContext
    from flocks.channel.outbound.deliver import OutboundDelivery

    out_ctx = OutboundContext(
        channel_id=req.channel_id,
        account_id=req.account_id,
        to=req.to,
        text=req.text,
        media_url=req.media_url,
        reply_to_id=req.reply_to_id,
    )
    results = await OutboundDelivery.deliver(out_ctx, session_id=req.session_id)
    failed = [r for r in results if not r.success]
    if failed:
        raise HTTPException(status_code=502, detail=failed[0].error)
    return {
        "ok": True,
        "message_ids": [r.message_id for r in results if r.message_id],
    }


@router.post("/session-send")
async def channel_session_send(req: SessionSendRequest):
    """通过 session_id 查找绑定的渠道，向该渠道发送消息。"""
    from flocks.channel.base import OutboundContext
    from flocks.channel.inbound.session_binding import SessionBindingService
    from flocks.channel.outbound.deliver import OutboundDelivery

    svc = SessionBindingService()
    all_bindings = await svc.list_bindings()
    matched = [b for b in all_bindings if b.session_id == req.session_id]

    if not matched:
        raise HTTPException(
            status_code=404,
            detail=f"未找到 session '{req.session_id}' 的渠道绑定",
        )

    if req.channel_type:
        matched = [b for b in matched if b.channel_id == req.channel_type]
        if not matched:
            raise HTTPException(
                status_code=404,
                detail=f"session '{req.session_id}' 未绑定渠道 '{req.channel_type}'",
            )

    all_results = []
    errors = []
    for binding in matched:
        out_ctx = OutboundContext(
            channel_id=binding.channel_id,
            account_id=binding.account_id,
            to=binding.chat_id,
            text=req.text,
            media_url=req.media_url,
        )
        results = await OutboundDelivery.deliver(out_ctx, session_id=req.session_id)
        all_results.extend(results)
        for r in results:
            if not r.success:
                errors.append(f"[{binding.channel_id}] {r.error}")

    if errors:
        raise HTTPException(status_code=502, detail="; ".join(errors))

    return {
        "ok": True,
        "message_ids": [r.message_id for r in all_results if r.message_id],
        "channels": list({b.channel_id for b in matched}),
    }


@router.post("/{channel_id}/webhook")
async def channel_webhook(channel_id: str, request: Request):
    """
    Receive a platform webhook callback.

    Platforms (Feishu, WeCom, …) POST events to this endpoint in
    webhook mode.  The plugin is responsible for URL verification,
    signature validation, and event parsing.
    """
    plugin = default_registry.get(channel_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")

    body = await request.body()
    headers = dict(request.headers)

    result = await plugin.handle_webhook(body, headers)
    if isinstance(result, dict) and isinstance(result.get("status_code"), int):
        status_code = int(result["status_code"])
        payload = {k: v for k, v in result.items() if k != "status_code"}
        return JSONResponse(status_code=status_code, content=payload)
    return result if result else {"ok": True}


@router.get("/status")
async def channel_status():
    """Return health status of all running channels."""
    statuses = default_manager.get_status()
    return {
        channel_id: status.to_dict()
        for channel_id, status in statuses.items()
    }


@router.get("/{channel_id}/status")
async def single_channel_status(channel_id: str):
    """Return health status of a single channel."""
    statuses = default_manager.get_status()
    status = statuses.get(channel_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not running")
    return {"channel_id": channel_id, **status.to_dict()}


@router.get("/list")
async def list_channels():
    """List all registered channel plugins."""
    default_registry.init()
    channels = default_registry.list_channels()
    return [
        {
            "id": ch.meta().id,
            "label": ch.meta().label,
            "aliases": ch.meta().aliases,
            "capabilities": {
                "chat_types": [ct.value for ct in ch.capabilities().chat_types],
                "media": ch.capabilities().media,
                "threads": ch.capabilities().threads,
                "reactions": ch.capabilities().reactions,
                "edit": ch.capabilities().edit,
                "rich_text": ch.capabilities().rich_text,
            },
            "running": default_manager.is_channel_running(ch.meta().id),
        }
        for ch in channels
    ]


@router.post("/{channel_id}/record-inbound")
async def record_inbound(channel_id: str):
    """Notify the gateway that a message was received on this channel.

    Used by out-of-process bridges (e.g. DingTalk's runner.ts) that bypass the
    InboundDispatcher so that last_message_at is updated on the plugin status.
    """
    default_manager.record_message(channel_id)
    return {"ok": True}


class BindSessionRequest(BaseModel):
    """Body for ``POST /api/channel/{channel_id}/bind``."""
    session_id: str
    chat_id: str
    chat_type: str = "direct"  # "direct" | "group"
    account_id: Optional[str] = "default"
    thread_id: Optional[str] = None
    agent_id: Optional[str] = None


@router.post("/{channel_id}/bind")
async def bind_session(channel_id: str, req: BindSessionRequest):
    """Register a (channel, conversation) → session mapping in ``channel_bindings``.

    For Feishu/WeCom/Telegram this row is written automatically inside
    ``InboundDispatcher`` → ``SessionBindingService.resolve_or_create``.  Out-
    of-process bridges (e.g. DingTalk's ``runner.ts``) create their Flocks
    session on their own and must call this endpoint after each session
    creation so that ``channel_message`` / ``POST /session-send`` can route
    outbound replies back.

    Idempotent — re-binding the same conversation key replaces the prior row.
    """
    from flocks.channel.base import ChatType
    from flocks.channel.inbound.session_binding import SessionBindingService

    # Conversation-level bindings only — CHANNEL-broadcast style targets
    # (e.g. Telegram channels) are not addressable by a single chat reply
    # and would never be a legitimate ``channel_message`` destination.
    if req.chat_type not in ("direct", "group"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chat_type '{req.chat_type}', expected 'direct' or 'group'",
        )
    chat_type = ChatType(req.chat_type)

    svc = SessionBindingService()
    try:
        binding = await svc.bind_session(
            session_id=req.session_id,
            channel_id=channel_id,
            account_id=req.account_id or "default",
            chat_id=req.chat_id,
            chat_type=chat_type,
            thread_id=req.thread_id,
            agent_id=req.agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "ok": True,
        "channel_id": binding.channel_id,
        "session_id": binding.session_id,
        "chat_id": binding.chat_id,
        "chat_type": binding.chat_type.value,
    }


@router.post("/{channel_id}/restart")
async def restart_channel(channel_id: str):
    """Restart a single channel connection with the latest config.

    Fires the restart in the background and returns immediately so that
    long WebSocket disconnect sequences do not block the HTTP response.
    Stops the current long-connection (if any) and re-connects using the
    freshly saved configuration.
    """
    import asyncio

    plugin = default_registry.get(channel_id)
    if not plugin:
        raise HTTPException(
            status_code=404, detail=f"Channel '{channel_id}' not found"
        )

    asyncio.create_task(default_manager.restart_channel(channel_id))
    return {"ok": True, "channel_id": channel_id}


@router.post("/restart-all")
async def restart_all_channels():
    """Restart all enabled channel connections (background, returns immediately)."""
    import asyncio

    async def _do():
        await default_manager.stop_all()
        await default_manager.start_all()

    asyncio.create_task(_do())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Telegram pairing
# ---------------------------------------------------------------------------

class TelegramPairRequest(BaseModel):
    code: str


def _append_telegram_allow_from(user_id: str) -> None:
    """Atomically add *user_id* to channels.telegram.allowFrom in flocks.json.

    Creates the key (as an empty list) if it is absent, then appends the ID
    (deduplicated).  Uses ConfigWriter so the write is atomic and the cache is
    cleared automatically.
    """
    from flocks.config.config_writer import ConfigWriter  # local import — avoids circular deps

    data = ConfigWriter._read_raw()
    channels = data.setdefault("channels", {})
    telegram = channels.setdefault("telegram", {})

    allow_from: list = telegram.get("allowFrom", [])
    str_id = str(user_id)
    if str_id not in [str(x) for x in allow_from]:
        allow_from = list(allow_from) + [str_id]
    telegram["allowFrom"] = allow_from

    ConfigWriter._write_raw(data)
    log.info("telegram.pairing.config_saved", {"user_id": str_id})


@router.post("/telegram/pair")
async def telegram_pair(req: TelegramPairRequest):
    """
    Verify a Telegram pairing code.

    On success, the user_id is immediately persisted to channels.telegram.allowFrom
    in flocks.json, so no manual save is required in the UI.
    """
    plugin = default_registry.get("telegram")
    if not plugin:
        raise HTTPException(status_code=404, detail="Telegram channel plugin not loaded")

    # Duck-type access to pairing store (avoids importing the plugin module here)
    get_store = getattr(plugin, "get_pairing_store", None)
    if get_store is None:
        raise HTTPException(status_code=501, detail="Telegram plugin does not support pairing")

    store = get_store()
    entry = store.consume(req.code.strip().upper())
    if entry is None:
        raise HTTPException(status_code=400, detail="配对码无效或已过期")

    user_id = entry["user_id"]

    # Persist user_id to flocks.json immediately
    config_saved = False
    try:
        import asyncio
        await asyncio.get_event_loop().run_in_executor(None, _append_telegram_allow_from, user_id)
        config_saved = True
    except Exception as exc:
        log.warning("telegram.pairing.config_save_failed", {"error": str(exc)})

    # Send a confirmation message back to the Telegram user (best-effort, before restart)
    confirm = getattr(plugin, "confirm_pairing", None)
    if confirm is not None:
        import asyncio
        asyncio.create_task(confirm(entry))

    # Restart the channel so the new allowFrom takes effect immediately.
    # Schedule after a short delay so the confirmation message is sent first.
    if config_saved:
        import asyncio

        async def _delayed_restart() -> None:
            await asyncio.sleep(2)
            await default_manager.restart_channel("telegram")

        asyncio.create_task(_delayed_restart())
        log.info("telegram.pairing.channel_restart_scheduled", {"user_id": user_id})

    return {
        "ok": True,
        "user_id": user_id,
        "username": entry.get("username"),
    }
