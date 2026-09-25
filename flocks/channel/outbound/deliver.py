"""
Unified outbound delivery with chunking, rate-limiting, and retry.

All outbound messages — whether triggered by Agent callbacks or
the channel_message tool — go through ``OutboundDelivery.deliver()``.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar, Optional

from flocks.channel.base import (
    ChannelPlugin,
    DeliveryResult,
    OutboundContext,
)
from flocks.channel.registry import default_registry
from flocks.hooks.pipeline import HookPipeline
from flocks.utils.log import Log
from flocks.utils.rate_limiter import AsyncTokenBucket

log = Log.create(service="channel.outbound")


class OutboundDelivery:
    """Stateless dispatcher: hook → format → chunk → rate-limit → retry → send → hook."""

    _rate_limiters: ClassVar[dict[str, AsyncTokenBucket]] = {}
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0

    @classmethod
    def _get_limiter(cls, channel_id: str, plugin: ChannelPlugin) -> AsyncTokenBucket:
        if channel_id not in cls._rate_limiters:
            rate, burst = plugin.rate_limit
            cls._rate_limiters[channel_id] = AsyncTokenBucket(rate=rate, burst=burst)
        return cls._rate_limiters[channel_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    async def deliver(
        cls,
        ctx: OutboundContext,
        *,
        max_retries: int = MAX_RETRIES,
        session_id: Optional[str] = None,
    ) -> list[DeliveryResult]:
        """Deliver a message described by *ctx*.

        Returns a list of :class:`DeliveryResult` — one per chunk, or one
        for a media-only message.
        """
        plugin = default_registry.get(ctx.channel_id)
        if not plugin:
            return [DeliveryResult(
                channel_id=ctx.channel_id, message_id="",
                success=False, error=f"Channel '{ctx.channel_id}' not registered",
            )]

        text = ctx.text

        # Hook: channel.outbound.before
        try:
            hook_ctx = await HookPipeline.run_channel_outbound_before({
                "channel_id": ctx.channel_id,
                "to": ctx.to,
                "text": text,
                "media_url": ctx.media_url,
                "session_id": session_id,
            })
            if hook_ctx.output.get("blocked"):
                return [DeliveryResult(
                    channel_id=ctx.channel_id, message_id="",
                    success=True,
                )]
            if "text" in hook_ctx.output:
                text = hook_ctx.output["text"]
        except Exception as e:
            log.warning("channel.delivery.hook_before_failed", {"error": str(e)})
            if ctx.single_message:
                return [DeliveryResult(channel_id=ctx.channel_id, message_id="", success=False,
                                       error="Notification hook failed")]

        formatted_text = plugin.format_message(text) if text else ""

        # Pure-media (no text)
        if not formatted_text and ctx.media_url:
            media_ctx = OutboundContext(
                channel_id=ctx.channel_id, account_id=ctx.account_id,
                to=ctx.to, text="", media_url=ctx.media_url,
                reply_to_id=ctx.reply_to_id, thread_id=ctx.thread_id,
                silent=ctx.silent,
            )
            result = await cls._send_with_retry(plugin, media_ctx, "media", max_retries)
            await cls._publish_sent_event(result, ctx.to, ctx.account_id, session_id)
            return [result]

        # Chunk text
        chunks = plugin.chunk_text(formatted_text, plugin.text_chunk_limit)
        if ctx.single_message and len(chunks) != 1:
            return [DeliveryResult(channel_id=ctx.channel_id, message_id="", success=False,
                                   error="Notification must fit in one message")]
        if not chunks:
            return [DeliveryResult(
                channel_id=ctx.channel_id, message_id="", success=True,
            )]

        limiter = cls._get_limiter(ctx.channel_id, plugin)
        results: list[DeliveryResult] = []

        for i, chunk in enumerate(chunks):
            chunk_ctx = OutboundContext(
                channel_id=ctx.channel_id, account_id=ctx.account_id,
                to=ctx.to, text=chunk,
                media_url=ctx.media_url if (i == len(chunks) - 1) else None,
                reply_to_id=ctx.reply_to_id, thread_id=ctx.thread_id,
                silent=ctx.silent,
                subject=ctx.subject, message_id=ctx.message_id,
                new_thread=ctx.new_thread, single_message=ctx.single_message,
            )

            await limiter.acquire()

            send_type = "media" if chunk_ctx.media_url else "text"
            result = await cls._send_with_retry(plugin, chunk_ctx, send_type, max_retries)
            results.append(result)

            if not result.success:
                log.error("channel.delivery.chunk_failed", {
                    "channel": ctx.channel_id,
                    "chunk": f"{i + 1}/{len(chunks)}",
                    "error": result.error,
                })
                break

        for result in results:
            await cls._publish_sent_event(result, ctx.to, ctx.account_id, session_id)

        # Hook: channel.outbound.after
        try:
            await HookPipeline.run_channel_outbound_after({
                "channel_id": ctx.channel_id,
                "to": ctx.to,
                "session_id": session_id,
                "results": [
                    {"message_id": r.message_id, "success": r.success, "error": r.error}
                    for r in results
                ],
            })
        except Exception as e:
            log.warning("channel.delivery.hook_after_failed", {"error": str(e)})

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    async def _send_with_retry(
        cls,
        plugin: ChannelPlugin,
        ctx: OutboundContext,
        send_type: str,
        max_retries: int,
    ) -> DeliveryResult:
        """Retry with exponential back-off."""
        last_result: Optional[DeliveryResult] = None
        for attempt in range(max_retries):
            try:
                result = (
                    await plugin.send_media(ctx)
                    if send_type == "media"
                    else await plugin.send_text(ctx)
                )

                if result.success:
                    return result

                last_result = result
                if not result.retryable:
                    return result

            except Exception as e:
                last_result = DeliveryResult(
                    channel_id=ctx.channel_id, message_id="",
                    success=False, error=str(e), retryable=True,
                )

            if attempt < max_retries - 1:
                delay = cls.RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("channel.delivery.retry", {
                    "attempt": attempt + 1,
                    "delay": delay,
                    "error": last_result.error if last_result else "unknown",
                })
                await asyncio.sleep(delay)

        return last_result or DeliveryResult(
            channel_id=ctx.channel_id, message_id="",
            success=False, error="Max retries exceeded",
        )

    @classmethod
    async def _publish_sent_event(
        cls,
        result: DeliveryResult,
        to: str,
        account_id: Optional[str],
        session_id: Optional[str] = None,
    ) -> None:
        try:
            from flocks.channel.events import ChannelMessageSent
            from flocks.bus.bus import Bus
            await Bus.publish(ChannelMessageSent, {
                "channel_id": result.channel_id,
                "account_id": account_id,
                "message_id": result.message_id,
                "to": to,
                "session_id": session_id,
                "success": result.success,
                "error": result.error,
            })
        except Exception as e:
            log.warning("channel.delivery.event_publish_failed", {"error": str(e)})
