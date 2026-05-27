from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.agent.task import _resolve_child_model, task_tool


def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-message", agent="rex")


class TestTaskModelPinning:
    @pytest.mark.asyncio
    async def test_resolve_child_model_ignores_unpinned_parent_session_model(self):
        parent_session = SimpleNamespace(
            provider="anthropic",
            model="stale-model",
            model_pinned=False,
        )

        with patch("flocks.storage.storage.Storage.read", AsyncMock(return_value={})), \
             patch("flocks.agent.registry.Agent.get", AsyncMock(return_value=None)), \
             patch("flocks.config.config.Config.resolve_default_llm", AsyncMock(return_value={
                 "provider_id": "openai",
                 "model_id": "gpt-5",
             })):
            provider, model, source = await _resolve_child_model("explore", parent_session)

        assert provider == "openai"
        assert model == "gpt-5"
        assert source == "config"

    @pytest.mark.asyncio
    async def test_task_tool_explicit_model_override_pins_child_session(self):
        manager = SimpleNamespace(
            launch=AsyncMock(return_value=SimpleNamespace(
                id="bg-task",
                description="delegate explore",
                agent="explore",
                status="running",
                session_id="ses-child",
            ))
        )
        parent_session = SimpleNamespace(
            id="ses-parent",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
            model_pinned=False,
        )

        with patch("flocks.tool.agent.task.is_delegatable", return_value=True), \
             patch("flocks.tool.agent.task.Session.get_by_id", AsyncMock(return_value=parent_session)), \
             patch("flocks.tool.agent.task.get_background_manager", return_value=manager):
            result = await task_tool(
                _make_ctx(),
                description="delegate explore",
                prompt="Inspect the repository",
                subagent_type="explore",
                run_in_background=True,
                model="openai/gpt-5",
            )

        assert result.success is True
        manager.launch.assert_awaited_once()
        launch_input = manager.launch.await_args.args[0]
        assert launch_input.model == {
            "providerID": "openai",
            "modelID": "gpt-5",
        }
        assert launch_input.model_pinned is True

    @pytest.mark.asyncio
    async def test_task_tool_default_resolution_does_not_pin_child_session(self):
        manager = SimpleNamespace(
            launch=AsyncMock(return_value=SimpleNamespace(
                id="bg-task",
                description="delegate explore",
                agent="explore",
                status="running",
                session_id="ses-child",
            ))
        )
        parent_session = SimpleNamespace(
            id="ses-parent",
            project_id="proj",
            directory="/tmp/project",
            provider="anthropic",
            model="stale-model",
            model_pinned=False,
        )

        with patch("flocks.tool.agent.task.is_delegatable", return_value=True), \
             patch("flocks.tool.agent.task.Session.get_by_id", AsyncMock(return_value=parent_session)), \
             patch("flocks.tool.agent.task.get_background_manager", return_value=manager), \
             patch("flocks.storage.storage.Storage.read", AsyncMock(return_value={})), \
             patch("flocks.agent.registry.Agent.get", AsyncMock(return_value=None)), \
             patch("flocks.config.config.Config.resolve_default_llm", AsyncMock(return_value={
                 "provider_id": "anthropic",
                 "model_id": "claude-sonnet-4-6",
             })):
            result = await task_tool(
                _make_ctx(),
                description="delegate explore",
                prompt="Inspect the repository",
                subagent_type="explore",
                run_in_background=True,
            )

        assert result.success is True
        manager.launch.assert_awaited_once()
        launch_input = manager.launch.await_args.args[0]
        assert launch_input.model is None
        assert launch_input.model_pinned is False

    @pytest.mark.asyncio
    async def test_task_tool_sync_continue_returns_session_loop_error(self):
        parent_session = SimpleNamespace(
            id="ses-parent",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
            model_pinned=False,
        )
        child_session = SimpleNamespace(
            id="ses-child",
            agent="explore",
        )

        with patch("flocks.tool.agent.task.is_delegatable", return_value=True), \
             patch("flocks.tool.agent.task.Session.get_by_id", AsyncMock(side_effect=[parent_session, child_session])), \
             patch("flocks.tool.agent.task.Message.create", AsyncMock()), \
             patch("flocks.tool.agent.task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                 action="error",
                 error="subagent crashed",
                 last_message=None,
             ))):
            result = await task_tool(
                _make_ctx(),
                description="continue explore",
                prompt="Continue the task",
                subagent_type="explore",
                session_id="ses-child",
            )

        assert result.success is False
        assert result.metadata["sessionId"] == "ses-child"
        assert "subagent crashed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_task_tool_sync_continue_fails_when_last_message_missing(self):
        parent_session = SimpleNamespace(
            id="ses-parent",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
            model_pinned=False,
        )
        child_session = SimpleNamespace(
            id="ses-child",
            agent="explore",
        )

        with patch("flocks.tool.agent.task.is_delegatable", return_value=True), \
             patch("flocks.tool.agent.task.Session.get_by_id", AsyncMock(side_effect=[parent_session, child_session])), \
             patch("flocks.tool.agent.task.Message.create", AsyncMock()), \
             patch("flocks.tool.agent.task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                 action="stop",
                 error=None,
                 last_message=None,
             ))):
            result = await task_tool(
                _make_ctx(),
                description="continue explore",
                prompt="Continue the task",
                subagent_type="explore",
                session_id="ses-child",
            )

        assert result.success is True
        assert result.metadata["sessionId"] == "ses-child"
        assert result.metadata["emptyOutput"] is True
        assert "without producing a final assistant message" in (result.output or "")

    @pytest.mark.asyncio
    async def test_task_tool_sync_continue_fails_when_last_message_has_no_text(self):
        parent_session = SimpleNamespace(
            id="ses-parent",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
            model_pinned=False,
        )
        child_session = SimpleNamespace(
            id="ses-child",
            agent="explore",
        )
        last_message = SimpleNamespace(
            id="msg-last",
            sessionID="ses-child",
            finish="stop",
            error=None,
        )

        with patch("flocks.tool.agent.task.is_delegatable", return_value=True), \
             patch("flocks.tool.agent.task.Session.get_by_id", AsyncMock(side_effect=[parent_session, child_session])), \
             patch("flocks.tool.agent.task.Message.create", AsyncMock()), \
             patch("flocks.tool.agent.task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                 action="stop",
                 error=None,
                 last_message=last_message,
             ))), \
             patch("flocks.tool.subagent_result.Message.get_text_content", AsyncMock(return_value="")):
            result = await task_tool(
                _make_ctx(),
                description="continue explore",
                prompt="Continue the task",
                subagent_type="explore",
                session_id="ses-child",
            )

        assert result.success is True
        assert result.metadata["sessionId"] == "ses-child"
        assert result.metadata["emptyOutput"] is True
        assert "without text output" in (result.output or "")
