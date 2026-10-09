from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    tool_call_denial_reason,
)
from flocks.session.interaction_queue import InteractionQueue
from flocks.session.plan_file import is_current_plan_path, session_plan_file
from flocks.session.session import SessionInfo, SessionTime
from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    ParameterType,
)
from flocks.tool.file.write import write_tool


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
    assert is_tool_allowed(SessionExecutionMode.PLAN, "plan_exit")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "bash")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "edit")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "write")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "unknown_plugin_tool")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "task")
    assert is_tool_allowed(SessionExecutionMode.PLAN, "delegate_task")
    assert not is_tool_allowed(SessionExecutionMode.PLAN, "run_slash_command")

    assert is_tool_allowed(SessionExecutionMode.BUILD, "bash")
    assert not is_tool_allowed(SessionExecutionMode.BUILD, "plan_exit")
    assert "decision-complete implementation plan" in execution_mode_prompt("plan")
    assert "material clarification question" in execution_mode_prompt("plan")
    assert "call plan_exit" in execution_mode_prompt("plan")
    assert "`explore` and `librarian`" in execution_mode_prompt("plan")
    assert execution_mode_prompt("build") == ""


@pytest.mark.parametrize("tool_name", ["task", "delegate_task"])
def test_plan_delegation_only_allows_explore_and_librarian(tool_name) -> None:
    ctx = ToolContext(session_id="session-1", message_id="message-1")

    for subagent_type in ("explore", "librarian"):
        assert tool_call_denial_reason(
            SessionExecutionMode.PLAN,
            tool_name,
            {"subagent_type": subagent_type},
            ctx,
        ) is None

    for arguments in (
        {"subagent_type": "general"},
        {"session_id": "child-session"},
        {},
    ):
        reason = tool_call_denial_reason(
            SessionExecutionMode.PLAN,
            tool_name,
            arguments,
            ctx,
        )
        assert reason is not None
        assert "explore, librarian" in reason


@pytest.fixture
def communication_sessions(monkeypatch, tmp_path):
    from flocks.session.agent_inbox import AgentInbox
    from flocks.session.message import Message
    from flocks.session.session import Session

    sessions = {
        session_id: SessionInfo(
            id=session_id,
            projectID="project",
            directory=str(tmp_path),
            agent="rex-junior",
            parentID=parent_id,
            permission=[{"permission": "edit", "action": "allow"}],
        )
        for session_id, parent_id in (
            ("root", None), ("parent", "root"),
            ("source", "parent"), ("target", "source"),
        )
    }
    histories = {session_id: [] for session_id in sessions}
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(side_effect=sessions.get))
    monkeypatch.setattr(Session, "is_lifecycle_transitioning", lambda _sid: False)
    monkeypatch.setattr(Message, "list", AsyncMock(side_effect=histories.get))
    monkeypatch.setattr(AgentInbox, "_inboxes", {})
    post = Mock(wraps=AgentInbox.post)
    wake = Mock()
    monkeypatch.setattr(AgentInbox, "post", post)
    monkeypatch.setattr(AgentInbox, "wake", wake)
    permission = AsyncMock()
    ctx = ToolContext(
        session_id="source",
        message_id="message",
        agent="rex-junior",
        extra={"execution_mode": "plan"},
        permission_callback=permission,
    )
    return SimpleNamespace(
        sessions=sessions, histories=histories, ctx=ctx,
        permission=permission, post=post, wake=wake, inboxes=AgentInbox,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_agent,user_agent,user_mode,sibling",
    [
        ("rex-junior", None, None, False),
        ("rex-junior", "rex-junior", "build", False),
        ("explore", "rex-junior", "build", False),
        ("librarian", "rex-junior", "goal", False),
        ("rex-junior", "rex-junior", "build", True),
    ],
)
async def test_plan_send_blocks_write_capable_recipient_before_permission(
    communication_sessions, session_agent, user_agent, user_mode, sibling,
) -> None:
    from flocks.tool.agent.communication import send_to_session

    state = communication_sessions
    target = state.sessions["target"]
    target.agent = session_agent
    if sibling:
        target.parent_id = "parent"
    if user_agent is not None:
        state.histories["target"] = [
            SimpleNamespace(role="user", agent="explore", executionMode="plan"),
            SimpleNamespace(role="user", agent=user_agent, executionMode=user_mode),
            SimpleNamespace(role="assistant", agent="explore", executionMode="plan"),
        ]
    before = target.model_dump()
    inbox = state.inboxes.get("target")

    result = await send_to_session(state.ctx, "target", "Implement the change")

    assert not result.success
    assert "Plan mode" in result.error
    state.permission.assert_not_awaited()
    state.post.assert_not_called()
    state.wake.assert_not_called()
    assert not inbox.messages
    assert not inbox.ready.is_set()
    assert target.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_agent,user_agent,user_mode",
    [
        ("rex-junior", "explore", "build"),
        ("rex-junior", "librarian", "build"),
        ("rex-junior", "rex-junior", "plan"),
        ("explore", None, None),
        ("librarian", None, None),
        ("explore", "", "build"),
    ],
)
async def test_plan_send_allows_read_only_recipient_without_changing_permissions(
    communication_sessions, session_agent, user_agent, user_mode,
) -> None:
    from flocks.tool.agent.communication import send_to_session

    state = communication_sessions
    target = state.sessions["target"]
    target.agent = session_agent
    if user_agent is not None:
        state.histories["target"] = [
            SimpleNamespace(role="user", agent="rex-junior", executionMode="build"),
            SimpleNamespace(role="user", agent=user_agent, executionMode=user_mode),
            SimpleNamespace(role="assistant", agent="rex-junior", executionMode="build"),
        ]
    before = target.model_dump()

    result = await send_to_session(state.ctx, "target", "Inspect the implementation")

    assert result.success
    state.permission.assert_awaited_once()
    state.post.assert_called_once()
    state.wake.assert_called_once_with("target", None)
    assert state.inboxes.get("target").messages[0].content == "Inspect the implementation"
    assert target.model_dump() == before
    if user_agent is not None:
        assert state.histories["target"][-2].agent == user_agent
        assert state.histories["target"][-2].executionMode == user_mode


@pytest.mark.asyncio
@pytest.mark.parametrize("target_id", ["parent", "root"])
async def test_plan_send_allows_reports_to_actual_parent_or_ancestor(
    communication_sessions, target_id,
) -> None:
    from flocks.tool.agent.communication import send_to_session

    state = communication_sessions
    state.histories[target_id] = [
        SimpleNamespace(role="user", agent="rex-junior", executionMode="build"),
    ]

    result = await send_to_session(state.ctx, target_id, "Investigation progress")

    assert result.success
    state.permission.assert_awaited_once()
    state.wake.assert_called_once_with(target_id, None)
    assert state.inboxes.get(target_id).messages[0].content == "Investigation progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["build", "goal", None])
async def test_build_send_to_write_capable_recipient_is_unchanged(
    communication_sessions, mode,
) -> None:
    from flocks.tool.agent.communication import send_to_session

    state = communication_sessions
    state.ctx.extra = {"execution_mode": mode} if mode else {}

    result = await send_to_session(state.ctx, "target", "Implement the change")

    assert result.success
    state.permission.assert_awaited_once()
    state.wake.assert_called_once_with("target", None)
    assert state.inboxes.get("target").messages[0].content == "Implement the change"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_agent,initial_mode", [("rex-junior", "plan"), ("explore", "build")],
)
async def test_plan_send_rechecks_recipient_mode_and_agent_after_permission(
    communication_sessions, initial_agent, initial_mode,
) -> None:
    from flocks.tool.agent.communication import send_to_session

    state = communication_sessions
    state.sessions["target"].agent = "explore"
    state.histories["target"] = [
        SimpleNamespace(role="user", agent=initial_agent, executionMode=initial_mode),
    ]

    async def change_recipient(_request):
        state.histories["target"].append(
            SimpleNamespace(role="user", agent="rex-junior", executionMode="build"),
        )

    state.permission.side_effect = change_recipient
    inbox = state.inboxes.get("target")

    result = await send_to_session(state.ctx, "target", "Inspect the implementation")

    assert not result.success
    assert "Plan mode" in result.error
    state.permission.assert_awaited_once()
    state.post.assert_not_called()
    state.wake.assert_not_called()
    assert not inbox.messages
    assert not inbox.ready.is_set()


def test_plan_file_is_stable_and_session_scoped(tmp_path) -> None:
    first = SessionInfo(
        id="session-1",
        slug="first-plan",
        projectID="project-1",
        directory=str(tmp_path),
        time=SessionTime(created=1234, updated=1234),
    )
    second = first.model_copy(update={
        "slug": "second-plan",
        "time": SessionTime(created=5678, updated=5678),
    })

    first_plan = session_plan_file(first)
    second_plan = session_plan_file(second)

    assert first_plan.path == tmp_path / ".flocks" / "plans" / "1234-first-plan.md"
    assert first_plan.relative_path == ".flocks/plans/1234-first-plan.md"
    assert first_plan.permission_path == ".flocks/plans/1234-first-plan.md"
    assert session_plan_file(first) == first_plan
    assert second_plan.path != first_plan.path


def test_plan_file_uses_worktree_root_and_directory_relative_tool_path(
    tmp_path,
) -> None:
    worktree = tmp_path / "repo"
    directory = worktree / "packages" / "app"
    directory.mkdir(parents=True)
    session = SessionInfo(
        slug="nested-plan",
        projectID="project-1",
        directory=str(directory),
        time=SessionTime(created=1234, updated=1234),
    )

    plan = session_plan_file(session, worktree=str(worktree))

    assert plan.path == worktree / ".flocks" / "plans" / "1234-nested-plan.md"
    assert plan.relative_path == "../../.flocks/plans/1234-nested-plan.md"
    assert plan.permission_path == ".flocks/plans/1234-nested-plan.md"


def test_plan_prompt_describes_create_then_incremental_edit(tmp_path) -> None:
    session = SessionInfo(
        slug="prompt-plan",
        projectID="project-1",
        directory=str(tmp_path),
        time=SessionTime(created=1234, updated=1234),
    )
    plan = session_plan_file(session)

    create_prompt = execution_mode_prompt("plan", session=session)
    assert plan.relative_path in create_prompt
    assert "No plan file exists yet" in create_prompt
    assert "Bash is available only for read-only exploration" in create_prompt

    plan.path.parent.mkdir(parents=True)
    plan.path.write_text("# Plan\n", encoding="utf-8")

    edit_prompt = execution_mode_prompt("plan", session=session)
    assert plan.relative_path in edit_prompt
    assert "already exists" in edit_prompt
    assert "update it incrementally" in edit_prompt


def test_plan_path_guard_rejects_symlink_escape(tmp_path) -> None:
    plan_relative = ".flocks/plans/1234-plan.md"
    plan_path = tmp_path / plan_relative
    external = tmp_path / "external.md"
    external.write_text("outside\n", encoding="utf-8")
    plan_path.parent.mkdir(parents=True)
    plan_path.symlink_to(external)
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "execution_mode": "plan",
            "workspace_dir": str(tmp_path),
            "plan_file_path": str(plan_path),
            "plan_relative_path": plan_relative,
            "plan_permission_path": plan_relative,
        },
    )

    assert not is_current_plan_path(ctx, plan_relative)


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
async def test_registry_scopes_plan_delegation_before_handler(monkeypatch) -> None:
    calls: list[dict] = []

    async def handler(_ctx, **_kwargs):
        calls.append(_kwargs)
        return ToolResult(success=True, output="ok")

    tool = Tool(
        info=ToolInfo(
            name="task",
            description="Delegation test tool",
            category=ToolCategory.FILE,
        ),
        handler=handler,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(lambda _cls, _name: tool),
    )

    explore = await ToolRegistry.execute(
        "task",
        ctx=ToolContext(
            session_id="session-1",
            message_id="message-1",
            extra={"execution_mode": "plan"},
        ),
        subagent_type="explore",
    )
    denied = await ToolRegistry.execute(
        "delegate_task",
        ctx=ToolContext(
            session_id="session-1",
            message_id="message-1",
            extra={"execution_mode": "plan"},
        ),
        subagent_type="general",
    )

    assert explore.success
    assert not denied.success
    assert "explore, librarian" in (denied.error or "")
    assert calls == [{"subagent_type": "explore"}]


@pytest.mark.asyncio
async def test_registry_scopes_plan_edits_to_current_plan_file(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    async def handler(_ctx, **kwargs):
        calls.append(kwargs["filePath"])
        return ToolResult(success=True, output="ok")

    tool = Tool(
        info=ToolInfo(
            name="write",
            description="Write test",
            category=ToolCategory.FILE,
            parameters=[
                ToolParameter(
                    name="filePath",
                    type=ParameterType.STRING,
                    required=True,
                )
            ],
        ),
        handler=handler,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(lambda _cls, _name: tool),
    )
    plan_relative = ".flocks/plans/1234-plan.md"
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "execution_mode": "plan",
            "workspace_dir": str(tmp_path),
            "plan_file_path": str(tmp_path / plan_relative),
            "plan_relative_path": plan_relative,
            "plan_permission_path": plan_relative,
        },
    )

    allowed = await ToolRegistry.execute(
        "write",
        ctx=ctx,
        filePath=plan_relative,
    )
    denied = await ToolRegistry.execute(
        "write",
        ctx=ctx,
        filePath=".flocks/plans/other.md",
    )
    traversal = await ToolRegistry.execute(
        "write",
        ctx=ctx,
        filePath=".flocks/plans/../outside.md",
    )

    assert allowed.success
    assert not denied.success
    assert not traversal.success
    assert calls == [plan_relative]


@pytest.mark.asyncio
async def test_tool_context_rechecks_plan_edit_permission(tmp_path) -> None:
    plan_relative = ".flocks/plans/1234-plan.md"
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "execution_mode": "plan",
            "workspace_dir": str(tmp_path),
            "plan_file_path": str(tmp_path / plan_relative),
            "plan_relative_path": plan_relative,
            "plan_permission_path": plan_relative,
        },
    )

    await ctx.ask(permission="edit", patterns=[plan_relative])
    with pytest.raises(PermissionError, match="current session plan file"):
        await ctx.ask(permission="edit", patterns=["src/main.py"])


@pytest.mark.asyncio
async def test_read_only_sandbox_allows_only_plan_artifact_write(tmp_path) -> None:
    plan_relative = ".flocks/plans/1234-plan.md"
    plan_path = tmp_path / plan_relative
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "execution_mode": "plan",
            "workspace_dir": str(tmp_path),
            "plan_file_path": str(plan_path),
            "plan_relative_path": plan_relative,
            "plan_permission_path": plan_relative,
            "sandbox": {
                "workspace_dir": str(tmp_path),
                "workspace_access": "ro",
            },
        },
    )

    allowed = await write_tool(ctx, "# Plan\n", plan_relative)
    denied = await write_tool(ctx, "bad\n", "src/main.py")

    assert allowed.success
    assert plan_path.read_text(encoding="utf-8") == "# Plan\n"
    assert not denied.success
    assert not (tmp_path / "src" / "main.py").exists()


@pytest.mark.asyncio
async def test_runner_filters_tools_with_message_mode(monkeypatch) -> None:
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-1")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    agent = SimpleNamespace(
        name="rex",
        tools=[
            "read",
            "bash",
            "write",
            "edit",
            "task",
            "delegate_task",
            "run_slash_command",
        ]
    )

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="read"),
            SimpleNamespace(name="bash"),
            SimpleNamespace(name="write"),
            SimpleNamespace(name="edit"),
            SimpleNamespace(name="task"),
            SimpleNamespace(name="delegate_task"),
            SimpleNamespace(name="run_slash_command"),
        ],
        metadata={},
    )

    async def list_tools(**_kwargs):
        return result

    monkeypatch.setattr(
        "flocks.session.runner.list_session_callable_tool_infos",
        list_tools,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(
            lambda _cls, name: (
                SimpleNamespace(info=SimpleNamespace(name="plan_exit", enabled=True))
                if name == "plan_exit"
                else None
            )
        ),
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

    assert [tool.name for tool in tools] == [
        "read",
        "bash",
        "write",
        "edit",
        "task",
        "delegate_task",
        "plan_exit",
    ]
    assert metadata["executionMode"] == "plan"
    assert metadata["modeAllowedToolNames"] == [
        "bash",
        "delegate_task",
        "edit",
        "plan_exit",
        "read",
        "task",
        "write",
    ]


@pytest.mark.asyncio
async def test_runner_hides_always_load_question_when_permission_denied(monkeypatch) -> None:
    from flocks.permission.helpers import from_config
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-question-deny")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    runner._turn_permission_ruleset = from_config({"question": "deny"})
    agent = SimpleNamespace(name="custom-agent", tools=["request_ledger"])

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="question"),
            SimpleNamespace(name="tool_search"),
            SimpleNamespace(name="request_ledger"),
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
            executionMode=SessionExecutionMode.BUILD,
        )
    ]

    tools, metadata = await runner._list_callable_tool_infos_for_turn(
        agent,
        messages,
    )

    assert [tool.name for tool in tools] == ["tool_search", "request_ledger"]
    assert metadata["permissionDeniedToolNames"] == ["question"]
    assert metadata["modeAllowedToolNames"] == ["request_ledger", "tool_search"]


@pytest.mark.asyncio
async def test_runner_keeps_always_load_question_without_permission_denial(monkeypatch) -> None:
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-question-allowed")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    runner._turn_permission_ruleset = []
    agent = SimpleNamespace(name="custom-agent", tools=["question", "request_ledger"])

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="question"),
            SimpleNamespace(name="tool_search"),
            SimpleNamespace(name="request_ledger"),
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
            executionMode=SessionExecutionMode.BUILD,
        )
    ]

    tools, metadata = await runner._list_callable_tool_infos_for_turn(
        agent,
        messages,
    )

    assert [tool.name for tool in tools] == [
        "question",
        "tool_search",
        "request_ledger",
    ]
    assert metadata["permissionDeniedToolNames"] == []


@pytest.mark.asyncio
async def test_runner_keeps_question_visible_when_only_tool_list_omits_it(monkeypatch) -> None:
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-question-unchecked")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    runner._turn_permission_ruleset = []
    agent = SimpleNamespace(name="custom-agent", tools=["request_ledger"])

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="question"),
            SimpleNamespace(name="tool_search"),
            SimpleNamespace(name="request_ledger"),
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
            executionMode=SessionExecutionMode.BUILD,
        )
    ]

    tools, metadata = await runner._list_callable_tool_infos_for_turn(
        agent,
        messages,
    )

    assert [tool.name for tool in tools] == [
        "question",
        "tool_search",
        "request_ledger",
    ]
    assert metadata["permissionDeniedToolNames"] == []


@pytest.mark.asyncio
async def test_runner_keeps_non_question_tools_visible_when_permission_denied(monkeypatch) -> None:
    from flocks.permission.helpers import from_config
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-bash-deny")
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None)
    runner._turn_permission_ruleset = from_config({"bash": "deny"})
    agent = SimpleNamespace(name="custom-agent", tools=["question", "bash"])

    result = SimpleNamespace(
        tool_infos=[
            SimpleNamespace(name="question"),
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
            executionMode=SessionExecutionMode.BUILD,
        )
    ]

    tools, metadata = await runner._list_callable_tool_infos_for_turn(
        agent,
        messages,
    )

    assert [tool.name for tool in tools] == ["question", "bash"]
    assert metadata["permissionDeniedToolNames"] == []


@pytest.mark.asyncio
async def test_question_preflight_ask_permission_does_not_prompt() -> None:
    from flocks.permission.helpers import from_config
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-question-ask")
    runner._step = 1
    runner.callbacks = SimpleNamespace(
        event_publish_callback=None,
        on_permission_request=AsyncMock(return_value=False),
    )
    runner._turn_permission_ruleset = from_config({"question": "ask"})

    await runner._handle_permission(SimpleNamespace(
        permission="question",
        patterns=["*"],
        always=[],
        metadata={"reason": "question_tool"},
        message_id="msg_1",
    ))

    runner.callbacks.on_permission_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_question_preflight_deny_permission_still_blocks() -> None:
    from flocks.permission.helpers import from_config
    from flocks.session.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="session-question-deny")
    runner._step = 1
    runner.callbacks = SimpleNamespace(
        event_publish_callback=None,
        on_permission_request=AsyncMock(return_value=True),
    )
    runner._turn_permission_ruleset = from_config({"question": "deny"})

    with pytest.raises(PermissionError, match="Permission denied: question"):
        await runner._handle_permission(SimpleNamespace(
            permission="question",
            patterns=["*"],
            always=[],
            metadata={"reason": "question_tool"},
            message_id="msg_1",
        ))

    runner.callbacks.on_permission_request.assert_not_awaited()
