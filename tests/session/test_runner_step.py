"""
Tests for SessionRunner internals in flocks/session/runner.py

Covers:
- _agent_allows_tool(): tool permission filtering
- _exception_to_error_dict(): exception to error dict conversion
- _build_tools(): excluded tools filter
- RunnerCallbacks dataclass
- ToolCall / StepResult dataclasses
- SessionRunner construction and abort behavior (from existing tests)
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import flocks.session.runner as runner_mod
from flocks.session.message import UserMessageInfo
from flocks.session.runner import (
    RunnerCallbacks,
    SessionRunner,
    StepResult,
    ToolCall,
)
from flocks.session.session import SessionInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(session_id="ses_runner_test"):
    return SessionInfo.model_construct(
        id=session_id,
        slug="test",
        project_id="proj_runner",
        directory="/tmp",
        title="Runner Test",
    )


def _make_agent(name="rex", permission=None):
    agent = MagicMock()
    agent.name = name
    agent.permission = permission
    return agent


def _make_runner(session_id="ses_runner_test"):
    session = _make_session(session_id)
    return SessionRunner(session=session)


# ---------------------------------------------------------------------------
# ToolCall dataclass
# ---------------------------------------------------------------------------

class TestToolCallDataclass:
    def test_basic_creation(self):
        tc = ToolCall(id="call_001", name="bash", arguments={"command": "ls"})
        assert tc.id == "call_001"
        assert tc.name == "bash"
        assert tc.arguments == {"command": "ls"}

    def test_empty_arguments(self):
        tc = ToolCall(id="call_002", name="noop", arguments={})
        assert tc.arguments == {}


# ---------------------------------------------------------------------------
# StepResult dataclass
# ---------------------------------------------------------------------------

class TestStepResult:
    def test_stop_action(self):
        result = StepResult(action="stop", content="All done")
        assert result.action == "stop"
        assert result.content == "All done"
        assert result.tool_calls == []
        assert result.error is None

    def test_continue_with_tool_calls(self):
        tc = ToolCall(id="c1", name="bash", arguments={})
        result = StepResult(action="continue", tool_calls=[tc])
        assert len(result.tool_calls) == 1

    def test_error_action(self):
        result = StepResult(action="error", error="LLM failed")
        assert result.error == "LLM failed"


# ---------------------------------------------------------------------------
# RunnerCallbacks dataclass
# ---------------------------------------------------------------------------

class TestRunnerCallbacks:
    def test_all_defaults_none(self):
        cb = RunnerCallbacks()
        assert cb.on_step_start is None
        assert cb.on_step_end is None
        assert cb.on_text_delta is None
        assert cb.on_reasoning_delta is None
        assert cb.on_tool_start is None
        assert cb.on_tool_end is None
        assert cb.on_permission_request is None
        assert cb.on_error is None
        assert cb.event_publish_callback is None

    def test_set_callbacks(self):
        async def my_callback(x):
            pass

        cb = RunnerCallbacks(on_text_delta=my_callback, on_error=my_callback)
        assert cb.on_text_delta is my_callback
        assert cb.on_error is my_callback
        assert cb.on_step_start is None


# ---------------------------------------------------------------------------
# _agent_allows_tool()
# ---------------------------------------------------------------------------

class TestAgentAllowsTool:
    def test_rex_allows_all_tools(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        assert runner._agent_allows_tool(agent, "bash") is True
        assert runner._agent_allows_tool(agent, "write_file") is True
        assert runner._agent_allows_tool(agent, "any_tool") is True

    def test_agent_without_permission_allows_all(self):
        runner = _make_runner()
        agent = _make_agent(name="plan", permission=None)
        assert runner._agent_allows_tool(agent, "bash") is True

    def test_agent_with_empty_permission_allows_all(self):
        runner = _make_runner()
        agent = _make_agent(name="explore", permission=[])
        assert runner._agent_allows_tool(agent, "bash") is True

    def test_non_rex_agent_defaults_to_allow(self):
        runner = _make_runner()
        agent = _make_agent(name="custom_agent", permission=None)
        # Without permission rules, should default to allow
        assert runner._agent_allows_tool(agent, "read_file") is True


# ---------------------------------------------------------------------------
# _exception_to_error_dict()
# ---------------------------------------------------------------------------

class TestExceptionToErrorDict:
    def test_basic_exception(self):
        runner = _make_runner()
        exc = ValueError("something went wrong")
        result = runner._exception_to_error_dict(exc)
        assert result["name"] == "ValueError"
        assert "something went wrong" in result["data"]["message"]

    def test_rate_limit_exception_is_retryable(self):
        runner = _make_runner()
        exc = Exception("429 Too Many Requests - rate limit exceeded")
        result = runner._exception_to_error_dict(exc)
        assert result["name"] == "APIError"
        assert result["data"]["isRetryable"] is True

    def test_overloaded_exception_is_retryable(self):
        runner = _make_runner()
        exc = Exception("Provider is overloaded, please retry")
        result = runner._exception_to_error_dict(exc)
        assert result["data"]["isRetryable"] is True

    def test_timeout_exception_is_retryable(self):
        runner = _make_runner()
        exc = Exception("Connection timed out after 30s")
        result = runner._exception_to_error_dict(exc)
        assert result["data"]["isRetryable"] is True

    def test_exception_with_status_code_429(self):
        runner = _make_runner()
        exc = Exception("Rate limited")
        exc.status_code = 429
        result = runner._exception_to_error_dict(exc)
        assert result["name"] == "APIError"
        assert result["data"]["statusCode"] == 429
        assert result["data"]["isRetryable"] is True

    def test_exception_with_status_code_400_not_retryable(self):
        runner = _make_runner()
        exc = Exception("Bad request")
        exc.status_code = 400
        result = runner._exception_to_error_dict(exc)
        assert result["data"]["isRetryable"] is False

    def test_exception_with_status_code_500_retryable(self):
        runner = _make_runner()
        exc = Exception("Internal server error")
        exc.status_code = 500
        result = runner._exception_to_error_dict(exc)
        assert result["data"]["isRetryable"] is True

    def test_exception_with_response_headers(self):
        runner = _make_runner()
        exc = Exception("Rate limited")
        exc.status_code = 429
        exc.response = MagicMock()
        exc.response.headers = {"retry-after-ms": "5000"}
        result = runner._exception_to_error_dict(exc)
        assert result["data"]["responseHeaders"]["retry-after-ms"] == "5000"

    def test_generic_exception_name_preserved(self):
        runner = _make_runner()
        exc = RuntimeError("Something happened")
        result = runner._exception_to_error_dict(exc)
        assert "message" in result["data"]


# ---------------------------------------------------------------------------
# _build_tools(): excluded tools filter
# ---------------------------------------------------------------------------

class TestBuildTools:
    @pytest.mark.asyncio
    async def test_excludes_invalid_tool(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        mock_invalid = MagicMock()
        mock_invalid.name = "invalid"
        mock_invalid.enabled = True
        mock_invalid.description = "invalid"

        mock_bash = MagicMock()
        mock_bash.name = "bash"
        mock_bash.enabled = True
        mock_bash.description = "Execute bash"
        mock_bash.get_schema.return_value = MagicMock(to_json_schema=lambda: {"type": "object", "properties": {}})

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[mock_invalid, mock_bash],
        ):
            tools = await runner._build_tools(agent)

        tool_names = [t["function"]["name"] for t in tools]
        assert "invalid" not in tool_names

    @pytest.mark.asyncio
    async def test_excludes_noop_tool(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        mock_noop = MagicMock()
        mock_noop.name = "_noop"
        mock_noop.enabled = True

        mock_real = MagicMock()
        mock_real.name = "read_file"
        mock_real.enabled = True
        mock_real.description = "Read a file"
        mock_real.get_schema.return_value = MagicMock(to_json_schema=lambda: {"type": "object", "properties": {}})

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[mock_noop, mock_real],
        ):
            tools = await runner._build_tools(agent)

        tool_names = [t["function"]["name"] for t in tools]
        assert "_noop" not in tool_names

    @pytest.mark.asyncio
    async def test_disabled_tools_excluded(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        mock_disabled = MagicMock()
        mock_disabled.name = "disabled_tool"
        mock_disabled.enabled = False

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[mock_disabled],
        ):
            tools = await runner._build_tools(agent)

        assert tools == []

    @pytest.mark.asyncio
    async def test_tool_format_is_function_type(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        mock_tool = MagicMock()
        mock_tool.name = "bash"
        mock_tool.enabled = True
        mock_tool.description = "Execute bash commands"
        mock_tool.get_schema.return_value = MagicMock(
            to_json_schema=lambda: {"type": "object", "properties": {"command": {"type": "string"}}}
        )

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[mock_tool],
        ):
            tools = await runner._build_tools(agent)

        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "bash"
        assert tools[0]["function"]["description"] == "Execute bash commands"

    @pytest.mark.asyncio
    async def test_build_tools_reuses_loop_static_cache(self):
        shared_cache = {}
        session = _make_session("ses_tools_cache")
        runner1 = SessionRunner(session=session, static_cache=shared_cache)
        runner2 = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")

        mock_tool = MagicMock()
        mock_tool.name = "bash"
        mock_tool.enabled = True
        mock_tool.description = "Execute bash commands"
        mock_tool.get_schema.return_value = MagicMock(
            to_json_schema=lambda: {"type": "object", "properties": {"command": {"type": "string"}}}
        )

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[mock_tool],
        ) as list_tools:
            tools1 = await runner1._build_tools(agent)
            tools2 = await runner2._build_tools(agent)

        assert tools1 == tools2
        list_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_tools_rebuilds_when_tool_revision_changes(self):
        shared_cache = {}
        session = _make_session("ses_tools_revision")
        runner = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")

        tool_v1 = MagicMock()
        tool_v1.name = "bash"
        tool_v1.enabled = True
        tool_v1.description = "Execute bash commands"
        tool_v1.get_schema.return_value = MagicMock(
            to_json_schema=lambda: {"type": "object", "properties": {"command": {"type": "string"}}}
        )

        tool_v2 = MagicMock()
        tool_v2.name = "read"
        tool_v2.enabled = True
        tool_v2.description = "Read file contents"
        tool_v2.get_schema.return_value = MagicMock(
            to_json_schema=lambda: {"type": "object", "properties": {"path": {"type": "string"}}}
        )

        with patch("flocks.session.runner.ToolRegistry.revision", side_effect=[1, 2]), \
             patch(
                 "flocks.session.runner.ToolRegistry.list_tools",
                 side_effect=[[tool_v1], [tool_v2]],
             ) as list_tools:
            tools1 = await runner._build_tools(agent)
            tools2 = await runner._build_tools(agent)

        assert [tool["function"]["name"] for tool in tools1] == ["bash"]
        assert [tool["function"]["name"] for tool in tools2] == ["read"]
        assert list_tools.call_count == 2


class TestBuildSystemPrompts:
    @pytest.mark.asyncio
    async def test_build_system_prompts_reuses_loop_static_cache(self):
        shared_cache = {}
        session = _make_session("ses_prompts_cache")
        runner1 = SessionRunner(session=session, static_cache=shared_cache)
        runner2 = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        env_mock = AsyncMock(return_value=["env prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])
        sandbox_mock = AsyncMock(return_value="sandbox prompt")
        channel_mock = AsyncMock(return_value="channel prompt")

        with patch("flocks.session.runner.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.runner.SystemPrompt.environment", env_mock), \
             patch("flocks.session.runner.SystemPrompt.custom", custom_mock), \
             patch.object(SessionRunner, "_build_sandbox_prompt", sandbox_mock), \
             patch.object(SessionRunner, "_build_channel_context_prompt", channel_mock), \
             patch.object(SessionRunner, "_get_tool_instructions", return_value="tool instructions"):
            prompts1 = await runner1._build_system_prompts(agent)
            prompts2 = await runner2._build_system_prompts(agent)

        assert prompts1 == prompts2
        env_mock.assert_awaited_once()
        custom_mock.assert_awaited_once()
        sandbox_mock.assert_awaited_once()
        channel_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_system_prompts_rebuilds_when_tool_revision_changes(self):
        shared_cache = {}
        session = _make_session("ses_prompts_revision")
        runner = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt v1"

        env_mock = AsyncMock(return_value=["env prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])
        sandbox_mock = AsyncMock(return_value="sandbox prompt")
        channel_mock = AsyncMock(return_value="channel prompt")

        with patch("flocks.session.runner.ToolRegistry.revision", side_effect=[1, 2]), \
             patch("flocks.session.runner.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.runner.SystemPrompt.environment", env_mock), \
             patch("flocks.session.runner.SystemPrompt.custom", custom_mock), \
             patch.object(SessionRunner, "_build_sandbox_prompt", sandbox_mock), \
             patch.object(SessionRunner, "_build_channel_context_prompt", channel_mock), \
             patch.object(SessionRunner, "_get_tool_instructions", return_value="tool instructions"):
            prompts1 = await runner._build_system_prompts(agent)
            agent.prompt = "agent prompt v2"
            prompts2 = await runner._build_system_prompts(agent)

        assert prompts1 != prompts2
        assert "agent prompt v1" in prompts1
        assert "agent prompt v2" in prompts2
        assert env_mock.await_count == 2
        assert custom_mock.await_count == 2
        assert sandbox_mock.await_count == 2
        assert channel_mock.await_count == 2


class TestMiniMaxTextToolMode:
    def test_enabled_for_custom_threatbook_minimax(self):
        session = _make_session("ses_minimax_mode")
        runner = SessionRunner(
            session=session,
            provider_id="custom-threatbook-internal",
            model_id="minimax:MiniMax-M2.5",
        )
        assert runner._should_use_text_tool_call_mode() is True

    def test_enabled_for_custom_tb_inner_minimax(self):
        session = _make_session("ses_minimax_mode_tb_inner")
        runner = SessionRunner(
            session=session,
            provider_id="custom-tb-inner",
            model_id="minimax:MiniMax-M2.7",
        )
        assert runner._should_use_text_tool_call_mode() is True

    def test_disabled_for_other_models(self):
        session = _make_session("ses_normal_mode")
        runner = SessionRunner(
            session=session,
            provider_id="anthropic",
            model_id="claude-sonnet-4-5-20250929",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_tool_instructions_switch_to_minimax_xml(self):
        session = _make_session("ses_minimax_prompt")
        runner = SessionRunner(
            session=session,
            provider_id="custom-tb-inner",
            model_id="minimax:MiniMax-M2.5",
        )
        instructions = runner._get_tool_instructions()
        assert "<minimax:tool_call>" in instructions
        assert "native API tool-calling" in instructions

    def test_build_text_tool_call_catalog_prompt(self):
        session = _make_session("ses_minimax_catalog")
        runner = SessionRunner(
            session=session,
            provider_id="custom-threatbook-internal",
            model_id="minimax:MiniMax-M2.5",
        )
        prompt = runner._build_text_tool_call_catalog_prompt([
            {
                "type": "function",
                "function": {
                    "name": "onesec_ops",
                    "description": "Grouped OneSEC ops tool",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "description": "OPS action"},
                            "cur_page": {"type": "integer", "description": "Page number"},
                            "page_size": {"type": "integer", "description": "Page size"},
                        },
                        "required": ["action"],
                    },
                },
            }
        ])
        assert "onesec_ops" in prompt
        assert "action" in prompt
        assert "cur_page" in prompt
        assert "required" in prompt


@pytest.mark.asyncio
async def test_process_step_creates_assistant_message_with_provider_and_model(monkeypatch):
    runner = _make_runner("ses_runner_provider_model")
    runner.callbacks = RunnerCallbacks(
        on_text_delta=AsyncMock(),
        on_error=AsyncMock(),
    )

    last_user = UserMessageInfo(
        id="msg_user_runner",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=None, permission=None)
    provider = MagicMock()
    provider.is_configured.return_value = True
    captured_kwargs = {}

    async def fake_create(*args, **kwargs):
        captured_kwargs.update(kwargs)
        raise RuntimeError("assistant message created")

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "_build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "create", fake_create)

    with pytest.raises(RuntimeError, match="assistant message created"):
        await runner._process_step([last_user], last_user)

    assert captured_kwargs["model_id"] == runner.model_id
    assert captured_kwargs["provider_id"] == runner.provider_id
