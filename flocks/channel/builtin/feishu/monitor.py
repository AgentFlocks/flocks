"""
Feishu event subscription via WebSocket (long-connection mode).

Uses the Feishu SDK's WebSocket client to receive real-time events.
Supports multiple accounts: each account starts its own WSClient.

Additional capabilities:
- Persistent dedup (FeishuDedup): prevents reprocessing messages across restarts
- Inbound debounce (InboundDebouncer): merges rapid consecutive messages
- Emoji Reaction: converts reaction events to synthetic messages for the Agent
- Per-group fine-grained control: supports groups.<chat_id> specific policies
- Bot identity resolution: precisely detects @mentions targeting this Bot
- Card button events: card.action.trigger → synthetic message dispatch
- share_chat / merge_forward message parsing
- Bot join / leave group event logging
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import threading
import uuid
from urllib.parse import urlsplit
from typing import Any, Awaitable, Callable, Optional

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.builtin.feishu.config import (
    list_account_configs,
    merge_group_overrides,
    resolve_api_base,
)
from flocks.utils.log import Log

log = Log.create(service="channel.feishu.monitor")

# _chat_locks LRU cap: evict oldest unlocked entries when exceeded
_CHAT_LOCKS_MAX = 2000


def _extract_ws_close_code(exc: BaseException | None) -> int | None:
    """Return a websocket close code from common exception shapes."""
    if exc is None:
        return None
    for attr in ("rcvd", "sent"):
        frame = getattr(exc, attr, None)
        code = getattr(frame, "code", None)
        if isinstance(code, int):
            return code
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _is_normal_ws_close(exc: BaseException | None) -> bool:
    """Return True when the websocket closed cleanly."""
    if exc is None:
        return False
    if type(exc).__name__ == "ConnectionClosedOK":
        return True
    return _extract_ws_close_code(exc) in {1000, 1001}


def _resolve_ws_domain(config: dict) -> str:
    """Return the domain root expected by the modern lark-oapi websocket client."""
    api_base = resolve_api_base(config)
    parts = urlsplit(api_base)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return "https://open.feishu.cn"


def _build_ws_client(
    *,
    app_id: str,
    app_secret: str,
    event_handler: Callable[[dict], None],
    domain: str,
):
    """Build a websocket client compatible with both old and new lark-oapi SDKs."""
    try:
        import lark_oapi as lark
        from lark_oapi.adapter.websocket import WSClient

        return WSClient(
            app_id=app_id,
            app_secret=app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )
    except ImportError:
        lark = importlib.import_module("lark_oapi")
        ws_module = importlib.import_module("lark_oapi.ws.client")
        native_client_cls = ws_module.Client

        class _Dispatcher:
            def do_without_validation(self, payload: bytes) -> None:
                try:
                    data = json.loads(payload.decode("utf-8"))
                except Exception as e:
                    log.error("feishu.ws.parse_error", {"error": str(e)})
                    return None
                event_handler(data)
                return None

        class _CompatWSClient:
            def __init__(self) -> None:
                self._client = native_client_cls(
                    app_id=app_id,
                    app_secret=app_secret,
                    log_level=lark.LogLevel.WARNING,
                    event_handler=_Dispatcher(),
                    domain=domain,
                    auto_reconnect=False,
                )
                self._thread: Optional[threading.Thread] = None
                self._loop: Optional[asyncio.AbstractEventLoop] = None
                self._start_error: Optional[BaseException] = None
                self._stop_requested = False
                self._finished = threading.Event()

            def start(self) -> None:
                def _run() -> None:
                    self._loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._loop)
                    ws_module.loop = self._loop

                    async def _receive_message_loop() -> None:
                        try:
                            while True:
                                if self._client._conn is None:
                                    raise RuntimeError("connection is closed")
                                msg = await self._client._conn.recv()
                                asyncio.get_running_loop().create_task(
                                    self._client._handle_message(msg)
                                )
                        except Exception as e:
                            if self._stop_requested and _is_normal_ws_close(e):
                                await self._client._disconnect()
                                return
                            log.error("feishu.ws.receive_loop_error", {
                                "app_id": app_id,
                                "error": str(e),
                            })
                            await self._client._disconnect()
                            if self._client._auto_reconnect:
                                await self._client._reconnect()
                            else:
                                raise

                    self._client._receive_message_loop = _receive_message_loop
                    try:
                        self._client.start()
                    except RuntimeError as e:
                        if "Event loop stopped before Future completed" not in str(e):
                            self._start_error = e
                    except BaseException as e:  # pragma: no cover - defensive
                        self._start_error = e
                    finally:
                        self._finished.set()

                self._thread = threading.Thread(
                    target=_run,
                    name=f"feishu-ws-{app_id}",
                    daemon=True,
                )
                self._thread.start()
                self._finished.wait(timeout=0.2)
                if self._start_error:
                    raise RuntimeError(str(self._start_error)) from self._start_error

            def stop(self) -> None:
                if self._loop is None:
                    return
                self._stop_requested = True
                with contextlib.suppress(Exception):
                    future = asyncio.run_coroutine_threadsafe(
                        self._client._disconnect(),
                        self._loop,
                    )
                    future.result(timeout=5)
                with contextlib.suppress(Exception):
                    self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread:
                    self._thread.join(timeout=5)

            @property
            def start_error(self) -> Optional[BaseException]:
                return self._start_error

        return _CompatWSClient()


def _list_enabled_accounts(config: dict) -> list[dict]:
    """Return enabled, credentialed Feishu account configs for WebSocket mode."""
    accounts = list_account_configs(
        config,
        webhook_only=False,
        require_credentials=True,
    )
    result: list[dict] = []
    for account in accounts:
        if account.get("connectionMode", "websocket") != "websocket":
            continue
        result.append(account)
    return result


async def start_websocket(
    config: dict,
    on_message: Callable[[InboundMessage], Awaitable[None]],
    abort_event: Optional[asyncio.Event] = None,
) -> None:
    """Start Feishu WebSocket long-connections for all configured accounts."""
    accounts = _list_enabled_accounts(config)
    if not accounts:
        raise RuntimeError("No enabled Feishu accounts found in config")

    if len(accounts) == 1:
        await _start_single_websocket(accounts[0], on_message, abort_event)
        return

    tasks = [
        asyncio.create_task(
            _start_single_websocket(acc, on_message, abort_event),
            name=f"feishu-ws-{acc['_account_id']}",
        )
        for acc in accounts
    ]
    try:
        # return_exceptions=True: one account failing does not affect others
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for acc, result in zip(accounts, results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.error("feishu.ws.account_failed", {
                    "account_id": acc["_account_id"],
                    "error": str(result),
                })
    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        # Wait for all tasks to finish cancellation to avoid "Task destroyed but pending" warnings
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _start_single_websocket(
    config: dict,
    on_message: Callable[[InboundMessage], Awaitable[None]],
    abort_event: Optional[asyncio.Event] = None,
) -> None:
    """Start a single Feishu WebSocket long-connection for one account."""
    try:
        importlib.import_module("lark_oapi")
    except ImportError:
        log.error("feishu.ws.sdk_missing", {
            "hint": "pip install lark-oapi to use WebSocket mode",
        })
        raise RuntimeError(
            "lark-oapi package not installed. "
            "Run `pip install lark-oapi` to enable Feishu WebSocket mode."
        )

    app_id = config.get("appId", "")
    app_secret = config.get("appSecret", "")
    account_id = config.get("_account_id", "default")
    debounce_ms = int(config.get("inboundDebounceMs", 800))
    dedup_enabled = bool(config.get("dedupEnabled", False))
    dedup_ttl = int(config.get("dedupTtlSeconds", 86400))

    # ── Pre-fetch bot identity: get bot open_id for accurate @mention detection ──
    from flocks.channel.builtin.feishu.identity import get_bot_identity
    bot_open_id, bot_name = await get_bot_identity(config, account_id)
    if bot_open_id:
        log.info("feishu.ws.bot_identity", {
            "account_id": account_id,
            "bot_open_id": bot_open_id,
            "bot_name": bot_name,
        })
    else:
        log.warning("feishu.ws.bot_identity_unknown", {
            "account_id": account_id,
            "hint": "Unable to resolve bot open_id; @mention detection may be inaccurate",
        })

    # ── Persistent dedup warmup ─────────────────────────────────────────────
    from flocks.channel.builtin.feishu.dedup import get_dedup
    dedup = await get_dedup(account_id, ttl_seconds=dedup_ttl)
    if dedup_enabled:
        warmup_count = await dedup.warmup()
        if warmup_count > 0:
            log.info("feishu.ws.dedup_warmup", {
                "account_id": account_id, "loaded": warmup_count,
            })
    else:
        log.debug("feishu.ws.dedup_disabled", {"account_id": account_id})

    # ── Per-chat serial queue: LRU cap prevents memory leaks ─────────────────
    from collections import OrderedDict
    _chat_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def _get_chat_lock(chat_id: str) -> asyncio.Lock:
        if chat_id in _chat_locks:
            _chat_locks.move_to_end(chat_id)
            return _chat_locks[chat_id]
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
        # LRU eviction: remove oldest unlocked entry when over the limit
        while len(_chat_locks) > _CHAT_LOCKS_MAX:
            oldest_key = next(iter(_chat_locks))
            oldest_lock = _chat_locks[oldest_key]
            if not oldest_lock.locked():
                del _chat_locks[oldest_key]
            else:
                # Oldest entry is still locked; move to end and keep it
                _chat_locks.move_to_end(oldest_key)
                break
        return lock

    # ── Write suppressed_ids back to dedup ────────────────────────────────
    async def _record_suppressed(ids: list[str]) -> None:
        if not dedup_enabled:
            return
        for mid in ids:
            await dedup.is_duplicate(mid)   # is_duplicate records the ID; duplicate hits return True harmlessly

    # ── Inbound debouncer ──────────────────────────────────────────────────
    from flocks.channel.builtin.feishu.debounce import get_debouncer
    debouncer = get_debouncer(
        account_id=account_id,
        debounce_ms=debounce_ms,
        on_flush=on_message,
        on_suppressed_ids=_record_suppressed,
    )

    running_loop = asyncio.get_running_loop()

    def _on_future_done(future: Any) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc:
            log.error("feishu.ws.dispatch_error", {
                "account_id": account_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    async def _dispatch(data: dict) -> None:
        """Parse the event and process it through dedup → debounce → on_message pipeline."""
        event_type = (data.get("header") or {}).get("event_type", "")

        # Explicitly ignore message-read receipts to avoid spurious unregistered-event warnings
        if event_type == "im.message.message_read_v1":
            return

        # ── Bot join / leave group events ──────────────────────────────────
        if event_type == "im.chat.member.bot.added_v1":
            chat_id = (data.get("event") or {}).get("chat_id", "")
            log.info("feishu.bot.added_to_chat", {
                "account_id": account_id, "chat_id": chat_id,
            })
            return

        if event_type == "im.chat.member.bot.deleted_v1":
            chat_id = (data.get("event") or {}).get("chat_id", "")
            log.info("feishu.bot.removed_from_chat", {
                "account_id": account_id, "chat_id": chat_id,
            })
            return

        # ── Card button click event ─────────────────────────────────────────
        if event_type == "card.action.trigger":
            msg = _parse_card_action_event(data, config)
            if msg:
                if dedup_enabled and await dedup.is_duplicate(msg.message_id):
                    log.debug("feishu.ws.synthetic_dedup_skip", {
                        "account_id": account_id,
                        "message_id": msg.message_id,
                    })
                    return
                await on_message(msg)
            return

        # ── Regular message ─────────────────────────────────────────────────
        if event_type == "im.message.receive_v1":
            msg = _parse_event(data, config, bot_open_id=bot_open_id)
            if msg is None:
                return

            # Persistent dedup
            if dedup_enabled and await dedup.is_duplicate(msg.message_id):
                log.debug("feishu.ws.dedup_skip", {
                    "account_id": account_id,
                    "message_id": msg.message_id,
                })
                return

            # Process same chat/session serially to prevent concurrent reordering
            chat_lock = _get_chat_lock(msg.chat_id)
            async with chat_lock:
                await debouncer.enqueue(msg)

        # ── Emoji Reaction ──────────────────────────────────────────────────
        elif event_type == "im.message.reaction.created_v1":
            reaction_policy = config.get("reactionNotifications", "off")
            if reaction_policy == "off":
                return
            msg = await _parse_reaction_event(data, config)
            if msg:
                if dedup_enabled and await dedup.is_duplicate(msg.message_id):
                    log.debug("feishu.ws.synthetic_dedup_skip", {
                        "account_id": account_id,
                        "message_id": msg.message_id,
                    })
                    return
                await on_message(msg)

    def _event_handler(data: dict) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                _dispatch(data), running_loop,
            )
            future.add_done_callback(_on_future_done)
        except Exception as e:
            log.error("feishu.ws.parse_error", {
                "account_id": account_id, "error": str(e),
            })

    ws_client = _build_ws_client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=_event_handler,
        domain=_resolve_ws_domain(config),
    )

    log.info("feishu.ws.starting", {"app_id": app_id, "account_id": account_id})
    ws_client.start()

    # Launch background dedup flush task (only when dedup is enabled)
    flush_task = await dedup.start_background_flush() if dedup_enabled else asyncio.create_task(asyncio.sleep(0))

    try:
        if abort_event:
            await abort_event.wait()
        else:
            while True:
                await asyncio.sleep(3600)
    finally:
        flush_task.cancel()
        if dedup_enabled:
            await dedup.flush()   # final flush before exit
        ws_client.stop()
        start_error = getattr(ws_client, "start_error", None)
        log.info("feishu.ws.stopped", {"app_id": app_id, "account_id": account_id})
        if start_error:
            raise RuntimeError(str(start_error)) from start_error


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _parse_event(
    data: dict,
    config: dict,
    bot_open_id: Optional[str] = None,
) -> Optional[InboundMessage]:
    """Convert a raw Feishu im.message.receive_v1 event to InboundMessage."""
    header = data.get("header", {})
    event = data.get("event", {})
    event_type = header.get("event_type", "")

    if event_type != "im.message.receive_v1":
        return None

    msg_data = event.get("message", {})
    sender = event.get("sender", {}).get("sender_id", {})

    msg_type = msg_data.get("message_type", "")
    try:
        content = json.loads(msg_data.get("content", "{}"))
    except json.JSONDecodeError:
        content = {}

    text, media_url = _extract_content(msg_type, content, msg_data)

    if not text and not media_url:
        log.debug("feishu.event.empty_content", {"type": msg_type})
        return None

    chat_id = msg_data.get("chat_id", "")
    chat_type_raw = msg_data.get("chat_type", "")
    chat_type = ChatType.GROUP if chat_type_raw == "group" else ChatType.DIRECT

    mentions = msg_data.get("mentions", [])

    # ── Precise @mention detection: only treat as mentioned if the Bot itself is @-ed ──
    if bot_open_id:
        # Check @_all mention
        raw_content_str = msg_data.get("content", "")
        at_all = "@_all" in raw_content_str
        mentioned = at_all or any(
            (m.get("id") or {}).get("open_id") == bot_open_id
            for m in mentions
        )
    else:
        # bot_open_id unknown: fall back to conservative strategy — any mention counts
        mentioned = bool(mentions)

    mention_text = text
    for m in mentions:
        mention_key = m.get("key", "")
        if mention_key:
            mention_text = mention_text.replace(mention_key, "").strip()

    sender_id = sender.get("open_id", "")
    # Some mobile messages may only have user_id, not open_id
    if not sender_id:
        sender_id = sender.get("user_id", "") or sender.get("union_id", "")

    # Apply group-level access policy
    if not _check_group_policy(config, chat_type, mentioned, sender_id, chat_id):
        return None

    return InboundMessage(
        channel_id="feishu",
        account_id=config.get("_account_id", "default"),
        message_id=msg_data.get("message_id", ""),
        sender_id=sender_id,
        sender_name=None,   # requires contact API lookup; see sender_name module
        chat_id=chat_id,
        chat_type=chat_type,
        text=text,
        media_url=media_url,
        mentioned=mentioned,
        mention_text=mention_text,
        thread_id=msg_data.get("root_id") or None,
        reply_to_id=msg_data.get("parent_id") or None,
        raw=data,
    )


# ---------------------------------------------------------------------------
# Card button click event parsing
# ---------------------------------------------------------------------------

def _parse_card_action_event(
    data: dict,
    config: dict,
) -> Optional[InboundMessage]:
    """Convert a card.action.trigger button-click event to a synthetic InboundMessage.

    Extracts the text or command field from action.value as message text,
    constructing a synthetic message to dispatch to the Agent for card interaction.
    """
    event = data.get("event") or data
    operator = event.get("operator") or {}
    action = event.get("action") or {}
    context = event.get("context") or {}
    token = event.get("token", "")

    sender_open_id = operator.get("open_id", "")
    sender_user_id = operator.get("user_id", "")
    sender_id = sender_open_id or sender_user_id
    if not sender_id:
        return None

    chat_id = context.get("chat_id", "") or operator.get("open_id", "")
    has_group_chat = bool(context.get("chat_id", "").strip())
    chat_type = ChatType.GROUP if has_group_chat else ChatType.DIRECT

    # Extract text content from action.value
    action_value = action.get("value") or {}
    if isinstance(action_value, dict):
        content = (
            action_value.get("text")
            or action_value.get("command")
            or json.dumps(action_value, ensure_ascii=False)
        )
    else:
        content = str(action_value)

    if not content:
        return None

    account_id = config.get("_account_id", "default")
    # Synthetic unique message_id with card-action prefix to distinguish from regular messages
    stable_token = token or "unknown"
    action_fingerprint = hashlib.sha256(
        json.dumps(action_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    synthetic_id = (
        f"card-action:{account_id}:{stable_token}:{sender_id}:{chat_id}:{action_fingerprint}"
    )

    log.debug("feishu.card_action.synthetic", {
        "account_id": account_id,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "content": content[:100],
    })

    return InboundMessage(
        channel_id="feishu",
        account_id=account_id,
        message_id=synthetic_id,
        sender_id=sender_id,
        sender_name=None,
        chat_id=chat_id,
        chat_type=chat_type,
        text=content,
        media_url=None,
        mentioned=True,  # card click is treated as a direct trigger, equivalent to @Bot
        mention_text=content,
        thread_id=None,
        raw=data,
    )

async def _parse_reaction_event(
    data: dict,
    config: dict,
) -> Optional[InboundMessage]:
    """Convert an Emoji Reaction event to a synthetic InboundMessage.

    reactionNotifications policy:
    - "off"   → ignored (filtered before calling this)
    - "own"   → only respond to reactions on the Bot's own messages (requires message ownership lookup)
    - "all"   → respond to all reactions
    """
    event = data.get("event", {}) or data  # handle SDK version differences
    message_id = event.get("message_id", "")
    emoji = (event.get("reaction_type") or {}).get("emoji_type", "")
    user_id_info = event.get("user_id") or {}
    sender_id = (
        user_id_info.get("open_id", "")
        or user_id_info.get("user_id", "")
        or user_id_info.get("union_id", "")
    )
    chat_id = event.get("chat_id", "")
    chat_type_raw = event.get("chat_type", "p2p")
    account_id = config.get("_account_id", "default")

    if not message_id or not emoji or not sender_id:
        return None

    # Filter system internal emoji
    if emoji == "Typing":
        return None

    # Filter reactions from the Bot itself (operator_type == "app")
    operator_type = event.get("operator_type", "")
    if operator_type == "app":
        return None

    policy = config.get("reactionNotifications", "off")

    if policy == "own":
        # Query message ownership to confirm the Bot sent it
        is_bot_msg = await _is_bot_message(message_id, config)
        if not is_bot_msg:
            log.debug("feishu.reaction.skip_not_bot_msg", {
                "message_id": message_id, "emoji": emoji,
            })
            return None

    chat_type = ChatType.GROUP if chat_type_raw == "group" else ChatType.DIRECT
    synthetic_id = f"synthetic:reaction:{account_id}:{message_id}:{emoji}:{sender_id}"
    synthetic_text = f"[reacted with {emoji} to message {message_id}]"

    log.debug("feishu.reaction.synthetic", {
        "account_id": account_id,
        "emoji": emoji,
        "sender_id": sender_id,
        "chat_id": chat_id,
    })

    return InboundMessage(
        channel_id="feishu",
        account_id=account_id,
        message_id=synthetic_id,
        sender_id=sender_id,
        chat_id=chat_id or f"p2p:{sender_id}",
        chat_type=chat_type,
        text=synthetic_text,
        mention_text=synthetic_text,
        mentioned=False,
        raw=data,
    )


async def _is_bot_message(message_id: str, config: dict) -> bool:
    """Check whether a message was sent by the Bot (sender_type == app). Returns False on timeout."""
    try:
        from flocks.channel.builtin.feishu.client import api_request_for_account
        account_id = config.get("_account_id", "default")
        data = await asyncio.wait_for(
            api_request_for_account(
                "GET", f"/im/v1/messages/{message_id}",
                config=config,
                account_id=account_id,
            ),
            timeout=1.5,
        )
        # API returns two structures: data.items[0] or data (single message)
        items = (data.get("data") or {}).get("items") or []
        if items:
            raw_item = items[0]
        else:
            raw_item = data.get("data") or {}
        sender_type = (raw_item.get("sender") or {}).get("sender_type", "")
        return sender_type == "app"
    except Exception as e:
        log.debug("feishu.reaction.msg_lookup_failed", {
            "message_id": message_id, "error": str(e),
        })
    return False


# ---------------------------------------------------------------------------
# Group policy (with per-chat fine-grained groups config)
# ---------------------------------------------------------------------------

def _extract_post_first_image_key(content: dict) -> Optional[str]:
    """Extract the first embedded image_key from a post rich-text message."""
    locale_order = ["zh_cn", "en_us"]
    locale_body = None
    for key in locale_order:
        val = content.get(key)
        if isinstance(val, dict):
            locale_body = val
            break
    if locale_body is None:
        for val in content.values():
            if isinstance(val, dict):
                locale_body = val
                break

    if locale_body is None:
        return None

    for paragraph in locale_body.get("content", []):
        for element in paragraph:
            tag = element.get("tag", "")
            if tag == "img":
                image_key = element.get("image_key", "") or element.get("file_key", "")
                if image_key:
                    return image_key
    return None


def _resolve_effective_config(config: dict, chat_id: str) -> dict:
    """Merge top-level config with per-chat overrides; priority: groups.<id> > groups.* > top-level."""
    merged = {**config, **merge_group_overrides(config.get("groups"), chat_id)}
    merged.pop("groups", None)
    return merged


def _check_group_policy(
    config: dict,
    chat_type: ChatType,
    mentioned: bool,
    sender_id: str,
    chat_id: str,
) -> bool:
    """Apply group-level access policy from config.

    Config fields (all optional):
    - ``groupPolicy``: ``"open"`` | ``"allowlist"`` | ``"disabled"``  (default: ``"open"``)
    - ``groupAllowFrom``: list of allowed chat_ids (used when policy=allowlist)
    - ``requireMention``: bool — whether @mention is required in groups (default: True)
    - ``groups.<chat_id>.enabled``: bool — whether this group is enabled (default True)
    - ``groups.<chat_id>.requireMention``: overrides global requireMention
    - ``groups.<chat_id>.allowFrom``: per-group user allowlist

    DMs are never filtered here (handled by InboundDispatcher).
    """
    if chat_type == ChatType.DIRECT:
        return True

    # Merge per-chat config
    eff = _resolve_effective_config(config, chat_id)

    # Whether this group is explicitly disabled
    if eff.get("enabled") is False:
        log.debug("feishu.group.disabled_specific", {"chat_id": chat_id})
        return False

    group_policy = eff.get("groupPolicy", "open")

    if group_policy == "disabled":
        log.debug("feishu.group.disabled", {"chat_id": chat_id})
        return False

    if group_policy == "allowlist":
        allow_from: list = eff.get("groupAllowFrom") or []
        if not allow_from:
            log.debug("feishu.group.allowlist_empty", {"chat_id": chat_id})
            return False
        if chat_id not in allow_from:
            log.debug("feishu.group.chat_not_in_allowlist", {
                "chat_id": chat_id,
            })
            return False

    # Per-chat user allowlist (groups.<chat_id>.allowFrom)
    sender_allow = eff.get("allowFrom") or []
    if sender_allow and sender_id not in sender_allow:
        log.debug("feishu.group.sender_not_in_allowlist", {
            "sender_id": sender_id, "chat_id": chat_id,
        })
        return False

    # requireMention: per-chat config takes precedence
    require_mention = eff.get("requireMention", True)
    if require_mention and not mentioned:
        log.debug("feishu.group.mention_required", {"chat_id": chat_id})
        return False

    return True


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _extract_interactive_text(content: dict) -> str:
    """Extract readable text from an interactive card (schema 2.0 elements)."""
    elements = content.get("elements") or (content.get("body") or {}).get("elements") or []
    texts: list[str] = []
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        tag = elem.get("tag", "")
        if tag == "markdown" and isinstance(elem.get("content"), str):
            texts.append(elem["content"])
        elif tag == "div":
            inner = elem.get("text") or {}
            if isinstance(inner, dict) and isinstance(inner.get("content"), str):
                texts.append(inner["content"])
    return "\n".join(texts).strip() or "[Card message]"


def _extract_content(
    msg_type: str,
    content: dict,
    msg_data: Optional[dict] = None,
) -> tuple[str, Optional[str]]:
    """Extract text and optional media_url from a Feishu message content dict."""
    if msg_type == "text":
        return content.get("text", ""), None

    if msg_type == "image":
        image_key = content.get("image_key", "")
        return "", f"lark://image/{image_key}" if image_key else None

    if msg_type == "file":
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "file")
        return f"[File: {file_name}]", f"lark://file/{file_key}" if file_key else None

    if msg_type == "audio":
        file_key = content.get("file_key", "")
        duration = content.get("duration", "")
        label = f"[Voice message{': ' + str(duration) + 'ms' if duration else ''}]"
        return label, f"lark://file/{file_key}" if file_key else None

    if msg_type == "media":
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "video")
        return f"[Video: {file_name}]", f"lark://file/{file_key}" if file_key else None

    if msg_type == "sticker":
        file_key = content.get("file_key", "")
        return "[Sticker]", f"lark://file/{file_key}" if file_key else None

    if msg_type == "post":
        text = _extract_post_text(content)
        # Extract the first embedded image key from the post rich-text, for media module processing
        first_image_key = _extract_post_first_image_key(content)
        media = f"lark://image/{first_image_key}" if first_image_key else None
        return text, media

    if msg_type == "interactive":
        card_text = _extract_interactive_text(content)
        return card_text, None

    if msg_type == "share_chat":
        # Forwarded group card
        body = content.get("body") or content.get("summary") or ""
        if isinstance(body, str) and body.strip():
            return f"[Shared group chat: {body.strip()}]", None
        share_chat_id = content.get("share_chat_id", "")
        return f"[Shared group chat: {share_chat_id}]" if share_chat_id else "[Shared group chat]", None

    if msg_type == "merge_forward":
        # Merged-forward message: sub-messages require API fetch to expand.
        # Return placeholder text + message ID; dispatcher layer expands asynchronously (see _expand_merge_forward)
        msg_id = (msg_data or {}).get("message_id", "")
        placeholder = f"__merge_forward_expand__{msg_id}" if msg_id else "[Merged forward message]"
        return placeholder, None

    log.debug("feishu.event.unsupported_type", {"type": msg_type})
    return "", None


def _extract_post_text(content: dict) -> str:
    """Flatten a Feishu *post* (rich-text) message into plain text.

    Priority: zh_cn > en_us > first available locale.
    """
    lines: list[str] = []

    # Priority: zh_cn > en_us > first available
    locale_order = ["zh_cn", "en_us"]
    locale_body = None
    for key in locale_order:
        val = content.get(key)
        if isinstance(val, dict):
            locale_body = val
            break
    if locale_body is None:
        for val in content.values():
            if isinstance(val, dict):
                locale_body = val
                break

    if locale_body is None:
        return ""

    title = locale_body.get("title", "")
    if title:
        lines.append(title)
    for paragraph in locale_body.get("content", []):
        parts: list[str] = []
        for element in paragraph:
            tag = element.get("tag", "")
            if tag in ("text", "md"):
                parts.append(element.get("text", ""))
            elif tag == "a":
                parts.append(element.get("text", element.get("href", "")))
            elif tag == "at":
                parts.append(f"@{element.get('user_name', element.get('user_id', ''))}")
            elif tag in ("img", "media"):
                # Embedded image/media in post: use placeholder
                parts.append("[image]" if tag == "img" else "[media]")
        if parts:
            lines.append("".join(parts))
    return "\n".join(lines)
