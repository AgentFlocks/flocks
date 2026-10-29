"""Stopping a reply keeps the text the user has already seen.

The stop button cancels the step task in the middle of the model stream.
Text was only stored at ``text-end`` (reasoning at ``reasoning-end``), which a
cancelled stream never reaches, so the stored assistant message ended up with
an empty text part although the text had been shown.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.provider.provider import ChatMessage
from flocks.session.message import Message, MessageRole
from flocks.session.runner import SessionRunner
from flocks.session.session import Session


def _chunk(*, delta=None, reasoning=None):
    return SimpleNamespace(delta=delta, reasoning=reasoning, tool_calls=None)


class _BlockingProvider:
    """Streams the given chunks, then waits until the step is cancelled."""

    def __init__(self, chunks, streamed: asyncio.Event):
        self._chunks = chunks
        self._streamed = streamed

    async def chat_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk
        # Resumed only after the runner processed the last chunk.
        self._streamed.set()
        await asyncio.Event().wait()


async def _cancel_mid_stream(tmp_path, monkeypatch, chunks):
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    await Message.create(session.id, MessageRole.USER, "count to a thousand")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "")
    runner = SessionRunner(session=session, provider_id="p", model_id="m")
    streamed = asyncio.Event()
    step = asyncio.create_task(runner._call_llm(
        provider=_BlockingProvider(chunks, streamed),
        messages=[ChatMessage(role="user", content="count to a thousand")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant,
    ))
    await asyncio.wait_for(streamed.wait(), 5)
    step.cancel()  # what SessionLoop.signal_abort does for the stop button
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(step, 5)
    return await Message.parts(assistant.id, session.id)


@pytest.mark.asyncio
async def test_cancelled_stream_keeps_the_streamed_text(tmp_path, monkeypatch):
    parts = await _cancel_mid_stream(
        tmp_path,
        monkeypatch,
        [_chunk(delta="1\n2\n"), _chunk(delta="3\n4\n"), _chunk(delta="5")],
    )

    texts = [part.text for part in parts if part.type == "text" and part.text]
    assert texts == ["1\n2\n3\n4\n5"]
    text_part = next(part for part in parts if part.type == "text" and part.text)
    assert text_part.time is not None and text_part.time.end is not None


@pytest.mark.asyncio
async def test_cancelled_stream_keeps_unfinished_reasoning(tmp_path, monkeypatch):
    parts = await _cancel_mid_stream(
        tmp_path,
        monkeypatch,
        [_chunk(reasoning="Thinking about "), _chunk(reasoning="the count")],
    )

    reasoning = [part.text for part in parts if part.type == "reasoning"]
    assert reasoning == ["Thinking about the count"]


class _EndingProvider:
    """Streams the chunks and ends normally."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def chat_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_stop_while_the_finished_stream_is_closed_keeps_the_text(tmp_path, monkeypatch):
    """Stopped during text-end (e.g. running a tool call written in the text), after the stream ended."""
    entered = asyncio.Event()

    class SlowTextEnd(runner_mod.StreamProcessor):
        async def _handle_text_end(self, event):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "StreamProcessor", SlowTextEnd)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    await Message.create(session.id, MessageRole.USER, "count")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "")
    runner = SessionRunner(session=session, provider_id="p", model_id="m")
    step = asyncio.create_task(runner._call_llm(
        provider=_EndingProvider([_chunk(delta="1\n2\n"), _chunk(delta="3")]),
        messages=[ChatMessage(role="user", content="count")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant,
    ))
    await asyncio.wait_for(entered.wait(), 5)
    step.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(step, 5)

    parts = await Message.parts(assistant.id, session.id)
    assert [part.text for part in parts if part.type == "text" and part.text] == ["1\n2\n3"]


@pytest.mark.asyncio
async def test_a_second_stop_waits_for_the_streamed_text_to_be_written(tmp_path, monkeypatch):
    """A repeated stop must not end the step before the write, or the released session refuses it."""
    from flocks.session import message as message_mod

    original_store = message_mod.Message.store_part.__func__
    writing, release = asyncio.Event(), asyncio.Event()

    async def slow_store(cls, session_id, message_id, part, *args, **kwargs):
        if getattr(part, "type", None) == "text" and getattr(part, "text", ""):
            writing.set()
            await release.wait()
        return await original_store(cls, session_id, message_id, part, *args, **kwargs)

    monkeypatch.setattr(message_mod.Message, "store_part", classmethod(slow_store))
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    await Message.create(session.id, MessageRole.USER, "count")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "")
    runner = SessionRunner(session=session, provider_id="p", model_id="m")
    streamed = asyncio.Event()
    step = asyncio.create_task(runner._call_llm(
        provider=_BlockingProvider([_chunk(delta="1\n2\n3")], streamed),
        messages=[ChatMessage(role="user", content="count")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant,
    ))
    await asyncio.wait_for(streamed.wait(), 5)
    step.cancel()
    await asyncio.wait_for(writing.wait(), 5)
    step.cancel()  # the user presses stop again while the text is being written
    await asyncio.sleep(0.2)
    assert not step.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(step, 5)
    parts = await Message.parts(assistant.id, session.id)
    assert [part.text for part in parts if part.type == "text" and part.text] == ["1\n2\n3"]


class _FailingProvider:
    """Streams the chunks, then fails like a dropped provider connection."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def chat_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk
        raise RuntimeError("provider connection dropped")


@pytest.mark.parametrize(
    "where",
    ["after the stream ended", "second stop in the cancel branch", "stop in the error branch"],
)
@pytest.mark.asyncio
async def test_a_stop_while_the_activity_marker_is_closed_keeps_the_text(tmp_path, monkeypatch, where):
    """The marker's write is an await on every way out of the stream; a stop landing there kept nothing."""
    stopping = asyncio.Event()

    class StopBlocksOnce(runner_mod.ModelActivity):
        blocked = False

        async def stop(self):
            if not StopBlocksOnce.blocked:
                StopBlocksOnce.blocked = True
                stopping.set()
                await asyncio.Event().wait()
            await super().stop()

    monkeypatch.setattr(runner_mod, "ModelActivity", StopBlocksOnce)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    await Message.create(session.id, MessageRole.USER, "count")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "")
    runner = SessionRunner(session=session, provider_id="p", model_id="m")
    chunks = [_chunk(delta="1\n2\n"), _chunk(delta="3")]
    streamed = asyncio.Event()
    provider = {
        "after the stream ended": _EndingProvider(chunks),
        "second stop in the cancel branch": _BlockingProvider(chunks, streamed),
        "stop in the error branch": _FailingProvider(chunks),
    }[where]
    step = asyncio.create_task(runner._call_llm(
        provider=provider,
        messages=[ChatMessage(role="user", content="count")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant,
    ))
    if where == "second stop in the cancel branch":
        await asyncio.wait_for(streamed.wait(), 5)
        step.cancel()
    await asyncio.wait_for(stopping.wait(), 5)
    step.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(step, 5)

    parts = await Message.parts(assistant.id, session.id)
    assert [part.text for part in parts if part.type == "text" and part.text] == ["1\n2\n3"]


@pytest.mark.asyncio
async def test_a_write_given_up_on_is_cancelled_before_the_step_ends(tmp_path, monkeypatch):
    """Left running, it would write after the session was released (and be refused)."""
    from flocks.session import message as message_mod

    original_store = message_mod.Message.store_part.__func__
    attempts = 0
    write_cancelled = asyncio.Event()

    async def hanging_store(cls, session_id, message_id, part, *args, **kwargs):
        nonlocal attempts
        if getattr(part, "type", None) == "text" and getattr(part, "text", ""):
            attempts += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                write_cancelled.set()
                raise
        return await original_store(cls, session_id, message_id, part, *args, **kwargs)

    monkeypatch.setattr(message_mod.Message, "store_part", classmethod(hanging_store))
    monkeypatch.setattr(runner_mod, "_INTERRUPTED_PARTS_WRITE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    await Message.create(session.id, MessageRole.USER, "count")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "")
    runner = SessionRunner(session=session, provider_id="p", model_id="m")
    streamed = asyncio.Event()
    step = asyncio.create_task(runner._call_llm(
        provider=_BlockingProvider([_chunk(delta="1\n2\n3")], streamed),
        messages=[ChatMessage(role="user", content="count")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=assistant,
    ))
    await asyncio.wait_for(streamed.wait(), 5)
    step.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(step, 5)

    assert write_cancelled.is_set()
    # Given up on once, not tried again on the way out.
    assert attempts == 1
