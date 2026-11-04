"""StreamProcessor / accumulator / replacement-buffer cases from the 2026-09-14 execution (G2a).

Each test names the case ids it covers (SES-xxxx).  Existing tests in
``test_stream_processor.py`` / ``test_tool_accumulator.py`` / ``test_response_transform.py``
cover the rest; these fill the gaps only.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.message import Message
from flocks.session.streaming.stream_events import (
    FinishEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEvent,
    ToolInputStartEvent,
)
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.tool.registry import ToolResult

from tests.session.test_stream_processor import _make_agent, _make_assistant_msg, _make_processor


def _part_events(callback: AsyncMock, part_type: str):
    return [
        call.args[1]["part"]
        for call in callback.await_args_list
        if call.args[0] == "message.part.updated" and call.args[1].get("part", {}).get("type") == part_type
    ]


def _mock_result(output="ok", title="t", success=True, error=None, metadata=None):
    return ToolResult(success=success, output=output, title=title, error=error, metadata=metadata or {})


# ---------------------------------------------------------------------------
# text blocks  (SES-0464 / 0465 / 0466 / 0467 / 0468 / 0469 / 0471 / 0472 / 0475 / 0476 / 0477 / 0478 / 0544 / 0546 / 0549)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_fifty_chars_publish_every_delta_with_cumulative_text():
    """SES-0464 / SES-0544."""
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
        await proc.process_event(TextStartEvent())
        for ch in "abcde":
            await proc.process_event(TextDeltaEvent(text=ch))
    parts = _part_events(cb, "text")
    assert [p["text"] for p in parts] == ["", "a", "ab", "abc", "abcd", "abcde"]
    deltas = [call.args[1].get("delta") for call in cb.await_args_list if call.args[0] == "message.part.updated"][1:]
    assert deltas == ["a", "b", "c", "d", "e"]


@pytest.mark.asyncio
async def test_deltas_after_fifty_chars_are_throttled_but_text_accumulates():
    """SES-0465 / SES-0549."""
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="x" * 60))
        published_before = len(_part_events(cb, "text"))
        for _ in range(1000):
            await proc.process_event(TextDeltaEvent(text="yyyyy"))
    published = len(_part_events(cb, "text")) - published_before
    assert proc.get_text_content() == "x" * 60 + "yyyyy" * 1000
    assert published < 200  # 1000 deltas collapse to a handful of 50ms windows


@pytest.mark.asyncio
async def test_text_delta_without_start_is_ignored():
    """SES-0466."""
    proc = _make_processor()
    await proc.process_event(TextDeltaEvent(text="x"))
    assert proc.get_text_content() == ""


@pytest.mark.asyncio
async def test_text_end_strips_trailing_whitespace_and_sets_end_time():
    """SES-0467."""
    cb = AsyncMock()
    store = AsyncMock()
    proc = _make_processor(event_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=store):
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="hi \n"))
        await proc.process_event(TextEndEvent())
    stored = [call.args for call in store.await_args_list]
    assert stored and stored[-1][-1].text == "hi"
    final = _part_events(cb, "text")[-1]
    assert final["text"] == "hi" and final["time"].get("end")
    assert proc.current_text_part is None


@pytest.mark.asyncio
async def test_text_delta_without_publish_callback_still_accumulates():
    """SES-0468."""
    proc = _make_processor(event_callback=None)
    await proc.process_event(TextStartEvent())
    await proc.process_event(TextDeltaEvent(text="abc"))
    assert proc.get_text_content() == "abc"


@pytest.mark.asyncio
async def test_text_delta_callback_receives_only_visible_text():
    """SES-0469."""
    seen = []

    async def cb(delta):
        seen.append(delta)

    proc = _make_processor(text_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="答案<tool_use>"))
        await proc.process_event(TextDeltaEvent(text='{"name":"echo","input":{}}</tool_use>尾'))
    assert "".join(seen).startswith("答案")
    assert "<tool_use>" not in "".join(seen)


async def _run_text_tool_use(proc: StreamProcessor, text: str, execute: AsyncMock):
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.get_schema", return_value=None),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.get", return_value=MagicMock()),
    ):
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text=text))
        await proc.process_event(TextEndEvent())


@pytest.mark.asyncio
async def test_text_end_parses_tool_use_json_and_overrides_finish_reason():
    """SES-0471 / SES-0477 / SES-0546."""
    cb = AsyncMock()
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor(event_callback=cb)
    await _run_text_tool_use(proc, '<tool_use>{"name":"echo","input":{"v":1}}</tool_use>', execute)
    execute.assert_awaited()
    assert execute.await_args.kwargs["tool_name"] == "echo"
    assert execute.await_args.kwargs["v"] == 1
    assert proc._text_tool_calls_executed is True
    final_text = _part_events(cb, "text")[-1]["text"]
    assert "<tool_use>" not in final_text
    await proc.process_event(FinishEvent(finish_reason="stop"))
    assert proc.get_finish_reason() == "tool-calls"


@pytest.mark.asyncio
async def test_text_end_parses_tool_name_parameters_subtags():
    """SES-0472."""
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor()
    await _run_text_tool_use(proc, "<tool_use><tool_name>echo</tool_name><parameters>{\"v\":2}</parameters></tool_use>", execute)
    assert execute.await_args.kwargs["tool_name"] == "echo" and execute.await_args.kwargs["v"] == 2


@pytest.mark.asyncio
async def test_text_end_parses_inline_json_array_tool_calls():
    """SES-0475."""
    execute = AsyncMock(return_value=_mock_result())
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    await _run_text_tool_use(proc, '请看 [{"tool_name":"echo","parameters":{"a":[1,2]}}] 完', execute)
    assert execute.await_args.kwargs["tool_name"] == "echo" and execute.await_args.kwargs["a"] == [1, 2]
    assert "tool_name" not in _part_events(cb, "text")[-1]["text"]


@pytest.mark.asyncio
async def test_unparseable_tool_use_is_stripped_without_execution():
    """SES-0476."""
    execute = AsyncMock(return_value=_mock_result())
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    await _run_text_tool_use(proc, "<tool_use>not json</tool_use>", execute)
    execute.assert_not_awaited()
    assert "<tool_use>" not in _part_events(cb, "text")[-1]["text"]


@pytest.mark.asyncio
async def test_doom_loop_skips_inline_tool_calls_at_text_end():
    """SES-0478."""
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor()
    proc._stop_tool_processing = True
    await _run_text_tool_use(proc, '<tool_use>{"name":"echo","input":{}}</tool_use>', execute)
    execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# reasoning blocks  (SES-0480 / 0481 / 0483 / 0484)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reasoning_start_delta_merge_and_end():
    cb = AsyncMock()
    store = AsyncMock()
    proc = _make_processor(event_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=store):
        await proc.process_event(ReasoningStartEvent(id="r1", metadata={"providerID": "anthropic"}))
        assert "r1" in proc.reasoning_parts and proc.reasoning_parts["r1"].metadata.get("providerID") == "anthropic"  # SES-0480
        first = _part_events(cb, "reasoning")
        assert first and first[0]["text"] == ""
        await proc.process_event(ReasoningDeltaEvent(id="r1", text="ab", metadata={"reasoningContent": "ab"}))
        await proc.process_event(ReasoningDeltaEvent(id="r1", text="c", metadata={"reasoningContent": "abc"}))
        await proc.process_event(ReasoningDeltaEvent(id="r1", text="d", metadata={"reasoningContent": "d", "other": 1}))
        meta = proc.reasoning_parts["r1"].metadata
        assert meta["reasoningContent"] == "abcd" and meta["other"] == 1  # SES-0481
        await proc.process_event(ReasoningStartEvent(id="r2"))
        for i in range(3):
            await proc.process_event(ReasoningDeltaEvent(id="r2", text=f"z{i}"))
        r2_events = [p for p in _part_events(cb, "reasoning") if p["text"].startswith("z")]
        assert len(r2_events) == 3  # SES-0483 per-id throttle: short blocks publish every delta
        await proc.process_event(ReasoningDeltaEvent(id="r1", text=" plan \n"))
        await proc.process_event(ReasoningEndEvent(id="r1"))
    assert "r1" not in proc.reasoning_parts  # SES-0484
    final_r1 = [p for p in _part_events(cb, "reasoning") if p["id"] != proc.reasoning_parts.get("r2", MagicMock()).id and p["time"].get("end")]
    assert final_r1 and final_r1[-1]["text"] == "abcd plan"


# ---------------------------------------------------------------------------
# tool lifecycle  (SES-0490 / 0491 / 0492 / 0499 / 0500 / 0501 / 0502 / 0503 / 0505 / 0506 / 0507 / 0508 / 0509 / 0511 / 0513 / 0545)
# ---------------------------------------------------------------------------

def _patches(execute, parts=None):
    return (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=parts or [])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute),
    )


@pytest.mark.asyncio
async def test_tool_input_start_stores_pending_part_and_reuses_part_id():
    """SES-0490 / SES-0491 / SES-0545."""
    cb = AsyncMock()
    store = AsyncMock()
    proc = _make_processor(event_callback=cb)
    with patch("flocks.session.streaming.stream_processor.Message.store_part", new=store):
        await proc.process_event(ToolInputStartEvent(id="c1", tool_name="read"))
        first_part_id = proc.tool_calls["c1"].part_id
        await proc.process_event(ToolInputStartEvent(id="c1", tool_name="read"))
    pending = _part_events(cb, "tool")
    assert pending and pending[0]["state"]["status"] == "pending" and pending[0]["callID"] == "c1"
    assert pending[0]["state"]["input"] == {} and pending[0]["state"].get("raw", "") == ""
    assert proc.tool_calls["c1"].status == "pending"
    assert proc.tool_calls["c1"].part_id == first_part_id
    stored_part = store.await_args_list[0].args[-1]
    assert stored_part.id == first_part_id and stored_part.callID == "c1"


@pytest.mark.asyncio
async def test_tool_call_without_input_start_still_executes():
    """SES-0492."""
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor()
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        await proc.process_event(ToolCallEvent(tool_call_id="c9", tool_name="echo", input={}))
    execute.assert_awaited_once()
    assert proc.tool_calls["c9"].status == "completed"


@pytest.mark.asyncio
async def test_completed_tool_call_id_is_not_executed_twice():
    """SES-0503."""
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor()
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        event = ToolCallEvent(tool_call_id="c1", tool_name="echo", input={"a": 1})
        await proc.process_event(event)
        await proc.process_event(event)
    assert execute.await_count == 1


def _completed_tool_part(tool: str, input_: dict):
    part = MagicMock()
    part.type = "tool"
    part.tool = tool
    part.state = MagicMock()
    part.state.status = "completed"
    part.state.input = input_
    return part


@pytest.mark.asyncio
async def test_doom_loop_triggers_on_third_identical_call_and_blocks_following_calls():
    """SES-0501 / SES-0502."""
    execute = AsyncMock(return_value=_mock_result())
    proc = _make_processor()
    parts = [_completed_tool_part("read", {"path": "a"})] * 2 + [_completed_tool_part("read", {"path": "a"})]
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=parts)), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        await proc.process_event(ToolCallEvent(tool_call_id="c3", tool_name="read", input={"path": "a"}))
        assert proc._stop_tool_processing is True
        execute.assert_not_awaited()
        await proc.process_event(ToolCallEvent(tool_call_id="c4", tool_name="read", input={"path": "b"}))
        execute.assert_not_awaited()  # everything after prevention is skipped

    execute2 = AsyncMock(return_value=_mock_result())
    proc2 = _make_processor()
    parts2 = [_completed_tool_part("read", {"path": "a"})] * 2 + [_completed_tool_part("read", {"path": "b"})]
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=parts2)), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute2):
        await proc2.process_event(ToolCallEvent(tool_call_id="c5", tool_name="read", input={"path": "b"}))
    execute2.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_success_and_error_states_and_callbacks():
    """SES-0506 / SES-0508 / SES-0511."""
    cb = AsyncMock()
    ends = []

    async def tool_end(name, result):
        ends.append((name, result.success, result.error))
        raise RuntimeError("callback boom")  # SES-0511: must not break the flow

    async def tool_start(name, inp):
        raise RuntimeError("start boom")

    proc = StreamProcessor(
        session_id="ses_sp_test",
        assistant_message=_make_assistant_msg(),
        agent=_make_agent(),
        allowed_tool_names=["echo"],
        event_publish_callback=cb,
        tool_start_callback=tool_start,
        tool_end_callback=tool_end,
    )
    ok = AsyncMock(return_value=ToolResult(success=True, output="ok", title="Echo", metadata={"k": 1}))
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=ok):
        await proc.process_event(ToolCallEvent(tool_call_id="ok1", tool_name="echo", input={"v": 1}))
    done = [p for p in _part_events(cb, "tool") if p["state"]["status"] == "completed"]
    assert done and done[-1]["state"]["output"] == "ok" and done[-1]["state"]["title"] == "Echo"
    assert done[-1]["state"]["time"]["start"] and done[-1]["state"]["time"]["end"]
    assert ends[-1] == ("echo", True, None)

    boom = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=boom):
        await proc.process_event(ToolCallEvent(tool_call_id="err1", tool_name="echo", input={}))
    errors = [p for p in _part_events(cb, "tool") if p["state"]["status"] == "error"]
    assert errors and errors[-1]["state"]["error"] == "boom"
    assert ends[-1] == ("echo", False, "boom")


@pytest.mark.asyncio
async def test_tool_error_message_fallback_order():
    """SES-0507."""
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)
    results = [
        ToolResult(success=False, output="", error=None, metadata={"output": "stderr text"}),
        ToolResult(success=False, output="out", error=None, metadata={}),
        ToolResult(success=False, output="", error=None, metadata={}),
    ]
    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=AsyncMock(side_effect=results)):
        for i in range(3):
            await proc.process_event(ToolCallEvent(tool_call_id=f"e{i}", tool_name="echo", input={"i": i}))
    errors = [p["state"]["error"] for p in _part_events(cb, "tool") if p["state"]["status"] == "error"]
    assert errors == ["stderr text", "out", "Unknown error"]


@pytest.mark.asyncio
async def test_running_metadata_is_published_and_final_state_wins():
    """SES-0509."""
    cb = AsyncMock()
    proc = _make_processor(event_callback=cb)

    async def execute(*, tool_name, ctx, **inp):
        ctx.metadata({"metadata": {"sessionId": "child_1"}})
        await asyncio.sleep(0.05)
        return _mock_result(output="done")

    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        await proc.process_event(ToolCallEvent(tool_call_id="m1", tool_name="delegate", input={}))
        await asyncio.sleep(0.05)
    tool_events = _part_events(cb, "tool")
    running_with_meta = [p for p in tool_events if p["state"]["status"] == "running" and (p["state"].get("metadata") or {}).get("sessionId") == "child_1"]
    assert running_with_meta
    assert tool_events[-1]["state"]["status"] == "completed"


@pytest.mark.asyncio
async def test_tool_context_carries_abort_event_execution_mode_and_model():
    """SES-0513."""
    proc = _make_processor()
    proc.assistant_message.providerID = "p"
    proc.assistant_message.modelID = "m"
    captured = {}

    async def execute(*, tool_name, ctx, **inp):
        captured["ctx"] = ctx
        return _mock_result()

    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        await proc.process_event(ToolCallEvent(tool_call_id="x1", tool_name="echo", input={}))
    ctx = captured["ctx"]
    assert ctx.abort is proc.abort_event
    assert ctx.extra["execution_mode"] == "build"
    assert ctx.extra["model"] == {"providerID": "p", "modelID": "m"}


@pytest.mark.asyncio
async def test_sandbox_meta_only_for_sandboxed_tools():
    """SES-0505: non-sandbox tools only get the policy metadata, bash also gets the container extra."""
    proc = _make_processor()
    runtime = MagicMock()
    runtime.sandboxed = True
    runtime.tool_policy_metadata = {"mode": "sandbox"}
    proc._sandbox_runtime_cache = runtime
    proc._sandbox_config_cache = MagicMock()
    grep_meta = await proc._resolve_sandbox_meta("grep")
    assert grep_meta["blocked"] is False
    assert grep_meta["extra"] == {"sandbox_tool_policy": {"mode": "sandbox"}}
    sandbox_ctx = MagicMock()
    sandbox_ctx.container_name = "c1"
    sandbox_ctx.workspace_dir = "/ws"
    sandbox_ctx.container_workdir = "/work"
    sandbox_ctx.docker.env = {}
    sandbox_ctx.workspace_access = "rw"
    sandbox_ctx.agent_workspace_dir = "/ws/agent"
    proc._sandbox_context_cache = sandbox_ctx
    proc._sandbox_context_resolved = True
    bash_meta = await proc._resolve_sandbox_meta("bash")
    assert bash_meta["extra"]["sandbox_tool_policy"] == {"mode": "sandbox"}
    assert bash_meta["extra"]["sandbox"]["container_name"] == "c1"
    assert bash_meta["extra"]["sandbox"]["workspace_dir"] == "/ws"


# ---------------------------------------------------------------------------
# parallel sub-agent tools  (SES-0516 / 0517 / 0518)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_delegate_runs_inline_and_duplicate_parallel_ids_start_once():
    """SES-0516 / SES-0517."""
    proc = _make_processor()
    assert proc._should_run_tool_call_parallel(ToolCallEvent(tool_call_id="a", tool_name="delegate_task", input={"run_in_background": True, "subagent_type": "explore", "prompt": "x"})) is False
    assert proc._should_run_tool_call_parallel(ToolCallEvent(tool_call_id="b", tool_name="delegate_task", input={"prompt": "x"})) is False
    assert proc._should_run_tool_call_parallel(ToolCallEvent(tool_call_id="c", tool_name="delegate_task", input={"subagent_type": "explore", "prompt": "x"})) is True

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def execute(*, tool_name, ctx, **inp):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _mock_result()

    with patch.multiple("flocks.session.streaming.stream_processor.Message", store_part=AsyncMock(), update_part=AsyncMock(), parts=AsyncMock(return_value=[])), \
         patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=execute):
        ev = ToolCallEvent(tool_call_id="p1", tool_name="delegate_task", input={"subagent_type": "explore", "prompt": "x"})
        await proc.process_event(ev)
        await proc.process_event(ev)
        await started.wait()
        assert len(proc._parallel_tool_tasks) == 1
        release.set()
        await proc.drain_parallel_tool_calls()
    assert calls == 1


@pytest.mark.asyncio
async def test_drain_parallel_tool_calls_survives_task_errors():
    """SES-0518."""
    proc = _make_processor()
    await proc.drain_parallel_tool_calls()  # no tasks: returns immediately

    async def boom():
        raise RuntimeError("delegate failed")

    async def fine():
        return "ok"

    proc._parallel_tool_tasks["b"] = asyncio.create_task(boom())
    proc._parallel_tool_tasks["f"] = asyncio.create_task(fine())
    await proc.drain_parallel_tool_calls()  # gather(return_exceptions=True): no raise
    assert all(t.done() for t in proc._parallel_tool_tasks.values())
    assert isinstance(proc._parallel_tool_tasks["b"].exception(), RuntimeError)


# ---------------------------------------------------------------------------
# accumulator  (SES-0534 / 0537 / 0540 / 0541)
# ---------------------------------------------------------------------------

def _acc():
    from flocks.session.streaming.tool_accumulator import ToolCallAccumulator

    processor = MagicMock()
    processor.process_event = AsyncMock()
    return ToolCallAccumulator(processor), processor


def _chunk(index=0, tc_id=None, name=None, arguments=None):
    chunk = {"index": index}
    if tc_id:
        chunk["id"] = tc_id
    func = {}
    if name:
        func["name"] = name
    if arguments is not None:
        func["arguments"] = arguments
    if func:
        chunk["function"] = func
    return chunk


@pytest.mark.asyncio
async def test_closed_json_missing_required_keeps_accumulating():
    """SES-0534."""
    acc, proc = _acc()
    schema = MagicMock()
    schema.required = ["path", "content"]
    with patch("flocks.session.streaming.tool_accumulator.ToolRegistry.get_schema", return_value=schema):
        await acc.feed_chunk(_chunk(tc_id="c1", name="write", arguments='{"path":"a"}'))
        calls = [c.args[0] for c in proc.process_event.await_args_list if isinstance(c.args[0], ToolCallEvent)]
        assert calls == []
        assert acc._should_accumulate("c1", '{"path":"a"}') is True


@pytest.mark.asyncio
async def test_truncated_json_is_reported_not_repaired():
    """SES-0537 (behaviour changed upstream): padding a cut-off ``write`` back
    into shape and executing it wrote half a file and reported success, so a
    truncated argument stream is now surfaced as a truncation error and the
    tool is never executed."""
    from flocks.session.streaming.stream_events import ToolInputErrorEvent
    from flocks.session.streaming.tool_accumulator import StreamToolArgumentsTruncatedError

    acc, proc = _acc()
    schema = MagicMock()
    schema.required = ["path", "content"]
    with patch("flocks.session.streaming.tool_accumulator.ToolRegistry.get_schema", return_value=schema), \
         patch("flocks.session.streaming.tool_accumulator.ToolRegistry.get", return_value=MagicMock()):
        await acc.feed_chunk(_chunk(tc_id="c1", name="write", arguments='{"path":"a","content":"x'))
        with pytest.raises(StreamToolArgumentsTruncatedError):
            await acc.flush_remaining()
    events = [c.args[0] for c in proc.process_event.await_args_list]
    assert not [e for e in events if isinstance(e, ToolCallEvent)]
    errors = [e for e in events if isinstance(e, ToolInputErrorEvent)]
    assert len(errors) == 1 and errors[0].tool_name == "write"


@pytest.mark.asyncio
async def test_unicode_and_escaped_quotes_parse():
    """SES-0540."""
    acc, proc = _acc()
    with patch("flocks.session.streaming.tool_accumulator.ToolRegistry.get_schema", return_value=None):
        await acc.feed_chunk(_chunk(tc_id="c1", name="echo", arguments='{"text":"你好😀 \\"q\\""}'))
    calls = [c.args[0] for c in proc.process_event.await_args_list if isinstance(c.args[0], ToolCallEvent)]
    assert calls and calls[0].input == {"text": '你好😀 "q"'}


@pytest.mark.asyncio
async def test_large_arguments_over_many_chunks_execute_once():
    """SES-0541."""
    acc, proc = _acc()
    payload = json.dumps({"content": "x" * (10 * 1024)})
    pieces = [payload[i:i + len(payload) // 100 + 1] for i in range(0, len(payload), len(payload) // 100 + 1)]
    with patch("flocks.session.streaming.tool_accumulator.ToolRegistry.get_schema", return_value=None):
        await acc.feed_chunk(_chunk(tc_id="c1", name="write"))
        for piece in pieces:
            await acc.feed_chunk(_chunk(tc_id="c1", arguments=piece))
        await acc.flush_remaining()
    starts = [c.args[0] for c in proc.process_event.await_args_list if isinstance(c.args[0], ToolInputStartEvent)]
    calls = [c.args[0] for c in proc.process_event.await_args_list if isinstance(c.args[0], ToolCallEvent)]
    assert len(starts) == 1 and len(calls) == 1
    assert calls[0].input == {"content": "x" * (10 * 1024)}


# ---------------------------------------------------------------------------
# streaming replacement buffer  (SES-0550 ~ 0554 / 0556)
# ---------------------------------------------------------------------------

def test_replacement_buffer_holds_partial_placeholder_until_complete():
    """SES-0550 / SES-0552 / SES-0554."""
    from flocks.session.llm_hook_utils import StreamingTextReplacementBuffer

    buf = StreamingTextReplacementBuffer([("<<SECRET_1>>", "sk-real")])
    assert buf.feed("token <<SEC") == "token "
    assert buf.feed("RET_1>> end") == "sk-real end"
    assert buf.feed("") == ""
    assert buf.feed("abc<<") == "abc"
    assert buf.flush() == "<<"
    assert buf.flush() == ""


def test_replacement_buffer_prefers_longer_patterns_and_passthrough_without_rules():
    """SES-0551 / SES-0553."""
    from flocks.session.llm_hook_utils import StreamingTextReplacementBuffer

    buf = StreamingTextReplacementBuffer([("<<A>>", "x"), ("<<A>>_2", "y")])
    assert (buf.feed("<<A>>_2 <<A>>") + buf.flush()) == "y x"
    empty = StreamingTextReplacementBuffer([])
    assert empty.enabled is False
    assert empty.feed("<<partial") == "<<partial"


def test_invalid_stream_replacement_items_are_ignored():
    """SES-0556."""
    from flocks.session.llm_hook_utils import stream_text_replacements_from_hook_output

    result = stream_text_replacements_from_hook_output(
        {"redaction": {"streamTextReplacements": [{"placeholder": 1, "value": "x"}, "bad", {"placeholder": "<<A>>", "value": "v"}]}}
    )
    assert result == [("<<A>>", "v")]
