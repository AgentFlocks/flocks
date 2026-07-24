from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from flocks.server.routes.session import (
    PromptRequest,
    _event_text_for_execution_mode,
    _validate_execution_mode_request,
)
from flocks.session.execution_mode import (
    SessionExecutionMode,
    execution_mode_prompt,
    is_tool_allowed,
    runtime_execution_mode,
)
from flocks.session.interaction_queue import InteractionQueue
from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolRegistry,
    ToolResult,
)


def test_prompt_request_defaults_to_build_and_accepts_plan() -> None:
    default_request = PromptRequest(parts=[{"type": "text", "text": "hello"}])
    plan_request = PromptRequest.model_validate({
        "parts": [{"type": "text", "text": "hello"}],
        "executionMode": "plan",
    })

    assert default_request.execution_mode == SessionExecutionMode.BUILD
    assert plan_request.execution_mode == SessionExecutionMode.PLAN


def test_prompt_request_rejects_removed_ask_mode() -> None:
    with pytest.raises(ValidationError):
        PromptRequest.model_validate({
            "parts": [{"type": "text", "text": "hello"}],
            "executionMode": "ask",
        })


def test_goal_transport_uses_build_permissions_and_slash_dispatch() -> None:
    parts = [{"type": "text", "text": "  finish the feature  "}]

    assert runtime_execution_mode("goal") == SessionExecutionMode.BUILD
    assert _event_text_for_execution_mode(
        parts,
        SessionExecutionMode.GOAL,
    ) == "/goal finish the feature"


def test_goal_requires_text_only_objective() -> None:
    empty = PromptRequest.model_validate({
        "parts": [],
        "executionMode": "goal",
    })
    attachment = PromptRequest.model_validate({
        "parts": [
            {"type": "text", "text": "inspect this"},
            {"type": "file", "url": "file:///tmp/report.txt"},
        ],
        "executionMode": "goal",
    })

    with pytest.raises(HTTPException, match="non-empty text objective"):
        _validate_execution_mode_request(empty)
    with pytest.raises(HTTPException, match="does not support attachments"):
        _validate_execution_mode_request(attachment)


def test_plan_uses_read_only_permission_rules() -> None:
    assert is_tool_allowed(SessionExecutionMode.PLAN, "read")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "grep")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "question")
    assert not is_tool_allowed(SessionExecutionMode.PLAN, "bash")
    assert not is_tool_allowed(SessionExecutionMode.PLAN, "edit")
    assert not is_tool_allowed(SessionExecutionMode.PLAN, "unknown_plugin_tool")

    assert is_tool_allowed(SessionExecutionMode.BUILD, "bash")
    assert "decision-complete implementation plan" in execution_mode_prompt("plan")
    assert execution_mode_prompt("build") == ""


@pytest.mark.asyncio
async def test_prompt_queue_preserves_execution_mode() -> None:
    session_id = "execution-mode-queue"
    await InteractionQueue.clear(session_id)

    item = await InteractionQueue.enqueue(
        session_id,
        parts=[{"type": "text", "text": "plan this"}],
        execution_mode=SessionExecutionMode.PLAN,
    )

    queued = await InteractionQueue.list(session_id)
    assert item.executionMode == SessionExecutionMode.PLAN
    assert queued[0].executionMode == SessionExecutionMode.PLAN

    await InteractionQueue.clear(session_id)


@pytest.mark.asyncio
async def test_registry_denies_disallowed_tool_before_handler(monkeypatch) -> None:
    called = False

    async def handler(_ctx, **_kwargs):
        nonlocal called
        called = True
        return ToolResult(success=True, output="unexpected")

    tool = Tool(
        info=ToolInfo(
            name="write_mode_test",
            description="Mutating test tool",
            category=ToolCategory.FILE,
        ),
        handler=handler,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(lambda _cls, _name: tool),
    )

    result = await ToolRegistry.execute(
        "write_mode_test",
        ctx=ToolContext(
            session_id="session-1",
            message_id="message-1",
            extra={"execution_mode": "plan"},
        ),
    )

    assert not result.success
    assert "not available" in (result.error or "")
    assert not called


@pytest.mark.asyncio
async def test_runner_filters_tools_with_message_mode(monkeypatch) -> None:
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-1")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    agent = SimpleNamespace(tools=["read", "bash"])

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="read"),
            SimpleNamespace(name="bash"),
        ],
        metadata={},
    )

    async def list_tools(**_kwargs):
        return result

    monkeypatch.setattr(
        "flocks.session.runner.list_session_callable_tool_infos",
        list_tools,
    )
    messages = [
        SimpleNamespace(
            role="user",
            executionMode=SessionExecutionMode.PLAN,
        )
    ]

    tools, metadata = await runner._list_callable_tool_infos_for_turn(
        agent,
        messages,
    )

    assert [tool.name for tool in tools] == ["read"]
    assert metadata["executionMode"] == "plan"
    assert metadata["modeAllowedToolNames"] == ["read"]
