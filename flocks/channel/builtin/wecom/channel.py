"""
WeCom (企业微信) ChannelPlugin implementation.

Uses the ``wecom-aibot-sdk`` package to maintain a WebSocket long-connection
to ``wss://openws.work.weixin.qq.com``.  The SDK handles authentication,
heartbeat keep-alive, and exponential-backoff reconnection internally; what it
does not recover from (refused credentials, the server dropping the connection
for a newer one) ends start() with a readable reason, which the gateway shows
in the channel status and retries with backoff.

Reference implementation:
    https://github.com/WecomTeam/wecom-openclaw-plugin
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

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
_LEADING_MENTION_RE = re.compile(r"^\s*(?:@\S+(?:\s+|$))+")
# Shown in error messages when no websocketUrl is configured; the SDK's own default.
_FALLBACK_WS_URL = "wss://openws.work.weixin.qq.com"
# The SDK reports a refused subscribe frame as a plain Exception starting with
# this text (wecom_aibot_sdk.ws ``_handle_frame``), and raises
# WSAuthFailureError once it gives up retrying.
_AUTH_FAILURE_PREFIX = "Authentication failed"
# Reason the SDK passes to "disconnected" when the server drops this connection
# because the same bot opened another one (its ``disconnected_event``).
_REPLACED_BY_NEW_CONNECTION_PREFIX = "New connection established"


def _is_auth_failure(error: BaseException) -> bool:
    return type(error).__name__ == "WSAuthFailureError" or str(error).startswith(_AUTH_FAILURE_PREFIX)


def _shown_ws_url(url: str) -> str:
    """The WebSocket address as status messages show it: scheme, host and port only.

    A custom websocketUrl (or the {env:…}/{secret:…} it came from) may carry a
    token in its user info, path or query, and the channel status is readable
    by every signed-in user.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "自定义地址"
    if not parts.scheme or not host:
        return "自定义地址"
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


# The code the SDK appends to a refused subscribe ("… (code: 40058)").
_AUTH_ERRCODE_RE = re.compile(r"\(code: (-?\d+)\)\s*$")
_SEE_SERVER_LOG = "详细报错见服务端日志 wecom.ws.error。"


def _error_facts(error: BaseException) -> str:
    """What the status may say about an SDK error: its type and numeric codes, never its text.

    The text can quote an address (a redirect target, an invalid URI) with the
    credentials it carries, or whatever a custom server sent back, and the
    channel status is readable by every signed-in user. The full error is
    logged on the server (``wecom.ws.error``) only.
    """
    facts = [f"错误类型 {type(error).__name__}"]
    errno = getattr(error, "errno", None)
    if isinstance(errno, int):
        facts.append(f"errno {errno}")
    # websockets' InvalidStatus carries the response; older InvalidStatusCode the code itself.
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is None:
        status = getattr(error, "status_code", None)
    if isinstance(status, int):
        facts.append(f"HTTP {status}")
    return "，".join(facts)


def _describe_sdk_error(error: BaseException, ws_url: str) -> str:
    """What the channel status shows for an SDK ``error`` event: the cause, then type and codes only."""
    if _is_auth_failure(error):
        match = _AUTH_ERRCODE_RE.search(str(error))
        facts = f"企业微信错误码 {match.group(1)}" if match else _error_facts(error)
        return (
            "企业微信机器人鉴权失败：请在企业微信管理后台核对智能机器人的 Bot ID 和 Secret，"
            "Secret 重置过的要在这里重新填写并保存。"
            f"{facts}，{_SEE_SERVER_LOG}"
        )
    return (
        f"连接企业微信长连接服务（{_shown_ws_url(ws_url)}）出错：请确认服务器能访问这个地址，"
        "需要走代理的检查代理设置。"
        f"{_error_facts(error)}，{_SEE_SERVER_LOG}"
    )


def _describe_replaced_connection(reason: str) -> Optional[str]:
    """The status message when the server dropped this connection for a newer one, else None."""
    if not reason.startswith(_REPLACED_BY_NEW_CONNECTION_PREFIX):
        return None
    return (
        "企业微信服务端断开了这个连接：同一个机器人在别处建立了新的长连接（一个 Bot ID 同时只能保持一个连接）。"
        "请停掉其他正在使用这个机器人的服务（比如升级前的旧版本），再点「重启连接」。"
    )


class _WeComSdkLogger:
    """Bridge wecom-aibot-sdk logs into Flocks logs while dropping heartbeat debug noise."""

    def debug(self, message: str, *args: Any) -> None:
        return None

    def info(self, message: str, *args: Any) -> None:
        return None

    def warn(self, message: str, *args: Any) -> None:
        log.warning("wecom.sdk.warn", {"message": _format_sdk_log_message(message, args)})

    def error(self, message: str, *args: Any) -> None:
        log.error("wecom.sdk.error", {"message": _format_sdk_log_message(message, args)})


def _format_sdk_log_message(message: str, args: tuple[Any, ...]) -> str:
    if not args:
        return str(message)
    extra = " ".join(str(arg) for arg in args)
    return f"{message} {extra}"


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
        self._ws_url = _FALLBACK_WS_URL
        # Set when the SDK will not recover on its own (refused credentials, a
        # newer connection took over): start() fails with this message so the
        # gateway shows it and retries with backoff.
        self._failure_event = asyncio.Event()
        self._failure_message = ""
        self._last_error_message: Optional[str] = None

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="wecom", label="企业微信",
            aliases=["wechat_work", "wxwork"], order=20,
        )

    def capabilities(self) -> ChannelCapabilities:
        # Connected only once WeCom accepts the bot's credentials
        # (_handle_authenticated), not as soon as start() is running.
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True, threads=False, reactions=False,
            edit=False, rich_text=True,
            self_managed_connection=True,
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
        self._failure_event = asyncio.Event()
        self._failure_message = ""
        self._last_error_message = None
        self._cancel_reconnect_watchdog()

        try:
            from wecom_aibot_sdk import WSClient
        except ImportError:
            raise RuntimeError(
                "wecom-aibot-sdk not installed. "
                "Run `pip install wecom-aibot-sdk` to enable WeCom channel."
            )

        ws_url = config.get("websocketUrl", "")
        self._ws_url = ws_url or _FALLBACK_WS_URL
        client = WSClient(
            bot_id=config["botId"],
            secret=config["secret"],
            **({"ws_url": ws_url} if ws_url else {}),
            max_reconnect_attempts=-1,
            heartbeat_interval=30_000,
            scene=1,            # SCENE_WECOM_OPENCLAW — 标识为 OpenClaw 连接，MCP 能力依赖此字段
            plug_version="1.0.0",  # 企微服务端用于下发 MCP Server URL 时的版本校验
            logger=_WeComSdkLogger(),
        )
        self._ws_client = client

        # Each handler knows which client raised the event: one that has been
        # replaced (a restart) must not change the status of the next one.
        client.on("authenticated", lambda: self._handle_authenticated(client=client))
        client.on("disconnected", lambda reason="": self._handle_disconnected(reason, client=client))
        client.on("reconnecting", lambda attempt=0: self._handle_reconnecting(attempt, client=client))
        client.on("error", lambda error: self._handle_error(error, client=client))

        handler = self._make_message_handler(on_message)
        # 监听通用 message 事件（SDK 对所有消息类型都会触发此事件）
        # 同时保留各类型事件作为备用，handler 内部有去重保护
        client.on("message", handler)
        for event in ("message.text", "message.image", "message.mixed",
                       "message.voice", "message.file"):
            client.on(event, handler)

        log.info("wecom.ws.connecting", {"bot_id": config["botId"]})
        await client.connect()

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
            message_id = _extract_sent_message_id(sent)
            if ctx.text:
                text_result = await self.send_text(
                    OutboundContext(**{**vars(ctx), "media_url": None})
                )
                if not text_result.success:
                    return DeliveryResult(
                        channel_id="wecom",
                        message_id=message_id,
                        chat_id=ctx.to,
                        success=False,
                        error=f"WeCom media sent but caption failed: {text_result.error}",
                        retryable=text_result.retryable,
                    )

            self.record_message()
            return DeliveryResult(
                channel_id="wecom",
                message_id=message_id,
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
        failure_waiter = asyncio.create_task(self._failure_event.wait())
        done, pending = await asyncio.wait(
            {abort_waiter, reconnect_waiter, failure_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if failure_waiter in done and self._failure_event.is_set():
            raise RuntimeError(self._failure_message)
        if reconnect_waiter in done and self._reconnect_timeout_event.is_set():
            raise RuntimeError(self._reconnect_timeout_message())

    def _reconnect_timeout_message(self) -> str:
        head = f"WeCom reconnect timed out after {self._reconnect_timeout_seconds:.1f}s"
        if self._last_error_message:
            return f"{head}。最近一次错误：{self._last_error_message}"
        return (
            f"{head}：断开后没能重新连上企业微信长连接服务（{_shown_ws_url(self._ws_url)}），"
            "请确认服务器能访问这个地址，需要走代理的检查代理设置。"
        )

    def _is_stale(self, client: Any) -> bool:
        """True for an event raised by a client this channel no longer runs."""
        return client is not None and client is not self._ws_client

    def _fail(self, message: str) -> None:
        """End this connection attempt: start() raises *message*, which the status shows."""
        self.mark_disconnected(message)
        self._failure_message = message
        self._failure_event.set()

    def _handle_authenticated(self, client: Any = None) -> None:
        if self._is_stale(client):
            return
        self.mark_connected()
        self._last_error_message = None
        self._reconnect_timeout_event.clear()
        self._cancel_reconnect_watchdog()
        log.info("wecom.ws.authenticated")

    def _handle_disconnected(self, reason: str, client: Any = None) -> None:
        if self._is_stale(client):
            return
        reason = str(reason)
        replaced = None if self._intentional_disconnect else _describe_replaced_connection(reason)
        if replaced:
            # The SDK stops reconnecting after this: the server would drop a new
            # connection again while the other one is up.
            log.warning("wecom.ws.replaced", {"reason": reason})
            self._fail(replaced)
            return
        self.mark_disconnected()
        log.warning("wecom.ws.disconnected", {"reason": reason})
        self._start_reconnect_watchdog(reason=f"disconnected:{reason}")

    def _handle_reconnecting(self, attempt: int, client: Any = None) -> None:
        if self._is_stale(client):
            return
        self.mark_disconnected()
        log.info("wecom.ws.reconnecting", {"attempt": attempt})
        self._start_reconnect_watchdog(reason=f"reconnecting:{attempt}")

    def _handle_error(self, error: Exception, client: Any = None) -> None:
        log.error("wecom.ws.error", {"error": str(error)})
        if self._is_stale(client) or self._intentional_disconnect:
            return
        message = _describe_sdk_error(error, self._ws_url)
        self._last_error_message = message
        if _is_auth_failure(error):
            # After a refused subscribe frame the SDK closes the socket itself,
            # and as a clean close ends its receive loop it never tries again.
            self._fail(message)
            return
        # Connection-level errors: the SDK keeps reconnecting; the status says why
        # until it is back (or the reconnect watchdog gives up).
        self.mark_disconnected(message)

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
        text = _strip_leading_mentions(text)

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


def _strip_leading_mentions(text: str) -> str:
    """Remove only the bot mention prefix from a WeCom group message."""
    return _LEADING_MENTION_RE.sub("", text, count=1).strip()


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
