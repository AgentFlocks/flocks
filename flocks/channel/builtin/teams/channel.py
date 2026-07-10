"""Microsoft Teams ChannelPlugin for Flocks.

The channel uses the Microsoft Teams Apps SDK to validate and process Bot
Framework activities, while reusing Flocks' public channel webhook route:
``POST /api/channel/teams/webhook``.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote, urlparse

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.security import resolve_value
from flocks.utils.log import Log

log = Log.create(service="channel.teams")

_DEFAULT_ACCOUNT_ID = "default"
_TEXT_LIMIT = 28000
_MENTION_RE = re.compile(r"<at>[^<]*</at>\s*", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

try:  # pragma: no cover - exercised indirectly when dependency is installed
    from microsoft_teams.api import MessageActivity
    from microsoft_teams.apps import ActivityContext, App
    from microsoft_teams.apps.http.adapter import (
        HttpMethod,
        HttpRequest,
        HttpResponse,
        HttpRouteHandler,
    )
    from microsoft_teams.common.http.client import ClientOptions

    TEAMS_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - test suite monkeypatches these symbols
    ActivityContext = Any  # type: ignore
    App = None  # type: ignore
    ClientOptions = None  # type: ignore
    HttpMethod = Any  # type: ignore
    HttpRequest = None  # type: ignore
    HttpResponse = Any  # type: ignore
    HttpRouteHandler = Callable[[Any], Awaitable[Any]]  # type: ignore
    MessageActivity = Any  # type: ignore
    TEAMS_SDK_AVAILABLE = False


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _coerce_str(value)
        if text:
            return text
    return ""


def _resolve_config(config: dict) -> dict[str, Any]:
    resolved = resolve_value(config or {})
    return {
        **resolved,
        "_client_id": _first_non_empty(
            resolved.get("clientId"),
            resolved.get("client_id"),
            os.getenv("TEAMS_CLIENT_ID"),
        ),
        "_client_secret": _first_non_empty(
            resolved.get("clientSecret"),
            resolved.get("client_secret"),
            os.getenv("TEAMS_CLIENT_SECRET"),
        ),
        "_tenant_id": _first_non_empty(
            resolved.get("tenantId"),
            resolved.get("tenant_id"),
            os.getenv("TEAMS_TENANT_ID"),
        ),
        "_service_url": _first_non_empty(
            resolved.get("serviceUrl"),
            resolved.get("service_url"),
            os.getenv("TEAMS_SERVICE_URL"),
        ),
        "_bot_id": _first_non_empty(
            resolved.get("botId"),
            resolved.get("bot_id"),
            os.getenv("TEAMS_BOT_ID"),
        ),
        "_bot_user_id": _first_non_empty(
            resolved.get("botUserId"),
            resolved.get("bot_user_id"),
            os.getenv("TEAMS_BOT_USER_ID"),
        ),
        "_bot_aad_object_id": _first_non_empty(
            resolved.get("botAadObjectId"),
            resolved.get("bot_aad_object_id"),
            os.getenv("TEAMS_BOT_AAD_OBJECT_ID"),
        ),
    }


def _activity_value(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _strip_mentions(text: str) -> tuple[str, bool]:
    mentioned = bool(_MENTION_RE.search(text or ""))
    cleaned = _MENTION_RE.sub("", text or "")
    cleaned = _TAG_RE.sub("", cleaned)
    return html.unescape(cleaned).strip(), mentioned


def _conversation_chat_type(conversation_type: str) -> ChatType:
    if conversation_type == "personal":
        return ChatType.DIRECT
    if conversation_type == "channel":
        return ChatType.CHANNEL
    return ChatType.GROUP


def _path_from_file_url(url: str) -> Path:
    parsed = urlparse(url)
    return Path(unquote(parsed.path))


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


class _TeamsWebhookBridge:
    """Minimal HTTP adapter bridge expected by ``microsoft-teams-apps``.

    Flocks owns the actual FastAPI route. The SDK registers a route handler on
    this adapter, and ``TeamsChannel.handle_webhook`` invokes it with an SDK
    ``HttpRequest`` object.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, HttpRouteHandler] = {}

    @property
    def handler(self) -> Optional[HttpRouteHandler]:
        return self.handlers.get("/api/messages") or next(iter(self.handlers.values()), None)

    @property
    def path(self) -> str:
        return "/api/messages" if "/api/messages" in self.handlers else next(iter(self.handlers.keys()), "")

    def register_route(self, method: HttpMethod, path: str, handler: HttpRouteHandler) -> None:
        method_text = str(getattr(method, "value", method)).upper()
        if method_text.endswith("POST") or method_text == "POST":
            if path != "/api/messages":
                log.warning("teams.webhook.extra_post_route", {"path": path})
            self.handlers[path] = handler

    def serve_static(self, _path: str, _name: Optional[str] = None) -> None:
        return None

    async def start(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def stop(self) -> None:
        return None


class TeamsChannel(ChannelPlugin):
    """Microsoft Teams bot channel."""

    def __init__(self) -> None:
        super().__init__()
        self._bridge: Optional[_TeamsWebhookBridge] = None
        self._app: Any = None
        self._account_id = _DEFAULT_ACCOUNT_ID
        self._conversation_service_urls: dict[str, str] = {}

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="teams",
            label="Microsoft Teams",
            aliases=["team", "ms-teams", "msteams"],
            order=50,
        )

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
        resolved = _resolve_config(config)
        missing = [
            label
            for key, label in (
                ("_client_id", "clientId"),
                ("_client_secret", "clientSecret"),
                ("_tenant_id", "tenantId"),
            )
            if not resolved.get(key)
        ]
        if missing:
            return f"Missing required config: {', '.join(missing)}"
        return None

    @property
    def text_chunk_limit(self) -> int:
        configured = self._config.get("textChunkLimit") if isinstance(self._config, dict) else None
        try:
            return max(int(configured), 1) if configured is not None else _TEXT_LIMIT
        except (TypeError, ValueError):
            return _TEXT_LIMIT

    @property
    def rate_limit(self) -> tuple[float, int]:
        if not isinstance(self._config, dict):
            return (5.0, 3)
        try:
            rate = float(self._config.get("rateLimit", 5.0))
        except (TypeError, ValueError):
            rate = 5.0
        try:
            burst = int(self._config.get("rateBurst", 3))
        except (TypeError, ValueError):
            burst = 3
        return (max(rate, 0.1), max(burst, 1))

    def target_hint(self) -> str:
        return "<conversation_id>"

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        resolved = _resolve_config(config)
        error = self.validate_config(config)
        if error:
            raise RuntimeError(error)
        if not TEAMS_SDK_AVAILABLE or App is None or ClientOptions is None:
            raise RuntimeError(
                "Microsoft Teams SDK is not installed. Install microsoft-teams-apps to enable the Teams channel."
            )

        self._bridge = _TeamsWebhookBridge()
        client_options = ClientOptions(headers={"User-Agent": "Flocks Teams Channel"})
        app_kwargs: dict[str, Any] = {
            "client_id": resolved["_client_id"],
            "client_secret": resolved["_client_secret"],
            "tenant_id": resolved["_tenant_id"],
            "http_server_adapter": self._bridge,
            "client": client_options,
        }
        if resolved["_service_url"]:
            app_kwargs["service_url"] = resolved["_service_url"]

        self._app = App(**app_kwargs)

        @self._app.on_message
        async def _handle_message(ctx: ActivityContext[MessageActivity]) -> None:
            await self._on_message_activity(ctx)

        await self._app.initialize()
        if not self._bridge.handler:
            raise RuntimeError("Teams SDK did not register a webhook handler")
        self.mark_connected()

        return None

    async def stop(self) -> None:
        if self._bridge:
            await self._bridge.stop()
        self._bridge = None
        self._app = None
        self.mark_disconnected()

    async def handle_webhook(self, body: bytes, headers: dict) -> Optional[dict]:
        if not self._bridge or not self._bridge.handler:
            return {"error": "teams channel not started", "status_code": 503}
        if HttpRequest is None:
            return {"error": "teams sdk unavailable", "status_code": 500}

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"error": "invalid json", "status_code": 400}

        try:
            response = await self._bridge.handler(HttpRequest(body=payload, headers=headers))
        except Exception as exc:
            log.error("teams.webhook.handler_failed", {"error": str(exc)})
            return {"error": "teams webhook failed", "status_code": 500}

        return self._normalise_webhook_response(response)

    async def _on_message_activity(self, ctx: ActivityContext[MessageActivity]) -> None:
        activity = _activity_value(ctx, "activity")
        if activity is None or self._on_message is None:
            return

        from_user = _activity_value(activity, "from_") or _activity_value(activity, "from")
        resolved_config = _resolve_config(self._config)
        bot_ids = self._bot_identifiers(resolved_config)
        sender_id = _first_non_empty(
            _activity_value(from_user, "aad_object_id"),
            _activity_value(from_user, "id"),
        )
        sender_values = {
            _coerce_str(_activity_value(from_user, "aad_object_id")),
            _coerce_str(_activity_value(from_user, "id")),
        }
        if bot_ids and any(value in bot_ids for value in sender_values if value):
            return

        conversation = _activity_value(activity, "conversation")
        conversation_id = _coerce_str(_activity_value(conversation, "id"))
        if not conversation_id:
            return

        activity_id = _coerce_str(_activity_value(activity, "id"))
        text, regex_mentioned = _strip_mentions(_coerce_str(_activity_value(activity, "text")))
        mentioned = self._activity_mentions_bot(activity, bot_ids, regex_mentioned)
        service_url = _first_non_empty(
            _activity_value(activity, "service_url"),
            _activity_value(activity, "serviceUrl"),
        )
        if service_url:
            self._conversation_service_urls[conversation_id] = service_url
        attachment_text, media_url, media_mime = self._extract_attachment(activity)
        if attachment_text:
            text = f"{text}\n\n{attachment_text}".strip() if text else attachment_text
        if not text and not media_url:
            return

        chat_type = _conversation_chat_type(_coerce_str(_activity_value(conversation, "conversation_type")))
        message_id = f"{conversation_id}:{activity_id}" if activity_id else conversation_id
        inbound = InboundMessage(
            channel_id="teams",
            account_id=self._account_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=_coerce_str(_activity_value(from_user, "name")) or None,
            chat_id=conversation_id,
            chat_type=chat_type,
            text=text,
            media_url=media_url,
            media_mime=media_mime,
            reply_to_id=_coerce_str(_activity_value(activity, "reply_to_id")) or None,
            thread_id=_coerce_str(_activity_value(activity, "reply_to_id")) or None,
            mentioned=mentioned,
            mention_text=text if mentioned else "",
            raw=activity,
        )
        await self._on_message(inbound)
        self.record_message()

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        app = self._app
        if app is None:
            return DeliveryResult(
                channel_id="teams",
                message_id="",
                chat_id=ctx.to,
                success=False,
                error="Teams channel not started",
                retryable=True,
            )
        if not ctx.to:
            return DeliveryResult(
                channel_id="teams",
                message_id="",
                success=False,
                error="Missing Teams conversation id",
            )

        try:
            self._apply_service_url(ctx.to)
            if self._can_reply(ctx.reply_to_id) and hasattr(app, "reply"):
                result = await app.reply(ctx.to, ctx.reply_to_id, ctx.text)
            else:
                result = await app.send(ctx.to, ctx.text)
        except Exception as exc:
            return DeliveryResult(
                channel_id="teams",
                message_id="",
                chat_id=ctx.to,
                success=False,
                error=f"Teams send failed: {exc}",
                retryable=True,
            )

        self.record_message()
        return DeliveryResult(
            channel_id="teams",
            message_id=_coerce_str(_activity_value(result, "id")),
            chat_id=ctx.to,
            success=True,
        )

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        if not ctx.media_url:
            return await self.send_text(ctx)
        app = self._app
        if app is None:
            return DeliveryResult(
                channel_id="teams",
                message_id="",
                chat_id=ctx.to,
                success=False,
                error="Teams channel not started",
                retryable=True,
            )

        try:
            from microsoft_teams.api import MessageActivityInput
        except ImportError:
            return await super().send_media(ctx)

        try:
            self._apply_service_url(ctx.to)
            attachment = self._build_outbound_attachment(ctx.media_url)
            activity = MessageActivityInput(text=ctx.text or None, attachments=[attachment])
            if self._can_reply(ctx.reply_to_id) and hasattr(app, "reply"):
                result = await app.reply(ctx.to, ctx.reply_to_id, activity)
            else:
                result = await app.send(ctx.to, activity)
        except FileNotFoundError as exc:
            return DeliveryResult(channel_id="teams", message_id="", chat_id=ctx.to, success=False, error=str(exc))
        except Exception as exc:
            return DeliveryResult(
                channel_id="teams",
                message_id="",
                chat_id=ctx.to,
                success=False,
                error=f"Teams send_media failed: {exc}",
                retryable=True,
            )

        self.record_message()
        return DeliveryResult(
            channel_id="teams",
            message_id=_coerce_str(_activity_value(result, "id")),
            chat_id=ctx.to,
            success=True,
        )

    @staticmethod
    def _normalise_webhook_response(response: Any) -> dict:
        status_code = int(_activity_value(response, "status", _activity_value(response, "status_code", 200)) or 200)
        body = _activity_value(response, "body", None)
        if isinstance(response, dict):
            body = response.get("body", response if "body" not in response else body)
            status_code = int(response.get("status", response.get("status_code", status_code)) or status_code)

        if isinstance(body, dict):
            return {**body, "status_code": status_code}
        if body in (None, ""):
            return {"ok": True, "status_code": status_code}
        return {"body": body, "status_code": status_code}

    @staticmethod
    def _extract_attachment(activity: Any) -> tuple[str, Optional[str], Optional[str]]:
        attachments = _activity_value(activity, "attachments", []) or []
        if not attachments:
            return "", None, None

        labels: list[str] = []
        first_url: Optional[str] = None
        first_mime: Optional[str] = None
        for attachment in attachments:
            name = _first_non_empty(
                _activity_value(attachment, "name"),
                _activity_value(attachment, "content_type"),
                "attachment",
            )
            labels.append(f"[Teams attachment: {name}]")
            url = _coerce_str(_activity_value(attachment, "content_url"))
            if url and first_url is None:
                first_url = url
                first_mime = _coerce_str(_activity_value(attachment, "content_type")) or None

        return "\n".join(labels), first_url, first_mime

    def _bot_identifiers(self, resolved_config: dict[str, Any]) -> set[str]:
        return {
            value
            for value in (
                _coerce_str(_activity_value(self._app, "id")),
                _coerce_str(resolved_config.get("_bot_id")),
                _coerce_str(resolved_config.get("_bot_user_id")),
                _coerce_str(resolved_config.get("_bot_aad_object_id")),
                _coerce_str(resolved_config.get("_client_id")),
            )
            if value
        }

    @staticmethod
    def _activity_mentions_bot(activity: Any, bot_ids: set[str], regex_mentioned: bool) -> bool:
        entities = _activity_value(activity, "entities", []) or []
        mention_entities = [
            entity for entity in entities
            if _coerce_str(_activity_value(entity, "type")).lower() == "mention"
        ]
        if not mention_entities:
            return regex_mentioned

        for entity in mention_entities:
            mentioned = _activity_value(entity, "mentioned", {}) or {}
            mention_ids = {
                _coerce_str(_activity_value(mentioned, "id")),
                _coerce_str(_activity_value(mentioned, "aad_object_id")),
                _coerce_str(_activity_value(mentioned, "aadObjectId")),
            }
            if bot_ids and any(value in bot_ids for value in mention_ids if value):
                return True

        return False

    @staticmethod
    def _can_reply(reply_to_id: Optional[str]) -> bool:
        value = _coerce_str(reply_to_id)
        return bool(value and value.isdigit() and value != "0")

    def _apply_service_url(self, conversation_id: str) -> None:
        app = self._app
        if app is None:
            return
        service_url = self._conversation_service_urls.get(conversation_id) or _resolve_config(self._config).get("_service_url")
        if not service_url:
            return
        try:
            setattr(app, "service_url", service_url)
        except Exception:
            pass
        try:
            setattr(app, "_service_url", service_url)
        except Exception:
            pass

    @staticmethod
    def _build_outbound_attachment(media_url: str) -> Any:
        from microsoft_teams.api import Attachment

        mime = mimetypes.guess_type(media_url)[0] or "application/octet-stream"
        if _looks_like_url(media_url):
            name = Path(urlparse(media_url).path).name or "attachment"
            return Attachment(content_type=mime, content_url=media_url, name=name)

        path = _path_from_file_url(media_url) if media_url.startswith("file://") else Path(media_url)
        if not path.exists():
            raise FileNotFoundError(str(path))
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return Attachment(
            content_type=mime,
            content_url=f"data:{mime};base64,{data}",
            name=path.name,
        )
