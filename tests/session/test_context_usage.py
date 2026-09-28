from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.session import context_usage
from flocks.session.prompt import SystemPromptBlock, TurnPromptContext


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
        "tool_definitions": 0,
        "parts": {},
        "list_calls": [],
    }

    async def fake_list(session_id: str, include_archived: bool = False):
        state["list_calls"].append(include_archived)
        return list(state["all"] if include_archived else state["active"])

    async def fake_parts(message_id: str, session_id: str | None = None):
        return list(state["parts"].get(message_id, []))

    async def fake_estimate(session_id: str, messages: list):
        return state["estimate"] if messages else 0

    async def fake_system_prompt_tokens(*args, **kwargs):
        return state["system_prompt"]

    async def fake_tool_definition_tokens(*args, **kwargs):
        return state["tool_definitions"], ()

    context_usage.reset_observation_baselines()
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
    monkeypatch.setattr(
        context_usage,
        "_estimate_tool_definition_tokens",
        fake_tool_definition_tokens,
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
    context_usage_mocks["tool_definitions"] = 20
    context_usage_mocks["parts"] = {
        "assistant-1": [
            SimpleNamespace(type="text", text="c" * 160),
            SimpleNamespace(type="reasoning", text="r" * 80),
        ]
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 125
    assert snapshot.observed_tokens == 125
    assert snapshot.estimated_tokens == 110
    assert snapshot.source == "observed"
    assert snapshot.percent == 63
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("systemPrompt", 30),
        ("toolDefinitions", 20),
        ("conversation", 55),
        ("reasoning", 20),
        ("agentDelegation", 0),
    ]
    assert sum(segment.tokens for segment in snapshot.segments) == snapshot.used_tokens


@pytest.mark.asyncio
async def test_context_usage_follows_observed_total_when_estimate_is_higher(context_usage_mocks):
    """The meter must match the compaction trigger, which uses the observed
    figure; history reasoning the adapter never re-sends inflates the
    estimate, and ``max`` showed a near-full meter that never compacted."""
    msg = _message(
        "assistant-1",
        tokens={"input": 90, "output": 10, "cache": {"read": 0, "write": 0}},
    )
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 150
    context_usage_mocks["system_prompt"] = 30
    context_usage_mocks["tool_definitions"] = 20

    snapshot = await context_usage.build_context_usage_snapshot("sess-high-estimate")

    assert snapshot.estimated_tokens == 200
    assert snapshot.used_tokens == 100
    assert snapshot.source == "observed"
    assert snapshot.percent == 50


@pytest.mark.asyncio
async def test_context_usage_counts_cache_writes_as_prompt(context_usage_mocks, monkeypatch):
    """Anthropic reports a cold-cache prompt almost entirely as cache
    writes; counting only ``input + cache.read`` put the meter near 0%."""
    msg = _message(
        "assistant-1",
        tokens={"input": 3, "output": 400, "cache": {"read": 0, "write": 58_000}},
    )
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 20
    monkeypatch.setattr(
        context_usage.Provider,
        "resolve_model_info",
        lambda provider_id, model_id: (200_000, 8_000, None),
    )

    snapshot = await context_usage.build_context_usage_snapshot("sess-cold-cache")

    assert snapshot.used_tokens == 58_403
    assert snapshot.observed_tokens == 58_403
    assert snapshot.source == "observed"


@pytest.mark.asyncio
async def test_context_usage_never_drops_below_fixed_costs_after_cleanup(
    context_usage_mocks,
    monkeypatch,
):
    """A cleanup estimate that over-counts (history reasoning the adapter
    never re-sent) must not eat the system prompt and tool definitions."""
    user = _message("user-1", role="user", created=50, tokens=None, finish=None)
    observed = _message(
        "assistant-1",
        created=100,
        tokens={"input": 150, "output": 10, "cache": {"read": 0, "write": 0}},
    )
    context_usage_mocks["active"] = [user, observed]
    context_usage_mocks["all"] = [user, observed]
    context_usage_mocks["system_prompt"] = 60
    context_usage_mocks["tool_definitions"] = 40
    prefix_tokens = {"value": 400}  # estimate says the prefix is far bigger than real

    async def fake_estimate(session_id: str, messages: list):
        if not messages:
            return 0
        return prefix_tokens["value"] if messages == [user] else prefix_tokens["value"] + 10

    monkeypatch.setattr(
        context_usage.SessionPrompt,
        "estimate_full_context_tokens",
        fake_estimate,
    )

    await context_usage.build_context_usage_snapshot("sess-over-freed")  # baseline 400
    prefix_tokens["value"] = 0  # the whole prefix was archived
    snapshot = await context_usage.build_context_usage_snapshot("sess-over-freed")

    # 160 observed - 400 "freed" would be negative; fixed costs stay.
    assert snapshot.used_tokens == 100


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
        ("conversation", 80),
        ("agentDelegation", 0),
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
    context_usage_mocks["active"] = [observed, summary]
    context_usage_mocks["all"] = [observed, summary]
    context_usage_mocks["estimate"] = 40

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 40
    assert snapshot.observed_tokens is None
    assert snapshot.source == "estimated"
    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("conversation", 40),
        ("agentDelegation", 0),
    ]


@pytest.mark.asyncio
async def test_context_usage_subtracts_tokens_cleaned_up_after_observation(
    context_usage_mocks,
    monkeypatch,
):
    """Cleanup after the observed call must lower the meter.

    The same figure drives the compaction trigger; before, a stale observed
    value kept an already-pruned prompt looking full.
    """
    user = _message("user-1", role="user", created=50, tokens=None, finish=None)
    observed = _message(
        "assistant-1",
        created=100,
        tokens={"input": 150, "output": 10, "cache": {"read": 0, "write": 0}},
    )
    context_usage_mocks["active"] = [user, observed]
    context_usage_mocks["all"] = [user, observed]
    prefix_tokens = {"value": 120}

    async def fake_estimate(session_id: str, messages: list):
        if not messages:
            return 0
        # Only the part before the observed call shrinks when pruned.
        return prefix_tokens["value"] if messages == [user] else prefix_tokens["value"] + 10

    monkeypatch.setattr(
        context_usage.SessionPrompt,
        "estimate_full_context_tokens",
        fake_estimate,
    )

    before = await context_usage.build_context_usage_snapshot("sess-cleanup")
    prefix_tokens["value"] = 40  # a prune replaced tool output with placeholders
    after = await context_usage.build_context_usage_snapshot("sess-cleanup")

    assert before.used_tokens == 160
    assert after.used_tokens == 80
    assert after.observed_tokens == 160
    assert after.source == "observed"


@pytest.mark.asyncio
async def test_context_usage_adjusts_observation_across_a_summary_when_baseline_known(
    context_usage_mocks,
):
    observed = _message(
        "assistant-1",
        created=100,
        tokens={"input": 190, "output": 20, "cache": {"read": 0, "write": 0}},
    )
    context_usage_mocks["active"] = [observed]
    context_usage_mocks["all"] = [observed]
    context_usage_mocks["estimate"] = 40
    await context_usage.build_context_usage_snapshot("sess-summary")  # baseline 0

    summary = _message(
        "summary-1",
        created=200,
        tokens=None,
        finish="summary",
        summary={"tokens": 40},
    )
    context_usage_mocks["active"] = [observed, summary]
    snapshot = await context_usage.build_context_usage_snapshot("sess-summary")

    assert snapshot.used_tokens == 250  # 210 observed + 40 for the summary
    assert snapshot.source == "observed"


@pytest.mark.asyncio
async def test_context_usage_reports_recent_user_message_ids(context_usage_mocks):
    messages = [
        _message(f"user-{index:02d}", role="user", tokens=None, finish=None)
        for index in range(25)
    ]
    context_usage_mocks["active"] = messages
    context_usage_mocks["all"] = messages

    snapshot = await context_usage.build_context_usage_snapshot("sess-users")
    payload = snapshot.model_dump(by_alias=True)

    assert payload["recentUserMessageIDs"] == [f"user-{index:02d}" for index in range(5, 25)]


@pytest.mark.asyncio
async def test_context_usage_does_not_scan_archived_history(context_usage_mocks):
    active = _message("assistant-1", tokens=None)
    archived = _message("archived-1", tokens=None, compacted={"summary": "old"})
    context_usage_mocks["active"] = [active]
    context_usage_mocks["all"] = [active, archived]
    context_usage_mocks["estimate"] = 40

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert context_usage_mocks["list_calls"] == [False]
    assert snapshot.compacted_tokens == 0
    assert snapshot.excluded_segments == []


@pytest.mark.asyncio
async def test_context_usage_ignores_ignored_text_parts(context_usage_mocks):
    msg = _message("command-output", tokens=None)
    context_usage_mocks["active"] = [msg]
    context_usage_mocks["all"] = [msg]
    context_usage_mocks["estimate"] = 0
    context_usage_mocks["parts"] = {
        "command-output": [
            SimpleNamespace(
                type="text",
                text="Available Tools\n" + ("x" * 16_000),
                ignored=True,
            ),
        ],
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert snapshot.used_tokens == 0
    assert all(segment.key != "conversation" for segment in snapshot.segments)


def test_context_usage_uses_last_real_model_before_builtin_command():
    real_model = _message(
        "assistant-1",
        provider_id="openai",
        model_id="gpt-5.6-luna",
    )
    command_output = _message(
        "command-output",
        created=200,
        provider_id="builtin",
        model_id="command",
    )

    assert context_usage._resolve_message_model([real_model, command_output]) == (
        "openai",
        "gpt-5.6-luna",
    )


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
        ("agentDelegation", 0),
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
                tool="delegate_task",
                state=SimpleNamespace(input={}, output="t" * 80, time={"start": 3}),
            ),
            SimpleNamespace(
                type="tool",
                tool="task",
                state=SimpleNamespace(input={}, output="d" * 40, time={"start": 4}),
            ),
            SimpleNamespace(
                type="tool",
                metadata={"tool": "skill_load"},
                state=SimpleNamespace(input={}, output="m" * 40, time={"start": 5}),
            ),
        ]
    }

    snapshot = await context_usage.build_context_usage_snapshot("sess-1")

    assert [(segment.key, segment.tokens) for segment in snapshot.segments] == [
        ("conversation", 20),
        ("tools", 30),
        ("skillLoad", 30),
        ("agentDelegation", 30),
    ]
    tools_segment = next(segment for segment in snapshot.segments if segment.key == "tools")
    assert tools_segment.tokens == 30


@pytest.mark.asyncio
async def test_system_prompt_estimate_uses_resolved_turn_context(monkeypatch):
    captured = {}

    async def fake_build_system_prompt_blocks(**kwargs):
        captured.update(kwargs)
        return [
            SystemPromptBlock("stable", "a" * 40, "global"),
            SystemPromptBlock("tail", "b" * 20, "runtime_tail"),
        ]

    agent = SimpleNamespace(name="rex", prompt="agent prompt")
    config = SimpleNamespace(instructions=["rules.md"])
    monkeypatch.setattr(
        context_usage.SessionPrompt,
        "build_system_prompt_blocks",
        fake_build_system_prompt_blocks,
    )
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.default_agent",
        AsyncMock(return_value="rex"),
    )
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=agent),
    )
    monkeypatch.setattr(
        "flocks.config.Config.get",
        AsyncMock(return_value=config),
    )
    worktree_for_directory = MagicMock(return_value="/workspace")
    monkeypatch.setattr(
        "flocks.project.project.Project.worktree_for_directory",
        worktree_for_directory,
    )
    monkeypatch.setattr(
        "flocks.project.instance.Instance.get_worktree",
        lambda: "/ambient-worktree",
    )
    monkeypatch.setattr(
        "flocks.tool.registry.ToolRegistry.revision",
        lambda: 7,
    )

    tokens = await context_usage._estimate_system_prompt_tokens(
        "sess-1",
        session=SimpleNamespace(directory="/workspace/project"),
        messages=[],
        provider_id="openai",
        model_id="gpt-5",
    )

    assert tokens == 15
    assert captured["turn_context"] == TurnPromptContext(
        worktree="/workspace",
        config_instructions=("rules.md",),
        tool_revision=7,
    )
    worktree_for_directory.assert_called_once_with("/workspace/project")
