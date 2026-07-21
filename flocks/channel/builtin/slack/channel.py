"""Slack ChannelPlugin using Socket Mode."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    NonRetryableChannelError,
    OutboundContext,
)
from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .config import (
    normalize_target,
    resolve_app_token,
    resolve_bot_token,
    resolve_home_channel,
    should_reply_broadcast,
    should_reply_in_thread,
)
from .format import markdown_to_slack_mrkdwn
from .inbound import build_inbound_message

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient

    SLACK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by validate_config tests
    AsyncApp = None  # type: ignore[assignment]
    AsyncSocketModeHandler = None  # type: ignore[assignment]
    AsyncWebClient = None  # type: ignore[assignment]
    SLACK_AVAILABLE = False


log = Log.create(service="channel.slack")

_KNOWN_THREADS_STORAGE_KEY = "channel:slack:known_threads"


_PERMANENT_SLACK_ERROR_CODES = {
    "invalid_auth",
    "not_authed",
    "no_auth",
    "token_revoked",
    "account_inactive",
    "not_allowed_token_type",
    "missing_scope",
}


def _is_token_reference(value: str) -> bool:
    return value.startswith("{secret:") or value.startswith("{env:")


def _slack_response_get(response: Any, key: str, default: str = "") -> str:
    getter = getattr(response, "get", None)
    if callable(getter):
        return str(getter(key, default) or default)
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get(key) or default)
    return default


def _slack_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get("error") or "").strip()
    if isinstance(response, dict):
        return str(response.get("error") or "").strip()

    message = str(exc).lower().replace("-", "_")
    for code in _PERMANENT_SLACK_ERROR_CODES:
        if code in message:
            return code
    if "invalid app token" in message:
        return "invalid_auth"
    return ""


def _is_permanent_slack_error(exc: Exception) -> bool:
    return _slack_error_code(exc) in _PERMANENT_SLACK_ERROR_CODES


def _format_slack_start_error(exc: Exception, *, phase: str) -> str:
    code = _slack_error_code(exc)
    detail = f"Slack error={code}" if code else str(exc)

    if phase == "bot_auth":
        if code == "missing_scope":
            return (
                "Slack Bot Token 权限不足：请在 Slack 开发者管理后台的 OAuth & Permissions "
                "确认 Bot Token 具备 Manifest 中的 bot scopes，重新安装 App 后复制 "
                "Bot User OAuth Token（xoxb- 开头）。"
            )
        return (
            "Slack Bot Token 验证失败：请在 Slack 开发者管理后台的 OAuth & Permissions "
            "复制 Bot User OAuth Token（xoxb- 开头），确认 App 已安装到 workspace 后重新保存。"
            f" 原始错误：{detail}"
        )

    if code == "missing_scope":
        return (
            "Slack App Token 权限不足：请在 Basic Information > App-Level Tokens "
            "创建或更新具备 connections:write scope 的 App-Level Token（xapp- 开头）。"
        )
    if code in {"invalid_auth", "not_authed", "no_auth", "token_revoked", "account_inactive", "not_allowed_token_type"}:
        return (
            "Slack App Token 验证失败：请在 Basic Information > App-Level Tokens "
            "复制 App-Level Token（xapp- 开头），并确认它包含 connections:write scope。"
            f" 原始错误：{detail}"
        )
    return f"Slack Socket Mode 连接失败：{detail}"


class SlackChannel(ChannelPlugin):
    """Slack bot channel — Socket Mode inbound and Web API outbound."""

    MAX_MESSAGE_LENGTH = 39000

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._handler: Any = None
        self._socket_task: Optional[asyncio.Task] = None
        self._bot_user_id: Optional[str] = None
        self._known_thread_ids: OrderedDict[str, None] = OrderedDict()
        self._known_thread_ids_max = 5000

    def meta(self) -> ChannelMeta:
        return ChannelMeta(id="slack", label="Slack", aliases=["sl"], order=50)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP, ChatType.CHANNEL],
            media=False,
            threads=True,
            reactions=False,
            edit=False,
            rich_text=True,
            self_managed_connection=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        if not SLACK_AVAILABLE:
            return "Missing Python dependency: slack-bolt"
        bot_token = resolve_bot_token(config)
        app_token = resolve_app_token(config)
        if not bot_token:
            return "Missing required config: botToken"
        if not app_token:
            return "Missing required config: appToken"
        if not _is_token_reference(bot_token) and not bot_token.startswith("xoxb-"):
            return (
                "Slack Bot Token 必须是 Bot User OAuth Token（xoxb- 开头），"
                "不能使用 User Token（xoxp-）或其他 token。"
            )
        if not _is_token_reference(app_token) and not app_token.startswith("xapp-"):
            return "Slack App Token 必须是 App-Level Token（xapp- 开头）。"
        return None

    @property
    def text_chunk_limit(self) -> int:
        return self.MAX_MESSAGE_LENGTH

    @property
    def rate_limit(self) -> tuple[float, int]:
        return (1.0, 5)

    def normalize_target(self, raw: str) -> Optional[str]:
        target = normalize_target(raw, fallback=resolve_home_channel(self._config))
        return target or None

    def target_hint(self) -> str:
        return "<channel_id|dm_id> (C..., G..., or D...)"

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        if format_hint == "plain":
            return text
        return markdown_to_slack_mrkdwn(text)

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message

        error = self.validate_config(config)
        if error:
            self.mark_disconnected(error)
            log.error("slack.start.invalid_config", {"error": error})
            raise NonRetryableChannelError(error)

        bot_token = resolve_bot_token(config)
        app_token = resolve_app_token(config)
        assert AsyncApp is not None
        assert AsyncSocketModeHandler is not None

        self._app = AsyncApp(token=bot_token)
        abort = abort_event or asyncio.Event()
        try:
            try:
                auth = await self._app.client.auth_test()
            except Exception as exc:
                message = _format_slack_start_error(exc, phase="bot_auth")
                self.mark_disconnected(message)
                if _is_permanent_slack_error(exc):
                    raise NonRetryableChannelError(message) from exc
                raise RuntimeError(message) from exc

            self._bot_user_id = _slack_response_get(auth, "user_id")
            if not _slack_response_get(auth, "bot_id"):
                message = (
                    "Slack Bot Token 验证失败：当前 token 认证为用户而不是 Bot。"
                    "请在 Slack 开发者管理后台 OAuth & Permissions 中复制 "
                    "Bot User OAuth Token（xoxb- 开头），不要使用 xoxp- User Token。"
                )
                self.mark_disconnected(message)
                raise NonRetryableChannelError(message)
            team_id = _slack_response_get(auth, "team_id", "default")
            await self._load_known_threads()

            self._register_handlers()
            await self._connect_socket_mode(app_token)
            log.info("slack.connected", {"team_id": team_id, "bot_user_id": self._bot_user_id})
            await abort.wait()
        except asyncio.CancelledError:
            raise
        except NonRetryableChannelError:
            raise
        except Exception as exc:
            message = str(exc)
            if self.status.connected or self.status.last_error != message:
                self.mark_disconnected(message)
            log.warning("slack.socket.stopped", {"error": message})
            raise
        finally:
            await self.stop()

    async def _connect_socket_mode(self, app_token: str) -> None:
        """Connect Socket Mode once and mark connected only after success."""
        assert AsyncSocketModeHandler is not None
        await self._verify_app_token(app_token)
        self._handler = AsyncSocketModeHandler(self._app, app_token)
        timeout_seconds = float(self._config.get("socketConnectTimeoutSeconds") or 15.0)
        try:
            await asyncio.wait_for(
                self._handler.connect_async(),
                timeout=max(timeout_seconds, 1.0),
            )
        except asyncio.TimeoutError as exc:
            message = f"Slack Socket Mode connection timed out after {timeout_seconds:.1f}s"
            self.mark_disconnected(message)
            raise TimeoutError(message) from exc
        except Exception as exc:
            message = _format_slack_start_error(exc, phase="socket_mode")
            self.mark_disconnected(message)
            if _is_permanent_slack_error(exc):
                raise NonRetryableChannelError(message) from exc
            raise RuntimeError(message) from exc
        self.mark_connected()

    async def _verify_app_token(self, app_token: str) -> None:
        """Validate the app-level token once before Socket Mode's retry loop."""
        assert AsyncWebClient is not None
        client = AsyncWebClient(token=app_token)
        try:
            await client.apps_connections_open()
        except Exception as exc:
            message = _format_slack_start_error(exc, phase="socket_mode")
            self.mark_disconnected(message)
            if _is_permanent_slack_error(exc):
                raise NonRetryableChannelError(message) from exc
            raise RuntimeError(message) from exc

    async def stop(self) -> None:
        handler = self._handler
        self._handler = None
        if handler is not None:
            try:
                await handler.close_async()
            except Exception as exc:
                log.warning("slack.close.failed", {"error": str(exc)})

        task = self._socket_task
        self._socket_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self.mark_disconnected()

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        if not self._app:
            return DeliveryResult(
                channel_id="slack",
                message_id="",
                success=False,
                error="Slack channel is not connected",
                retryable=True,
            )

        target = normalize_target(ctx.to, fallback=resolve_home_channel(self._config))
        if not target:
            return DeliveryResult(
                channel_id="slack",
                message_id="",
                success=False,
                error="Slack send requires 'to' (channel ID C..., private channel G..., or DM D...)",
            )

        try:
            payload: dict[str, Any] = {
                "channel": target,
                "text": ctx.text,
                "mrkdwn": ctx.format_hint != "plain",
            }
            thread_ts = self._resolve_thread_ts(ctx)
            if thread_ts:
                payload["thread_ts"] = thread_ts
                if should_reply_broadcast(self._config):
                    payload["reply_broadcast"] = True

            result = await self._app.client.chat_postMessage(**payload)
            message_id = str(result.get("ts") or "")
            if message_id:
                self._remember_thread(message_id)
            if thread_ts:
                self._remember_thread(thread_ts)
            await self._persist_known_threads()
            self.record_message()
            return DeliveryResult(
                channel_id="slack",
                message_id=message_id,
                chat_id=target,
                success=True,
            )
        except Exception as exc:
            message = str(exc)
            retryable = any(term in message.lower() for term in ("timeout", "rate", "temporarily", "connection"))
            return DeliveryResult(
                channel_id="slack",
                message_id="",
                success=False,
                error=f"Slack send failed: {message}",
                retryable=retryable,
            )

    def _register_handlers(self) -> None:
        if not self._app:
            return

        @self._app.event("message")
        async def handle_message_event(event, say):
            await self._handle_slack_event(event)

        @self._app.event("app_mention")
        async def handle_app_mention_event(event, say):
            await self._handle_slack_event(event)

    async def _handle_slack_event(self, event: dict[str, Any]) -> None:
        if not self._on_message:
            return
        inbound = build_inbound_message(
            event,
            bot_user_id=self._bot_user_id,
            config=self._config,
            known_thread_ids=set(self._known_thread_ids.keys()),
        )
        if inbound is None:
            return
        self.record_message()
        await self._on_message(inbound)

    def _resolve_thread_ts(self, ctx: OutboundContext) -> Optional[str]:
        if ctx.thread_id:
            return ctx.thread_id
        if should_reply_in_thread(self._config):
            return ctx.reply_to_id
        return None

    def _remember_thread(self, thread_ts: str) -> None:
        if not thread_ts:
            return
        self._known_thread_ids[thread_ts] = None
        self._known_thread_ids.move_to_end(thread_ts)
        if len(self._known_thread_ids) > self._known_thread_ids_max:
            while len(self._known_thread_ids) > self._known_thread_ids_max // 2:
                self._known_thread_ids.popitem(last=False)

    async def _load_known_threads(self) -> None:
        try:
            stored = await Storage.get(_KNOWN_THREADS_STORAGE_KEY)
        except Exception as exc:
            log.warning("slack.known_threads.load_failed", {"error": str(exc)})
            return
        if not isinstance(stored, list):
            return
        for thread_ts in stored[-self._known_thread_ids_max:]:
            if isinstance(thread_ts, str) and thread_ts:
                self._known_thread_ids[thread_ts] = None
        while len(self._known_thread_ids) > self._known_thread_ids_max:
            self._known_thread_ids.popitem(last=False)

    async def _persist_known_threads(self) -> None:
        try:
            await Storage.set(
                _KNOWN_THREADS_STORAGE_KEY,
                list(self._known_thread_ids.keys()),
            )
        except Exception as exc:
            log.warning("slack.known_threads.persist_failed", {"error": str(exc)})
