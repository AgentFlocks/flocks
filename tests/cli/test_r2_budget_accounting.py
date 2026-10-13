"""T01: cumulative provider usage must be charged as cumulative cost deltas."""
from unittest.mock import AsyncMock

import pytest

from flocks.cli.headless import ExecOptions, run_headless
from flocks.provider.provider import Provider, StreamChunk
from flocks.provider.types import PriceConfig
from flocks.session.runtime_controls import RuntimeControls, runtime_controls
from tests.cli.test_exec_command import runtime  # noqa: F401
from tests.cli.test_review_fixes import provider_runtime  # noqa: F401


@pytest.fixture
async def budget_provider(provider_runtime, monkeypatch):
    import flocks.provider.usage_service as usage

    class ControlledProvider:
        _config_models = []

        def __init__(self):
            self.streams = []
            self.requests = 0

        def is_configured(self):
            return True

        async def chat_stream(self, **kwargs):
            events = self.streams[self.requests]
            self.requests += 1
            for event in events:
                if isinstance(event, Exception):
                    raise event
                yield event

    provider = ControlledProvider()
    price = PriceConfig(input=1, output=0, cache_read=.1, cache_write=2, unit=1, currency="USD")
    monkeypatch.setattr(Provider, "get", lambda _: provider)
    monkeypatch.setattr(usage, "resolve_usage_pricing", lambda *_: price)
    directory, tool = provider_runtime
    return directory, tool, provider


def read_call():
    return [{"index": 0, "id": "call_budget", "type": "function",
             "function": {"name": "read", "arguments": "{}"}}]


@pytest.mark.parametrize("shape", ["full", "repeated", "partial", "cache_write"])
async def test_late_cache_snapshot_does_not_falsely_exceed_budget(budget_provider, shape):
    directory, tool, provider = budget_provider
    initial = {"prompt_tokens": 100, "completion_tokens": 0, "cache_read_input_tokens": 0}
    final = {"prompt_tokens": 100, "completion_tokens": 2, "cache_read_input_tokens": 90}
    snapshots = [initial, final]
    expected_cost = 19
    if shape == "repeated":
        snapshots = [initial, initial, final, final]
    elif shape in {"partial", "cache_write"}:
        snapshots = [initial, {"cache_read_input_tokens": 90}, {"completion_tokens": 2},
                     {"prompt_tokens": 100, "completion_tokens": 2}]
        if shape == "cache_write":
            snapshots += [{"cache_creation_input_tokens": 3}, {"completion_tokens": 2}]
            expected_cost = 25
    provider.streams = [[*(StreamChunk(usage=snapshot) for snapshot in snapshots),
                         StreamChunk(delta="ok", finish_reason="stop")]]
    controls = RuntimeControls(max_budget=105)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=controls))

    assert result["success"], result
    assert result["result"] == "ok"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 2, "requests": 1,
                               "cost_by_currency": {"USD": expected_cost}}
    assert controls.total_cost == expected_cost
    assert controls.failure is None
    assert provider.requests == 1
    tool.execute.assert_not_called()


@pytest.mark.parametrize("continuation", ["tool", "retry"])
async def test_cost_snapshots_reset_per_request_and_retry(budget_provider, monkeypatch, continuation):
    from flocks.session.runner import SessionRetry

    directory, tool, provider = budget_provider
    final = {"prompt_tokens": 100, "completion_tokens": 1, "cache_read_input_tokens": 90}
    first = [StreamChunk(usage={"prompt_tokens": 100}), StreamChunk(usage=final)]
    if continuation == "tool":
        first.append(StreamChunk(usage=final, tool_calls=read_call(), finish_reason="tool_calls"))
    else:
        first.append(RuntimeError("synthetic accounting retry"))
        monkeypatch.setattr(SessionRetry, "retryable", lambda error: "retry" if "synthetic" in str(error) else None)
        monkeypatch.setattr(SessionRetry, "sleep", AsyncMock())
    provider.streams = [first, [
        StreamChunk(usage={"prompt_tokens": 50, "completion_tokens": 2}),
        StreamChunk(usage={"cache_read_input_tokens": 40}),
        StreamChunk(usage={"prompt_tokens": 50, "completion_tokens": 2, "cache_read_input_tokens": 40},
                    delta="ok", finish_reason="stop"),
    ]]
    controls = RuntimeControls(max_budget=105)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=controls))

    assert result["success"], result
    assert result["result"] == "ok"
    assert result["usage"] == {"input_tokens": 150, "output_tokens": 3, "requests": 2,
                               "cost_by_currency": {"USD": 33}}
    assert controls.total_cost == 33
    assert provider.requests == 2
    assert tool.execute.await_count == (1 if continuation == "tool" else 0)


@pytest.mark.parametrize("timing", ["same_chunk", "next_chunk"])
async def test_reaching_exact_budget_blocks_tool_before_execution(budget_provider, timing):
    directory, tool, provider = budget_provider
    final = {"prompt_tokens": 100, "completion_tokens": 0, "cache_read_input_tokens": 90}
    events = [StreamChunk(usage={"prompt_tokens": 10})]
    if timing == "same_chunk":
        events.append(StreamChunk(usage=final, tool_calls=read_call(), finish_reason="tool_calls"))
    else:
        events += [StreamChunk(usage=final), StreamChunk(tool_calls=read_call(), finish_reason="tool_calls")]
    provider.streams = [events]
    controls = RuntimeControls(max_budget=19)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=controls))

    assert not result["success"], result
    assert "Maximum budget reached" in result["error"]
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 0, "requests": 1,
                               "cost_by_currency": {"USD": 19}}
    assert controls.failure is not None
    assert provider.requests == 1
    tool.execute.assert_not_called()


async def test_budget_failure_stays_sticky_after_cache_correction(budget_provider):
    directory, tool, provider = budget_provider
    provider.streams = [[
        StreamChunk(usage={"prompt_tokens": 100}),
        StreamChunk(usage={"prompt_tokens": 100, "cache_read_input_tokens": 90},
                    tool_calls=read_call(), finish_reason="tool_calls"),
    ]]
    controls = RuntimeControls(max_budget=100)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=controls))

    assert not result["success"], result
    assert "Maximum budget reached" in result["error"]
    assert controls.total_cost == 19
    assert result["usage"]["cost_by_currency"] == {"USD": 19}
    assert result["usage"]["requests"] == provider.requests == 1
    assert controls.failure is not None
    with pytest.raises(RuntimeError, match="Maximum budget reached"):
        controls.check_budget()
    tool.execute.assert_not_called()


async def test_corrected_known_usage_survives_nonretryable_stream_failure(budget_provider):
    directory, tool, provider = budget_provider
    provider.streams = [[
        StreamChunk(usage={"prompt_tokens": 100, "completion_tokens": 2}),
        StreamChunk(usage={"cache_read_input_tokens": 90}),
        ValueError("synthetic accounting terminal error"),
    ]]
    controls = RuntimeControls(max_budget=105)
    result = await run_headless(ExecOptions(directory, prompt="hello", controls=controls))

    assert not result["success"], result
    assert "synthetic accounting terminal error" in result["error"]
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 2, "requests": 1,
                               "cost_by_currency": {"USD": 19}}
    assert controls.total_cost == 19
    assert controls.failure is None
    assert provider.requests == 1
    tool.execute.assert_not_called()


async def test_ordinary_loop_without_controls_does_not_account_budget(budget_provider, monkeypatch):
    from flocks.project.instance import Instance
    from flocks.session.message import Message, MessageRole
    from flocks.session.session import Session
    from flocks.session.session_loop import SessionLoop

    directory, tool, provider = budget_provider
    provider.streams = [[
        StreamChunk(usage={"prompt_tokens": 100}),
        StreamChunk(usage={"prompt_tokens": 100, "cache_read_input_tokens": 90},
                    delta="ordinary", finish_reason="stop"),
    ]]
    session = await Session.create(project_id="default", directory=str(directory), agent="rex")
    await Message.create(session.id, MessageRole.USER, "hello")
    assert runtime_controls.get() is None

    def unexpected_accounting(*args, **kwargs):
        raise AssertionError("ordinary runner must not invoke runtime budget accounting")

    monkeypatch.setattr(RuntimeControls, "record_usage", unexpected_accounting)
    outcome = await Instance.provide(str(directory), fn=lambda: SessionLoop.run(
        session_id=session.id, provider_id="test", model_id="model", agent_name="rex",
        working_directory=str(directory),
    ))

    assert outcome.action == "stop" and not outcome.error, outcome
    assert outcome.last_message is not None and not outcome.last_message.error
    parts = await Message.parts(outcome.last_message.id, session_id=session.id)
    assert [part.text for part in parts if part.type == "text" and part.text and not part.ignored] == ["ordinary"]
    assert provider.requests == 1
    assert runtime_controls.get() is None
    tool.execute.assert_not_called()
