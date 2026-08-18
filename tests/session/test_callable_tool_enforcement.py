"""Tests for turn-scoped callable tool enforcement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.hooks.pipeline import HookPipeline
from flocks.provider.provider import ChatMessage
from flocks.session.runner import SessionRunner
from flocks.session.session import SessionInfo
from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.tool.registry import Tool, ToolCategory, ToolContext, ToolInfo, ToolRegistry, ToolResult


def _registered_tool(name: str, execute: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            name=name,
            source="builtin",
            provider=None,
            enabled=True,
        ),
        execute=execute,
    )


def _patch_registered_tool(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    execute: AsyncMock,
) -> None:
    tool = _registered_tool(name, execute)
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(lambda _cls, requested_name: tool if requested_name == name else None),
    )
    monkeypatch.setattr(
        ToolRegistry,
        "_reset_failure_state",
        classmethod(lambda _cls, _tool_name: None),
    )


@pytest.mark.asyncio
async def test_allowed_tool_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    _patch_registered_tool(monkeypatch, "audit_read", execute)
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "enforce_callable_tools": True,
            "turn_callable_tool_names": ["audit_read"],
        },
    )

    result = await ToolRegistry.execute("audit_read", ctx=ctx, path="src/app.py")

    assert result.success is True
    execute.assert_awaited_once_with(ctx, path="src/app.py")


@pytest.mark.asyncio
async def test_tool_not_in_turn_set_is_rejected_before_hooks_and_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = AsyncMock(return_value=ToolResult(success=True, output="unexpected"))
    tool = Tool(
        info=ToolInfo(
            name="audit_secret",
            description="Hidden audit tool",
            category=ToolCategory.CUSTOM,
        ),
        handler=handler,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        classmethod(lambda _cls, _name: tool),
    )
    before_hook = AsyncMock()
    monkeypatch.setattr(HookPipeline, "run_tool_before", before_hook)
    ctx = ToolContext(
        session_id="session-1",
        message_id="message-1",
        extra={
            "enforce_callable_tools": True,
            "turn_callable_tool_names": ["audit_read"],
        },
    )

    result = await ToolRegistry.execute("audit_secret", ctx=ctx)

    assert result.success is False
    assert result.error == "Tool is not callable in this turn: audit_secret"
    before_hook.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"enforce_callable_tools": True},
        {"enforce_callable_tools": True, "turn_callable_tool_names": "audit_read"},
        {"enforce_callable_tools": True, "turn_callable_tool_names": ("audit_read",)},
        {"enforce_callable_tools": True, "turn_callable_tool_names": ["audit_read", None]},
    ],
)
async def test_missing_or_malformed_turn_set_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    extra: dict,
) -> None:
    execute = AsyncMock(return_value=ToolResult(success=True, output="unexpected"))
    _patch_registered_tool(monkeypatch, "audit_read", execute)

    result = await ToolRegistry.execute(
        "audit_read",
        ctx=ToolContext(session_id="session-1", message_id="message-1", extra=extra),
    )

    assert result.success is False
    assert result.error == "Tool is not callable in this turn: audit_read"
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"enforce_callable_tools": False},
        {"enforce_callable_tools": "true"},
    ],
)
async def test_direct_registry_calls_remain_compatible_without_enforcement(
    monkeypatch: pytest.MonkeyPatch,
    extra: dict,
) -> None:
    execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    _patch_registered_tool(monkeypatch, "audit_read", execute)
    ctx = ToolContext(session_id="direct", message_id="direct", extra=extra)

    result = await ToolRegistry.execute("audit_read", ctx=ctx)

    assert result.success is True
    execute.assert_awaited_once_with(ctx)


@pytest.mark.asyncio
async def test_stream_processor_adds_turn_tool_set_to_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_extra = {}

    async def execute(*, tool_name, ctx, **_kwargs):  # noqa: ANN001, ANN202
        assert tool_name == "audit_read"
        seen_extra.update(ctx.extra)
        return ToolResult(success=True, output="ok")

    processor = StreamProcessor(
        session_id="session-1",
        assistant_message=SimpleNamespace(id="message-1"),
        agent=SimpleNamespace(name="code-security"),
        allowed_tool_names=["audit_read"],
    )
    monkeypatch.setattr(
        processor,
        "_resolve_sandbox_meta",
        AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.store_part",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.update_part",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.ToolRegistry.execute",
        execute,
    )

    await processor.process_event(ToolInputStartEvent(id="call-1", tool_name="audit_read"))
    await processor.process_event(
        ToolCallEvent(tool_call_id="call-1", tool_name="audit_read", input={})
    )

    assert seen_extra["agent_execution_session"] is True
    assert seen_extra["enforce_callable_tools"] is True
    assert seen_extra["turn_callable_tool_names"] == ["audit_read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_tool_names", [["audit_read"], None])
async def test_stream_processor_rejects_before_hooks_and_execution_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    allowed_tool_names: list[str] | None,
) -> None:
    before_hook = AsyncMock()
    tool_start = AsyncMock()
    resolve_sandbox = AsyncMock()
    registry_execute = AsyncMock()
    store_part = AsyncMock()
    message_parts = AsyncMock()
    processor = StreamProcessor(
        session_id="session-1",
        assistant_message=SimpleNamespace(id="message-1"),
        agent=SimpleNamespace(name="code-security"),
        allowed_tool_names=allowed_tool_names,
        tool_start_callback=tool_start,
    )
    monkeypatch.setattr(HookPipeline, "run_tool_before", before_hook)
    monkeypatch.setattr(processor, "_resolve_sandbox_meta", resolve_sandbox)
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.ToolRegistry.execute",
        registry_execute,
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.store_part",
        store_part,
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.parts",
        message_parts,
    )

    await processor.process_event(
        ToolInputStartEvent(id="call-1", tool_name="audit_secret")
    )
    await processor.process_event(
        ToolCallEvent(tool_call_id="call-1", tool_name="audit_secret", input={})
    )

    state = processor.tool_calls["call-1"]
    assert state.status == "error"
    assert state.error == "Tool is not callable in this turn: audit_secret"
    assert store_part.await_count == 2
    stored_error_part = store_part.await_args_list[-1].args[2]
    assert stored_error_part.state.status == "error"
    assert stored_error_part.state.error == state.error
    before_hook.assert_not_awaited()
    tool_start.assert_not_awaited()
    resolve_sandbox.assert_not_awaited()
    registry_execute.assert_not_awaited()
    message_parts.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_refreshes_allowed_tools_after_llm_before_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {"initial": None, "updated": None}

    class ProcessorStub:
        def __init__(self, **kwargs):  # noqa: ANN003, ANN204
            captured["initial"] = tuple(kwargs["allowed_tool_names"])
            self.finish_reason = "stop"
            self.tool_calls = {}
            self._langfuse_generation = None

        def set_allowed_tool_names(self, names):  # noqa: ANN001, ANN201
            captured["updated"] = tuple(names)

        async def process_event(self, _event):  # noqa: ANN001, ANN202
            return None

        async def drain_parallel_tool_calls(self) -> None:
            return None

        def get_text_content(self) -> str:
            return ""

        def get_reasoning_content(self) -> str:
            return ""

        def get_finish_reason(self) -> str:
            return self.finish_reason

    class ToolAccumulatorStub:
        def __init__(self, _processor):  # noqa: ANN001, ANN204
            pass

        async def feed_chunk(self, _tool_call):  # noqa: ANN001, ANN202
            return None

        async def flush_remaining(self, _finish_reason):  # noqa: ANN001, ANN202
            return None

    final_tools = [
        {"type": "function", "function": {"name": "audit_search"}},
    ]

    async def run_llm_before(_payload):  # noqa: ANN001, ANN202
        return SimpleNamespace(output={"request": {"tools": final_tools}})

    class ProviderStub:
        async def chat_stream(self, **kwargs):  # noqa: ANN003, ANN202
            assert kwargs["tools"] == final_tools
            if False:
                yield None

    session = SessionInfo.model_construct(
        id="session-1",
        slug="test",
        project_id="project-1",
        directory="/tmp",
        title="Callable tools",
    )
    runner = SessionRunner(
        session=session,
        provider_id="test-provider",
        model_id="test-model",
    )
    monkeypatch.setattr(runner_mod, "StreamProcessor", ProcessorStub)
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        ToolAccumulatorStub,
    )
    monkeypatch.setattr(HookPipeline, "has_stage_handlers", AsyncMock(return_value=True))
    monkeypatch.setattr(HookPipeline, "run_llm_before", AsyncMock(side_effect=run_llm_before))
    monkeypatch.setattr(HookPipeline, "run_llm_after", AsyncMock())
    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock())
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda _provider_id, _model_id: {},
    )

    await runner._call_llm(
        provider=ProviderStub(),
        messages=[ChatMessage(role="user", content="audit")],
        tools=[{"type": "function", "function": {"name": "audit_read"}}],
        agent=SimpleNamespace(name="code-security"),
        assistant_msg=SimpleNamespace(id="message-1"),
    )

    assert captured == {
        "initial": ("audit_read",),
        "updated": ("audit_search",),
    }
