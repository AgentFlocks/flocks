"""Transform / memory / hook-gate cases from the 2026-09-14 execution (G2a)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolResult,
)


@pytest.fixture(autouse=True)
def _reset_hooks_and_transform():
    from flocks.transform.response import reset_response_transform

    HookPipeline.reset()
    HookPipeline._initialized = True
    reset_response_transform()
    yield
    HookPipeline.reset()
    reset_response_transform()


# ---------------------------------------------------------------------------
# response transform registration / open failure / close failure  (SES-0412 / 0413 / 0414 / 0415)
# ---------------------------------------------------------------------------

class _Transform:
    name = "t"

    def __init__(self, stream=None):
        self._stream = stream

    async def open_stream(self, metadata):
        if self._stream is None:
            raise RuntimeError("cannot open")
        return self._stream

    async def transform_text(self, text, metadata):
        return text

    async def transform_value(self, value, metadata):
        return value


class _Stream:
    async def feed(self, text):
        return text

    async def flush(self):
        return ""

    async def close(self):
        raise RuntimeError("close boom")


def test_registration_rejects_bad_pending_limit_and_incomplete_transform():
    """SES-0413 / SES-0414."""
    from flocks.transform.response import register_response_transform

    with pytest.raises(ValueError, match="max_pending_chars 必须为正数"):
        register_response_transform(_Transform(_Stream()), max_pending_chars=0)
    with pytest.raises(ValueError, match="max_pending_chars 必须为正数"):
        register_response_transform(_Transform(_Stream()), max_pending_chars=-5)

    class _Partial:
        name = "p"

        async def open_stream(self, metadata):
            return _Stream()

        async def transform_text(self, text, metadata):
            return text

    with pytest.raises(Exception):
        register_response_transform(_Partial())

    class _NoName(_Transform):
        name = ""

    with pytest.raises(ValueError, match="必须提供 name"):
        register_response_transform(_NoName(_Stream()))


@pytest.mark.asyncio
async def test_open_stream_failure_prevents_model_request(monkeypatch):
    """SES-0412: the runner must not call the provider when the transform cannot start."""
    import flocks.session.runner as runner_mod
    from flocks.provider.provider import ChatMessage
    from flocks.session.runner import SessionRunner
    from flocks.session.session import SessionInfo
    from flocks.transform.response import register_response_transform

    register_response_transform(_Transform(stream=None))
    runner = SessionRunner(
        session=SessionInfo.model_construct(id="ses_t", slug="t", project_id="p", directory="/tmp", title="t"),
        provider_id="anthropic",
        model_id="m",
    )
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.SessionRunner, "_end_observability", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda provider_id, model_id: {})
    monkeypatch.setattr(runner_mod, "trace_scope", lambda **kwargs: MagicMock(observation=None))
    monkeypatch.setattr(runner_mod, "generation_scope", lambda **kwargs: MagicMock(observation=None))
    provider = MagicMock()
    provider.chat_stream = MagicMock()
    with pytest.raises(RuntimeError, match="Response transform could not be started; request was not sent"):
        await runner._call_llm(
            provider=provider,
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
            agent=MagicMock(name="rex"),
            assistant_msg=MagicMock(id="msg_a"),
        )
    provider.chat_stream.assert_not_called()


@pytest.mark.asyncio
async def test_close_failure_is_only_logged():
    """SES-0415."""
    from flocks.transform import response as response_mod
    from flocks.transform.response import open_response_stream, register_response_transform

    register_response_transform(_Transform(_Stream()))
    stream = await open_response_stream({})
    assert await stream.feed("abc") == "abc"
    warned = []
    with patch.object(response_mod.log, "warn", lambda event, payload=None: warned.append(event)):
        await stream.close()
    assert "transform.stream.close_failed" in warned


# ---------------------------------------------------------------------------
# memory flush trigger / paths  (SES-0420 / 0421 / 0422 / 0423)
# ---------------------------------------------------------------------------

def test_memory_flush_triggers_at_threshold_once_per_compaction():
    """SES-0420 / SES-0421."""
    from flocks.memory.config import MemoryAutoFlushConfig
    from flocks.memory.flush import MemoryFlush
    from flocks.session.lifecycle.compaction.policy import CompactionPolicy

    policy = CompactionPolicy.from_model(100_000, 8_000)
    cfg = MemoryAutoFlushConfig(enabled=True)
    threshold = 100_000 - policy.flush_reserve - policy.flush_trigger
    assert MemoryFlush.should_trigger(threshold - 1, 100_000, cfg, policy=policy) is False
    assert MemoryFlush.should_trigger(threshold, 100_000, cfg, policy=policy) is True
    assert MemoryFlush.should_trigger(threshold, 100_000, cfg, last_flush_compaction=2, current_compaction=2, policy=policy) is False
    assert MemoryFlush.should_trigger(threshold, 100_000, cfg, last_flush_compaction=1, current_compaction=2, policy=policy) is True
    disabled = MemoryAutoFlushConfig(enabled=False)
    assert MemoryFlush.should_trigger(10**9, 100_000, disabled, policy=policy) is False


def test_memory_paths_reject_traversal_and_bad_scopes(tmp_path: Path):
    """SES-0422 / SES-0423."""
    from flocks.memory.manager import _safe_resolve_memory_path
    from flocks.memory.paths import normalize_curated_path, scope_id_for, validate_scope_path
    from flocks.memory.types import MemoryScope

    with pytest.raises(ValueError, match="Path traversal detected"):
        _safe_resolve_memory_path(tmp_path, "../../etc/passwd")
    with pytest.raises(ValueError, match="path must be USER.md or MEMORY.md"):
        normalize_curated_path("../MEMORY.md")
    assert normalize_curated_path("memory.md") == "MEMORY.md"
    with pytest.raises(ValueError, match="project scope only supports MEMORY.md"):
        validate_scope_path(MemoryScope.PROJECT, "USER.md")
    with pytest.raises(ValueError, match="only available for registered prj_"):
        scope_id_for(MemoryScope.PROJECT, "default")
    assert scope_id_for(MemoryScope.PROJECT, "prj_abc") == "prj_abc"


# ---------------------------------------------------------------------------
# registry gate outcomes  (SES-0497 / 0499 / 0500)
# ---------------------------------------------------------------------------

def _tool(name: str, params: dict[str, Any], calls: list) -> Tool:
    async def handler(_ctx: ToolContext, **kwargs: Any) -> ToolResult:
        calls.append(kwargs)
        return ToolResult(success=True, output="ok")

    return Tool(
        info=ToolInfo(
            name=name,
            description="g2a",
            category=ToolCategory.TERMINAL,
            parameters=[ToolParameter(name=k, type=ParameterType.STRING, required=True) for k in params],
        ),
        handler=handler,
    )


@pytest.mark.asyncio
async def test_confirm_decision_marks_confirmation_required():
    """SES-0499."""
    calls: list = []

    class Confirm(HookBase):
        async def tool_before(self, _ctx):
            return {"decision": {"action": "confirm"}}

    HookPipeline.register("confirm", Confirm())
    result = await _tool("bash", {"command": "x"}, calls).execute(ToolContext(session_id="s", message_id="m"), command="x")
    assert result.success is False
    assert result.error == "Tool execution requires confirmation"
    assert result.metadata["confirmation_required"] is True
    assert result.metadata["correlation_id"]
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_validated_input_patch_raises():
    """SES-0500."""
    calls: list = []

    class BadPatch(HookBase):
        async def tool_before(self, _ctx):
            return {"decision": {"action": "allow", "validated_input_patch": {"bogus": 1}}}

    HookPipeline.register("badpatch", BadPatch())
    result = await _tool("bash", {"command": "x"}, calls).execute(ToolContext(session_id="s", message_id="m"), command="x")
    # the ValueError is converted into an error result so the tool part lands in error state
    assert result.success is False
    assert result.error == "Hook requested invalid validated_input_patch keys: bogus"
    assert calls == []


@pytest.mark.asyncio
async def test_hook_stage_failure_is_fail_closed_for_streamed_calls():
    """SES-0497: a crashing tool-before hook denies the streamed call (registry never runs the handler)."""
    from flocks.session.streaming.stream_events import ToolCallEvent
    from flocks.tool.registry import ToolRegistry
    from tests.session.test_stream_processor import _make_processor

    calls: list = []
    tool = _tool("g2a_echo", {"v": "x"}, calls)

    class Crash(HookBase):
        async def tool_before(self, _ctx):
            raise RuntimeError("hook crashed")

    HookPipeline.register("crash", Crash(), critical=True)
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    proc.set_allowed_tool_names(["g2a_echo"])
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        patch.object(ToolRegistry, "get", return_value=tool),
        patch.object(ToolRegistry, "get_schema", return_value=None),
    ):
        await proc.process_event(ToolCallEvent(tool_call_id="c1", tool_name="g2a_echo", input={"v": "1"}))
    assert calls == []
    errors = [c.args[1]["part"] for c in cb.await_args_list if c.args[0] == "message.part.updated" and c.args[1]["part"].get("type") == "tool" and c.args[1]["part"]["state"]["status"] == "error"]
    assert errors and errors[-1]["state"]["metadata"].get("blocked_by_hook") is True
    assert "hook failed" in errors[-1]["state"]["error"]


# ---------------------------------------------------------------------------
# restart recovery marks unfinished assistant replies  (E2E-0011 via G8; SES-0529 family)
# ---------------------------------------------------------------------------

def _assistant(message_id: str, **fields):
    from flocks.session.message import AssistantMessageInfo, MessagePath, MessageWithParts, TokenUsage

    info = AssistantMessageInfo(
        id=message_id,
        sessionID="ses_r",
        time={"created": 1},
        parentID="msg_u",
        modelID="m",
        providerID="p",
        mode="rex",
        agent="rex",
        path=MessagePath(cwd="/", root="/"),
        tokens=TokenUsage(),
        **fields,
    )
    return MessageWithParts(info=info, parts=[])


@pytest.mark.asyncio
async def test_orphan_recovery_marks_unfinished_assistant_message_as_interrupted():
    from flocks.session.message import MessageWithParts, UserMessageInfo
    from flocks.session.orphan_tools import INTERRUPTED_MESSAGE_ERROR, abort_orphan_running_parts_in_messages

    user = MessageWithParts(
        info=UserMessageInfo(id="msg_u", sessionID="ses_r", time={"created": 1}, agent="rex", model={"providerID": "p", "modelID": "m"}),
        parts=[],
    )
    cut_off = _assistant("msg_cut")                      # streaming when the process died
    finished = _assistant("msg_done", finish="stop")     # normal reply
    failed = _assistant("msg_err", error={"name": "APIError", "message": "x"})
    updated_info = _assistant("msg_cut", finish="error", error={"name": "MessageAbortedError", "message": INTERRUPTED_MESSAGE_ERROR}).info
    update = AsyncMock(return_value=updated_info)
    with patch("flocks.session.orphan_tools.Message.update", update):
        repaired = await abort_orphan_running_parts_in_messages("ses_r", [user, cut_off, finished, failed])

    assert repaired == 1
    update.assert_awaited_once()
    kwargs = update.await_args.kwargs
    assert update.await_args.args == ("ses_r", "msg_cut")
    assert kwargs["finish"] == "error"
    assert kwargs["error"]["name"] == "MessageAbortedError"
    assert kwargs["error"]["message"] == INTERRUPTED_MESSAGE_ERROR
    # the preloaded object handed back to the API serializer carries the marker too
    assert cut_off.info.finish == "error" and cut_off.info.error["name"] == "MessageAbortedError"
    assert finished.info.finish == "stop" and finished.info.error is None
    assert failed.info.error["name"] == "APIError"


# ---------------------------------------------------------------------------
# per-turn system prompt override  (SES-0275)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_message_system_field_overrides_agent_prompt_for_the_turn(monkeypatch):
    """SES-0275: ``PromptRequest.system`` is stored on the user message and replaces the agent prompt."""
    from types import SimpleNamespace

    import flocks.session.runner as runner_mod
    from flocks.session.message import UserMessageInfo
    from flocks.session.runner import RunnerCallbacks, SessionRunner
    from flocks.session.session import SessionInfo

    runner = SessionRunner(
        session=SessionInfo.model_construct(id="ses_sys", slug="s", project_id="p", directory="/tmp", title="t"),
        provider_id="anthropic",
        model_id="m",
    )
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())
    last_user = UserMessageInfo(
        id="msg_user_sys",
        sessionID="ses_sys",
        time={"created": 1},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "m"},
        system="You are a pirate.",
    )
    agent = SimpleNamespace(name="rex", steps=None, permission=None, prompt="agent default prompt", mode="primary", tools=[])
    provider = MagicMock()
    provider.is_configured.return_value = True
    build = AsyncMock(return_value=[])

    async def stop_here(*args, **kwargs):
        raise RuntimeError("stop after prompts")

    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    # The runner builds the system prompt through ``build_system_prompt_blocks``
    # after collecting the per-turn context; both are stubbed so the assertion
    # only sees the agent prompt that was handed over.
    monkeypatch.setattr(runner_mod.SessionPrompt, "_is_builtin_system_subagent_session", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompt_blocks", build)
    monkeypatch.setattr(runner, "_build_turn_prompt_context", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_run_session_start_hook", stop_here)

    with pytest.raises(RuntimeError, match="stop after prompts"):
        await runner._process_step([last_user], last_user)
    assert build.await_args.kwargs["agent_prompt"] == "You are a pirate."

    # without an override the agent prompt is used
    plain = UserMessageInfo(id="msg_user_plain", sessionID="ses_sys", time={"created": 2}, agent="rex", model={"providerID": "anthropic", "modelID": "m"})
    with pytest.raises(RuntimeError, match="stop after prompts"):
        await runner._process_step([plain], plain)
    assert build.await_args.kwargs["agent_prompt"] == "agent default prompt"


# ---------------------------------------------------------------------------
# summary-model failure is stashed for the manual summarize path  (SES-0179)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_provider_failure_is_recorded_for_manual_summarize(monkeypatch):
    from types import SimpleNamespace

    from flocks.provider.provider import Provider
    from flocks.session.lifecycle.compaction import compaction as compaction_mod
    from flocks.session.lifecycle.compaction.compaction import SessionCompaction, _compaction_history
    from flocks.session.message import Message

    _compaction_history.clear()
    compaction_mod.reset_iterative_summary_cache("ses_sumfail")
    compaction_mod.pop_last_compaction_error("ses_sumfail")

    def _mwp(message_id, role, text, finish=None):
        return SimpleNamespace(info=SimpleNamespace(id=message_id, role=role, finish=finish, summary=None), parts=[SimpleNamespace(type="text", text=text)])

    msgs = [_mwp("u1", "user", "first question"), _mwp("a1", "assistant", "first answer", finish="stop"), _mwp("u2", "user", "/compact")]

    async def failing_summarize(*args, **kwargs):
        raise Exception("OpenAI API key not configured")

    archive_spy = AsyncMock(return_value="continue")
    monkeypatch.setattr(Provider, "get", classmethod(lambda cls, provider_id: MagicMock()))
    monkeypatch.setattr(Provider, "apply_config", classmethod(AsyncMock(return_value=None)))
    monkeypatch.setattr(Message, "list_with_parts", classmethod(AsyncMock(return_value=msgs)))
    monkeypatch.setattr(compaction_mod.summary, "summarize_single_pass", failing_summarize)
    monkeypatch.setattr(SessionCompaction, "_archive_and_write_summary", archive_spy)
    monkeypatch.setattr(SessionCompaction, "_dispatch_memory_flush", classmethod(AsyncMock(return_value=None)))
    try:
        result = await SessionCompaction.process(
            session_id="ses_sumfail", parent_id="u2", messages=[{"id": "u2"}], model_id="gpt-4o", provider_id="openai", auto=False,
        )
        assert result == "skipped"
        archive_spy.assert_not_called()
        assert compaction_mod.pop_last_compaction_error("ses_sumfail") == "OpenAI API key not configured"
        assert compaction_mod.pop_last_compaction_error("ses_sumfail") is None  # single-shot
    finally:
        _compaction_history.clear()
