from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import flocks.channel.builtin.teams.channel as teams_mod
from flocks.channel.base import ChatType, OutboundContext
from flocks.channel.builtin.teams import TeamsChannel


def _teams_config() -> dict[str, str]:
    return {
        "clientId": "client-id",
        "clientSecret": "client-secret",
        "tenantId": "tenant-id",
    }


def test_plugin_exports_teams_channel() -> None:
    plugin = TeamsChannel()

    assert plugin.meta().id == "teams"
    assert plugin.meta().label == "Microsoft Teams"
    assert plugin.capabilities().threads is True
    assert ChatType.CHANNEL in plugin.capabilities().chat_types


def test_validate_config_requires_microsoft_credentials() -> None:
    plugin = TeamsChannel()

    error = plugin.validate_config({})

    assert error is not None
    assert "clientId" in error
    assert "clientSecret" in error
    assert "tenantId" in error


def test_validate_config_accepts_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = TeamsChannel()
    monkeypatch.setenv("TEAMS_CLIENT_ID", "env-client")
    monkeypatch.setenv("TEAMS_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("TEAMS_TENANT_ID", "env-tenant")

    assert plugin.validate_config({}) is None


@pytest.mark.asyncio
async def test_start_initializes_sdk_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    initialized: dict[str, Any] = {}

    async def fake_route_handler(_request: Any) -> dict[str, Any]:
        return {"status": 200, "body": {"ok": True}}

    class FakeApp:
        id = "bot-id"

        def __init__(self, **kwargs: Any) -> None:
            initialized.update(kwargs)
            self._bridge = kwargs["http_server_adapter"]

        def on_message(self, fn: Any) -> Any:
            self.message_handler = fn
            return fn

        async def initialize(self) -> None:
            self._bridge.register_route("POST", "/api/messages", fake_route_handler)

    monkeypatch.setattr(teams_mod, "TEAMS_SDK_AVAILABLE", True)
    monkeypatch.setattr(teams_mod, "App", FakeApp)
    monkeypatch.setattr(teams_mod, "ClientOptions", lambda **kwargs: SimpleNamespace(**kwargs))

    plugin = TeamsChannel()
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    await plugin.start(_teams_config(), on_message)

    assert initialized["client_id"] == "client-id"
    assert initialized["client_secret"] == "client-secret"
    assert initialized["tenant_id"] == "tenant-id"
    assert plugin.status.connected is True
    assert dispatched == []


@pytest.mark.asyncio
async def test_handle_webhook_invokes_sdk_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_handler(request: Any) -> dict[str, Any]:
        captured["request"] = request
        return {"status": 202, "body": {"accepted": True}}

    monkeypatch.setattr(
        teams_mod,
        "HttpRequest",
        lambda *, body, headers: {"body": body, "headers": headers},
    )

    plugin = TeamsChannel()
    plugin._bridge = SimpleNamespace(handler=fake_handler)

    result = await plugin.handle_webhook(
        json.dumps({"type": "message", "text": "hi"}).encode("utf-8"),
        {"authorization": "Bearer token"},
    )

    assert result == {"accepted": True, "status_code": 202}
    assert captured["request"]["body"]["text"] == "hi"
    assert captured["request"]["headers"]["authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_json() -> None:
    plugin = TeamsChannel()
    plugin._bridge = SimpleNamespace(handler=lambda _request: None)

    result = await plugin.handle_webhook(b"{not-json", {})

    assert result == {"error": "invalid json", "status_code": 400}


@pytest.mark.asyncio
async def test_message_activity_dispatches_channel_mention_and_attachment() -> None:
    plugin = TeamsChannel()
    plugin._app = SimpleNamespace(id="bot-id")
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    plugin._config = _teams_config()
    plugin._on_message = on_message

    activity = SimpleNamespace(
        id="activity-1",
        text="<at>Flocks</at> investigate &amp; report",
        service_url="https://service.example/teams",
        conversation=SimpleNamespace(id="conversation-1", conversation_type="channel"),
        from_=SimpleNamespace(id="user-id", aad_object_id="aad-id", name="Alice"),
        reply_to_id="root-activity",
        attachments=[
            SimpleNamespace(
                name="evidence.txt",
                content_url="https://files.example/evidence.txt",
                content_type="text/plain",
            )
        ],
    )

    await plugin._on_message_activity(SimpleNamespace(activity=activity))

    assert len(dispatched) == 1
    msg = dispatched[0]
    assert msg.channel_id == "teams"
    assert msg.account_id == "default"
    assert msg.message_id == "conversation-1:activity-1"
    assert msg.sender_id == "aad-id"
    assert msg.sender_name == "Alice"
    assert msg.chat_id == "conversation-1"
    assert msg.chat_type == ChatType.CHANNEL
    assert msg.text == "investigate & report\n\n[Teams attachment: evidence.txt]"
    assert msg.media_url == "https://files.example/evidence.txt"
    assert msg.media_mime == "text/plain"
    assert msg.reply_to_id == "root-activity"
    assert msg.thread_id == "root-activity"
    assert msg.mentioned is True
    assert msg.mention_text == "investigate & report\n\n[Teams attachment: evidence.txt]"
    assert plugin._conversation_service_urls["conversation-1"] == "https://service.example/teams"


@pytest.mark.asyncio
async def test_message_activity_entities_must_mention_bot() -> None:
    plugin = TeamsChannel()
    plugin._app = SimpleNamespace(id="bot-id")
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    plugin._config = _teams_config()
    plugin._on_message = on_message
    activity = SimpleNamespace(
        id="activity-1",
        text="<at>Other</at> hello",
        conversation=SimpleNamespace(id="conversation-1", conversation_type="channel"),
        from_=SimpleNamespace(id="user-id", name="Alice"),
        entities=[
            {"type": "mention", "mentioned": {"id": "other-bot-id"}},
        ],
    )

    await plugin._on_message_activity(SimpleNamespace(activity=activity))

    assert len(dispatched) == 1
    assert dispatched[0].mentioned is False


@pytest.mark.asyncio
async def test_message_activity_entities_mark_bot_mention() -> None:
    plugin = TeamsChannel()
    plugin._app = SimpleNamespace(id="bot-id")
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    plugin._config = _teams_config()
    plugin._on_message = on_message
    activity = SimpleNamespace(
        id="activity-1",
        text="<at>Flocks</at> hello",
        conversation=SimpleNamespace(id="conversation-1", conversation_type="channel"),
        from_=SimpleNamespace(id="user-id", name="Alice"),
        entities=[
            {"type": "mention", "mentioned": {"id": "bot-id"}},
        ],
    )

    await plugin._on_message_activity(SimpleNamespace(activity=activity))

    assert len(dispatched) == 1
    assert dispatched[0].mentioned is True


@pytest.mark.asyncio
async def test_message_activity_ignores_self_messages() -> None:
    plugin = TeamsChannel()
    plugin._app = SimpleNamespace(id="bot-id")
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    plugin._config = _teams_config()
    plugin._on_message = on_message
    activity = SimpleNamespace(
        id="activity-1",
        text="hello",
        conversation=SimpleNamespace(id="conversation-1", conversation_type="personal"),
        from_=SimpleNamespace(id="bot-id", name="Flocks"),
    )

    await plugin._on_message_activity(SimpleNamespace(activity=activity))

    assert dispatched == []


@pytest.mark.asyncio
async def test_message_activity_ignores_self_messages_by_aad_object_id() -> None:
    plugin = TeamsChannel()
    plugin._app = SimpleNamespace(id="bot-id")
    dispatched: list[Any] = []

    async def on_message(msg: Any) -> None:
        dispatched.append(msg)

    plugin._config = {**_teams_config(), "botAadObjectId": "bot-aad-id"}
    plugin._on_message = on_message
    activity = SimpleNamespace(
        id="activity-1",
        text="hello",
        conversation=SimpleNamespace(id="conversation-1", conversation_type="personal"),
        from_=SimpleNamespace(id="from-id", aad_object_id="bot-aad-id", name="Flocks"),
    )

    await plugin._on_message_activity(SimpleNamespace(activity=activity))

    assert dispatched == []


@pytest.mark.asyncio
async def test_send_text_uses_reply_for_numeric_reply_to_id() -> None:
    plugin = TeamsChannel()
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class FakeApp:
        async def reply(self, *args: Any) -> SimpleNamespace:
            calls.append(("reply", args))
            return SimpleNamespace(id="reply-id")

        async def send(self, *args: Any) -> SimpleNamespace:
            calls.append(("send", args))
            return SimpleNamespace(id="send-id")

    plugin._app = FakeApp()

    result = await plugin.send_text(
        OutboundContext(
            channel_id="teams",
            to="conversation-1",
            reply_to_id="12345",
            text="hello",
        )
    )

    assert result.success is True
    assert result.message_id == "reply-id"
    assert calls == [("reply", ("conversation-1", "12345", "hello"))]


@pytest.mark.asyncio
async def test_send_text_falls_back_to_send_for_non_numeric_reply_to_id() -> None:
    plugin = TeamsChannel()
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class FakeApp:
        async def reply(self, *args: Any) -> SimpleNamespace:
            calls.append(("reply", args))
            return SimpleNamespace(id="reply-id")

        async def send(self, *args: Any) -> SimpleNamespace:
            calls.append(("send", args))
            return SimpleNamespace(id="send-id")

    plugin._app = FakeApp()

    result = await plugin.send_text(
        OutboundContext(
            channel_id="teams",
            to="conversation-1",
            reply_to_id="activity-1",
            text="hello",
        )
    )

    assert result.success is True
    assert result.message_id == "send-id"
    assert calls == [("send", ("conversation-1", "hello"))]


@pytest.mark.asyncio
async def test_send_text_applies_cached_service_url() -> None:
    plugin = TeamsChannel()
    plugin._conversation_service_urls["conversation-1"] = "https://service.example/teams"

    class FakeApp:
        async def send(self, *args: Any) -> SimpleNamespace:
            return SimpleNamespace(id="send-id")

    app = FakeApp()
    plugin._app = app

    result = await plugin.send_text(
        OutboundContext(channel_id="teams", to="conversation-1", text="hello")
    )

    assert result.success is True
    assert getattr(app, "service_url") == "https://service.example/teams"
    assert getattr(app, "_service_url") == "https://service.example/teams"


def test_teams_registered_in_builtin_registry() -> None:
    from flocks.channel.registry import ChannelRegistry

    reg = ChannelRegistry()
    reg._register_builtin_channels()

    assert reg.get("teams").meta().id == "teams"
    assert reg.get("msteams").meta().id == "teams"
