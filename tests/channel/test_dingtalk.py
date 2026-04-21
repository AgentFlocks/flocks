"""
Tests for the DingTalk active-outbound send library.

Layout:
  - send library  → flocks.channel.builtin.dingtalk.{config,client,send}
  - inbound owner → .flocks/plugins/channels/dingtalk/dingtalk.py (Node.js)

Only the OAPI app-robot ("stream/app push") path is supported; custom
group-robot incoming webhooks are intentionally out of scope.

The builtin package does NOT register a ChannelPlugin (to avoid id
collisions with the local plugin), so registry-side tests are absent.
The local Node.js plugin is owned separately and not exercised here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import ChatType, OutboundContext
from flocks.channel.builtin.dingtalk.client import (
    DingTalkApiError,
    ensure_api_success,
)
from flocks.channel.builtin.dingtalk.config import (
    list_account_configs,
    resolve_account_credentials,
    resolve_target_kind,
    strip_target_prefix,
)
from flocks.channel.builtin.dingtalk.send import (
    build_app_payload,
    send_message_app,
)


# ------------------------------------------------------------------
# config helpers
# ------------------------------------------------------------------

class TestConfigHelpers:
    def test_strip_target_prefix(self):
        assert strip_target_prefix("user:abc") == "abc"
        assert strip_target_prefix("chat:cidXYZ") == "cidXYZ"
        assert strip_target_prefix("plain") == "plain"
        assert strip_target_prefix("") == ""

    def test_resolve_target_kind(self):
        assert resolve_target_kind("user:zhangsan") == "user"
        assert resolve_target_kind("chat:cid1") == "group"
        assert resolve_target_kind("cidABC123") == "group"
        assert resolve_target_kind("zhangsan") == "user"
        assert resolve_target_kind("") == "user"

    def test_resolve_account_credentials_default(self):
        cfg = {"appKey": "K", "appSecret": "S", "robotCode": "R"}
        assert resolve_account_credentials(cfg, None) == ("K", "S", "R")
        assert resolve_account_credentials(cfg, "default") == ("K", "S", "R")

    def test_resolve_account_credentials_override(self):
        cfg = {
            "appKey": "K", "appSecret": "S", "robotCode": "R",
            "accounts": {
                "alice": {"appKey": "K2", "appSecret": "S2"},
            },
        }
        assert resolve_account_credentials(cfg, "alice") == ("K2", "S2", "R")

    def test_resolve_account_credentials_accepts_client_id_alias(self):
        # DingTalk Stream config uses clientId/clientSecret; the send library
        # must transparently treat them as appKey/appSecret.
        cfg = {"clientId": "K", "clientSecret": "S", "robotCode": "R"}
        assert resolve_account_credentials(cfg, None) == ("K", "S", "R")

    def test_resolve_account_credentials_app_key_wins_over_alias(self):
        cfg = {
            "appKey": "PRIMARY", "clientId": "FALLBACK",
            "appSecret": "PS", "clientSecret": "FS",
            "robotCode": "R",
        }
        assert resolve_account_credentials(cfg, None) == ("PRIMARY", "PS", "R")

    def test_robot_code_defaults_to_app_key(self):
        # Standard "enterprise internal app robot" — robotCode == appKey,
        # so users don't have to repeat themselves in flocks.json.
        cfg = {"appKey": "K", "appSecret": "S"}
        assert resolve_account_credentials(cfg, None) == ("K", "S", "K")

    def test_robot_code_defaults_to_client_id_alias(self):
        # Same fallback when only the Stream-style aliases are present.
        cfg = {"clientId": "dingXYZ", "clientSecret": "S"}
        assert resolve_account_credentials(cfg, None) == ("dingXYZ", "S", "dingXYZ")

    def test_robot_code_defaults_per_account(self):
        # Per-account override of credentials should produce a per-account
        # robotCode default — not a stale top-level fallback.
        cfg = {
            "appKey": "TOP_K", "appSecret": "TOP_S",
            "accounts": {
                "alice": {"appKey": "ALICE_K", "appSecret": "ALICE_S"},
            },
        }
        assert resolve_account_credentials(cfg, "alice") == (
            "ALICE_K", "ALICE_S", "ALICE_K",
        )

    def test_explicit_robot_code_overrides_app_key_default(self):
        cfg = {"appKey": "K", "appSecret": "S", "robotCode": "EXPLICIT"}
        assert resolve_account_credentials(cfg, None) == ("K", "S", "EXPLICIT")

    def test_list_account_configs_top_level_app(self):
        cfg = {"appKey": "k", "appSecret": "s", "robotCode": "r"}
        accounts = list_account_configs(cfg, require_credentials=True)
        assert len(accounts) == 1
        assert accounts[0]["_account_id"] == "default"

    def test_list_account_configs_accepts_client_id_alias(self):
        cfg = {"clientId": "k", "clientSecret": "s"}
        accounts = list_account_configs(cfg, require_credentials=True)
        assert len(accounts) == 1

    def test_list_account_configs_skips_disabled(self):
        cfg = {
            "robotCode": "r",
            "accounts": {
                "alice": {"appKey": "k", "appSecret": "s", "enabled": False},
                "bob":   {"appKey": "k2", "appSecret": "s2"},
            },
        }
        accounts = list_account_configs(cfg, require_credentials=True)
        ids = {a["_account_id"] for a in accounts}
        assert ids == {"bob"}

    def test_list_account_configs_filters_missing_credentials(self):
        cfg = {
            "accounts": {
                "alice": {"appKey": "k"},  # missing appSecret
            },
        }
        accounts = list_account_configs(cfg, require_credentials=True)
        assert accounts == []


# ------------------------------------------------------------------
# Payload builder
# ------------------------------------------------------------------

class TestAppPayloadBuilder:
    def test_plain_text(self):
        msg_key, msg_param = build_app_payload("hello", "plain")
        assert msg_key == "sampleText"
        assert json.loads(msg_param) == {"content": "hello"}

    def test_markdown_default(self):
        msg_key, msg_param = build_app_payload("# 标题\n正文", "auto")
        assert msg_key == "sampleMarkdown"
        param = json.loads(msg_param)
        assert param["title"] == "标题"
        assert "正文" in param["text"]

    def test_markdown_uses_fallback_title_when_blank(self):
        msg_key, msg_param = build_app_payload("\n\n   ", "card")
        param = json.loads(msg_param)
        assert msg_key == "sampleMarkdown"
        assert param["title"] == "通知"


# ------------------------------------------------------------------
# send_message_app — routing between user and group
# ------------------------------------------------------------------

class TestSendApp:
    async def test_user_target_uses_oto_endpoint(self):
        captured: dict = {}

        async def _fake_request(method, path, *, config, account_id, json_body=None, params=None):
            captured["path"] = path
            captured["body"] = json_body
            return {"processQueryKey": "pqk-1"}

        cfg = {"appKey": "k", "appSecret": "s", "robotCode": "r"}
        with patch(
            "flocks.channel.builtin.dingtalk.send.api_request_for_account",
            new=AsyncMock(side_effect=_fake_request),
        ):
            result = await send_message_app(
                config=cfg, to="user:zhangsan", text="hello",
            )

        assert captured["path"] == "/v1.0/robot/oToMessages/batchSend"
        assert captured["body"]["userIds"] == ["zhangsan"]
        assert captured["body"]["msgKey"] == "sampleMarkdown"
        assert captured["body"]["robotCode"] == "r"
        assert result["message_id"] == "pqk-1"
        assert result["chat_id"] == "zhangsan"

    async def test_chat_target_uses_group_endpoint(self):
        captured: dict = {}

        async def _fake_request(method, path, *, config, account_id, json_body=None, params=None):
            captured["path"] = path
            captured["body"] = json_body
            return {"processQueryKey": "pqk-2"}

        cfg = {
            "appKey": "k", "appSecret": "s", "robotCode": "r",
            "renderMode": "plain",
        }
        with patch(
            "flocks.channel.builtin.dingtalk.send.api_request_for_account",
            new=AsyncMock(side_effect=_fake_request),
        ):
            await send_message_app(
                config=cfg, to="chat:cid_GROUP_1", text="hi all",
            )

        assert captured["path"] == "/v1.0/robot/groupMessages/send"
        assert captured["body"]["openConversationId"] == "cid_GROUP_1"
        assert captured["body"]["msgKey"] == "sampleText"

    async def test_app_send_works_with_client_id_alias(self):
        # Reuses the DingTalk Stream credential fields end-to-end.
        captured: dict = {}

        async def _fake_request(method, path, *, config, account_id, json_body=None, params=None):
            captured["path"] = path
            captured["body"] = json_body
            return {"processQueryKey": "pqk-3"}

        cfg = {"clientId": "ck", "clientSecret": "cs", "robotCode": "r"}
        with patch(
            "flocks.channel.builtin.dingtalk.send.api_request_for_account",
            new=AsyncMock(side_effect=_fake_request),
        ):
            await send_message_app(config=cfg, to="user:u1", text="hello")

        assert captured["body"]["userIds"] == ["u1"]
        assert captured["body"]["robotCode"] == "r"

    async def test_missing_credentials_raises(self):
        # robotCode now defaults to appKey, so the only way the resolved
        # robotCode is empty is when no credentials are configured at all.
        cfg = {}
        with pytest.raises(ValueError, match="credentials not configured"):
            await send_message_app(config=cfg, to="user:abc", text="hi")

    async def test_robot_code_defaults_to_app_key_at_send_time(self):
        cfg = {"appKey": "myapp", "appSecret": "s"}
        captured: dict = {}

        async def _fake_request(method, path, *, config, account_id, json_body=None, params=None):
            captured["body"] = json_body
            return {"processQueryKey": "pqk"}

        with patch(
            "flocks.channel.builtin.dingtalk.send.api_request_for_account",
            new=_fake_request,
        ):
            await send_message_app(config=cfg, to="user:abc", text="hi")

        assert captured["body"]["robotCode"] == "myapp"

    async def test_empty_target_raises(self):
        cfg = {"appKey": "k", "appSecret": "s", "robotCode": "r"}
        with pytest.raises(ValueError, match="empty target"):
            await send_message_app(config=cfg, to="", text="hi")

    async def test_long_text_chunks_into_multiple_calls(self):
        calls: list[dict] = []

        async def _fake_request(method, path, *, config, account_id, json_body=None, params=None):
            calls.append({"path": path, "body": json_body})
            return {"processQueryKey": f"pqk-{len(calls)}"}

        cfg = {
            "appKey": "k", "appSecret": "s", "robotCode": "r",
            "textChunkLimit": 10,
        }
        with patch(
            "flocks.channel.builtin.dingtalk.send.api_request_for_account",
            new=AsyncMock(side_effect=_fake_request),
        ):
            await send_message_app(
                config=cfg, to="user:u1",
                text="abcde\nfghij\nklmno\npqrst",
            )

        assert len(calls) >= 2


# ------------------------------------------------------------------
# Client error parsing
# ------------------------------------------------------------------

class TestEnsureApiSuccess:
    def test_legacy_errcode_zero_passes(self):
        data = ensure_api_success({"errcode": 0, "errmsg": "ok"}, context="ctx")
        assert data["errcode"] == 0

    def test_legacy_errcode_non_zero_raises(self):
        with pytest.raises(DingTalkApiError) as exc:
            ensure_api_success(
                {"errcode": 310000, "errmsg": "keywords not in content"},
                context="oapi",
            )
        assert exc.value.code == "310000"

    def test_v1_code_field_raises(self):
        with pytest.raises(DingTalkApiError) as exc:
            ensure_api_success(
                {"code": "InvalidParameter", "message": "bad robotCode"},
                context="oapi",
                http_status=400,
            )
        assert exc.value.code == "InvalidParameter"
        assert exc.value.http_status == 400

    def test_throttling_marked_retryable(self):
        with pytest.raises(DingTalkApiError) as exc:
            ensure_api_success(
                {"code": "Throttling.Api", "message": "rate limit exceeded"},
                context="oapi",
                http_status=429,
            )
        assert exc.value.retryable is True

    def test_success_payload_passes_through(self):
        # v1.0 success payloads typically lack a ``code`` field altogether.
        data = ensure_api_success(
            {"processQueryKey": "abc"},
            context="oapi",
            http_status=200,
        )
        assert data["processQueryKey"] == "abc"


# ------------------------------------------------------------------
# Builtin package no longer registers a ChannelPlugin
# ------------------------------------------------------------------

class TestBuiltinHasNoChannelClass:
    def test_no_dingtalk_in_builtin_registry(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        reg._register_builtin_channels()
        # The builtin package intentionally exposes only a send library;
        # the dingtalk id is owned by the project-local plugin.
        assert reg.get("dingtalk") is None

    def test_builtin_package_has_no_channel_module(self):
        spec = importlib.util.find_spec(
            "flocks.channel.builtin.dingtalk.channel"
        )
        assert spec is None


# ------------------------------------------------------------------
# Local plugin send_text — delegates to send_message_app
# ------------------------------------------------------------------

# The local plugin lives under .flocks/plugins/channels/dingtalk/dingtalk.py;
# load it by file path because that directory is not on sys.path during tests.
_LOCAL_PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks/plugins/channels/dingtalk/dingtalk.py"
)


def _load_local_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "_test_dingtalk_local_plugin", _LOCAL_PLUGIN_PATH
    )
    assert spec and spec.loader, f"cannot load {_LOCAL_PLUGIN_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLocalPluginSendText:
    """Active outbound (channel_message tool) goes through send_text."""

    def _make_plugin(self, **config):
        mod = _load_local_plugin_module()
        plugin = mod.DingTalkChannel()
        plugin._config = config
        # Bypass the live `Config.get()` lookup added in send_text; tests
        # supply the channel config directly.
        async def _fake_resolve(self=plugin):
            return dict(config)
        plugin._resolve_outbound_config = _fake_resolve  # type: ignore[assignment]
        return plugin

    @pytest.mark.asyncio
    async def test_send_text_delegates_to_send_message_app(self):
        plugin = self._make_plugin(
            clientId="dingkey",
            clientSecret="secret",
            robotCode="dingrobot",
        )
        ctx = OutboundContext(
            channel_id="dingtalk",
            to="user:staff_001",
            text="hello from rex",
            account_id="default",
        )

        sent_kwargs = {}

        async def fake_send(**kwargs):
            sent_kwargs.update(kwargs)
            return {"message_id": "mid_xxx", "chat_id": "staff_001"}

        with patch(
            "flocks.channel.builtin.dingtalk.send_message_app",
            new=fake_send,
        ):
            result = await plugin.send_text(ctx)

        assert result.success is True
        assert result.message_id == "mid_xxx"
        assert result.chat_id == "staff_001"
        assert sent_kwargs["to"] == "user:staff_001"
        assert sent_kwargs["text"] == "hello from rex"
        # The plugin must forward its full config (so robotCode reaches the lib).
        assert sent_kwargs["config"]["robotCode"] == "dingrobot"

    @pytest.mark.asyncio
    async def test_send_text_works_without_explicit_robot_code(self):
        """robotCode defaults to clientId/appKey — no extra config needed."""
        plugin = self._make_plugin(clientId="dingkey", clientSecret="s")
        ctx = OutboundContext(channel_id="dingtalk", to="user:staff_001", text="hi")

        sent_kwargs = {}

        async def fake_send(**kwargs):
            sent_kwargs.update(kwargs)
            return {"message_id": "m", "chat_id": "staff_001"}

        with patch(
            "flocks.channel.builtin.dingtalk.send_message_app",
            new=fake_send,
        ):
            result = await plugin.send_text(ctx)

        assert result.success is True
        # The plugin must NOT inject robotCode itself; the send library
        # resolves it from clientId via resolve_account_credentials.
        assert "robotCode" not in sent_kwargs["config"]

    @pytest.mark.asyncio
    async def test_send_text_missing_target_returns_error(self):
        plugin = self._make_plugin(clientId="k", clientSecret="s")
        ctx = OutboundContext(channel_id="dingtalk", to="", text="hi")

        result = await plugin.send_text(ctx)

        assert result.success is False
        assert "to" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_resolve_outbound_config_reads_live_global_config(self):
        """send_text must NOT depend on self._config: PluginLoader can register
        a fresh DingTalkChannel after start() ran on the original instance,
        leaving self._config = None on the one outbound actually picks up.
        """
        mod = _load_local_plugin_module()
        plugin = mod.DingTalkChannel()  # NB: no plugin._config set on purpose

        from flocks.config.config import ChannelConfig as _CC

        live_cfg = _CC(
            enabled=True,
            **{
                "clientId": "live_key",
                "clientSecret": "live_secret",
            },
        )

        # _resolve_outbound_config inspects ``cfg.channels`` directly so it
        # can distinguish "not configured" from a synthesised default — the
        # stub must therefore expose the same attribute.
        class _FakeCfgInfo:
            channels = {"dingtalk": live_cfg}

        async def _fake_get():
            return _FakeCfgInfo()

        with patch("flocks.config.config.Config.get", new=_fake_get):
            data = await plugin._resolve_outbound_config()

        assert data["clientId"] == "live_key"
        assert data["clientSecret"] == "live_secret"

    @pytest.mark.asyncio
    async def test_send_text_propagates_dingtalk_api_error_as_failure(self):
        plugin = self._make_plugin(clientId="k", clientSecret="s")
        ctx = OutboundContext(channel_id="dingtalk", to="user:x", text="hi")

        async def raising_send(**_):
            raise DingTalkApiError(
                "throttled", code="Throttling.Api", retryable=True,
            )

        with patch(
            "flocks.channel.builtin.dingtalk.send_message_app",
            new=raising_send,
        ):
            result = await plugin.send_text(ctx)

        assert result.success is False
        assert result.retryable is True
        assert "throttled" in (result.error or "")


# ------------------------------------------------------------------
# SessionBindingService.bind_session — used by runner.ts → /bind
# ------------------------------------------------------------------

class TestSessionBindingServiceBindSession:
    @pytest.mark.asyncio
    async def test_bind_session_inserts_row_for_existing_session(self, monkeypatch):
        from flocks.channel.inbound import session_binding as sb_mod

        svc = sb_mod.SessionBindingService()

        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id="ses_42", agent="rex")),
        )

        inserted = []

        async def fake_insert(binding):
            inserted.append(binding)

        svc._insert = fake_insert  # type: ignore[assignment]

        binding = await svc.bind_session(
            session_id="ses_42",
            channel_id="dingtalk",
            account_id="default",
            chat_id="cidXXXX",
            chat_type=ChatType.GROUP,
        )

        assert binding.session_id == "ses_42"
        assert binding.channel_id == "dingtalk"
        assert binding.chat_type is ChatType.GROUP
        assert inserted and inserted[0].chat_id == "cidXXXX"

    @pytest.mark.asyncio
    async def test_bind_session_raises_when_session_missing(self, monkeypatch):
        from flocks.channel.inbound import session_binding as sb_mod

        svc = sb_mod.SessionBindingService()
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=None),
        )

        with pytest.raises(ValueError, match="not found"):
            await svc.bind_session(
                session_id="ses_missing",
                channel_id="dingtalk",
                account_id="default",
                chat_id="cidXXXX",
                chat_type=ChatType.DIRECT,
            )


# ------------------------------------------------------------------
# POST /api/channel/{channel_id}/bind — exposes bind_session over HTTP
# ------------------------------------------------------------------

class TestBindEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from flocks.server.routes.channel import router

        app = FastAPI()
        app.include_router(router, prefix="/api/channel")
        return TestClient(app)

    def test_bind_endpoint_calls_service_and_returns_payload(self, client, monkeypatch):
        from flocks.channel.base import ChatType as _ChatType
        from flocks.channel.inbound import session_binding as sb_mod

        called = {}

        async def fake_bind(self, **kwargs):
            called.update(kwargs)
            return sb_mod.SessionBinding(
                channel_id=kwargs["channel_id"],
                account_id=kwargs["account_id"],
                chat_id=kwargs["chat_id"],
                chat_type=kwargs["chat_type"],
                thread_id=kwargs.get("thread_id"),
                session_id=kwargs["session_id"],
                agent_id=kwargs.get("agent_id"),
                created_at=0.0,
                last_message_at=0.0,
            )

        monkeypatch.setattr(sb_mod.SessionBindingService, "bind_session", fake_bind)

        resp = client.post(
            "/api/channel/dingtalk/bind",
            json={
                "session_id": "ses_42",
                "chat_id": "cidXXXX",
                "chat_type": "group",
                "account_id": "default",
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {
            "ok": True,
            "channel_id": "dingtalk",
            "session_id": "ses_42",
            "chat_id": "cidXXXX",
            "chat_type": "group",
        }
        assert called["channel_id"] == "dingtalk"
        assert called["chat_type"] is _ChatType.GROUP

    def test_bind_endpoint_rejects_invalid_chat_type(self, client):
        resp = client.post(
            "/api/channel/dingtalk/bind",
            json={
                "session_id": "ses_42",
                "chat_id": "cidXXXX",
                "chat_type": "channel",  # not allowed
            },
        )
        assert resp.status_code == 400
        assert "chat_type" in resp.json()["detail"]

    def test_bind_endpoint_returns_404_when_session_missing(self, client, monkeypatch):
        from flocks.channel.inbound import session_binding as sb_mod

        async def raising(self, **_):
            raise ValueError("Session 'ses_missing' not found")

        monkeypatch.setattr(sb_mod.SessionBindingService, "bind_session", raising)

        resp = client.post(
            "/api/channel/dingtalk/bind",
            json={
                "session_id": "ses_missing",
                "chat_id": "x",
                "chat_type": "direct",
            },
        )

        assert resp.status_code == 404
        assert "ses_missing" in resp.json()["detail"]
