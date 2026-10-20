"""The REA analysis prompt without a file asks for the file and runs no tool (PRD 6.2)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from flocks.hooks.builtin import register_builtin_hooks
from flocks.hooks.pipeline import HookContext, HookPipeline, HookStage
from flocks.rea import attachment_guard
from flocks.rea.attachment_guard import (
    ASK_FOR_FILES_CONTEXT,
    HOOK_NAME,
    TOOL_STOP_DETAIL,
    ReaAttachmentGuard,
    asks_to_analyze_attachments,
    is_blocked,
)
from flocks.tool.registry import ParameterType, Tool, ToolCategory, ToolContext, ToolInfo, ToolParameter, ToolResult


SESSION = "ses_rea_use"
USE_PROMPT = "帮我使用 REA 逆向工具分析上传的附件文件"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    HookPipeline.reset()
    # No plugin loading: only the hooks a test registers.
    HookPipeline._initialized = True
    attachment_guard._reset_for_testing()
    yield
    HookPipeline.reset()
    attachment_guard._reset_for_testing()


@pytest.fixture
def conversation(monkeypatch):
    """Messages of any conversation as Message.list_with_parts returns them; ``archived`` only with include_archived."""

    from flocks.session.message import Message

    state = {"messages": [], "archived": [], "calls": []}

    async def list_with_parts(session_id, include_archived=False):
        state["calls"].append(include_archived)
        return list(state["archived"] if include_archived else []) + list(state["messages"])

    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    return state


def _message(role: str, *parts: Any):
    return SimpleNamespace(info=SimpleNamespace(role=role), parts=list(parts))


def _text(text: str):
    return SimpleNamespace(type="text", text=text)


def _file(name: str = "sample.bin"):
    return SimpleNamespace(type="file", filename=name, url=f"file:///uploads/{name}", mime="application/octet-stream")


def _prompt_ctx(prompt: str, *, session_id: str = SESSION, message_id: str = "msg_u1", output=None) -> HookContext:
    return HookContext(
        stage=HookStage.USER_PROMPT_BEFORE,
        input={"sessionID": session_id, "messageID": message_id, "prompt": prompt},
        output=dict(output or {}),
    )


def _tool_ctx(session_id: str, assistant_message: str = "msg_a1", tool: str = "bash", output=None) -> HookContext:
    return HookContext(
        stage=HookStage.TOOL_BEFORE,
        input={
            "operation": "tool.execute",
            "tool_execution": {"session": {"id": session_id, "message_id": assistant_message}, "tool": {"name": tool}},
        },
        output=dict(output or {}),
    )


def _bash_tool(calls: list) -> Tool:
    async def handler(_ctx: ToolContext, **kwargs: Any) -> ToolResult:
        calls.append(kwargs)
        return ToolResult(success=True, output="ls ~/Downloads")

    return Tool(
        info=ToolInfo(
            name="bash",
            description="bash",
            category=ToolCategory.TERMINAL,
            parameters=[ToolParameter(name="command", type=ParameterType.STRING)],
        ),
        handler=handler,
    )


@pytest.mark.parametrize("text", [
    USE_PROMPT,
    f"  {USE_PROMPT}\n",
    "帮我使用 REA  逆向工具分析上传的附件文件",
    f"请{USE_PROMPT}，重点看签名是怎么算的",
    "help me analyze the uploaded attachments with the REA reverse-engineering tools.",
    f"{USE_PROMPT}（JavaScript / .NET 都行）",
])
def test_the_shortcuts_prompt_is_recognised(text):
    assert asks_to_analyze_attachments(text) is True


@pytest.mark.parametrize("text", [
    "帮我逆向分析一下这个样本",
    "REA 安装指引\n安装原则：仅安装缺失组件",
    f"{USE_PROMPT} https://example.com/app.js",
    f"{USE_PROMPT}：www.example.com 的登录接口",
    f"{USE_PROMPT} /srv/samples/a.out",
    f"{USE_PROMPT} ~/samples/a.out",
    f"{USE_PROMPT} C:\\samples\\a.exe",
    "",
])
def test_other_requests_and_named_targets_are_left_alone(text):
    assert asks_to_analyze_attachments(text) is False


@pytest.mark.asyncio
async def test_no_file_in_the_conversation_asks_for_one_and_stops_its_tools(conversation):
    conversation["messages"].append(_message("user", _text(USE_PROMPT)))
    guard = ReaAttachmentGuard()
    ctx = _prompt_ctx(USE_PROMPT, output={"additionalContext": "context of another hook"})
    await guard.user_prompt_before(ctx)
    assert ctx.output["additionalContext"] == f"context of another hook\n\n{ASK_FOR_FILES_CONTEXT}"
    assert is_blocked(SESSION) is True

    for message, tool in (("msg_a1", "bash"), ("msg_a1", "glob"), ("msg_a2", "read"), ("msg_a2", "rea_analysis")):
        stopped = _tool_ctx(SESSION, message, tool)
        await guard.tool_before(stopped)
        assert stopped.output["execution"] == {"stop": True, "detail": TOOL_STOP_DETAIL}
    other = _tool_ctx("ses_other")
    await guard.tool_before(other)
    assert "execution" not in other.output


@pytest.mark.asyncio
async def test_everything_the_run_does_until_the_users_next_message_stays_blocked(conversation, monkeypatch):
    """A compaction, an inbox message or a goal continuation answers a synthetic user message, after any time."""

    guard = ReaAttachmentGuard()
    await guard.user_prompt_before(_prompt_ctx(USE_PROMPT))
    register_builtin_hooks()
    await HookPipeline.run_turn_after({"sessionID": SESSION, "workspace": "/tmp", "userMessage": {"id": "msg_u1"}})
    monkeypatch.setattr("time.monotonic", lambda: 10**9)
    for message in ("msg_after_compaction", "msg_inbox_reply", "msg_goal_continuation"):
        call = _tool_ctx(SESSION, message)
        await guard.tool_before(call)
        assert call.output["execution"]["stop"] is True
    # The user's next message decides again.
    await guard.user_prompt_before(_prompt_ctx("先看看这个链接 https://example.com/app.js", message_id="msg_u2"))
    assert is_blocked(SESSION) is False


@pytest.mark.asyncio
async def test_no_number_of_other_conversations_lifts_a_block(conversation):
    guard = ReaAttachmentGuard()
    await guard.user_prompt_before(_prompt_ctx(USE_PROMPT))
    for index in range(5000):
        await guard.user_prompt_before(_prompt_ctx(USE_PROMPT, session_id=f"ses_{index}"))
    assert is_blocked(SESSION) is True


@pytest.mark.asyncio
async def test_counting_the_files_fails_and_the_turn_stays_blocked(conversation, monkeypatch):
    async def broken(session_id):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(attachment_guard, "attached_file_count", broken)
    ctx = _prompt_ctx(USE_PROMPT)
    await ReaAttachmentGuard().user_prompt_before(ctx)
    assert ctx.output["additionalContext"] == ASK_FOR_FILES_CONTEXT
    assert is_blocked(SESSION) is True


@pytest.mark.asyncio
async def test_counting_cut_off_by_the_hook_time_limit_keeps_the_turn_blocked(conversation, monkeypatch):
    from flocks.session.message import Message

    async def slow(session_id, include_archived=False):
        await asyncio.sleep(1)
        return [_message("user", _text("先传个样本"), _file())]

    monkeypatch.setattr(Message, "list_with_parts", slow)
    HookPipeline.register(HOOK_NAME, ReaAttachmentGuard(), order=1000, timeout_seconds=0.05)
    hook_ctx = await HookPipeline.run_user_prompt_before({
        "sessionID": SESSION, "messageID": "msg_u1", "prompt": USE_PROMPT, "workspace": "/tmp",
    })
    assert hook_ctx.output["additionalContext"] == ASK_FOR_FILES_CONTEXT
    assert is_blocked(SESSION) is True


@pytest.mark.asyncio
async def test_a_file_anywhere_in_the_conversation_lets_the_analysis_run(conversation):
    # The file came with an earlier message; the prompt itself has none.
    conversation["messages"] += [_message("user", _text("先传个样本"), _file()), _message("assistant", _text("收到"))]
    conversation["messages"].append(_message("user", _text(USE_PROMPT)))
    ctx = _prompt_ctx(USE_PROMPT, output={"additionalContext": "context of another hook"})
    await ReaAttachmentGuard().user_prompt_before(ctx)
    assert ctx.output["additionalContext"] == "context of another hook"
    assert is_blocked(SESSION) is False
    plain = _prompt_ctx(USE_PROMPT)
    await ReaAttachmentGuard().user_prompt_before(plain)
    assert "additionalContext" not in plain.output


@pytest.mark.asyncio
async def test_a_file_in_a_message_a_compaction_archived_counts(conversation):
    conversation["archived"].append(_message("user", _text("样本在这"), _file()))
    conversation["messages"].append(_message("user", _text(USE_PROMPT)))
    ctx = _prompt_ctx(USE_PROMPT)
    await ReaAttachmentGuard().user_prompt_before(ctx)
    assert is_blocked(SESSION) is False and "additionalContext" not in ctx.output
    assert conversation["calls"] == [True]
    assert await attachment_guard.attached_file_count(SESSION) == 1


@pytest.mark.asyncio
async def test_a_file_in_an_assistant_message_is_not_the_users(conversation):
    conversation["messages"] += [_message("assistant", _file("report.md")), _message("user", _text(USE_PROMPT))]
    ctx = _prompt_ctx(USE_PROMPT)
    await ReaAttachmentGuard().user_prompt_before(ctx)
    assert ctx.output["additionalContext"] == ASK_FOR_FILES_CONTEXT


@pytest.mark.asyncio
async def test_every_human_turn_decides_again(conversation):
    guard = ReaAttachmentGuard()
    await guard.user_prompt_before(_prompt_ctx(USE_PROMPT))
    assert is_blocked(SESSION) is True
    # The user attaches the file and sends the prompt again.
    conversation["messages"].append(_message("user", _text(USE_PROMPT), _file()))
    ctx = _prompt_ctx(USE_PROMPT, message_id="msg_u2")
    await guard.user_prompt_before(ctx)
    assert is_blocked(SESSION) is False and "additionalContext" not in ctx.output
    # Or writes something else.
    conversation["messages"].clear()
    await guard.user_prompt_before(_prompt_ctx(USE_PROMPT, session_id="ses_b"))
    assert is_blocked("ses_b") is True
    await guard.user_prompt_before(_prompt_ctx("算了，先讲讲 REA 能做什么", session_id="ses_b"))
    assert is_blocked("ses_b") is False


@pytest.mark.asyncio
async def test_it_is_a_builtin_hook_that_stops_the_tool_call(conversation):
    register_builtin_hooks()
    assert HOOK_NAME in HookPipeline.list_hooks()

    hook_ctx = await HookPipeline.run_user_prompt_before({
        "sessionID": SESSION, "messageID": "msg_u1", "prompt": USE_PROMPT, "workspace": "/tmp",
    })
    assert hook_ctx.output["additionalContext"] == ASK_FOR_FILES_CONTEXT

    calls: list = []
    result = await _bash_tool(calls).execute(
        ToolContext(session_id=SESSION, message_id="msg_a1"), command="ls ~/Downloads",
    )
    assert result.success is False and result.error == TOOL_STOP_DETAIL
    assert calls == []
    # Another conversation is not affected.
    result = await _bash_tool(calls).execute(ToolContext(session_id="ses_other", message_id="msg_a2"), command="ls")
    assert result.success is True and len(calls) == 1
