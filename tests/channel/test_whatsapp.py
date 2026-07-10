from __future__ import annotations

from pathlib import Path

import pytest

from flocks.channel.base import ChatType, OutboundContext
from flocks.channel.builtin.whatsapp.channel import WhatsAppChannel
from flocks.channel.builtin.whatsapp.config import (
    matches_identifier,
    normalize_jid,
    parse_target,
    strip_jid,
)
from flocks.channel.builtin.whatsapp.inbound import build_inbound_message
from flocks.channel.builtin.whatsapp import pairing
from flocks.channel.registry import ChannelRegistry


def test_whatsapp_channel_metadata_and_capabilities():
    channel = WhatsAppChannel()

    assert channel.meta().id == "whatsapp"
    assert "wa" in channel.meta().aliases
    assert channel.capabilities().media is True
    assert channel.capabilities().chat_types == [ChatType.DIRECT, ChatType.GROUP]


def test_registry_registers_builtin_whatsapp_channel():
    registry = ChannelRegistry()
    registry.init()

    plugin = registry.get("whatsapp")
    assert plugin is not None
    assert plugin.meta().label == "WhatsApp"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+15551234567", "15551234567@s.whatsapp.net"),
        ("15551234567", "15551234567@s.whatsapp.net"),
        ("120363001234@g.us", "120363001234@g.us"),
        ("abc123@lid", "abc123@lid"),
        ("whatsapp:+15551234567", "15551234567@s.whatsapp.net"),
    ],
)
def test_normalize_and_parse_targets(raw: str, expected: str):
    assert parse_target(raw) == expected
    assert normalize_jid(raw.replace("whatsapp:", "")) == expected


def test_strip_jid_and_allowlist_alias_matching():
    assert strip_jid("15551234567@s.whatsapp.net") == "15551234567"
    assert matches_identifier("15551234567@s.whatsapp.net", ["+15551234567"])
    assert matches_identifier("abc123@lid", ["abc123"])
    assert matches_identifier("120363001234@g.us", ["120363001234@g.us"])
    assert not matches_identifier("15550000000@s.whatsapp.net", ["15551234567"])


def test_build_inbound_dm_message():
    msg = build_inbound_message({
        "messageId": "m1",
        "chatId": "15551234567@s.whatsapp.net",
        "senderId": "15551234567@s.whatsapp.net",
        "senderName": "Alice",
        "body": "hello",
        "isGroup": False,
    })

    assert msg is not None
    assert msg.channel_id == "whatsapp"
    assert msg.chat_type == ChatType.DIRECT
    assert msg.sender_id == "15551234567"
    assert msg.chat_id == "15551234567@s.whatsapp.net"
    assert msg.text == "hello"
    assert msg.message_id == "15551234567@s.whatsapp.net:m1"


def test_build_inbound_group_mention_message():
    msg = build_inbound_message({
        "messageId": "m2",
        "chatId": "120363001234@g.us",
        "senderId": "15551234567@s.whatsapp.net",
        "body": "@bot investigate this",
        "isGroup": True,
        "mentioned": True,
        "mentionText": "investigate this",
        "quotedMessageId": "q1",
    })

    assert msg is not None
    assert msg.chat_type == ChatType.GROUP
    assert msg.mentioned is True
    assert msg.mention_text == "investigate this"
    assert msg.reply_to_id == "q1"


def test_build_inbound_media_message(tmp_path: Path):
    media = tmp_path / "image.jpg"
    media.write_bytes(b"jpg")

    msg = build_inbound_message({
        "messageId": "m3",
        "chatId": "15551234567@s.whatsapp.net",
        "senderId": "15551234567@s.whatsapp.net",
        "body": "",
        "mediaUrls": [str(media)],
        "mime": "image/jpeg",
    })

    assert msg is not None
    assert msg.media_url == str(media)
    assert msg.media_mime == "image/jpeg"


def test_validate_config_requires_valid_mode_and_pairing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    channel = WhatsAppChannel()
    monkeypatch.setattr("flocks.channel.builtin.whatsapp.channel.find_executable", lambda name: "/usr/bin/node")

    assert channel.validate_config({"mode": "bad"}) == "WhatsApp mode must be 'bot' or 'self-chat'"

    session = tmp_path / "session"
    session.mkdir()
    assert "not paired" in (channel.validate_config({"sessionPath": str(session)}) or "")

    (session / "creds.json").write_text("{}", encoding="utf-8")
    assert channel.validate_config({"sessionPath": str(session), "bridgePort": 3100}) is None


@pytest.mark.asyncio
async def test_send_text_posts_to_bridge(monkeypatch: pytest.MonkeyPatch):
    channel = WhatsAppChannel()
    channel._http = _FakeSession({"success": True, "messageId": "wa1"})  # type: ignore[attr-defined]
    channel._send_timeout_ms = 1000  # type: ignore[attr-defined]
    channel._bridge_port = 3100  # type: ignore[attr-defined]

    result = await channel.send_text(
        OutboundContext(channel_id="whatsapp", to="+15551234567", text="hello", reply_to_id="q1")
    )

    assert result.success is True
    assert result.message_id == "wa1"
    assert result.chat_id == "15551234567@s.whatsapp.net"
    assert channel._http.calls == [  # type: ignore[attr-defined]
        (
            "http://127.0.0.1:3100/send",
            {
                "chatId": "15551234567@s.whatsapp.net",
                "message": "hello",
                "replyTo": "q1",
            },
            {"X-Flocks-Bridge-Token": channel._bridge_token},  # type: ignore[attr-defined]
        )
    ]


@pytest.mark.asyncio
async def test_send_text_strips_composite_reply_id():
    channel = WhatsAppChannel()
    channel._http = _FakeSession({"success": True, "messageId": "wa1"})  # type: ignore[attr-defined]
    channel._send_timeout_ms = 1000  # type: ignore[attr-defined]
    channel._bridge_port = 3100  # type: ignore[attr-defined]

    result = await channel.send_text(
        OutboundContext(
            channel_id="whatsapp",
            to="+15551234567",
            text="hello",
            reply_to_id="15551234567@s.whatsapp.net:q1",
        )
    )

    assert result.success is True
    assert channel._http.calls[0][1]["replyTo"] == "q1"  # type: ignore[attr-defined]


def test_bridge_identity_requires_matching_session_and_config(tmp_path: Path):
    channel = WhatsAppChannel()
    channel._session_path = tmp_path / "session"  # type: ignore[attr-defined]
    channel._media_cache_dir = tmp_path / "media"  # type: ignore[attr-defined]
    channel._mode = "bot"  # type: ignore[attr-defined]
    channel._reply_prefix = ""  # type: ignore[attr-defined]
    channel._send_chunk_delay_ms = 300  # type: ignore[attr-defined]
    channel._send_timeout_ms = 60000  # type: ignore[attr-defined]
    script = tmp_path / "bridge.js"
    script.write_text("console.log('bridge')", encoding="utf-8")

    matching = {
        "scriptHash": "placeholder",
        "sessionPath": str(channel._session_path),  # type: ignore[attr-defined]
        "mediaDir": str(channel._media_cache_dir),  # type: ignore[attr-defined]
        "mode": "bot",
        "configHash": channel._bridge_config_hash(),  # type: ignore[attr-defined]
    }
    from flocks.channel.builtin.whatsapp.bridge_runtime import file_hash

    matching["scriptHash"] = file_hash(script)
    assert channel._bridge_identity_matches(matching, script) is True  # type: ignore[attr-defined]

    mismatched = {**matching, "sessionPath": str(tmp_path / "other")}
    assert channel._bridge_identity_matches(mismatched, script) is False  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pairing_rejects_running_session_after_dependency_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    (bridge_dir / "bridge.js").write_text("console.log('bridge')", encoding="utf-8")
    session = tmp_path / "session"
    session.mkdir()
    (session / "bridge.pid").write_text(str(__import__("os").getpid()), encoding="utf-8")
    calls: list[Path] = []

    async def fake_ensure(path: Path) -> None:
        calls.append(path)

    monkeypatch.setattr(pairing, "find_executable", lambda name: "/usr/bin/node")
    monkeypatch.setattr(pairing, "ensure_bridge_deps", fake_ensure)

    with pytest.raises(RuntimeError, match="already running"):
        await pairing.start_pairing(session_path=str(session), bridge_dir=str(bridge_dir))

    assert calls == [bridge_dir]


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, dict, dict | None]] = []

    def post(self, url: str, *, json: dict, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return _FakeResponse(self.payload)
