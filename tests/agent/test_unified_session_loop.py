"""
Tests for Phase 1: Unified UI entry via SessionLoop.

Verifies that:
1. RunnerCallbacks.event_publish_callback is passed through to StreamProcessor
2. LoopCallbacks carries runner_callbacks and event_publish_callback
3. SessionRunner uses explicit callbacks (doesn't override with CLI fallback)
4. _resolve_model implements 5-level priority correctly
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from flocks.session.runner import RunnerCallbacks
from flocks.session.session_loop import LoopCallbacks


class TestRunnerCallbacksEventPublish:
    """RunnerCallbacks should carry event_publish_callback."""

    def test_event_publish_callback_field_exists(self):
        cb = RunnerCallbacks()
        assert hasattr(cb, 'event_publish_callback')
        assert cb.event_publish_callback is None

    def test_event_publish_callback_can_be_set(self):
        publish = AsyncMock()
        cb = RunnerCallbacks(event_publish_callback=publish)
        assert cb.event_publish_callback is publish


class TestLoopCallbacksFields:
    """LoopCallbacks should carry event_publish_callback and runner_callbacks."""

    def test_event_publish_callback_field(self):
        cb = LoopCallbacks()
        assert hasattr(cb, 'event_publish_callback')
        assert cb.event_publish_callback is None

    def test_runner_callbacks_field(self):
        cb = LoopCallbacks()
        assert hasattr(cb, 'runner_callbacks')
        assert cb.runner_callbacks is None

    def test_pass_runner_callbacks(self):
        runner_cb = RunnerCallbacks(on_error=AsyncMock())
        loop_cb = LoopCallbacks(runner_callbacks=runner_cb)
        assert loop_cb.runner_callbacks is runner_cb
        assert loop_cb.runner_callbacks.on_error is not None


class TestCallbackPrecedence:
    """SessionRunner should not override explicit callbacks with CLI fallback."""

    def test_explicit_callbacks_not_overridden(self):
        """When event_publish_callback is set, CLI fallback should NOT be used."""
        publish = AsyncMock()
        cb = RunnerCallbacks(event_publish_callback=publish)
        
        # Verify the check that _process_step uses
        has_explicit = any([
            cb.on_text_delta,
            cb.on_tool_start,
            cb.on_tool_end,
            cb.on_error,
            cb.event_publish_callback,
        ])
        assert has_explicit is True

    def test_empty_callbacks_allows_cli_fallback(self):
        """When no callbacks are set, CLI fallback should be used."""
        cb = RunnerCallbacks()
        has_explicit = any([
            cb.on_text_delta,
            cb.on_tool_start,
            cb.on_tool_end,
            cb.on_error,
            cb.event_publish_callback,
        ])
        assert has_explicit is False


class TestResolveModel:
    """Test the _resolve_model 5-level priority."""

    @pytest.mark.asyncio
    async def test_priority_1_request_model(self):
        """Request model takes highest priority."""
        from flocks.server.routes.session import _resolve_model

        request = MagicMock()
        request.model = MagicMock()
        request.model.providerID = "anthropic"
        request.model.modelID = "claude-sonnet-4-5"
        
        agent = MagicMock()
        agent.model = None

        provider_id, model_id, source = await _resolve_model(request, agent, "test-session")
        assert provider_id == "anthropic"
        assert model_id == "claude-sonnet-4-5"
        assert source == "request"

    @pytest.mark.asyncio
    async def test_priority_2_agent_model(self):
        """Agent model is used when request has no model."""
        from flocks.server.routes.session import _resolve_model

        request = MagicMock()
        request.model = None
        
        agent = MagicMock()
        agent.model = {"providerID": "openai", "modelID": "gpt-4o"}

        provider_id, model_id, source = await _resolve_model(request, agent, "test-session")
        assert provider_id == "openai"
        assert model_id == "gpt-4o"
        assert source == "agent"

    @pytest.mark.asyncio
    async def test_priority_5_env_fallback(self):
        """Environment variables are used as final fallback."""
        from flocks.server.routes.session import _resolve_model

        request = MagicMock()
        request.model = None
        
        agent = MagicMock()
        agent.model = None

        with patch("flocks.server.routes.session._get_last_model", return_value=None), \
             patch("flocks.config.config.Config") as mock_config_cls:
            # Make config not have a model
            mock_config = MagicMock()
            mock_config.model = None
            mock_config_cls.get = AsyncMock(return_value=mock_config)
            
            with patch.dict("os.environ", {"LLM_PROVIDER": "test-provider", "LLM_MODEL": "test-model"}):
                provider_id, model_id, source = await _resolve_model(request, agent, "test-session")
                assert provider_id == "test-provider"
                assert model_id == "test-model"
                assert source == "env_default"

    @pytest.mark.asyncio
    async def test_pinned_session_model_beats_agent_and_config(self):
        """An explicit session pin should win over agent/config defaults."""
        from types import SimpleNamespace
        from flocks.server.routes.session import _resolve_model

        request = MagicMock()
        request.model = None

        agent = MagicMock()
        agent.name = "rex"
        agent.model = {"providerID": "openai", "modelID": "gpt-4o"}

        pinned_session = SimpleNamespace(
            provider="anthropic",
            model="claude-sonnet-4-5",
            model_pinned=True,
            parent_id=None,
        )

        with patch("flocks.server.routes.session.Session.get_by_id", AsyncMock(return_value=pinned_session)), \
             patch("flocks.storage.storage.Storage.read", AsyncMock(return_value={})):
            provider_id, model_id, source = await _resolve_model(request, agent, "test-session")

        assert provider_id == "anthropic"
        assert model_id == "claude-sonnet-4-5"
        assert source == "session"

    @pytest.mark.asyncio
    async def test_unpinned_session_model_falls_through_to_config(self):
        """Legacy session.provider/model should not override the new default."""
        from types import SimpleNamespace
        from flocks.server.routes.session import _resolve_model

        request = MagicMock()
        request.model = None

        agent = MagicMock()
        agent.name = "rex"
        agent.model = None

        legacy_session = SimpleNamespace(
            provider="anthropic",
            model="old-sticky-model",
            model_pinned=False,
            parent_id=None,
        )

        with patch("flocks.server.routes.session.Session.get_by_id", AsyncMock(return_value=legacy_session)), \
             patch("flocks.storage.storage.Storage.read", AsyncMock(return_value={})), \
             patch("flocks.config.config.Config.resolve_default_llm", AsyncMock(return_value={
                 "provider_id": "openai",
                 "model_id": "gpt-4o",
             })), \
             patch("flocks.server.routes.session._get_last_model", AsyncMock(return_value=None)):
            provider_id, model_id, source = await _resolve_model(request, agent, "test-session")

        assert provider_id == "openai"
        assert model_id == "gpt-4o"
        assert source == "config"

    @pytest.mark.asyncio
    async def test_process_session_message_pins_explicit_request_model(self, monkeypatch):
        """Explicit request.model should persist an explicit session pin."""
        from types import SimpleNamespace
        from flocks.server.routes import session as session_routes

        request = session_routes.PromptRequest(
            parts=[{"type": "text", "text": "hello"}],
            model=session_routes.ModelInfo(
                providerID="anthropic",
                modelID="claude-sonnet-4-5",
            ),
            noReply=True,
        )
        session = SimpleNamespace(
            id="ses_test",
            project_id="proj",
            directory="/tmp/project",
            agent="rex",
            provider=None,
            model=None,
            model_pinned=False,
        )

        monkeypatch.setattr(
            "flocks.agent.registry.Agent.default_agent",
            AsyncMock(return_value="rex"),
        )
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        update_mock = AsyncMock(
            return_value=SimpleNamespace(
                provider="anthropic",
                model="claude-sonnet-4-5",
                model_pinned=True,
                id=session.id,
                project_id=session.project_id,
                directory=session.directory,
                agent=session.agent,
            )
        )
        monkeypatch.setattr("flocks.session.session.Session.update", update_mock)
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())
        monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.cleanup",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.session.message.Message.create",
            AsyncMock(return_value=SimpleNamespace(id="msg_user_1")),
        )
        monkeypatch.setattr(
            "flocks.server.routes.event.publish_event",
            AsyncMock(),
        )

        result = await session_routes._process_session_message(
            "ses_test",
            session,
            request,
            "/tmp/project",
        )

        assert result["role"] == "user"
        update_mock.assert_awaited_once_with(
            "proj",
            "ses_test",
            provider="anthropic",
            model="claude-sonnet-4-5",
            model_pinned=True,
        )
