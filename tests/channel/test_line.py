from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import ChatType, InboundMessage, OutboundContext
from flocks.channel.builtin.line.channel import LineChannel
from flocks.channel.builtin.line.config import (
    source_allowed,
    verify_line_signature,
)
from flocks.channel.builtin.line.format import (
    split_for_line,
    strip_markdown_preserving_urls,
)
from flocks.channel.builtin.line.inbound import build_inbound_message


def _sign(body: bytes, secret: str = "sec") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_verify_line_signature_accepts_valid_signature():
    body = b'{"events":[]}'
    assert verify_line_signature(body, _sign(body), "sec")


def test_verify_line_signature_rejects_tampered_body():
    body = b'{"events":[]}'
    assert not verify_line_signature(body + b" ", _sign(body), "sec")


def test_source_allowlist_defaults_to_open():
    assert source_allowed({"type": "user", "userId": "U1"}, {})


def test_source_allowlist_restricts_by_source_type():
    cfg = {"allowedUsers": ["U1"], "allowedGroups": ["C1"]}
    assert source_allowed({"type": "user", "userId": "U1"}, cfg)
    assert not source_allowed({"type": "user", "userId": "U2"}, cfg)
    assert source_allowed({"type": "group", "groupId": "C1"}, cfg)
    assert not source_allowed({"type": "room", "roomId": "R1"}, cfg)


def test_build_inbound_direct_text():
    event = {
        "type": "message",
        "webhookEventId": "evt_1",
        "source": {"type": "user", "userId": "Uuser"},
        "message": {"id": "m1", "type": "text", "text": "hello"},
    }
    msg = build_inbound_message(event, account_id="default")
    assert msg is not None
    assert msg.channel_id == "line"
    assert msg.chat_type == ChatType.DIRECT
    assert msg.chat_id == "Uuser"
    assert msg.sender_id == "Uuser"
    assert msg.message_id == "evt_1"
    assert msg.text == "hello"


def test_build_inbound_group_mention_strips_mention_range():
    event = {
        "type": "message",
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Uuser"},
        "message": {
            "id": "m1",
            "type": "text",
            "text": "@bot summarize",
            "mention": {"mentionees": [{"index": 0, "length": 4, "userId": "Ubot"}]},
        },
    }
    msg = build_inbound_message(event, account_id="default", bot_user_id="Ubot")
    assert msg is not None
    assert msg.chat_type == ChatType.GROUP
    assert msg.chat_id == "Cgroup"
    assert msg.mentioned is True
    assert msg.mention_text == "summarize"


def test_build_inbound_media_uses_line_uri():
    event = {
        "type": "message",
        "source": {"type": "user", "userId": "Uuser"},
        "message": {"id": "m1", "type": "image"},
    }
    msg = build_inbound_message(event, account_id="default")
    assert msg is not None
    assert msg.text == "[图片]"
    assert msg.media_url == "line://image/m1"


def test_markdown_stripping_preserves_urls_and_code():
    out = strip_markdown_preserving_urls("## Title\nsee [docs](https://x.test)\n`ls`")
    assert out == "Title\nsee docs (https://x.test)\nls"


def test_split_for_line_caps_at_five_chunks():
    chunks = split_for_line("\n\n".join(["x" * 4500 for _ in range(20)]))
    assert len(chunks) <= 5
    assert chunks[-1].endswith("…")


def test_line_credentials_are_extracted_to_secret_refs(monkeypatch):
    from flocks.security.channel_secrets import extract_channel_secrets

    stored = {}

    class FakeSecretManager:
        def set(self, secret_id, value):
            stored[secret_id] = value

    monkeypatch.setattr(
        "flocks.security.secrets.get_secret_manager",
        lambda: FakeSecretManager(),
    )

    result = extract_channel_secrets({
        "line": {
            "channelAccessToken": "tok",
            "channelSecret": "sec",
            "accessToken": "legacy",
            "botUserId": "Ubot",
            "alreadySecret": "{secret:kept}",
        }
    })

    assert result["line"]["channelAccessToken"] == "{secret:channel_line_channelAccessToken}"
    assert result["line"]["channelSecret"] == "{secret:channel_line_channelSecret}"
    assert result["line"]["accessToken"] == "{secret:channel_line_accessToken}"
    assert result["line"]["botUserId"] == "Ubot"
    assert result["line"]["alreadySecret"] == "{secret:kept}"
    assert stored == {
        "channel_line_channelAccessToken": "tok",
        "channel_line_channelSecret": "sec",
        "channel_line_accessToken": "legacy",
    }


@pytest.mark.asyncio
async def test_handle_webhook_dispatches_in_background():
    channel = LineChannel()
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    await channel.start(
        {
            "channelAccessToken": "tok",
            "channelSecret": "sec",
            "botUserId": "Ubot",
        },
        on_message,
    )
    body = json.dumps({
        "events": [{
            "type": "message",
            "replyToken": "reply-token",
            "webhookEventId": "evt_1",
            "source": {"type": "user", "userId": "Uchat"},
            "message": {"id": "m1", "type": "text", "text": "hello"},
        }]
    }).encode()

    result = await channel.handle_webhook(body, {"x-line-signature": _sign(body)})
    assert result == {"ok": True}
    await asyncio.sleep(0)
    assert len(dispatched) == 1
    assert dispatched[0].text == "hello"
    assert "Uchat" in channel._reply_tokens
    assert "evt_1" in channel._reply_tokens
    assert "m1" in channel._reply_tokens
    await channel.stop()


@pytest.mark.asyncio
async def test_handle_webhook_rejects_bad_signature():
    channel = LineChannel()
    await channel.start({"channelAccessToken": "tok", "channelSecret": "sec", "botUserId": "Ubot"}, AsyncMock())
    result = await channel.handle_webhook(b'{"events":[]}', {"x-line-signature": "bad"})
    assert result == {"error": "invalid signature", "status_code": 401}
    await channel.stop()


class _FakeLineClient:
    def __init__(self):
        self.reply = AsyncMock(return_value={})
        self.push = AsyncMock(return_value={"sentMessages": [{"id": "sent_1"}]})


@pytest.mark.asyncio
async def test_send_text_uses_reply_token_first():
    channel = LineChannel()
    await channel.start({"channelAccessToken": "tok", "channelSecret": "sec", "botUserId": "Ubot"}, AsyncMock())
    fake = _FakeLineClient()
    channel._client = fake
    channel._reply_tokens["Uchat"] = ("rt", time.monotonic() + 10)

    result = await channel.send_text(OutboundContext(channel_id="line", to="Uchat", text="hi"))

    assert result.success
    fake.reply.assert_awaited_once()
    fake.push.assert_not_called()
    assert "Uchat" not in channel._reply_tokens
    await channel.stop()


@pytest.mark.asyncio
async def test_send_text_uses_message_scoped_reply_tokens_for_consecutive_events():
    channel = LineChannel()
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    await channel.start(
        {"channelAccessToken": "tok", "channelSecret": "sec", "botUserId": "Ubot"},
        on_message,
    )
    fake = _FakeLineClient()
    channel._client = fake

    for idx in [1, 2]:
        body = json.dumps({
            "events": [{
                "type": "message",
                "replyToken": f"rt{idx}",
                "webhookEventId": f"evt_{idx}",
                "source": {"type": "user", "userId": "Uchat"},
                "message": {"id": f"m{idx}", "type": "text", "text": f"hello {idx}"},
            }]
        }).encode()
        result = await channel.handle_webhook(body, {"x-line-signature": _sign(body)})
        assert result == {"ok": True}

    await asyncio.sleep(0)
    assert [msg.message_id for msg in dispatched] == ["evt_1", "evt_2"]

    first = await channel.send_text(
        OutboundContext(channel_id="line", to="Uchat", text="reply 1", reply_to_id="evt_1")
    )
    second = await channel.send_text(
        OutboundContext(channel_id="line", to="Uchat", text="reply 2", reply_to_id="evt_2")
    )

    assert first.success
    assert second.success
    assert [call.args[0] for call in fake.reply.await_args_list] == ["rt1", "rt2"]
    fake.push.assert_not_called()
    await channel.stop()


@pytest.mark.asyncio
async def test_line_chunk_text_batches_up_to_five_bubbles_for_one_reply():
    channel = LineChannel()
    await channel.start(
        {
            "channelAccessToken": "tok",
            "channelSecret": "sec",
            "pushFallback": False,
            "textChunkLimit": 10,
            "botUserId": "Ubot",
        },
        AsyncMock(),
    )
    fake = _FakeLineClient()
    channel._client = fake
    channel._reply_tokens["evt_1"] = ("rt", time.monotonic() + 10)

    text = "a" * 25
    assert channel.chunk_text(text, channel.text_chunk_limit) == [text]

    result = await channel.send_text(
        OutboundContext(channel_id="line", to="Uchat", text=text, reply_to_id="evt_1")
    )

    assert result.success
    fake.reply.assert_awaited_once()
    sent_messages = fake.reply.await_args.args[1]
    assert [message["text"] for message in sent_messages] == ["a" * 10, "a" * 10, "a" * 5]
    fake.push.assert_not_called()
    await channel.stop()


@pytest.mark.asyncio
async def test_send_media_applies_text_chunk_limit_and_reserves_media_slot():
    channel = LineChannel()
    await channel.start(
        {
            "channelAccessToken": "tok",
            "channelSecret": "sec",
            "textChunkLimit": 10,
            "botUserId": "Ubot",
        },
        AsyncMock(),
    )
    fake = _FakeLineClient()
    channel._client = fake
    channel._reply_tokens["evt_1"] = ("rt", time.monotonic() + 10)

    result = await channel.send_media(
        OutboundContext(
            channel_id="line",
            to="Uchat",
            text="b" * 50,
            media_url="https://example.test/image.png",
            reply_to_id="evt_1",
        )
    )

    assert result.success
    sent_messages = fake.reply.await_args.args[1]
    assert len(sent_messages) == 5
    assert [message["type"] for message in sent_messages] == ["text", "text", "text", "text", "image"]
    assert sent_messages[0]["text"] == "b" * 10
    assert sent_messages[-2]["text"].endswith("…")
    await channel.stop()


@pytest.mark.asyncio
async def test_send_text_push_fallback_when_reply_fails():
    channel = LineChannel()
    await channel.start({"channelAccessToken": "tok", "channelSecret": "sec", "botUserId": "Ubot"}, AsyncMock())
    fake = _FakeLineClient()
    fake.reply.side_effect = RuntimeError("expired")
    channel._client = fake
    channel._reply_tokens["Uchat"] = ("rt", time.monotonic() + 10)

    result = await channel.send_text(OutboundContext(channel_id="line", to="Uchat", text="hi"))

    assert result.success
    fake.reply.assert_awaited_once()
    fake.push.assert_awaited_once()
    assert result.message_id == "sent_1"
    await channel.stop()


@pytest.mark.asyncio
async def test_send_text_can_disable_push_fallback():
    channel = LineChannel()
    await channel.start(
        {"channelAccessToken": "tok", "channelSecret": "sec", "pushFallback": False, "botUserId": "Ubot"},
        AsyncMock(),
    )
    channel._client = _FakeLineClient()

    result = await channel.send_text(OutboundContext(channel_id="line", to="Uchat", text="hi"))

    assert not result.success
    assert "reply token" in (result.error or "")
    await channel.stop()


@pytest.mark.asyncio
async def test_download_inbound_media(monkeypatch, tmp_path):
    from flocks.channel.builtin.line import inbound_media as mod

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def fetch_content(self, message_id, *, max_bytes):
            assert message_id == "m1"
            return b"image-bytes", "image/png"

    monkeypatch.setattr(mod, "LineClient", FakeClient)
    monkeypatch.setattr(mod, "_media_storage_dir", lambda account_id: tmp_path)

    media = await mod.download_inbound_media(
        InboundMessage(
            channel_id="line",
            account_id="default",
            message_id="evt_1",
            sender_id="Uuser",
            media_url="line://image/m1",
        ),
        {"channelAccessToken": "tok", "channelSecret": "sec"},
    )

    assert media is not None
    assert media.mime == "image/png"
    assert media.filename.endswith(".png")
    assert media.source["channel"] == "line"


def test_line_file_media_filename_includes_line_message_id():
    from flocks.channel.builtin.line.inbound_media import _filename

    filename = _filename(
        InboundMessage(
            channel_id="line",
            account_id="default",
            message_id="evt_1",
            sender_id="Uuser",
            media_url="line://file/m1",
            raw={"message": {"id": "m1", "type": "file", "fileName": "report.pdf"}},
        ),
        "file",
        "application/pdf",
    )

    assert filename == "line_m1_report.pdf"


def test_line_registered_as_builtin():
    from flocks.channel.registry import ChannelRegistry

    reg = ChannelRegistry()
    reg._register_builtin_channels()
    plugin = reg.get("line")
    assert plugin is not None
    assert plugin.meta().label == "LINE"


@pytest.mark.asyncio
async def test_dispatcher_routes_line_media_downloader(monkeypatch):
    from flocks.channel.inbound import dispatcher as dispatch_mod
    import flocks.channel.builtin.line.inbound_media as line_inb

    captured = {}

    async def fake_line(msg, config):
        captured["msg"] = msg
        captured["config"] = config
        return SimpleNamespace(
            filename="x.png",
            mime="image/png",
            url="file:///tmp/x.png",
            source={"channel": "line"},
        )

    monkeypatch.setattr(line_inb, "download_inbound_media", fake_line)
    result = await dispatch_mod._download_channel_media(
        InboundMessage(
            channel_id="line",
            account_id="a",
            message_id="m",
            sender_id="u",
            media_url="line://image/ABC",
        ),
        {"channelAccessToken": "tok"},
    )
    assert result is not None
    assert captured["msg"].channel_id == "line"
    assert captured["config"] == {"channelAccessToken": "tok"}
