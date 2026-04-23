"""
Tests for the channel system refactoring.

Covers:
  - AsyncTokenBucket (rate limiter extracted to utils)
  - ChannelStatus.to_dict()
  - ChannelPlugin status management methods
  - ChannelPlugin.chunk_text()
  - ChannelConfig merged into config.config
  - MessageDedup (merged into dispatcher)
  - _check_allowlist (merged into dispatcher)
  - ChannelDeliveryCallbacks (merged into dispatcher)
  - OutboundDelivery.deliver() with OutboundContext API
  - ChannelRegistry basic operations
  - GatewayManager helpers
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChannelStatus,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.config.config import ChannelAccountConfig, ChannelConfig, ConfigInfo
from flocks.utils.rate_limiter import AsyncTokenBucket


# =====================================================================
# Helpers — minimal concrete ChannelPlugin for testing
# =====================================================================

class _StubChannel(ChannelPlugin):
    """Minimal concrete implementation used throughout this test module."""

    def __init__(self, channel_id: str = "stub", label: str = "Stub") -> None:
        super().__init__()
        self._id = channel_id
        self._label = label
        self._sent: list[OutboundContext] = []

    def meta(self) -> ChannelMeta:
        return ChannelMeta(id=self._id, label=self._label, aliases=["s"])

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(chat_types=[ChatType.DIRECT, ChatType.GROUP])

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        self._sent.append(ctx)
        return DeliveryResult(channel_id=self._id, message_id="msg_001", success=True)


# =====================================================================
# 1. AsyncTokenBucket
# =====================================================================

class TestAsyncTokenBucket:
    async def test_acquire_within_burst(self):
        limiter = AsyncTokenBucket(rate=100.0, burst=3)
        for _ in range(3):
            await limiter.acquire()

    async def test_acquire_blocks_when_exhausted(self):
        limiter = AsyncTokenBucket(rate=1000.0, burst=1)
        await limiter.acquire()
        t0 = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # high rate → very short wait

    async def test_tokens_refill_over_time(self):
        limiter = AsyncTokenBucket(rate=1000.0, burst=2)
        await limiter.acquire()
        await limiter.acquire()
        await asyncio.sleep(0.01)
        await limiter.acquire()


# =====================================================================
# 2. ChannelStatus.to_dict
# =====================================================================

class TestChannelStatusToDict:
    def test_serialises_all_fields(self):
        status = ChannelStatus(
            channel_id="feishu",
            connected=True,
            last_message_at=1234.5,
            last_error="oops",
            error_count=2,
            reconnect_count=1,
            uptime_seconds=99.999,
        )
        d = status.to_dict()
        assert d["connected"] is True
        assert d["uptime_seconds"] == 100.0
        assert d["last_message_at"] == 1234.5
        assert d["last_error"] == "oops"
        assert d["error_count"] == 2
        assert d["reconnect_count"] == 1
        assert "channel_id" not in d
        assert "started_at" not in d


# =====================================================================
# 3. ChannelPlugin status management
# =====================================================================

class TestChannelPluginStatus:
    def test_reset_status(self):
        ch = _StubChannel()
        ch.reset_status("feishu", attempt=3)
        assert ch.status.channel_id == "feishu"
        assert ch.status.connected is False
        assert ch.status.reconnect_count == 3
        assert ch.status.started_at is not None

    def test_mark_connected(self):
        ch = _StubChannel()
        ch.reset_status("feishu")
        ch.mark_connected()
        assert ch.status.connected is True

    def test_mark_disconnected_without_error(self):
        ch = _StubChannel()
        ch.reset_status("feishu")
        ch.mark_connected()
        ch.mark_disconnected()
        assert ch.status.connected is False
        assert ch.status.error_count == 0

    def test_mark_disconnected_with_error(self):
        ch = _StubChannel()
        ch.reset_status("feishu")
        ch.mark_disconnected(error="timeout")
        assert ch.status.connected is False
        assert ch.status.last_error == "timeout"
        assert ch.status.error_count == 1
        ch.mark_disconnected(error="refused")
        assert ch.status.error_count == 2

    def test_record_message(self):
        ch = _StubChannel()
        ch.reset_status("feishu")
        assert ch.status.last_message_at is None
        ch.record_message()
        assert ch.status.last_message_at is not None


# =====================================================================
# 4. chunk_text
# =====================================================================

class TestChunkText:
    def setup_method(self):
        self.ch = _StubChannel()

    def test_empty_text(self):
        assert self.ch.chunk_text("", 100) == []

    def test_within_limit(self):
        assert self.ch.chunk_text("hello", 100) == ["hello"]

    def test_split_by_paragraphs(self):
        text = "aaa\n\nbbb\n\nccc"
        chunks = self.ch.chunk_text(text, 8)
        assert all(len(c) <= 8 for c in chunks)
        assert "aaa" in chunks[0]

    def test_force_split_long_line(self):
        text = "x" * 20
        chunks = self.ch.chunk_text(text, 8)
        assert all(len(c) <= 8 for c in chunks)
        assert "".join(chunks) == text


# =====================================================================
# 5. ChannelConfig in config.config
# =====================================================================

class TestChannelConfigMerge:
    def test_channel_config_defaults(self):
        cfg = ChannelConfig()
        assert cfg.enabled is False
        assert cfg.group_trigger == "mention"
        assert cfg.default_agent is None

    def test_channel_config_extra_fields(self):
        cfg = ChannelConfig(enabled=True, appId="abc", appSecret="xyz")
        assert cfg.get_extra("appId") == "abc"
        assert cfg.get_extra("missing", "fallback") == "fallback"

    def test_channel_account_config(self):
        acc = ChannelAccountConfig(enabled=False, name="bot1")
        assert acc.enabled is False
        assert acc.name == "bot1"

    def test_config_info_get_channel_config_default(self):
        info = ConfigInfo()
        cfg = info.get_channel_config("nonexistent")
        assert isinstance(cfg, ChannelConfig)
        assert cfg.enabled is False

    def test_config_info_get_channel_configs_empty(self):
        info = ConfigInfo()
        assert info.get_channel_configs() == {}

    def test_config_info_parses_channels_dict(self):
        info = ConfigInfo(channels={
            "feishu": {"enabled": True, "defaultAgent": "helper"},
        })
        assert isinstance(info.channels["feishu"], ChannelConfig)
        assert info.channels["feishu"].enabled is True
        assert info.channels["feishu"].default_agent == "helper"

    def test_get_channel_config_accessor(self):
        info = ConfigInfo(channels={
            "feishu": {"enabled": True, "groupTrigger": "all"},
        })
        cfg = info.get_channel_config("feishu")
        assert cfg.enabled is True
        assert cfg.group_trigger == "all"

        missing = info.get_channel_config("discord")
        assert missing.enabled is False


# =====================================================================
# 6. MessageDedup (now in dispatcher module)
# =====================================================================

class TestMessageDedup:
    def test_first_message_not_duplicate(self):
        from flocks.channel.inbound.dispatcher import MessageDedup
        dedup = MessageDedup(ttl_seconds=10, max_size=100)
        assert dedup.is_duplicate("msg_1") is False

    def test_second_same_message_is_duplicate(self):
        from flocks.channel.inbound.dispatcher import MessageDedup
        dedup = MessageDedup(ttl_seconds=10, max_size=100)
        dedup.is_duplicate("msg_1")
        assert dedup.is_duplicate("msg_1") is True

    def test_different_messages_not_duplicate(self):
        from flocks.channel.inbound.dispatcher import MessageDedup
        dedup = MessageDedup(ttl_seconds=10, max_size=100)
        dedup.is_duplicate("msg_1")
        assert dedup.is_duplicate("msg_2") is False


class TestFeishuNativeCommands:
    @pytest.mark.asyncio
    async def test_status_command_reports_session_state(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.channel.inbound.session_binding import SessionBinding

        dispatcher = InboundDispatcher()
        binding = SessionBinding(
            channel_id="feishu",
            account_id="default",
            chat_id="ou_user",
            chat_type=ChatType.DIRECT,
            thread_id=None,
            session_id="session_1",
            agent_id="rex",
            created_at=0,
            last_message_at=0,
        )
        msg = InboundMessage(
            channel_id="feishu",
            account_id="default",
            message_id="msg_1",
            sender_id="ou_user",
            chat_id="ou_user",
            chat_type=ChatType.DIRECT,
            text="/status",
            mention_text="/status",
        )

        delivered: list[str] = []

        async def fake_deliver(ctx, session_id=None):
            delivered.append(ctx.text)

        monkeypatch.setattr(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            fake_deliver,
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id="session_1",
                    project_id="channel",
                    agent="rex",
                )
            ),
        )
        monkeypatch.setattr(
            "flocks.session.session_loop.SessionLoop._resolve_model",
            AsyncMock(return_value=("anthropic", "claude-sonnet-4-20250514")),
        )
        monkeypatch.setattr(
            "flocks.session.core.status.SessionStatus.get",
            lambda _session_id: SimpleNamespace(type="idle"),
        )

        handled = await dispatcher._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=ChannelConfig(enabled=True),
            user_text="/status",
            scope_override=None,
        )

        assert handled is True
        assert delivered
        assert "session_1" in delivered[0]
        assert "anthropic/claude-sonnet-4-20250514" in delivered[0]

    @pytest.mark.asyncio
    async def test_model_command_updates_session_model(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.channel.inbound.session_binding import SessionBinding

        dispatcher = InboundDispatcher()
        dispatcher._trigger_command_hook = AsyncMock()
        binding = SessionBinding(
            channel_id="feishu",
            account_id="default",
            chat_id="ou_user",
            chat_type=ChatType.DIRECT,
            thread_id=None,
            session_id="session_1",
            agent_id="rex",
            created_at=0,
            last_message_at=0,
        )
        msg = InboundMessage(
            channel_id="feishu",
            account_id="default",
            message_id="msg_1",
            sender_id="ou_user",
            chat_id="ou_user",
            chat_type=ChatType.DIRECT,
            text="/model anthropic/claude-sonnet-4-20250514",
            mention_text="/model anthropic/claude-sonnet-4-20250514",
        )

        delivered: list[str] = []
        updated: list[tuple[str, str, dict]] = []

        async def fake_deliver(ctx, session_id=None):
            delivered.append(ctx.text)

        async def fake_update(project_id, session_id, **updates):
            updated.append((project_id, session_id, updates))
            return SimpleNamespace(id=session_id, project_id=project_id, **updates)

        monkeypatch.setattr(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            fake_deliver,
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id="session_1",
                    project_id="channel",
                    agent="rex",
                    provider=None,
                    model=None,
                )
            ),
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.get",
            lambda provider_id: object() if provider_id == "anthropic" else None,
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.list_models",
            lambda provider_id=None: [SimpleNamespace(id="claude-sonnet-4-20250514")],
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.update",
            fake_update,
        )

        handled = await dispatcher._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=ChannelConfig(enabled=True),
            user_text=msg.text,
            scope_override=None,
        )

        assert handled is True
        assert updated == [
            (
                "channel",
                "session_1",
                {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-20250514",
                },
            )
        ]
        assert "已切换到模型" in delivered[0]

    @pytest.mark.asyncio
    async def test_new_command_rebinds_conversation(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.channel.inbound.session_binding import SessionBinding

        dispatcher = InboundDispatcher()
        dispatcher._trigger_command_hook = AsyncMock()
        dispatcher.binding_service.rebind = AsyncMock(
            return_value=SessionBinding(
                channel_id="feishu",
                account_id="default",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                thread_id="root_1",
                session_id="session_new",
                agent_id="rex",
                created_at=0,
                last_message_at=0,
            )
        )
        binding = SessionBinding(
            channel_id="feishu",
            account_id="default",
            chat_id="oc_group",
            chat_type=ChatType.GROUP,
            thread_id="root_1",
            session_id="session_old",
            agent_id="rex",
            created_at=0,
            last_message_at=0,
        )
        msg = InboundMessage(
            channel_id="feishu",
            account_id="default",
            message_id="msg_1",
            sender_id="ou_user",
            chat_id="oc_group",
            chat_type=ChatType.GROUP,
            text="/new",
            mention_text="/new",
            thread_id="root_1",
        )

        delivered: list[str] = []

        async def fake_deliver(ctx, session_id=None):
            delivered.append(ctx.text)

        monkeypatch.setattr(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            fake_deliver,
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id="session_old",
                    project_id="channel",
                    directory="/tmp/project",
                    agent="rex",
                    provider="anthropic",
                    model="claude-sonnet-4-20250514",
                )
            ),
        )
        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                id="session_new",
                agent="rex",
            )
        )
        monkeypatch.setattr("flocks.session.session.Session.create", create_mock)

        handled = await dispatcher._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=ChannelConfig(enabled=True),
            user_text="/new",
            scope_override="group_topic",
        )

        assert handled is True
        dispatcher.binding_service.rebind.assert_awaited_once()
        assert dispatcher.binding_service.rebind.await_args.kwargs["scope_override"] == "group_topic"
        create_kwargs = create_mock.await_args.kwargs
        assert "parent_id" not in create_kwargs or create_kwargs["parent_id"] is None
        assert create_kwargs["title"] == "[Feishu] oc_group"
        assert "session_new" in delivered[0]
        assert "已开始全新对话。" in delivered[0]

    @pytest.mark.asyncio
    async def test_reset_alias_matches_new_semantics(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.channel.inbound.session_binding import SessionBinding

        dispatcher = InboundDispatcher()
        dispatcher._trigger_command_hook = AsyncMock()
        dispatcher.binding_service.rebind = AsyncMock(
            return_value=SessionBinding(
                channel_id="wecom",
                account_id="default",
                chat_id="room_1",
                chat_type=ChatType.DIRECT,
                thread_id=None,
                session_id="session_new",
                agent_id="rex",
                created_at=0,
                last_message_at=0,
            )
        )
        binding = SessionBinding(
            channel_id="wecom",
            account_id="default",
            chat_id="room_1",
            chat_type=ChatType.DIRECT,
            thread_id=None,
            session_id="session_old",
            agent_id="rex",
            created_at=0,
            last_message_at=0,
        )
        msg = InboundMessage(
            channel_id="wecom",
            account_id="default",
            message_id="msg_1",
            sender_id="user_1",
            chat_id="room_1",
            chat_type=ChatType.DIRECT,
            text="/reset",
            mention_text="/reset",
        )

        delivered: list[str] = []

        async def fake_deliver(ctx, session_id=None):
            delivered.append(ctx.text)

        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                id="session_new",
                agent="rex",
            )
        )

        monkeypatch.setattr(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            fake_deliver,
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id="session_old",
                    project_id="channel",
                    directory="/tmp/project",
                    agent="rex",
                    provider="anthropic",
                    model="claude-sonnet-4-20250514",
                )
            ),
        )
        monkeypatch.setattr("flocks.session.session.Session.create", create_mock)

        handled = await dispatcher._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=ChannelConfig(enabled=True),
            user_text="/reset",
            scope_override=None,
        )

        assert handled is True
        create_kwargs = create_mock.await_args.kwargs
        assert "parent_id" not in create_kwargs or create_kwargs["parent_id"] is None
        assert create_kwargs["title"] == "[Wecom] DM — user_1"
        dispatcher._trigger_command_hook.assert_awaited_once()
        assert dispatcher._trigger_command_hook.await_args.args[0] == "new"
        assert "已开始全新对话。" in delivered[0]

    @pytest.mark.asyncio
    async def test_help_command_works_for_non_feishu_channel(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.channel.inbound.session_binding import SessionBinding

        dispatcher = InboundDispatcher()
        binding = SessionBinding(
            channel_id="wecom",
            account_id="default",
            chat_id="room_1",
            chat_type=ChatType.DIRECT,
            thread_id=None,
            session_id="session_1",
            agent_id="rex",
            created_at=0,
            last_message_at=0,
        )
        msg = InboundMessage(
            channel_id="wecom",
            account_id="default",
            message_id="msg_1",
            sender_id="user_1",
            chat_id="room_1",
            chat_type=ChatType.DIRECT,
            text="/help",
            mention_text="/help",
        )

        delivered: list[str] = []

        async def fake_deliver(ctx, session_id=None):
            delivered.append(ctx.text)

        monkeypatch.setattr(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            fake_deliver,
        )

        handled = await dispatcher._handle_feishu_native_command(
            binding=binding,
            msg=msg,
            channel_config=ChannelConfig(enabled=True),
            user_text="/help",
            scope_override=None,
        )

        assert handled is True
        assert delivered
        assert "Available / commands:" in delivered[0]

    @pytest.mark.asyncio
    async def test_append_user_message_stores_feishu_media_part(self, monkeypatch):
        from flocks.channel.inbound.dispatcher import InboundDispatcher
        from flocks.config.config import ChannelConfig

        created_message = SimpleNamespace(id="message_user_1")
        store_part = AsyncMock()

        monkeypatch.setattr(
            "flocks.session.message.Message.create",
            AsyncMock(return_value=created_message),
        )
        monkeypatch.setattr(
            "flocks.session.message.Message.store_part",
            store_part,
        )
        monkeypatch.setattr(
            "flocks.channel.builtin.feishu.inbound_media.download_inbound_media",
            AsyncMock(
                return_value=SimpleNamespace(
                    filename="diagram.png",
                    mime="image/png",
                    url="file:///tmp/diagram.png",
                    source={"channel": "feishu"},
                )
            ),
        )

        await InboundDispatcher._append_user_message(
            "session_1",
            "",
            InboundMessage(
                channel_id="feishu",
                account_id="default",
                message_id="om_1",
                sender_id="ou_user",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                media_url="lark://image/img_1",
            ),
            ChannelConfig(enabled=True, appId="app-id", appSecret="app-secret"),
        )

        store_part.assert_awaited_once()
        stored_part = store_part.await_args.args[2]
        assert stored_part.type == "file"
        assert stored_part.filename == "diagram.png"
        assert stored_part.mime == "image/png"
        assert stored_part.url == "file:///tmp/diagram.png"


class TestMultimodalInput:
    @pytest.mark.asyncio
    async def test_runner_builds_multimodal_user_message_for_image_parts(self, tmp_path, monkeypatch):
        from flocks.session.message import FilePart, MessageRole, TextPart
        from flocks.session.runner import SessionRunner

        image_path = tmp_path / "sample.png"
        image_path.write_bytes(b"image-bytes")

        runner = SessionRunner(
            session=SimpleNamespace(id="session_1"),
            provider_id="anthropic",
        )

        monkeypatch.setattr(
            "flocks.session.runner.Message.parts",
            AsyncMock(
                return_value=[
                    TextPart(
                        sessionID="session_1",
                        messageID="message_1",
                        text="请分析这张图",
                    ),
                    FilePart(
                        sessionID="session_1",
                        messageID="message_1",
                        mime="image/png",
                        filename="sample.png",
                        url=image_path.resolve().as_uri(),
                    ),
                ]
            ),
        )

        chat_messages = await runner._to_chat_messages(
            [SimpleNamespace(id="message_1", role=MessageRole.USER)],
            [],
        )

        assert len(chat_messages) == 1
        assert isinstance(chat_messages[0].content, list)
        assert chat_messages[0].content[0] == {"type": "text", "text": "请分析这张图"}
        assert chat_messages[0].content[1]["type"] == "image"
        assert chat_messages[0].content[1]["mimeType"] == "image/png"

    def test_anthropic_provider_formats_image_blocks(self):
        from flocks.provider.provider import ChatMessage
        from flocks.provider.sdk.anthropic import AnthropicProvider

        formatted = AnthropicProvider._format_messages_anthropic([
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "看图"},
                    {"type": "image", "mimeType": "image/png", "data": "YWJj"},
                ],
            )
        ])

        assert formatted == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "YWJj",
                        },
                    },
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_runner_extracts_plain_text_file_content(self, tmp_path, monkeypatch):
        from flocks.session.message import FilePart, MessageRole
        from flocks.session.runner import SessionRunner

        text_path = tmp_path / "notes.txt"
        text_path.write_text("line 1\nline 2", encoding="utf-8")

        runner = SessionRunner(
            session=SimpleNamespace(id="session_1"),
            provider_id="anthropic",
        )

        monkeypatch.setattr(
            "flocks.session.runner.Message.parts",
            AsyncMock(
                return_value=[
                    FilePart(
                        sessionID="session_1",
                        messageID="message_1",
                        mime="text/plain",
                        filename="notes.txt",
                        url=text_path.resolve().as_uri(),
                    ),
                ]
            ),
        )

        chat_messages = await runner._to_chat_messages(
            [SimpleNamespace(id="message_1", role=MessageRole.USER)],
            [],
        )

        assert len(chat_messages) == 1
        assert isinstance(chat_messages[0].content, str)
        assert "[Attached file: notes.txt]" in chat_messages[0].content
        assert "line 1" in chat_messages[0].content
        assert "line 2" in chat_messages[0].content

    @pytest.mark.asyncio
    async def test_runner_extracts_pdf_content(self, tmp_path, monkeypatch):
        from flocks.session.message import FilePart, MessageRole
        from flocks.session.runner import SessionRunner

        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-test")

        runner = SessionRunner(
            session=SimpleNamespace(id="session_1"),
            provider_id="anthropic",
        )

        monkeypatch.setattr(
            "flocks.session.runner.Message.parts",
            AsyncMock(
                return_value=[
                    FilePart(
                        sessionID="session_1",
                        messageID="message_1",
                        mime="application/pdf",
                        filename="report.pdf",
                        url=pdf_path.resolve().as_uri(),
                    ),
                ]
            ),
        )
        monkeypatch.setattr(
            SessionRunner,
            "_extract_pdf_text_from_bytes",
            classmethod(lambda cls, data, max_pages=20, max_chars=12000: "PDF body text"),
        )

        chat_messages = await runner._to_chat_messages(
            [SimpleNamespace(id="message_1", role=MessageRole.USER)],
            [],
        )

        assert len(chat_messages) == 1
        assert isinstance(chat_messages[0].content, str)
        assert "[Attached file: report.pdf]" in chat_messages[0].content
        assert "PDF body text" in chat_messages[0].content

    def test_max_size_eviction(self):
        from flocks.channel.inbound.dispatcher import MessageDedup
        dedup = MessageDedup(ttl_seconds=300, max_size=3)
        for i in range(4):
            dedup.is_duplicate(f"msg_{i}")
        assert dedup.is_duplicate("msg_0") is False


# =====================================================================
# 7. _check_allowlist (now in dispatcher module)
# =====================================================================

class TestCheckAllowlist:
    def _make_msg(self, *, chat_type=ChatType.DIRECT, sender_id="u1"):
        return InboundMessage(
            channel_id="test", account_id="acc", message_id="m1",
            sender_id=sender_id, chat_type=chat_type,
        )

    def test_dm_open_allows_all(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig(dm_policy="open")
        assert _check_allowlist(self._make_msg(), cfg) is True

    def test_dm_allowlist_blocks_unlisted(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig(dm_policy="allowlist", allow_from=["u2"])
        assert _check_allowlist(self._make_msg(sender_id="u1"), cfg) is False

    def test_dm_allowlist_passes_listed(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig(dm_policy="allowlist", allow_from=["u1"])
        assert _check_allowlist(self._make_msg(sender_id="u1"), cfg) is True

    def test_dm_allowlist_empty_blocks_all(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig(dm_policy="allowlist", allow_from=None)
        assert _check_allowlist(self._make_msg(), cfg) is False

    def test_group_allow_from_blocks(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig(allow_from=["u2"])
        msg = self._make_msg(chat_type=ChatType.GROUP, sender_id="u1")
        assert _check_allowlist(msg, cfg) is False

    def test_group_no_allow_from_passes(self):
        from flocks.channel.inbound.dispatcher import _check_allowlist
        cfg = ChannelConfig()
        msg = self._make_msg(chat_type=ChatType.GROUP)
        assert _check_allowlist(msg, cfg) is True


class TestFeishuGroupContext:
    def test_group_override_can_disable_top_level_context_cache(self):
        from flocks.channel.inbound.dispatcher import InboundDispatcher

        limit = InboundDispatcher._resolve_feishu_context_limit(
            ChannelConfig(
                enabled=True,
                mentionContextMessages=3,
                groups={"oc_group": {"mentionContextMessages": 0}},
            ),
            "oc_group",
        )

        assert limit == 0

    def test_non_mentioned_group_message_is_cached_when_enabled(self):
        from flocks.channel.inbound.dispatcher import InboundDispatcher

        dispatcher = InboundDispatcher()
        result = dispatcher._cache_feishu_group_context(
            InboundMessage(
                channel_id="feishu",
                account_id="default",
                message_id="msg_1",
                sender_id="ou_user",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                text="第一条上下文",
                mentioned=False,
            ),
            ChannelConfig(enabled=True, mentionContextMessages=2),
        )

        assert result is True
        cached = dispatcher._group_context["feishu:default:oc_group:thread:main"]
        assert [entry.text for entry in cached] == ["第一条上下文"]

    def test_mentioned_group_message_consumes_cached_context(self):
        from flocks.channel.inbound.dispatcher import InboundDispatcher

        dispatcher = InboundDispatcher()
        cfg = ChannelConfig(enabled=True, mentionContextMessages=2)

        dispatcher._cache_feishu_group_context(
            InboundMessage(
                channel_id="feishu",
                account_id="default",
                message_id="msg_1",
                sender_id="ou_a",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                text="先看这个日志",
                mentioned=False,
            ),
            cfg,
        )
        dispatcher._cache_feishu_group_context(
            InboundMessage(
                channel_id="feishu",
                account_id="default",
                message_id="msg_2",
                sender_id="ou_b",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                text="还有这个异常",
                mentioned=False,
            ),
            cfg,
        )

        context = dispatcher._pull_feishu_group_context(
            InboundMessage(
                channel_id="feishu",
                account_id="default",
                message_id="msg_3",
                sender_id="ou_c",
                chat_id="oc_group",
                chat_type=ChatType.GROUP,
                text="@bot 帮我总结",
                mention_text="帮我总结",
                mentioned=True,
            ),
            cfg,
        )

        assert isinstance(context, str)
        assert "[Recent group context]" in context
        assert "- ou_a: 先看这个日志" in context
        assert "- ou_b: 还有这个异常" in context
        assert "feishu:default:oc_group:thread:main" not in dispatcher._group_context


# =====================================================================
# 8. ChannelDeliveryCallbacks
# =====================================================================

class TestChannelDeliveryCallbacks:
    def test_build_ctx(self):
        from flocks.channel.inbound.dispatcher import ChannelDeliveryCallbacks
        cb = ChannelDeliveryCallbacks(
            channel_id="feishu", account_id="acc",
            chat_id="chat_1", thread_id="t1", reply_to_id="r1",
        )
        ctx = cb._build_ctx("hello")
        assert isinstance(ctx, OutboundContext)
        assert ctx.channel_id == "feishu"
        assert ctx.to == "chat_1"
        assert ctx.text == "hello"
        assert ctx.reply_to_id == "r1"
        assert ctx.thread_id == "t1"

    async def test_deliver_text_empty_noop(self):
        from flocks.channel.inbound.dispatcher import ChannelDeliveryCallbacks
        cb = ChannelDeliveryCallbacks(
            channel_id="feishu", account_id="acc", chat_id="chat_1",
        )
        with patch("flocks.channel.outbound.deliver.OutboundDelivery.deliver") as mock:
            await cb.deliver_text("")
            mock.assert_not_called()

    async def test_on_error_delegates_to_deliver_text(self):
        from flocks.channel.inbound.dispatcher import ChannelDeliveryCallbacks
        cb = ChannelDeliveryCallbacks(
            channel_id="feishu", account_id="acc", chat_id="chat_1",
        )
        cb.deliver_text = AsyncMock()
        await cb.on_error("boom")
        cb.deliver_text.assert_awaited_once()
        call_arg = cb.deliver_text.call_args[0][0]
        assert "boom" in call_arg


# =====================================================================
# 9. OutboundDelivery with OutboundContext
# =====================================================================

class TestOutboundDelivery:
    async def test_deliver_unregistered_channel(self):
        from flocks.channel.outbound.deliver import OutboundDelivery
        ctx = OutboundContext(channel_id="nonexistent", to="u1", text="hi")
        with patch("flocks.channel.outbound.deliver.default_registry") as reg:
            reg.get.return_value = None
            results = await OutboundDelivery.deliver(ctx)
        assert len(results) == 1
        assert results[0].success is False
        assert "not registered" in results[0].error

    async def test_deliver_single_chunk(self):
        from flocks.channel.outbound.deliver import OutboundDelivery
        plugin = _StubChannel()
        ctx = OutboundContext(channel_id="stub", to="u1", text="hello")

        with patch("flocks.channel.outbound.deliver.default_registry") as reg, \
             patch("flocks.channel.outbound.deliver.HookPipeline") as hp:
            reg.get.return_value = plugin
            mock_hook_ctx = MagicMock()
            mock_hook_ctx.output = {}
            hp.run_channel_outbound_before = AsyncMock(return_value=mock_hook_ctx)
            hp.run_channel_outbound_after = AsyncMock()

            results = await OutboundDelivery.deliver(ctx, session_id="ses_1")

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].message_id == "msg_001"
        assert len(plugin._sent) == 1
        assert plugin._sent[0].text == "hello"

    async def test_deliver_hook_blocks_message(self):
        from flocks.channel.outbound.deliver import OutboundDelivery
        plugin = _StubChannel()
        ctx = OutboundContext(channel_id="stub", to="u1", text="blocked msg")

        with patch("flocks.channel.outbound.deliver.default_registry") as reg, \
             patch("flocks.channel.outbound.deliver.HookPipeline") as hp:
            reg.get.return_value = plugin
            mock_hook_ctx = MagicMock()
            mock_hook_ctx.output = {"blocked": True}
            hp.run_channel_outbound_before = AsyncMock(return_value=mock_hook_ctx)

            results = await OutboundDelivery.deliver(ctx)

        assert len(results) == 1
        assert results[0].success is True
        assert len(plugin._sent) == 0


# =====================================================================
# 10. ChannelRegistry
# =====================================================================

class TestChannelRegistry:
    def test_register_and_get(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        plugin = _StubChannel("test_ch", "Test Channel")
        reg.register(plugin)
        assert reg.get("test_ch") is plugin

    def test_get_by_alias(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        plugin = _StubChannel("test_ch", "Test Channel")
        reg.register(plugin)
        assert reg.get("s") is plugin

    def test_get_case_insensitive(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        plugin = _StubChannel("Feishu", "Feishu")
        reg.register(plugin)
        assert reg.get("feishu") is plugin
        assert reg.get("FEISHU") is plugin

    def test_get_missing_returns_none(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        assert reg.get("nonexistent") is None

    def test_list_channels_deduplicated(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        p1 = _StubChannel("ch1", "CH1")
        p2 = _StubChannel("ch2", "CH2")
        reg.register(p1)
        reg.register(p2)
        channels = reg.list_channels()
        assert len(channels) == 2

    def test_reset_clears_state(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        reg.register(_StubChannel())
        reg.reset()
        assert reg.get("stub") is None


# =====================================================================
# 11. GatewayManager helpers
# =====================================================================

class TestGatewayManagerHelpers:
    async def test_sleep_or_abort_returns_false_on_timeout(self):
        from flocks.channel.gateway.manager import GatewayManager
        event = asyncio.Event()
        result = await GatewayManager._sleep_or_abort(event, 0.01)
        assert result is False

    async def test_sleep_or_abort_returns_true_on_abort(self):
        from flocks.channel.gateway.manager import GatewayManager
        event = asyncio.Event()
        event.set()
        result = await GatewayManager._sleep_or_abort(event, 10.0)
        assert result is True

    async def test_mark_connected_sets_flag(self):
        from flocks.channel.gateway.manager import GatewayManager
        plugin = _StubChannel()
        plugin.reset_status("test")
        with patch("flocks.bus.bus.Bus.publish", new_callable=AsyncMock):
            await GatewayManager._mark_connected(plugin, "test")
        assert plugin.status.connected is True

    def test_record_error_updates_status(self):
        from flocks.channel.gateway.manager import GatewayManager
        plugin = _StubChannel()
        plugin.reset_status("test")
        plugin.mark_connected()
        GatewayManager._record_error(plugin, "test", RuntimeError("fail"))
        assert plugin.status.connected is False
        assert plugin.status.last_error == "fail"
        assert plugin.status.error_count == 1

    async def test_stop_all_drains_cancelled_tasks(self, monkeypatch):
        from flocks.channel.gateway.manager import GatewayManager
        from flocks.channel.registry import ChannelRegistry

        registry = ChannelRegistry()
        plugin = _StubChannel()
        registry.register(plugin)
        manager = GatewayManager(registry=registry)
        cancelled = asyncio.Event()

        async def _blocking() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(_blocking())
        await asyncio.sleep(0)
        manager._running["stub"] = task
        manager._abort_events["stub"] = asyncio.Event()

        async def _fake_wait(tasks, timeout):
            return set(), set(tasks)

        monkeypatch.setattr("flocks.channel.gateway.manager.asyncio.wait", _fake_wait)

        await manager.stop_all()

        assert cancelled.is_set()
        assert task.done()
