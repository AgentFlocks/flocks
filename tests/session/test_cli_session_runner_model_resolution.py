"""
Tests for CLISessionRunner model resolution.

Verifies that when no --model CLI flag is provided, the runner falls back to
flocks.json config.model, and that custom providers are loaded at startup.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from flocks.cli.session_runner import CLISessionRunner


class TestParseModel:
    """Test _parse_model correctly splits provider/model."""

    def test_parse_model_with_slash(self):
        runner = CLISessionRunner(
            console=MagicMock(),
            directory=Path("/tmp"),
        )
        result = runner._parse_model("custom-threatbook-internal/volcengine: glm-4-7-251222")
        assert result == {
            "provider_id": "custom-threatbook-internal",
            "model_id": "volcengine: glm-4-7-251222",
        }

    def test_parse_model_without_slash(self):
        runner = CLISessionRunner(
            console=MagicMock(),
            directory=Path("/tmp"),
        )
        result = runner._parse_model("gpt-4o")
        assert result == {
            "provider_id": "anthropic",
            "model_id": "gpt-4o",
        }

    def test_parse_model_none(self):
        runner = CLISessionRunner(
            console=MagicMock(),
            directory=Path("/tmp"),
        )
        result = runner._parse_model(None)
        assert result is None


class TestModelResolutionFromConfig:
    """Test that _process_message reads model from flocks.json when no CLI flag."""

    @pytest.mark.asyncio
    async def test_reads_config_model_when_no_cli_flag(self):
        """When self.model is None, should read from Config.get().model."""
        runner = CLISessionRunner(
            console=MagicMock(),
            directory=Path("/tmp"),
            model=None,  # no --model flag
        )
        runner._session = MagicMock()
        runner._session.id = "test-session-id"

        mock_config = MagicMock()
        mock_config.model = "custom-threatbook-internal/volcengine: glm-4-7-251222"

        mock_default_llm = {
            "provider_id": "custom-threatbook-internal",
            "model_id": "volcengine: glm-4-7-251222",
        }

        with patch("flocks.config.config.Config.get", new_callable=AsyncMock, return_value=mock_config), \
             patch("flocks.config.config.Config.resolve_default_llm", new_callable=AsyncMock, return_value=mock_default_llm), \
             patch("flocks.agent.registry.Agent.default_agent", new_callable=AsyncMock, return_value="rex"), \
             patch("flocks.agent.registry.Agent.get", new_callable=AsyncMock) as mock_agent_get, \
             patch("flocks.session.message.Message.create", new_callable=AsyncMock) as mock_msg_create, \
             patch("flocks.session.session_loop.SessionLoop.run", new_callable=AsyncMock) as mock_loop_run:

            mock_agent = MagicMock()
            mock_agent.name = "rex"
            mock_agent_get.return_value = mock_agent

            mock_loop_run.return_value = MagicMock(action="stop", last_message=None)

            await runner._process_message("hello")

            # Verify SessionLoop.run was called with the correct provider/model
            mock_loop_run.assert_called_once()
            call_kwargs = mock_loop_run.call_args
            assert call_kwargs.kwargs.get("provider_id") == "custom-threatbook-internal"
            assert call_kwargs.kwargs.get("model_id") == "volcengine: glm-4-7-251222"

    @pytest.mark.asyncio
    async def test_cli_flag_overrides_config(self):
        """When self.model is set via CLI, it should take precedence over config."""
        runner = CLISessionRunner(
            console=MagicMock(),
            directory=Path("/tmp"),
            model="openai/gpt-4o",  # explicit --model flag
        )
        runner._session = MagicMock()
        runner._session.id = "test-session-id"

        with patch("flocks.agent.registry.Agent.default_agent", new_callable=AsyncMock, return_value="rex"), \
             patch("flocks.agent.registry.Agent.get", new_callable=AsyncMock) as mock_agent_get, \
             patch("flocks.session.message.Message.create", new_callable=AsyncMock), \
             patch("flocks.session.session_loop.SessionLoop.run", new_callable=AsyncMock) as mock_loop_run:

            mock_agent = MagicMock()
            mock_agent.name = "rex"
            mock_agent_get.return_value = mock_agent

            mock_loop_run.return_value = MagicMock(action="stop", last_message=None)

            await runner._process_message("hello")

            call_kwargs = mock_loop_run.call_args
            assert call_kwargs.kwargs.get("provider_id") == "openai"
            assert call_kwargs.kwargs.get("model_id") == "gpt-4o"
