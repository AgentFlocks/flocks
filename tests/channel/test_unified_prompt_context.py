"""
Regression tests for the WebUI / IM-channel prompt-context unification.

These cover the three knobs introduced to make Rex see the same system
prompt regardless of whether the user message arrived through the Web UI
or through an IM channel (Feishu / WeCom / DingTalk):

1. ``session.directory`` — channel sessions now follow the same priority
   chain as the WebUI ``Session.create`` route:
   ``ChannelConfig.workspaceDir`` → ``Instance.get_directory()`` →
   ``os.getcwd()``.
2. ``default_agent`` — when the channel config does not specify an agent,
   the dispatcher falls back to ``Agent.default_agent()`` (which honours
   the global ``defaultAgent`` config and finally returns ``rex``) instead
   of the bogus literal ``"default"`` that previously slipped through.
3. ``model`` — the channel dispatcher now resolves the provider/model via
   ``SessionLoop._resolve_model`` and persists it on the user message,
   matching what the WebUI ``_process_session_message`` route does. This
   is what keeps title generation and the ``SystemPrompt.provider``
   template selection consistent on the first turn.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.inbound import session_binding as binding_mod
from flocks.channel.inbound.dispatcher import (
    InboundDispatcher,
    _resolve_session_model,
)
from flocks.channel.inbound.session_binding import (
    SessionBindingService,
    _resolve_session_directory,
)
from flocks.config.config import ChannelConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(**overrides) -> InboundMessage:
    base = dict(
        channel_id="wecom",
        account_id="default",
        message_id="m1",
        sender_id="user_a",
        sender_name="Alice",
        chat_id="user_a",
        chat_type=ChatType.DIRECT,
        text="hello",
    )
    base.update(overrides)
    return InboundMessage(**base)


# ---------------------------------------------------------------------------
# 1. session.directory unification
# ---------------------------------------------------------------------------

class TestResolveSessionDirectory:
    def test_explicit_wins(self):
        assert _resolve_session_directory("/srv/proj") == "/srv/proj"

    def test_falls_back_to_instance_directory(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value="/workspace/foo",
        ):
            assert _resolve_session_directory(None) == "/workspace/foo"

    def test_falls_back_to_cwd_when_instance_unavailable(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value=None,
        ):
            assert _resolve_session_directory(None) == os.getcwd()

    def test_swallows_instance_lookup_errors(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            side_effect=RuntimeError("no instance"),
        ):
            assert _resolve_session_directory(None) == os.getcwd()


class TestChannelConfigWorkspaceDir:
    def test_workspace_dir_default_is_none(self):
        cfg = ChannelConfig()
        assert cfg.workspace_dir is None

    def test_workspace_dir_alias(self):
        cfg = ChannelConfig(workspaceDir="/data/proj")
        assert cfg.workspace_dir == "/data/proj"

    def test_workspace_dir_snake_case(self):
        cfg = ChannelConfig(workspace_dir="/data/proj")
        assert cfg.workspace_dir == "/data/proj"


class TestSessionBindingDirectoryPropagation:
    @pytest.mark.asyncio
    async def test_create_session_uses_explicit_directory(self):
        captured = {}

        class _StubSession:
            id = "ses_1"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubSession()

        with patch("flocks.session.session.Session.create", new=_fake_create):
            sid = await SessionBindingService._create_session(
                _msg(),
                default_agent="rex",
                directory="/explicit/dir",
            )

        assert sid == "ses_1"
        assert captured["directory"] == "/explicit/dir"
        assert captured["project_id"] == "channel"
        assert captured["agent"] == "rex"

    @pytest.mark.asyncio
    async def test_create_session_falls_back_to_instance(self):
        captured = {}

        class _StubSession:
            id = "ses_2"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubSession()

        with patch("flocks.session.session.Session.create", new=_fake_create), \
             patch(
                 "flocks.project.instance.Instance.get_directory",
                 return_value="/instance/dir",
             ):
            await SessionBindingService._create_session(
                _msg(), default_agent="rex", directory=None,
            )

        assert captured["directory"] == "/instance/dir"


# ---------------------------------------------------------------------------
# 2. default_agent unification
# ---------------------------------------------------------------------------

class TestDispatcherDefaultAgentResolution:
    """The dispatcher logic that turns a missing channel config into the
    same default agent that the WebUI uses."""

    @pytest.mark.asyncio
    async def test_channel_config_default_agent_takes_precedence(self):
        # When the channel config explicitly names an agent, we MUST honour
        # it instead of consulting Agent.default_agent().
        cfg = ChannelConfig(defaultAgent="security_helper")

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(return_value="rex"),
        ) as mock_default:
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "security_helper"
            mock_default.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_agent_default_agent(self):
        cfg = ChannelConfig()  # no defaultAgent

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(return_value="custom_default"),
        ):
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "custom_default"

    @pytest.mark.asyncio
    async def test_falls_back_to_rex_when_resolver_raises(self):
        cfg = ChannelConfig()

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "rex"


async def _call_default_agent_fallback() -> str:
    """Mirror the resolver branch inside InboundDispatcher.dispatch."""
    try:
        from flocks.agent.registry import Agent as _Agent
        return await _Agent.default_agent()
    except Exception:
        return "rex"


# ---------------------------------------------------------------------------
# 3. model unification
# ---------------------------------------------------------------------------

class TestResolveSessionModel:
    @pytest.mark.asyncio
    async def test_returns_provider_model_dict(self):
        fake_session = SimpleNamespace(
            id="ses_x", provider=None, model=None, agent="rex",
            parent_id=None, project_id="channel",
        )

        async def _fake_get(_sid):
            return fake_session

        async def _fake_resolve(_session, _p, _m):
            return ("anthropic", "claude-sonnet-4-5")

        with patch("flocks.session.session.Session.get_by_id", new=_fake_get), \
             patch(
                 "flocks.session.session_loop.SessionLoop._resolve_model",
                 new=_fake_resolve,
             ):
            resolved = await _resolve_session_model("ses_x", "rex")

        assert resolved == {
            "providerID": "anthropic",
            "modelID": "claude-sonnet-4-5",
        }

    @pytest.mark.asyncio
    async def test_returns_none_when_session_missing(self):
        async def _missing(_sid):
            return None

        with patch("flocks.session.session.Session.get_by_id", new=_missing):
            assert await _resolve_session_model("ghost", "rex") is None

    @pytest.mark.asyncio
    async def test_swallows_resolution_errors(self):
        async def _fake_get(_sid):
            return SimpleNamespace(id="ses_y")

        async def _boom(_session, _p, _m):
            raise RuntimeError("provider not configured")

        with patch("flocks.session.session.Session.get_by_id", new=_fake_get), \
             patch(
                 "flocks.session.session_loop.SessionLoop._resolve_model",
                 new=_boom,
             ):
            assert await _resolve_session_model("ses_y", "rex") is None


class TestAppendUserMessagePersistsModel:
    @pytest.mark.asyncio
    async def test_model_is_passed_to_message_create(self):
        captured = {}

        class _StubMessage:
            id = "msg_1"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_1",
                "hi",
                _msg(),
                channel_config=None,
                model={"providerID": "anthropic", "modelID": "claude-sonnet-4-5"},
            )

        assert captured["model"] == {
            "providerID": "anthropic",
            "modelID": "claude-sonnet-4-5",
        }
        assert captured["content"] == "hi"
        # part_metadata must still be propagated (audit fields)
        assert captured["part_metadata"]["source"] == "channel"
        assert captured["part_metadata"]["channel_id"] == "wecom"

    @pytest.mark.asyncio
    async def test_omits_model_when_none(self):
        captured = {}

        class _StubMessage:
            id = "msg_2"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_2", "hi", _msg(), channel_config=None,
            )

        assert "model" not in captured
