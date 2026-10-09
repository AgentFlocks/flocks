"""SessionRunner + provider request limiter: slot lifecycle, 429 handling, priority."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import flocks.session.runner as runner_mod
from flocks.provider import concurrency
from flocks.provider.concurrency import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    get_provider_limiter,
)
from flocks.provider.provider import ChatMessage
from flocks.session.streaming.stream_processor import (
    _PARALLEL_TOOL_CALL_OWNER,
    StreamProcessor,
    parallel_tool_call_owner,
)
from flocks.session.lifecycle.retry import RETRY_MAX_DELAY_NO_HEADERS
from flocks.session.message import Message
from flocks.session.runner import RunnerCallbacks, SessionRunner
from flocks.session.session import Session, SessionInfo


@pytest.fixture(autouse=True)
def _fresh_limiters(monkeypatch):
    concurrency.reset_limiters_for_testing()
    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "1")
    yield
    concurrency.reset_limiters_for_testing()


def _session(session_id: str = "ses_llm_conc", **overrides) -> SessionInfo:
    values = dict(
        id=session_id,
        slug="test",
        project_id="proj",
        directory="/tmp",
        title="Concurrency test",
        category="user",
        parent_id=None,
    )
    values.update(overrides)
    return SessionInfo.model_construct(**values)


class _FakeProcessor:
    """Minimal StreamProcessor stand-in that can simulate a tool starting mid-stream.

    Borrows the production ownership check instead of reimplementing it, so
    breaking StreamProcessor.owns_current_parallel_tool_call breaks these
    tests too (a local copy would keep them green through that change).
    """

    tool_start_callback = None
    last_instance = None

    owns_current_parallel_tool_call = StreamProcessor.owns_current_parallel_tool_call

    def __init__(self, **kwargs):
        self._text_parts: list[str] = []
        self.finish_reason = "stop"
        self.tool_calls = {}
        self._langfuse_generation = None
        self._parallel_owner = object()
        _FakeProcessor.tool_start_callback = kwargs.get("tool_start_callback")
        _FakeProcessor.last_instance = self

    async def process_event(self, event) -> None:
        name = type(event).__name__
        if name == "TextDeltaEvent":
            self._text_parts.append(event.text)
        elif name == "FinishEvent":
            self.finish_reason = event.finish_reason

    def get_text_content(self) -> str:
        return "".join(self._text_parts)

    def get_reasoning_content(self) -> str:
        return ""

    def get_finish_reason(self):
        return self.finish_reason

    async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
        return None


class _FakeToolAccumulator:
    def __init__(self, processor):
        self.processor = processor

    async def feed_chunk(self, tool_call) -> None:
        return None

    async def flush_remaining(
        self,
        finish_reason,
        *,
        output_limit=None,
        raised_output_limit=None,
    ) -> None:
        return None


def _patch_call_llm_env(monkeypatch):
    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(
        runner_mod.SessionRunner,
        "_end_observability",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "trace_scope", lambda **kwargs: SimpleNamespace(observation=None))
    monkeypatch.setattr(runner_mod, "generation_scope", lambda **kwargs: SimpleNamespace(observation=None))


def _chunk(text: str, finish: str | None = None):
    return SimpleNamespace(
        delta=text,
        reasoning=None,
        tool_calls=None,
        event_type=None,
        finish_reason=finish,
        usage=None,
    )


@pytest.mark.asyncio
async def test_call_llm_holds_one_provider_slot_while_streaming(monkeypatch):
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")
    observed: dict[str, int] = {}

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                observed["during"] = get_provider_limiter("prov").snapshot()["active"]
                yield _chunk("hello", "stop")

            return _gen()

    result = await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_a"),
    )

    assert result.content == "hello"
    assert observed["during"] == 1
    assert get_provider_limiter("prov").snapshot()["active"] == 0
    assert runner._active_llm_slot is None


@pytest.mark.asyncio
async def test_call_llm_releases_slot_when_a_tool_starts_mid_stream(monkeypatch):
    """A parent that holds its slot while its subagents wait for one would deadlock."""
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")
    observed: dict[str, int] = {}

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                yield _chunk("calling tool")
                await _FakeProcessor.tool_start_callback("delegate_task", {"subagent_type": "explore"})
                observed["after_tool_start"] = get_provider_limiter("prov").snapshot()["active"]
                yield _chunk("", "stop")

            return _gen()

    await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_a"),
    )

    assert observed["after_tool_start"] == 0
    assert get_provider_limiter("prov").snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_parallel_subagent_tool_keeps_the_parent_slot(monkeypatch):
    """Fan-out stays inside the cap: siblings wait for the parent stream to end.

    Sibling delegate_task calls run in their own tasks and the stream never
    awaits them, so the parent can hold its slot until the stream closes
    (released in the `finally`, before drain). Without that, the parent keeps
    generating while a child takes the freed slot and the cap is breached.
    """
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")
    observed: dict[str, object] = {}

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                yield _chunk("dispatching subagents")
                # What StreamProcessor._start_parallel_tool_call does inside
                # the sibling's own task before running the tool.
                _PARALLEL_TOOL_CALL_OWNER.set(
                    _FakeProcessor.last_instance._parallel_owner
                )
                await _FakeProcessor.tool_start_callback("delegate_task", {"subagent_type": "explore"})
                limiter = get_provider_limiter("prov")
                observed["active_during_tool"] = limiter.snapshot()["active"]
                # Cap is 1: a child cannot squeeze in while the parent streams.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(limiter.acquire(), timeout=0.15)
                yield _chunk("", "stop")

            return _gen()

    await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_a"),
    )

    assert observed["active_during_tool"] == 1
    # Released at stream end, before the runner drains the siblings.
    assert get_provider_limiter("prov").snapshot()["active"] == 0
    slot = await asyncio.wait_for(get_provider_limiter("prov").acquire(), timeout=0.5)
    slot.release()


@pytest.mark.asyncio
async def test_child_session_inside_a_parallel_task_still_frees_its_own_slot(monkeypatch):
    """The parent's marker must not make a child hold its slot across an inline tool.

    A child session's runner executes inside the task its delegate_task was
    dispatched in, so it inherits the parent's context. If the marker were a
    bare flag the child would read "parallel" for its own inline tools, keep
    its slot, and a nested model call (run_workflow's llm node) would wait on
    the slot the child itself is holding.
    """
    _patch_call_llm_env(monkeypatch)
    parent_owner = object()
    _PARALLEL_TOOL_CALL_OWNER.set(parent_owner)  # inherited from the parent task
    runner = SessionRunner(session=_session("ses_child"), provider_id="prov", model_id="m")
    observed: dict[str, object] = {}

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                yield _chunk("child running an inline tool")
                # The child's own processor is a different owner, so this
                # inline tool must behave like any other inline tool.
                assert parallel_tool_call_owner() is parent_owner
                assert not _FakeProcessor.last_instance.owns_current_parallel_tool_call()
                await _FakeProcessor.tool_start_callback("run_workflow", {"workflow_id": "wf"})
                limiter = get_provider_limiter("prov")
                observed["active_during_tool"] = limiter.snapshot()["active"]
                # The nested model call the tool makes can get a slot.
                nested = await asyncio.wait_for(limiter.acquire(), timeout=0.5)
                observed["nested_acquired"] = True
                nested.release()
                yield _chunk("", "stop")

            return _gen()

    await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_child"),
    )

    assert observed["active_during_tool"] == 0
    assert observed["nested_acquired"] is True
    assert get_provider_limiter("prov").snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_rate_limit_cooldown_lands_before_the_slot_is_handed_on(monkeypatch):
    """A waiter must not inherit the slot while the cooldown is still zero."""
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")
    observed: dict[str, float] = {}

    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(status_code=429, headers={"retry-after": "2"})

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                raise _RateLimited("429 Too Many Requests")
                yield  # pragma: no cover

            return _gen()

    limiter = get_provider_limiter("prov")
    original_release = limiter._release

    def _release_and_snapshot():
        observed["cooldown_at_release"] = limiter.cooldown_remaining()
        return original_release()

    monkeypatch.setattr(limiter, "_release", _release_and_snapshot)

    with pytest.raises(Exception):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
            agent=SimpleNamespace(name="rex"),
            assistant_msg=SimpleNamespace(id="msg_a"),
        )

    # retry-after: 2s was already in effect when the slot changed hands.
    assert observed["cooldown_at_release"] > 1.0, observed


@pytest.mark.asyncio
async def test_cooldown_survives_an_abort_during_error_cleanup(monkeypatch):
    """An abort landing on cleanup must not skip the cooldown.

    The slot is released in `finally` regardless, so if the cooldown were
    registered after an await that gets cancelled, the waiter inheriting the
    slot would reach a provider that just returned 429.
    """
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")

    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(status_code=429, headers={"retry-after": "2"})

    class _CancellingActivity:
        async def start(self):
            return None

        async def stop(self):
            # Abort arrives while the error path is tidying up.
            raise asyncio.CancelledError()

    monkeypatch.setattr(runner_mod, "ModelActivity", lambda *a, **k: _CancellingActivity())

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                raise _RateLimited("429 Too Many Requests")
                yield  # pragma: no cover

            return _gen()

    with pytest.raises(asyncio.CancelledError):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
            agent=SimpleNamespace(name="rex"),
            assistant_msg=SimpleNamespace(id="msg_a"),
        )

    limiter = get_provider_limiter("prov")
    assert limiter.snapshot()["active"] == 0
    assert limiter.cooldown_remaining() > 1.0, limiter.snapshot()


@pytest.mark.asyncio
async def test_open_streams_may_exceed_the_cap_while_a_tool_runs(monkeypatch):
    """Documents the remaining overshoot: inline tools, not fan-out.

    An inline tool blocks the streaming loop and may call the model itself,
    so the session hands its slot back while it runs even though its own
    stream is still open and still generating. Sibling delegate_task calls do
    not do this (see the test above). If inline tool execution ever moves
    after the stream closes, this test should start failing and the cap can be
    documented as a hard one.
    """
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")
    observed: dict[str, int] = {}
    second_slot: list = []

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                yield _chunk("calling a tool")
                # The stream is still open here, exactly as it is when a real
                # tool-call event arrives mid-response.
                await _FakeProcessor.tool_start_callback("bash", {"command": "sleep"})
                limiter = get_provider_limiter("prov")
                observed["active_after_release"] = limiter.snapshot()["active"]
                # Cap is 1; another caller gets in while this stream is open.
                second_slot.append(await asyncio.wait_for(limiter.acquire(), timeout=0.5))
                observed["active_with_second"] = limiter.snapshot()["active"]
                yield _chunk("", "stop")

            return _gen()

    await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hi")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg_a"),
    )

    assert observed["active_after_release"] == 0
    assert observed["active_with_second"] == 1
    second_slot[0].release()
    assert get_provider_limiter("prov").snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_call_llm_releases_slot_when_the_stream_raises(monkeypatch):
    _patch_call_llm_env(monkeypatch)
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m")

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                # Not a 429 on purpose: a rate limit would also start a
                # cooldown, which the dedicated test below covers.
                raise RuntimeError("HTTP 500 internal server error")
                yield  # pragma: no cover

            return _gen()

    with pytest.raises(RuntimeError):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
            agent=SimpleNamespace(name="rex"),
            assistant_msg=SimpleNamespace(id="msg_a"),
        )

    assert get_provider_limiter("prov").snapshot()["active"] == 0
    assert get_provider_limiter("prov").cooldown_remaining() == 0
    # The next caller is not blocked by the failed one.
    slot = await asyncio.wait_for(get_provider_limiter("prov").acquire(), timeout=0.5)
    slot.release()


@pytest.mark.asyncio
async def test_second_session_waits_for_the_first_sessions_stream(monkeypatch):
    _patch_call_llm_env(monkeypatch)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    class _SlowProvider:
        def chat_stream(self, **kwargs):
            async def _gen():
                order.append("first-start")
                first_started.set()
                await release_first.wait()
                yield _chunk("one", "stop")

            return _gen()

    class _FastProvider:
        def chat_stream(self, **kwargs):
            async def _gen():
                order.append("second-start")
                yield _chunk("two", "stop")

            return _gen()

    async def run(runner, provider):
        return await runner._call_llm(
            provider=provider,
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
            agent=SimpleNamespace(name="rex"),
            assistant_msg=SimpleNamespace(id=f"msg_{runner.session.id}"),
        )

    first = asyncio.create_task(run(SessionRunner(session=_session("s1"), provider_id="prov", model_id="m"), _SlowProvider()))
    await first_started.wait()
    second = asyncio.create_task(run(SessionRunner(session=_session("s2"), provider_id="prov", model_id="m"), _FastProvider()))
    await asyncio.sleep(0.05)
    assert order == ["first-start"]
    assert get_provider_limiter("prov").snapshot()["waiting"] == 1

    release_first.set()
    await asyncio.gather(first, second)
    assert order == ["first-start", "second-start"]


@pytest.mark.asyncio
async def test_process_step_on_429_publishes_retry_status_and_cools_down_provider(monkeypatch):
    runner = SessionRunner(
        session=_session(),
        provider_id="prov",
        model_id="m",
        defer_step_errors=True,
        failover_available=False,
    )
    published: list[tuple[str, dict]] = []

    async def _publish(event_type, properties):
        published.append((event_type, properties))

    runner.callbacks = RunnerCallbacks(event_publish_callback=_publish)
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True

    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(status_code=429, headers={"retry-after": "3"})

    failure = _RateLimited("429 Too Many Requests")
    call_llm = AsyncMock(side_effect=failure)
    sleeps: list[int] = []

    async def _sleep(ms, _abort):
        sleeps.append(ms)

    cooldowns: list[tuple[str, float, str]] = []
    monkeypatch.setattr(
        runner_mod,
        "report_provider_rate_limited",
        lambda provider_id, cooldown_s, reason="rate_limit": cooldowns.append((provider_id, cooldown_s, reason)) or cooldown_s,
    )
    monkeypatch.setattr(
        "flocks.session.runner.Agent.get",
        AsyncMock(return_value=SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])),
    )
    monkeypatch.setattr("flocks.session.runner.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runner.Provider.apply_config", AsyncMock())
    monkeypatch.setattr("flocks.session.runner.SessionPrompt.build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_to_chat_messages", AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]))
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=SimpleNamespace(id="msg_assistant")))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", call_llm)
    monkeypatch.setattr("flocks.session.runner.SessionRetry.sleep", _sleep)

    before = int(time.time() * 1000)
    result = await runner._process_step([last_user], last_user)

    assert result.failure is not None
    assert result.failure.reason == "rate_limit"
    assert call_llm.await_count == 6  # 1 + MAX_ERROR_RETRIES

    # retry-after: 3s is honoured (plus up to 25 % jitter) on every attempt.
    assert len(sleeps) == 5
    assert all(3000 <= ms <= 3750 for ms in sleeps), sleeps
    # Each 429 pauses every other caller of the provider for the same wait —
    # all six attempts, including the last one that has no retry budget left
    # and therefore never sleeps itself.
    assert [c[0] for c in cooldowns] == ["prov"] * 6
    assert [c[2] for c in cooldowns] == ["http_429"] * 6
    assert all(abs(c[1] * 1000 - ms) < 1.0 for c, ms in zip(cooldowns, sleeps))

    statuses = [props["status"] for evt, props in published if evt == "session.status"]
    retries = [s for s in statuses if s["type"] == "retry"]
    assert [s["attempt"] for s in retries] == [1, 2, 3, 4, 5]
    assert all(s["maxAttempts"] == 5 for s in retries)
    assert all(s["rateLimited"] is True for s in retries)
    assert all(s["providerID"] == "prov" and s["modelID"] == "m" for s in retries)
    assert all(s["next"] >= before for s in retries), "next must be an epoch timestamp"
    # After every sleep the client is told the next attempt is in flight.
    assert [s["type"] for s in statuses] == ["retry", "busy"] * 5


@pytest.mark.asyncio
async def test_last_429_cools_down_the_provider_even_with_no_retry_budget(monkeypatch):
    """The 429 that exhausts our budget is exactly when others must back off."""
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m", defer_step_errors=True)
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True

    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(status_code=429, headers={"retry-after": "1"})

    cooldowns: list[tuple[str, float, str]] = []
    monkeypatch.setattr(
        runner_mod,
        "report_provider_rate_limited",
        lambda provider_id, cooldown_s, reason="rate_limit": cooldowns.append((provider_id, cooldown_s, reason)),
    )
    monkeypatch.setattr(
        "flocks.session.runner.Agent.get",
        AsyncMock(return_value=SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])),
    )
    monkeypatch.setattr("flocks.session.runner.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runner.Provider.apply_config", AsyncMock())
    monkeypatch.setattr("flocks.session.runner.SessionPrompt.build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_to_chat_messages", AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]))
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=SimpleNamespace(id="msg_assistant")))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", AsyncMock(side_effect=_RateLimited("429 Too Many Requests")))
    monkeypatch.setattr("flocks.session.runner.SessionRetry.sleep", AsyncMock())

    await runner._process_step([last_user], last_user)

    # 1 initial attempt + MAX_ERROR_RETRIES, and every one of them cools the
    # provider down — including the final attempt that has no budget left.
    assert len(cooldowns) == 6, cooldowns
    assert {c[0] for c in cooldowns} == {"prov"}
    assert {c[2] for c in cooldowns} == {"http_429"}


@pytest.mark.asyncio
async def test_retry_status_carries_the_same_fields_on_both_channels(monkeypatch):
    """SSE and /api/session/status must not disagree after a refresh."""
    from flocks.session.core.status import SessionStatus

    runner = SessionRunner(session=_session("ses_fields"), provider_id="prov", model_id="m", defer_step_errors=True)
    published: list[dict] = []

    async def _publish(event_type, properties):
        if event_type == "session.status":
            published.append(properties["status"])

    runner.callbacks = RunnerCallbacks(event_publish_callback=_publish)
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True

    class _RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(status_code=429, headers={"retry-after": "1"})

    stored: list = []
    real_set = SessionStatus.set
    monkeypatch.setattr(
        SessionStatus,
        "set",
        classmethod(lambda cls, sid, status: (stored.append(status), real_set(sid, status))[1]),
    )
    monkeypatch.setattr(runner_mod, "report_provider_rate_limited", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        "flocks.session.runner.Agent.get",
        AsyncMock(return_value=SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])),
    )
    monkeypatch.setattr("flocks.session.runner.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runner.Provider.apply_config", AsyncMock())
    monkeypatch.setattr("flocks.session.runner.SessionPrompt.build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_to_chat_messages", AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]))
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=SimpleNamespace(id="msg_assistant")))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", AsyncMock(side_effect=_RateLimited("429 Too Many Requests")))
    monkeypatch.setattr("flocks.session.runner.SessionRetry.sleep", AsyncMock())

    await runner._process_step([last_user], last_user)

    retry_events = [s for s in published if s["type"] == "retry"]
    retry_stored = [s for s in stored if getattr(s, "type", None) == "retry"]
    assert retry_events and len(retry_events) == len(retry_stored)
    for event, status in zip(retry_events, retry_stored):
        # The poll serves model_dump() of the stored object; the SSE payload
        # must be exactly that, field for field.
        assert event == status.model_dump(mode="json")
        assert event["maxAttempts"] == 5
        assert event["rateLimited"] is True
        assert event["providerID"] == "prov"
        assert event["modelID"] == "m"


@pytest.mark.asyncio
async def test_process_step_jitters_plain_backoff(monkeypatch):
    runner = SessionRunner(session=_session(), provider_id="prov", model_id="m", defer_step_errors=True)
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True
    failure = RuntimeError("Provider HTTP 500")
    failure.status_code = 500
    sleeps: list[int] = []

    async def _sleep(ms, _abort):
        sleeps.append(ms)

    monkeypatch.setattr(
        "flocks.session.runner.Agent.get",
        AsyncMock(return_value=SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])),
    )
    monkeypatch.setattr("flocks.session.runner.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runner.Provider.apply_config", AsyncMock())
    monkeypatch.setattr("flocks.session.runner.SessionPrompt.build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_to_chat_messages", AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]))
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=SimpleNamespace(id="msg_assistant")))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", AsyncMock(side_effect=failure))
    monkeypatch.setattr("flocks.session.runner.SessionRetry.sleep", _sleep)
    cooldowns: list = []
    monkeypatch.setattr(runner_mod, "report_provider_rate_limited", lambda *a, **k: cooldowns.append(a))

    await runner._process_step([last_user], last_user)

    schedule = [2000, 4000, 8000, 16000, RETRY_MAX_DELAY_NO_HEADERS]
    assert len(sleeps) == 5
    for ms, base in zip(sleeps, schedule):
        assert base // 2 <= ms <= base, (ms, base)
    # A 500 is not a rate limit: no provider-wide cooldown.
    assert cooldowns == []


@pytest.mark.asyncio
async def test_llm_priority_follows_root_session_category(monkeypatch):
    sessions = {
        "root_task": _session("root_task", category="task"),
        "child_of_task": _session("child_of_task", category="task", parent_id="root_task"),
        "root_user": _session("root_user", category="user"),
        "subagent_of_user": _session("subagent_of_user", category="task", parent_id="root_user"),
    }

    async def _get(session_id):
        return sessions.get(session_id)

    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(side_effect=_get))

    assert await SessionRunner(session=sessions["root_user"])._resolve_llm_priority() == PRIORITY_INTERACTIVE
    assert await SessionRunner(session=sessions["subagent_of_user"])._resolve_llm_priority() == PRIORITY_INTERACTIVE
    assert await SessionRunner(session=sessions["root_task"])._resolve_llm_priority() == PRIORITY_BACKGROUND
    assert await SessionRunner(session=sessions["child_of_task"])._resolve_llm_priority() == PRIORITY_BACKGROUND

    # Cached per runner: a later lookup failure does not flip the answer.
    runner = SessionRunner(session=sessions["child_of_task"])
    assert await runner._resolve_llm_priority() == PRIORITY_BACKGROUND
    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(side_effect=RuntimeError("db down")))
    assert await runner._resolve_llm_priority() == PRIORITY_BACKGROUND
    # A fresh runner that cannot resolve its lineage defaults to interactive.
    assert await SessionRunner(session=sessions["child_of_task"])._resolve_llm_priority() == PRIORITY_INTERACTIVE
