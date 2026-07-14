"""WhatsApp ChannelPlugin implementation."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote, urlparse

import aiohttp

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
    DEFAULT_BRIDGE_PORT,
    DEFAULT_MESSAGE_LIMIT,
    DEFAULT_SEND_CHUNK_DELAY_MS,
    DEFAULT_SEND_TIMEOUT_MS,
    DEFAULT_TEXT_BATCH_DELAY_SECONDS,
    VALID_DM_POLICIES,
    VALID_GROUP_POLICIES,
    VALID_GROUP_TRIGGERS,
    VALID_MODES,
    coerce_float,
    coerce_int,
    coerce_list,
    coerce_str,
    default_bridge_dir,
    default_media_cache_dir,
    default_session_path,
    find_executable,
    format_env_list,
    matches_identifier,
    parse_target,
)
from .bridge_runtime import NODE_USE_BUNDLED_CA_OPTION, append_node_options, config_hash, ensure_bridge_deps, file_hash
from .format import format_for_whatsapp
from .inbound import build_inbound_message

log = Log.create(service="channel.whatsapp")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _path_from_media_url(media_url: str) -> Optional[Path]:
    parsed = urlparse(media_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme in {"", None}:
        return Path(media_url)
    return None


def _media_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
        return "video"
    if suffix in {".ogg", ".opus", ".mp3", ".wav", ".m4a"}:
        return "audio"
    return "document"


def _raw_reply_id(reply_to_id: Optional[str]) -> Optional[str]:
    if not reply_to_id:
        return None
    value = coerce_str(reply_to_id)
    if "@" in value and ":" in value:
        return value.rsplit(":", 1)[-1] or value
    return value


class WhatsAppChannel(ChannelPlugin):
    """WhatsApp personal-account channel via a local Baileys bridge."""

    def __init__(self) -> None:
        super().__init__()
        self._account_id = "default"
        self._bridge_port = DEFAULT_BRIDGE_PORT
        self._bridge_dir = default_bridge_dir()
        self._session_path = default_session_path()
        self._media_cache_dir = default_media_cache_dir()
        self._mode = "bot"
        self._dm_policy = "allowlist"
        self._group_policy = "disabled"
        self._group_trigger = "mention"
        self._allow_from: list[str] = []
        self._group_allow_from: list[str] = []
        self._text_batch_delay = DEFAULT_TEXT_BATCH_DELAY_SECONDS
        self._send_chunk_delay_ms = DEFAULT_SEND_CHUNK_DELAY_MS
        self._send_timeout_ms = DEFAULT_SEND_TIMEOUT_MS
        self._reply_prefix = ""
        self._http: Optional[aiohttp.ClientSession] = None
        self._bridge_token = secrets.token_urlsafe(32)
        self._bridge_process: Optional[subprocess.Popen] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._bridge_log_fh = None
        self._bridge_log_path: Optional[Path] = None
        self._pending_text: dict[str, InboundMessage] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._shutting_down = False

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="whatsapp",
            label="WhatsApp",
            aliases=["wa"],
            order=45,
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
        mode = coerce_str(config.get("mode") or "bot")
        if mode not in VALID_MODES:
            return "WhatsApp mode must be 'bot' or 'self-chat'"
        dm_policy = coerce_str(config.get("dmPolicy") or "allowlist")
        if dm_policy not in VALID_DM_POLICIES:
            return "WhatsApp dmPolicy must be 'open', 'allowlist', or 'disabled'"
        group_policy = coerce_str(config.get("groupPolicy") or "disabled")
        if group_policy not in VALID_GROUP_POLICIES:
            return "WhatsApp groupPolicy must be 'open', 'allowlist', or 'disabled'"
        group_trigger = coerce_str(config.get("groupTrigger") or "mention")
        if group_trigger not in VALID_GROUP_TRIGGERS:
            return "WhatsApp groupTrigger must be 'mention' or 'all'"
        bridge_port = coerce_int(config.get("bridgePort"), DEFAULT_BRIDGE_PORT)
        if bridge_port <= 0 or bridge_port > 65535:
            return "WhatsApp bridgePort must be between 1 and 65535"
        if not find_executable("node"):
            return "WhatsApp channel requires Node.js"
        session_path = Path(coerce_str(config.get("sessionPath")) or default_session_path()).expanduser()
        if not (session_path / "creds.json").exists():
            return "WhatsApp is not paired yet. Use QR pairing before enabling the channel."
        return None

    @property
    def text_chunk_limit(self) -> int:
        return DEFAULT_MESSAGE_LIMIT

    @property
    def rate_limit(self) -> tuple[float, int]:
        return (3.0, 2)

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        if format_hint == "plain":
            return text
        return format_for_whatsapp(text)

    def normalize_target(self, raw: str) -> Optional[str]:
        target = parse_target(raw)
        return target or None

    def target_hint(self) -> str:
        return "<phone> / <phone>@s.whatsapp.net / <group>@g.us"

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._load_config(config)
        self._shutting_down = False

        await self._ensure_bridge_deps()
        await self._start_bridge()
        self._http = aiohttp.ClientSession()
        self.mark_connected()
        log.info("whatsapp.connected", {
            "bridge_port": self._bridge_port,
            "session_path": str(self._session_path),
        })

        _abort = abort_event if abort_event is not None else asyncio.Event()
        self._poll_task = asyncio.create_task(self._poll_messages(_abort))
        abort_task = asyncio.create_task(_abort.wait())
        try:
            done, _pending = await asyncio.wait(
                {abort_task, self._poll_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self._poll_task in done:
                exc = self._poll_task.exception()
                if exc:
                    raise exc
        finally:
            abort_task.cancel()
            await asyncio.gather(abort_task, return_exceptions=True)
            await self.stop()

    async def stop(self) -> None:
        self._shutting_down = True
        for task in list(self._pending_tasks.values()):
            task.cancel()
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks.values(), return_exceptions=True)
        self._pending_tasks.clear()
        self._pending_text.clear()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        self._poll_task = None

        if self._http is not None:
            await self._http.close()
            self._http = None

        if self._bridge_process is not None:
            proc = self._bridge_process
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        proc.terminate()
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                    await asyncio.to_thread(proc.wait, 5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._bridge_process = None

        if self._bridge_log_fh:
            try:
                self._bridge_log_fh.close()
            except Exception:
                pass
            self._bridge_log_fh = None
        self.mark_disconnected()

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        if self._http is None:
            return DeliveryResult(
                channel_id="whatsapp",
                message_id="",
                success=False,
                error="WhatsApp bridge is not connected",
                retryable=True,
            )
        chat_id = parse_target(ctx.to)
        if not chat_id:
            return DeliveryResult(
                channel_id="whatsapp",
                message_id="",
                success=False,
                error="Invalid WhatsApp target",
            )

        try:
            async with self._http.post(
                self._url("/send"),
                json={
                    "chatId": chat_id,
                    "message": ctx.text,
                    "replyTo": _raw_reply_id(ctx.reply_to_id),
                },
                headers=self._bridge_headers(),
                timeout=aiohttp.ClientTimeout(total=max(self._send_timeout_ms / 1000, 1)),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 or data.get("success") is False:
                    return DeliveryResult(
                        channel_id="whatsapp",
                        message_id="",
                        success=False,
                        error=str(data.get("error") or f"HTTP {resp.status}"),
                        retryable=resp.status >= 500,
                    )
        except Exception as exc:
            return DeliveryResult(
                channel_id="whatsapp",
                message_id="",
                success=False,
                error=f"WhatsApp send failed: {exc}",
                retryable=True,
            )

        self.record_message()
        return DeliveryResult(
            channel_id="whatsapp",
            message_id=coerce_str(data.get("messageId")),
            chat_id=chat_id,
            success=True,
        )

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        if not ctx.media_url:
            return await self.send_text(ctx)
        if self._http is None:
            return DeliveryResult(
                channel_id="whatsapp",
                message_id="",
                success=False,
                error="WhatsApp bridge is not connected",
                retryable=True,
            )
        chat_id = parse_target(ctx.to)
        path = _path_from_media_url(ctx.media_url)
        if not chat_id:
            return DeliveryResult(channel_id="whatsapp", message_id="", success=False, error="Invalid WhatsApp target")
        if path is None or not path.is_file():
            return DeliveryResult(channel_id="whatsapp", message_id="", success=False, error="WhatsApp media must be a local file")

        try:
            async with self._http.post(
                self._url("/send-media"),
                json={
                    "chatId": chat_id,
                    "filePath": str(path),
                    "mediaType": _media_type_for_path(path),
                    "caption": ctx.text or "",
                    "replyTo": _raw_reply_id(ctx.reply_to_id),
                },
                headers=self._bridge_headers(),
                timeout=aiohttp.ClientTimeout(total=max(self._send_timeout_ms / 1000, 1)),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 or data.get("success") is False:
                    return DeliveryResult(
                        channel_id="whatsapp",
                        message_id="",
                        success=False,
                        error=str(data.get("error") or f"HTTP {resp.status}"),
                        retryable=resp.status >= 500,
                    )
        except Exception as exc:
            return DeliveryResult(
                channel_id="whatsapp",
                message_id="",
                success=False,
                error=f"WhatsApp send_media failed: {exc}",
                retryable=True,
            )

        self.record_message()
        return DeliveryResult(
            channel_id="whatsapp",
            message_id=coerce_str(data.get("messageId")),
            chat_id=chat_id,
            success=True,
        )

    def _load_config(self, config: dict) -> None:
        self._account_id = coerce_str(config.get("accountId")) or "default"
        self._bridge_port = coerce_int(config.get("bridgePort"), DEFAULT_BRIDGE_PORT)
        self._bridge_dir = Path(coerce_str(config.get("bridgeDir")) or default_bridge_dir()).expanduser()
        self._session_path = Path(coerce_str(config.get("sessionPath")) or default_session_path()).expanduser()
        self._media_cache_dir = Path(coerce_str(config.get("mediaCacheDir")) or default_media_cache_dir()).expanduser()
        self._mode = coerce_str(config.get("mode") or "bot")
        self._dm_policy = coerce_str(config.get("dmPolicy") or "allowlist")
        self._group_policy = coerce_str(config.get("groupPolicy") or "disabled")
        self._group_trigger = coerce_str(config.get("groupTrigger") or "mention")
        self._allow_from = coerce_list(config.get("allowFrom"))
        self._group_allow_from = coerce_list(config.get("groupAllowFrom"))
        self._text_batch_delay = coerce_float(config.get("textBatchDelaySeconds"), DEFAULT_TEXT_BATCH_DELAY_SECONDS)
        self._send_chunk_delay_ms = coerce_int(config.get("sendChunkDelayMs"), DEFAULT_SEND_CHUNK_DELAY_MS)
        self._send_timeout_ms = coerce_int(config.get("sendTimeoutMs"), DEFAULT_SEND_TIMEOUT_MS)
        self._reply_prefix = coerce_str(config.get("replyPrefix"))

    async def _ensure_bridge_deps(self) -> None:
        await ensure_bridge_deps(self._bridge_dir)

    async def _start_bridge(self) -> None:
        node = find_executable("node")
        if not node:
            raise RuntimeError("Node.js is required for WhatsApp channel")
        script = self._bridge_dir / "bridge.js"
        if not script.exists():
            raise RuntimeError(f"WhatsApp bridge script not found: {script}")
        self._session_path.mkdir(parents=True, exist_ok=True)
        self._media_cache_dir.mkdir(parents=True, exist_ok=True)

        if await self._reuse_or_stop_existing_bridge(script):
            return

        self._bridge_log_path = self._session_path.parent / "bridge.log"
        self._bridge_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._bridge_log_fh = open(self._bridge_log_path, "a", encoding="utf-8")
        env = os.environ.copy()
        append_node_options(env, NODE_USE_BUNDLED_CA_OPTION)
        env["FLOCKS_WHATSAPP_MEDIA_DIR"] = str(self._media_cache_dir)
        env["FLOCKS_WHATSAPP_BRIDGE_TOKEN"] = self._bridge_token
        env["FLOCKS_WHATSAPP_CONFIG_HASH"] = self._bridge_config_hash()
        env["FLOCKS_WHATSAPP_ALLOWED_MEDIA_ROOTS"] = os.pathsep.join(self._allowed_media_roots())
        env["FLOCKS_WHATSAPP_MODE"] = self._mode
        env["FLOCKS_WHATSAPP_DM_POLICY"] = self._dm_policy
        env["FLOCKS_WHATSAPP_ALLOWED_USERS"] = format_env_list(self._allow_from)
        env["FLOCKS_WHATSAPP_REPLY_PREFIX"] = self._reply_prefix
        env["FLOCKS_WHATSAPP_CHUNK_DELAY_MS"] = str(self._send_chunk_delay_ms)
        env["FLOCKS_WHATSAPP_SEND_TIMEOUT_MS"] = str(self._send_timeout_ms)

        self._bridge_process = subprocess.Popen(
            [
                node,
                str(script),
                "--port",
                str(self._bridge_port),
                "--session",
                str(self._session_path),
            ],
            cwd=str(self._bridge_dir),
            stdout=self._bridge_log_fh,
            stderr=self._bridge_log_fh,
            start_new_session=(os.name != "nt"),
            env=env,
        )
        await self._wait_for_bridge(script)

    async def _reuse_or_stop_existing_bridge(self, script: Path) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self._url("/health"),
                    headers=self._bridge_headers(),
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status == 200 and data.get("status") == "connected" and self._bridge_identity_matches(data, script):
                        self._bridge_process = None
                        return True
                    if resp.status == 200 and data.get("scriptHash") == file_hash(script):
                        if coerce_str(data.get("sessionPath")) != str(self._session_path):
                            raise RuntimeError(
                                f"WhatsApp bridge port {self._bridge_port} is already used by another session"
                            )
        except RuntimeError:
            raise
        except Exception:
            pass
        pid_file = self._session_path / "bridge.pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip().splitlines()[0])
            if _pid_exists(pid):
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(1)
        except Exception:
            pass
        return False

    async def _wait_for_bridge(self, script: Path) -> None:
        deadline = time.monotonic() + 30
        last_status = "unknown"
        while time.monotonic() < deadline:
            if self._bridge_process is not None and self._bridge_process.poll() is not None:
                raise RuntimeError(
                    f"WhatsApp bridge exited early with code {self._bridge_process.returncode}; log: {self._bridge_log_path}"
                )
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self._url("/health"),
                        headers=self._bridge_headers(),
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        data = await resp.json(content_type=None)
                        last_status = coerce_str(data.get("status")) or last_status
                        if resp.status == 200 and self._bridge_identity_matches(data, script):
                            if data.get("status") == "connected":
                                if self._bridge_process is not None:
                                    try:
                                        (self._session_path / "bridge.pid").write_text(
                                            str(self._bridge_process.pid),
                                            encoding="utf-8",
                                        )
                                    except OSError:
                                        pass
                                return
            except Exception:
                pass
            await asyncio.sleep(1)
        raise RuntimeError(f"WhatsApp bridge did not connect in time (last status: {last_status}); log: {self._bridge_log_path}")

    async def _poll_messages(self, abort_event: asyncio.Event) -> None:
        consecutive_failures = 0
        while not abort_event.is_set() and not self._shutting_down:
            if self._http is None:
                return
            if self._bridge_process is not None and self._bridge_process.poll() is not None:
                raise RuntimeError(f"WhatsApp bridge exited with code {self._bridge_process.returncode}")
            try:
                async with self._http.get(
                    self._url("/messages"),
                    headers=self._bridge_headers(),
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"WhatsApp bridge /messages returned HTTP {resp.status}")
                    events = await resp.json(content_type=None)
                    consecutive_failures = 0
                    if isinstance(events, list):
                        for event in events:
                            await self._handle_bridge_event(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                log.warning("whatsapp.poll.error", {"error": str(exc)})
                if consecutive_failures >= 3:
                    self.mark_disconnected(str(exc))
                    raise RuntimeError(f"WhatsApp bridge polling failed {consecutive_failures} times: {exc}") from exc
                try:
                    await asyncio.wait_for(abort_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def _handle_bridge_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        inbound = build_inbound_message(event, self._account_id)
        if inbound is None or not self._is_allowed(inbound):
            return
        if inbound.chat_type == ChatType.GROUP and not self._passes_group_trigger(inbound):
            return
        await self._enqueue_or_dispatch(inbound)

    def _is_allowed(self, msg: InboundMessage) -> bool:
        if msg.chat_type == ChatType.DIRECT:
            if self._dm_policy == "disabled":
                return False
            if self._dm_policy == "open":
                return True
            sender_aliases = []
            if isinstance(msg.raw, dict):
                raw_aliases = msg.raw.get("senderAliases")
                if isinstance(raw_aliases, list):
                    sender_aliases = raw_aliases
            return matches_identifier([msg.sender_id, *sender_aliases], self._allow_from)
        if self._group_policy == "disabled":
            return False
        if self._group_policy == "open":
            return True
        chat_aliases = []
        if isinstance(msg.raw, dict):
            raw_aliases = msg.raw.get("chatAliases")
            if isinstance(raw_aliases, list):
                chat_aliases = raw_aliases
        return matches_identifier([msg.chat_id, *chat_aliases], self._group_allow_from)

    def _passes_group_trigger(self, msg: InboundMessage) -> bool:
        if self._group_trigger == "all":
            return True
        return msg.mentioned

    async def _enqueue_or_dispatch(self, msg: InboundMessage) -> None:
        if msg.media_url or self._text_batch_delay <= 0:
            if self._on_message:
                await self._on_message(msg)
            return
        key = f"{msg.account_id}:{msg.chat_id}"
        existing = self._pending_text.get(key)
        if existing is None:
            self._pending_text[key] = msg
        else:
            existing.text = f"{existing.text}\n{msg.text}".strip()
            existing.message_id = msg.message_id
            existing.raw = msg.raw
        prior = self._pending_tasks.get(key)
        if prior and not prior.done():
            prior.cancel()
        self._pending_tasks[key] = asyncio.create_task(self._flush_text_batch(key))

    async def _flush_text_batch(self, key: str) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.sleep(self._text_batch_delay)
            msg = self._pending_text.pop(key, None)
            if msg and self._on_message:
                await self._on_message(msg)
        except asyncio.CancelledError:
            pass
        finally:
            if self._pending_tasks.get(key) is current:
                self._pending_tasks.pop(key, None)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._bridge_port}{path}"

    def _bridge_headers(self) -> dict[str, str]:
        return {"X-Flocks-Bridge-Token": self._bridge_token}

    def _bridge_config_hash(self) -> str:
        return config_hash({
            "sessionPath": str(self._session_path),
            "mediaDir": str(self._media_cache_dir),
            "mode": self._mode,
            "replyPrefix": self._reply_prefix,
            "sendChunkDelayMs": self._send_chunk_delay_ms,
            "sendTimeoutMs": self._send_timeout_ms,
        })

    def _bridge_identity_matches(self, data: dict[str, Any], script: Path) -> bool:
        return (
            data.get("scriptHash") == file_hash(script)
            and coerce_str(data.get("sessionPath")) == str(self._session_path)
            and coerce_str(data.get("mediaDir")) == str(self._media_cache_dir)
            and coerce_str(data.get("mode")) == self._mode
            and coerce_str(data.get("configHash")) == self._bridge_config_hash()
        )

    def _allowed_media_roots(self) -> list[str]:
        roots = [
            self._media_cache_dir,
            Path.home() / ".flocks" / "workspace",
            Path(os.getenv("TMPDIR") or "/tmp"),
        ]
        extra = coerce_list(self._config.get("mediaAllowedRoots") if hasattr(self, "_config") else None)
        roots.extend(Path(item).expanduser() for item in extra)
        resolved: list[str] = []
        for root in roots:
            try:
                resolved.append(str(root.resolve()))
            except OSError:
                resolved.append(str(root.expanduser()))
        return list(dict.fromkeys(resolved))
