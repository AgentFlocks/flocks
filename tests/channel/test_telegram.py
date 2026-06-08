from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from flocks.channel.base import ChatType, OutboundContext
import flocks.channel.builtin.telegram.channel as tg_mod
import flocks.channel.builtin.telegram.polling as polling_mod
import flocks.channel.builtin.telegram.pairing as pairing_mod
from flocks.channel.builtin.telegram.channel import TelegramChannel
from flocks.channel.builtin.telegram.format import markdown_to_telegram_html, split_html_chunks
from flocks.channel.builtin.telegram.inbound import build_inbound_message, extract_media_description
from flocks.channel.builtin.telegram.inbound import BotIdentityResolver


# ---------------------------------------------------------------------------
# Fake HTTP helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"ok": True}

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Configurable fake HTTP client that returns pre-defined responses."""
    def __init__(self, responses: list[_FakeResponse] | None = None) -> None:
        self._responses = responses or [_FakeResponse()]
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> _FakeResponse:
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return self._responses[-1]

    async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self._next()


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------

def test_plugin_exports_telegram_channel():
    plugin = TelegramChannel()
    assert plugin.meta().id == "telegram"
    assert plugin.meta().label == "Telegram"


def test_validate_config_polling_mode_only_needs_token():
    plugin = TelegramChannel()
    error = plugin.validate_config({"botToken": "123:abc"})
    assert error is None


def test_validate_config_explicit_polling_mode():
    plugin = TelegramChannel()
    error = plugin.validate_config({"botToken": "123:abc", "mode": "polling"})
    assert error is None


def test_validate_config_webhook_mode_requires_secret():
    plugin = TelegramChannel()
    error = plugin.validate_config({"botToken": "123:abc", "mode": "webhook"})
    assert error is not None
    assert "webhookSecret" in error


def test_validate_config_webhook_mode_valid():
    plugin = TelegramChannel()
    error = plugin.validate_config({"botToken": "123:abc", "webhookSecret": "s3cr3t"})
    assert error is None


def test_validate_config_rejects_unknown_mode():
    plugin = TelegramChannel()
    error = plugin.validate_config({"botToken": "123:abc", "mode": "long-polling"})
    assert error is not None
    assert "mode" in error.lower()


def test_validate_config_rejects_missing_token():
    plugin = TelegramChannel()
    error = plugin.validate_config({})
    assert error is not None
    assert "botToken" in error


def test_validate_config_rejects_multiple_enabled_accounts():
    plugin = TelegramChannel()
    error = plugin.validate_config({
        "accounts": {
            "a": {"enabled": True, "botToken": "123:aaa", "webhookSecret": "s1"},
            "b": {"enabled": True, "botToken": "123:bbb", "webhookSecret": "s2"},
        }
    })
    assert "exactly one enabled account" in error


# ---------------------------------------------------------------------------
# Webhook mode — existing tests (unchanged behaviour)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_secret():
    plugin = TelegramChannel()
    dispatched: list[Any] = []

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "expected-secret", "botUsername": "helper_bot"},
        on_message,
    )

    result = await plugin.handle_webhook(
        json.dumps({"message": {}}).encode("utf-8"),
        {"x-telegram-bot-api-secret-token": "wrong-secret"},
    )

    assert result == {"error": "invalid webhook secret", "status_code": 401}
    assert dispatched == []


@pytest.mark.asyncio
async def test_handle_webhook_dispatches_direct_message():
    plugin = TelegramChannel()
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "expected-secret", "botUsername": "helper_bot"},
        on_message,
    )

    body = {
        "message": {
            "message_id": 88,
            "from": {"id": 42, "is_bot": False, "username": "alice", "first_name": "Alice"},
            "chat": {"id": 42, "type": "private"},
            "text": "hello there",
            "reply_to_message": {"message_id": 70},
        }
    }

    result = await plugin.handle_webhook(
        json.dumps(body).encode("utf-8"),
        {"x-telegram-bot-api-secret-token": "expected-secret"},
    )

    assert result == {"ok": True}
    assert len(dispatched) == 1
    msg = dispatched[0]
    assert msg.channel_id == "telegram"
    assert msg.account_id == "default"
    assert msg.message_id == "42:88"
    assert msg.reply_to_id == "70"
    assert msg.chat_type == ChatType.DIRECT
    assert msg.sender_id == "42"
    assert msg.chat_id == "42"
    assert msg.text == "hello there"
    assert msg.mentioned is False
    assert msg.mention_text == ""


@pytest.mark.asyncio
async def test_handle_webhook_marks_group_mentions_and_thread():
    plugin = TelegramChannel()
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "expected-secret", "botUsername": "@helper_bot"},
        on_message,
    )

    body = {
        "message": {
            "message_id": 77,
            "message_thread_id": 9,
            "from": {"id": 1001, "is_bot": False, "first_name": "Bob"},
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "text": "@helper_bot summarize this IOC",
        }
    }

    result = await plugin.handle_webhook(
        json.dumps(body).encode("utf-8"),
        {"x-telegram-bot-api-secret-token": "expected-secret"},
    )

    assert result == {"ok": True}
    assert len(dispatched) == 1
    msg = dispatched[0]
    assert msg.chat_type == ChatType.GROUP
    assert msg.message_id == "-1001234567890:77"
    assert msg.thread_id == "9"
    assert msg.mentioned is True
    assert msg.mention_text == "summarize this IOC"


# ---------------------------------------------------------------------------
# channel_post support
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_webhook_channel_post_dispatched():
    """channel_post received via webhook should be dispatched."""
    plugin = TelegramChannel()
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "s3cr3t", "botUsername": "bot"},
        on_message,
    )

    body = {
        "channel_post": {
            "message_id": 1,
            "chat": {"id": -1001111111111, "type": "channel", "title": "My Channel"},
            "text": "channel announcement",
            "date": 1700000000,
        }
    }

    result = await plugin.handle_webhook(
        json.dumps(body).encode("utf-8"),
        {"x-telegram-bot-api-secret-token": "s3cr3t"},
    )

    assert result == {"ok": True}
    assert len(dispatched) == 1
    assert dispatched[0].text == "channel announcement"
    assert dispatched[0].chat_id == "-1001111111111"


@pytest.mark.asyncio
async def test_polling_channel_post_dispatched(monkeypatch):
    """Polling loop must dispatch channel_post updates, not just message."""
    plugin = TelegramChannel()
    dispatched: list[Any] = []
    abort = asyncio.Event()
    call_count = 0

    channel_post_update = {
        "update_id": 200,
        "channel_post": {
            "message_id": 10,
            "chat": {"id": -1001111111111, "type": "channel", "title": "My Channel"},
            "text": "breaking news",
            "date": 1700000000,
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    return _FakeResponse(payload={"ok": True})
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [channel_post_update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start({"botToken": "123:abc"}, on_message, abort)

    assert len(dispatched) == 1
    msg = dispatched[0]
    assert msg.text == "breaking news"
    assert msg.chat_id == "-1001111111111"


# ---------------------------------------------------------------------------
# Polling mode tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_polling_mode_start_calls_delete_webhook(monkeypatch):
    """start() in polling mode must call deleteWebhook before the first getUpdates."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    call_log: list[str] = []

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                call_log.append(url)
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    await plugin.start({"botToken": "123:abc"}, lambda _m: None, abort)

    assert any("deleteWebhook" in u for u in call_log), "deleteWebhook was not called"
    assert any("getUpdates" in u for u in call_log), "getUpdates was not called"
    delete_idx = next(i for i, u in enumerate(call_log) if "deleteWebhook" in u)
    get_idx = next(i for i, u in enumerate(call_log) if "getUpdates" in u)
    assert delete_idx < get_idx, "deleteWebhook must be called before getUpdates"


@pytest.mark.asyncio
async def test_polling_mode_dispatches_direct_message(monkeypatch):
    """Polling mode must parse updates and dispatch InboundMessage."""
    plugin = TelegramChannel()
    dispatched: list[Any] = []
    abort = asyncio.Event()
    call_count = 0

    dm_update = {
        "update_id": 100,
        "message": {
            "message_id": 5,
            "from": {"id": 7, "is_bot": False, "username": "charlie", "first_name": "Charlie"},
            "chat": {"id": 7, "type": "private"},
            "text": "ping",
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    return _FakeResponse(payload={"ok": True})
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [dm_update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        dispatched.append(msg)

    await plugin.start({"botToken": "123:abc"}, on_message, abort)

    assert len(dispatched) == 1
    msg = dispatched[0]
    assert msg.channel_id == "telegram"
    assert msg.chat_type == ChatType.DIRECT
    assert msg.sender_id == "7"
    assert msg.text == "ping"
    assert msg.message_id == "7:5"


@pytest.mark.asyncio
async def test_polling_mode_advances_offset(monkeypatch):
    """Offset must advance to update_id + 1 after processing an update."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    recorded_offsets: list[int | None] = []
    call_count = 0

    update = {
        "update_id": 999,
        "message": {
            "message_id": 1,
            "from": {"id": 1, "is_bot": False, "first_name": "A"},
            "chat": {"id": 1, "type": "private"},
            "text": "hi",
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    return _FakeResponse(payload={"ok": True})
                recorded_offsets.append(json.get("offset"))
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        pass

    await plugin.start({"botToken": "123:abc"}, on_message, abort)

    assert len(recorded_offsets) >= 2
    assert recorded_offsets[0] is None, "First getUpdates should have no offset"
    assert recorded_offsets[1] == 1000, "Second getUpdates must use update_id + 1"


@pytest.mark.asyncio
async def test_polling_mode_aborts_cleanly_on_event(monkeypatch):
    """Setting abort_event causes the polling loop to exit without errors."""
    plugin = TelegramChannel()
    abort = asyncio.Event()

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                if "deleteWebhook" in url:
                    abort.set()
                    return _FakeResponse(payload={"ok": True})
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    await plugin.start({"botToken": "123:abc"}, lambda _m: None, abort)


@pytest.mark.asyncio
async def test_polling_mode_drains_409_before_long_poll(monkeypatch):
    """Startup drain retries non-blocking getUpdates on 409 until success."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    drain_calls = 0
    long_poll_started = False

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal drain_calls, long_poll_started
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if json.get("timeout") == 0:
                    drain_calls += 1
                    if drain_calls < 3:
                        return _FakeResponse(
                            status_code=409,
                            payload={"ok": False, "description": "Conflict"},
                        )
                    return _FakeResponse(payload={"ok": True, "result": []})
                # Long-poll: we got past the drain phase
                long_poll_started = True
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "DRAIN_RETRY_INTERVAL_S", 0.01)
    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    await plugin.start({"botToken": "123:abc"}, lambda _m: None, abort)

    assert drain_calls == 3, f"Expected 3 drain attempts, got {drain_calls}"
    assert long_poll_started, "Long-poll should have started after drain succeeded"


@pytest.mark.asyncio
async def test_polling_sends_typing_indicator(monkeypatch):
    """A sendChatAction(typing) must be sent while on_message is being awaited."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    typing_calls: list[dict] = []
    call_count = 0

    dm_update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 5, "is_bot": False, "first_name": "Eve"},
            "chat": {"id": 5, "type": "private"},
            "text": "hello",
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    typing_calls.append(json)
                    return _FakeResponse(payload={"ok": True})
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [dm_update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        # Yield control so the typing task can run at least one iteration.
        await asyncio.sleep(0)

    await plugin.start({"botToken": "123:abc"}, on_message, abort)

    assert len(typing_calls) >= 1, "sendChatAction should have been called at least once"
    assert typing_calls[0]["action"] == "typing"
    assert str(typing_calls[0]["chat_id"]) == "5"


# ---------------------------------------------------------------------------
# Outbound — HTML mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_text_uses_html_parse_mode(monkeypatch):
    plugin = TelegramChannel()
    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "expected-secret"},
        lambda _msg: None,
    )

    fake_client = _FakeClient([
        _FakeResponse(
            payload={
                "ok": True,
                "result": {"message_id": 501, "chat": {"id": -1001234567890}},
            }
        )
    ])

    async def fake_get_http_client():
        return fake_client

    monkeypatch.setattr(tg_mod, "get_http_client", fake_get_http_client)

    result = await plugin.send_text(
        OutboundContext(
            channel_id="telegram",
            account_id="default",
            to="-1001234567890:topic:9",
            text="hello world",
            reply_to_id="77",
            silent=True,
        )
    )

    assert result.success is True
    assert result.message_id == "501"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert call["json"]["chat_id"] == "-1001234567890"
    assert call["json"]["message_thread_id"] == 9
    assert call["json"]["reply_to_message_id"] == 77
    assert call["json"]["disable_notification"] is True
    assert call["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_text_falls_back_to_plain_on_html_error(monkeypatch):
    """When Telegram returns 400 (HTML parse error), retry with plain text."""
    plugin = TelegramChannel()
    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "s3cr3t"},
        lambda _msg: None,
    )

    fake_client = _FakeClient([
        _FakeResponse(
            status_code=400,
            payload={"ok": False, "description": "Bad Request: can't parse entities in text"},
        ),
        _FakeResponse(
            payload={
                "ok": True,
                "result": {"message_id": 99, "chat": {"id": 7}},
            }
        ),
    ])

    async def fake_get_http_client():
        return fake_client

    monkeypatch.setattr(tg_mod, "get_http_client", fake_get_http_client)

    result = await plugin.send_text(
        OutboundContext(channel_id="telegram", to="7", text="test")
    )

    assert result.success is True
    assert len(fake_client.calls) == 2
    assert fake_client.calls[0]["json"]["parse_mode"] == "HTML"
    assert "parse_mode" not in fake_client.calls[1]["json"]


@pytest.mark.asyncio
async def test_send_text_does_not_fallback_on_non_parse_400(monkeypatch):
    """A 400 'chat not found' must NOT trigger plain-text retry."""
    plugin = TelegramChannel()
    await plugin.start(
        {"botToken": "123:abc", "webhookSecret": "s3cr3t"},
        lambda _msg: None,
    )

    fake_client = _FakeClient([
        _FakeResponse(
            status_code=400,
            payload={"ok": False, "description": "Bad Request: chat not found"},
        ),
    ])

    async def fake_get_http_client():
        return fake_client

    monkeypatch.setattr(tg_mod, "get_http_client", fake_get_http_client)

    result = await plugin.send_text(
        OutboundContext(channel_id="telegram", to="999999", text="test")
    )

    assert result.success is False
    assert len(fake_client.calls) == 1  # No retry, just one call


# ---------------------------------------------------------------------------
# format.py — markdown_to_telegram_html
# ---------------------------------------------------------------------------

def test_format_bold():
    assert markdown_to_telegram_html("**bold**") == "<b>bold</b>"


def test_format_italic():
    assert markdown_to_telegram_html("*italic*") == "<i>italic</i>"


def test_format_bold_and_italic():
    assert markdown_to_telegram_html("***bi***") == "<b><i>bi</i></b>"


def test_format_strikethrough():
    assert markdown_to_telegram_html("~~del~~") == "<s>del</s>"


def test_format_inline_code():
    assert markdown_to_telegram_html("`code`") == "<code>code</code>"


def test_format_code_block():
    result = markdown_to_telegram_html("```python\nprint('hi')\n```")
    assert result == "<pre><code class=\"language-python\">print('hi')\n</code></pre>"


def test_format_link():
    result = markdown_to_telegram_html("[click](https://example.com)")
    assert result == '<a href="https://example.com">click</a>'


def test_format_header():
    result = markdown_to_telegram_html("## Title")
    assert result == "<b>Title</b>"


def test_format_escapes_html_in_text():
    result = markdown_to_telegram_html("a < b & c > d")
    assert "&lt;" in result
    assert "&amp;" in result
    assert "&gt;" in result


def test_format_does_not_escape_html_in_code_block():
    result = markdown_to_telegram_html("```\na < b\n```")
    assert "&lt;" in result  # escaped inside <pre><code>
    assert "<pre>" in result


def test_format_empty_string():
    assert markdown_to_telegram_html("") == ""


def test_format_no_markdown_unchanged():
    text = "Hello, world!"
    assert markdown_to_telegram_html(text) == text


# ---------------------------------------------------------------------------
# format.py — split_html_chunks
# ---------------------------------------------------------------------------

def test_split_short_text_returns_single_chunk():
    text = "hello"
    assert split_html_chunks(text) == [text]


def test_split_empty_returns_empty():
    assert split_html_chunks("") == []


def test_split_long_text_splits_at_paragraphs():
    para = "x" * 100
    long = f"{para}\n\n{para}\n\n{para}"
    chunks = split_html_chunks(long, limit=150)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 150


def test_split_oversized_line_is_force_split():
    long_line = "a" * 200
    chunks = split_html_chunks(long_line, limit=50)
    assert len(chunks) == 4
    assert all(len(c) <= 50 for c in chunks)


# ---------------------------------------------------------------------------
# inbound.py — media description
# ---------------------------------------------------------------------------

def test_extract_media_description_photo():
    msg = {"photo": [{"file_id": "x", "width": 100, "height": 100}]}
    assert extract_media_description(msg) == "[图片]"


def test_extract_media_description_photo_with_caption():
    msg = {
        "photo": [{"file_id": "x"}],
        "caption": "Look at this",
    }
    assert extract_media_description(msg) == "[图片]: Look at this"


def test_extract_media_description_document():
    msg = {"document": {"file_id": "y", "file_name": "report.pdf"}}
    assert extract_media_description(msg) == "[文件: report.pdf]"


def test_extract_media_description_voice():
    msg = {"voice": {"file_id": "z", "duration": 10}}
    assert extract_media_description(msg) == "[语音消息]"


def test_extract_media_description_sticker():
    msg = {"sticker": {"file_id": "s", "emoji": "😂"}}
    assert "贴纸" in extract_media_description(msg)
    assert "😂" in extract_media_description(msg)


def test_extract_media_description_unknown_returns_none():
    msg = {"poll": {"question": "?", "options": []}}
    assert extract_media_description(msg) is None


@pytest.mark.asyncio
async def test_build_inbound_message_photo():
    """Photo messages should produce an InboundMessage with [图片] placeholder."""
    identity = BotIdentityResolver()
    identity.seed("bot", 999)

    msg = {
        "message_id": 1,
        "from": {"id": 10, "is_bot": False, "first_name": "Alice"},
        "chat": {"id": 10, "type": "private"},
        "photo": [{"file_id": "abc", "width": 800, "height": 600}],
    }

    inbound = await build_inbound_message(msg, "default", identity, {})
    assert inbound is not None
    assert "[图片]" in inbound.text


@pytest.mark.asyncio
async def test_build_inbound_message_photo_with_caption():
    """Caption AND media-type tag must both appear in text."""
    identity = BotIdentityResolver()
    identity.seed("bot", 999)

    msg = {
        "message_id": 2,
        "from": {"id": 10, "is_bot": False, "first_name": "Alice"},
        "chat": {"id": 10, "type": "private"},
        "photo": [{"file_id": "abc"}],
        "caption": "This is the scene",
    }

    inbound = await build_inbound_message(msg, "default", identity, {})
    assert inbound is not None
    assert "[图片]" in inbound.text
    assert "This is the scene" in inbound.text


@pytest.mark.asyncio
async def test_build_inbound_message_discards_bot_messages():
    identity = BotIdentityResolver()
    msg = {
        "message_id": 3,
        "from": {"id": 999, "is_bot": True, "first_name": "OtherBot"},
        "chat": {"id": 999, "type": "private"},
        "text": "bot reply",
    }
    inbound = await build_inbound_message(msg, "default", identity, {})
    assert inbound is None


@pytest.mark.asyncio
async def test_build_inbound_message_discards_unknown_content():
    """Poll / game messages (no text, no media) must be dropped."""
    identity = BotIdentityResolver()
    msg = {
        "message_id": 4,
        "from": {"id": 10, "is_bot": False, "first_name": "Alice"},
        "chat": {"id": 10, "type": "private"},
        "poll": {"question": "Best OS?", "options": []},
    }
    inbound = await build_inbound_message(msg, "default", identity, {})
    assert inbound is None


# ---------------------------------------------------------------------------
# Pairing — allowFrom semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pairing_not_triggered_when_allow_from_missing(monkeypatch):
    """No allowFrom key → open access, pairing never fires."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    pairing_calls: list[str] = []
    dispatched: list[Any] = []
    call_count = 0

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 42, "is_bot": False, "first_name": "Bob"},
            "chat": {"id": 42, "type": "private"},
            "text": "hello",
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendMessage" in url:
                    pairing_calls.append(url)
                    return _FakeResponse(payload={"ok": True})
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(pairing_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        dispatched.append(msg)

    # No allowFrom key at all → open access
    await plugin.start({"botToken": "123:abc"}, on_message, abort)

    assert pairing_calls == [], "Pairing should not fire when allowFrom is absent"
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_pairing_triggered_when_allow_from_empty_list(monkeypatch):
    """allowFrom: [] → require pairing for everyone, even though list is empty."""
    plugin = TelegramChannel()
    abort = asyncio.Event()
    pairing_calls: list[str] = []
    dispatched: list[Any] = []
    call_count = 0

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 42, "is_bot": False, "first_name": "Bob"},
            "chat": {"id": 42, "type": "private"},
            "text": "hello",
        },
    }

    async def fake_get_http_client():
        class _Client:
            async def post(self, url: str, *, json: dict[str, Any], timeout: int) -> _FakeResponse:
                nonlocal call_count
                if "deleteWebhook" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendChatAction" in url:
                    return _FakeResponse(payload={"ok": True})
                if "sendMessage" in url:
                    pairing_calls.append(url)
                    return _FakeResponse(payload={"ok": True})
                call_count += 1
                if call_count == 1:
                    return _FakeResponse(payload={"ok": True, "result": [update]})
                abort.set()
                return _FakeResponse(payload={"ok": True, "result": []})
        return _Client()

    monkeypatch.setattr(polling_mod, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(pairing_mod, "get_http_client", fake_get_http_client)

    async def on_message(msg):
        dispatched.append(msg)

    # allowFrom key present but empty → pairing required for everyone
    await plugin.start({"botToken": "123:abc", "allowFrom": []}, on_message, abort)

    assert len(pairing_calls) == 1, "Pairing should fire when allowFrom is an empty list"
    assert dispatched == [], "Message must not be dispatched to AI before pairing"


# ------------------------------------------------------------------
# Inbound media download (getFile + download)
# ------------------------------------------------------------------

class TestTelegramInboundMedia:
    @pytest.mark.asyncio
    async def test_parse_telegram_uri(self):
        from flocks.channel.builtin.telegram import inbound_media as mod
        kind, file_id = mod._parse_telegram_uri("telegram://photo/AgAD-file")
        assert kind == "photo"
        assert file_id == "AgAD-file"

    @pytest.mark.asyncio
    async def test_invalid_uri_returns_none(self):
        from flocks.channel.builtin.telegram import inbound_media as mod
        kind, file_id = mod._parse_telegram_uri("https://example.com/x.png")
        assert kind is None
        assert file_id is None

    @pytest.mark.asyncio
    async def test_get_file_then_download(self, monkeypatch, tmp_path):
        from flocks.channel.builtin.telegram import inbound_media as mod

        async def fake_get_file_path(*, bot_token, api_base, file_id, timeout):
            assert api_base == "https://api.telegram.org/bot123:abc"
            return "documents/file_42.pdf", file_id

        async def fake_download_file(*, download_base, file_path, max_bytes, timeout):
            assert download_base == "https://api.telegram.org/file/bot123:abc"
            return b"%PDF-1.4 hello"

        monkeypatch.setattr(mod, "_get_file_path", fake_get_file_path)
        monkeypatch.setattr(mod, "_download_file", fake_download_file)
        monkeypatch.setattr(mod, "_media_storage_dir", lambda _acc: tmp_path)

        from flocks.channel.base import ChatType, InboundMessage
        media = await mod.download_inbound_media(
            InboundMessage(
                channel_id="telegram",
                account_id="acc1",
                message_id="m1",
                sender_id="u1",
                chat_id="c1",
                chat_type=ChatType.DIRECT,
                media_url="telegram://document/ABC",
            ),
            config={"botToken": "123:abc"},
            max_bytes=1024,
        )
        assert media is not None
        assert media.filename == "file_42.pdf"
        assert media.mime == "application/pdf"
        assert Path(media.url.removeprefix("file://")).read_bytes() == b"%PDF-1.4 hello"
        assert media.source["file_id"] == "ABC"
        assert media.source["kind"] == "document"

    @pytest.mark.asyncio
    async def test_too_large_returns_none(self, monkeypatch, tmp_path):
        from flocks.channel.builtin.telegram import inbound_media as mod

        async def fake_get_file_path(*, bot_token, api_base, file_id, timeout):
            return "documents/big.bin", file_id

        async def fake_download_file(*, download_base, file_path, max_bytes, timeout):
            raise mod.TelegramInboundMediaTooLarge("too large")

        monkeypatch.setattr(mod, "_get_file_path", fake_get_file_path)
        monkeypatch.setattr(mod, "_download_file", fake_download_file)
        monkeypatch.setattr(mod, "_media_storage_dir", lambda _acc: tmp_path)

        from flocks.channel.base import ChatType, InboundMessage
        media = await mod.download_inbound_media(
            InboundMessage(
                channel_id="telegram", account_id="acc1",
                message_id="m2", sender_id="u1", chat_id="c1",
                chat_type=ChatType.DIRECT,
                media_url="telegram://document/BIG",
            ),
            config={"botToken": "123:abc"},
        )
        assert media is None
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_no_token_returns_none(self, monkeypatch, tmp_path):
        from flocks.channel.builtin.telegram import inbound_media as mod
        from flocks.channel.base import ChatType, InboundMessage
        media = await mod.download_inbound_media(
            InboundMessage(
                channel_id="telegram", account_id="acc1",
                message_id="m3", sender_id="u1", chat_id="c1",
                chat_type=ChatType.DIRECT,
                media_url="telegram://photo/X",
            ),
            config={},
        )
        assert media is None

    @pytest.mark.asyncio
    async def test_download_uses_configured_api_root_and_preserves_nested_filename(
        self, monkeypatch, tmp_path,
    ):
        from flocks.channel.builtin.telegram import inbound_media as mod
        from flocks.channel.base import ChatType, InboundMessage

        async def fake_get_file_path(*, bot_token, api_base, file_id, timeout):
            assert api_base == "https://tg-proxy.example/bot123:abc"
            return "documents/file_42.pdf", file_id

        async def fake_download_file(*, download_base, file_path, max_bytes, timeout):
            assert download_base == "https://tg-proxy.example/file/bot123:abc"
            assert file_path == "documents/file_42.pdf"
            return b"%PDF-1.4 proxy"

        monkeypatch.setattr(mod, "_get_file_path", fake_get_file_path)
        monkeypatch.setattr(mod, "_download_file", fake_download_file)
        monkeypatch.setattr(mod, "_media_storage_dir", lambda _acc: tmp_path)

        media = await mod.download_inbound_media(
            InboundMessage(
                channel_id="telegram",
                account_id="acc1",
                message_id="m4",
                sender_id="u1",
                chat_id="c1",
                chat_type=ChatType.DIRECT,
                media_url="telegram://document/ABC",
                raw={"document": {"file_name": "原始报告.pdf"}},
            ),
            config={
                "accounts": {
                    "acc1": {
                        "botToken": "123:abc",
                        "apiRoot": "https://tg-proxy.example",
                    },
                },
            },
        )
        assert media is not None
        assert media.filename.endswith(".pdf")
        assert media.filename != "file_42.pdf"

    @pytest.mark.asyncio
    async def test_download_supports_legacy_api_base_with_token(
        self, monkeypatch, tmp_path,
    ):
        from flocks.channel.builtin.telegram import inbound_media as mod
        from flocks.channel.base import ChatType, InboundMessage

        async def fake_get_file_path(*, bot_token, api_base, file_id, timeout):
            assert api_base == "https://legacy.example/bot123:abc"
            return "documents/file_7.pdf", file_id

        async def fake_download_file(*, download_base, file_path, max_bytes, timeout):
            assert download_base == "https://legacy.example/file/bot123:abc"
            return b"legacy"

        monkeypatch.setattr(mod, "_get_file_path", fake_get_file_path)
        monkeypatch.setattr(mod, "_download_file", fake_download_file)
        monkeypatch.setattr(mod, "_media_storage_dir", lambda _acc: tmp_path)

        media = await mod.download_inbound_media(
            InboundMessage(
                channel_id="telegram",
                account_id="default",
                message_id="m5",
                sender_id="u1",
                chat_id="c1",
                chat_type=ChatType.DIRECT,
                media_url="telegram://document/ABC",
            ),
            config={"botToken": "123:abc", "apiBase": "https://legacy.example/bot123:abc"},
        )
        assert media is not None


# ------------------------------------------------------------------
# Outbound media (prepare + send_media)
# ------------------------------------------------------------------

class TestTelegramSendMedia:
    @pytest.mark.asyncio
    async def test_prepare_local_image(self, tmp_path):
        from flocks.channel.builtin.telegram.media import (
            prepare_telegram_media,
        )
        path = tmp_path / "photo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
        prepared = await prepare_telegram_media(path.as_uri())
        assert prepared.kind == "photo"
        assert prepared.filename == "photo.png"
        assert prepared.data == b"\x89PNG\r\n\x1a\nDATA"

    @pytest.mark.asyncio
    async def test_prepare_local_pdf_uses_document(self, tmp_path):
        from flocks.channel.builtin.telegram.media import (
            prepare_telegram_media,
        )
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.4 data")
        prepared = await prepare_telegram_media(path.as_uri())
        assert prepared.kind == "document"
        assert prepared.mime == "application/pdf"

    @pytest.mark.asyncio
    async def test_prepare_gif_uses_animation(self, tmp_path):
        from flocks.channel.builtin.telegram.media import (
            prepare_telegram_media,
        )
        path = tmp_path / "anim.gif"
        path.write_bytes(b"GIF89a-data")
        prepared = await prepare_telegram_media(path.as_uri())
        assert prepared.kind == "animation"

    @pytest.mark.asyncio
    async def test_prepare_ogg_uses_voice(self, tmp_path):
        from flocks.channel.builtin.telegram.media import (
            prepare_telegram_media,
        )
        path = tmp_path / "clip.ogg"
        path.write_bytes(b"OggS-data")
        prepared = await prepare_telegram_media(path.as_uri())
        assert prepared.kind == "voice"

    @pytest.mark.asyncio
    async def test_kind_override(self, tmp_path):
        from flocks.channel.builtin.telegram.media import (
            prepare_telegram_media,
        )
        path = tmp_path / "img.png"
        path.write_bytes(b"\x89PNG")
        prepared = await prepare_telegram_media(
            path.as_uri(), kind_override="document",
        )
        assert prepared.kind == "document"

    @pytest.mark.asyncio
    async def test_send_media_routes_to_send_photo(self, tmp_path, monkeypatch):
        from flocks.channel.builtin.telegram.channel import TelegramChannel
        from flocks.channel.builtin.telegram import media as media_mod

        path = tmp_path / "photo.jpg"
        path.write_bytes(b"\xff\xd8\xff-data")

        ch = TelegramChannel()
        ch._config = {"botToken": "123:abc"}

        async def fake_prepare(media_url, *, kind_override=None, **kwargs):
            return media_mod.PreparedTelegramMedia(
                data=b"\xff\xd8\xff-data", filename="photo.jpg",
                mime="image/jpeg", kind="photo",
            )

        posted = []

        class FakeResponse:
            status_code = 200
            is_success = True
            def json(self):
                return {"ok": True, "result": {"message_id": "111", "chat": {"id": "c1"}}}

        class FakeClient:
            async def post(self, url, *, data, files, timeout):
                posted.append({"url": url, "data": data, "files": files})
                return FakeResponse()

        from unittest.mock import AsyncMock
        fake_client = FakeClient()
        monkeypatch.setattr(
            "flocks.channel.builtin.telegram.channel.get_http_client",
            AsyncMock(return_value=fake_client),
        )
        monkeypatch.setattr(media_mod, "prepare_telegram_media", fake_prepare)

        result = await ch.send_media(
            OutboundContext(
                channel_id="telegram", to="c1",
                text="look", media_url=path.as_uri(),
            )
        )
        assert result.success is True
        assert result.message_id == "111"
        assert posted[0]["url"].endswith("/sendPhoto")
        assert "photo" in posted[0]["files"]
        assert posted[0]["files"]["photo"][0] == "photo.jpg"
        assert posted[0]["data"]["caption"] == "look"

    @pytest.mark.asyncio
    async def test_send_media_routes_to_send_document_for_pdf(
        self, tmp_path, monkeypatch,
    ):
        from flocks.channel.builtin.telegram.channel import TelegramChannel
        from flocks.channel.builtin.telegram import media as media_mod

        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-data")

        ch = TelegramChannel()
        ch._config = {"botToken": "123:abc"}

        async def fake_prepare(media_url, *, kind_override=None, **kwargs):
            return media_mod.PreparedTelegramMedia(
                data=b"%PDF-data", filename="doc.pdf",
                mime="application/pdf", kind="document",
            )

        posted = []

        class FakeResponse:
            status_code = 200
            is_success = True
            def json(self):
                return {"ok": True, "result": {"message_id": "222", "chat": {"id": "c1"}}}

        class FakeClient:
            async def post(self, url, *, data, files, timeout):
                posted.append({"url": url, "data": data, "files": files})
                return FakeResponse()

        from unittest.mock import AsyncMock
        fake_client = FakeClient()
        monkeypatch.setattr(
            "flocks.channel.builtin.telegram.channel.get_http_client",
            AsyncMock(return_value=fake_client),
        )
        monkeypatch.setattr(media_mod, "prepare_telegram_media", fake_prepare)

        result = await ch.send_media(
            OutboundContext(
                channel_id="telegram", to="c1",
                media_url=path.as_uri(),
            )
        )
        assert result.success is True
        assert posted[0]["url"].endswith("/sendDocument")
        assert posted[0]["files"]["document"][0] == "doc.pdf"

    @pytest.mark.asyncio
    async def test_send_media_kind_override_prefix(
        self, tmp_path, monkeypatch,
    ):
        from flocks.channel.builtin.telegram.channel import TelegramChannel
        from flocks.channel.builtin.telegram import media as media_mod

        path = tmp_path / "img.png"
        path.write_bytes(b"x")

        ch = TelegramChannel()
        ch._config = {"botToken": "123:abc"}

        captured = {}

        async def fake_prepare(media_url, *, kind_override=None, **kwargs):
            captured["kind"] = kind_override
            captured["source"] = media_url
            return media_mod.PreparedTelegramMedia(
                data=b"x", filename="img.png", mime="image/png",
                kind=kind_override or "photo",
            )

        class FakeResponse:
            status_code = 200
            is_success = True
            def json(self):
                return {"ok": True, "result": {"message_id": "333", "chat": {"id": "c1"}}}

        class FakeClient:
            async def post(self, url, *, data, files, timeout):
                return FakeResponse()

        from unittest.mock import AsyncMock
        fake_client = FakeClient()
        monkeypatch.setattr(
            "flocks.channel.builtin.telegram.channel.get_http_client",
            AsyncMock(return_value=fake_client),
        )
        monkeypatch.setattr(media_mod, "prepare_telegram_media", fake_prepare)

        result = await ch.send_media(
            OutboundContext(
                channel_id="telegram", to="c1",
                media_url=f"telegram:document:{path.as_uri()}",
            )
        )
        assert result.success is True
        assert captured["kind"] == "document"
        # The prefix should be stripped before passing to the preparer.
        assert captured["source"] == path.as_uri()

    @pytest.mark.asyncio
    async def test_send_media_api_error_returns_failure(
        self, tmp_path, monkeypatch,
    ):
        from flocks.channel.builtin.telegram.channel import TelegramChannel
        from flocks.channel.builtin.telegram import media as media_mod

        path = tmp_path / "doc.pdf"
        path.write_bytes(b"x")

        ch = TelegramChannel()
        ch._config = {"botToken": "123:abc"}

        async def fake_prepare(media_url, *, kind_override=None, **kwargs):
            return media_mod.PreparedTelegramMedia(
                data=b"x", filename="doc.pdf",
                mime="application/pdf", kind="document",
            )

        class FakeResponse:
            status_code = 400
            is_success = False
            def json(self):
                return {"ok": False, "description": "bad chat id"}

        class FakeClient:
            async def post(self, url, *, data, files, timeout):
                return FakeResponse()

        from unittest.mock import AsyncMock
        fake_client = FakeClient()
        monkeypatch.setattr(
            "flocks.channel.builtin.telegram.channel.get_http_client",
            AsyncMock(return_value=fake_client),
        )
        monkeypatch.setattr(media_mod, "prepare_telegram_media", fake_prepare)

        result = await ch.send_media(
            OutboundContext(
                channel_id="telegram", to="c1",
                media_url=path.as_uri(),
            )
        )
        assert result.success is False
        assert "bad chat id" in result.error

    @pytest.mark.asyncio
    async def test_send_media_missing_media_url_falls_back_to_text(
        self, monkeypatch,
    ):
        from flocks.channel.builtin.telegram.channel import TelegramChannel

        ch = TelegramChannel()
        ch._config = {"botToken": "123:abc"}

        async def fake_send_text(self, ctx):
            from flocks.channel.base import DeliveryResult
            return DeliveryResult(
                channel_id="telegram", message_id="t1", success=True,
            )

        monkeypatch.setattr(
            TelegramChannel, "send_text", fake_send_text,
        )

        result = await ch.send_media(
            OutboundContext(channel_id="telegram", to="c1", text="hi"),
        )
        assert result.success is True
        assert result.message_id == "t1"
