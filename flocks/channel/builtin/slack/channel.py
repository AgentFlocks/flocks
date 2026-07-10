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
    OutboundContext,
)
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

    SLACK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by validate_config tests
    AsyncApp = None  # type: ignore[assignment]
    AsyncSocketModeHandler = None  # type: ignore[assignment]
    SLACK_AVAILABLE = False


log = Log.create(service="channel.slack")


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
            media=True,
            threads=True,
            reactions=False,
            edit=False,
            rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        if not SLACK_AVAILABLE:
            return "Missing Python dependency: slack-bolt"
        if not resolve_bot_token(config):
            return "Missing required config: botToken"
        if not resolve_app_token(config):
            return "Missing required config: appToken"
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
            return

        bot_token = resolve_bot_token(config)
        app_token = resolve_app_token(config)
        assert AsyncApp is not None
        assert AsyncSocketModeHandler is not None

        self._app = AsyncApp(token=bot_token)
        abort = abort_event or asyncio.Event()
        try:
            auth = await self._app.client.auth_test()
            self._bot_user_id = str(auth.get("user_id") or "")
            team_id = str(auth.get("team_id") or "default")

            self._register_handlers()
            await self._connect_socket_mode(app_token)
            log.info("slack.connected", {"team_id": team_id, "bot_user_id": self._bot_user_id})
            await abort.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.mark_disconnected(str(exc))
            log.warning("slack.socket.stopped", {"error": str(exc)})
        finally:
            await self.stop()

    async def _connect_socket_mode(self, app_token: str) -> None:
        """Connect Socket Mode once and mark connected only after success."""
        assert AsyncSocketModeHandler is not None
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
            self.mark_disconnected(str(exc))
            raise
        self.mark_connected()

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
