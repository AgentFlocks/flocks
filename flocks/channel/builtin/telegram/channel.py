"""
Telegram ChannelPlugin for Flocks.

Supports two modes:

- polling (default): long-polls getUpdates; only requires botToken; works without
  a public URL or ngrok.  The start() method runs a persistent loop and blocks
  until abort_event is set.

- webhook: receives update POSTs from Telegram at /channel/telegram/webhook.
  Requires botToken + webhookSecret.  start() returns immediately; the FastAPI
  server handles incoming requests via handle_webhook().

Mode auto-detection: if webhookSecret is configured (or mode="webhook") →
webhook mode; otherwise → polling mode.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any, Awaitable, Callable, Optional

import httpx

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

from .client import close_http_client, get_http_client
from .config import (
    clean_bot_username,
    coerce_int,
    coerce_str,
    is_retryable,
    is_webhook_mode,
    parse_target,
    resolve_account_config,
    resolve_api_base,
)
from .format import markdown_to_telegram_html
from .inbound import BotIdentityResolver, build_inbound_message
from .pairing import PairingStore, pairing_store, send_pairing_confirmed
from .polling import PollingLoop

log = Log.create(service="channel.telegram")

# Mapping from :class:`PreparedTelegramMedia.kind` to Bot API endpoint
# + multipart field name.  Animation covers GIFs that should NOT be sent
# via the photo endpoint.
_TELEGRAM_KIND_TO_ENDPOINT: dict[str, tuple[str, str]] = {
    "photo": ("sendPhoto", "photo"),
    "document": ("sendDocument", "document"),
    "video": ("sendVideo", "video"),
    "audio": ("sendAudio", "audio"),
    "voice": ("sendVoice", "voice"),
    "animation": ("sendAnimation", "animation"),
}


class TelegramChannel(ChannelPlugin):
    """Telegram bot channel — polling (default) or webhook mode."""

    def __init__(self) -> None:
        super().__init__()
        self._identity = BotIdentityResolver()
        self._account_id: str = "default"

    # ------------------------------------------------------------------
    # ChannelPlugin interface
    # ------------------------------------------------------------------

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="telegram",
            label="Telegram",
            aliases=["tg", "tele"],
            order=40,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True,
            threads=True,
            reactions=False,
            edit=False,
            rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        mode = coerce_str(config.get("mode")).lower()
        if mode and mode not in ("webhook", "polling"):
            return "Telegram mode must be 'webhook' or 'polling'"
        try:
            _, account = resolve_account_config(config)
        except ValueError as exc:
            return str(exc)
        token = coerce_str(account.get("botToken"))
        if not token:
            return "Missing required config: botToken"
        if is_webhook_mode(account):
            if not coerce_str(account.get("webhookSecret")):
                return "Missing required config: webhookSecret (required in webhook mode)"
        return None

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        try:
            resolved_id, account = resolve_account_config(config)
        except ValueError:
            resolved_id, account = "default", config
        self._account_id = resolved_id
        self._identity.seed(
            clean_bot_username(account.get("botUsername")),
            coerce_int(account.get("botUserId")),
        )

        if is_webhook_mode(account):
            return

        token = coerce_str(account.get("botToken"))
        if not token:
            log.error("telegram.polling.no_token", {"account": resolved_id})
            return

        _abort = abort_event if abort_event is not None else asyncio.Event()
        base_url = resolve_api_base(account, token)
        loop = PollingLoop(
            account_id=resolved_id,
            base_url=base_url,
            config=config,
            identity=self._identity,
            pairing=pairing_store,
            on_message=on_message,
            record_message=self.record_message,
        )
        await loop.run(_abort)

    async def stop(self) -> None:
        await close_http_client()
        self._identity.clear()
        self.mark_disconnected()

    @property
    def text_chunk_limit(self) -> int:
        return 4096

    @property
    def rate_limit(self) -> tuple[float, int]:
        return (10.0, 3)

    def normalize_target(self, raw: str) -> Optional[str]:
        chat_id, thread_id = parse_target(raw)
        if not chat_id:
            return None
        if thread_id is not None:
            return f"{chat_id}:topic:{thread_id}"
        return chat_id

    def target_hint(self) -> str:
        return "<chat_id> 或 <chat_id>:topic:<thread_id>"

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def get_pairing_store(self) -> PairingStore:
        return pairing_store

    async def confirm_pairing(self, entry: dict) -> None:
        """Send a 'pairing successful' message back to the Telegram user."""
        try:
            _, account = resolve_account_config(self._config)
        except (ValueError, AttributeError):
            return
        token = coerce_str(account.get("botToken"))
        if not token:
            return
        base_url = resolve_api_base(account, token)
        await send_pairing_confirmed(base_url, entry)

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        body: bytes,
        headers: dict,
    ) -> Optional[dict]:
        if not self._on_message:
            return {"error": "telegram channel not started", "status_code": 503}

        try:
            account_id, account = resolve_account_config(self._config)
        except ValueError as exc:
            return {"error": str(exc), "status_code": 500}

        expected_secret = coerce_str(account.get("webhookSecret"))
        provided_secret = (
            headers.get("x-telegram-bot-api-secret-token")
            or headers.get("X-Telegram-Bot-Api-Secret-Token")
            or ""
        )
        if not expected_secret or not hmac.compare_digest(provided_secret, expected_secret):
            return {"error": "invalid webhook secret", "status_code": 401}

        try:
            update = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"error": "invalid json", "status_code": 400}

        message = update.get("message") or update.get("channel_post")
        if not isinstance(message, dict):
            return {"ok": True}

        inbound = await build_inbound_message(
            message, account_id, self._identity, self._config,
        )
        if inbound is None:
            return {"ok": True}

        await self._on_message(inbound)
        self.record_message()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        """Send a media message via the Telegram Bot HTTP API.

        Routes the call to ``sendPhoto`` / ``sendDocument`` /
        ``sendVideo`` / ``sendAudio`` / ``sendVoice`` / ``sendAnimation``
        based on the inferred kind of *ctx.media_url* (or ``ctx.text``
        carrying a ``KIND:...`` prefix when overridden by the agent).
        Local files are read directly; ``http(s)`` URLs are passed
        through to the Bot API (Telegram fetches them itself for
        sub-5MB photos / sub-20MB files).
        """
        try:
            account_id, account = resolve_account_config(self._config, ctx.account_id)
        except ValueError as exc:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False, error=str(exc),
            )

        token = coerce_str(account.get("botToken"))
        if not token:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False, error="Missing botToken",
            )

        chat_id, target_thread_id = parse_target(ctx.to)
        if not chat_id:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False,
                error="Invalid Telegram target",
            )

        if not ctx.media_url:
            return await self.send_text(ctx)

        message_thread_id = coerce_int(ctx.thread_id) or target_thread_id
        reply_to_message_id = coerce_int(ctx.reply_to_id)
        base_url = resolve_api_base(account, token)
        timeout_seconds = max(coerce_int(account.get("timeoutSeconds")) or 60, 1)
        client = await get_http_client()

        try:
            from flocks.channel.builtin.telegram.media import prepare_telegram_media

            kind_override = None
            media_source = ctx.media_url
            # Optional agent-side override: ``telegram:document:<url>``
            # forces the document endpoint (e.g. for images that fail
            # photo dimension checks).
            if media_source.startswith("telegram:"):
                head, _, tail = media_source.partition(":")
                # head is e.g. ``telegram:document`` then a ``:``-separated URL
                kind_part = media_source.split(":", 2)
                if len(kind_part) == 3 and kind_part[0] == "telegram":
                    candidate = kind_part[1].strip().lower()
                    if candidate in {"photo", "document", "video", "audio", "voice", "animation"}:
                        kind_override = candidate
                        media_source = kind_part[2]

            prepared = await prepare_telegram_media(
                media_source, kind_override=kind_override,
            )

            endpoint, param_name = _TELEGRAM_KIND_TO_ENDPOINT[prepared.kind]
            fields: dict[str, Any] = {
                "chat_id": chat_id,
            }
            if ctx.text:
                fields["caption"] = (ctx.text or "")[:1024]
            if message_thread_id is not None:
                fields["message_thread_id"] = message_thread_id
            if reply_to_message_id is not None:
                fields["reply_to_message_id"] = reply_to_message_id
            if ctx.silent:
                fields["disable_notification"] = True
            files = {
                param_name: (prepared.filename, prepared.data, prepared.mime),
            }

            response = await client.post(
                f"{base_url}/{endpoint}",
                data=fields,
                files=files,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False,
                error=str(exc),
            )
        except Exception as exc:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False,
                error=f"Telegram send_media failed: {exc}",
                retryable=is_retryable(0, exc if isinstance(exc, httpx.HTTPError) else None),
            )

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400 or not data.get("ok", response.is_success):
            description = (
                data.get("description") or data.get("error")
                or f"HTTP {response.status_code}"
            )
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False,
                error=f"Telegram send_media failed: {description}",
                retryable=is_retryable(response.status_code),
            )

        result = data.get("result") or {}
        message_id = coerce_str(result.get("message_id"))
        returned_chat_id = coerce_str((result.get("chat") or {}).get("id")) or chat_id
        self.record_message()
        return DeliveryResult(
            channel_id="telegram",
            message_id=message_id,
            chat_id=returned_chat_id,
            success=True,
        )


    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        try:
            account_id, account = resolve_account_config(self._config, ctx.account_id)
        except ValueError as exc:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False, error=str(exc),
            )

        token = coerce_str(account.get("botToken"))
        if not token:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False, error="Missing botToken",
            )

        chat_id, target_thread_id = parse_target(ctx.to)
        if not chat_id:
            return DeliveryResult(
                channel_id="telegram", message_id="", success=False,
                error="Invalid Telegram target",
            )

        message_thread_id = coerce_int(ctx.thread_id) or target_thread_id
        reply_to_message_id = coerce_int(ctx.reply_to_id)
        base_url = resolve_api_base(account, token)
        timeout_seconds = max(coerce_int(account.get("timeoutSeconds")) or 30, 1)
        client = await get_http_client()

        # Try to send with Telegram HTML (converted from Markdown).
        # Fall back to plain text if Telegram rejects the HTML.
        html_text = markdown_to_telegram_html(ctx.text)
        use_html = ctx.format_hint != "plain" and bool(html_text)

        for attempt in ("html", "plain"):
            if attempt == "html" and not use_html:
                continue

            send_text = html_text if attempt == "html" else ctx.text
            payload: dict[str, Any] = {"chat_id": chat_id, "text": send_text}
            if attempt == "html":
                payload["parse_mode"] = "HTML"
            if ctx.silent:
                payload["disable_notification"] = True
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id

            try:
                response = await client.post(
                    f"{base_url}/sendMessage",
                    json=payload,
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                return DeliveryResult(
                    channel_id="telegram", message_id="", success=False,
                    error=f"Telegram request failed: {exc}",
                    retryable=is_retryable(0, exc if isinstance(exc, httpx.HTTPError) else None),
                )

            try:
                data = response.json()
            except ValueError:
                data = {}

            desc_lower = (data.get("description") or "").lower()
            if (
                response.status_code == 400
                and attempt == "html"
                and ("parse" in desc_lower or "entity" in desc_lower)
            ):
                log.warning("telegram.send.html_parse_error", {
                    "description": data.get("description", ""),
                })
                continue

            if response.status_code >= 400 or not data.get("ok", response.is_success):
                description = (
                    data.get("description") or data.get("error")
                    or f"HTTP {response.status_code}"
                )
                return DeliveryResult(
                    channel_id="telegram", message_id="", success=False,
                    error=f"Telegram send failed: {description}",
                    retryable=is_retryable(response.status_code),
                )

            result = data.get("result") or {}
            message_id = coerce_str(result.get("message_id"))
            returned_chat_id = coerce_str((result.get("chat") or {}).get("id")) or chat_id
            self.record_message()
            return DeliveryResult(
                channel_id="telegram",
                message_id=message_id,
                chat_id=returned_chat_id,
                success=True,
            )

        # Should not reach here; return a generic failure
        return DeliveryResult(
            channel_id="telegram", message_id="", success=False,
            error="Telegram send failed after HTML fallback",
        )
