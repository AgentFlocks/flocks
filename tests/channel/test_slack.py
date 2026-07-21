from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import ChatType, NonRetryableChannelError, OutboundContext
import flocks.channel.builtin.slack.channel as slack_mod
from flocks.channel.builtin.slack.channel import SlackChannel
from flocks.channel.builtin.slack.format import markdown_to_slack_mrkdwn
from flocks.channel.builtin.slack.inbound import build_inbound_message, slack_thread_cache_key
from flocks.channel.builtin.slack.manifest import build_slack_app_manifest


def test_plugin_exports_slack_channel():
    plugin = SlackChannel()
    assert plugin.meta().id == "slack"
    assert plugin.meta().label == "Slack"
    assert "sl" in plugin.meta().aliases
    assert plugin.capabilities().media is False
    assert plugin.capabilities().rich_text is True
    assert plugin.capabilities().self_managed_connection is True


def test_slack_manifest_matches_socket_mode_setup_needs():
    manifest = build_slack_app_manifest()

    assert manifest["settings"]["socket_mode_enabled"] is True
    scopes = set(manifest["oauth_config"]["scopes"]["bot"])
    assert {
        "app_mentions:read",
        "channels:history",
        "channels:read",
        "chat:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "users:read",
    }.issubset(scopes)
    events = set(manifest["settings"]["event_subscriptions"]["bot_events"])
    assert {
        "app_mention",
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
    }.issubset(events)


def test_validate_config_requires_tokens(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", True)
    plugin = SlackChannel()

    assert "botToken" in (plugin.validate_config({}) or "")
    assert "appToken" in (plugin.validate_config({"botToken": "xoxb-1"}) or "")
    assert plugin.validate_config({"botToken": "xoxb-1", "appToken": "xapp-1"}) is None


def test_validate_config_rejects_non_slack_token_prefixes(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", True)
    plugin = SlackChannel()

    assert "xoxb-" in (
        plugin.validate_config({"botToken": "xoxp-user", "appToken": "xapp-1"}) or ""
    )
    assert "xapp-" in (
        plugin.validate_config({"botToken": "xoxb-1", "appToken": "xoxb-wrong"}) or ""
    )
    assert plugin.validate_config(
        {"botToken": "{secret:slack_bot}", "appToken": "{secret:slack_app}"}
    ) is None


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


def test_markdown_to_slack_mrkdwn_keeps_escaped_special_mentions():
    rendered = markdown_to_slack_mrkdwn("safe &lt;!channel&gt; and &amp;")

    assert "&lt;!channel&gt;" in rendered
    assert "<!channel>" not in rendered
    assert "&amp;" in rendered


def test_markdown_to_slack_mrkdwn_escapes_raw_slack_special_mentions():
    rendered = markdown_to_slack_mrkdwn(
        "notify <!here> <!channel> <!everyone> <@U123>; compare 1 < 2 & 3 > 2"
    )

    assert "<!here>" not in rendered
    assert "<!channel>" not in rendered
    assert "<!everyone>" not in rendered
    assert "<@U123>" not in rendered
    assert "&lt;!here&gt;" in rendered
    assert "&lt;!channel&gt;" in rendered
    assert "&lt;!everyone&gt;" in rendered
    assert "&lt;@U123&gt;" in rendered
    assert "1 &lt; 2 &amp; 3 &gt; 2" in rendered


def test_markdown_to_slack_mrkdwn_escapes_unsafe_link_url_delimiters():
    rendered = markdown_to_slack_mrkdwn("[x](https://example.com/a|<!here>)")

    assert rendered == "<https://example.com/a%7C%3C!here%3E|x>"
    assert "|<!here>" not in rendered


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


def test_build_inbound_extracts_rich_text_blocks():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.22",
            "blocks": [
                {
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "user", "user_id": "UBOT"},
                                {"type": "text", "text": " summarize "},
                                {"type": "link", "text": "this", "url": "https://example.com"},
                            ],
                        }
                    ],
                }
            ],
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert "<@UBOT> summarize this (https://example.com)" in msg.text
    assert msg.mentioned is True
    assert "summarize this" in msg.mention_text


def test_build_inbound_merges_blocks_when_plain_text_exists():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.24",
            "text": "<@UBOT> please inspect",
            "blocks": [
                {
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "user", "user_id": "UBOT"},
                                {"type": "text", "text": " please inspect"},
                            ],
                        },
                        {
                            "type": "rich_text_quote",
                            "elements": [
                                {
                                    "type": "rich_text_section",
                                    "elements": [
                                        {"type": "text", "text": "quoted outage context"},
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ],
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert "please inspect" in msg.text
    assert "quoted outage context" in msg.text
    assert "quoted outage context" in msg.mention_text


def test_build_inbound_appends_attachments_and_file_names():
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.23",
            "text": "<@UBOT> review alert",
            "attachments": [
                {
                    "title": "Alert context",
                    "fields": [
                        {"title": "Severity", "value": "high"},
                    ],
                }
            ],
            "files": [
                {"name": "screenshot.png"},
            ],
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(),
    )

    assert msg is not None
    assert "review alert" in msg.text
    assert "Alert context" in msg.text
    assert "Severity: high" in msg.text
    assert "[Slack files: screenshot.png]" in msg.text


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
        known_thread_ids={slack_thread_cache_key("default", "C123", "171.2")},
    )

    assert msg is not None
    assert msg.mentioned is True
    assert msg.mention_text == "continue"
    assert msg.thread_id == "171.2"


def test_build_inbound_same_thread_ts_in_other_channel_does_not_trigger():
    msg = build_inbound_message(
        {
            "channel": "C999",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.3",
            "thread_ts": "171.2",
            "text": "same timestamp, different channel",
            "team": "T1",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids={slack_thread_cache_key("T1", "C123", "171.2")},
    )

    assert msg is not None
    assert msg.mentioned is False


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
    assert slack_thread_cache_key("default", "C123", "171.1") in plugin._known_thread_ids
    assert slack_thread_cache_key("default", "C123", "172.1") not in plugin._known_thread_ids


@pytest.mark.asyncio
async def test_known_thread_roots_are_persisted_and_restored(monkeypatch):
    stored: dict[str, list[str]] = {}

    class FakeStorage:
        @staticmethod
        async def get(key):
            return stored.get(key)

        @staticmethod
        async def set(key, value, value_type="json"):
            stored[key] = value

    monkeypatch.setattr(slack_mod, "Storage", FakeStorage)

    plugin = SlackChannel()
    plugin._remember_thread("T1", "C123", "171.2")
    await plugin._persist_known_threads()

    restarted = SlackChannel()
    await restarted._load_known_threads()
    msg = build_inbound_message(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "171.3",
            "thread_ts": "171.2",
            "parent_user_id": "U123",
            "text": "continue after process restart",
            "team": "T1",
        },
        bot_user_id="UBOT",
        config={},
        known_thread_ids=set(restarted._known_thread_ids.keys()),
    )

    assert msg is not None
    assert msg.mentioned is True
    assert msg.mention_text == "continue after process restart"


@pytest.mark.asyncio
async def test_connect_socket_mode_marks_connected_after_connect(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()
    plugin._verify_app_token = AsyncMock()

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
    plugin._verify_app_token = AsyncMock()

    class FakeHandler:
        def __init__(self, app, token):
            pass

        async def connect_async(self):
            raise RuntimeError("invalid app token")

    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    with pytest.raises(NonRetryableChannelError):
        await plugin._connect_socket_mode("xapp-bad")

    assert plugin.status.connected is False
    assert "Slack App Token" in (plugin.status.last_error or "")
    assert "xapp-" in (plugin.status.last_error or "")


@pytest.mark.asyncio
async def test_connect_socket_mode_slack_response_error_is_actionable(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()
    plugin._verify_app_token = AsyncMock()

    class FakeSlackError(Exception):
        def __init__(self):
            super().__init__("Slack API error")
            self.response = SimpleNamespace(data={"error": "missing_scope"})

    class FakeHandler:
        def __init__(self, app, token):
            pass

        async def connect_async(self):
            raise FakeSlackError()

    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    with pytest.raises(NonRetryableChannelError):
        await plugin._connect_socket_mode("xapp-missing-scope")

    assert "connections:write" in (plugin.status.last_error or "")


@pytest.mark.asyncio
async def test_connect_socket_mode_timeout_marks_disconnected(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()
    plugin._verify_app_token = AsyncMock()

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
async def test_connect_socket_mode_preflights_app_token_before_handler(monkeypatch):
    plugin = SlackChannel()
    plugin._config = {"socketConnectTimeoutSeconds": 1}
    plugin._app = SimpleNamespace()

    class FakeSlackError(Exception):
        def __init__(self):
            super().__init__("Slack API error")
            self.response = SimpleNamespace(data={"error": "invalid_auth"})

    class FakeWebClient:
        def __init__(self, token):
            self.token = token

        async def apps_connections_open(self, *, app_token):
            assert app_token == "xapp-bad"
            raise FakeSlackError()

    class FakeHandler:
        def __init__(self, app, token):
            raise AssertionError("handler should not start for invalid app token")

    monkeypatch.setattr(slack_mod, "AsyncWebClient", FakeWebClient)
    monkeypatch.setattr(slack_mod, "AsyncSocketModeHandler", FakeHandler)

    with pytest.raises(NonRetryableChannelError):
        await plugin._connect_socket_mode("xapp-bad")

    assert "Slack App Token" in (plugin.status.last_error or "")
    assert "xapp-" in (plugin.status.last_error or "")


@pytest.mark.asyncio
async def test_start_invalid_config_raises_non_retryable(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", True)
    plugin = SlackChannel()

    with pytest.raises(NonRetryableChannelError):
        await plugin.start({}, AsyncMock())

    assert plugin.status.connected is False
    assert "botToken" in (plugin.status.last_error or "")


@pytest.mark.asyncio
async def test_start_rejects_user_token_auth_identity(monkeypatch):
    monkeypatch.setattr(slack_mod, "SLACK_AVAILABLE", True)

    class FakeClient:
        async def auth_test(self):
            return {
                "user_id": "U_HUMAN",
                "team_id": "T1",
            }

    class FakeApp:
        def __init__(self, token):
            self.token = token
            self.client = FakeClient()

    monkeypatch.setattr(slack_mod, "AsyncApp", FakeApp)
    plugin = SlackChannel()

    with pytest.raises(NonRetryableChannelError):
        await plugin.start({"botToken": "xoxb-looks-valid", "appToken": "xapp-1"}, AsyncMock())

    assert plugin.status.connected is False
    assert "Bot User OAuth Token" in (plugin.status.last_error or "")
    assert "xoxp-" in (plugin.status.last_error or "")


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
