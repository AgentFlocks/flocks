"""
Feishu (Lark) ChannelPlugin implementation.

Supports both WebSocket long-connection and Webhook callback modes,
multiple accounts, domain switching (feishu/lark), message editing,
and group-policy enforcement.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from typing import Optional

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

log = Log.create(service="channel.feishu")


_WEBHOOK_CHAT_LOCKS_MAX = 2000


class FeishuChannel(ChannelPlugin):
    """Feishu / Lark channel plugin."""

    def __init__(self) -> None:
        super().__init__()
        self._webhook_chat_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._webhook_dedup_flush_tasks: dict[str, asyncio.Task] = {}
        self._webhook_dedup_warmed: set[str] = set()

    def _get_webhook_chat_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id in self._webhook_chat_locks:
            self._webhook_chat_locks.move_to_end(chat_id)
            return self._webhook_chat_locks[chat_id]
        lock = asyncio.Lock()
        self._webhook_chat_locks[chat_id] = lock
        while len(self._webhook_chat_locks) > _WEBHOOK_CHAT_LOCKS_MAX:
            oldest_key = next(iter(self._webhook_chat_locks))
            if not self._webhook_chat_locks[oldest_key].locked():
                del self._webhook_chat_locks[oldest_key]
            else:
                self._webhook_chat_locks.move_to_end(oldest_key)
                break
        return lock

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="feishu", label="飞书",
            aliases=["lark"], order=10,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True, threads=True, reactions=True,
            edit=True, rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        # Accept if top-level or any named account has credentials
        if config.get("appId") and config.get("appSecret"):
            return None
        accounts = config.get("accounts", {})
        for acc in accounts.values():
            if acc.get("appId") and acc.get("appSecret"):
                return None
        return "Missing required config: appId and appSecret (top-level or in accounts)"

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        # Streaming card mode: use StreamingCard when streaming=true and a valid chat_id is present
        if self._config.get("streaming") and ctx.to and ctx.to.strip():
            return await self._send_streaming(ctx)
        return await self._send_static(ctx)

    async def _send_static(self, ctx: OutboundContext) -> DeliveryResult:
        """Send message statically (default behavior)."""
        from flocks.channel.builtin.feishu.client import FeishuApiError
        from flocks.channel.builtin.feishu.send import send_message_feishu
        try:
            result = await send_message_feishu(
                config=self._config,
                to=ctx.to, text=ctx.text,
                reply_to_id=ctx.reply_to_id,
                account_id=ctx.account_id,
            )
            self.record_message()
            return DeliveryResult(
                channel_id="feishu",
                message_id=result["message_id"],
                chat_id=result.get("chat_id"),
            )
        except Exception as e:
            retryable = getattr(e, "retryable", False)
            if not retryable and not isinstance(e, FeishuApiError):
                retryable = "rate limit" in str(e).lower() or "timeout" in str(e).lower()
            return DeliveryResult(
                channel_id="feishu", message_id="",
                success=False, error=str(e), retryable=retryable,
            )

    async def _send_streaming(self, ctx: OutboundContext) -> DeliveryResult:
        """Send via streaming card. Falls back to static send on permission error or init failure."""
        from flocks.channel.builtin.feishu.client import FeishuApiError
        from flocks.channel.builtin.feishu.streaming_card import StreamingCard
        coalesce_ms = int(self._config.get("streamingCoalesceMs", 200))
        card = StreamingCard(
            config=self._config,
            account_id=ctx.account_id,
            chat_id=ctx.to,
            reply_to_id=ctx.reply_to_id,
            coalesce_ms=coalesce_ms,
        )
        message_id = await card.start()
        if card.is_degraded or not message_id:
            # Degraded: streaming unavailable, fall back to static send
            return await self._send_static(ctx)

        try:
            # Write full text at once (send_text scenario has no per-chunk streaming data)
            await card.finalize(ctx.text)
            self.record_message()
            return DeliveryResult(
                channel_id="feishu",
                message_id=message_id,
                chat_id=ctx.to,
            )
        except Exception as e:
            await card.abort(f"Send failed: {e}")
            retryable = getattr(e, "retryable", False)
            if not retryable and not isinstance(e, FeishuApiError):
                retryable = "rate limit" in str(e).lower() or "timeout" in str(e).lower()
            return DeliveryResult(
                channel_id="feishu", message_id="",
                success=False, error=str(e), retryable=retryable,
            )

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        from flocks.channel.builtin.feishu.client import FeishuApiError
        from flocks.channel.builtin.feishu.media import send_media_feishu
        try:
            result = await send_media_feishu(
                config=self._config,
                to=ctx.to,
                text=ctx.text or "",
                media_url=ctx.media_url or "",
                reply_to_id=ctx.reply_to_id,
                account_id=ctx.account_id,
            )
            self.record_message()
            return DeliveryResult(
                channel_id="feishu",
                message_id=result["message_id"],
                chat_id=result.get("chat_id"),
            )
        except Exception as e:
            retryable = getattr(e, "retryable", False)
            if not retryable and not isinstance(e, FeishuApiError):
                retryable = "rate limit" in str(e).lower() or "timeout" in str(e).lower()
            return DeliveryResult(
                channel_id="feishu", message_id="",
                success=False, error=str(e), retryable=retryable,
            )

    async def edit_message(self, message_id: str, text: str, account_id: Optional[str] = None) -> None:
        """Edit an existing Feishu message in-place (within 24 hours)."""
        from flocks.channel.builtin.feishu.send import edit_message_feishu
        await edit_message_feishu(
            config=self._config,
            message_id=message_id,
            text=text,
            account_id=account_id,
        )

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        render_mode = self._config.get("renderMode", "auto")
        if render_mode == "plain":
            return self._strip_markdown(text)
        return text

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Naively remove common Markdown markers."""
        import re
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"`(.+?)`", r"\1", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        return text

    async def start(self, config, on_message, abort_event=None):
        self._config = config
        self._on_message = on_message
        from flocks.channel.builtin.feishu.config import list_account_configs

        websocket_accounts = [
            account
            for account in list_account_configs(config, require_credentials=True)
            if account.get("connectionMode", "websocket") == "websocket"
        ]
        if websocket_accounts:
            from flocks.channel.builtin.feishu.monitor import start_websocket
            await start_websocket(config, on_message, abort_event)

    async def _ensure_webhook_dedup_ready(self, account_id: str, dedup) -> None:
        if account_id not in self._webhook_dedup_warmed:
            await dedup.warmup()
            self._webhook_dedup_warmed.add(account_id)
        task = self._webhook_dedup_flush_tasks.get(account_id)
        if task is None or task.done():
            self._webhook_dedup_flush_tasks[account_id] = await dedup.start_background_flush()

    async def handle_webhook(self, body: bytes, headers: dict) -> Optional[dict]:
        from flocks.channel.builtin.feishu.config import (
            build_webhook_replay_key,
            resolve_webhook_account_config,
            verify_webhook_timestamp,
        )
        from flocks.channel.builtin.feishu.dedup import get_dedup

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            log.warning("feishu.webhook.invalid_json")
            return {"error": "invalid json", "status_code": 400}

        resolved_config = resolve_webhook_account_config(
            self._config,
            body=body,
            headers=headers,
            data=data,
        )
        if not resolved_config:
            log.warning("feishu.webhook.account_unresolved")
            return {"error": "invalid signature or verification token", "status_code": 401}

        account_id = resolved_config.get("_account_id", "default")

        if not verify_webhook_timestamp(headers):
            log.warning("feishu.webhook.timestamp_invalid", {"account_id": account_id})
            return {"error": "invalid timestamp", "status_code": 400}

        # Feishu URL verification challenge
        if "challenge" in data:
            return {"challenge": data["challenge"]}

        event_type = (data.get("header") or {}).get("event_type", "")
        dedup_ttl = int(resolved_config.get("dedupTtlSeconds", 86400))

        dedup = await get_dedup(account_id, ttl_seconds=dedup_ttl)
        await self._ensure_webhook_dedup_ready(account_id, dedup)

        replay_key = build_webhook_replay_key(headers, data)
        if replay_key and await dedup.is_duplicate(replay_key):
            log.debug("feishu.webhook.replay_skip", {
                "account_id": account_id,
                "replay_key": replay_key,
            })
            return None

        # ── Card button click event ─────────────────────────────────────
        if event_type == "card.action.trigger":
            from flocks.channel.builtin.feishu.monitor import _parse_card_action_event
            msg = _parse_card_action_event(data, resolved_config)
            if msg and self._on_message:
                if await dedup.is_duplicate(msg.message_id):
                    log.debug("feishu.webhook.synthetic_dedup_skip", {
                        "account_id": account_id,
                        "message_id": msg.message_id,
                    })
                    return None
                await self._on_message(msg)
            return None

        # ── Emoji Reaction event ────────────────────────────────────────
        if event_type == "im.message.reaction.created_v1":
            reaction_policy = resolved_config.get("reactionNotifications", "off")
            if reaction_policy != "off" and self._on_message:
                from flocks.channel.builtin.feishu.monitor import _parse_reaction_event
                msg = await _parse_reaction_event(data, resolved_config)
                if msg:
                    if await dedup.is_duplicate(msg.message_id):
                        log.debug("feishu.webhook.synthetic_dedup_skip", {
                            "account_id": account_id,
                            "message_id": msg.message_id,
                        })
                        return None
                    await self._on_message(msg)
            return None

        # ── Regular message ─────────────────────────────────────────────
        if event_type != "im.message.receive_v1":
            return None

        # Fetch bot open_id for accurate @mention detection
        from flocks.channel.builtin.feishu.identity import (
            get_bot_identity,
            get_cached_bot_open_id,
        )
        bot_open_id = get_cached_bot_open_id(account_id)
        if not bot_open_id:
            bot_open_id, _ = await get_bot_identity(resolved_config, account_id)

        from flocks.channel.builtin.feishu.monitor import _parse_event
        msg = _parse_event(data, resolved_config, bot_open_id=bot_open_id)
        if msg and self._on_message:
            if await dedup.is_duplicate(msg.message_id):
                log.debug("feishu.webhook.dedup_skip", {
                    "account_id": account_id,
                    "message_id": msg.message_id,
                })
                return None

            chat_lock = self._get_webhook_chat_lock(msg.chat_id)
            async with chat_lock:
                debounce_ms = int(resolved_config.get("inboundDebounceMs", 800))
                from flocks.channel.builtin.feishu.debounce import get_debouncer

                async def _record_suppressed(ids: list[str]) -> None:
                    for mid in ids:
                        await dedup.is_duplicate(mid)

                debouncer = get_debouncer(
                    account_id=f"webhook:{account_id}",
                    debounce_ms=debounce_ms,
                    on_flush=self._on_message,
                    on_suppressed_ids=_record_suppressed,
                )
                await debouncer.enqueue(msg)
        return None

    async def stop(self) -> None:
        from flocks.channel.builtin.feishu.dedup import get_dedup
        from flocks.channel.builtin.feishu.client import close_http_client

        flush_tasks = list(self._webhook_dedup_flush_tasks.values())
        for task in flush_tasks:
            task.cancel()
        if flush_tasks:
            await asyncio.gather(*flush_tasks, return_exceptions=True)
        for account_id in list(self._webhook_dedup_warmed):
            try:
                dedup = await get_dedup(account_id)
                await dedup.flush()
            except Exception:
                pass
        self._webhook_dedup_flush_tasks.clear()
        self._webhook_dedup_warmed.clear()
        await close_http_client()

    @property
    def text_chunk_limit(self) -> int:
        return self._config.get("textChunkLimit", 4000)

    @property
    def rate_limit(self) -> tuple[float, int]:
        rate = self._config.get("rateLimit", 50.0)
        burst = self._config.get("rateBurst", 10)
        return (float(rate), int(burst))

    def normalize_target(self, raw: str) -> Optional[str]:
        from flocks.channel.builtin.feishu.config import strip_target_prefix
        return strip_target_prefix(raw)

    def target_hint(self) -> str:
        return "user:<open_id> or chat:<chat_id>"
