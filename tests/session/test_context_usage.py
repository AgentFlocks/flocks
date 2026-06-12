from types import SimpleNamespace

import pytest

from flocks.session import context_usage


def _message(
    message_id: str,
    *,
    role: str = "assistant",
    created: int = 100,
    tokens=None,
    provider_id: str = "openai",
    model_id: str = "gpt-4.1",
    finish: str | None = "stop",
    summary=None,
    compacted=None,
):
    return SimpleNamespace(
        id=message_id,
        role=role,
        time=SimpleNamespace(created=created),
        tokens=tokens,
        providerID=provider_id,
        modelID=model_id,
        finish=finish,
        summary=summary,
        compacted=compacted,
    )


@pytest.fixture
def context_usage_mocks(monkeypatch):
    state = {
        "active": [],
        "all": [],
        "estimate": 0,
        "system_prompt": 0,
        "parts": {},
    }

    async def fake_list(session_id: str, include_archived: bool = False):
        return list(state["all"] if include_archived else state["active"])

    async def fake_parts(message_id: str, session_id: str | None = None):
        return list(state["parts"].get(message_id, []))

    async def fake_estimate(session_id: str, messages: list):
        return state["estimate"]

    async def fake_system_prompt_tokens(*args, **kwargs):
        return state["system_prompt"]

    monkeypatch.setattr(context_usage.Message, "list", fake_list)
    monkeypatch.setattr(context_usage.Message, "parts", fake_parts)
    monkeypatch.setattr(
        context_usage.SessionPrompt,
        "estimate_full_context_tokens",
        fake_estimate,
    )
    monkeypatch.setattr(
        context_usage.Provider,
        "resolve_model_info",
        lambda provider_id, model_id: (200, 50, None),
    )
    monkeypatch.setattr(
        context_usage,
        "_estimate_system_prompt_tokens",
        fake_system_prompt_tokens,
    )
    return state


@pytest.mark.asyncio
async def test_context_usage_prefers_fresh_observed_tokens(context_usage_mocks):
    msg = _message(
        "assistant-1",
        tokens={
            "input": 90,
            "output": 20,
            "reasoning": 5,
            "cache": {"read": 10, "write": 0},
        },
    )
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 60
    context_usage_mocks["system_prompt"] = 30
    context_usage_mocks["parts"] = {
        "assistant-1": [
            SimpleNamespace(type="text", text="c" * 160),
        ]
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 125
    assert snapshot.observed_tokens == 125
    assert snapshot.estimated_tokens == 90
    assert snapshot.source == "observed"
    assert snapshot.percent == 63
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("systemPrompt", 30),
        ("conversation", 40),
        ("otherContext", 55),
    ]
    assert sum(segment.tokens for segment in snapshot.segments) == snapshot.used_tokens


@pytest.mark.asyncio
async def test_context_usage_falls_back_to_estimate_without_provider_tokens(context_usage_mocks):
    msg = _message("assistant-1", tokens=None)
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 80

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 80
    assert snapshot.observed_tokens is None
    assert snapshot.source == "estimated"
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("otherContext", 80),
    ]


@pytest.mark.asyncio
async def test_context_usage_ignores_observed_tokens_after_later_summary(context_usage_mocks):
    observed = _message(
        "assistant-1",
        created=100,
        tokens={"input": 190, "output": 20, "cache": {"read": 0, "write": 0}},
    )
    summary = _message(
        "summary-1",
        created=200,
        tokens=None,
        finish="summary",
        summary={"tokens": 40},
    )
    context_usage_mocks["active"] = [observed]
    context_usage_mocks["all"] = [observed, summary]
    context_usage_mocks["estimate"] = 40

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 40
    assert snapshot.observed_tokens is None
    assert snapshot.source == "estimated"
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("otherContext", 40),
    ]


@pytest.mark.asyncio
async def test_context_usage_splits_tool_parts_from_conversation(context_usage_mocks):
    msg = _message("assistant-1", tokens=None)
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 30
    context_usage_mocks["parts"] = {
        "assistant-1": [
            SimpleNamespace(
                type="tool",
                state=SimpleNamespace(
                    input={},
                    output="b" * 120,
                    time={"start": 1, "end": 2},
                ),
            )
        ]
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 30
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("tools", 30),
    ]
    assert sum(segment.tokens for segment in snapshot.segments) == 30


@pytest.mark.asyncio
async def test_context_usage_splits_skill_and_delegation_tools(context_usage_mocks):
    msg = _message("assistant-1", tokens=None)
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 110
    context_usage_mocks["parts"] = {
        "assistant-1": [
            SimpleNamespace(
                type="tool",
                tool="read",
                state=SimpleNamespace(input={}, output="r" * 120, time={"start": 1}),
            ),
            SimpleNamespace(
                type="tool",
                tool="skill_load",
                state=SimpleNamespace(input={}, output="s" * 80, time={"start": 2}),
            ),
            SimpleNamespace(
                type="tool",
                tool="task",
                state=SimpleNamespace(input={}, output="t" * 80, time={"start": 3}),
            ),
            SimpleNamespace(
                type="tool",
                tool="delegate_task",
                state=SimpleNamespace(input={}, output="d" * 40, time={"start": 4}),
            ),
            SimpleNamespace(
                type="tool",
                metadata={"tool": "skill_load"},
                state=SimpleNamespace(input={}, output="m" * 40, time={"start": 5}),
            ),
            SimpleNamespace(
                type="subtask",
                prompt="p" * 40,
                description="q" * 40,
            ),
        ]
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("tools", 30),
        ("skillLoad", 30),
        ("agentDelegation", 50),
    ]
    tools_segment = next(segment for segment in snapshot.segments if segment.key == "tools")
    assert tools_segment.tokens == 30
