"""
Weixin (微信) ChannelPlugin implementation.

Connects Flocks to WeChat personal accounts via Tencent's iLink Bot API.
Only accounts registered as iLink bots (via QR scan) are supported.

Design notes:
- Long-poll ``getupdates`` drives inbound delivery.
- Every outbound reply should echo the latest ``context_token`` for the peer.
- Media files move through an AES-128-ECB encrypted CDN protocol — see
  ``media.py`` and ``cdn.py``.
- Token / credentials are obtained via QR login on the iLink Bot developer
  portal, then configured under the ``weixin`` channel.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from typing import Awaitable, Callable, Optional
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

from . import client as ilink
from .config import (
    AIOHTTP_AVAILABLE,
    BACKOFF_DELAY_SECONDS,
    CRYPTO_AVAILABLE,
    ILINK_BASE_URL,
    LONG_POLL_TIMEOUT_MS,
    MAX_CONSECUTIVE_FAILURES,
    MAX_MESSAGE_LENGTH,
    RATE_LIMIT_ERRCODE,
    RETRY_DELAY_SECONDS,
    SESSION_EXPIRED_ERRCODE,
    WEIXIN_CDN_BASE_URL,
)
from .format import format_for_weixin, split_chunks
from .inbound import extract_text, guess_chat_type, safe_id
from .media import (
    MediaCache,
    download_inbound_item,
    fetch_remote_to_temp,
    is_downloadable_media_item,
    send_outbound_file,
)
from .store import (
    ContextTokenStore,
    MessageDedup,
    load_sync_buf,
    save_sync_buf,
)

log = Log.create(service="channel.weixin")

# Local alias to keep type hints readable when aiohttp is missing at import time
if AIOHTTP_AVAILABLE:
    import aiohttp  # type: ignore[import-untyped]


class WeixinChannel(ChannelPlugin):
    """WeChat (微信) personal account channel via Tencent iLink Bot API.

    Prerequisites:
    - A WeChat account registered as an iLink bot (QR scan on the iLink portal).
    - ``aiohttp`` and ``cryptography`` Python packages installed.

    Required config keys:
    - ``token``     — iLink bot token (``WEIXIN_TOKEN`` env var as fallback)
    - ``accountId`` — iLink bot account ID (``WEIXIN_ACCOUNT_ID`` env var as fallback)

    Optional config keys:
    - ``baseUrl``        — iLink API base URL (defaults to ilinkai.weixin.qq.com)
    - ``cdnBaseUrl``     — iLink CDN base URL (defaults to novac2c.cdn.weixin.qq.com)
    - ``dmPolicy``       — ``"open"`` (default) | ``"disabled"`` | ``"allowlist"``
    - ``allowFrom``      — comma-separated list of allowed sender user IDs
    - ``sendChunkDelay`` — seconds between multi-chunk messages (default 1.5)
    - ``dataDir``        — override path for storing sync_buf / context-token / media cache
                          (default: ~/.flocks/workspace/channels/weixin)
    """

    def __init__(self) -> None:
        super().__init__()
        self._token: str = ""
        self._account_id: str = ""
        self._base_url: str = ILINK_BASE_URL
        self._cdn_base_url: str = WEIXIN_CDN_BASE_URL
        self._dm_policy: str = "open"
        self._allow_from: list[str] = []
        self._send_chunk_delay: float = 1.5
        self._send_chunk_retries: int = 4
        self._data_dir: Optional[str] = None

        self._token_store: ContextTokenStore = ContextTokenStore()
        self._dedup: MessageDedup = MessageDedup()
        self._media_cache: Optional[MediaCache] = None

        self._poll_session: "Optional[aiohttp.ClientSession]" = None
        self._send_session: "Optional[aiohttp.ClientSession]" = None

    # ------------------------------------------------------------------
    # ChannelPlugin interface
    # ------------------------------------------------------------------

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="weixin",
            label="微信",
            aliases=["wechat", "wx"],
            order=30,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True,
            threads=False,
            reactions=False,
            edit=False,
            rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        token = config.get("token") or os.getenv("WEIXIN_TOKEN", "")
        account_id = config.get("accountId") or os.getenv("WEIXIN_ACCOUNT_ID", "")
        if not str(token).strip():
            return "Missing required config: token (or WEIXIN_TOKEN env var)"
        if not str(account_id).strip():
            return "Missing required config: accountId (or WEIXIN_ACCOUNT_ID env var)"
        return None

    def config_schema(self) -> Optional[dict]:
        return {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "iLink bot token (从 QR 登录获取)"},
                "accountId": {"type": "string", "description": "iLink bot account ID (从 QR 登录获取)"},
                "baseUrl": {"type": "string", "description": "iLink API 地址", "default": ILINK_BASE_URL},
                "cdnBaseUrl": {"type": "string", "description": "iLink CDN 地址", "default": WEIXIN_CDN_BASE_URL},
                "dmPolicy": {
                    "type": "string",
                    "enum": ["open", "disabled", "allowlist"],
                    "description": "私信策略",
                    "default": "open",
                },
                "allowFrom": {"type": "string", "description": "allowlist 模式下允许的发送者 user_id，逗号分隔"},
                "sendChunkDelay": {"type": "number", "description": "多段消息发送间隔（秒）", "default": 1.5},
                "dataDir": {"type": "string", "description": "状态文件 / 媒体缓存存储目录（默认 ~/.flocks/workspace/channels/weixin）"},
            },
            "required": ["token", "accountId"],
        }

    def target_hint(self) -> str:
        return "<weixin_user_id>"

    @property
    def text_chunk_limit(self) -> int:
        return MAX_MESSAGE_LENGTH

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        return format_for_weixin(text)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        if not (AIOHTTP_AVAILABLE and CRYPTO_AVAILABLE):
            raise RuntimeError(
                "Weixin channel requires ``aiohttp`` and ``cryptography``. "
                "Run: pip install aiohttp cryptography"
            )

        self._token = str(config.get("token") or os.getenv("WEIXIN_TOKEN", "")).strip()
        self._account_id = str(config.get("accountId") or os.getenv("WEIXIN_ACCOUNT_ID", "")).strip()
        self._base_url = str(config.get("baseUrl") or ILINK_BASE_URL).rstrip("/")
        self._cdn_base_url = str(config.get("cdnBaseUrl") or WEIXIN_CDN_BASE_URL).rstrip("/")
        self._dm_policy = str(config.get("dmPolicy") or "open").lower()
        raw_allow = config.get("allowFrom") or ""
        self._allow_from = [s.strip() for s in str(raw_allow).split(",") if s.strip()]
        self._send_chunk_delay = float(config.get("sendChunkDelay") or 1.5)
        self._send_chunk_retries = int(config.get("sendChunkRetries") or 4)
        self._data_dir = config.get("dataDir")

        self._token_store = ContextTokenStore(self._data_dir)
        self._token_store.restore(self._account_id)
        self._dedup = MessageDedup()
        self._media_cache = MediaCache(self._data_dir)

        no_timeout = aiohttp.ClientTimeout(
            total=None, connect=None, sock_connect=None, sock_read=None,
        )
        self._poll_session = aiohttp.ClientSession(
            trust_env=True, connector=ilink.make_ssl_connector(),
        )
        self._send_session = aiohttp.ClientSession(
            trust_env=True, connector=ilink.make_ssl_connector(), timeout=no_timeout,
        )

        self.mark_connected()
        log.info("weixin.connected", {
            "account_id": safe_id(self._account_id),
            "base_url": self._base_url,
        })

        try:
            await self._poll_loop(on_message, abort_event)
        finally:
            await self._close_sessions()
            self.mark_disconnected()

    async def stop(self) -> None:
        await self._close_sessions()

    async def _close_sessions(self) -> None:
        for attr in ("_poll_session", "_send_session"):
            session = getattr(self, attr, None)
            if session and not session.closed:
                try:
                    await session.close()
                except Exception:
                    pass
            setattr(self, attr, None)

    # ------------------------------------------------------------------
    # Inbound long-poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(
        self,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event],
    ) -> None:
        assert self._poll_session is not None
        sync_buf = load_sync_buf(self._account_id, self._data_dir)
        timeout_ms = LONG_POLL_TIMEOUT_MS
        consecutive_failures = 0

        while abort_event is None or not abort_event.is_set():
            try:
                response = await ilink.get_updates(
                    self._poll_session,
                    base_url=self._base_url,
                    token=self._token,
                    sync_buf=sync_buf,
                    timeout_ms=timeout_ms,
                )

                suggested = response.get("longpolling_timeout_ms")
                if isinstance(suggested, int) and suggested > 0:
                    timeout_ms = suggested

                ret = response.get("ret", 0)
                errcode = response.get("errcode", 0)

                if ret not in (0, None) or errcode not in (0, None):
                    if (
                        ret == SESSION_EXPIRED_ERRCODE
                        or errcode == SESSION_EXPIRED_ERRCODE
                        or ilink.is_stale_session(ret, errcode, response.get("errmsg"))
                    ):
                        log.error("weixin.session_expired", {
                            "account_id": safe_id(self._account_id),
                        })
                        await asyncio.sleep(600)
                        consecutive_failures = 0
                        continue

                    consecutive_failures += 1
                    log.warning("weixin.getupdates_error", {
                        "ret": ret, "errcode": errcode,
                        "errmsg": response.get("errmsg", ""),
                        "attempt": consecutive_failures,
                    })
                    await asyncio.sleep(
                        BACKOFF_DELAY_SECONDS
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                        else RETRY_DELAY_SECONDS
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                    continue

                consecutive_failures = 0
                new_sync_buf = str(response.get("get_updates_buf") or "")
                if new_sync_buf:
                    sync_buf = new_sync_buf
                    save_sync_buf(self._account_id, sync_buf, self._data_dir)

                for message in response.get("msgs") or []:
                    asyncio.create_task(self._process_message_safe(message, on_message))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                log.error("weixin.poll_error", {
                    "error": str(exc), "attempt": consecutive_failures,
                })
                await asyncio.sleep(
                    BACKOFF_DELAY_SECONDS
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                    else RETRY_DELAY_SECONDS
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0

    async def _process_message_safe(
        self,
        message: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        try:
            await self._process_message(message, on_message)
        except Exception as exc:
            log.error("weixin.process_error", {
                "from": safe_id(message.get("from_user_id")),
                "error": str(exc),
            })

    async def _process_message(
        self,
        message: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id or sender_id == self._account_id:
            return

        message_id = str(message.get("message_id") or "").strip()
        if message_id and self._dedup.is_duplicate(message_id):
            return

        item_list = message.get("item_list") or []
        text = extract_text(item_list)

        if text:
            content_key = f"content:{sender_id}:{hashlib.md5(text.encode()).hexdigest()}"
            if self._dedup.is_duplicate(content_key):
                log.debug("weixin.dedup_content", {"sender": safe_id(sender_id)})
                return

        chat_type_str, effective_chat_id = guess_chat_type(message, self._account_id)

        if chat_type_str != "group" and not self._is_dm_allowed(sender_id):
            return

        # Download the first inbound media item (image / video / voice / file).
        # InboundMessage.media_url is single-valued, so any extras are dropped.
        media_url, media_mime = await self._collect_inbound_media(item_list, sender_id)

        if not text and not media_url:
            return

        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._token_store.set(self._account_id, sender_id, context_token)

        chat_type = ChatType.GROUP if chat_type_str == "group" else ChatType.DIRECT
        inbound = InboundMessage(
            channel_id="weixin",
            account_id=self._account_id,
            message_id=message_id or str(uuid.uuid4()),
            sender_id=sender_id,
            sender_name=sender_id,
            chat_id=effective_chat_id,
            chat_type=chat_type,
            text=text,
            media_url=media_url,
            media_mime=media_mime,
            mentioned=chat_type == ChatType.GROUP,
            raw=message,
        )
        log.info("weixin.inbound", {
            "from": safe_id(sender_id),
            "chat_type": chat_type_str,
            "text_preview": text[:50],
            "media_mime": media_mime,
        })
        await on_message(inbound)

    async def _collect_inbound_media(
        self, item_list: list, sender_id: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Download the first downloadable media item and return ``(uri, mime)``.

        ``InboundMessage.media_url`` is single-valued, so we deliberately do NOT
        download items beyond the first — only count them so a warning is logged.
        """
        if not self._poll_session or not self._media_cache:
            return None, None
        media_items = [item for item in item_list if is_downloadable_media_item(item)]
        if not media_items:
            return None, None

        sender_log = safe_id(sender_id)
        if len(media_items) > 1:
            log.warning("weixin.media.extra_dropped", {
                "from": sender_log,
                "dropped": len(media_items) - 1,
            })

        result = await download_inbound_item(
            self._poll_session,
            item=media_items[0],
            cdn_base_url=self._cdn_base_url,
            cache=self._media_cache,
            sender_log_id=sender_log,
        )
        return (result[0], result[1]) if result else (None, None)

    def _is_dm_allowed(self, sender_id: str) -> bool:
        if self._dm_policy == "disabled":
            return False
        if self._dm_policy == "allowlist":
            return sender_id in self._allow_from
        return True

    # ------------------------------------------------------------------
    # Outbound: text
    # ------------------------------------------------------------------

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        if not self._send_session or not self._token:
            return DeliveryResult(
                channel_id="weixin", message_id="",
                success=False, error="Not connected",
            )

        formatted = format_for_weixin(ctx.text)
        chunks = split_chunks(formatted, MAX_MESSAGE_LENGTH)
        if not chunks:
            return DeliveryResult(channel_id="weixin", message_id="")

        context_token = self._token_store.get(self._account_id, ctx.to)
        last_message_id = ""
        try:
            for idx, chunk in enumerate(chunks):
                client_id = f"flocks-weixin-{uuid.uuid4().hex}"
                await self._send_chunk_with_retry(
                    to=ctx.to, chunk=chunk,
                    context_token=context_token, client_id=client_id,
                )
                last_message_id = client_id
                if idx < len(chunks) - 1 and self._send_chunk_delay > 0:
                    await asyncio.sleep(self._send_chunk_delay)
        except Exception as exc:
            log.error("weixin.send_text.error", {
                "to": safe_id(ctx.to), "error": str(exc),
            })
            return DeliveryResult(
                channel_id="weixin", message_id="",
                success=False, error=str(exc),
            )
        return DeliveryResult(channel_id="weixin", message_id=last_message_id)

    async def _send_chunk_with_retry(
        self,
        *,
        to: str,
        chunk: str,
        context_token: Optional[str],
        client_id: str,
    ) -> None:
        """Send a single text chunk with per-chunk retry and backoff.

        - On session-expired (errcode -14): retry once *without* ``context_token``
          and drop it from the local store.
        - On rate-limit (errcode -2): back off 3× and retry.
        """
        last_error: Optional[Exception] = None
        retried_without_token = False
        retry_delay = 1.0

        for attempt in range(self._send_chunk_retries + 1):
            try:
                resp = await ilink.send_text_message(
                    self._send_session,
                    base_url=self._base_url,
                    token=self._token,
                    to=to, text=chunk,
                    context_token=context_token, client_id=client_id,
                )

                if isinstance(resp, dict):
                    ret = resp.get("ret")
                    errcode = resp.get("errcode")
                    # Always log the iLink response so we can confirm whether
                    # the message was actually accepted (vs silently dropped).
                    log.info("weixin.send.response", {
                        "to": safe_id(to),
                        "client_id": client_id[:24],
                        "ret": ret,
                        "errcode": errcode,
                        "errmsg": resp.get("errmsg"),
                        "msg_id": resp.get("msg_id") or resp.get("message_id"),
                        "has_context_token": bool(context_token),
                        "chunk_len": len(chunk),
                    })
                    if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
                        is_session_expired = (
                            ret == SESSION_EXPIRED_ERRCODE
                            or errcode == SESSION_EXPIRED_ERRCODE
                            or ilink.is_stale_session(ret, errcode, resp.get("errmsg"))
                        )
                        if is_session_expired and not retried_without_token and context_token:
                            retried_without_token = True
                            context_token = None
                            self._token_store.clear(self._account_id, to)
                            log.warning("weixin.send.session_expired_retry", {
                                "to": safe_id(to),
                            })
                            continue

                        is_rate_limited = (
                            ret == RATE_LIMIT_ERRCODE or errcode == RATE_LIMIT_ERRCODE
                        )
                        if is_rate_limited:
                            errmsg = resp.get("errmsg") or resp.get("msg") or "rate limited"
                            last_error = RuntimeError(
                                f"iLink sendmessage rate limited: "
                                f"ret={ret} errcode={errcode} errmsg={errmsg}"
                            )
                            if attempt >= self._send_chunk_retries:
                                break
                            wait = retry_delay * 3
                            log.warning("weixin.send.rate_limited", {
                                "to": safe_id(to), "wait": wait,
                            })
                            await asyncio.sleep(wait)
                            continue

                        errmsg = resp.get("errmsg") or resp.get("msg") or "unknown error"
                        raise RuntimeError(
                            f"iLink sendmessage error: ret={ret} errcode={errcode} errmsg={errmsg}"
                        )
                return

            except Exception as exc:
                last_error = exc
                if attempt >= self._send_chunk_retries:
                    break
                wait = retry_delay * (attempt + 1)
                log.warning("weixin.send.retry", {
                    "to": safe_id(to),
                    "attempt": attempt + 1,
                    "wait": wait,
                    "error": str(exc),
                })
                await asyncio.sleep(wait)

        if last_error is not None:
            raise last_error

    # ------------------------------------------------------------------
    # Outbound: media
    # ------------------------------------------------------------------

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        """Send a media file (image / video / voice / document).

        ``ctx.media_url`` may be:
        - a local path (``/abs/path/to/file.png``)
        - a ``file://`` URI
        - a remote ``http(s)://`` URL on the WeChat CDN allowlist
        """
        if not self._send_session or not self._token:
            return DeliveryResult(
                channel_id="weixin", message_id="",
                success=False, error="Not connected",
            )
        if not ctx.media_url:
            # No media to send — fall back to plain text via send_text.
            return await self.send_text(ctx)

        local_path, cleanup = await self._resolve_media_to_path(ctx.media_url)
        if not local_path:
            return DeliveryResult(
                channel_id="weixin", message_id="",
                success=False, error=f"Could not resolve media URL: {ctx.media_url}",
            )

        context_token = self._token_store.get(self._account_id, ctx.to)
        try:
            # Caption first (if any) so the file appears under it in chat order.
            if ctx.text and ctx.text.strip():
                caption_result = await self.send_text(ctx)
                if not caption_result.success:
                    return caption_result

            client_id = await send_outbound_file(
                self._send_session,
                base_url=self._base_url,
                cdn_base_url=self._cdn_base_url,
                token=self._token,
                chat_id=ctx.to,
                path=local_path,
                context_token=context_token,
            )
            return DeliveryResult(channel_id="weixin", message_id=client_id)

        except Exception as exc:
            log.error("weixin.send_media.error", {
                "to": safe_id(ctx.to), "error": str(exc),
            })
            return DeliveryResult(
                channel_id="weixin", message_id="",
                success=False, error=str(exc),
            )
        finally:
            if cleanup and local_path:
                try:
                    os.unlink(local_path)
                except OSError:
                    pass

    async def _resolve_media_to_path(self, media_url: str) -> tuple[Optional[str], bool]:
        """Resolve *media_url* to an on-disk path. Returns ``(path, should_cleanup)``."""
        parsed = urlparse(media_url)
        scheme = parsed.scheme.lower()

        if scheme in ("", "file"):
            path = parsed.path if scheme == "file" else media_url
            if not os.path.isabs(path):
                path = os.path.abspath(path)
            return (path, False) if os.path.exists(path) else (None, False)

        if scheme in ("http", "https"):
            try:
                # Validate host before downloading to prevent SSRF.
                from .cdn import assert_weixin_cdn_url
                assert_weixin_cdn_url(media_url)
                path = await fetch_remote_to_temp(self._send_session, url=media_url)
                return path, True
            except Exception as exc:
                log.warning("weixin.media.fetch_failed", {
                    "url": media_url, "error": str(exc),
                })
                return None, False

        log.warning("weixin.media.unsupported_scheme", {"scheme": scheme})
        return None, False
