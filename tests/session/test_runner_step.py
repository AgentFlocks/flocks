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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import flocks.session.runner as runner_mod
from flocks.session.message import (
    Message,
    MessageRole,
    PartTime,
    ReasoningPart,
    ToolPart,
    ToolStateRunning,
    UserMessageInfo,
)
from flocks.session.runner import (
    RunnerCallbacks,
    SessionRunner,
    StepResult,
    ToolCall,
)
from flocks.session.prompt import SessionPrompt
from flocks.session.core.defaults import DEFAULT_MAX_TOOL_STEPS
from flocks.session.session import Session, SessionInfo
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


def _make_callable_schema_result(*tool_names):
    return SimpleNamespace(
        tool_infos=[SimpleNamespace(name=name) for name in tool_names],
        metadata={},
    )


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


class TestToolLoopGuard:
    def test_halts_after_three_exact_tool_only_steps(self):
        runner = _make_runner("ses_runner_tool_loop_exact")
        result = StepResult(
            action="continue",
            tool_calls=[ToolCall(id="c1", name="echo_tool", arguments={"text": "loop"})],
        )

        first = runner._update_tool_loop_guard(result, last_user_id="user-1")
        second = runner._update_tool_loop_guard(result, last_user_id="user-1")
        third = runner._update_tool_loop_guard(result, last_user_id="user-1")

        assert first["action"] == "allow"
        assert second["action"] == "warn"
        assert third["action"] == "halt"
        assert third["reason"] == "repeated_exact_tool_call"
        assert third["count"] == 3

    def test_halts_after_same_tool_streak_with_varying_args(self):
        runner = _make_runner("ses_runner_tool_loop_same_tool")
        decision = None

        for idx in range(1, 9):
            decision = runner._update_tool_loop_guard(
                StepResult(
                    action="continue",
                    tool_calls=[ToolCall(id=f"c{idx}", name="echo_tool", arguments={"text": f"loop-{idx}"})],
                ),
                last_user_id="user-1",
            )

        assert decision is not None
        assert decision["action"] == "halt"
        assert decision["reason"] == "same_tool_streak"
        assert decision["count"] == 8

    def test_resets_after_text_response(self):
        runner = _make_runner("ses_runner_tool_loop_reset")
        tool_only = StepResult(
            action="continue",
            tool_calls=[ToolCall(id="c1", name="echo_tool", arguments={"text": "loop"})],
        )

        runner._update_tool_loop_guard(tool_only, last_user_id="user-1")
        warned = runner._update_tool_loop_guard(tool_only, last_user_id="user-1")
        reset = runner._update_tool_loop_guard(
            StepResult(action="stop", content="done"),
            last_user_id="user-1",
        )
        restarted = runner._update_tool_loop_guard(tool_only, last_user_id="user-1")

        assert warned["action"] == "warn"
        assert reset["action"] == "allow"
        assert restarted["action"] == "allow"
        assert runner._get_tool_loop_guard_state(last_user_id="user-1")["exact_count"] == 1


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
    async def test_build_callable_tool_schema_reuses_cached_schema(self):
        runner = _make_runner("ses_runner_cache")
        agent = _make_agent(name="rex")
        schema_calls = 0

        class _Schema:
            def to_json_schema(self):
                nonlocal schema_calls
                schema_calls += 1
                return {"type": "object", "properties": {"path": {"type": "string"}}}

        tool_info = SimpleNamespace(
            name="read",
            description="Read a file",
            get_schema=lambda: _Schema(),
            provider_version=None,
        )

        with patch.object(
            runner,
            "_list_callable_tool_infos_for_turn",
            new=AsyncMock(return_value=([tool_info], {"enabledToolCount": 1})),
        ), patch.object(
            runner,
            "_publish_turn_tools_event",
            new=AsyncMock(),
        ):
            tools_first = await runner._build_callable_tool_schema(agent)
            tools_second = await runner._build_callable_tool_schema(agent)

        assert schema_calls == 1
        assert tools_first == tools_second
        assert tools_first is not tools_second
        assert tools_first[0]["function"]["name"] == "read"

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

    def test_prompt_tool_names_from_schema_uses_loaded_tool_names(self):
        runner = _make_runner()

        prompt_tool_names = runner._get_prompt_tool_names_from_schema([
            {"type": "function", "function": {"name": "memory_search"}},
            {"type": "function", "function": {"name": "bash"}},
            {"type": "function", "function": {"name": "bash"}},
            {"type": "function", "function": {}},
            {"type": "other"},
        ])

        assert prompt_tool_names == ("bash", "memory_search")

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
    async def test_build_tools_refreshes_skill_description_from_enabled_skills(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        skill_tool = ToolInfo(
            name="skill_load",
            description="Original skill description",
            category=ToolCategory.SYSTEM,
            native=True,
            enabled=True,
        )

        with patch.object(
            SessionRunner,
            "_list_callable_tool_infos_for_turn",
            AsyncMock(return_value=([skill_tool], {"enabledToolCount": 3})),
        ), patch(
            "flocks.skill.skill.Skill.list_enabled",
            AsyncMock(return_value=[SimpleNamespace(name="agent-builder")]),
        ), patch(
            "flocks.tool.system.skill.build_description",
            return_value="Refreshed skill description",
        ):
            tools = await runner._build_callable_tool_schema(agent, [])

        assert tools[0]["function"]["name"] == "skill_load"
        assert tools[0]["function"]["description"] == "Original skill description"


class TestBuildSystemPrompts:
    @pytest.mark.asyncio
    async def test_build_system_prompts_reuses_loop_static_cache(self):
        shared_cache = {}
        session = _make_session("ses_prompts_cache")
        runner1 = SessionRunner(session=session, static_cache=shared_cache)
        runner2 = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        env_mock = MagicMock(return_value=["env prompt"])
        runtime_mock = MagicMock(return_value=["runtime prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])
        sandbox_mock = AsyncMock(return_value="sandbox prompt")
        channel_mock = AsyncMock(return_value="channel prompt")

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", env_mock), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", runtime_mock), \
             patch("flocks.session.prompt.SystemPrompt.custom", custom_mock):
            prompts1 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner1.provider_id,
                model_id=runner1.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                static_cache=shared_cache,
                sandbox_prompt_factory=sandbox_mock,
                channel_context_prompt_factory=channel_mock,
                tool_catalog_prompt_factory=lambda: "tool catalog",
            )
            prompts2 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner2.provider_id,
                model_id=runner2.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                static_cache=shared_cache,
                sandbox_prompt_factory=sandbox_mock,
                channel_context_prompt_factory=channel_mock,
                tool_catalog_prompt_factory=lambda: "tool catalog",
            )

        assert prompts1 == prompts2
        env_mock.assert_called_once()
        runtime_mock.assert_called_once()
        custom_mock.assert_awaited_once()
        sandbox_mock.assert_awaited_once()
        channel_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_system_prompts_orders_stable_prefix_before_runtime_tail(self):
        session = _make_session("ses_prompts_order")
        runner = SessionRunner(session=session)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"
        memory_bootstrap_data = {
            "instructions": "memory guidance",
            "main_memory": {
                "path": "MEMORY.md",
                "content": "remembered context",
                "inject": True,
            },
        }
        sandbox_mock = AsyncMock(return_value="sandbox prompt")
        channel_mock = AsyncMock(return_value="channel prompt")

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch.object(SessionPrompt, "_build_tool_guidance_prompt", return_value="tool protocol"), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", return_value=["env prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", return_value=["runtime prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.custom", AsyncMock(return_value=["custom prompt"])):
            prompts = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("bash", "memory_search", "read"),
                memory_bootstrap_data=memory_bootstrap_data,
                tool_catalog_prompt_factory=lambda: "tool catalog",
                sandbox_prompt_factory=sandbox_mock,
                channel_context_prompt_factory=channel_mock,
            )

        assert prompts == [
            "provider prompt",
            "tool protocol",
            "memory guidance",
            "agent prompt",
            "## MEMORY.md\n\nremembered context",
            "tool catalog",
            "env prompt",
            "custom prompt",
            "sandbox prompt",
            "channel prompt",
            "runtime prompt",
        ]

    @pytest.mark.asyncio
    async def test_build_system_prompts_rebuilds_when_tool_revision_changes(self):
        shared_cache = {}
        session = _make_session("ses_prompts_revision")
        runner = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt v1"

        env_mock = MagicMock(return_value=["env prompt"])
        runtime_mock = MagicMock(return_value=["runtime prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])
        sandbox_mock = AsyncMock(return_value="sandbox prompt")
        channel_mock = AsyncMock(return_value="channel prompt")

        catalog_prompts = iter(["tool catalog v1", "tool catalog v2"])

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", env_mock), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", runtime_mock), \
             patch("flocks.session.prompt.SystemPrompt.custom", custom_mock):
            prompts1 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                static_cache=shared_cache,
                sandbox_prompt_factory=sandbox_mock,
                channel_context_prompt_factory=channel_mock,
                tool_catalog_prompt_factory=lambda: next(catalog_prompts),
            )
            agent.prompt = "agent prompt v2"
            prompts2 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                tool_revision=2,
                static_cache=shared_cache,
                sandbox_prompt_factory=sandbox_mock,
                channel_context_prompt_factory=channel_mock,
                tool_catalog_prompt_factory=lambda: next(catalog_prompts),
            )

        assert prompts1 != prompts2
        assert "agent prompt v1" in prompts1
        assert "agent prompt v2" in prompts2
        assert "tool catalog v1" in prompts1
        assert "tool catalog v2" in prompts2
        env_mock.assert_called_once()
        runtime_mock.assert_called_once()
        custom_mock.assert_awaited_once()
        sandbox_mock.assert_awaited_once()
        channel_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_system_prompts_rebuilds_when_agent_prompt_changes(self):
        shared_cache = {}
        session = _make_session("ses_prompts_agent_prompt")
        runner = SessionRunner(session=session, static_cache=shared_cache)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt v1"

        env_mock = MagicMock(return_value=["env prompt"])
        runtime_mock = MagicMock(return_value=["runtime prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", env_mock), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", runtime_mock), \
             patch("flocks.session.prompt.SystemPrompt.custom", custom_mock):
            prompts1 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                static_cache=shared_cache,
            )
            agent.prompt = "agent prompt v2"
            prompts2 = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                static_cache=shared_cache,
            )

        assert prompts1 != prompts2
        assert "agent prompt v1" in prompts1
        assert "agent prompt v2" in prompts2
        env_mock.assert_called_once()
        runtime_mock.assert_called_once()
        custom_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_system_prompts_includes_memory_guidance_when_memory_tools_loaded(self):
        session = _make_session("ses_prompts_memory_guidance")
        runner = SessionRunner(
            session=session,
            memory_bootstrap_data={
                "instructions": "memory guidance",
                "main_memory": {
                    "path": "MEMORY.md",
                    "content": "remembered context",
                    "inject": True,
                },
            },
        )
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", return_value=["env prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", return_value=["runtime prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.custom", AsyncMock(return_value=["custom prompt"])):
            prompts = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("memory_search", "read"),
                memory_bootstrap_data=runner._memory_bootstrap_data,
            )

        assert "memory guidance" in "\n\n".join(prompts)
        assert "## MEMORY.md\n\nremembered context" in prompts
        assert prompts.index("memory guidance") < prompts.index("agent prompt")
        assert prompts.index("memory guidance") < prompts.index("## MEMORY.md\n\nremembered context")

    @pytest.mark.asyncio
    async def test_build_system_prompts_does_not_add_bash_guidance_prompt_when_bash_loaded(self):
        session = _make_session("ses_prompts_no_bash_guidance")
        runner = SessionRunner(session=session)
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", return_value=["env prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", return_value=["runtime prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.custom", AsyncMock(return_value=["custom prompt"])):
            prompts = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("bash", "read"),
            )

        combined = "\n\n".join(prompts)
        assert "## Bash Tool Guidance" not in combined
        assert "PowerShell syntax" not in combined

    @pytest.mark.asyncio
    async def test_build_system_prompts_skips_memory_guidance_without_memory_tools(self):
        session = _make_session("ses_prompts_no_memory_guidance")
        runner = SessionRunner(
            session=session,
            memory_bootstrap_data={
                "instructions": "memory guidance",
                "main_memory": {
                    "path": "MEMORY.md",
                    "content": "remembered context",
                    "inject": True,
                },
            },
        )
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", return_value=["env prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", return_value=["runtime prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.custom", AsyncMock(return_value=["custom prompt"])):
            prompts = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                memory_bootstrap_data=runner._memory_bootstrap_data,
            )

        assert "memory guidance" not in "\n\n".join(prompts)
        assert "## MEMORY.md\n\nremembered context" in prompts

    @pytest.mark.asyncio
    async def test_build_system_prompts_rebuilds_when_prompt_tool_names_change(self):
        shared_cache = {}
        session = _make_session("ses_prompts_tool_names")
        runner = SessionRunner(
            session=session,
            static_cache=shared_cache,
            memory_bootstrap_data={
                "instructions": "memory guidance",
                "main_memory": None,
            },
        )
        agent = _make_agent(name="rex")
        agent.prompt = "agent prompt"

        env_mock = MagicMock(return_value=["env prompt"])
        runtime_mock = MagicMock(return_value=["runtime prompt"])
        custom_mock = AsyncMock(return_value=["custom prompt"])

        with patch("flocks.session.prompt.SystemPrompt.provider", return_value=["provider prompt"]), \
             patch("flocks.session.prompt.SystemPrompt.environment_stable", env_mock), \
             patch("flocks.session.prompt.SystemPrompt.runtime_metadata", runtime_mock), \
             patch("flocks.session.prompt.SystemPrompt.custom", custom_mock):
            prompts_with_memory = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("memory_search", "read"),
                tool_revision=1,
                memory_bootstrap_data=runner._memory_bootstrap_data,
                static_cache=shared_cache,
            )
            prompts_without_memory = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name=agent.name,
                agent_prompt=agent.prompt,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                tool_revision=1,
                memory_bootstrap_data=runner._memory_bootstrap_data,
                static_cache=shared_cache,
            )

        assert prompts_with_memory != prompts_without_memory
        assert "memory guidance" in "\n\n".join(prompts_with_memory)
        assert "memory guidance" not in "\n\n".join(prompts_without_memory)
        env_mock.assert_called_once()
        runtime_mock.assert_called_once()
        custom_mock.assert_awaited_once()

    def test_build_tool_catalog_prompt_for_rex(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        agent.mode = "primary"

        with patch(
            "flocks.session.runner.SessionRunner._list_catalog_tool_infos",
            return_value=[ToolInfo(
                name="plugin_memory",
                description="Access project memory",
                category=ToolCategory.CUSTOM,
                native=False,
                enabled=True,
            )],
        ), patch(
            "flocks.agent.toolset.get_all_enabled_builtin_tool_names",
            return_value=["read", "bash"],
        ), patch(
            "flocks.session.runner.get_always_load_tool_names",
            return_value={"question", "tool_search"},
        ), patch(
            "flocks.command.direct.format_tools_catalog_summary",
            return_value="Available Tools (grouped by category):\n\n**custom**\n- plugin_memory: Access project memory",
        ):
            prompt = runner._build_tool_catalog_prompt(agent)

        assert prompt is not None
        assert "Tool Catalog Awareness" in prompt
        assert "tool_search" in prompt
        assert "InputValidationError" in prompt
        assert "select:<name>[,<name>...]" in prompt
        assert "- plugin_memory: Access project memory" in prompt

    def test_build_tool_catalog_prompt_for_subagent_returns_none(self):
        runner = _make_runner()
        agent = _make_agent(name="plan")
        agent.mode = "subagent"

        prompt = runner._build_tool_catalog_prompt(agent)

        assert prompt is None

    def test_build_tool_catalog_prompt_for_rex_excludes_builtin_and_always_load_tools(self):
        runner = _make_runner()
        agent = _make_agent(name="rex")
        agent.mode = "primary"
        catalog_tools = [
            ToolInfo(name="bash", description="Run commands", category=ToolCategory.CODE, native=True, enabled=True),
            ToolInfo(name="question", description="Ask user a question", category=ToolCategory.SYSTEM, native=True, enabled=True),
            ToolInfo(name="plugin_memory", description="Access project memory", category=ToolCategory.CUSTOM, native=False, enabled=True),
        ]

        with patch(
            "flocks.session.runner.SessionRunner._list_catalog_tool_infos",
            return_value=catalog_tools,
        ), patch(
            "flocks.agent.toolset.get_all_enabled_builtin_tool_names",
            return_value=["bash", "read"],
        ), patch(
            "flocks.session.runner.get_always_load_tool_names",
            return_value={"question", "tool_search"},
        ), patch(
            "flocks.command.direct.format_tools_catalog_summary",
            side_effect=lambda tools, **_: "\n".join(tool.name for tool in tools),
        ) as formatter_mock:
            prompt = runner._build_tool_catalog_prompt(agent)

        assert prompt is not None
        assert "plugin_memory" in prompt
        assert "bash" not in prompt
        assert "question" not in prompt
        formatter_tools = formatter_mock.call_args.kwargs["tools"]
        assert [tool.name for tool in formatter_tools] == ["plugin_memory"]

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

    def test_list_catalog_tool_infos_keeps_always_load_tools_for_subagent(self):
        runner = _make_runner()
        agent = _make_agent(name="plan")
        agent.mode = "subagent"
        agent.tools = ["read"]
        tool_infos = [
            ToolInfo(name="read", description="Read file contents", category=ToolCategory.FILE, native=True, enabled=True),
            ToolInfo(name="question", description="Ask user a question", category=ToolCategory.SYSTEM, native=True, enabled=True),
            ToolInfo(name="tool_search", description="Search tools", category=ToolCategory.SYSTEM, native=True, enabled=True),
            ToolInfo(name="bash", description="Run commands", category=ToolCategory.CODE, native=True, enabled=True),
        ]

        with patch("flocks.session.runner.list_tool_catalog_infos", return_value=tool_infos):
            infos = runner._list_catalog_tool_infos(agent)

        assert [tool.name for tool in infos] == ["read", "question", "tool_search"]

    def test_list_catalog_tool_infos_does_not_fall_back_to_full_catalog_when_tools_missing(self):
        runner = _make_runner()
        agent = _make_agent(name="plan", tools=None)
        agent.mode = "subagent"
        tool_infos = [
            ToolInfo(name="read", description="Read file contents", category=ToolCategory.FILE, native=True, enabled=True),
            ToolInfo(name="question", description="Ask user a question", category=ToolCategory.SYSTEM, native=True, enabled=True),
            ToolInfo(name="tool_search", description="Search tools", category=ToolCategory.SYSTEM, native=True, enabled=True),
            ToolInfo(name="bash", description="Run commands", category=ToolCategory.CODE, native=True, enabled=True),
        ]

        with patch("flocks.session.runner.list_tool_catalog_infos", return_value=tool_infos):
            infos = runner._list_catalog_tool_infos(agent)

        assert [tool.name for tool in infos] == ["question", "tool_search"]


class TestMiniMaxTextToolMode:
    def test_disabled_for_custom_threatbook_minimax(self):
        session = _make_session("ses_minimax_mode")
        runner = SessionRunner(
            session=session,
            provider_id="custom-threatbook-internal",
            model_id="minimax:MiniMax-M2.5",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_disabled_for_custom_tb_inner_minimax(self):
        session = _make_session("ses_minimax_mode_tb_inner")
        runner = SessionRunner(
            session=session,
            provider_id="custom-tb-inner",
            model_id="minimax:MiniMax-M2.7",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_disabled_for_threatbook_cn_llm_minimax(self):
        session = _make_session("ses_minimax_threatbook_cn_llm")
        runner = SessionRunner(
            session=session,
            provider_id="threatbook-cn-llm",
            model_id="minimax-m2.7",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_disabled_for_threatbook_cn_llm_minimax_case_insensitive(self):
        session = _make_session("ses_minimax_threatbook_cn_llm_case")
        runner = SessionRunner(
            session=session,
            provider_id="ThreatBook-CN-LLM",
            model_id="MiniMax-M2.5",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_disabled_for_threatbook_cn_llm_non_minimax(self):
        # Other models routed through the same gateway (e.g. qwen, GLM) keep
        # the standard OpenAI native function-calling path.
        session = _make_session("ses_threatbook_cn_llm_qwen")
        runner = SessionRunner(
            session=session,
            provider_id="threatbook-cn-llm",
            model_id="qwen3.6-plus",
        )
        assert runner._should_use_text_tool_call_mode() is False

    def test_disabled_for_other_models(self):
        session = _make_session("ses_normal_mode")
        runner = SessionRunner(
            session=session,
            provider_id="anthropic",
            model_id="claude-sonnet-4-5-20250929",
        )
        assert runner._should_use_text_tool_call_mode() is False

    @pytest.mark.asyncio
    async def test_system_prompts_add_minimax_native_tool_guidance(self):
        session = _make_session("ses_minimax_prompt")
        runner = SessionRunner(
            session=session,
            provider_id="custom-tb-inner",
            model_id="minimax:MiniMax-M2.5",
        )

        with patch("flocks.session.prompt.SystemPrompt.environment", AsyncMock(return_value=["env prompt"])), \
             patch("flocks.session.prompt.SystemPrompt.custom", AsyncMock(return_value=["custom prompt"])):
            prompts = await SessionPrompt.build_system_prompts(
                session_id=session.id,
                session_directory=session.directory,
                agent_name="rex",
                agent_prompt=None,
                provider_id=runner.provider_id,
                model_id=runner.model_id,
                prompt_tool_names=("read",),
                use_text_tool_call_mode=runner._should_use_text_tool_call_mode(),
            )

        combined = "\n\n".join(prompts)
        assert "native API tool-calling" in combined
        assert "prefer actually invoking the needed tool" in combined
        assert "Misleading behavior" in combined
        assert "<minimax:tool_call>" not in combined

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


@pytest.mark.asyncio
async def test_to_chat_messages_uses_structured_anthropic_system_blocks(monkeypatch):
    runner = SessionRunner(
        session=_make_session("ses_anthropic_system_blocks"),
        provider_id="anthropic",
        model_id="claude-sonnet",
    )
    message = SimpleNamespace(id="msg_user", role="user", content="hello")

    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hello"))

    chat_messages = await runner._to_chat_messages(
        [message],
        ["provider prompt", "agent prompt", "context prompt", "runtime prompt"],
    )

    assert chat_messages[0].role == "system"
    assert isinstance(chat_messages[0].content, list)
    assert chat_messages[0].content[1]["cache_control"] == {"type": "ephemeral"}
    assert chat_messages[0].content[-1]["text"] == "runtime prompt"


@pytest.mark.asyncio
async def test_to_chat_messages_keeps_joined_system_prompt_for_openai(monkeypatch):
    runner = SessionRunner(
        session=_make_session("ses_openai_system_blocks"),
        provider_id="openai",
        model_id="gpt-5",
    )
    message = SimpleNamespace(id="msg_user", role="user", content="hello")

    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hello"))

    chat_messages = await runner._to_chat_messages(
        [message],
        ["provider prompt", "agent prompt"],
    )

    assert chat_messages[0].role == "system"
    assert chat_messages[0].content == "provider prompt\n\nagent prompt"


@pytest.mark.asyncio
async def test_to_chat_messages_invalidates_shared_cache_when_message_parts_change():
    session = await Session.create(
        project_id="test_runner_chat_cache_invalidation",
        directory="/tmp/runner-cache",
    )
    assistant_message = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="starting",
    )
    runner = SessionRunner(session=session, static_cache={})

    first_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(first_messages) == 1
    assert first_messages[0].role == "assistant"
    assert first_messages[0].tool_calls is None

    await Message.add_part(
        session.id,
        assistant_message.id,
        ToolPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            callID="call_cache_fix",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "continue"},
                time={"start": 1},
            ),
        ),
    )

    second_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(second_messages) == 2
    assert second_messages[0].role == "assistant"
    assert second_messages[0].tool_calls is not None
    assert second_messages[0].tool_calls[0]["function"]["name"] == "task"
    assert second_messages[1].role == "tool"
    assert second_messages[1].tool_call_id == "call_cache_fix"
    assert second_messages[1].content == "Error: Tool execution was interrupted"


@pytest.mark.asyncio
async def test_to_chat_messages_preserves_assistant_reasoning_for_replay():
    session = await Session.create(
        project_id="test_runner_reasoning_replay",
        directory="/tmp/runner-reasoning",
    )
    assistant_message = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
    )
    runner = SessionRunner(session=session, static_cache={})

    await Message.add_part(
        session.id,
        assistant_message.id,
        ReasoningPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            text="Need to call the tool first.",
            time=PartTime(start=1),
        ),
    )
    await Message.add_part(
        session.id,
        assistant_message.id,
        ToolPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            callID="call_reasoning_replay",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "continue"},
                time={"start": 1},
            ),
        ),
    )

    chat_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(chat_messages) == 2
    assert chat_messages[0].role == "assistant"
    assert chat_messages[0].reasoning == "Need to call the tool first."
    assert chat_messages[0].tool_calls is not None
    assert chat_messages[0].tool_calls[0]["function"]["name"] == "task"
    assert chat_messages[1].role == "tool"
    assert chat_messages[1].tool_call_id == "call_reasoning_replay"


@pytest.mark.asyncio
async def test_to_chat_messages_restores_provider_reasoning_fields_from_metadata(monkeypatch):
    session = await Session.create(
        project_id="test_runner_reasoning_metadata_replay",
        directory="/tmp/runner-reasoning-metadata",
    )
    assistant_message = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
    )
    runner = SessionRunner(session=session, static_cache={})
    runner.provider_id = "alibaba"
    runner.model_id = "qwen3-max"

    monkeypatch.setattr(
        runner_mod.Provider,
        "get_model",
        lambda _model_id: SimpleNamespace(
            capabilities=SimpleNamespace(
                interleaved={
                    "field": "reasoning_content",
                    "echo": "tool_calls",
                    "cross_provider_policy": "promote",
                }
            )
        ),
    )

    await Message.add_part(
        session.id,
        assistant_message.id,
        ReasoningPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            text="Need to call the tool first.",
            metadata={
                "reasoningContent": "Need to call the tool first.",
                "reasoningSource": "native_reasoning_content",
            },
            time=PartTime(start=1),
        ),
    )
    await Message.add_part(
        session.id,
        assistant_message.id,
        ToolPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            callID="call_reasoning_metadata",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "continue"},
                time={"start": 1},
            ),
        ),
    )

    chat_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(chat_messages) == 2
    assert chat_messages[0].reasoning == "Need to call the tool first."
    assert chat_messages[0].reasoning_content == "Need to call the tool first."
    assert chat_messages[0].reasoning_source == "native_reasoning_content"
    assert chat_messages[0].tool_calls[0]["function"]["name"] == "task"


@pytest.mark.asyncio
async def test_to_chat_messages_restores_redacted_anthropic_thinking_blocks(monkeypatch):
    session = await Session.create(
        project_id="test_runner_redacted_thinking",
        directory="/tmp/runner-redacted-thinking",
    )
    assistant_message = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
    )
    runner = SessionRunner(session=session, static_cache={})
    runner.provider_id = "anthropic"
    runner.model_id = "claude-sonnet-4-6"

    monkeypatch.setattr(
        runner_mod.Provider,
        "get_model",
        lambda _model_id: SimpleNamespace(
            capabilities=SimpleNamespace(interleaved=None)
        ),
    )

    await Message.add_part(
        session.id,
        assistant_message.id,
        ReasoningPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            text="",
            metadata={
                "reasoningField": "thinking",
                "reasoningSource": "anthropic_redacted_thinking",
                "redactedThinkingData": "opaque_blob",
            },
            time=PartTime(start=1),
        ),
    )
    await Message.add_part(
        session.id,
        assistant_message.id,
        ToolPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            callID="call_redacted_reasoning",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "continue"},
                time={"start": 1},
            ),
        ),
    )

    chat_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(chat_messages) == 2
    assert chat_messages[0].custom_settings["anthropic_thinking_blocks"] == [
        {"type": "redacted_thinking", "data": "opaque_blob"}
    ]
    assert chat_messages[0].tool_calls[0]["function"]["name"] == "task"


@pytest.mark.asyncio
async def test_to_chat_messages_prefers_provider_specific_interleaved_resolution(monkeypatch):
    session = await Session.create(
        project_id="test_runner_provider_specific_interleaved",
        directory="/tmp/runner-provider-interleaved",
    )
    assistant_message = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
    )
    runner = SessionRunner(session=session, static_cache={})
    runner.provider_id = "deepseek"
    runner.model_id = "shared-model"

    monkeypatch.setattr(
        runner_mod.Provider,
        "resolve_model",
        lambda provider_id, model_id: (
            SimpleNamespace(
                capabilities=SimpleNamespace(
                    interleaved={
                        "field": "reasoning_content",
                        "echo": "tool_calls",
                        "placeholder": " ",
                        "cross_provider_policy": "placeholder",
                    }
                )
            )
            if provider_id == "deepseek" and model_id == "shared-model"
            else None
        ),
    )
    monkeypatch.setattr(
        runner_mod.Provider,
        "get_model",
        lambda _model_id: SimpleNamespace(
            capabilities=SimpleNamespace(
                interleaved={
                    "field": "reasoning_details",
                    "echo": "tool_calls",
                    "cross_provider_policy": "promote",
                }
            )
        ),
    )

    await Message.add_part(
        session.id,
        assistant_message.id,
        ReasoningPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            text="Prior provider chain of thought",
            time=PartTime(start=1),
        ),
    )
    await Message.add_part(
        session.id,
        assistant_message.id,
        ToolPart(
            sessionID=session.id,
            messageID=assistant_message.id,
            callID="call_provider_specific_interleaved",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "continue"},
                time={"start": 1},
            ),
        ),
    )

    chat_messages = await runner._to_chat_messages([assistant_message], [])

    assert len(chat_messages) == 2
    assert chat_messages[0].reasoning_content == " "
    assert chat_messages[0].reasoning_details is None
    assert chat_messages[0].reasoning_source == "placeholder"


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
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
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


@pytest.mark.asyncio
async def test_call_llm_skips_observability_when_langfuse_inactive(monkeypatch):
    runner = _make_runner("ses_runner_langfuse_inactive")
    runner.callbacks = RunnerCallbacks()

    agent = SimpleNamespace(name="rex")
    assistant_msg = SimpleNamespace(id="msg_assistant_langfuse")
    provider = MagicMock()

    trace_mock = MagicMock()
    generation_mock = MagicMock()

    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod, "trace_scope", trace_mock)
    monkeypatch.setattr(runner_mod, "generation_scope", generation_mock)

    result = await runner._call_llm(
        provider=provider,
        messages=[runner_mod.ChatMessage(role="system", content="system only")],
        tools=[],
        agent=agent,
        assistant_msg=assistant_msg,
    )

    assert result.action == "stop"
    assert result.error == "No valid messages to send to LLM"
    trace_mock.assert_not_called()
    generation_mock.assert_not_called()


@pytest.mark.asyncio
async def test_call_llm_skips_llm_hook_payload_preparation_without_handlers(monkeypatch):
    runner = _make_runner("ses_runner_no_llm_hooks")
    runner.callbacks = RunnerCallbacks()

    class _ProviderStub:
        async def chat_stream(self, **kwargs):  # noqa: ANN003
            del kwargs
            yield SimpleNamespace(delta="done", finish_reason="stop")

    user_message = SimpleNamespace(
        role="user",
        content="hi",
        model_dump=MagicMock(side_effect=AssertionError("message serialization should be skipped")),
    )
    deep_copy_mock = MagicMock(side_effect=AssertionError("tool deepcopy should be skipped"))
    run_before_mock = AsyncMock()
    run_after_mock = AsyncMock()

    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_llm_before", run_before_mock)
    monkeypatch.setattr(runner_mod.HookPipeline, "run_llm_after", run_after_mock)
    monkeypatch.setattr(runner_mod.copy, "deepcopy", deep_copy_mock)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))

    result = await runner._call_llm(
        provider=_ProviderStub(),
        messages=[user_message],
        tools=[{"type": "function", "function": {"name": "read"}}],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_assistant_no_llm_hooks"),
    )

    assert result.action == "stop"
    assert result.content == "done"
    deep_copy_mock.assert_not_called()
    run_before_mock.assert_not_awaited()
    run_after_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_step_uses_loaded_tool_schema_names_for_prompt_guidance(monkeypatch):
    runner = _make_runner("ses_runner_prompt_guidance_tool_names")
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())

    last_user = UserMessageInfo(
        id="msg_user_prompt_guidance",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=["read"])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id="msg_assistant_prompt_guidance")
    build_system_prompts = AsyncMock(return_value=[])
    tool_schema = [
        {"type": "function", "function": {"name": "memory_search", "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "bash", "description": "", "parameters": {}}},
    ]

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", build_system_prompts)
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=tool_schema))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner,
        "_call_llm",
        AsyncMock(return_value=StepResult(action="stop", content="done")),
    )

    result = await runner._process_step([last_user], last_user)

    assert result.content == "done"
    build_system_prompts.assert_awaited_once()
    assert build_system_prompts.await_args.kwargs["prompt_tool_names"] == ("bash", "memory_search")


@pytest.mark.asyncio
async def test_process_step_records_usage_after_success(monkeypatch):
    runner = _make_runner("ses_runner_usage_success")
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())

    last_user = UserMessageInfo(
        id="msg_user_usage_success",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id="msg_assistant_usage_success")
    update_mock = AsyncMock(return_value=None)
    record_mock = AsyncMock(return_value=None)
    usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", update_mock)
    monkeypatch.setattr(
        runner,
        "_call_llm",
        AsyncMock(return_value=StepResult(action="stop", content="done", usage=usage)),
    )
    monkeypatch.setattr(runner, "_record_usage_if_available", record_mock)

    result = await runner._process_step([last_user], last_user)

    assert result.content == "done"
    record_mock.assert_awaited_once_with(usage, message_id=assistant_msg.id)
    update_mock.assert_any_await(runner.session.id, assistant_msg.id, finish="stop")


@pytest.mark.asyncio
async def test_process_step_empty_retry_records_usage_per_attempt(monkeypatch):
    """Each empty-response attempt records its own usage so that provider
    charges are not lost when the model returns tokens but no content."""
    runner = _make_runner("ses_runner_usage_retry")
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())

    last_user = UserMessageInfo(
        id="msg_user_usage_retry",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id="msg_assistant_usage_retry")
    record_mock = AsyncMock(return_value=None)
    sleep_mock = AsyncMock(return_value=None)
    first_usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
    second_usage = {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner,
        "_call_llm",
        AsyncMock(
            side_effect=[
                StepResult(action="stop", content="", usage=first_usage),
                StepResult(action="stop", content="recovered", usage=second_usage),
            ]
        ),
    )
    monkeypatch.setattr(runner, "_record_usage_if_available", record_mock)
    monkeypatch.setattr(runner_mod.SessionRetry, "sleep", sleep_mock)

    result = await runner._process_step([last_user], last_user)

    assert result.content == "recovered"
    sleep_mock.assert_awaited_once()
    # Both the empty attempt and the successful attempt must be recorded so
    # that provider charges are not silently dropped during retries.
    assert record_mock.await_count == 2
    record_mock.assert_any_await(first_usage, message_id=assistant_msg.id)
    record_mock.assert_any_await(second_usage, message_id=assistant_msg.id)


@pytest.mark.asyncio
async def test_process_step_uses_default_max_steps_when_agent_steps_missing(monkeypatch):
    runner = _make_runner("ses_runner_default_max_steps")
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())
    runner._step = DEFAULT_MAX_TOOL_STEPS

    last_user = UserMessageInfo(
        id="msg_user_default_max_steps",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=["read"])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id="msg_assistant_default_max_steps")
    sentinel_tools = [{"type": "function", "function": {"name": "read", "description": "", "parameters": {}}}]
    captured = {}

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=sentinel_tools))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        captured["tools"] = tools
        return StepResult(action="stop", content="done")

    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    result = await runner._process_step([last_user], last_user)

    assert result.action == "stop"
    assert captured["tools"] == []


@pytest.mark.asyncio
async def test_process_step_respects_explicit_agent_steps_over_default(monkeypatch):
    runner = _make_runner("ses_runner_explicit_max_steps")
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())
    runner._step = DEFAULT_MAX_TOOL_STEPS

    last_user = UserMessageInfo(
        id="msg_user_explicit_max_steps",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    agent = SimpleNamespace(name="rex", steps=DEFAULT_MAX_TOOL_STEPS + 1, mode="primary", prompt="", tools=["read"])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id="msg_assistant_explicit_max_steps")
    sentinel_tools = [{"type": "function", "function": {"name": "read", "description": "", "parameters": {}}}]
    captured = {}

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=sentinel_tools))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
    )
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        captured["tools"] = tools
        return StepResult(action="stop", content="done")

    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    result = await runner._process_step([last_user], last_user)

    assert result.action == "stop"
    assert captured["tools"] == sentinel_tools


@pytest.mark.asyncio
async def test_process_step_halts_after_third_exact_tool_only_turn(monkeypatch):
    shared_cache = {}
    provider = MagicMock()
    provider.is_configured.return_value = True
    update_mock = AsyncMock(return_value=None)
    create_mock = AsyncMock(
        side_effect=[
            SimpleNamespace(id="msg_assistant_tool_loop_1"),
            SimpleNamespace(id="msg_assistant_tool_loop_2"),
            SimpleNamespace(id="msg_assistant_tool_loop_3"),
        ]
    )

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        del provider, messages, tools, agent, assistant_msg
        return StepResult(
            action="continue",
            tool_calls=[ToolCall(id="c-loop", name="echo_tool", arguments={"text": "loop"})],
        )

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=SimpleNamespace(
        name="rex",
        steps=None,
        mode="primary",
        prompt="",
        tools=["echo_tool"],
    )))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "create", create_mock)
    monkeypatch.setattr(runner_mod.Message, "update", update_mock)
    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    last_user = UserMessageInfo(
        id="msg_user_tool_loop_guard",
        sessionID="ses_runner_tool_loop_guard",
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    for idx in range(1, 4):
        runner = SessionRunner(session=_make_session("ses_runner_tool_loop_guard"), static_cache=shared_cache)
        runner.callbacks = RunnerCallbacks(on_error=AsyncMock())
        monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[
            {"type": "function", "function": {"name": "echo_tool", "description": "", "parameters": {}}}
        ]))
        monkeypatch.setattr(
            runner,
            "_to_chat_messages",
            AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]),
        )
        result = await runner._process_step([last_user], last_user)
        if idx < 3:
            assert result.action == "continue"
        else:
            assert result.action == "stop"
            assert "Stopped the loop because `echo_tool` was called 3 times in a row" in result.content

    assert update_mock.await_args_list[-2].kwargs["content"].startswith("Stopped the loop because `echo_tool`")
    assert update_mock.await_args_list[-1].kwargs["finish"] == "stop"


@pytest.mark.asyncio
async def test_record_usage_if_available_swallows_import_error():
    """ImportError from the usage service import must not propagate out of
    _record_usage_if_available so that a CLI-only environment (where fastapi /
    server deps may be absent) never turns a successful step into an error."""
    runner = _make_runner("ses_runner_import_error")
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    with patch.dict("sys.modules", {"flocks.provider.usage_service": None}):
        # Should complete without raising, even though the import will fail.
        await runner._record_usage_if_available(usage)


@pytest.mark.asyncio
async def test_record_usage_if_available_passes_message_id():
    runner = _make_runner("ses_runner_message_id")
    usage = {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
        "cache_creation_input_tokens": 4,
    }
    request_cls = MagicMock(return_value=SimpleNamespace())
    record_usage_mock = AsyncMock(return_value=None)
    fake_module = SimpleNamespace(
        RecordUsageRequest=request_cls,
        record_usage=record_usage_mock,
    )
    runner._resolve_usage_pricing = MagicMock(return_value=None)

    with patch.dict("sys.modules", {"flocks.provider.usage_service": fake_module}):
        await runner._record_usage_if_available(usage, message_id="msg_assistant")

    request_cls.assert_called_once()
    kwargs = request_cls.call_args.kwargs
    assert kwargs["session_id"] == runner.session.id
    assert kwargs["message_id"] == "msg_assistant"
    assert kwargs["cache_write_tokens"] == 4
    record_usage_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_usage_if_available_swallows_runtime_error():
    """Any exception raised by record_usage() itself must also be silently
    swallowed so that usage-recording failures never corrupt step results."""
    runner = _make_runner("ses_runner_record_error")
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    fake_module = SimpleNamespace(
        RecordUsageRequest=MagicMock(return_value=SimpleNamespace()),
        record_usage=AsyncMock(side_effect=RuntimeError("db unavailable")),
    )
    with patch.dict("sys.modules", {"flocks.provider.usage_service": fake_module}):
        await runner._record_usage_if_available(usage)


@pytest.mark.asyncio
async def test_to_chat_messages_expands_workflow_node_ref_marker(monkeypatch):
    runner = _make_runner("ses_runner_node_ref")
    user_message = UserMessageInfo(
        id="msg_user_node_ref",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )

    monkeypatch.setattr(
        runner_mod.Message,
        "parts",
        AsyncMock(return_value=[
            SimpleNamespace(
                type="text",
                text="@@node:query_fofa|python\n只修改这个节点的代码并保留其他节点不变",
            ),
        ]),
    )

    chat_messages = await runner._to_chat_messages([user_message], [])

    assert len(chat_messages) == 1
    assert chat_messages[0].role == "user"
    assert isinstance(chat_messages[0].content, str)
    assert "Selected workflow node context:" in chat_messages[0].content
    assert "node_id: query_fofa" in chat_messages[0].content
    assert "node_type: python" in chat_messages[0].content
    assert "只修改这个节点的代码并保留其他节点不变" in chat_messages[0].content
