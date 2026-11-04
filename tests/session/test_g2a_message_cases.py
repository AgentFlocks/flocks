"""Message / runner / queue cases from the 2026-09-14 execution (G2a).

Each test names the case ids it covers (SES-xxxx).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import flocks.session.runner as runner_mod
from flocks.provider.provider import ChatMessage
from flocks.session.runner import SessionRunner
from flocks.session.session import SessionInfo


# ---------------------------------------------------------------------------
# runner harness (real ``SessionRunner._call_llm`` with a fake provider stream)
# ---------------------------------------------------------------------------

_last_processor: "_RecordingProcessor | None" = None


class _RecordingProcessor:
    """Records every stream event the runner emits, in order."""

    def __init__(self, **_: object):
        global _last_processor
        _last_processor = self
        self.events: list[tuple[str, dict]] = []
        self.finish_reason = "stop"
        self.tool_calls = {}
        self._langfuse_generation = None
        self.drained = 0
        self.drain_positions: list[int] = []

    async def process_event(self, event) -> None:
        name = type(event).__name__
        payload = {}
        for key in ("id", "text", "finish_reason", "tool_name", "tool_call_id", "metadata"):
            if hasattr(event, key):
                payload[key] = getattr(event, key)
        self.events.append((name, payload))
        if name == "FinishEvent":
            self.finish_reason = event.finish_reason

    def get_text_content(self) -> str:
        return "".join(p.get("text", "") for n, p in self.events if n == "TextDeltaEvent")

    def get_reasoning_content(self) -> str:
        return "".join(p.get("text", "") for n, p in self.events if n == "ReasoningDeltaEvent")

    def get_finish_reason(self):
        return self.finish_reason

    async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
        self.drained += 1
        self.drain_positions.append(len(self.events))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]


class _FakeToolAccumulator:
    def __init__(self, processor):
        self.processor = processor
        self.fed = []

    async def feed_chunk(self, tool_call) -> None:
        self.fed.append(tool_call)

    async def flush_remaining(self, finish_reason, *, output_limit=None, raised_output_limit=None) -> None:
        return None


def _chunk(**kwargs):
    base = dict(delta="", reasoning=None, tool_calls=None, event_type=None, finish_reason=None, usage=None, metadata=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def _provider(chunks, *, on_chunk=None):
    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                for index, chunk in enumerate(chunks):
                    if on_chunk:
                        await on_chunk(index)
                    yield chunk

            return _gen()

    return _Provider()


def _runner(session_id: str = "ses_g2a_runner") -> SessionRunner:
    session = SessionInfo.model_construct(id=session_id, slug="t", project_id="proj", directory="/tmp", title="t")
    return SessionRunner(session=session, provider_id="anthropic", model_id="claude-sonnet")


@pytest.fixture
def runner_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_mod, "StreamProcessor", _RecordingProcessor)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.SessionRunner, "_end_observability", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda provider_id, model_id: {})
    monkeypatch.setattr("flocks.session.streaming.tool_accumulator.ToolCallAccumulator", _FakeToolAccumulator)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "trace_scope", lambda **kwargs: SimpleNamespace(observation=None))
    monkeypatch.setattr(runner_mod, "generation_scope", lambda **kwargs: SimpleNamespace(observation=None))
    return monkeypatch


async def _call(runner: SessionRunner, provider, *, assistant_msg=None):
    return await runner._call_llm(
        provider=provider,
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant_msg or SimpleNamespace(id="msg_a"),
    )


# ---------------------------------------------------------------------------
# reasoning / text chunk handling  (SES-0485 / 0486 / 0487 / 0488 / 0489)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_chunk_closes_reasoning_before_text(runner_env):
    """SES-0485."""
    runner = _runner()
    result = await _call(runner, _provider([_chunk(reasoning="think", delta="text", finish_reason="stop")]))
    assert result.content == "text"
    names = _last_processor.names()
    assert names.index("ReasoningStartEvent") < names.index("ReasoningDeltaEvent") < names.index("ReasoningEndEvent") < names.index("TextStartEvent") < names.index("TextDeltaEvent")


@pytest.mark.asyncio
async def test_legacy_reasoning_event_type_treats_delta_as_thought(runner_env):
    """SES-0486."""
    runner = _runner()
    await _call(runner, _provider([_chunk(event_type="reasoning", delta="思考"), _chunk(finish_reason="stop")]))
    names = _last_processor.names()
    assert "ReasoningDeltaEvent" in names and "TextDeltaEvent" not in names
    assert _last_processor.get_reasoning_content() == "思考"


@pytest.mark.asyncio
async def test_usage_only_chunk_keeps_reasoning_block_open(runner_env):
    """SES-0487."""
    runner = _runner()
    await _call(runner, _provider([
        _chunk(reasoning="a"),
        _chunk(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
        _chunk(reasoning="b"),
        _chunk(finish_reason="stop"),
    ]))
    names = _last_processor.names()
    assert names.count("ReasoningStartEvent") == 1 and names.count("ReasoningEndEvent") == 1


@pytest.mark.asyncio
async def test_metadata_only_reasoning_chunk_opens_block(runner_env):
    """SES-0488."""
    runner = _runner()
    await _call(runner, _provider([_chunk(event_type="reasoning", metadata={"reasoningDetails": [{"type": "x"}]}), _chunk(delta="t", finish_reason="stop")]))
    names = _last_processor.names()
    assert "ReasoningStartEvent" in names
    assert not any(n == "ReasoningDeltaEvent" and p.get("text") for n, p in _last_processor.events)


@pytest.mark.asyncio
async def test_stream_end_closes_unfinished_reasoning_block(runner_env):
    """SES-0489."""
    runner = _runner()
    await _call(runner, _provider([_chunk(reasoning="only thought")]))
    names = _last_processor.names()
    assert names.count("ReasoningEndEvent") == 1
    assert names.index("ReasoningEndEvent") > names.index("ReasoningDeltaEvent")


# ---------------------------------------------------------------------------
# parallel drain / abort during stream  (SES-0519 / 0522 / 0523 / 0547 / 0548)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_tool_calls_are_drained_after_finish(runner_env):
    """SES-0519 / SES-0547."""
    runner = _runner()
    await _call(runner, _provider([_chunk(delta="a"), _chunk(delta="b", finish_reason="stop")]))
    proc = _last_processor
    assert proc.drained >= 1
    finish_index = proc.names().index("FinishEvent")
    assert proc.drain_positions[0] >= finish_index  # drained after the finish event
    assert not any(n == "TextDeltaEvent" for n in proc.names()[finish_index + 1:])


@pytest.mark.asyncio
async def test_abort_mid_stream_keeps_received_text_and_marks_call_aborted(runner_env):
    """SES-0522: cooperative abort after the 3rd delta."""
    runner = _runner()
    seen = 0

    async def on_chunk(index):
        nonlocal seen
        seen = index
        if index == 3:
            runner.abort()

    result = await _call(runner, _provider([_chunk(delta=str(i)) for i in range(6)] + [_chunk(finish_reason="stop")], on_chunk=on_chunk))
    assert runner._llm_call_aborted is True
    # the runner checks the abort flag as each chunk arrives: three deltas were kept, nothing after
    assert _last_processor.get_text_content() == "012"
    assert result.content == "012"


@pytest.mark.asyncio
async def test_cancelled_stream_drains_and_closes_rewriter(runner_env, monkeypatch):
    """SES-0523."""
    runner = _runner()
    closed = []

    class _Rewriter:
        async def close(self):
            closed.append(True)

        async def feed_text(self, text):
            return text

        async def feed_reasoning(self, text):
            return text

        async def flush_text(self):
            return ""

        async def flush_reasoning(self):
            return ""

        async def transform_metadata(self, value, metadata):
            return value

    monkeypatch.setattr(runner_mod.SessionResponseRewriter, "create", AsyncMock(return_value=_Rewriter()))

    async def on_chunk(index):
        if index == 1:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _call(runner, _provider([_chunk(delta="a"), _chunk(delta="b"), _chunk(finish_reason="stop")], on_chunk=on_chunk))
    assert closed == [True]
    assert _last_processor.drained >= 1


# ---------------------------------------------------------------------------
# cancel path marks the assistant message  (SES-0374 / 0522)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_task_cancellation_marks_assistant_message_aborted(monkeypatch):
    """Cancelling the step (external abort) persists MessageAbortedError with finish=error."""
    updates = []

    async def fake_update(session_id, message_id, **kwargs):
        updates.append((session_id, message_id, kwargs))

    monkeypatch.setattr(runner_mod.Message, "update", fake_update)
    runner = _runner("ses_cancel")

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runner, "_call_llm", cancelled)
    assistant_msg = SimpleNamespace(id="msg_cancel")

    async def _step():
        # replicate the guarded call site inside ``_process_step``
        try:
            await runner._call_llm(provider=None, messages=[], tools=[], agent=None, assistant_msg=assistant_msg)
        except asyncio.CancelledError:
            await asyncio.shield(runner_mod.Message.update(
                runner.session.id,
                assistant_msg.id,
                error=runner._build_message_aborted_error(),
                finish="error",
            ))
            raise

    with pytest.raises(asyncio.CancelledError):
        await _step()
    assert updates and updates[-1][2]["finish"] == "error"
    assert updates[-1][2]["error"]["name"] == "MessageAbortedError"


def test_process_step_guards_call_llm_against_cancellation():
    """The guard above is what ``_process_step`` runs (pin the source so the replica stays honest)."""
    import inspect

    source = inspect.getsource(runner_mod.SessionRunner._process_step)
    assert "except asyncio.CancelledError:" in source
    assert "_build_message_aborted_error()" in source and 'finish="error"' in source


# ---------------------------------------------------------------------------
# routes: directory fallback / background errors / queue context  (SES-0273 / 0290 / 0291 / 0323 / 0368 / 0375 / 0383)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_directories_unavailable_is_409(tmp_path, monkeypatch):
    """SES-0273."""
    from fastapi import HTTPException

    from flocks.server.routes import session as session_routes

    monkeypatch.setattr("flocks.project.instance.Instance.get_directory", staticmethod(lambda: str(tmp_path / "missing-instance")))
    with pytest.raises(HTTPException) as err:
        await session_routes._resolve_session_working_directory(SimpleNamespace(id="ses_x", directory=str(tmp_path / "missing-session")))
    assert err.value.status_code == 409
    assert err.value.detail == "The session directory and current request directory are unavailable"


@pytest.mark.asyncio
async def test_background_error_is_published_as_session_error(monkeypatch):
    """SES-0290."""
    from flocks.server.routes import event as event_routes
    from flocks.server.routes import session as session_routes

    published = []

    async def fake_publish(event_type, properties=None, directory=None):
        published.append((event_type, properties))

    monkeypatch.setattr(event_routes, "publish_event", fake_publish)

    async def boom():
        raise RuntimeError("dispatch failed")

    session_routes._schedule_background_coro(boom(), session_id="ses_bg", action="session.prompt_async")
    for _ in range(50):
        if published:
            break
        await asyncio.sleep(0.01)
    assert published and published[0][0] == "session.error"
    assert published[0][1]["error"]["data"]["action"] == "session.prompt_async"
    assert published[0][1]["error"]["message"] == "dispatch failed"


@pytest.mark.asyncio
async def test_queue_dispatch_restores_item_context_without_inheriting(monkeypatch):
    """SES-0291."""
    from flocks.hooks.execution import current_execution_context, execution_context_scope
    from flocks.server.routes import session as session_routes
    from flocks.session.interaction_queue import InteractionQueue

    seen = []

    async def fake_dispatch(session_id, session, event, working_directory):
        seen.append(current_execution_context())

    async def fake_provide(*, directory, init, fn):
        return await fn()

    monkeypatch.setattr(session_routes, "_dispatch_sse_input", fake_dispatch)
    monkeypatch.setattr("flocks.project.instance.Instance.provide", fake_provide)
    monkeypatch.setattr(session_routes, "_publish_prompt_queue", AsyncMock())
    monkeypatch.setattr("flocks.session.session_loop.SessionLoop.is_running", classmethod(lambda cls, sid: False))
    monkeypatch.setattr(session_routes.Session, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="ses_q", directory="/tmp")))
    await InteractionQueue.clear("ses_q")
    await InteractionQueue.enqueue("ses_q", parts=[{"type": "text", "text": "a"}], execution_context={"subject": {"subject_id": "u1"}})
    await InteractionQueue.enqueue("ses_q", parts=[{"type": "text", "text": "b"}], execution_context={"subject": {"subject_id": "u2"}})
    with execution_context_scope({"subject": {"subject_id": "worker"}, "leak": True}):
        await session_routes._drain_prompt_queue_locked("ses_q", "/tmp")
    assert [ctx["subject"]["subject_id"] for ctx in seen] == ["u1", "u2"]
    assert all("leak" not in ctx for ctx in seen)


@pytest.mark.asyncio
async def test_drain_retry_exhausted_is_logged(monkeypatch):
    """SES-0323."""
    from flocks.server.routes import session as session_routes

    warned = []
    monkeypatch.setattr(session_routes.log, "warn", lambda event, payload=None: warned.append((event, payload)))
    monkeypatch.setattr(session_routes, "_drain_prompt_queue_locked", AsyncMock(return_value=False))
    original_sleep = asyncio.sleep
    monkeypatch.setattr(session_routes.asyncio, "sleep", lambda s: original_sleep(0))
    await session_routes._schedule_prompt_queue_drain("ses_drain", "/tmp")
    for _ in range(200):
        if any(e == "session.prompt_queue.drain_retry_exhausted" for e, _ in warned):
            break
        await original_sleep(0.01)
    exhausted = [p for e, p in warned if e == "session.prompt_queue.drain_retry_exhausted"]
    assert exhausted and exhausted[0]["attempts"] == 80


@pytest.mark.asyncio
async def test_update_executing_queue_item_is_rejected():
    """SES-0311."""
    from flocks.session.interaction_queue import InteractionQueue, QueueItemNotFoundError

    await InteractionQueue.clear("ses_exec")
    item = await InteractionQueue.enqueue("ses_exec", parts=[{"type": "text", "text": "a"}])
    popped = await InteractionQueue.pop_next("ses_exec")
    assert popped.status == "executing"
    with pytest.raises(QueueItemNotFoundError):
        await InteractionQueue.update_text("ses_exec", item.id, "new")


@pytest.mark.asyncio
async def test_regenerate_without_parent_is_400(monkeypatch):
    """SES-0368."""
    from fastapi import HTTPException

    from flocks.server.routes import session as session_routes

    monkeypatch.setattr("flocks.session.message.Message.get", AsyncMock(return_value=SimpleNamespace(role="assistant", parentID=None)))
    with pytest.raises(HTTPException) as err:
        await session_routes.regenerate_session_message("ses_r", "msg_a", SimpleNamespace())
    assert err.value.status_code == 400
    assert err.value.detail == "Assistant message does not have a parent user message"


@pytest.mark.asyncio
async def test_abort_cascades_to_children_background_tasks_and_questions(monkeypatch):
    """SES-0375."""
    from flocks.server.routes import event as event_routes
    from flocks.server.routes import session as session_routes
    from flocks.session.runner import SessionRunner as Runner
    from flocks.session.session_loop import SessionLoop

    monkeypatch.setattr(SessionLoop, "abort", classmethod(lambda cls, sid: True))
    monkeypatch.setattr(SessionLoop, "abort_children", classmethod(lambda cls, sid: 2))
    monkeypatch.setattr(Runner, "cancel", classmethod(lambda cls, sid: True))
    monkeypatch.setattr(Runner, "cancel_children", classmethod(lambda cls, sid: 2))
    monkeypatch.setattr("flocks.server.routes.question.reject_session_questions", AsyncMock(return_value=1))
    # Background runs live in the manager of the instance that launched them:
    # the abort reaches every instance's manager, not only the request's own.
    manager = MagicMock()
    manager.cancel_by_parent_session_id.return_value = 3
    other_instance = MagicMock()
    other_instance.cancel_by_parent_session_id.return_value = 1
    monkeypatch.setattr("flocks.task.background.all_background_managers", lambda: [manager, other_instance])
    monkeypatch.setattr(event_routes, "publish_event", AsyncMock())
    logged = []
    monkeypatch.setattr(session_routes.log, "info", lambda event, payload=None: logged.append((event, payload)))

    assert await session_routes._abort_session_processing("ses_parent") is True
    payload = next(p for e, p in logged if e == "session.aborted")
    assert payload["children_loops_aborted"] == 2
    assert payload["bg_tasks_cancelled"] == 4
    assert payload["questions_rejected"] == 1
    manager.cancel_by_parent_session_id.assert_called_once_with("ses_parent")
    other_instance.cancel_by_parent_session_id.assert_called_once_with("ses_parent")


@pytest.mark.asyncio
async def test_command_arguments_json_is_serialized_and_kept_in_metadata(monkeypatch):
    """SES-0383."""
    import json

    from flocks.server.routes import session as session_routes

    dispatch = AsyncMock()

    async def fake_provide(*, directory, init, fn):
        await fn()

    monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch)
    monkeypatch.setattr("flocks.project.instance.Instance.provide", fake_provide)
    monkeypatch.setattr(session_routes, "_get_session_by_id_unfiltered", AsyncMock(return_value=SimpleNamespace(id="ses_cmd", directory="/tmp", status="active")))
    monkeypatch.setattr(session_routes, "_resolve_session_working_directory", AsyncMock(return_value="/tmp"))
    request = session_routes.CommandRequest(command="x", argumentsJson={"a": 1})
    resp = await session_routes.send_session_command("ses_cmd", request)
    assert resp["status"] == "accepted"
    await asyncio.sleep(0)
    dispatch.assert_awaited_once()
    event = dispatch.await_args.args[2]
    assert event.text == f"/x {json.dumps({'a': 1}, ensure_ascii=False)}"
    assert event.metadata["commandArgumentsJson"] == {"a": 1}


@pytest.mark.asyncio
async def test_update_tool_part_state_only_touches_existing_keys():
    """SES-0354."""
    from flocks.session.message import Message, MessageRole, ToolPart, ToolStateRunning

    await Message.init() if hasattr(Message, "init") else None
    user = await Message.create(session_id="ses_tool_part", role=MessageRole.USER, content="hi")
    assistant = await Message.create(session_id="ses_tool_part", role=MessageRole.ASSISTANT, content="", parentID=user.id, modelID="m", providerID="p")
    part = ToolPart(
        id="prt_tool_state",
        sessionID="ses_tool_part",
        messageID=assistant.id,
        type="tool",
        callID="c1",
        tool="bash",
        state=ToolStateRunning(status="running", input={"command": "ls"}, time={"start": 1}),
    )
    await Message.add_part("ses_tool_part", assistant.id, part)
    updated = await Message.update_part(
        "ses_tool_part",
        assistant.id,
        "prt_tool_state",
        type="tool",
        state={"status": "completed", "input": {"command": "ls"}, "output": "ok", "title": "ls", "metadata": {}, "time": {"start": 1, "end": 2}},
        bogus="ignored",
    )
    assert updated is not None
    assert updated.state.status == "completed"
    assert not hasattr(updated, "bogus")
