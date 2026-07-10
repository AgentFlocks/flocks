"""LINE ChannelPlugin for Flocks."""

from __future__ import annotations

import asyncio
import mimetypes
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

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

from .client import LineApiError, LineClient, maybe_get_bot_user_id
from .config import (
    LINE_MAX_MESSAGES_PER_CALL,
    LINE_REPLY_TOKEN_TTL_SECONDS,
    LINE_TEXT_BUBBLE_LIMIT,
    coerce_bool,
    coerce_int,
    coerce_str,
    normalize_headers,
    normalize_target,
    resolve_api_roots,
    resolve_credentials,
    source_allowed,
    verify_line_signature,
)
from .format import split_for_line, strip_markdown_preserving_urls, text_messages
from .inbound import build_inbound_message

log = Log.create(service="channel.line")

_REPLY_TOKEN_CACHE_MAX = 512


class LineChannel(ChannelPlugin):
    """LINE Messaging API channel — webhook-only."""

    def __init__(self) -> None:
        super().__init__()
        self._account_id = "default"
        self._client: Optional[LineClient] = None
        self._reply_tokens: dict[str, tuple[str, float]] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._bot_user_id: Optional[str] = None

    def meta(self) -> ChannelMeta:
        return ChannelMeta(id="line", label="LINE", aliases=["linebot"], order=45)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True,
            threads=False,
            reactions=False,
            edit=False,
            rich_text=False,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        token, secret = resolve_credentials(config)
        if not token:
            return "Missing required config: channelAccessToken"
        if not secret:
            return "Missing required config: channelSecret"
        group_trigger = coerce_str(config.get("groupTrigger")).lower()
        if group_trigger and group_trigger not in {"mention", "all"}:
            return "LINE groupTrigger must be 'mention' or 'all'"
        return None

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._account_id = coerce_str(config.get("accountId")) or "default"
        token, _ = resolve_credentials(config)
        api_root, data_api_root = resolve_api_roots(config)
        timeout = max(coerce_int(config.get("timeoutSeconds"), 30), 1)
        self._client = LineClient(
            token,
            api_root=api_root,
            data_api_root=data_api_root,
            timeout=float(timeout),
        )
        configured_bot_user_id = coerce_str(config.get("botUserId"))
        if configured_bot_user_id:
            self._bot_user_id = configured_bot_user_id
        else:
            self._spawn(self._resolve_bot_user_id())
        return

    async def stop(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._reply_tokens.clear()
        self.mark_disconnected()

    @property
    def text_chunk_limit(self) -> int:
        return max(1, coerce_int(self._config.get("textChunkLimit"), 4500))

    @property
    def rate_limit(self) -> tuple[float, int]:
        return (
            float(max(coerce_int(self._config.get("rateLimit"), 10), 1)),
            max(coerce_int(self._config.get("rateBurst"), 3), 1),
        )

    def normalize_target(self, raw: str) -> Optional[str]:
        return normalize_target(raw)

    def target_hint(self) -> str:
        return "LINE user/group/room ID, e.g. U..., C..., R..."

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        if format_hint == "plain":
            return text
        return strip_markdown_preserving_urls(text)

    async def handle_webhook(self, body: bytes, headers: dict) -> Optional[dict]:
        if not self._on_message:
            return {"error": "line channel not started", "status_code": 503}

        _, secret = resolve_credentials(self._config)
        normalized_headers = normalize_headers(headers)
        signature = normalized_headers.get("x-line-signature", "")
        if not verify_line_signature(body, signature, secret):
            return {"error": "invalid signature", "status_code": 401}

        try:
            import json
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"error": "invalid json", "status_code": 400}

        events = payload.get("events") or []
        if not isinstance(events, list):
            return {"ok": True}

        for event in events:
            if isinstance(event, dict):
                self._spawn(self._dispatch_event(event))
        return {"ok": True}

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        target = normalize_target(ctx.to)
        if not target:
            return DeliveryResult(
                channel_id="line",
                message_id="",
                success=False,
                error="Invalid LINE target",
            )
        messages = text_messages(ctx.text or "", max_chars=self.text_chunk_limit)
        if not messages:
            return DeliveryResult(channel_id="line", message_id="", success=True)
        return await self._send_messages(target, messages, reply_to_id=ctx.reply_to_id)

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        target = normalize_target(ctx.to)
        if not target:
            return DeliveryResult(
                channel_id="line",
                message_id="",
                success=False,
                error="Invalid LINE target",
            )
        if not ctx.media_url:
            return await self.send_text(ctx)

        messages: list[dict[str, Any]] = []
        if ctx.text:
            messages.extend(
                text_messages(
                    ctx.text,
                    max_chars=self.text_chunk_limit,
                    max_messages=LINE_MAX_MESSAGES_PER_CALL - 1,
                )
            )
        media_message = self._build_outbound_media_message(ctx.media_url)
        if media_message:
            messages.append(media_message)
        else:
            fallback = f"{ctx.text}\n{ctx.media_url}".strip() if ctx.text else ctx.media_url
            messages = text_messages(fallback, max_chars=self.text_chunk_limit)

        return await self._send_messages(
            target,
            messages[:LINE_MAX_MESSAGES_PER_CALL],
            reply_to_id=ctx.reply_to_id,
        )

    def chunk_text(self, text: str, limit: int) -> list[str]:
        if not text:
            return []
        bubble_limit = max(1, min(limit, LINE_TEXT_BUBBLE_LIMIT))
        return super().chunk_text(text, bubble_limit * LINE_MAX_MESSAGES_PER_CALL)

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        try:
            source = event.get("source") or {}
            if not isinstance(source, dict) or not source_allowed(source, self._config):
                log.info("line.webhook.unauthorized_source", {"source": source})
                return

            inbound = build_inbound_message(
                event,
                account_id=self._account_id,
                bot_user_id=self._bot_user_id,
                bot_mention_text=coerce_str(self._config.get("botMentionText")) or None,
            )
            reply_token = coerce_str(event.get("replyToken"))
            if inbound and reply_token:
                self._cache_reply_token(
                    reply_token,
                    inbound.message_id,
                    inbound.reply_to_id,
                    self._source_chat_id(source),
                )
            if inbound and self._on_message:
                await self._on_message(inbound)
                self.record_message()
        except Exception as exc:
            log.exception("line.webhook.dispatch_failed", {"error": str(exc)})

    async def _send_messages(
        self,
        target: str,
        messages: list[dict[str, Any]],
        *,
        reply_to_id: Optional[str] = None,
    ) -> DeliveryResult:
        if self._client is None:
            return DeliveryResult(
                channel_id="line",
                message_id="",
                success=False,
                error="LINE client not initialized",
            )

        reply_token = self._take_reply_token(reply_to_id, target)
        if reply_token:
            try:
                await self._client.reply(reply_token, messages)
                self.record_message()
                return DeliveryResult(channel_id="line", message_id="", chat_id=target)
            except Exception as exc:
                if not coerce_bool(self._config.get("pushFallback"), True):
                    return self._failure(exc)
                log.warning("line.send.reply_failed_push_fallback", {"error": str(exc)})

        if not coerce_bool(self._config.get("pushFallback"), True):
            return DeliveryResult(
                channel_id="line",
                message_id="",
                success=False,
                error="No valid LINE reply token and pushFallback is disabled",
            )

        try:
            data = await self._client.push(target, messages)
            self.record_message()
            sent = data.get("sentMessages") if isinstance(data, dict) else None
            message_id = ""
            if isinstance(sent, list) and sent:
                first = sent[0]
                if isinstance(first, dict):
                    message_id = coerce_str(first.get("id"))
            return DeliveryResult(channel_id="line", message_id=message_id, chat_id=target)
        except Exception as exc:
            return self._failure(exc)

    def _cache_reply_token(self, reply_token: str, *keys: Optional[str]) -> None:
        now = time.monotonic()
        self._prune_reply_tokens(now)
        expires_at = now + LINE_REPLY_TOKEN_TTL_SECONDS
        for key in keys:
            normalized = coerce_str(key)
            if normalized:
                self._reply_tokens[normalized] = (reply_token, expires_at)
        if len(self._reply_tokens) > _REPLY_TOKEN_CACHE_MAX:
            overflow = len(self._reply_tokens) - _REPLY_TOKEN_CACHE_MAX
            for key, _ in sorted(self._reply_tokens.items(), key=lambda item: item[1][1])[:overflow]:
                self._reply_tokens.pop(key, None)

    def _take_reply_token(self, reply_to_id: Optional[str], target: str) -> Optional[str]:
        now = time.monotonic()
        for key in [coerce_str(reply_to_id), target]:
            if not key:
                continue
            token_entry = self._reply_tokens.pop(key, None)
            if not token_entry:
                continue
            token, expires_at = token_entry
            self._drop_reply_token_aliases(token)
            if expires_at > now:
                return token
        self._prune_reply_tokens(now)
        return None

    def _drop_reply_token_aliases(self, token: str) -> None:
        for key, (cached_token, _) in list(self._reply_tokens.items()):
            if cached_token == token:
                self._reply_tokens.pop(key, None)

    def _prune_reply_tokens(self, now: Optional[float] = None) -> None:
        cutoff = time.monotonic() if now is None else now
        for key, (_, expires_at) in list(self._reply_tokens.items()):
            if expires_at <= cutoff:
                self._reply_tokens.pop(key, None)

    def _failure(self, exc: Exception) -> DeliveryResult:
        retryable = getattr(exc, "retryable", False)
        if not retryable and not isinstance(exc, LineApiError):
            retryable = "timeout" in str(exc).lower() or "rate" in str(exc).lower()
        return DeliveryResult(
            channel_id="line",
            message_id="",
            success=False,
            error=str(exc),
            retryable=bool(retryable),
        )

    def _build_outbound_media_message(self, media_url: str) -> Optional[dict[str, Any]]:
        parsed = urlparse(media_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        mime = mimetypes.guess_type(parsed.path)[0] or ""
        if mime.startswith("image/"):
            return {
                "type": "image",
                "originalContentUrl": media_url,
                "previewImageUrl": media_url,
            }
        if mime.startswith("audio/"):
            return {
                "type": "audio",
                "originalContentUrl": media_url,
                "duration": max(coerce_int(self._config.get("audioDurationMs"), 1000), 1),
            }
        if mime.startswith("video/"):
            preview = coerce_str(self._config.get("videoPreviewUrl"))
            if not preview:
                return None
            return {
                "type": "video",
                "originalContentUrl": media_url,
                "previewImageUrl": preview,
            }
        return None

    @staticmethod
    def _source_chat_id(source: dict[str, Any]) -> str:
        source_type = coerce_str(source.get("type"))
        if source_type == "group":
            return coerce_str(source.get("groupId"))
        if source_type == "room":
            return coerce_str(source.get("roomId"))
        return coerce_str(source.get("userId"))

    async def _resolve_bot_user_id(self) -> None:
        resolved = await maybe_get_bot_user_id(self._client)
        if resolved:
            self._bot_user_id = resolved

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
