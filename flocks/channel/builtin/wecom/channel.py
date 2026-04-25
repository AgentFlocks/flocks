"""
WeCom (企业微信) ChannelPlugin implementation.

Uses the ``wecom-aibot-sdk`` package to maintain a WebSocket long-connection
to ``wss://openws.work.weixin.qq.com``.  The SDK handles authentication,
heartbeat keep-alive, and exponential-backoff reconnection internally.

Reference implementation:
    https://github.com/WecomTeam/wecom-openclaw-plugin
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.utils.log import Log

log = Log.create(service="channel.wecom")

_FRAME_CACHE_MAX = 500
_DEFAULT_RECONNECT_TIMEOUT_SECONDS = 60.0


def _parse_reconnect_timeout_seconds(raw: Any) -> tuple[float, Optional[str]]:
    """Parse and validate the reconnect watchdog timeout."""
    if raw is None:
        return _DEFAULT_RECONNECT_TIMEOUT_SECONDS, None

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0, "reconnectTimeoutSeconds must be a positive number"

    if not math.isfinite(value) or value <= 0:
        return 0.0, "reconnectTimeoutSeconds must be a positive number"

    return value, None


class WeComChannel(ChannelPlugin):
    """WeCom channel plugin — WebSocket long-connection via ``wecom-aibot-sdk``."""

    def __init__(self) -> None:
        super().__init__()
        self._ws_client: Any = None
        self._frame_cache: OrderedDict[str, Any] = OrderedDict()
        self._intentional_disconnect = False
        self._reconnect_timeout_seconds = _DEFAULT_RECONNECT_TIMEOUT_SECONDS
        self._reconnect_timeout_event = asyncio.Event()
        self._reconnect_watchdog_task: asyncio.Task[None] | None = None

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="wecom", label="企业微信",
            aliases=["wechat_work", "wxwork"], order=20,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True, threads=False, reactions=False,
            edit=False, rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        for key in ("botId", "secret"):
            if not config.get(key):
                return f"Missing required config: {key}"
        reconnect_timeout_seconds, error = _parse_reconnect_timeout_seconds(
            config.get("reconnectTimeoutSeconds")
        )
        if error:
            return error
        if "reconnectTimeoutSeconds" in config:
            config["reconnectTimeoutSeconds"] = reconnect_timeout_seconds
        # WeCom only delivers @mentioned group messages; normalize legacy "all".
        if config.get("groupTrigger") == "all":
            config["groupTrigger"] = "mention"
        return None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def start(self, config, on_message, abort_event=None):
        """Connect to WeCom via WebSocket and block until *abort_event* fires."""
        self._config = config
        self._on_message = on_message
        self._intentional_disconnect = False
        self._reconnect_timeout_seconds, error = _parse_reconnect_timeout_seconds(
            config.get("reconnectTimeoutSeconds")
        )
        if error:
            raise ValueError(error)
        self._reconnect_timeout_event = asyncio.Event()
        self._cancel_reconnect_watchdog()

        try:
            from wecom_aibot_sdk import WSClient
        except ImportError:
            raise RuntimeError(
                "wecom-aibot-sdk not installed. "
                "Run `pip install wecom-aibot-sdk` to enable WeCom channel."
            )

        ws_url = config.get("websocketUrl", "")
        self._ws_client = WSClient(
            bot_id=config["botId"],
            secret=config["secret"],
            **({"ws_url": ws_url} if ws_url else {}),
            max_reconnect_attempts=-1,
            heartbeat_interval=30_000,
            scene=1,            # SCENE_WECOM_OPENCLAW — 标识为 OpenClaw 连接，MCP 能力依赖此字段
            plug_version="1.0.0",  # 企微服务端用于下发 MCP Server URL 时的版本校验
        )

        self._ws_client.on("authenticated", self._handle_authenticated)
        self._ws_client.on("disconnected", self._handle_disconnected)
        self._ws_client.on("reconnecting", self._handle_reconnecting)
        self._ws_client.on("error", self._handle_error)

        handler = self._make_message_handler(on_message)
        # 监听通用 message 事件（SDK 对所有消息类型都会触发此事件）
        # 同时保留各类型事件作为备用，handler 内部有去重保护
        self._ws_client.on("message", handler)
        for event in ("message.text", "message.image", "message.mixed",
                       "message.voice", "message.file"):
            self._ws_client.on(event, handler)

        log.info("wecom.ws.connecting", {"bot_id": config["botId"]})
        await self._ws_client.connect()

        try:
            await self._wait_until_stopped(abort_event)
        finally:
            await self._disconnect_ws_client()

    async def stop(self) -> None:
        await self._disconnect_ws_client()

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        if not self._ws_client:
            return DeliveryResult(
                channel_id="wecom", message_id="",
                success=False, error="WebSocket not connected",
            )

        try:
            from wecom_aibot_sdk import generate_req_id

            frame = (
                self._frame_cache.pop(ctx.reply_to_id, None)
                if ctx.reply_to_id else None
            )

            if frame:
                stream_id = generate_req_id("stream")
                await self._ws_client.reply_stream(
                    frame, stream_id, ctx.text, True,
                )
            else:
                await self._ws_client.send_message(ctx.to, {
                    "msgtype": "markdown",
                    "markdown": {"content": ctx.text},
                })

            self.record_message()
            return DeliveryResult(channel_id="wecom", message_id="")
        except Exception as e:
            retryable = "timeout" in str(e).lower()
            return DeliveryResult(
                channel_id="wecom", message_id="",
                success=False, error=str(e), retryable=retryable,
            )

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        if not self._ws_client:
            return DeliveryResult(
                channel_id="wecom", message_id="",
                success=False, error="WebSocket not connected",
            )
        if not ctx.media_url:
            return await self.send_text(ctx)

        try:
            from flocks.channel.builtin.wecom.media import prepare_wecom_media

            media = await prepare_wecom_media(ctx.media_url)
            upload = await self._ws_client.upload_media(
                media.data,
                type=media.media_type,
                filename=media.filename,
            )
            media_id = upload.get("media_id", "")
            if not media_id:
                raise RuntimeError(f"WeCom media upload failed: {upload}")

            frame = (
                self._frame_cache.pop(ctx.reply_to_id, None)
                if ctx.reply_to_id else None
            )
            if frame:
                sent = await self._ws_client.reply_media(
                    frame,
                    media.media_type,
                    media_id,
                    video_title=media.filename if media.media_type == "video" else None,
                )
            else:
                sent = await self._ws_client.send_media_message(
                    ctx.to,
                    media.media_type,
                    media_id,
                    video_title=media.filename if media.media_type == "video" else None,
                )

            # WeCom's media payload does not carry companion text. Keep the
            # attachment first, then send text as a separate message.
            if ctx.text:
                await self.send_text(OutboundContext(**{**vars(ctx), "media_url": None}))

            self.record_message()
            return DeliveryResult(
                channel_id="wecom",
                message_id=_extract_sent_message_id(sent),
                chat_id=ctx.to,
            )
        except Exception as e:
            retryable = "timeout" in str(e).lower() or "rate limit" in str(e).lower()
            return DeliveryResult(
                channel_id="wecom", message_id="",
                success=False, error=str(e), retryable=retryable,
            )

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        return text

    @property
    def text_chunk_limit(self) -> int:
        return self._config.get("textChunkLimit", 4000)

    @property
    def rate_limit(self) -> tuple[float, int]:
        rate = self._config.get("rateLimit", 20.0)
        burst = self._config.get("rateBurst", 5)
        return (float(rate), int(burst))

    # ------------------------------------------------------------------
    # Target resolution
    # ------------------------------------------------------------------

    def normalize_target(self, raw: str) -> Optional[str]:
        for prefix in ("user:", "group:"):
            if raw.startswith(prefix):
                return raw[len(prefix):]
        return raw

    def target_hint(self) -> str:
        return "user:<userid> 或 group:<chatid>"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_frame(self, msg_id: str, frame: Any) -> None:
        self._frame_cache[msg_id] = frame
        while len(self._frame_cache) > _FRAME_CACHE_MAX:
            self._frame_cache.popitem(last=False)

    async def _wait_until_stopped(
        self,
        abort_event: asyncio.Event | None,
    ) -> None:
        abort_waiter = asyncio.create_task(
            abort_event.wait() if abort_event else asyncio.Event().wait()
        )
        reconnect_waiter = asyncio.create_task(self._reconnect_timeout_event.wait())
        done, pending = await asyncio.wait(
            {abort_waiter, reconnect_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if reconnect_waiter in done and self._reconnect_timeout_event.is_set():
            raise RuntimeError(
                "WeCom reconnect timed out after "
                f"{self._reconnect_timeout_seconds:.1f}s"
            )

    def _handle_authenticated(self) -> None:
        self.mark_connected()
        self._reconnect_timeout_event.clear()
        self._cancel_reconnect_watchdog()
        log.info("wecom.ws.authenticated")

    def _handle_disconnected(self, reason: str) -> None:
        self.mark_disconnected()
        log.warning("wecom.ws.disconnected", {"reason": reason})
        self._start_reconnect_watchdog(reason=f"disconnected:{reason}")

    def _handle_reconnecting(self, attempt: int) -> None:
        self.mark_disconnected()
        log.info("wecom.ws.reconnecting", {"attempt": attempt})
        self._start_reconnect_watchdog(reason=f"reconnecting:{attempt}")

    def _handle_error(self, error: Exception) -> None:
        log.error("wecom.ws.error", {"error": str(error)})

    def _start_reconnect_watchdog(self, reason: str) -> None:
        if self._intentional_disconnect or self._ws_client is None:
            return
        if self._reconnect_timeout_event.is_set():
            return
        if self._reconnect_watchdog_task and not self._reconnect_watchdog_task.done():
            return
        self._reconnect_watchdog_task = asyncio.create_task(
            self._reconnect_watchdog(reason)
        )
        log.warning(
            "wecom.ws.reconnect_watchdog_started",
            {
                "reason": reason,
                "timeout_seconds": self._reconnect_timeout_seconds,
            },
        )

    async def _reconnect_watchdog(self, reason: str) -> None:
        try:
            await asyncio.sleep(self._reconnect_timeout_seconds)
        except asyncio.CancelledError:
            return

        self._reconnect_timeout_event.set()
        log.error(
            "wecom.ws.reconnect_watchdog_expired",
            {
                "reason": reason,
                "timeout_seconds": self._reconnect_timeout_seconds,
            },
        )

    def _cancel_reconnect_watchdog(self) -> None:
        if self._reconnect_watchdog_task and not self._reconnect_watchdog_task.done():
            self._reconnect_watchdog_task.cancel()
        self._reconnect_watchdog_task = None

    async def _disconnect_ws_client(self) -> None:
        ws_client = self._ws_client
        if ws_client is None:
            self._cancel_reconnect_watchdog()
            return

        self._intentional_disconnect = True
        try:
            self._cancel_reconnect_watchdog()
            try:
                await asyncio.wait_for(ws_client.disconnect(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass
        finally:
            if self._ws_client is ws_client:
                self._ws_client = None
            self._cancel_reconnect_watchdog()
            self._intentional_disconnect = False

    def _make_message_handler(
        self,
        on_message: Callable[[InboundMessage], Awaitable[None]],
    ):
        async def _handle(frame: dict) -> None:
            log.info("wecom.handler.received", {
                "keys": list(frame.keys()) if isinstance(frame, dict) else type(frame).__name__,
                "has_body": "body" in frame if isinstance(frame, dict) else False,
            })
            try:
                msg = _parse_frame(frame, self._config)
                if msg:
                    self._cache_frame(msg.message_id, frame)
                    log.info("wecom.handler.dispatching", {
                        "message_id": msg.message_id,
                        "sender": msg.sender_id,
                        "text_preview": (msg.text or "")[:50],
                    })
                    await on_message(msg)
                else:
                    log.warning("wecom.handler.parse_returned_none", {
                        "frame_keys": list(frame.keys()) if isinstance(frame, dict) else str(type(frame)),
                    })
            except Exception as e:
                log.error("wecom.handler.error", {"error": str(e)})

        def handler(frame: dict) -> None:
            """同步 handler 供 SDK emit 调用，内部调度异步逻辑到当前事件循环"""
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(_handle(frame))
                else:
                    loop.run_until_complete(_handle(frame))
            except Exception as e:
                log.error("wecom.handler.schedule_error", {"error": str(e)})

        return handler


# ------------------------------------------------------------------
# Frame parsing (matches openclaw plugin's message-parser.ts)
# ------------------------------------------------------------------

def _parse_frame(frame: dict, config: dict) -> Optional[InboundMessage]:
    """Convert a ``wecom-aibot-sdk`` frame into an ``InboundMessage``."""
    body = frame.get("body", {})
    msg_type = body.get("msgtype", "")

    if msg_type == "stream":
        return None

    text, media_url = _extract_content(body)

    if not text and not media_url:
        return None

    chat_type_raw = body.get("chattype", "single")
    chat_type = ChatType.GROUP if chat_type_raw == "group" else ChatType.DIRECT
    from_user = body.get("from", {}).get("userid", "")
    chat_id = body.get("chatid") or from_user

    if chat_type == ChatType.GROUP:
        text = re.sub(r"@\S+", "", text).strip()

    # WeCom platform only delivers group messages when the bot is @mentioned,
    # so every group message that reaches here is inherently a mention.
    return InboundMessage(
        channel_id="wecom",
        account_id=config.get("_account_id", "default"),
        message_id=body.get("msgid", ""),
        sender_id=from_user,
        chat_id=chat_id,
        chat_type=chat_type,
        text=text,
        media_url=media_url,
        mentioned=chat_type == ChatType.GROUP,
        raw=body,
    )


def _extract_sent_message_id(frame: Any) -> str:
    if not isinstance(frame, dict):
        return ""
    body = frame.get("body") or {}
    if not isinstance(body, dict):
        return ""
    return str(body.get("msgid") or body.get("message_id") or "")


def _extract_content(body: dict) -> tuple[str, Optional[str]]:
    """Extract ``(text, media_url)`` from the frame body."""
    msg_type = body.get("msgtype", "")

    if msg_type == "text":
        return body.get("text", {}).get("content", ""), None

    if msg_type == "image":
        url = body.get("image", {}).get("url", "")
        return "[图片消息]", url or None

    if msg_type == "voice":
        return body.get("voice", {}).get("content", "[语音消息]"), None

    if msg_type == "file":
        file_block = body.get("file", {})
        url = file_block.get("url", "")
        filename = str(file_block.get("filename", "") or "").strip()
        if filename:
            return f"[文件消息: {filename}]", url or None
        return "[文件消息]", url or None

    if msg_type == "mixed":
        text, media_url = _extract_mixed(body.get("mixed", {}))
        return text, media_url

    return "", None


def _extract_mixed(mixed: dict) -> tuple[str, Optional[str]]:
    """Flatten a mixed (图文混排) message into text + first media URL."""
    parts: list[str] = []
    first_media_url: Optional[str] = None
    for item in mixed.get("msg_item", []):
        item_type = item.get("msgtype", "")
        if item_type == "text":
            parts.append(item.get("text", {}).get("content", ""))
        elif item_type == "image":
            parts.append("[图片]")
            url = item.get("image", {}).get("url", "")
            if not first_media_url and url:
                first_media_url = url
        elif item_type == "file":
            file_block = item.get("file", {})
            filename = str(file_block.get("filename", "") or "").strip()
            parts.append(f"[文件: {filename}]" if filename else "[文件]")
            url = file_block.get("url", "")
            if not first_media_url and url:
                first_media_url = url
    return " ".join(parts).strip(), first_media_url
