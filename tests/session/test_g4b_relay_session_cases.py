"""Session message / streaming / abort cases from the 2026-09-14 execution, G4b relay batch.

Each test names the case ids it covers (SES-xxxx).  The runner harness mirrors
``test_g2a_message_cases.py`` (real ``SessionRunner._call_llm`` / ``_process_step``
with a fake provider stream) and the loop harness mirrors
``test_session_abort_inject.py``.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import flocks.session.runner as runner_mod
import flocks.session.session_loop as loop_mod
from flocks.provider.provider import ChatMessage
from flocks.session.core.status import SessionStatusRetry
from flocks.session.lifecycle.compaction.compaction import SessionCompaction
from flocks.session.lifecycle.compaction.policy import CompactionPolicy
from flocks.session.lifecycle.retry import MAX_ERROR_RETRIES, SessionRetry
from flocks.session.message import UserMessageInfo
from flocks.session.runner import RunnerCallbacks, SessionRunner, StepResult, ToolCall
from flocks.session.session import SessionInfo
from flocks.session.session_loop import LoopCallbacks, LoopContext, SessionLoop
from flocks.session.streaming.timeouts import resolve_llm_stream_timeouts

from tests.session import test_g2a_message_cases as g2a
from tests.session.test_g2a_message_cases import runner_env  # noqa: F401  (pytest fixture re-export)
from tests.session.test_stream_timeouts import _provider as _timeout_provider


# ---------------------------------------------------------------------------
# _process_step harness (copied from test_runner_step.py::max_retries test)
# ---------------------------------------------------------------------------

def _make_runner(session_id: str) -> SessionRunner:
    session = SessionInfo.model_construct(id=session_id, slug="t", project_id="proj", directory="/tmp", title="t")
    return SessionRunner(session=session)


def _last_user(runner: SessionRunner) -> UserMessageInfo:
    return UserMessageInfo(
        id=f"msg_user_{runner.session.id}",
        sessionID=runner.session.id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )


def _install_step_env(monkeypatch: pytest.MonkeyPatch, runner: SessionRunner, *, call_llm, sleep=None):
    """Patch everything ``_process_step`` touches before/after the retry loop."""
    agent = SimpleNamespace(name="rex", steps=None, mode="primary", prompt="", tools=[])
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant_msg = SimpleNamespace(id=f"msg_assistant_{runner.session.id}")
    update_mock = AsyncMock(return_value=None)
    runner.callbacks = RunnerCallbacks(on_error=AsyncMock())
    monkeypatch.setattr(runner_mod.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(runner_mod.Provider, "get", lambda provider_id: provider)
    monkeypatch.setattr(runner_mod.Provider, "apply_config", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionPrompt, "build_system_prompts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_to_chat_messages", AsyncMock(return_value=[SimpleNamespace(role="user", content="hi")]))
    monkeypatch.setattr(runner_mod.Message, "get_text_content", AsyncMock(return_value="hi"))
    monkeypatch.setattr(runner_mod.Message, "create", AsyncMock(return_value=assistant_msg))
    monkeypatch.setattr(runner_mod.Message, "update", update_mock)
    monkeypatch.setattr(runner, "_record_usage_if_available", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "_call_llm", call_llm)
    if sleep is not None:
        monkeypatch.setattr(runner_mod.SessionRetry, "sleep", sleep)
    return assistant_msg, update_mock


def _rate_limit_error(headers=None, *, status_code=None):
    """A provider 429.  Without ``status_code`` the runner classifies it by message
    pattern ('Rate Limited'); with one it becomes an APIError whose own message is
    surfaced as the retry reason."""
    exc = Exception("429 Too Many Requests: rate limit exceeded")
    if status_code is not None:
        exc.status_code = status_code
    if headers is not None:
        exc.status_code = 429
        exc.response = MagicMock()
        exc.response.headers = headers
    return exc


# ---------------------------------------------------------------------------
# SES-0257 / SES-0258: 429 -> retry with backoff, MAX_ERROR_RETRIES then error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limited_provider_retries_five_times_then_fails(monkeypatch):
    """SES-0257: 'Rate Limited' retry status, 2s×2^n backoff capped at 30s, error after 5 retries."""
    runner = _make_runner("ses_g4b_429")
    calls = 0

    async def fake_call_llm(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _rate_limit_error()

    slept: list[int] = []

    async def fake_sleep(ms, _abort):
        slept.append(ms)

    statuses: list = []
    monkeypatch.setattr(runner_mod.SessionStatus, "set", lambda _sid, status: statuses.append(status))
    assistant_msg, update_mock = _install_step_env(monkeypatch, runner, call_llm=fake_call_llm, sleep=fake_sleep)

    result = await runner._process_step([_last_user(runner)], _last_user(runner))

    assert MAX_ERROR_RETRIES == 5
    assert calls == MAX_ERROR_RETRIES + 1  # first attempt + 5 retries
    assert slept == [2000, 4000, 8000, 16000, 30000]  # capped at RETRY_MAX_DELAY_NO_HEADERS
    retry_statuses = [s for s in statuses if isinstance(s, SessionStatusRetry)]
    assert [s.attempt for s in retry_statuses] == [1, 2, 3, 4, 5]
    # The runner normalises every rate-limit shaped exception into a retryable APIError,
    # and SessionRetry.retryable() surfaces the provider's own message as the retry reason.
    # The bare 'Rate Limited' label only appears for non-APIError dicts (see test_retry.py).
    assert all(s.message == "429 Too Many Requests: rate limit exceeded" for s in retry_statuses)
    assert SessionRetry.retryable({"name": "RuntimeError", "data": {"message": "429 rate limit"}}) == "Rate Limited"
    assert result.action == "stop" and "429" in (result.error or "")
    for shape in (_rate_limit_error(), _rate_limit_error(status_code=429)):
        api_error = runner._exception_to_error_dict(shape)
        assert api_error["name"] == "APIError" and api_error["data"]["isRetryable"] is True
    final = update_mock.await_args_list[-1]
    assert final.args[1] == assistant_msg.id and final.kwargs["finish"] == "error"


@pytest.mark.asyncio
async def test_retry_after_header_beats_exponential_backoff(monkeypatch):
    """SES-0258: retry-after: 7 -> 7000ms wait instead of the 2000ms first backoff step."""
    runner = _make_runner("ses_g4b_retry_after")
    attempts = 0

    async def fake_call_llm(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _rate_limit_error({"retry-after": "7"})
        return StepResult(action="stop", content="ok")

    slept: list[int] = []

    async def fake_sleep(ms, _abort):
        slept.append(ms)

    _install_step_env(monkeypatch, runner, call_llm=fake_call_llm, sleep=fake_sleep)

    result = await runner._process_step([_last_user(runner)], _last_user(runner))

    assert slept == [7000]
    assert result.action == "stop" and result.content == "ok"
    # priority: retry-after-ms > retry-after > backoff
    assert SessionRetry.delay(1, {"data": {"responseHeaders": {"retry-after-ms": "1500", "retry-after": "7"}}}) == 1500
    assert SessionRetry.delay(1, {"data": {"responseHeaders": {"retry-after": "7"}}}) == 7000
    assert SessionRetry.delay(1) == 2000


# ---------------------------------------------------------------------------
# SES-0259 / SES-0260: first-chunk timeout resolution and enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_chunk_timeout_is_enforced_and_retryable(monkeypatch):
    """SES-0259: a stalled first chunk raises a retryable 'timed out' error; defaults 120s / 1800s local."""
    monkeypatch.delenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", raising=False)
    assert resolve_llm_stream_timeouts(_timeout_provider(), "test-model").first_chunk_s == 120.0
    assert resolve_llm_stream_timeouts(_timeout_provider(base_url="http://127.0.0.1:11434/v1"), "test-model").first_chunk_s == 1800.0
    assert resolve_llm_stream_timeouts(_timeout_provider(base_url="http://10.0.0.5:8000/v1"), "test-model").first_chunk_s == 1800.0

    async def _stalled():
        await asyncio.sleep(5)
        yield SimpleNamespace(delta="late")

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError) as excinfo:
        async for _ in runner_mod._iter_with_chunk_timeout(_stalled(), first_chunk_timeout_s=0.05, ongoing_chunk_timeout_s=5):
            pass
    assert time.monotonic() - started < 2
    assert "timed out" in str(excinfo.value) and "waiting for first response" in str(excinfo.value)

    runner = _make_runner("ses_g4b_timeout")
    error_dict = runner._exception_to_error_dict(excinfo.value)
    assert error_dict["name"] == "APIError" and error_dict["data"]["isRetryable"] is True
    assert SessionRetry.retryable(error_dict) is not None
    # the classifier's pattern path also treats the message as retryable (the wording
    # "may be overloaded" matches the rate-limit patterns before the timeout ones)
    assert SessionRetry.retryable({"name": "TimeoutError", "data": {"message": str(excinfo.value)}}) in {"Rate Limited", "Connection or Server Error"}


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("abc", 120.0), ("-1", 120.0), ("30", 30.0)],
)
def test_env_override_for_first_chunk_timeout(monkeypatch, env_value, expected):
    """SES-0260: invalid env values fall back to 120; a valid one applies; model custom_settings wins."""
    monkeypatch.setenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", env_value)
    assert resolve_llm_stream_timeouts(_timeout_provider(), "test-model").first_chunk_s == expected
    with_model = _timeout_provider(model_settings={"stream_first_chunk_timeout_s": 45})
    assert resolve_llm_stream_timeouts(with_model, "test-model").first_chunk_s == 45.0


# ---------------------------------------------------------------------------
# SES-0261 / SES-0262: overflow detection threshold and compaction attempt budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overflow_threshold_boundary():
    """SES-0262: overflow means *exceeding* 85% of the context window; threshold-1 and threshold itself do not trigger."""
    policy = CompactionPolicy.from_model(context_window=8000, max_output_tokens=1024)
    assert policy.overflow_threshold == int(8000 * 0.85) == 6800

    async def _is_over(count: int) -> bool:
        return await SessionCompaction.is_overflow(
            tokens={"input": count, "output": 0, "cache": {"read": 0}}, model_context=8000, policy=policy,
        )

    assert await _is_over(policy.overflow_threshold - 1) is False
    assert await _is_over(policy.overflow_threshold) is False
    assert await _is_over(policy.overflow_threshold + 1) is True


def _loop_msg(msg_id: str, role: str, *, finish=None, tokens=None):
    msg = MagicMock()
    msg.id = msg_id
    msg.role = role
    msg.finish = finish
    msg.summary = False
    msg.tokens = tokens
    msg.parentID = None
    msg.time = SimpleNamespace(created=1_000)
    return msg


@pytest.mark.asyncio
async def test_overflow_compaction_gives_up_after_three_attempts():
    """SES-0261: overflow -> compaction (progress events published) x3, then session error 'compaction attempts'."""
    session = SimpleNamespace(id="ses_g4b_overflow", agent="rex", directory="/tmp", memory_enabled=False, project_id="proj")
    ctx = LoopContext(session=session, provider_id="test-provider", model_id="test-model", agent_name="rex")
    user = _loop_msg("msg_u", "user")
    # finish=tool-calls keeps the loop from exiting; the reported usage is far beyond 85% of an 8k window
    assistant = _loop_msg("msg_a", "assistant", finish="tool-calls", tokens={"input": 9000, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}})
    ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=[user, assistant]))
    events = AsyncMock()
    on_error = AsyncMock()
    callbacks = LoopCallbacks(event_publish_callback=events, on_error=on_error)

    compaction_calls = 0

    async def fake_run_compaction(*_args, progress_callback=None, **_kwargs):
        nonlocal compaction_calls
        compaction_calls += 1
        if progress_callback is not None:
            await progress_callback("summarizing", {"attempt": compaction_calls})
        return "continue"

    history = SimpleNamespace(summary_last_error=None, summary_cooldown_until=0)
    with patch.object(loop_mod.Provider, "resolve_model_info", return_value=(8000, 1024, None)), \
         patch.object(loop_mod.Message, "parts", AsyncMock(return_value=[])), \
         patch.object(loop_mod.Message, "get_text_content", MagicMock(return_value="working")), \
         patch.object(loop_mod.SessionPrompt, "estimate_tool_result_tokens", AsyncMock(return_value=0)), \
         patch.object(loop_mod.SessionPrompt, "estimate_full_context_tokens", AsyncMock(return_value=0)), \
         patch.object(loop_mod.SessionCompaction, "truncate_oversized_tool_outputs", AsyncMock(return_value=0)), \
         patch.object(loop_mod.SessionCompaction, "prune", AsyncMock(return_value=None)), \
         patch.object(loop_mod, "run_compaction", fake_run_compaction), \
         patch.object(loop_mod, "_get_compaction_history", lambda _sid: history), \
         patch.object(loop_mod, "set_context_state", lambda *a, **k: None), \
         patch.object(SessionRunner, "_process_step", AsyncMock(side_effect=AssertionError("the model must not be called while overflowing"))):
        result = await SessionLoop._run_loop(ctx, callbacks)

    assert compaction_calls == loop_mod.MAX_OVERFLOW_COMPACTION_ATTEMPTS == 3
    assert ctx.overflow_compaction_attempts == 3
    progress = [c.args[1] for c in events.await_args_list if c.args[0] == "session.compaction_progress"]
    assert [p["data"]["attempt"] for p in progress] == [1, 2, 3]
    on_error.assert_awaited_once()
    assert "3 compaction attempts" in on_error.await_args.args[0]
    assert result.action in {"stop", "error"}


# ---------------------------------------------------------------------------
# SES-0265 / SES-0266 / SES-0267: title fallback, tool-call rejection, transform
# ---------------------------------------------------------------------------

def _title_patches(question: str, provider):
    from tests.session.test_cli_title_generation import _make_session, _make_user_msg, _patch_title_deps

    msg, part = _make_user_msg(question)
    update = AsyncMock()
    return _patch_title_deps(_make_session(), [msg], [part], provider, update), update


@pytest.mark.asyncio
async def test_title_llm_failure_falls_back_to_first_line_truncated(monkeypatch):
    """SES-0265: provider error -> first line cut to 47 chars + '...' and title.llm_failed logged."""
    from flocks.session.lifecycle.title import SessionTitle, log as title_log

    first_line = "A" * 70
    provider = MagicMock()

    async def failing(*_a, **_k):
        raise ConnectionError("title provider down")
        yield  # pragma: no cover

    provider.chat_stream = failing
    warned = []
    monkeypatch.setattr(title_log, "warn", lambda name, payload=None: warned.append(name))
    patches, update = _title_patches(first_line + "\nsecond line", provider)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        title = await SessionTitle.generate_title_after_first_message(session_id="s", model_id="m", provider_id="p")

    assert title == "A" * 47 + "..." and len(title) == 50
    assert "title.llm_failed" in warned
    update.assert_awaited_once_with("proj-1", "s", title=title)


@pytest.mark.asyncio
async def test_title_tool_call_shape_is_rejected(monkeypatch):
    """SES-0266: '[TOOL_CALL] ...' from the title model is rejected (logged) and the simple title is used."""
    from flocks.session.lifecycle.title import SessionTitle, log as title_log

    provider = MagicMock()

    async def tool_shaped(*_a, **_k):
        yield SimpleNamespace(delta='[TOOL_CALL] {"name": "news"}')

    provider.chat_stream = tool_shaped
    warned = []
    monkeypatch.setattr(title_log, "warn", lambda name, payload=None: warned.append(name))
    question = "Give me the latest security news"
    patches, _ = _title_patches(question, provider)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        title = await SessionTitle.generate_title_after_first_message(session_id="s", model_id="m", provider_id="p")

    assert title == SessionTitle._generate_simple_title(question) == question
    assert "title.rejected_tool_call_candidate" in warned


class _ReplaceXWithY:
    name = "g4b-x-to-y"

    async def open_stream(self, metadata):
        raise AssertionError("title generation must use the whole-response path")

    async def transform_text(self, text, metadata):
        return text.replace("X", "Y")

    async def transform_value(self, value, metadata):
        return value


@pytest.mark.asyncio
async def test_title_goes_through_registered_response_transform():
    """SES-0267: the generated title is passed through transform_complete_response."""
    from flocks.session.lifecycle.title import SessionTitle
    from flocks.transform.response import register_response_transform, reset_response_transform

    provider = MagicMock()

    async def stream(*_a, **_k):
        yield SimpleNamespace(delta="Fix X pipeline")

    provider.chat_stream = stream
    patches, update = _title_patches("please fix the X pipeline", provider)
    register_response_transform(_ReplaceXWithY())
    try:
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(session_id="s", model_id="m", provider_id="p")
    finally:
        reset_response_transform()

    assert title == "Fix Y pipeline"
    update.assert_awaited_once_with("proj-1", "s", title="Fix Y pipeline")


# ---------------------------------------------------------------------------
# SES-0269: default goal turn budget is 20 and exhausting it pauses the goal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_default_budget_is_twenty_and_pauses_when_spent():
    """SES-0269: max_turns defaults to 20; the 20th 'continue' verdict pauses with 'turn budget exhausted (20/20)'."""
    from flocks.session.goal import DEFAULT_GOAL_MAX_TURNS, GoalManager

    assert DEFAULT_GOAL_MAX_TURNS == 20
    session_id = "ses_g4b_goal_budget"
    state = await GoalManager.set_goal(session_id, "keep going")
    assert state.max_turns == 20
    provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(
        content='{"verdict": "continue", "reason": "not done"}'
    )))
    with patch("flocks.session.goal.Provider.get", return_value=provider):
        for turn in range(1, 21):
            decision = await GoalManager.evaluate_after_turn(session_id, f"progress {turn}", provider_id="p", model_id="m")
            state = await GoalManager.get(session_id)
            if turn < 20:
                assert decision.should_continue is True and state.status == "active", (turn, state)
    assert decision.verdict == "continue" and decision.should_continue is False
    assert state.status == "paused" and state.paused_reason == "turn budget exhausted (20/20)"


# ---------------------------------------------------------------------------
# SES-0520: provider failure mid-stream drains parallel work before the retry layer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_failure_mid_stream_drains_before_raising(runner_env):
    """SES-0520: the except branch drains parallel tool calls first, then re-raises to the retry layer."""
    runner = g2a._runner("ses_g4b_stream_fail")

    async def on_chunk(index):
        if index == 1:
            raise RuntimeError("500 provider exploded")

    with pytest.raises(RuntimeError, match="500 provider exploded"):
        await g2a._call(runner, g2a._provider([g2a._chunk(delta="a"), g2a._chunk(delta="b"), g2a._chunk(finish_reason="stop")], on_chunk=on_chunk))
    proc = g2a._last_processor
    assert proc.drained >= 1
    assert proc.get_text_content() == "a"  # the delta received before the failure was processed, nothing after


@pytest.mark.asyncio
async def test_retry_is_suppressed_once_a_tool_started(monkeypatch):
    """SES-0520 (retry layer): tool_execution_started makes a retryable error final."""
    runner = _make_runner("ses_g4b_retry_suppressed")
    calls = 0

    async def fake_call_llm(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        runner._attempt_state.tool_execution_started = True
        raise _rate_limit_error()

    errors = []
    monkeypatch.setattr(runner_mod.log, "error", lambda name, payload=None: errors.append(name))
    sleep = AsyncMock()
    _install_step_env(monkeypatch, runner, call_llm=fake_call_llm, sleep=sleep)

    result = await runner._process_step([_last_user(runner)], _last_user(runner))

    assert calls == 1 and sleep.await_count == 0
    assert "runner.step.retry_suppressed" in errors
    assert result.action == "stop" and result.error


# ---------------------------------------------------------------------------
# SES-0521 / SES-0527: abort before the first token / during the retry sleep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_abort_before_first_token_returns_aborted_without_model_call(monkeypatch):
    """SES-0521: abort set before _call_llm -> StepResult(stop, 'Aborted'), no provider call, no assistant text."""
    runner = _make_runner("ses_g4b_abort_early")
    call_llm = AsyncMock(side_effect=AssertionError("provider must not be called"))
    _, update_mock = _install_step_env(monkeypatch, runner, call_llm=call_llm)
    runner.abort()

    result = await runner._process_step([_last_user(runner)], _last_user(runner))

    assert result.action == "stop" and result.error == "Aborted"
    call_llm.assert_not_awaited()
    assert not any(c.kwargs.get("finish") for c in update_mock.await_args_list)


@pytest.mark.asyncio
async def test_abort_during_retry_sleep_returns_immediately(monkeypatch):
    """SES-0527: SessionRetry.sleep wakes up on abort; no further attempt is made."""
    runner = _make_runner("ses_g4b_abort_sleep")
    calls = 0

    async def fake_call_llm(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _rate_limit_error()  # -> 2000ms backoff sleep on the real SessionRetry.sleep

    _install_step_env(monkeypatch, runner, call_llm=fake_call_llm)
    loop = asyncio.get_running_loop()
    loop.call_later(0.1, runner.abort)
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await runner._process_step([_last_user(runner)], _last_user(runner))
    assert time.monotonic() - started < 1.5  # far below the 2s backoff
    assert calls == 1


@pytest.mark.asyncio
async def test_loop_publishes_idle_after_cancelled_run():
    """SES-0527 (status): SessionLoop.run restores idle even when the loop is cancelled by an abort."""
    session_info = SessionInfo.model_construct(id="ses_g4b_idle", slug="t", project_id="proj", directory="/tmp", title="t")
    events = AsyncMock()
    with patch.object(loop_mod.Session, "get_by_id", AsyncMock(return_value=session_info)), \
         patch.object(loop_mod.Message, "list", AsyncMock(return_value=[])), \
         patch("flocks.session.orphan_tools.abort_orphan_running_parts", AsyncMock(return_value=0)), \
         patch.object(SessionLoop, "_run_loop", AsyncMock(side_effect=asyncio.CancelledError())), \
         patch.object(loop_mod.Session, "touch", AsyncMock()), \
         patch("flocks.bus.bus.Bus.publish", AsyncMock()):
        with pytest.raises(asyncio.CancelledError):
            await SessionLoop.run(session_id=session_info.id, provider_id="p", model_id="m", agent_name="rex", callbacks=LoopCallbacks(event_publish_callback=events))
    statuses = [c.args[1]["status"]["type"] for c in events.await_args_list if c.args[0] == "session.status"]
    assert statuses == ["busy", "idle"]


# ---------------------------------------------------------------------------
# SES-0548: tool calls finish the running assistant message as 'tool-calls'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_calls_mark_assistant_finish_tool_calls_and_continue(monkeypatch):
    """SES-0548: Message.update(finish='tool-calls') and the step asks the loop to continue."""
    runner = _make_runner("ses_g4b_tool_calls_finish")

    async def fake_call_llm(*_args, **_kwargs):
        return StepResult(action="continue", tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "x"})])

    assistant_msg, update_mock = _install_step_env(monkeypatch, runner, call_llm=fake_call_llm)

    result = await runner._process_step([_last_user(runner)], _last_user(runner))

    assert result.action == "continue" and [t.name for t in result.tool_calls] == ["read"]
    assert any(c.args[1] == assistant_msg.id and c.kwargs.get("finish") == "tool-calls" for c in update_mock.await_args_list)
    # the loop keeps going on 'tool-calls' (see test_session_abort_inject.py: should NOT exit on tool-calls)
    last_user = _loop_msg("msg_u", "user")
    last_assistant = _loop_msg("msg_a", "assistant", finish="tool-calls")
    last_assistant.parentID = last_user.id
    assert SessionLoop._should_exit(last_user, last_assistant, []) is False


# ---------------------------------------------------------------------------
# SES-0555: legacy streamTextReplacements without a registered transform
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_replacements_rewrite_text_and_reasoning(runner_env, monkeypatch):
    """SES-0555: hook-provided replacements go through _LegacyBufferStream; split placeholders are restored and the tail flushed."""
    from flocks.session.response_rewriter import _LegacyBufferStream, SessionResponseRewriter

    runner = g2a._runner("ses_g4b_legacy_replacements")

    async def _before(payload):
        return SimpleNamespace(output={
            "request": {**payload["request"], "providerOptions": {}},
            "redaction": {"streamTextReplacements": [{"placeholder": "[[V_EMAIL_1]]", "value": "alice@example.com"}]},
        })

    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(side_effect=lambda stage, _m=None: stage == runner_mod.HookStage.LLM_BEFORE))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_llm_before", AsyncMock(side_effect=_before))
    created = []
    real_create = SessionResponseRewriter.create

    async def spy_create(*args, **kwargs):
        inst = await real_create(*args, **kwargs)
        created.append(inst)
        return inst

    monkeypatch.setattr(runner_mod.SessionResponseRewriter, "create", spy_create)

    chunks = [
        g2a._chunk(reasoning="mail [[V_EMAIL_1]] first"),
        g2a._chunk(delta="contact [[V_EM"),
        g2a._chunk(delta="AIL_1]] now; tail [[V_"),  # partial marker left at the end of the stream
        g2a._chunk(finish_reason="stop"),
    ]
    result = await g2a._call(runner, g2a._provider(chunks))

    assert created and isinstance(created[0]._text_stream, _LegacyBufferStream)
    assert g2a._last_processor.get_reasoning_content() == "mail alice@example.com first"
    assert result.content == "contact alice@example.com now; tail [[V_"
    assert "[[V_EMAIL_1]]" not in result.content
