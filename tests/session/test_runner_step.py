"""
Tests for SessionRunner internals in flocks/session/runner.py

Covers:
- _agent_declares_tool(): tool declaration filtering
- _exception_to_error_dict(): exception to error dict conversion
- _build_callable_tool_schema(): excluded tools filter
- RunnerCallbacks dataclass
- ToolCall / StepResult dataclasses
- SessionRunner construction and abort behavior (from existing tests)
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from flocks.session.runner import (
    RunnerCallbacks,
    SessionRunner,
    StepResult,
    ToolCall,
)
from flocks.session.session import SessionInfo
from flocks.tool.registry import ToolCategory, ToolInfo


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


def _make_agent(name="rex", tools=None):
    agent = MagicMock()
    agent.name = name
    agent.tools = tools
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
# _agent_declares_tool()
# ---------------------------------------------------------------------------

class TestAgentDeclaresTool:
    def test_agent_with_explicit_tools_allows_declared_tools(self):
        runner = _make_runner()
        agent = _make_agent(name="rex", tools=["bash", "read"])
        assert runner._agent_declares_tool(agent, "bash") is True
        assert runner._agent_declares_tool(agent, "read") is True
        assert runner._agent_declares_tool(agent, "any_tool") is False

    def test_agent_without_tools_defaults_to_deny(self):
        runner = _make_runner()
        agent = _make_agent(name="plan", tools=None)
        assert runner._agent_declares_tool(agent, "bash") is False

    def test_agent_with_empty_tools_allows_nothing(self):
        runner = _make_runner()
        agent = _make_agent(name="explore", tools=[])
        assert runner._agent_declares_tool(agent, "bash") is False

    def test_non_rex_agent_defaults_to_deny(self):
        runner = _make_runner()
        agent = _make_agent(name="custom_agent", tools=None)
        # Without an explicit tools list, only always-load tools remain available.
        assert runner._agent_declares_tool(agent, "read") is False


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
# _build_callable_tool_schema(): excluded tools filter
# ---------------------------------------------------------------------------

class TestBuildTools:
    @pytest.mark.asyncio
    async def test_excludes_invalid_tool(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        invalid_tool = ToolInfo(
            name="invalid",
            description="invalid",
            category=ToolCategory.SYSTEM,
            native=True,
            enabled=True,
        )
        bash_tool = ToolInfo(
            name="bash",
            description="Execute bash",
            category=ToolCategory.CODE,
            native=True,
            enabled=True,
        )

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[invalid_tool, bash_tool],
        ):
            tools = await runner._build_callable_tool_schema(agent)

        tool_names = [t["function"]["name"] for t in tools]
        assert "invalid" not in tool_names

    @pytest.mark.asyncio
    async def test_excludes_noop_tool(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        noop_tool = ToolInfo(
            name="_noop",
            description="noop",
            category=ToolCategory.SYSTEM,
            native=True,
            enabled=True,
        )
        real_tool = ToolInfo(
            name="read",
            description="Read a file",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
        )

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[noop_tool, real_tool],
        ):
            tools = await runner._build_callable_tool_schema(agent)

        tool_names = [t["function"]["name"] for t in tools]
        assert "_noop" not in tool_names

    @pytest.mark.asyncio
    async def test_disabled_tools_excluded(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")

        disabled_tool = ToolInfo(
            name="disabled_tool",
            description="disabled",
            category=ToolCategory.SYSTEM,
            native=True,
            enabled=False,
        )

        with patch(
            "flocks.session.runner.ToolRegistry.list_tools",
            return_value=[disabled_tool],
        ):
            tools = await runner._build_callable_tool_schema(agent)

        assert tools == []

    @pytest.mark.asyncio
    async def test_tool_format_is_function_type(self):
        runner = _make_runner()
        agent = _make_agent(name="rex", tools=["bash"])

        tool_info = ToolInfo(
            name="bash",
            description="Execute bash commands",
            category=ToolCategory.CODE,
            native=True,
            enabled=True,
        )

        with patch(
            "flocks.session.runner.SessionRunner._list_callable_tool_infos_for_turn",
            AsyncMock(return_value=([tool_info], {"enabledToolCount": 1})),
        ):
            tools = await runner._build_callable_tool_schema(agent)

        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "bash"
        assert tools[0]["function"]["description"] == "Execute bash commands"

    @pytest.mark.asyncio
    async def test_build_tools_reflects_latest_selector_result(self):
        runner = _make_runner("ses_tools_selector_refresh")
        agent = _make_agent(name="rex")

        tool_v1 = ToolInfo(
            name="bash",
            description="Execute bash commands",
            category=ToolCategory.CODE,
            native=True,
            enabled=True,
        )
        tool_v2 = ToolInfo(
            name="read",
            description="Read file contents",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
        )

        selector_mock = AsyncMock(side_effect=[
            ([tool_v1], {"enabledToolCount": 3}),
            ([tool_v2], {"enabledToolCount": 3}),
        ])
        with patch.object(SessionRunner, "_list_callable_tool_infos_for_turn", selector_mock):
            tools1 = await runner._build_callable_tool_schema(agent, [])
            tools2 = await runner._build_callable_tool_schema(agent, [])

        assert [tool["function"]["name"] for tool in tools1] == ["bash"]
        assert [tool["function"]["name"] for tool in tools2] == ["read"]
        assert selector_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_build_tools_calls_selector_for_each_runner_instance(self):
        shared_cache = {}
        session = _make_session("ses_tools_runner_instances")
        runner1 = SessionRunner(session=session, static_cache=shared_cache)
        runner2 = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")

        selected_tool = ToolInfo(
            name="bash",
            description="Execute bash commands",
            category=ToolCategory.CODE,
            native=True,
            enabled=True,
        )

        selector_mock = AsyncMock(return_value=([selected_tool], {"enabledToolCount": 3}))
        with patch.object(SessionRunner, "_list_callable_tool_infos_for_turn", selector_mock):
            tools1 = await runner1._build_callable_tool_schema(agent, [])
            tools2 = await runner2._build_callable_tool_schema(agent, [])

        assert tools1 == tools2
        assert selector_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_build_tools_uses_selector_results_and_emits_event(self):
        runner = _make_runner()
        event_callback = AsyncMock()
        runner.callbacks.event_publish_callback = event_callback
        agent = _make_agent(name="rex")

        selected_tool = ToolInfo(
            name="read",
            description="Read file contents",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
        )

        with patch.object(
            SessionRunner,
            "_list_callable_tool_infos_for_turn",
            AsyncMock(return_value=(
                [selected_tool],
                {"enabledToolCount": 3},
            )),
        ):
            tools = await runner._build_callable_tool_schema(agent, [])

        assert [tool["function"]["name"] for tool in tools] == ["read"]
        event_callback.assert_awaited_once()
        assert event_callback.await_args.args[0] == "turn.tools_selected"
        assert event_callback.await_args.args[1]["enabledToolCount"] == 3

    @pytest.mark.asyncio
    async def test_build_tools_rewrites_skill_description(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        skill_tool = ToolInfo(
            name="skill",
            description="Original skill description",
            category=ToolCategory.SYSTEM,
            native=True,
            enabled=True,
        )

        mock_skill = MagicMock()
        mock_skill.name = "secops"
        mock_skill.description = "Security workflow guidance"

        with patch.object(
            SessionRunner,
            "_list_callable_tool_infos_for_turn",
            AsyncMock(return_value=([skill_tool], {"enabledToolCount": 3})),
        ), patch(
            "flocks.tool.system.skill.Skill.all",
            AsyncMock(return_value=[mock_skill]),
        ), patch(
            "flocks.tool.system.skill.build_description",
            return_value="Dynamic skill description",
        ):
            tools = await runner._build_callable_tool_schema(agent, [])

        assert tools[0]["function"]["name"] == "skill"
        assert tools[0]["function"]["description"] == "Dynamic skill description"


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
             patch.object(SessionRunner, "_get_tool_instructions", return_value="tool instructions"), \
             patch.object(SessionRunner, "_build_tool_catalog_prompt", return_value="tool catalog"):
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
             patch.object(SessionRunner, "_get_tool_instructions", return_value="tool instructions"), \
             patch.object(SessionRunner, "_build_tool_catalog_prompt", side_effect=["tool catalog v1", "tool catalog v2"]):
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

    def test_build_tool_catalog_prompt_for_rex(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        agent.mode = "primary"

        with patch(
            "flocks.session.runner.SessionRunner._list_catalog_tool_infos",
            return_value=[ToolInfo(
                name="read",
                description="Read file contents",
                category=ToolCategory.FILE,
                native=True,
                enabled=True,
            )],
        ), patch(
            "flocks.tool.system.slash_command.format_tools_catalog_summary",
            return_value="Available Tools (grouped by category):\n\n**file**\n- read: Read file contents",
        ):
            prompt = runner._build_tool_catalog_prompt(agent)

        assert prompt is not None
        assert "Tool Catalog Awareness" in prompt
        assert "tool_search" in prompt
        assert "full tool catalog" in prompt
        assert "reference-only" in prompt
        assert "sole source of truth for parameters" in prompt
        assert "- read: Read file contents" in prompt

    def test_build_tool_catalog_prompt_for_subagent_uses_filtered_catalog(self):
        runner = _make_runner()
        agent = _make_agent(name="plan")
        agent.mode = "subagent"

        with patch(
            "flocks.session.runner.SessionRunner._list_catalog_tool_infos",
            return_value=[ToolInfo(
                name="read",
                description="Read file contents",
                category=ToolCategory.FILE,
                native=True,
                enabled=True,
            )],
        ), patch(
            "flocks.tool.system.slash_command.format_tools_catalog_summary",
            return_value="Available Tools (grouped by category):\n\n**file**\n- read: Read file contents",
        ):
            prompt = runner._build_tool_catalog_prompt(agent)

        assert prompt is not None
        assert "derived from your configured callable tool set" in prompt
        assert "use `tool_search` first" not in prompt

    def test_list_catalog_tool_infos_returns_full_catalog_for_rex(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        agent.mode = "primary"
        shell_tool = ToolInfo(
            name="bash",
            description="Run commands",
            category=ToolCategory.CODE,
            native=True,
            enabled=True,
        )
        helper_tool = ToolInfo(
            name="read",
            description="Read file contents",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
        )

        with patch(
            "flocks.session.runner.list_tool_catalog_infos",
            return_value=[shell_tool, helper_tool],
        ):
            infos = runner._list_catalog_tool_infos(agent)

        assert [tool.name for tool in infos] == ["bash", "read"]

    def test_list_catalog_tool_infos_filters_subagent_boundaries(self):
        runner = _make_runner()
        agent = _make_agent(name="plan")
        agent.mode = "subagent"
        agent.tools = ["read"]
        tool_infos = [
            ToolInfo(name="bash", description="Run commands", category=ToolCategory.CODE, native=True, enabled=True),
            ToolInfo(name="read", description="Read file contents", category=ToolCategory.FILE, native=True, enabled=True),
            ToolInfo(name="websearch", description="Search web", category=ToolCategory.BROWSER, native=True, enabled=True),
        ]

        with patch("flocks.session.runner.list_tool_catalog_infos", return_value=tool_infos):
            infos = runner._list_catalog_tool_infos(agent)

        assert [tool.name for tool in infos] == ["read"]


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
        assert "authoritative callable schema" in prompt
        assert "Parameter names must match exactly" in prompt
        assert "action" in prompt
        assert "cur_page" in prompt
        assert "required" in prompt
