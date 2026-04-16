"""
Channel Plugin base classes and data models.

Defines the ChannelPlugin abstract base class and common data structures
used across the channel system.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class ChatType(str, Enum):
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"


@dataclass
class ChannelMeta:
    """Channel plugin metadata."""
    id: str
    label: str
    aliases: list[str] = field(default_factory=list)
    order: int = 100


@dataclass
class ChannelCapabilities:
    """Declares what a channel supports."""
    chat_types: list[ChatType] = field(default_factory=lambda: [ChatType.DIRECT])
    media: bool = False
    threads: bool = False
    reactions: bool = False
    edit: bool = False
    rich_text: bool = False


@dataclass
class InboundMessage:
    """Standardised inbound message from a platform."""
    channel_id: str
    account_id: str
    message_id: str
    sender_id: str
    sender_name: Optional[str] = None
    chat_id: str = ""
    chat_type: ChatType = ChatType.DIRECT
    text: str = ""
    media_url: Optional[str] = None
    reply_to_id: Optional[str] = None
    thread_id: Optional[str] = None
    mentioned: bool = False
    mention_text: str = ""
    raw: Any = None


@dataclass
class OutboundContext:
    """Context for sending a message."""
    channel_id: str
    account_id: Optional[str] = None
    to: str = ""
    text: str = ""
    media_url: Optional[str] = None
    reply_to_id: Optional[str] = None
    thread_id: Optional[str] = None
    silent: bool = False
    format_hint: str = "markdown"


@dataclass
class DeliveryResult:
    """Result of a single message delivery attempt."""
    channel_id: str
    message_id: str
    chat_id: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    retryable: bool = False


@dataclass
class ChannelStatus:
    """Runtime health status of a channel connection."""
    channel_id: str
    connected: bool
    last_message_at: Optional[float] = None
    last_error: Optional[str] = None
    error_count: int = 0
    reconnect_count: int = 0
    uptime_seconds: float = 0.0
    started_at: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict for API responses."""
        return {
            "connected": self.connected,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "last_message_at": self.last_message_at,
            "last_error": self.last_error,
            "error_count": self.error_count,
            "reconnect_count": self.reconnect_count,
        }


class ChannelPlugin(ABC):
    """
    Abstract base class for all channel plugins.

    To implement a new channel:
    1. Subclass ChannelPlugin
    2. Implement meta(), capabilities(), send_text()
    3. Implement start() for WebSocket/polling or handle_webhook() for HTTP callbacks
    4. Export CHANNELS = [MyChannel()] in your module
    """

    def __init__(self) -> None:
        self._config: dict = {}
        self._on_message: Optional[Callable[[InboundMessage], Awaitable[None]]] = None
        self._status = ChannelStatus(channel_id="", connected=False)

    # --- metadata ---

    @abstractmethod
    def meta(self) -> ChannelMeta:
        """Return channel metadata (id, label, aliases)."""

    @abstractmethod
    def capabilities(self) -> ChannelCapabilities:
        """Return channel capability declarations."""

    # --- outbound ---

    @abstractmethod
    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        """Send a text message (must be implemented)."""

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        """Send a media message. Falls back to send_text with URL."""
        fallback = f"{ctx.text}\n{ctx.media_url}" if ctx.media_url else ctx.text
        return await self.send_text(
            OutboundContext(**{**vars(ctx), "text": fallback})
        )

    def chunk_text(self, text: str, limit: int) -> list[str]:
        """Split text into chunks respecting the per-message limit."""
        if not text:
            return []
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            if len(paragraph) > limit:
                # Flush accumulator first
                if current:
                    chunks.append(current.strip())
                    current = ""
                # Force-split oversized paragraph by line, then by char
                chunks.extend(self._force_split(paragraph, limit))
                continue
            if len(current) + len(paragraph) + 2 > limit:
                if current:
                    chunks.append(current.strip())
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}" if current else paragraph
        if current:
            chunks.append(current.strip())
        return chunks or [text[:limit]]

    @staticmethod
    def _force_split(text: str, limit: int) -> list[str]:
        """Split oversized text by newlines, then by character position."""
        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            if len(line) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                for i in range(0, len(line), limit):
                    chunks.append(line[i:i + limit])
                continue
            if len(current) + len(line) + 1 > limit:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    def format_message(self, text: str, format_hint: str = "markdown") -> str:
        """Convert generic Markdown to a platform-specific format."""
        return text

    @property
    def text_chunk_limit(self) -> int:
        """Maximum text length for a single message."""
        return 4000

    @property
    def rate_limit(self) -> tuple[float, int]:
        """Outbound rate limit as ``(requests_per_second, burst)``.

        Subclasses should override to match their platform's API quotas.
        """
        return (20.0, 5)

    # --- inbound / gateway ---

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Start message listening (WebSocket / polling)."""
        self._config = config
        self._on_message = on_message

    async def handle_webhook(
        self,
        body: bytes,
        headers: dict,
    ) -> Optional[dict]:
        """Handle an incoming webhook callback."""
        raise NotImplementedError(
            f"Channel '{self.meta().id}' does not support webhook mode"
        )

    async def stop(self) -> None:
        """Stop listening and release resources."""

    # --- config ---

    def config_schema(self) -> Optional[dict]:
        """Return a JSON Schema for config validation / UI generation."""
        return None

    def validate_config(self, config: dict) -> Optional[str]:
        """Validate config dict. Return error string or None."""
        return None

    # --- target resolution ---

    def normalize_target(self, raw: str) -> Optional[str]:
        """Normalise a raw target address."""
        return raw

    def target_hint(self) -> str:
        """Hint string describing the expected target format."""
        return "<id>"

    # --- status ---

    @property
    def status(self) -> ChannelStatus:
        return self._status

    def reset_status(self, channel_id: str, attempt: int = 0) -> None:
        """Prepare a fresh status for a new connection attempt."""
        self._status = ChannelStatus(
            channel_id=channel_id,
            connected=False,
            started_at=time.monotonic(),
            reconnect_count=attempt,
        )

    def mark_connected(self) -> None:
        self._status.connected = True

    def mark_disconnected(self, error: Optional[str] = None) -> None:
        self._status.connected = False
        if error:
            self._status.last_error = error
            self._status.error_count += 1

    def record_message(self) -> None:
        """Update the last-message timestamp (called by inbound dispatcher)."""
        self._status.last_message_at = time.time()
