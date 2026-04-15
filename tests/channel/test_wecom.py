"""
Tests for the WeCom channel implementation (WebSocket long-connection mode).

Covers:
  - channel: meta, capabilities, validate_config, send_text, frame parsing
  - registry: WeComChannel is discoverable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.channel.base import (
    ChatType,
    DeliveryResult,
    OutboundContext,
)
from flocks.channel.builtin.wecom.channel import (
    WeComChannel,
    _extract_content,
    _extract_mixed,
    _parse_frame,
)


# ------------------------------------------------------------------
# WeComChannel — meta, capabilities, config validation
# ------------------------------------------------------------------

class TestWeComChannelMeta:
    def setup_method(self):
        self.ch = WeComChannel()

    def test_meta(self):
        m = self.ch.meta()
        assert m.id == "wecom"
        assert "wxwork" in m.aliases

    def test_capabilities(self):
        cap = self.ch.capabilities()
        assert ChatType.DIRECT in cap.chat_types
        assert ChatType.GROUP in cap.chat_types
        assert cap.rich_text is True
        assert cap.threads is False
        assert cap.media is True

    def test_validate_config_missing_bot_id(self):
        err = self.ch.validate_config({"secret": "s"})
        assert err is not None and "botId" in err

    def test_validate_config_missing_secret(self):
        err = self.ch.validate_config({"botId": "b"})
        assert err is not None and "secret" in err

    def test_validate_config_ok(self):
        err = self.ch.validate_config({"botId": "b", "secret": "s"})
        assert err is None

    def test_validate_config_normalizes_group_trigger_all(self):
        """Legacy groupTrigger 'all' should be normalized to 'mention'."""
        config = {"botId": "b", "secret": "s", "groupTrigger": "all"}
        err = self.ch.validate_config(config)
        assert err is None
        assert config["groupTrigger"] == "mention"

    def test_validate_config_keeps_group_trigger_mention(self):
        config = {"botId": "b", "secret": "s", "groupTrigger": "mention"}
        self.ch.validate_config(config)
        assert config["groupTrigger"] == "mention"


# ------------------------------------------------------------------
# send_text — via WSClient mock
# ------------------------------------------------------------------

class TestWeComSendText:
    async def test_send_text_not_connected(self):
        ch = WeComChannel()
        ch._config = {}
        ctx = OutboundContext(channel_id="wecom", to="zhangsan", text="hello")
        result = await ch.send_text(ctx)
        assert result.success is False
        assert "not connected" in result.error.lower()

    async def test_send_text_with_send_message(self):
        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.send_message = AsyncMock()

        ctx = OutboundContext(channel_id="wecom", to="zhangsan", text="hello")

        with patch("wecom_aibot_sdk.generate_req_id",
                    return_value="stream-123", create=True):
            result = await ch.send_text(ctx)

        assert result.success is True
        assert result.channel_id == "wecom"
        ch._ws_client.send_message.assert_awaited_once()
        call_args = ch._ws_client.send_message.call_args
        assert call_args[0][0] == "zhangsan"
        assert call_args[0][1]["msgtype"] == "markdown"

    async def test_send_text_with_reply_stream(self):
        """When reply_to_id matches a cached frame, use replyStream."""
        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.reply_stream = AsyncMock()

        fake_frame = {"body": {"msgid": "m1"}, "headers": {"req_id": "r1"}}
        ch._cache_frame("m1", fake_frame)

        ctx = OutboundContext(
            channel_id="wecom", to="zhangsan",
            text="reply!", reply_to_id="m1",
        )

        with patch("wecom_aibot_sdk.generate_req_id",
                    return_value="stream-456", create=True):
            result = await ch.send_text(ctx)

        assert result.success is True
        ch._ws_client.reply_stream.assert_awaited_once()
        args = ch._ws_client.reply_stream.call_args[0]
        assert args[0] is fake_frame
        assert args[1] == "stream-456"
        assert args[2] == "reply!"
        assert args[3] is True  # finish=True

    async def test_send_text_exception(self):
        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.send_message = AsyncMock(side_effect=RuntimeError("timeout"))

        ctx = OutboundContext(channel_id="wecom", to="zhangsan", text="hello")
        result = await ch.send_text(ctx)

        assert result.success is False
        assert result.retryable is True
        assert "timeout" in result.error


# ------------------------------------------------------------------
# normalize_target
# ------------------------------------------------------------------

class TestWeComNormalizeTarget:
    def test_strip_user_prefix(self):
        ch = WeComChannel()
        assert ch.normalize_target("user:zhangsan") == "zhangsan"

    def test_strip_group_prefix(self):
        ch = WeComChannel()
        assert ch.normalize_target("group:chatid_123") == "chatid_123"

    def test_no_prefix(self):
        ch = WeComChannel()
        assert ch.normalize_target("lisi") == "lisi"


# ------------------------------------------------------------------
# Frame parsing (wecom-aibot-sdk frame format)
# ------------------------------------------------------------------

class TestParseFrame:
    def test_text_message_dm(self):
        frame = {
            "body": {
                "msgid": "msg001",
                "chattype": "single",
                "from": {"userid": "zhangsan"},
                "msgtype": "text",
                "text": {"content": "你好"},
            }
        }
        msg = _parse_frame(frame, {"_account_id": "default"})
        assert msg is not None
        assert msg.text == "你好"
        assert msg.sender_id == "zhangsan"
        assert msg.chat_id == "zhangsan"
        assert msg.chat_type == ChatType.DIRECT
        assert msg.channel_id == "wecom"

    def test_text_message_group(self):
        frame = {
            "body": {
                "msgid": "msg002",
                "chattype": "group",
                "chatid": "grp_001",
                "from": {"userid": "lisi"},
                "msgtype": "text",
                "text": {"content": "@Bot 查一下"},
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert msg.chat_type == ChatType.GROUP
        assert msg.chat_id == "grp_001"
        assert msg.sender_id == "lisi"
        assert "@Bot" not in msg.text
        assert "查一下" in msg.text
        assert msg.mentioned is True

    def test_image_message(self):
        frame = {
            "body": {
                "msgid": "msg003",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "image",
                "image": {"url": "https://example.com/img.jpg", "aeskey": "k1"},
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert msg.text == "[图片消息]"
        assert msg.media_url == "https://example.com/img.jpg"

    def test_voice_message(self):
        frame = {
            "body": {
                "msgid": "msg004",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "voice",
                "voice": {"content": "语音转文字内容"},
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert msg.text == "语音转文字内容"

    def test_file_message(self):
        frame = {
            "body": {
                "msgid": "msg005",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "file",
                "file": {"url": "https://example.com/file.pdf"},
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert msg.text == "[文件消息]"
        assert msg.media_url == "https://example.com/file.pdf"

    def test_mixed_message(self):
        frame = {
            "body": {
                "msgid": "msg006",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "mixed",
                "mixed": {
                    "msg_item": [
                        {"msgtype": "text", "text": {"content": "看这个"}},
                        {"msgtype": "image", "image": {"url": "https://img.com/a.png"}},
                    ]
                },
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert "看这个" in msg.text
        assert "[图片]" in msg.text

    def test_stream_type_ignored(self):
        frame = {
            "body": {
                "msgid": "msg007",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "stream",
                "stream": {"id": "s1"},
            }
        }
        assert _parse_frame(frame, {}) is None

    def test_empty_text_ignored(self):
        frame = {
            "body": {
                "msgid": "msg008",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "text",
                "text": {"content": ""},
            }
        }
        assert _parse_frame(frame, {}) is None


# ------------------------------------------------------------------
# _extract_content / _extract_mixed
# ------------------------------------------------------------------

class TestExtractContent:
    def test_text(self):
        text, media = _extract_content({"msgtype": "text", "text": {"content": "hi"}})
        assert text == "hi"
        assert media is None

    def test_image(self):
        text, media = _extract_content({
            "msgtype": "image", "image": {"url": "https://x.com/a.png"},
        })
        assert text == "[图片消息]"
        assert media == "https://x.com/a.png"

    def test_unknown_type(self):
        text, media = _extract_content({"msgtype": "location"})
        assert text == ""
        assert media is None


class TestExtractMixed:
    def test_mixed_text_and_image(self):
        result = _extract_mixed({
            "msg_item": [
                {"msgtype": "text", "text": {"content": "A"}},
                {"msgtype": "image", "image": {"url": "https://x.com/b.png"}},
                {"msgtype": "text", "text": {"content": "B"}},
            ]
        })
        assert "A" in result
        assert "[图片]" in result
        assert "B" in result


# ------------------------------------------------------------------
# Frame cache
# ------------------------------------------------------------------

class TestFrameCache:
    def test_cache_bounded(self):
        ch = WeComChannel()
        for i in range(600):
            ch._cache_frame(f"msg_{i}", {"body": {"msgid": f"msg_{i}"}})
        assert len(ch._frame_cache) == 500

    def test_cache_pop(self):
        ch = WeComChannel()
        ch._cache_frame("m1", {"body": {"msgid": "m1"}})
        assert ch._frame_cache.get("m1") is not None
        frame = ch._frame_cache.pop("m1", None)
        assert frame is not None
        assert ch._frame_cache.get("m1") is None


# ------------------------------------------------------------------
# Registry integration
# ------------------------------------------------------------------

class TestWeComRegistry:
    def test_wecom_registered(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        reg._register_builtin_channels()
        plugin = reg.get("wecom")
        assert plugin is not None
        assert plugin.meta().id == "wecom"

    def test_wecom_alias_wxwork(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        reg._register_builtin_channels()
        assert reg.get("wxwork") is not None
