from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import ChatType, OutboundContext
import flocks.channel.builtin.slack.channel as slack_mod
from flocks.channel.builtin.slack.channel import SlackChannel
from flocks.channel.builtin.slack.format import markdown_to_slack_mrkdwn
from flocks.channel.builtin.slack.inbound import build_inbound_message


def test_plugin_exports_slack_channel():
    plugin = SlackChannel()
    assert plugin.meta().id == "slack"
    assert plugin.meta().label == "Slack"
    assert "sl" in plugin.meta().aliases


def test_validate_config_requires_tokens(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", True)
    plugin = SlackChannel()

    assert "botToken" in (plugin.validate_config({}) or "")
    assert "appToken" in (plugin.validate_config({"botToken": "xoxb-1"}) or "")
    assert plugin.validate_config({"botToken": "xoxb-1", "appToken": "xapp-1"}) is None


def test_validate_config_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", False)
    plugin = SlackChannel()

    assert "slack-bolt" in (plugin.validate_config({"botToken": "x", "appToken": "y"}) or "")


def test_markdown_to_slack_mrkdwn_preserves_code_and_links():
    text = "# Title\n\n**bold** and [OpenAI](https://openai.com)\n\n```py\n**not bold**\n```"

    rendered = markdown_to_slack_mrkdwn(text)

    assert "*Title*" in rendered
    assert "*bold*" in rendered
    assert "<https://openai.com|OpenAI>" in rendered
    assert "```py\n**not bold**\n```" in rendered


def test_build_inbound_direct_message():
    msg = build_inbound_message(
        {
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "ts": "171.1",
            "text": "hello",
            "team": "T1",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert msg.channel_id == "slack"
    assert msg.account_id == "T1"
    assert msg.chat_type == ChatType.DIRECT
    assert msg.chat_id == "D123"
    assert msg.message_id == "171.1"
    assert msg.text == "hello"
    assert msg.mentioned is False
    assert msg.mention_text == ""


def test_build_inbound_group_mention_strips_bot_mention():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.2",
            "text": "<@UBOT> summarize this",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert msg.chat_type == ChatType.CHANNEL
    assert msg.mentioned is True
    assert msg.mention_text == "summarize this"


def test_build_inbound_group_dm_uses_group_chat_type():
    msg = build_inbound_message(
        {
            "channel": "G123",
            "channel_type": "mpim",
            "user": "U123",
            "ts": "171.21",
            "text": "<@UBOT> summarize this",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert msg.chat_type == ChatType.GROUP
    assert msg.mentioned is True


def test_build_inbound_thread_reply_to_known_bot_thread_triggers_without_mention():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.3",
            "thread_ts": "171.2",
            "text": "continue",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids={"171.2"},
    )

    assert msg is not None
    assert msg.mentioned is True
    assert msg.mention_text == "continue"
    assert msg.thread_id == "171.2"


def test_build_inbound_thread_reply_to_parent_bot_user_triggers_after_restart():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.4",
            "thread_ts": "171.2",
            "parent_user_id": "UBOT",
            "text": "continue after restart",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert msg.mentioned is True
    assert msg.mention_text == "continue after restart"
    assert msg.thread_id == "171.2"


def test_build_inbound_allowed_bot_message_without_user_uses_bot_id():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "bot_id": "B_OTHER",
            "subtype": "bot_message",
            "ts": "171.5",
            "text": "automation says hi",
        },
        bot_user_id="UBOT",
        config={"allowBots": "all"},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert msg.sender_id == "B_OTHER"
    assert msg.text == "automation says hi"


def test_build_inbound_bot_message_mentions_policy_requires_bot_mention():
    blocked = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "bot_id": "B_OTHER",
            "subtype": "bot_message",
            "ts": "171.6",
            "text": "automation says hi",
        },
        bot_user_id="UBOT",
        config={"allowBots": "mentions"},
        known_thread_ids=set(),
    )
    allowed = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "bot_id": "B_OTHER",
            "subtype": "bot_message",
            "ts": "171.7",
            "text": "<@UBOT> automation says hi",
        },
        bot_user_id="UBOT",
        config={"allowBots": "mentions"},
        known_thread_ids=set(),
    )

    assert blocked is None
    assert allowed is not None
    assert allowed.sender_id == "B_OTHER"
    assert allowed.mention_text == "automation says hi"


def test_build_inbound_ignores_own_and_edit_messages():
    own = build_inbound_message(
        {"channel": "C1", "user": "UBOT", "ts": "1", "text": "echo"},
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )
    edited = build_inbound_message(
        {"channel": "C1", "user": "U1", "ts": "2", "text": "x", "subtype": "message_changed"},
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert own is None
    assert edited is None


@pytest.mark.asyncio
async def test_send_text_posts_thread_reply_and_remembers_thread():
    plugin = SlackChannel()
    plugin._config = {"replyInThread": True, "replyBroadcast": True}
    fake_client = SimpleNamespace(
        chat_postMessage=AsyncMock(return_value={"ts": "172.1"})
    )
    plugin._app = SimpleNamespace(client=fake_client)

    result = await plugin.send_text(
        OutboundContext(
            channel_id="slack",
            to="slack:C123",
            text="hello",
            reply_to_id="171.1",
        )
    )

    assert result.success is True
    assert result.message_id == "172.1"
    fake_client.chat_postMessage.assert_awaited_once_with(
        channel="C123",
        text="hello",
        mrkdwn=True,
        thread_ts="171.1",
        reply_broadcast=True,
    )
    assert "171.1" in plugin._known_thread_ids
    assert "172.1" in plugin._known_thread_ids


@pytest.mark.asyncio
async def test_connect_socket_mode_marks_connected_after_connect(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()

    class FakeHandler:
        def __init__(self, app, token):
            self.app = app
            self.token = token
            self.connected = False

        async def connect_async(self):
            self.connected = True

        async def close_async(self):
            pass

    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    await plugin._connect_socket_mode("xapp-ok")

    assert plugin.status.connected is True
    assert plugin._handler.connected is True


@pytest.mark.asyncio
async def test_connect_socket_mode_failure_marks_disconnected(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()

    class FakeHandler:
        def __init__(self, app, token):
            pass

        async def connect_async(self):
            raise RuntimeError("invalid app token")

    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    with pytest.raises(RuntimeError):
        await plugin._connect_socket_mode("xapp-bad")

    assert plugin.status.connected is False
    assert plugin.status.last_error == "invalid app token"


@pytest.mark.asyncio
async def test_connect_socket_mode_timeout_marks_disconnected(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()

    class FakeHandler:
        def __init__(self, app, token):
            pass

        async def connect_async(self):
            await asyncio.sleep(2)

    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    with pytest.raises(TimeoutError):
        await plugin._connect_socket_mode("xapp-slow")

    assert plugin.status.connected is False
    assert "timed out" in (plugin.status.last_error or "")


@pytest.mark.asyncio
async def test_handle_slack_event_dispatches_inbound_message():
    plugin = SlackChannel()
    plugin._config = {}
    plugin._bot_user_id = "UBOT"
    dispatched = []

    async def on_message(msg):
        dispatched.append(msg)

    plugin._on_message = on_message

    await plugin._handle_slack_event(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.2",
            "text": "<@UBOT> ping",
        }
    )

    assert len(dispatched) == 1
    assert dispatched[0].mention_text == "ping"
