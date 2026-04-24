"""
Tests for the DingTalk channel — Stream Mode inbound + OAPI outbound.

Layout:
  - send library    → flocks.channel.builtin.dingtalk.{config,client,send}
  - stream inbound  → flocks.channel.builtin.dingtalk.stream
  - channel plugin  → flocks.channel.builtin.dingtalk.channel.DingTalkChannel
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
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
# Builtin DingTalk channel plugin (Stream Mode)
# ------------------------------------------------------------------

class TestBuiltinChannelPlugin:
    def test_dingtalk_in_builtin_registry(self):
        from flocks.channel.registry import ChannelRegistry
        reg = ChannelRegistry()
        reg._register_builtin_channels()
        plugin = reg.get("dingtalk")
        assert plugin is not None, "DingTalkChannel must be registered as a builtin"
        assert plugin.meta().id == "dingtalk"

    def test_builtin_channel_module_exists(self):
        spec = importlib.util.find_spec(
            "flocks.channel.builtin.dingtalk.channel"
        )
        assert spec is not None

    def test_validate_config_accepts_client_id_alias(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        plugin = DingTalkChannel()
        assert plugin.validate_config({
            "clientId": "k", "clientSecret": "s",
        }) is None

    def test_validate_config_rejects_missing_credentials(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        plugin = DingTalkChannel()
        error = plugin.validate_config({})
        assert error is not None
        assert "appKey" in error or "credentials" in error.lower()

    def test_meta_aliases(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        meta = DingTalkChannel().meta()
        assert "dingding" in meta.aliases

    @pytest.mark.asyncio
    async def test_send_text_delegates_to_send_message_app(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        plugin = DingTalkChannel()
        plugin._config = {"clientId": "k", "clientSecret": "s"}
        ctx = OutboundContext(
            channel_id="dingtalk",
            to="user:staff_001",
            text="hello",
        )

        captured: dict = {}

        async def fake_send(**kwargs):
            captured.update(kwargs)
            return {"message_id": "mid", "chat_id": "staff_001"}

        with patch(
            "flocks.channel.builtin.dingtalk.send_message_app",
            new=fake_send,
        ):
            result = await plugin.send_text(ctx)

        assert result.success is True
        assert result.message_id == "mid"
        assert captured["to"] == "user:staff_001"
        assert captured["text"] == "hello"

    @pytest.mark.asyncio
    async def test_send_text_rejects_empty_target(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        plugin = DingTalkChannel()
        plugin._config = {"clientId": "k", "clientSecret": "s"}
        ctx = OutboundContext(channel_id="dingtalk", to="", text="x")

        result = await plugin.send_text(ctx)

        assert result.success is False
        assert "to" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_wait_until_done_handles_gather_future(self):
        """Regression: ``_wait_until_done`` must not pass the
        ``_GatheringFuture`` returned by ``asyncio.gather()`` to
        ``asyncio.create_task``.  Symptom in production:

            "a coroutine was expected, got <_GatheringFuture pending>"

        was raised on every reconnect, looping the gateway forever.
        """
        from flocks.channel.builtin.dingtalk import DingTalkChannel

        plugin = DingTalkChannel()

        async def _quick_runner():
            return None

        plugin._runner_tasks = [
            asyncio.create_task(_quick_runner()),
            asyncio.create_task(_quick_runner()),
        ]
        abort_event = asyncio.Event()

        # Should complete cleanly when all runners finish — no TypeError.
        await asyncio.wait_for(
            plugin._wait_until_done(abort_event), timeout=2.0,
        )

    @pytest.mark.asyncio
    async def test_wait_until_done_returns_when_abort_fires(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel

        plugin = DingTalkChannel()

        async def _slow_runner():
            await asyncio.sleep(60)

        plugin._runner_tasks = [asyncio.create_task(_slow_runner())]
        abort_event = asyncio.Event()

        async def _fire_abort():
            await asyncio.sleep(0.05)
            abort_event.set()

        asyncio.create_task(_fire_abort())
        await asyncio.wait_for(
            plugin._wait_until_done(abort_event), timeout=2.0,
        )

        # Caller is responsible for cancelling runners; verify the
        # waiter returns even though the runner is still pending.
        plugin._runner_tasks[0].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await plugin._runner_tasks[0]

    @pytest.mark.asyncio
    async def test_wait_until_done_suppresses_permanent_auth_error(self):
        """Permanent auth errors must NOT propagate from _wait_until_done
        — otherwise the gateway would reconnect indefinitely with the
        same bad credentials.
        """
        from flocks.channel.builtin.dingtalk import DingTalkChannel
        from flocks.channel.builtin.dingtalk.stream import (
            DingTalkPermanentAuthError,
        )

        plugin = DingTalkChannel()

        async def _failing_runner():
            raise DingTalkPermanentAuthError(
                "bad creds", code="InvalidAuthentication", http_status=401,
            )

        plugin._runner_tasks = [asyncio.create_task(_failing_runner())]
        abort_event = asyncio.Event()

        # Must return cleanly, not raise.
        await asyncio.wait_for(
            plugin._wait_until_done(abort_event), timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_wait_until_done_propagates_transient_error(self):
        from flocks.channel.builtin.dingtalk import DingTalkChannel

        plugin = DingTalkChannel()

        async def _failing_runner():
            raise RuntimeError("network hiccup")

        plugin._runner_tasks = [asyncio.create_task(_failing_runner())]
        abort_event = asyncio.Event()

        with pytest.raises(RuntimeError, match="network hiccup"):
            await asyncio.wait_for(
                plugin._wait_until_done(abort_event), timeout=1.0,
            )

    @pytest.mark.asyncio
    async def test_wait_until_done_treats_external_cancel_as_transient(self):
        """Regression: when ``plugin.stop()`` cancels runners while
        ``abort_event`` is still clear (concurrent restart race in the
        gateway), ``_wait_until_done`` must surface a transient error
        — NOT return cleanly.

        Returning cleanly there was the production bug observed in
        ``backend.log``: the gateway's ``_run_with_reconnect`` then
        misinterpreted the fast clean return as "webhook / passive
        mode" and parked on ``abort_event.wait()`` forever, so DingTalk
        never reconnected and stopped receiving messages.
        """
        from flocks.channel.builtin.dingtalk import DingTalkChannel

        plugin = DingTalkChannel()

        async def _slow_runner():
            await asyncio.sleep(60)

        plugin._runner_tasks = [asyncio.create_task(_slow_runner())]
        abort_event = asyncio.Event()  # deliberately NOT set

        async def _external_cancel():
            await asyncio.sleep(0.05)
            for t in plugin._runner_tasks:
                t.cancel()

        asyncio.create_task(_external_cancel())

        with pytest.raises(RuntimeError, match="concurrent stop/restart"):
            await asyncio.wait_for(
                plugin._wait_until_done(abort_event), timeout=2.0,
            )

    @pytest.mark.asyncio
    async def test_wait_until_done_silent_when_cancel_after_abort(self):
        """When abort fires *first* and runners are cancelled afterwards
        (the normal shutdown path), no exception should be raised — the
        cancellation is expected and the gateway is already breaking out.
        """
        from flocks.channel.builtin.dingtalk import DingTalkChannel

        plugin = DingTalkChannel()

        async def _slow_runner():
            await asyncio.sleep(60)

        plugin._runner_tasks = [asyncio.create_task(_slow_runner())]
        abort_event = asyncio.Event()

        async def _abort_then_cancel():
            await asyncio.sleep(0.05)
            abort_event.set()
            await asyncio.sleep(0)  # let _wait_until_done observe abort
            for t in plugin._runner_tasks:
                t.cancel()

        asyncio.create_task(_abort_then_cancel())

        # No exception expected — abort fired before cancel, so the
        # gateway will break out of its loop normally.
        await asyncio.wait_for(
            plugin._wait_until_done(abort_event), timeout=2.0,
        )

        # Drain the cancelled runner
        with contextlib.suppress(asyncio.CancelledError):
            await plugin._runner_tasks[0]


# ------------------------------------------------------------------
# Stream Mode helpers — gating + message extraction
# ------------------------------------------------------------------

class TestStreamHelpers:
    def test_message_gate_dm_always_processes(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({"requireMention": True})
        assert gate.should_process(SimpleNamespace(), "hi", is_group=False, chat_id="x")

    def test_message_gate_group_requires_mention_when_enabled(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({"requireMention": True})
        msg_no_mention = SimpleNamespace(is_in_at_list=False)
        msg_mention = SimpleNamespace(is_in_at_list=True)
        assert not gate.should_process(msg_no_mention, "hi", is_group=True, chat_id="g")
        assert gate.should_process(msg_mention, "hi", is_group=True, chat_id="g")

    def test_message_gate_free_response_chats_bypass_mention_check(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({
            "requireMention": True,
            "freeResponseChats": ["cidABC"],
        })
        msg = SimpleNamespace(is_in_at_list=False)
        assert gate.should_process(msg, "hi", is_group=True, chat_id="cidABC")

    def test_message_gate_mention_pattern_match(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({
            "requireMention": True,
            "mentionPatterns": ["^小马"],
        })
        msg = SimpleNamespace(is_in_at_list=False)
        assert gate.should_process(msg, "小马 你好", is_group=True, chat_id="g")
        assert not gate.should_process(msg, "你好", is_group=True, chat_id="g")

    def test_message_gate_allowed_users_wildcard(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({"allowedUsers": ["*"]})
        assert gate.is_user_allowed("anyone", "")

    def test_message_gate_allowed_users_exact_match(self):
        from flocks.channel.builtin.dingtalk.stream import _MessageGate
        gate = _MessageGate({"allowedUsers": ["staff_001"]})
        assert gate.is_user_allowed("u1", "staff_001")
        assert not gate.is_user_allowed("u1", "staff_002")

    def test_chatbot_message_to_inbound_extracts_text(self):
        from flocks.channel.builtin.dingtalk.stream import (
            chatbot_message_to_inbound,
        )
        message = SimpleNamespace(
            text=SimpleNamespace(content="hello world"),
            conversation_id="cid_001",
            conversation_type="2",  # group
            sender_id="u1",
            sender_staff_id="staff_001",
            sender_nick="Alice",
            message_id="msg_1",
            is_in_at_list=True,
        )
        inbound = chatbot_message_to_inbound(
            message, channel_id="dingtalk", account_id="default",
        )
        assert inbound is not None
        assert inbound.text == "hello world"
        assert inbound.chat_id == "cid_001"
        assert inbound.chat_type is ChatType.GROUP
        assert inbound.sender_id == "staff_001"
        assert inbound.sender_name == "Alice"
        assert inbound.mentioned is True
        assert inbound.message_id == "msg_1"

    @pytest.mark.asyncio
    async def test_preflight_raises_on_invalid_credentials(self):
        """4xx responses with auth-related codes must abort fast."""
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        class _FakeResp:
            def __init__(self):
                self.status_code = 401
                self.content = b'{"code":"InvalidAuthentication","message":"bad secret"}'
                self.text = self.content.decode()
                self.request = None

            def json(self):
                return {"code": "InvalidAuthentication", "message": "bad secret"}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _FakeResp()

        with patch.object(stream_mod.httpx, "AsyncClient", _FakeClient):
            with pytest.raises(stream_mod.DingTalkPermanentAuthError) as exc_info:
                await stream_mod._preflight_open_connection(
                    client_id="bad", client_secret="bad",
                )
        assert exc_info.value.http_status == 401
        assert exc_info.value.code == "InvalidAuthentication"

    @pytest.mark.asyncio
    async def test_preflight_passes_on_2xx(self):
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        class _OkResp:
            status_code = 200
            content = b'{"endpoint":"wss://x","ticket":"t"}'
            text = content.decode()
            request = None

            def json(self):
                return {"endpoint": "wss://x", "ticket": "t"}

        class _OkClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _OkResp()

        with patch.object(stream_mod.httpx, "AsyncClient", _OkClient):
            await stream_mod._preflight_open_connection(
                client_id="ok", client_secret="ok",
            )

    @pytest.mark.asyncio
    async def test_preflight_5xx_is_transient(self):
        """5xx must NOT be flagged permanent — the SDK / outer loop
        should be allowed to retry."""
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        class _Err5xx:
            status_code = 503
            content = b"unavailable"
            text = "unavailable"
            request = None

            def json(self):
                raise ValueError

        class _Client5xx:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _Err5xx()

        with patch.object(stream_mod.httpx, "AsyncClient", _Client5xx):
            with pytest.raises(stream_mod.httpx.HTTPStatusError):
                await stream_mod._preflight_open_connection(
                    client_id="x", client_secret="x",
                )

    def test_chatbot_message_to_inbound_dm_uses_staff_id_as_chat_id(self):
        """Regression: DM ``chat_id`` MUST be the staff_id, NOT the
        ``conversation_id``.

        Production symptom (logs ``2026-04-23T112342.log``):

            POST /v1.0/robot/groupMessages/send: 错误描述: robot 不存在；
            解决方案:请确认 robotCode 是否正确

        DingTalk delivers a ``cid…`` ``conversation_id`` for DMs as
        well as groups, but only the group endpoint accepts it.  Using
        ``conversation_id`` for DMs makes ``resolve_target_kind`` see
        the ``cid`` prefix and route through ``/groupMessages/send``,
        which fails with the above error.  The fix routes DMs through
        ``staffId`` → ``/oToMessages/batchSend`` instead.
        """
        from flocks.channel.builtin.dingtalk.config import resolve_target_kind
        from flocks.channel.builtin.dingtalk.stream import (
            chatbot_message_to_inbound,
        )
        message = SimpleNamespace(
            text=SimpleNamespace(content="Hi"),
            conversation_id="cidnrPnAQNfmP4fZCcLfRxZtv43vx736",
            conversation_type="1",  # DM
            sender_id="$:LWCP_v1:$opaqueSenderId",
            sender_staff_id="2250583914922119",
            sender_nick="熊剑",
            message_id="msg_dm_1",
        )
        inbound = chatbot_message_to_inbound(
            message, channel_id="dingtalk", account_id="default",
        )
        assert inbound is not None
        assert inbound.chat_type is ChatType.DIRECT
        # The critical assertion: chat_id must NOT be the conversation_id
        # for DMs, because that would route the reply to /groupMessages/send.
        assert inbound.chat_id == "2250583914922119"
        # And the resolver must treat it as a 1:1 user target.
        assert resolve_target_kind(inbound.chat_id) == "user"

    def test_chatbot_message_to_inbound_group_keeps_conversation_id(self):
        from flocks.channel.builtin.dingtalk.config import resolve_target_kind
        from flocks.channel.builtin.dingtalk.stream import (
            chatbot_message_to_inbound,
        )
        message = SimpleNamespace(
            text=SimpleNamespace(content="@bot hi"),
            conversation_id="cidGROUP123",
            conversation_type="2",  # group
            sender_id="u1",
            sender_staff_id="staff_001",
            sender_nick="Alice",
            message_id="msg_g_1",
            is_in_at_list=True,
        )
        inbound = chatbot_message_to_inbound(
            message, channel_id="dingtalk", account_id="default",
        )
        assert inbound is not None
        assert inbound.chat_type is ChatType.GROUP
        assert inbound.chat_id == "cidGROUP123"
        assert resolve_target_kind(inbound.chat_id) == "group"

    def test_resolve_chat_id_dm_prefers_staff_id(self):
        from flocks.channel.builtin.dingtalk.stream import _resolve_chat_id
        msg = SimpleNamespace(
            conversation_id="cidABC",
            sender_id="$:LWCP_v1:$opaque",
            sender_staff_id="2250583914922119",
        )
        assert _resolve_chat_id(msg, is_group=False) == "2250583914922119"

    def test_resolve_chat_id_dm_falls_back_when_no_staff_id(self):
        """When DingTalk omits ``sender_staff_id`` (rare but possible
        for external/unverified users) we fall back to ``sender_id`` —
        and only as a last resort to ``conversation_id``.
        """
        from flocks.channel.builtin.dingtalk.stream import _resolve_chat_id
        msg = SimpleNamespace(
            conversation_id="cidABC",
            sender_id="user_42",
            sender_staff_id="",
        )
        assert _resolve_chat_id(msg, is_group=False) == "user_42"

        msg2 = SimpleNamespace(
            conversation_id="cidABC",
            sender_id="",
            sender_staff_id="",
        )
        assert _resolve_chat_id(msg2, is_group=False) == "cidABC"

    def test_resolve_chat_id_group_uses_conversation_id(self):
        from flocks.channel.builtin.dingtalk.stream import _resolve_chat_id
        msg = SimpleNamespace(
            conversation_id="cidGROUP123",
            sender_id="u1",
            sender_staff_id="staff_001",
        )
        assert _resolve_chat_id(msg, is_group=True) == "cidGROUP123"

    def test_dispatch_and_inbound_agree_on_chat_id(self):
        """Regression: ``_dispatch`` (used for gating) and
        ``chatbot_message_to_inbound`` (used for routing) MUST agree on
        the ``chat_id`` for the same message — otherwise an admin who
        whitelists a chat in ``free_response_chats`` could see gating
        accept the message but the reply land in a different chat (or
        worse, fail with ``robot 不存在``).
        """
        from flocks.channel.builtin.dingtalk.stream import (
            _is_group_message,
            _resolve_chat_id,
            chatbot_message_to_inbound,
        )
        dm = SimpleNamespace(
            text=SimpleNamespace(content="hello"),
            conversation_id="cidDM",
            conversation_type="1",
            sender_id="u_dm",
            sender_staff_id="staff_dm",
            sender_nick="Bob",
            message_id="m_dm",
        )
        inbound_dm = chatbot_message_to_inbound(
            dm, channel_id="dingtalk", account_id="default",
        )
        assert inbound_dm is not None
        assert inbound_dm.chat_id == _resolve_chat_id(
            dm, is_group=_is_group_message(dm),
        )

        group = SimpleNamespace(
            text=SimpleNamespace(content="@bot hi"),
            conversation_id="cidGRP",
            conversation_type="2",
            sender_id="u_g",
            sender_staff_id="staff_g",
            sender_nick="Carol",
            message_id="m_g",
            is_in_at_list=True,
        )
        inbound_group = chatbot_message_to_inbound(
            group, channel_id="dingtalk", account_id="default",
        )
        assert inbound_group is not None
        assert inbound_group.chat_id == _resolve_chat_id(
            group, is_group=_is_group_message(group),
        )

    def test_chatbot_message_to_inbound_returns_none_when_empty(self):
        from flocks.channel.builtin.dingtalk.stream import (
            chatbot_message_to_inbound,
        )
        message = SimpleNamespace(
            text=SimpleNamespace(content=""),
            conversation_id="cid_001",
            conversation_type="1",
            sender_id="u1",
            sender_staff_id="",
            sender_nick="",
            message_id="msg_2",
        )
        assert chatbot_message_to_inbound(
            message, channel_id="dingtalk", account_id="default",
        ) is None


# ------------------------------------------------------------------
# Resilience regressions: R1 (silent stall) + R3 (back-pressure)
# ------------------------------------------------------------------

@pytest.mark.skipif(
    importlib.util.find_spec("dingtalk_stream") is None,
    reason="dingtalk-stream SDK not installed",
)
class TestStreamRunnerResilience:
    """End-to-end tests for the runner's failure-mode safeguards.

    These exercise the loop in :meth:`DingTalkStreamRunner._run_with_reconnect`
    by patching :meth:`DingTalkStreamClient.start` so we can drive
    ``clean return`` / ``raised`` / ``slow`` scenarios deterministically
    without touching the network.  The pre-flight HTTP check is also
    short-circuited.
    """

    def _make_runner(self, *, on_message=None, account_overrides=None):
        from flocks.channel.builtin.dingtalk.stream import DingTalkStreamRunner

        config = {
            "_account_id": "default",
            "appKey": "key",
            "appSecret": "secret",
        }
        if account_overrides:
            config.update(account_overrides)
        runner = DingTalkStreamRunner(
            account_config=config,
            on_message=on_message or AsyncMock(),
        )
        return runner

    @pytest.mark.asyncio
    async def test_stall_detection_escalates_after_consecutive_short_clean_returns(
        self, monkeypatch,
    ):
        """R1 regression: SDK ``start()`` returning instantly with zero
        messages, repeated N times, MUST raise
        :class:`DingTalkStreamStallError` so the channel layer pauses
        reconnects on this account.

        Production symptom (without this guard): the runner burns one
        preflight + one reconnect cycle every ~2-60s forever, never
        delivering a message and never surfacing a permanent error.
        """
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        # Skip pre-flight; we're testing the SDK's own behaviour.
        async def _ok_preflight(**_kw):
            return None

        monkeypatch.setattr(
            stream_mod, "_preflight_open_connection", _ok_preflight
        )
        # Collapse backoff so the test runs in milliseconds, not minutes.
        monkeypatch.setattr(stream_mod, "_RECONNECT_BACKOFF", [0])

        runner = self._make_runner()

        # Fake stream client whose ``start()`` returns immediately every
        # time — the exact pathology we want to detect.
        class _ImmediateStartClient:
            def __init__(self, *_a, **_kw):
                self.start_calls = 0
                self.websocket = None

            def register_callback_handler(self, *_a, **_kw):
                pass

            async def start(self):
                self.start_calls += 1

            def close(self):
                pass

        monkeypatch.setattr(
            stream_mod.dingtalk_stream, "DingTalkStreamClient",
            _ImmediateStartClient,
        )

        with pytest.raises(stream_mod.DingTalkStreamStallError) as exc_info:
            await asyncio.wait_for(runner.run(), timeout=2.0)

        # Threshold is 5 — confirm we escalate exactly at the boundary
        # rather than after some larger arbitrary number of retries.
        assert exc_info.value.consecutive_short_runs == 5
        # And it MUST be a subclass of DingTalkPermanentError so
        # channel.py's ``_classify_and_raise`` will swallow it (no
        # retry) instead of re-raising for the gateway.
        assert isinstance(exc_info.value, stream_mod.DingTalkPermanentError)

    @pytest.mark.asyncio
    async def test_stall_counter_resets_after_inbound_message(self, monkeypatch):
        """A single healthy run (with messages delivered) MUST reset
        the short-run counter — otherwise a busy account that
        occasionally has a < 30s reconnect window would slowly
        accumulate strikes and eventually be killed wrongly.
        """
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        async def _ok_preflight(**_kw):
            return None

        monkeypatch.setattr(
            stream_mod, "_preflight_open_connection", _ok_preflight
        )
        monkeypatch.setattr(stream_mod, "_RECONNECT_BACKOFF", [0])

        runner = self._make_runner()
        # Pre-load a few "short runs" — half of the threshold.
        runner._consecutive_short_runs = 3

        call_count = {"n": 0}

        class _MixedClient:
            def __init__(self, *_a, **_kw):
                self.websocket = None

            def register_callback_handler(self, *_a, **_kw):
                pass

            async def start(self):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    # Simulate a healthy run: deliver one message,
                    # then return cleanly.  The runner counts
                    # ``_messages_received`` directly, so we bump it
                    # before returning to mimic an inbound frame.
                    runner._messages_received += 1
                    return
                # On the 2nd call, stop the runner so the loop exits
                # without further iterations.
                runner._running = False

            def close(self):
                pass

        monkeypatch.setattr(
            stream_mod.dingtalk_stream, "DingTalkStreamClient",
            _MixedClient,
        )

        await asyncio.wait_for(runner.run(), timeout=2.0)
        # Counter MUST have reset after the run that delivered a message.
        assert runner._consecutive_short_runs == 0

    @pytest.mark.asyncio
    async def test_dispatch_queue_drops_overflow_without_blocking(
        self, monkeypatch,
    ):
        """R3 regression: when the dispatch queue saturates, new
        messages MUST be dropped (not blocked, not stacked as
        unbounded tasks) so the SDK's ``process()`` ack path stays
        non-blocking and heartbeats keep flowing.

        We size the queue + worker pool down to 1 each and pin the
        single worker on a slow ``on_message`` so we can deterministically
        push the queue into the QueueFull branch.
        """
        from flocks.channel.builtin.dingtalk import stream as stream_mod

        # The on_message handler hangs until released — guarantees the
        # single worker is busy when we enqueue the second message.
        release = asyncio.Event()
        first_received = asyncio.Event()
        seen: list = []

        async def _slow_on_message(msg):
            seen.append(msg)
            first_received.set()
            await release.wait()

        runner = self._make_runner(
            on_message=_slow_on_message,
            account_overrides={
                "dispatchWorkers": 1,
                "dispatchQueueSize": 1,
            },
        )

        # We don't want to actually open a websocket, so manually
        # bootstrap just the queue + worker pool — same setup the
        # ``run()`` entry point performs.  This keeps the test focused
        # on the back-pressure invariant.
        runner._dispatch_queue = asyncio.Queue(
            maxsize=runner._dispatch_queue_size,
        )
        runner._worker_tasks = [
            asyncio.create_task(runner._dispatch_worker(0))
        ]

        try:
            # Build three "messages" that pass the gate trivially.  We
            # use bare SimpleNamespace because the gate happily accepts
            # any object exposing the expected attributes.
            def _msg(text):
                return SimpleNamespace(
                    text=SimpleNamespace(content=text),
                    conversation_id="cid_dm",
                    conversation_type="1",  # DM → unconditionally accepted
                    sender_id="u1",
                    sender_staff_id="staff_001",
                    sender_nick="Alice",
                    message_id=f"m_{text}",
                    is_in_at_list=False,
                )

            runner._enqueue_dispatch(_msg("a"))   # consumed by worker
            await first_received.wait()
            runner._enqueue_dispatch(_msg("b"))   # parked in queue (size=1)
            runner._enqueue_dispatch(_msg("c"))   # MUST be dropped

            # _messages_received counts EVERY frame the SDK delivered —
            # that's the right metric for stall detection (R1) even when
            # back-pressure is sheding.
            assert runner._messages_received == 3
            # The third message was dropped, not blocked, not crashed.
            assert runner._dropped_messages == 1

            release.set()
            # Drain whatever the worker can finish before we cancel.
            for _ in range(20):
                if len(seen) >= 2:
                    break
                await asyncio.sleep(0.01)
            # Worker processed exactly the 2 that fit (a + b); c was dropped.
            assert len(seen) == 2
        finally:
            for task in runner._worker_tasks:
                task.cancel()
            await asyncio.gather(
                *runner._worker_tasks, return_exceptions=True,
            )

    @pytest.mark.asyncio
    async def test_enqueue_before_pool_started_does_not_crash(self):
        """Calling ``_enqueue_dispatch`` before ``run()`` initialises
        the queue (or after ``_shutdown()`` tore it down) must not
        raise — we just log + drop.  Guards against rare ordering bugs
        where the SDK pushes a frame during teardown.
        """
        runner = self._make_runner()
        assert runner._dispatch_queue is None

        runner._enqueue_dispatch(SimpleNamespace(text="orphan"))

        # Counter ticks even though we shed — preserves the R1 stall
        # signal in case a frame slips through during shutdown.
        assert runner._messages_received == 1
        assert runner._dropped_messages == 0  # no QueueFull, just no queue

    def test_permanent_error_hierarchy(self):
        """``DingTalkStreamStallError`` MUST inherit from
        ``DingTalkPermanentError`` so :meth:`DingTalkChannel._classify_and_raise`
        treats stalls the same as auth failures (drop the account from
        the schedule rather than letting the gateway retry forever).
        """
        from flocks.channel.builtin.dingtalk.stream import (
            DingTalkPermanentAuthError,
            DingTalkPermanentError,
            DingTalkStreamStallError,
        )

        assert issubclass(DingTalkPermanentAuthError, DingTalkPermanentError)
        assert issubclass(DingTalkStreamStallError, DingTalkPermanentError)
        # Belt-and-braces: make sure both still descend from RuntimeError
        # so any generic ``except RuntimeError`` in calling code
        # continues to work.
        assert issubclass(DingTalkPermanentError, RuntimeError)


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

    def test_bind_endpoint_rejects_group_sender_composite_key(self, client, monkeypatch):
        """group_sender mode builds peerId = `<conversationId>:<senderId>`;
        that composite is only valid for session isolation, not as a send
        target.  The endpoint must refuse to persist it so the bug cannot
        regress into the bindings table.
        """
        from flocks.channel.inbound import session_binding as sb_mod

        called = {"count": 0}

        async def _unexpected_bind(self, **_):
            called["count"] += 1

        monkeypatch.setattr(
            sb_mod.SessionBindingService, "bind_session", _unexpected_bind,
        )

        resp = client.post(
            "/api/channel/dingtalk/bind",
            json={
                "session_id": "ses_42",
                "chat_id": "cidXXXX:staff_001",  # group_sender composite
                "chat_type": "group",
            },
        )

        assert resp.status_code == 400
        body = resp.json()
        assert "composite" in body["detail"].lower()
        # Must NOT have reached the service: the check is meant to prevent
        # the bad row from ever being written.
        assert called["count"] == 0

    def test_bind_endpoint_accepts_colon_in_direct_targets(self, client, monkeypatch):
        """Some platforms embed ':' in user IDs (namespacing, e.g. feishu's
        ``user:open_id``).  The composite-key guard must only fire for
        *group* chats, never for direct ones.
        """
        from flocks.channel.inbound import session_binding as sb_mod

        async def fake_bind(self, **kwargs):
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
                "chat_id": "user:staff_001",
                "chat_type": "direct",
            },
        )
        assert resp.status_code == 200, resp.text
