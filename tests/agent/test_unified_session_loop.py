"""
Tests for Phase 1: Unified UI entry via SessionLoop.

Verifies that:
1. RunnerCallbacks.event_publish_callback is passed through to StreamProcessor
2. LoopCallbacks carries runner_callbacks and event_publish_callback
3. SessionRunner uses explicit callbacks (doesn't override with CLI fallback)
4. _resolve_model implements 5-level priority correctly
"""

import asyncio

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
            model_auto=False,
        )

    @pytest.mark.asyncio
    async def test_webui_auto_uses_default_primary_and_returns_actual_model(self, monkeypatch):
        """Auto ignores agent overrides and reports the model that recovered."""
        from types import SimpleNamespace

        from flocks.server.routes import session as session_routes
        from flocks.session.session_loop import LoopResult, SessionLoop

        request = session_routes.PromptRequest(
            parts=[{"type": "text", "text": "hello"}],
        )
        session = SimpleNamespace(
            id="ses_auto",
            project_id="proj",
            directory="/tmp/project",
            agent="rex",
            provider="stale",
            model="stale-model",
            model_pinned=False,
            model_auto=True,
            category="user",
        )
        agent = SimpleNamespace(
            name="rex",
            model={"providerID": "agent-provider", "modelID": "agent-model"},
        )
        fallback_message = SimpleNamespace(
            id="msg_assistant",
            providerID="fallback",
            modelID="fallback-model",
            finish="stop",
            tokens=None,
        )
        loop_run = AsyncMock(return_value=LoopResult(
            action="stop",
            last_message=fallback_message,
            provider_id="fallback",
            model_id="fallback-model",
        ))
        message_create = AsyncMock(return_value=SimpleNamespace(id="msg_user"))
        context_usage_update = AsyncMock()
        title_generation = AsyncMock()

        monkeypatch.setattr(session_routes, "_require_agent_usable_for_chat", AsyncMock())
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.default_agent",
            AsyncMock(return_value="rex"),
        )
        monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=agent))
        monkeypatch.setattr(
            "flocks.config.config.Config.resolve_default_llm",
            AsyncMock(return_value={
                "provider_id": "primary",
                "model_id": "primary-model",
            }),
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.get",
            AsyncMock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(
            SessionLoop,
            "validate_runtime_model",
            AsyncMock(return_value=(True, "available")),
        )
        monkeypatch.setattr(SessionLoop, "run", loop_run)
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.cleanup",
            AsyncMock(),
        )
        monkeypatch.setattr("flocks.session.message.Message.create", message_create)
        monkeypatch.setattr(
            "flocks.session.message.Message.get_text_content",
            AsyncMock(return_value="recovered"),
        )
        monkeypatch.setattr("flocks.session.message.Message.parts", AsyncMock(return_value=[]))
        monkeypatch.setattr("flocks.server.routes.event.publish_event", AsyncMock())
        monkeypatch.setattr(
            session_routes,
            "_publish_context_usage_update",
            context_usage_update,
        )
        monkeypatch.setattr(
            "flocks.session.lifecycle.title.SessionTitle.generate_title_after_first_message",
            title_generation,
        )

        response = await session_routes._process_session_message(
            session.id,
            session,
            request,
            session.directory,
        )

        assert message_create.await_args.kwargs["model"] == {
            "providerID": "primary",
            "modelID": "primary-model",
        }
        assert loop_run.await_args.kwargs["auto_failover"] is True
        assert loop_run.await_args.kwargs["provider_id"] == "primary"
        assert response["info"]["providerID"] == "fallback"
        assert response["info"]["modelID"] == "fallback-model"
        assert context_usage_update.await_args.kwargs["provider_id"] == "fallback"
        assert context_usage_update.await_args.kwargs["model_id"] == "fallback-model"
        await asyncio.sleep(0)
        assert title_generation.await_args.kwargs["provider_id"] == "fallback"
        assert title_generation.await_args.kwargs["model_id"] == "fallback-model"

    @pytest.mark.asyncio
    async def test_non_user_session_ignores_legacy_auto_flag(self, monkeypatch):
        """Corrupt or historical workflow flags cannot activate WebUI Auto."""
        from types import SimpleNamespace

        from flocks.server.routes import session as session_routes
        from flocks.session.session_loop import SessionLoop

        request = session_routes.PromptRequest(
            parts=[{"type": "text", "text": "workflow input"}],
            noReply=True,
        )
        session = SimpleNamespace(
            id="ses_workflow",
            project_id="proj",
            directory="/tmp/project",
            agent="rex",
            provider="direct",
            model="direct-model",
            model_pinned=True,
            model_auto=True,
            category="workflow",
        )
        agent = SimpleNamespace(name="rex", model=None)
        resolve = AsyncMock(return_value=("direct", "direct-model", "session"))
        default_llm = AsyncMock()
        validate = AsyncMock()
        message_create = AsyncMock(return_value=SimpleNamespace(id="msg_user"))

        monkeypatch.setattr(
            session_routes,
            "_require_agent_usable_for_chat",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.default_agent",
            AsyncMock(return_value="rex"),
        )
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=agent),
        )
        monkeypatch.setattr(session_routes, "_resolve_model", resolve)
        monkeypatch.setattr(
            "flocks.config.config.Config.resolve_default_llm",
            default_llm,
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.get",
            AsyncMock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(SessionLoop, "validate_runtime_model", validate)
        monkeypatch.setattr(
            "flocks.provider.provider.Provider._ensure_initialized",
            lambda: None,
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.apply_config",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.get",
            lambda _provider_id: object(),
        )
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.cleanup",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.session.message.Message.create",
            message_create,
        )
        monkeypatch.setattr(
            "flocks.server.routes.event.publish_event",
            AsyncMock(),
        )
        context_usage = AsyncMock()
        monkeypatch.setattr(
            session_routes,
            "_publish_context_usage_update",
            context_usage,
        )

        await session_routes._process_session_message(
            session.id,
            session,
            request,
            session.directory,
        )

        resolve.assert_awaited_once()
        default_llm.assert_not_awaited()
        validate.assert_not_awaited()
        assert message_create.await_args.kwargs["model"] == {
            "providerID": "direct",
            "modelID": "direct-model",
        }
        assert context_usage.await_args.kwargs["provider_id"] == "direct"
        assert context_usage.await_args.kwargs["model_id"] == "direct-model"

    @pytest.mark.asyncio
    async def test_display_text_does_not_replace_model_prompt(self, monkeypatch):
        """displayText is presentation metadata; the stored text remains the real prompt."""
        from types import SimpleNamespace
        from flocks.server.routes import session as session_routes

        request = session_routes.PromptRequest(
            parts=[{"type": "text", "text": "Read guide.md and configure the workflow."}],
            displayText="@@flocks-instruction:智能配置",
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
        monkeypatch.setattr("flocks.session.session.Session.update", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())
        monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.cleanup",
            AsyncMock(),
        )
        message_create = AsyncMock(return_value=SimpleNamespace(id="msg_user_1"))
        publish_event = AsyncMock()
        monkeypatch.setattr("flocks.session.message.Message.create", message_create)
        monkeypatch.setattr("flocks.server.routes.event.publish_event", publish_event)

        await session_routes._process_session_message(
            "ses_test",
            session,
            request,
            "/tmp/project",
        )

        create_kwargs = message_create.await_args.kwargs
        assert create_kwargs["content"] == "Read guide.md and configure the workflow."
        assert create_kwargs["part_metadata"] == {"displayText": "@@flocks-instruction:智能配置"}

        part_event = publish_event.await_args_list[1].args[1]["part"]
        assert part_event["text"] == "Read guide.md and configure the workflow."
        assert part_event["metadata"] == {"displayText": "@@flocks-instruction:智能配置"}
