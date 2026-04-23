"""
DingTalk Stream Mode inbound (Python-native).

Wraps the official ``dingtalk-stream`` SDK (>= 0.20) so that incoming
ChatBot messages are converted into Flocks :class:`InboundMessage`
instances and forwarded to the gateway dispatcher.

This module is the inbound counterpart to :mod:`flocks.channel.builtin.dingtalk.send`
and replaces the legacy Node.js ``runner.ts`` connector.

Design notes
------------
* One :class:`DingTalkStreamRunner` per account.  Each runner owns a
  long-lived WebSocket connection to ``wss://wss-open.dingtalk.com``
  managed by ``dingtalk_stream.DingTalkStreamClient``.
* SDK >= 0.20 is fully async; ``ChatbotHandler.process()`` must return
  ``(status_code, str)`` quickly and dispatch the heavy lifting in the
  background, otherwise heartbeats stall and the connection drops.
* The SDK's own ``start()`` swallows every error from
  ``open_connection()`` (returns ``None``) and retries forever — so
  bad credentials would silently spin without ever surfacing.  We work
  around it with a one-shot pre-flight against the same gateway endpoint
  before delegating to the SDK; permanent 4xx auth failures abort the
  reconnect loop instead of looping endlessly.
* Group-chat gating mirrors Feishu / WeCom: by default groups require
  an explicit @mention or an entry in ``free_response_chats``; DMs are
  unconditional aside from the optional ``allowed_users`` allow-list.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import uuid
from typing import Any, Awaitable, Callable, Optional

import httpx

from flocks.channel.base import ChatType, InboundMessage
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk.stream")

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotMessage
    from dingtalk_stream.frames import AckMessage, CallbackMessage

    DINGTALK_STREAM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when the optional dep is missing
    DINGTALK_STREAM_AVAILABLE = False
    dingtalk_stream = None  # type: ignore[assignment]
    ChatbotMessage = None  # type: ignore[assignment]
    CallbackMessage = None  # type: ignore[assignment]
    AckMessage = type(
        "AckMessage",
        (),
        {"STATUS_OK": 200, "STATUS_SYSTEM_EXCEPTION": 500},
    )  # type: ignore[assignment]


# Reconnection backoff schedule (seconds) — matches hermes-agent's defaults.
_RECONNECT_BACKOFF = [2, 5, 10, 30, 60]

# Group conversation type as reported by DingTalk in ``conversationType``.
_CONVERSATION_TYPE_GROUP = "2"

# Pre-flight target for the Stream Mode WebSocket gateway.  Calling this
# endpoint with bad credentials returns a structured 4xx body that lets
# us distinguish "wrong key/secret" from "transient network blip".
_GATEWAY_OPEN_URL = "https://api.dingtalk.com/v1.0/gateway/connections/open"
_GATEWAY_PREFLIGHT_TIMEOUT = 10.0

# DingTalk error codes that mean "the credentials / app are not valid"
# — retrying with the same secret will never succeed.  Codes are
# documented at https://open.dingtalk.com/document/orgapp/error-code .
_PERMANENT_AUTH_CODES = frozenset({
    "invalidauthentication",       # bad clientSecret
    "invalidappkey",                # bad clientId
    "appnotexist",                  # app deleted / wrong id
    "forbidden.accesstoken",        # app revoked
    "forbidden",                    # generic forbidden
    "subscription.notpermitted",    # Stream Mode not enabled for this app
    "unauthorizedclient",
})


class DingTalkPermanentAuthError(RuntimeError):
    """Raised when the DingTalk gateway rejects the credentials with a
    4xx status that retrying cannot fix (bad clientId/clientSecret, app
    revoked, Stream Mode subscription not enabled, …).
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


OnMessage = Callable[[InboundMessage], Awaitable[None]]


# ---------------------------------------------------------------------------
# Pre-flight: detect permanent credential failures up front
# ---------------------------------------------------------------------------


async def _preflight_open_connection(
    *,
    client_id: str,
    client_secret: str,
    timeout: float = _GATEWAY_PREFLIGHT_TIMEOUT,
) -> None:
    """Probe the Stream Mode gateway with the given credentials.

    Returns silently on success (the returned ticket is single-use, so
    we deliberately throw it away — the SDK will mint its own when it
    actually opens the WebSocket).  Raises
    :class:`DingTalkPermanentAuthError` for 4xx responses with
    auth-related error codes; any other transport / 5xx error is
    re-raised as-is so the caller can apply normal retry semantics.
    """
    payload = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "subscriptions": [],
        "ua": "flocks-dingtalk-preflight/1.0",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            f"flocks-dingtalk-preflight/1.0 "
            f"Python/{platform.python_version()}"
        ),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_GATEWAY_OPEN_URL, json=payload, headers=headers)

    if resp.status_code < 400:
        return

    body: dict = {}
    try:
        body = resp.json() if resp.content else {}
    except (ValueError, json.JSONDecodeError):
        body = {}

    code = str(body.get("code") or "").strip()
    message = str(body.get("message") or resp.text or "").strip()

    # 4xx with a recognised auth code → permanent failure.
    if 400 <= resp.status_code < 500:
        if code.lower() in _PERMANENT_AUTH_CODES or resp.status_code in (401, 403):
            raise DingTalkPermanentAuthError(
                f"DingTalk gateway rejected credentials: "
                f"HTTP {resp.status_code} {code or '<no-code>'}: {message}",
                code=code or None,
                http_status=resp.status_code,
            )
        # Other 4xx (e.g. 400 with a transient validation error) — let
        # the caller retry; the SDK's own loop may also recover.
        raise httpx.HTTPStatusError(
            f"DingTalk gateway preflight failed: HTTP {resp.status_code}: {message}",
            request=resp.request,
            response=resp,
        )

    raise httpx.HTTPStatusError(
        f"DingTalk gateway preflight failed: HTTP {resp.status_code}: {message}",
        request=resp.request,
        response=resp,
    )


# ---------------------------------------------------------------------------
# Gating helpers (mirrors hermes-agent's DingTalkAdapter logic)
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [str(part).strip() for part in loaded if str(part).strip()]
        except (json.JSONDecodeError, ValueError):
            pass
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def _compile_mention_patterns(raw: Any) -> list[re.Pattern]:
    patterns = _coerce_str_list(raw)
    compiled: list[re.Pattern] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            log.warning("dingtalk.stream.mention_pattern_invalid", {
                "pattern": pattern, "error": str(exc),
            })
    return compiled


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------


def _extract_text(message: Any) -> str:
    """Pull plain text out of a ChatbotMessage across SDK shapes.

    SDK >= 0.20 exposes ``message.text`` as a ``TextContent`` dataclass
    (``str(text)`` returns ``"TextContent(content=...)"`` — useless),
    while older versions used a plain dict.  Rich-text payloads moved
    from ``message.rich_text`` to ``message.rich_text_content``.
    """
    text = getattr(message, "text", None) or ""
    if hasattr(text, "content"):
        content = (text.content or "").strip()
    elif isinstance(text, dict):
        content = str(text.get("content") or "").strip()
    else:
        content = str(text).strip()

    if content:
        return content

    rich_text = (
        getattr(message, "rich_text_content", None)
        or getattr(message, "rich_text", None)
    )
    if not rich_text:
        return ""

    rich_list = getattr(rich_text, "rich_text_list", None) or rich_text
    if not isinstance(rich_list, list):
        return ""

    parts: list[str] = []
    for item in rich_list:
        if isinstance(item, dict):
            piece = item.get("text") or item.get("content") or ""
            if piece:
                parts.append(piece)
        else:
            piece = getattr(item, "text", None)
            if piece:
                parts.append(piece)
    return " ".join(parts).strip()


def _extract_media_url(message: Any) -> Optional[str]:
    """Return the first media reference (download_code or URL) in *message*.

    DingTalk delivers a ``download_code`` that must be exchanged for a
    short-lived URL via the OAPI; here we pass the raw code through so
    downstream tools can resolve it lazily, matching what other channels
    do for opaque media handles.
    """
    image_content = getattr(message, "image_content", None)
    if image_content:
        code = getattr(image_content, "download_code", None) or getattr(
            image_content, "downloadCode", None
        )
        if code:
            return str(code)

    rich_text = getattr(message, "rich_text_content", None) or getattr(
        message, "rich_text", None
    )
    if rich_text:
        rich_list = getattr(rich_text, "rich_text_list", None) or rich_text
        if isinstance(rich_list, list):
            for item in rich_list:
                if isinstance(item, dict):
                    code = (
                        item.get("downloadCode")
                        or item.get("download_code")
                        or item.get("pictureDownloadCode")
                    )
                    if code:
                        return str(code)
    return None


# ---------------------------------------------------------------------------
# Gating decisions per inbound message
# ---------------------------------------------------------------------------


class _MessageGate:
    """Encapsulates the require_mention / allowed_users / mention_patterns
    rules so the SDK handler stays thin.
    """

    def __init__(self, account_config: dict) -> None:
        self.require_mention = _truthy(
            account_config.get("requireMention", account_config.get("require_mention", False))
        )
        self.free_response_chats: set[str] = set(
            _coerce_str_list(
                account_config.get("freeResponseChats")
                or account_config.get("free_response_chats")
            )
        )
        self.mention_patterns = _compile_mention_patterns(
            account_config.get("mentionPatterns")
            or account_config.get("mention_patterns")
        )
        self.allowed_users: set[str] = {
            item.lower()
            for item in _coerce_str_list(
                account_config.get("allowedUsers")
                or account_config.get("allowed_users")
            )
        }

    def is_user_allowed(self, sender_id: str, sender_staff_id: str) -> bool:
        if not self.allowed_users or "*" in self.allowed_users:
            return True
        candidates = {(sender_id or "").lower(), (sender_staff_id or "").lower()}
        candidates.discard("")
        return bool(candidates & self.allowed_users)

    def should_process(
        self,
        message: Any,
        text: str,
        is_group: bool,
        chat_id: str,
    ) -> bool:
        if not is_group:
            return True
        if chat_id and chat_id in self.free_response_chats:
            return True
        if not self.require_mention:
            return True
        if bool(getattr(message, "is_in_at_list", False)):
            return True
        if text and self.mention_patterns:
            return any(p.search(text) for p in self.mention_patterns)
        return False


# ---------------------------------------------------------------------------
# Conversion: ChatbotMessage → InboundMessage
# ---------------------------------------------------------------------------


def chatbot_message_to_inbound(
    message: Any,
    *,
    channel_id: str,
    account_id: str,
) -> Optional[InboundMessage]:
    """Convert a parsed ``dingtalk_stream.ChatbotMessage`` into an InboundMessage.

    Returns ``None`` for empty messages so the handler can drop them
    silently rather than spamming the dispatcher.
    """
    text = _extract_text(message)
    media_url = _extract_media_url(message)
    if not text and not media_url:
        return None

    conversation_id = str(getattr(message, "conversation_id", "") or "")
    conversation_type = str(getattr(message, "conversation_type", "1") or "1")
    is_group = conversation_type == _CONVERSATION_TYPE_GROUP

    sender_id = str(getattr(message, "sender_id", "") or "")
    sender_staff_id = str(getattr(message, "sender_staff_id", "") or "")
    sender_nick = str(getattr(message, "sender_nick", "") or sender_id)

    # DingTalk delivers a ``conversation_id`` (cid…) for *both* DMs and
    # group chats — but only group chats can be replied to via
    # ``/v1.0/robot/groupMessages/send``.  DMs MUST be sent to the
    # user's ``staffId`` via ``/v1.0/robot/oToMessages/batchSend``;
    # routing a DM through the group endpoint fails with
    # ``robot 不存在``.  ``resolve_target_kind`` infers the route from
    # the ``chat_id`` prefix (``cid`` → group, otherwise → user), so
    # picking the right id here is what keeps outbound replies routed
    # correctly.
    if is_group:
        chat_id = conversation_id or sender_staff_id or sender_id
    else:
        chat_id = sender_staff_id or sender_id or conversation_id
    chat_type = ChatType.GROUP if is_group else ChatType.DIRECT
    mentioned = bool(getattr(message, "is_in_at_list", False)) if is_group else False

    msg_id = str(getattr(message, "message_id", "") or uuid.uuid4().hex)

    return InboundMessage(
        channel_id=channel_id,
        account_id=account_id,
        message_id=msg_id,
        sender_id=sender_staff_id or sender_id,
        sender_name=sender_nick,
        chat_id=chat_id,
        chat_type=chat_type,
        text=text,
        media_url=media_url,
        mentioned=mentioned,
        mention_text="",
        raw=message,
    )


# ---------------------------------------------------------------------------
# Stream client wrapper
# ---------------------------------------------------------------------------


class DingTalkStreamRunner:
    """Owns one ``DingTalkStreamClient`` and forwards inbound messages.

    The runner only does inbound conversion + gating; connection-status
    bookkeeping (``mark_connected`` / ``mark_disconnected`` /
    ``record_message``) is the gateway's responsibility.  See
    :meth:`flocks.channel.gateway.manager.ChannelGateway._run_with_reconnect`
    and ``_make_on_message`` for where those hooks fire.
    """

    def __init__(
        self,
        *,
        account_config: dict,
        on_message: OnMessage,
    ) -> None:
        self.account_config = account_config
        self.account_id = str(account_config.get("_account_id") or "default")
        self.client_id = str(
            account_config.get("appKey") or account_config.get("clientId") or ""
        )
        self.client_secret = str(
            account_config.get("appSecret")
            or account_config.get("clientSecret")
            or ""
        )

        self._on_message = on_message

        self._gate = _MessageGate(account_config)
        self._stream_client: Any = None
        self._stream_task: Optional[asyncio.Task] = None
        self._running = False
        self._dispatch_tasks: set[asyncio.Task] = set()
        # Set by ``_run_with_reconnect`` when the gateway rejects the
        # credentials with a 4xx — surfaced from ``run()`` after shutdown
        # so the channel layer can stop retrying that account.
        self._permanent_error: Optional[DingTalkPermanentAuthError] = None

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def run(self, abort_event: Optional[asyncio.Event] = None) -> None:
        """Connect and block until *abort_event* is set or run() is cancelled."""
        if not DINGTALK_STREAM_AVAILABLE:
            raise RuntimeError(
                "dingtalk-stream is not installed. "
                "Run `pip install 'dingtalk-stream>=0.20'` to enable the DingTalk channel."
            )
        if not self.is_configured():
            raise ValueError(
                "DingTalk account missing appKey/appSecret (also accepted as "
                "clientId/clientSecret)"
            )

        credential = dingtalk_stream.Credential(self.client_id, self.client_secret)
        self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

        handler = _IncomingHandler(self)
        self._stream_client.register_callback_handler(
            dingtalk_stream.ChatbotMessage.TOPIC, handler
        )

        self._running = True
        self._stream_task = asyncio.create_task(self._run_with_reconnect())
        try:
            if abort_event is None:
                await self._stream_task
            else:
                await self._wait_for_abort(abort_event)
        finally:
            await self._shutdown()

        # Surface permanent auth failure AFTER cleanup so the channel
        # can drop this account from the reconnect schedule.
        if self._permanent_error is not None:
            raise self._permanent_error

    async def _wait_for_abort(self, abort_event: asyncio.Event) -> None:
        abort_waiter = asyncio.create_task(abort_event.wait())
        done, pending = await asyncio.wait(
            {abort_waiter, self._stream_task} if self._stream_task else {abort_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _run_with_reconnect(self) -> None:
        backoff_idx = 0
        while self._running:
            # Pre-flight credential check: surfaces 4xx auth errors that
            # the SDK would otherwise silently swallow inside its own
            # forever-retry loop.  Performed every iteration so that an
            # app revoked mid-flight also breaks us out cleanly.
            try:
                await _preflight_open_connection(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )
            except DingTalkPermanentAuthError as exc:
                log.error("dingtalk.stream.permanent_auth_failure", {
                    "account": self.account_id,
                    "code": exc.code,
                    "http_status": exc.http_status,
                    "error": str(exc),
                })
                self._permanent_error = exc
                return
            except asyncio.CancelledError:
                return
            except Exception as exc:
                # Transient pre-flight failure (network blip, 5xx, …) —
                # log and fall through to the SDK, which will re-attempt
                # ``open_connection`` with its own loop.
                log.warning("dingtalk.stream.preflight_transient_error", {
                    "account": self.account_id, "error": str(exc),
                })

            try:
                log.debug("dingtalk.stream.starting", {"account": self.account_id})
                await self._stream_client.start()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if not self._running:
                    return
                log.warning("dingtalk.stream.error", {
                    "account": self.account_id, "error": str(exc),
                })

            if not self._running:
                return

            delay = _RECONNECT_BACKOFF[
                min(backoff_idx, len(_RECONNECT_BACKOFF) - 1)
            ]
            log.info("dingtalk.stream.reconnecting", {
                "account": self.account_id, "delay_seconds": delay,
            })
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            backoff_idx += 1

    async def _shutdown(self) -> None:
        self._running = False

        client = self._stream_client
        websocket = getattr(client, "websocket", None) if client else None
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass

        if self._stream_task:
            if client is not None and hasattr(client, "close"):
                try:
                    await asyncio.to_thread(client.close)
                except Exception:
                    pass
            self._stream_task.cancel()
            try:
                await asyncio.wait_for(self._stream_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._stream_task = None

        for task in list(self._dispatch_tasks):
            task.cancel()
        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()

        self._stream_client = None

    # -- inbound dispatch ------------------------------------------------

    async def _dispatch(self, chatbot_msg: Any) -> None:
        try:
            text = _extract_text(chatbot_msg)
            sender_id = str(getattr(chatbot_msg, "sender_id", "") or "")
            sender_staff_id = str(getattr(chatbot_msg, "sender_staff_id", "") or "")

            if not self._gate.is_user_allowed(sender_id, sender_staff_id):
                log.debug("dingtalk.stream.user_not_allowed", {
                    "account": self.account_id,
                    "sender_id": sender_id,
                    "sender_staff_id": sender_staff_id,
                })
                return

            conversation_type = str(
                getattr(chatbot_msg, "conversation_type", "1") or "1"
            )
            is_group = conversation_type == _CONVERSATION_TYPE_GROUP
            chat_id = str(getattr(chatbot_msg, "conversation_id", "") or "") or sender_id

            if not self._gate.should_process(chatbot_msg, text, is_group, chat_id):
                log.debug("dingtalk.stream.gate_dropped", {
                    "account": self.account_id, "chat_id": chat_id,
                })
                return

            inbound = chatbot_message_to_inbound(
                chatbot_msg,
                channel_id="dingtalk",
                account_id=self.account_id,
            )
            if inbound is None:
                return

            await self._on_message(inbound)
        except Exception:
            log.exception("dingtalk.stream.dispatch_error", {
                "account": self.account_id,
            })

    def _spawn_dispatch(self, chatbot_msg: Any) -> None:
        task = asyncio.create_task(self._dispatch(chatbot_msg))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)


# ---------------------------------------------------------------------------
# SDK callback handler
# ---------------------------------------------------------------------------


class _IncomingHandler(
    dingtalk_stream.ChatbotHandler if DINGTALK_STREAM_AVAILABLE else object  # type: ignore[misc]
):
    """``ChatbotHandler`` subclass that converts SDK callbacks → InboundMessage.

    The SDK invokes ``process()`` once per inbound frame; we MUST ack
    quickly so heartbeats keep flowing, otherwise the connection is
    torn down server-side.  The actual gating + dispatch runs in a
    background task (tracked on the runner so shutdown can cancel it).
    """

    def __init__(self, runner: DingTalkStreamRunner) -> None:
        if DINGTALK_STREAM_AVAILABLE:
            super().__init__()
        self._runner = runner

    async def process(self, message: Any):  # type: ignore[override]
        try:
            data = getattr(message, "data", None)
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return AckMessage.STATUS_OK, "OK"

            chatbot_msg = ChatbotMessage.from_dict(data)

            # SDKs across versions disagree on whether ``session_webhook``
            # and ``isInAtList`` are mapped automatically — backfill from
            # the raw payload when they are missing.
            if not getattr(chatbot_msg, "session_webhook", None):
                webhook = data.get("sessionWebhook") or data.get("session_webhook")
                if webhook:
                    chatbot_msg.session_webhook = webhook

            if not getattr(chatbot_msg, "is_in_at_list", False):
                if data.get("isInAtList"):
                    chatbot_msg.is_in_at_list = True

            self._runner._spawn_dispatch(chatbot_msg)
        except Exception:
            log.exception("dingtalk.stream.handler_error")
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "error"
        return AckMessage.STATUS_OK, "OK"


__all__ = [
    "DINGTALK_STREAM_AVAILABLE",
    "DingTalkPermanentAuthError",
    "DingTalkStreamRunner",
    "chatbot_message_to_inbound",
]
