"""
Tests for the WeCom channel implementation (WebSocket long-connection mode).

Covers:
  - channel: meta, capabilities, validate_config, send_text, frame parsing
  - registry: WeComChannel is discoverable
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import (
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.channel.builtin.wecom.channel import (
    WeComChannel,
    _extract_content,
    _extract_mixed,
    _parse_frame,
)
from flocks.channel.builtin.wecom.inbound_media import (
    _filename_from_content_disposition,
    download_inbound_media,
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

    def test_validate_config_normalizes_reconnect_timeout(self):
        config = {
            "botId": "b",
            "secret": "s",
            "reconnectTimeoutSeconds": "12",
        }
        err = self.ch.validate_config(config)
        assert err is None
        assert config["reconnectTimeoutSeconds"] == 12.0

    def test_validate_config_rejects_invalid_reconnect_timeout(self):
        err = self.ch.validate_config({
            "botId": "b",
            "secret": "s",
            "reconnectTimeoutSeconds": "abc",
        })
        assert err == "reconnectTimeoutSeconds must be a positive number"

    def test_validate_config_rejects_non_positive_reconnect_timeout(self):
        err = self.ch.validate_config({
            "botId": "b",
            "secret": "s",
            "reconnectTimeoutSeconds": 0,
        })
        assert err == "reconnectTimeoutSeconds must be a positive number"

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


class TestWeComSendMedia:
    async def test_send_media_uploads_and_sends_file(self, tmp_path: Path):
        path = tmp_path / "report.pdf"
        path.write_bytes(b"pdf-data")

        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.upload_media = AsyncMock(
            return_value={"media_id": "media_1", "type": "file"},
        )
        ch._ws_client.send_media_message = AsyncMock(
            return_value={"body": {"msgid": "wx_msg_1"}},
        )

        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                media_url=path.as_uri(),
            )
        )

        assert result.success is True
        assert result.message_id == "wx_msg_1"
        ch._ws_client.upload_media.assert_awaited_once()
        upload_args = ch._ws_client.upload_media.await_args
        assert upload_args.args[0] == b"pdf-data"
        assert upload_args.kwargs["type"] == "file"
        assert upload_args.kwargs["filename"] == "report.pdf"
        ch._ws_client.send_media_message.assert_awaited_once_with(
            "zhangsan",
            "file",
            "media_1",
            video_title=None,
        )

    async def test_send_media_replies_with_cached_frame(self, tmp_path: Path):
        path = tmp_path / "image.png"
        path.write_bytes(b"png-data")

        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.upload_media = AsyncMock(
            return_value={"media_id": "media_img", "type": "image"},
        )
        ch._ws_client.reply_media = AsyncMock(
            return_value={"body": {"msgid": "wx_reply_1"}},
        )
        frame = {"body": {"msgid": "incoming_1"}, "headers": {"req_id": "req_1"}}
        ch._cache_frame("incoming_1", frame)

        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                media_url=path.as_uri(),
                reply_to_id="incoming_1",
            )
        )

        assert result.success is True
        assert result.message_id == "wx_reply_1"
        ch._ws_client.reply_media.assert_awaited_once_with(
            frame,
            "image",
            "media_img",
            video_title=None,
        )
        assert ch._frame_cache.get("incoming_1") is None

    async def test_send_media_not_connected(self, tmp_path: Path):
        path = tmp_path / "report.pdf"
        path.write_bytes(b"pdf-data")

        ch = WeComChannel()
        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                media_url=path.as_uri(),
            )
        )

        assert result.success is False
        assert "not connected" in result.error.lower()

    async def test_send_media_upload_missing_media_id(self, tmp_path: Path):
        path = tmp_path / "report.pdf"
        path.write_bytes(b"pdf-data")

        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.upload_media = AsyncMock(return_value={"type": "file"})

        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                media_url=path.as_uri(),
            )
        )

        assert result.success is False
        assert "media upload failed" in result.error
        ch._ws_client.send_media_message.assert_not_awaited()

    async def test_send_media_upload_exception(self, tmp_path: Path):
        path = tmp_path / "report.pdf"
        path.write_bytes(b"pdf-data")

        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.upload_media = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                media_url=path.as_uri(),
            )
        )

        assert result.success is False
        assert result.retryable is True
        assert "timeout" in result.error

    async def test_send_media_with_text_sends_text_after_media(self, tmp_path: Path):
        path = tmp_path / "report.pdf"
        path.write_bytes(b"pdf-data")

        ch = WeComChannel()
        ch._config = {"botId": "b", "secret": "s"}
        ch._ws_client = AsyncMock()
        ch._ws_client.upload_media = AsyncMock(
            return_value={"media_id": "media_1", "type": "file"},
        )
        ch._ws_client.send_media_message = AsyncMock(
            return_value={"body": {"msgid": "wx_media_1"}},
        )
        ch._ws_client.send_message = AsyncMock(
            return_value={"body": {"msgid": "wx_text_1"}},
        )

        result = await ch.send_media(
            OutboundContext(
                channel_id="wecom",
                to="zhangsan",
                text="这是附件说明",
                media_url=path.as_uri(),
            )
        )

        assert result.success is True
        assert result.message_id == "wx_media_1"
        ch._ws_client.send_media_message.assert_awaited_once()
        ch._ws_client.send_message.assert_awaited_once_with(
            "zhangsan",
            {"msgtype": "markdown", "markdown": {"content": "这是附件说明"}},
        )


# ------------------------------------------------------------------
# reconnect watchdog
# ------------------------------------------------------------------

class TestWeComReconnectWatchdog:
    async def test_watchdog_sets_timeout_event(self):
        ch = WeComChannel()
        ch._ws_client = object()
        ch._reconnect_timeout_seconds = 0.01

        ch._start_reconnect_watchdog("socket closed")

        await asyncio.wait_for(ch._reconnect_timeout_event.wait(), timeout=0.2)
        assert ch._reconnect_timeout_event.is_set() is True

    async def test_authenticated_cancels_watchdog(self):
        ch = WeComChannel()
        ch._reconnect_timeout_seconds = 0.05

        ch._start_reconnect_watchdog("socket closed")
        ch._handle_authenticated()
        await asyncio.sleep(0)

        assert ch._reconnect_timeout_event.is_set() is False
        assert ch._reconnect_watchdog_task is None

    async def test_start_raises_when_sdk_reconnect_stalls(self):
        class FakeWSClient:
            last_instance = None

            def __init__(self, *args, **kwargs):
                self.handlers = {}
                self.disconnect = AsyncMock()
                FakeWSClient.last_instance = self

            def on(self, event, handler):
                self.handlers[event] = handler

            async def connect(self):
                self.handlers["disconnected"]("socket closed")

        ch = WeComChannel()
        abort_event = asyncio.Event()
        config = {
            "botId": "bot-1",
            "secret": "secret-1",
            "reconnectTimeoutSeconds": 0.01,
        }

        with patch("wecom_aibot_sdk.WSClient", FakeWSClient):
            with pytest.raises(RuntimeError, match="reconnect timed out"):
                await ch.start(config, AsyncMock(), abort_event)

        FakeWSClient.last_instance.disconnect.assert_awaited_once()

    async def test_stop_does_not_leave_watchdog_after_intentional_disconnect(self):
        class FakeWSClient:
            def __init__(self):
                self.handlers = {}
                self.disconnect = AsyncMock(side_effect=self._disconnect)

            def on(self, event, handler):
                self.handlers[event] = handler

            async def _disconnect(self):
                self.handlers["disconnected"]("manual stop")

        ch = WeComChannel()
        ch._ws_client = FakeWSClient()
        ch._ws_client.on("disconnected", ch._handle_disconnected)

        await ch.stop()

        assert ch._reconnect_watchdog_task is None
        assert ch._ws_client is None

    async def test_start_finally_does_not_leave_watchdog_after_abort(self):
        class FakeWSClient:
            last_instance = None

            def __init__(self, *args, **kwargs):
                self.handlers = {}
                self.disconnect = AsyncMock(side_effect=self._disconnect)
                FakeWSClient.last_instance = self

            def on(self, event, handler):
                self.handlers[event] = handler

            async def connect(self):
                return None

            async def _disconnect(self):
                self.handlers["disconnected"]("abort shutdown")

        ch = WeComChannel()
        abort_event = asyncio.Event()
        abort_event.set()
        config = {"botId": "bot-1", "secret": "secret-1"}

        with patch("wecom_aibot_sdk.WSClient", FakeWSClient):
            await ch.start(config, AsyncMock(), abort_event)

        FakeWSClient.last_instance.disconnect.assert_awaited_once()
        assert ch._reconnect_watchdog_task is None
        assert ch._ws_client is None


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
                "file": {"url": "https://example.com/file.pdf", "filename": "report.pdf"},
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert msg.text == "[文件消息: report.pdf]"
        assert msg.media_url == "https://example.com/file.pdf"

    def test_file_message_no_filename(self):
        frame = {
            "body": {
                "msgid": "msg005b",
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

    def test_mixed_file_message(self):
        frame = {
            "body": {
                "msgid": "msg006b",
                "chattype": "single",
                "from": {"userid": "u1"},
                "msgtype": "mixed",
                "mixed": {
                    "msg_item": [
                        {"msgtype": "text", "text": {"content": "看附件"}},
                        {
                            "msgtype": "file",
                            "file": {
                                "url": "https://example.com/file.bin",
                                "filename": "report.bin",
                                "aeskey": "k1",
                            },
                        },
                    ]
                },
            }
        }
        msg = _parse_frame(frame, {})
        assert msg is not None
        assert "看附件" in msg.text
        assert "[文件: report.bin]" in msg.text
        assert msg.media_url == "https://example.com/file.bin"

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
        result_text, result_media = _extract_mixed({
            "msg_item": [
                {"msgtype": "text", "text": {"content": "A"}},
                {"msgtype": "image", "image": {"url": "https://x.com/b.png"}},
                {"msgtype": "text", "text": {"content": "B"}},
            ]
        })
        assert "A" in result_text
        assert "[图片]" in result_text
        assert "B" in result_text
        assert result_media == "https://x.com/b.png"

    def test_mixed_no_image(self):
        result_text, result_media = _extract_mixed({
            "msg_item": [
                {"msgtype": "text", "text": {"content": "hello"}},
            ]
        })
        assert result_text == "hello"
        assert result_media is None

    def test_mixed_text_and_file(self):
        result_text, result_media = _extract_mixed({
            "msg_item": [
                {"msgtype": "text", "text": {"content": "A"}},
                {
                    "msgtype": "file",
                    "file": {
                        "url": "https://x.com/report.pdf",
                        "filename": "report.pdf",
                    },
                },
            ]
        })
        assert "A" in result_text
        assert "[文件: report.pdf]" in result_text
        assert result_media == "https://x.com/report.pdf"


class TestContentDispositionFilename:
    def test_filename_from_content_disposition_plain(self):
        filename = _filename_from_content_disposition(
            'attachment; filename="report.pdf"',
        )
        assert filename == "report.pdf"

    def test_filename_from_content_disposition_utf8(self):
        filename = _filename_from_content_disposition(
            "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.pdf",
        )
        assert filename == "报告.pdf"


# ------------------------------------------------------------------
# 入站媒体下载
# ------------------------------------------------------------------

class TestWeComInboundMedia:
    @pytest.mark.asyncio
    async def test_download_inbound_media_streams_decrypts_and_closes(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        class FakeResponse:
            headers = {
                "content-disposition": 'attachment; filename="from-header.bin"',
            }

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, _size):
                yield b"hello"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeClient:
            def __init__(self):
                self.closed = False

            def stream(self, method, url):
                assert method == "GET"
                assert url == "https://example.com/file.bin"
                return FakeStream()

            async def aclose(self):
                self.closed = True

        class FakeWeComApiClient:
            last_instance = None

            def __init__(self, *_args, **_kwargs):
                self._client = FakeClient()
                FakeWeComApiClient.last_instance = self

            async def download_file_raw(self, _url):
                raise AssertionError("stream path should be used")

        fake_sdk = types.SimpleNamespace(
            WeComApiClient=FakeWeComApiClient,
            decrypt_file=lambda data, key: data + key.encode(),
        )
        monkeypatch.setitem(sys.modules, "wecom_aibot_sdk", fake_sdk)
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media._media_storage_dir",
            lambda _account_id: tmp_path,
        )

        media = await download_inbound_media(
            InboundMessage(
                channel_id="wecom",
                account_id="main",
                message_id="msg_1",
                sender_id="u1",
                media_url="https://example.com/file.bin",
                raw={
                    "msgtype": "file",
                    "file": {
                        "filename": "../report.bin",
                        "aeskey": "k1",
                    },
                },
            ),
            {},
            max_bytes=20,
        )

        assert media is not None
        assert media.filename == ".._report.bin"
        assert media.mime == "application/octet-stream"
        assert Path(media.url.removeprefix("file://")).read_bytes() == b"hellok1"
        assert FakeWeComApiClient.last_instance._client.closed is True

    @pytest.mark.asyncio
    async def test_download_inbound_media_rejects_large_content_length(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        class FakeResponse:
            headers = {"content-length": "30"}

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, _size):
                yield b"too-large"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeClient:
            def __init__(self):
                self.closed = False

            def stream(self, *_args, **_kwargs):
                return FakeStream()

            async def aclose(self):
                self.closed = True

        class FakeWeComApiClient:
            last_instance = None

            def __init__(self, *_args, **_kwargs):
                self._client = FakeClient()
                FakeWeComApiClient.last_instance = self

        fake_sdk = types.SimpleNamespace(
            WeComApiClient=FakeWeComApiClient,
            decrypt_file=lambda data, _key: data,
        )
        warnings = []
        monkeypatch.setitem(sys.modules, "wecom_aibot_sdk", fake_sdk)
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media._media_storage_dir",
            lambda _account_id: tmp_path,
        )
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media.log.warning",
            lambda event, data=None: warnings.append((event, data or {})),
        )

        media = await download_inbound_media(
            InboundMessage(
                channel_id="wecom",
                account_id="main",
                message_id="msg_2",
                sender_id="u1",
                media_url="https://example.com/big.bin",
                raw={"msgtype": "file", "file": {"filename": "big.bin"}},
            ),
            {},
            max_bytes=10,
        )

        assert media is None
        assert list(tmp_path.iterdir()) == []
        assert FakeWeComApiClient.last_instance._client.closed is True
        assert warnings[0][0] == "wecom.media.file_too_large"

    @pytest.mark.asyncio
    async def test_download_inbound_media_decrypt_failure_returns_none(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        class FakeResponse:
            headers = {}

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, _size):
                yield b"encrypted"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeClient:
            def __init__(self):
                self.closed = False

            def stream(self, *_args, **_kwargs):
                return FakeStream()

            async def aclose(self):
                self.closed = True

        class FakeWeComApiClient:
            last_instance = None

            def __init__(self, *_args, **_kwargs):
                self._client = FakeClient()
                FakeWeComApiClient.last_instance = self

        def fail_decrypt(_data, _key):
            raise RuntimeError("bad aes key")

        fake_sdk = types.SimpleNamespace(
            WeComApiClient=FakeWeComApiClient,
            decrypt_file=fail_decrypt,
        )
        warnings = []
        monkeypatch.setitem(sys.modules, "wecom_aibot_sdk", fake_sdk)
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media._media_storage_dir",
            lambda _account_id: tmp_path,
        )
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media.log.warning",
            lambda event, data=None: warnings.append((event, data or {})),
        )

        media = await download_inbound_media(
            InboundMessage(
                channel_id="wecom",
                account_id="main",
                message_id="msg_decrypt",
                sender_id="u1",
                media_url="https://example.com/file.bin",
                raw={"msgtype": "file", "file": {"filename": "file.bin", "aeskey": "bad"}},
            ),
            {},
            max_bytes=20,
        )

        assert media is None
        assert list(tmp_path.iterdir()) == []
        assert FakeWeComApiClient.last_instance._client.closed is True
        assert warnings[0][0] == "wecom.media.decrypt_failed"

    @pytest.mark.asyncio
    async def test_download_inbound_media_mixed_file_uses_nested_aeskey(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        class FakeResponse:
            headers = {}

            def raise_for_status(self):
                return None

            async def aiter_bytes(self, _size):
                yield b"encrypted"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeClient:
            def stream(self, *_args, **_kwargs):
                return FakeStream()

            async def aclose(self):
                return None

        class FakeWeComApiClient:
            def __init__(self, *_args, **_kwargs):
                self._client = FakeClient()

        captured = {}

        def decrypt(data, key):
            captured["key"] = key
            return data + b"-ok"

        fake_sdk = types.SimpleNamespace(
            WeComApiClient=FakeWeComApiClient,
            decrypt_file=decrypt,
        )
        monkeypatch.setitem(sys.modules, "wecom_aibot_sdk", fake_sdk)
        monkeypatch.setattr(
            "flocks.channel.builtin.wecom.inbound_media._media_storage_dir",
            lambda _account_id: tmp_path,
        )

        media = await download_inbound_media(
            InboundMessage(
                channel_id="wecom",
                account_id="main",
                message_id="msg_mixed",
                sender_id="u1",
                media_url="https://example.com/file.bin",
                raw={
                    "msgtype": "mixed",
                    "mixed": {
                        "msg_item": [
                            {"msgtype": "text", "text": {"content": "见附件"}},
                            {
                                "msgtype": "file",
                                "file": {
                                    "filename": "nested.bin",
                                    "aeskey": "nested-key",
                                },
                            },
                        ]
                    },
                },
            ),
            {},
            max_bytes=20,
        )

        assert media is not None
        assert captured["key"] == "nested-key"
        assert media.filename == "nested.bin"
        assert Path(media.url.removeprefix("file://")).read_bytes() == b"encrypted-ok"


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
