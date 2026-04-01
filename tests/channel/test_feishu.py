from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import ChatType, InboundMessage, OutboundContext
from flocks.channel.builtin.feishu.channel import FeishuChannel
from flocks.channel.builtin.feishu.config import (
    list_account_configs,
    merge_group_overrides,
    resolve_webhook_account_config,
)
from flocks.channel.builtin.feishu.debounce import get_debouncer
from flocks.channel.builtin.feishu.dedup import FeishuDedup
from flocks.channel.builtin.feishu.inbound_media import download_inbound_media
from flocks.channel.builtin.feishu.media import send_media_feishu
from flocks.channel.builtin.feishu.monitor import _build_ws_client, _start_single_websocket
from flocks.channel.builtin.feishu.send import send_message_feishu
from flocks.channel.inbound.dispatcher import _resolve_feishu_group_overrides
from flocks.config.config import ChannelConfig, FeishuGroupConfig


class _FakeDedup:
    def __init__(self, duplicate_ids: set[str] | None = None) -> None:
        self.duplicate_ids = duplicate_ids or set()
        self.seen: list[str] = []

    async def warmup(self) -> int:
        return 0

    async def start_background_flush(self) -> asyncio.Task:
        return asyncio.create_task(asyncio.sleep(3600))

    async def is_duplicate(self, message_id: str) -> bool:
        self.seen.append(message_id)
        return message_id in self.duplicate_ids

    async def flush(self) -> None:
        return None


def test_merge_group_overrides_normalizes_pydantic_models() -> None:
    groups = {
        "*": FeishuGroupConfig(requireMention=True, defaultAgent="wildcard"),
        "oc_test": FeishuGroupConfig(groupSessionScope="group_topic", allowFrom=["ou_user"]),
    }

    merged = merge_group_overrides(groups, "oc_test")

    assert merged == {
        "requireMention": True,
        "defaultAgent": "wildcard",
        "groupSessionScope": "group_topic",
        "allowFrom": ["ou_user"],
    }


def test_resolve_feishu_group_overrides_uses_normalized_group_config() -> None:
    cfg = ChannelConfig(
        groups={
            "*": FeishuGroupConfig(defaultAgent="wildcard", groupSessionScope="group"),
            "oc_test": FeishuGroupConfig(defaultAgent="specific", groupSessionScope="group_topic"),
        }
    )

    scope, agent = _resolve_feishu_group_overrides(cfg, "oc_test")

    assert scope == "group_topic"
    assert agent == "specific"


def test_resolve_webhook_account_config_matches_named_account_token() -> None:
    config = {
        "connectionMode": "webhook",
        "appId": "top",
        "appSecret": "secret",
        "verificationToken": "top-token",
        "accounts": {
            "main": {
                "connectionMode": "webhook",
                "appId": "main-id",
                "appSecret": "main-secret",
                "verificationToken": "main-token",
            },
            "backup": {
                "connectionMode": "webhook",
                "appId": "backup-id",
                "appSecret": "backup-secret",
                "verificationToken": "backup-token",
            },
        },
    }

    resolved = resolve_webhook_account_config(
        config,
        body=b'{"token":"backup-token"}',
        headers={},
        data={"token": "backup-token"},
    )

    assert resolved is not None
    assert resolved["_account_id"] == "backup"
    assert resolved["appId"] == "backup-id"


def test_resolve_webhook_account_config_rejects_ambiguous_matches() -> None:
    config = {
        "connectionMode": "webhook",
        "verificationToken": "shared-token",
        "accounts": {
            "main": {
                "connectionMode": "webhook",
                "verificationToken": "shared-token",
            },
            "backup": {
                "connectionMode": "webhook",
                "verificationToken": "shared-token",
            },
        },
    }

    resolved = resolve_webhook_account_config(
        config,
        body=b'{"token":"shared-token"}',
        headers={},
        data={"token": "shared-token"},
    )

    assert resolved is None


def test_resolve_webhook_account_config_requires_verification_surface() -> None:
    config = {
        "connectionMode": "webhook",
        "appId": "top-id",
        "appSecret": "top-secret",
    }

    resolved = resolve_webhook_account_config(
        config,
        body=b"{}",
        headers={},
        data={},
    )

    assert resolved is None


def test_list_account_configs_includes_top_level_default_with_named_accounts() -> None:
    config = {
        "appId": "top-id",
        "appSecret": "top-secret",
        "accounts": {
            "backup": {
                "appId": "backup-id",
                "appSecret": "backup-secret",
            }
        },
    }

    accounts = list_account_configs(config, require_credentials=True)

    assert [account["_account_id"] for account in accounts] == ["default", "backup"]


@pytest.mark.asyncio
async def test_feishu_channel_start_runs_websocket_for_mixed_transport(monkeypatch) -> None:
    channel = FeishuChannel()
    start_websocket = AsyncMock()

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor.start_websocket",
        start_websocket,
    )

    await channel.start(
        {
            "connectionMode": "webhook",
            "verificationToken": "top-token",
            "accounts": {
                "ws-main": {
                    "connectionMode": "websocket",
                    "appId": "ws-id",
                    "appSecret": "ws-secret",
                },
                "hook-backup": {
                    "connectionMode": "webhook",
                    "verificationToken": "hook-token",
                    "appId": "hook-id",
                    "appSecret": "hook-secret",
                },
            },
        },
        AsyncMock(),
        asyncio.Event(),
    )

    assert start_websocket.await_count == 1


@pytest.mark.asyncio
async def test_handle_webhook_uses_resolved_account_and_fetches_bot_identity(monkeypatch) -> None:
    channel = FeishuChannel()
    channel._config = {
        "accounts": {
            "main": {
                "connectionMode": "webhook",
                "inboundDebounceMs": 0,
                "verificationToken": "main-token",
                "appId": "main-id",
                "appSecret": "main-secret",
            }
        }
    }
    channel._on_message = AsyncMock()

    fake_dedup = _FakeDedup()
    parse_calls: list[tuple[dict, str | None]] = []

    def fake_parse_event(data: dict, config: dict, bot_open_id: str | None = None):
        parse_calls.append((config, bot_open_id))
        return InboundMessage(
            channel_id="feishu",
            account_id=config["_account_id"],
            message_id="msg_1",
            sender_id="ou_sender",
            chat_id="oc_group",
            chat_type=ChatType.GROUP,
            text="hello",
            mention_text="hello",
            mentioned=True,
        )

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.dedup.get_dedup",
        AsyncMock(return_value=fake_dedup),
    )
    monkeypatch.setattr(channel, "_ensure_webhook_dedup_ready", AsyncMock())
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.identity.get_cached_bot_open_id",
        lambda account_id="default": None,
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.identity.get_bot_identity",
        AsyncMock(return_value=("ou_bot", "bot")),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor._parse_event",
        fake_parse_event,
    )

    body = json.dumps(
        {
            "token": "main-token",
            "header": {"event_type": "im.message.receive_v1", "event_id": "evt_1"},
            "event": {},
        }
    ).encode("utf-8")

    await channel.handle_webhook(body, {})

    assert channel._on_message.await_count == 1
    assert parse_calls[0][0]["_account_id"] == "main"
    assert parse_calls[0][1] == "ou_bot"


@pytest.mark.asyncio
async def test_get_bot_identity_parses_top_level_bot_payload(monkeypatch) -> None:
    from flocks.channel.builtin.feishu import identity as identity_module

    identity_module._identity_cache.clear()

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.client.api_request_for_account",
        AsyncMock(
            return_value={
                "code": 0,
                "msg": "ok",
                "bot": {
                    "open_id": "ou_real_bot",
                    "app_name": "Flocks - feishu",
                },
            }
        ),
    )

    open_id, name = await identity_module.get_bot_identity(
        {"appId": "app-id", "appSecret": "app-secret"},
        "default",
    )

    assert open_id == "ou_real_bot"
    assert name == "Flocks - feishu"


@pytest.mark.asyncio
async def test_handle_webhook_invalid_timestamp_returns_status_code() -> None:
    channel = FeishuChannel()
    channel._config = {
        "connectionMode": "webhook",
        "verificationToken": "main-token",
        "appId": "main-id",
        "appSecret": "main-secret",
    }

    body = json.dumps(
        {
            "token": "main-token",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {},
        }
    ).encode("utf-8")

    result = await channel.handle_webhook(
        body,
        {"x-lark-request-timestamp": "1"},
    )

    assert result == {"error": "invalid timestamp", "status_code": 400}


@pytest.mark.asyncio
async def test_channel_route_uses_plugin_status_code(monkeypatch) -> None:
    from flocks.server.routes.channel import channel_webhook

    class _StubPlugin:
        async def handle_webhook(self, body, headers):
            return {"error": "invalid signature", "status_code": 401}

    class _StubRequest:
        headers = {}

        async def body(self):
            return b"{}"

    monkeypatch.setattr(
        "flocks.server.routes.channel.default_registry.get",
        lambda _channel_id: _StubPlugin(),
    )

    response = await channel_webhook("feishu", _StubRequest())

    assert response.status_code == 401
    assert json.loads(response.body) == {"error": "invalid signature"}


@pytest.mark.asyncio
async def test_handle_webhook_skips_replayed_requests(monkeypatch) -> None:
    channel = FeishuChannel()
    channel._config = {
        "connectionMode": "webhook",
        "inboundDebounceMs": 0,
        "verificationToken": "main-token",
        "appId": "main-id",
        "appSecret": "main-secret",
    }
    channel._on_message = AsyncMock()

    replay_key = "replay:event:evt_replayed"
    fake_dedup = _FakeDedup(duplicate_ids={replay_key})

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.dedup.get_dedup",
        AsyncMock(return_value=fake_dedup),
    )
    monkeypatch.setattr(channel, "_ensure_webhook_dedup_ready", AsyncMock())

    body = json.dumps(
        {
            "token": "main-token",
            "header": {"event_type": "im.message.receive_v1", "event_id": "evt_replayed"},
            "event": {},
        }
    ).encode("utf-8")

    await channel.handle_webhook(body, {})

    assert channel._on_message.await_count == 0
    assert replay_key in fake_dedup.seen


@pytest.mark.asyncio
async def test_websocket_card_action_events_are_deduplicated(monkeypatch, tmp_path) -> None:
    abort_event = asyncio.Event()
    on_message = AsyncMock()
    dedup = FeishuDedup(account_id="main", data_dir=tmp_path)

    class FakeWSClient:
        def __init__(self, app_id, app_secret, event_handler, log_level):
            self._event_handler = event_handler

        def start(self):
            payload = {"header": {"event_type": "card.action.trigger"}, "event": {}}
            self._event_handler(payload)
            self._event_handler(payload)
            asyncio.get_running_loop().call_later(0.05, abort_event.set)

        def stop(self):
            return None

    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.LogLevel = types.SimpleNamespace(WARNING="warning")
    fake_adapter = types.ModuleType("lark_oapi.adapter")
    fake_websocket = types.ModuleType("lark_oapi.adapter.websocket")
    fake_websocket.WSClient = FakeWSClient

    monkeypatch.setitem(sys.modules, "lark_oapi", fake_lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.adapter", fake_adapter)
    monkeypatch.setitem(sys.modules, "lark_oapi.adapter.websocket", fake_websocket)
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.identity.get_bot_identity",
        AsyncMock(return_value=("ou_bot", "bot")),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.dedup.get_dedup",
        AsyncMock(return_value=dedup),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor._parse_card_action_event",
        lambda data, config: InboundMessage(
            channel_id="feishu",
            account_id="main",
            message_id="card-action:main:stable",
            sender_id="ou_sender",
            chat_id="oc_group",
            chat_type=ChatType.GROUP,
            text="run",
            mention_text="run",
            mentioned=True,
        ),
    )

    await _start_single_websocket(
        {
            "appId": "main-id",
            "appSecret": "main-secret",
            "_account_id": "main",
            "dedupEnabled": True,
        },
        on_message,
        abort_event,
    )

    assert on_message.await_count == 1


def test_build_ws_client_falls_back_to_modern_sdk(monkeypatch) -> None:
    dispatched: list[dict] = []
    captured: dict[str, object] = {}

    class _FakeFuture:
        def result(self, timeout=None):
            return None

    class _FakeLoop:
        def call_soon_threadsafe(self, callback, *args):
            callback(*args)

        def stop(self):
            return None

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._disconnect_called = False

        def start(self):
            captured["event_handler"].do_without_validation(b'{"header":{"event_type":"ping"}}')

        async def _disconnect(self):
            self._disconnect_called = True

    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.LogLevel = types.SimpleNamespace(WARNING="warning")
    fake_ws_client = types.ModuleType("lark_oapi.ws.client")
    fake_ws_client.Client = _FakeClient
    fake_ws_client.loop = _FakeLoop()

    real_import_module = __import__("importlib").import_module

    def fake_import_module(name, package=None):
        if name == "lark_oapi":
            return fake_lark
        if name == "lark_oapi.ws.client":
            return fake_ws_client
        if name == "lark_oapi.adapter.websocket":
            raise ImportError("legacy websocket adapter missing")
        return real_import_module(name, package)

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor.importlib.import_module",
        fake_import_module,
    )
    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return _FakeFuture()

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor.asyncio.run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    ws_client = _build_ws_client(
        app_id="app-id",
        app_secret="app-secret",
        event_handler=lambda data: dispatched.append(data),
        domain="https://open.feishu.cn",
    )

    ws_client.start()
    ws_client.stop()

    assert captured["domain"] == "https://open.feishu.cn"
    assert dispatched == [{"header": {"event_type": "ping"}}]


def test_build_ws_client_ignores_normal_close_during_stop(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class ConnectionClosedOK(Exception):
        def __init__(self) -> None:
            self.rcvd = types.SimpleNamespace(code=1000, reason="bye")
            self.sent = types.SimpleNamespace(code=1000, reason="")
            super().__init__("sent 1000 (OK); then received 1000 (OK) bye")

    class _FakeConnection:
        def __init__(self, client) -> None:
            self._client = client

        async def recv(self):
            while not self._client.closed:
                await asyncio.sleep(0.01)
            raise ConnectionClosedOK()

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["client"] = self
            self._conn = None
            self._auto_reconnect = kwargs["auto_reconnect"]
            self.closed = False
            self.disconnect_calls = 0

        def start(self):
            loop = asyncio.get_event_loop()
            self._conn = _FakeConnection(self)
            loop.create_task(self._receive_message_loop())
            loop.run_forever()

        async def _handle_message(self, _msg):
            return None

        async def _disconnect(self):
            self.disconnect_calls += 1
            self.closed = True
            self._conn = None

    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.LogLevel = types.SimpleNamespace(WARNING="warning")
    fake_ws_client = types.ModuleType("lark_oapi.ws.client")
    fake_ws_client.Client = _FakeClient
    fake_ws_client.loop = None

    real_import_module = __import__("importlib").import_module

    def fake_import_module(name, package=None):
        if name == "lark_oapi":
            return fake_lark
        if name == "lark_oapi.ws.client":
            return fake_ws_client
        if name == "lark_oapi.adapter.websocket":
            raise ImportError("legacy websocket adapter missing")
        return real_import_module(name, package)

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor.importlib.import_module",
        fake_import_module,
    )

    ws_client = _build_ws_client(
        app_id="app-id",
        app_secret="app-secret",
        event_handler=lambda _data: None,
        domain="https://open.feishu.cn",
    )

    ws_client.start()
    ws_client.stop()

    fake_client = captured["client"]
    assert fake_client.disconnect_calls >= 1
    assert ws_client.start_error is None


@pytest.mark.asyncio
async def test_parse_reaction_event_falls_back_to_user_id(monkeypatch) -> None:
    from flocks.channel.builtin.feishu.monitor import _parse_reaction_event

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.monitor._is_bot_message",
        AsyncMock(return_value=True),
    )

    msg = await _parse_reaction_event(
        {
            "event": {
                "message_id": "om_1",
                "reaction_type": {"emoji_type": "THUMBSUP"},
                "user_id": {"user_id": "u_123"},
                "chat_id": "oc_group",
                "chat_type": "group",
            }
        },
        {"_account_id": "main", "reactionNotifications": "own"},
    )

    assert msg is not None
    assert msg.sender_id == "u_123"


@pytest.mark.asyncio
async def test_download_inbound_media_saves_message_resource(monkeypatch, tmp_path: Path) -> None:
    class _FakeStreamResponse:
        def __init__(self) -> None:
            self.headers = {"content-type": "image/png"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int):
            yield b"png-bytes"

    class _FakeClient:
        def stream(self, method, url, params=None, headers=None):
            assert method == "GET"
            assert url.endswith("/im/v1/messages/om_1/resources/img_1")
            assert params == {"type": "image"}
            assert headers == {"Authorization": "Bearer token_1"}
            return _FakeStreamResponse()

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.inbound_media._get_http_client",
        AsyncMock(return_value=_FakeClient()),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.inbound_media.get_tenant_token",
        AsyncMock(return_value="token_1"),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.inbound_media._media_storage_dir",
        lambda _account_id: tmp_path,
    )

    media = await download_inbound_media(
        InboundMessage(
            channel_id="feishu",
            account_id="main",
            message_id="om_1",
            sender_id="ou_user",
            chat_id="oc_group",
            chat_type=ChatType.GROUP,
            media_url="lark://image/img_1",
            raw={
                "event": {
                    "message": {
                        "content": json.dumps({"image_key": "img_1"}),
                    }
                }
            },
        ),
        {"appId": "app-id", "appSecret": "app-secret"},
    )

    assert media is not None
    assert media.mime == "image/png"
    assert media.filename.endswith(".png")
    assert Path(media.url.removeprefix("file://")).read_bytes() == b"png-bytes"


@pytest.mark.asyncio
async def test_send_media_feishu_preserves_reply_to_id(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    async def fake_send_payload(**kwargs):
        captured["reply_to_id"] = kwargs.get("reply_to_id")
        return {"message_id": "msg_1", "chat_id": "oc_group"}

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.send.send_payload_feishu",
        fake_send_payload,
    )

    result = await send_media_feishu(
        config={},
        to="chat:oc_group",
        media_url="lark://image/img_1",
        reply_to_id="reply_1",
        account_id="main",
    )

    assert result["message_id"] == "msg_1"
    assert captured["reply_to_id"] == "reply_1"


@pytest.mark.asyncio
async def test_send_message_feishu_requires_message_id(monkeypatch) -> None:
    async def fake_api_request_for_account(*args, **kwargs):
        return {"data": {}}

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.send.api_request_for_account",
        fake_api_request_for_account,
    )

    with pytest.raises(RuntimeError, match="missing message_id"):
        await send_message_feishu(
            config={"renderMode": "plain"},
            to="user:ou_user",
            text="hello",
            account_id="main",
        )


@pytest.mark.asyncio
async def test_dedup_tracks_synthetic_and_replay_ids() -> None:
    dedup = FeishuDedup(account_id="main")

    assert await dedup.is_duplicate("synthetic:reaction:main:msg_1:thumbs_up:ou_user") is False
    assert await dedup.is_duplicate("synthetic:reaction:main:msg_1:thumbs_up:ou_user") is True

    assert await dedup.is_duplicate("replay:event:evt_1") is False
    assert await dedup.is_duplicate("replay:event:evt_1") is True


def test_get_debouncer_updates_debounce_window() -> None:
    debouncer = get_debouncer("test-refresh", debounce_ms=800)
    same_debouncer = get_debouncer("test-refresh", debounce_ms=0)

    assert same_debouncer is debouncer
    assert same_debouncer._debounce_ms == 0


@pytest.mark.asyncio
async def test_sender_name_cache_isolated_by_account(monkeypatch) -> None:
    from flocks.channel.builtin.feishu import sender_name as sender_name_module

    sender_name_module._name_cache.clear()
    calls: list[str] = []

    async def fake_api_request_for_account(*args, **kwargs):
        account_id = kwargs["account_id"]
        calls.append(account_id)
        return {"data": {"user": {"name": f"name-{account_id}"}}}

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.client.api_request_for_account",
        fake_api_request_for_account,
    )

    name_a, _ = await sender_name_module.resolve_sender_name("ou_user", {}, "acc_a")
    name_b, _ = await sender_name_module.resolve_sender_name("ou_user", {}, "acc_b")
    name_a_cached, _ = await sender_name_module.resolve_sender_name("ou_user", {}, "acc_a")

    assert name_a == "name-acc_a"
    assert name_b == "name-acc_b"
    assert name_a_cached == "name-acc_a"
    assert calls == ["acc_a", "acc_b"]


def test_group_allowlist_without_entries_blocks_group() -> None:
    from flocks.channel.builtin.feishu.monitor import _check_group_policy

    assert _check_group_policy(
        {"groupPolicy": "allowlist"},
        ChatType.GROUP,
        mentioned=True,
        sender_id="ou_user",
        chat_id="oc_group",
    ) is False


@pytest.mark.asyncio
async def test_channel_send_media_passes_reply_to_id(monkeypatch) -> None:
    channel = FeishuChannel()
    channel._config = {}

    captured: dict[str, str | None] = {}

    async def fake_send_media_feishu(**kwargs):
        captured["reply_to_id"] = kwargs.get("reply_to_id")
        return {"message_id": "msg_1", "chat_id": "oc_group"}

    monkeypatch.setattr(
        "flocks.channel.builtin.feishu.media.send_media_feishu",
        fake_send_media_feishu,
    )

    result = await channel.send_media(
        OutboundContext(
            channel_id="feishu",
            account_id="main",
            to="oc_group",
            media_url="lark://image/img_1",
            reply_to_id="reply_1",
        )
    )

    assert result.success is True
    assert captured["reply_to_id"] == "reply_1"
