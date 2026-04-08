import pytest
from typer.testing import CliRunner

import flocks.cli.commands.stats as stats_cmd
from flocks.cli.commands.stats import ModelUsage, SessionStats, TokenStats
from flocks.session.message import (
    AssistantMessageInfo,
    MessagePath,
    MessageWithParts,
    PartTime,
    ReasoningPart,
    TextPart,
    TokenCache,
    TokenUsage,
    ToolPart,
    ToolStatePending,
    UserMessageInfo,
)
from flocks.session.session import SessionInfo, SessionTime

runner = CliRunner()


def _build_session() -> SessionInfo:
    return SessionInfo(
        id="ses_stats_test",
        projectID="proj_stats",
        directory="/tmp/stats-project",
        title="Stats Session",
        time=SessionTime(created=1_000, updated=2_000),
    )


def _build_messages(session_id: str) -> list[MessageWithParts]:
    user = UserMessageInfo(
        id="msg_user_stats",
        sessionID=session_id,
        role="user",
        time={"created": 1_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )
    assistant = AssistantMessageInfo(
        id="msg_assistant_stats",
        sessionID=session_id,
        role="assistant",
        time={"created": 1_100, "completed": 1_200},
        parentID=user.id,
        modelID="claude-sonnet",
        providerID="anthropic",
        mode="standard",
        agent="rex",
        path=MessagePath(cwd="/tmp/stats-project", root="/tmp/stats-project"),
        tokens=TokenUsage(
            input=11,
            output=7,
            reasoning=3,
            cache=TokenCache(read=5, write=2),
        ),
        cost=1.25,
    )
    return [
        MessageWithParts(
            info=user,
            parts=[
                TextPart(
                    id="part_user_text",
                    sessionID=session_id,
                    messageID=user.id,
                    text="How many tool calls?",
                )
            ],
        ),
        MessageWithParts(
            info=assistant,
            parts=[
                TextPart(
                    id="part_assistant_text",
                    sessionID=session_id,
                    messageID=assistant.id,
                    text="One bash call.",
                ),
                ToolPart(
                    id="part_assistant_tool",
                    sessionID=session_id,
                    messageID=assistant.id,
                    callID="call_stats",
                    tool="bash",
                    state=ToolStatePending(input={"command": "echo hi"}, raw='{"command":"echo hi"}'),
                ),
            ],
        ),
    ]


def _build_zero_token_messages(session_id: str) -> list[MessageWithParts]:
    user = UserMessageInfo(
        id=f"{session_id}_user_zero",
        sessionID=session_id,
        role="user",
        time={"created": 2_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )
    assistant = AssistantMessageInfo(
        id=f"{session_id}_assistant_zero",
        sessionID=session_id,
        role="assistant",
        time={"created": 2_100, "completed": 2_200},
        parentID=user.id,
        modelID="claude-sonnet",
        providerID="anthropic",
        mode="standard",
        agent="rex",
        path=MessagePath(cwd="/tmp/stats-project", root="/tmp/stats-project"),
        tokens=TokenUsage(),
        cost=0.0,
    )
    return [
        MessageWithParts(
            info=user,
            parts=[TextPart(id=f"{session_id}_user_text", sessionID=session_id, messageID=user.id, text="hi")],
        ),
        MessageWithParts(
            info=assistant,
            parts=[],
        ),
    ]


def _build_estimated_token_messages(session_id: str) -> list[MessageWithParts]:
    user = UserMessageInfo(
        id=f"{session_id}_user_estimate",
        sessionID=session_id,
        role="user",
        time={"created": 3_000},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )
    assistant = AssistantMessageInfo(
        id=f"{session_id}_assistant_estimate",
        sessionID=session_id,
        role="assistant",
        time={"created": 3_100, "completed": 3_200},
        parentID=user.id,
        modelID="claude-sonnet",
        providerID="anthropic",
        mode="standard",
        agent="rex",
        path=MessagePath(cwd="/tmp/stats-project", root="/tmp/stats-project"),
        tokens=TokenUsage(),
        cost=0.0,
    )
    return [
        MessageWithParts(
            info=user,
            parts=[
                TextPart(
                    id=f"{session_id}_user_estimate_text",
                    sessionID=session_id,
                    messageID=user.id,
                    text="Please investigate the issue carefully.",
                )
            ],
        ),
        MessageWithParts(
            info=assistant,
            parts=[
                TextPart(
                    id=f"{session_id}_assistant_estimate_text",
                    sessionID=session_id,
                    messageID=assistant.id,
                    text="Estimated assistant answer.",
                ),
                ReasoningPart(
                    id=f"{session_id}_assistant_estimate_reasoning",
                    sessionID=session_id,
                    messageID=assistant.id,
                    text="Hidden chain of thought summary.",
                    time=PartTime(start=3_120),
                ),
                ToolPart(
                    id=f"{session_id}_assistant_estimate_tool",
                    sessionID=session_id,
                    messageID=assistant.id,
                    callID=f"{session_id}_tool_call",
                    tool="bash",
                    state=ToolStatePending(
                        input={"command": "echo estimated"},
                        raw='{"command":"echo estimated"}',
                    ),
                ),
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_aggregate_stats_uses_current_message_model(monkeypatch) -> None:
    session = _build_session()
    messages = _build_messages(session.id)

    async def fake_get_all_sessions():
        return [session]

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        assert session_id == session.id
        assert include_archived is False
        return messages

    monkeypatch.setattr(stats_cmd, "_get_all_sessions", fake_get_all_sessions)
    monkeypatch.setattr(stats_cmd.Message, "list_with_parts", fake_list_with_parts)

    result = await stats_cmd._aggregate_stats(days=None, project_filter=None)

    assert result.total_sessions == 1
    assert result.total_messages == 2
    assert result.total_cost == 1.25
    assert result.total_tokens.input == 11
    assert result.total_tokens.output == 7
    assert result.total_tokens.reasoning == 3
    assert result.total_tokens.cache_read == 5
    assert result.total_tokens.cache_write == 2
    assert result.tool_usage == {"bash": 1}
    assert result.model_usage["anthropic/claude-sonnet"].messages == 1
    assert result.model_usage["anthropic/claude-sonnet"].tokens_input == 11
    assert result.model_usage["anthropic/claude-sonnet"].tokens_output == 10


@pytest.mark.asyncio
async def test_aggregate_stats_median_ignores_zero_token_sessions(monkeypatch) -> None:
    session_with_tokens = _build_session()
    zero_session = SessionInfo(
        id="ses_stats_zero",
        projectID="proj_stats",
        directory="/tmp/stats-project",
        title="Zero Session",
        time=SessionTime(created=2_000, updated=3_000),
    )

    async def fake_get_all_sessions():
        return [session_with_tokens, zero_session]

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        assert include_archived is False
        if session_id == session_with_tokens.id:
            return _build_messages(session_id)
        return _build_zero_token_messages(session_id)

    monkeypatch.setattr(stats_cmd, "_get_all_sessions", fake_get_all_sessions)
    monkeypatch.setattr(stats_cmd.Message, "list_with_parts", fake_list_with_parts)

    result = await stats_cmd._aggregate_stats(days=None, project_filter=None)

    assert result.total_sessions == 2
    assert result.median_tokens_per_session == 21


@pytest.mark.asyncio
async def test_aggregate_stats_estimates_missing_assistant_tokens(monkeypatch) -> None:
    session = _build_session()
    messages = _build_estimated_token_messages(session.id)

    async def fake_get_all_sessions():
        return [session]

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        assert session_id == session.id
        assert include_archived is False
        return messages

    monkeypatch.setattr(stats_cmd, "_get_all_sessions", fake_get_all_sessions)
    monkeypatch.setattr(stats_cmd.Message, "list_with_parts", fake_list_with_parts)

    result = await stats_cmd._aggregate_stats(days=None, project_filter=None)

    expected_input = 800 + stats_cmd._estimate_message_context_tokens(messages[0])
    expected_output = (
        stats_cmd._count_tokens("Estimated assistant answer.")
        + stats_cmd._count_tokens(stats_cmd._stringify_payload({"command": "echo estimated"}))
    )
    expected_reasoning = stats_cmd._count_tokens("Hidden chain of thought summary.")

    assert result.total_tokens.input == expected_input
    assert result.total_tokens.output == expected_output
    assert result.total_tokens.reasoning == expected_reasoning
    assert result.has_reasoning_tokens is True
    assert result.model_usage["anthropic/claude-sonnet"].tokens_input == expected_input
    assert result.model_usage["anthropic/claude-sonnet"].tokens_output == expected_output + expected_reasoning


def test_stats_command_renders_output(monkeypatch) -> None:
    async def fake_storage_init() -> None:
        return None

    async def fake_aggregate_stats(days, project):
        assert days is None
        assert project is None
        return SessionStats(
            total_sessions=2,
            total_messages=4,
            total_cost=2.5,
            total_tokens=TokenStats(input=20, output=10, reasoning=5, cache_read=3, cache_write=1),
            tool_usage={"bash": 2},
            model_usage={"anthropic/claude-sonnet": ModelUsage(messages=2, tokens_input=20, tokens_output=15, cost=2.5)},
            days=1,
            cost_per_day=2.5,
            tokens_per_day=35.0,
            tokens_per_session=17.5,
            median_tokens_per_session=17.5,
            has_reasoning_tokens=True,
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_aggregate_stats", fake_aggregate_stats)

    result = runner.invoke(stats_cmd.stats_app, ["--models", "0", "--tools", "5"])

    assert result.exit_code == 0
    assert "OVERVIEW" in result.stdout
    assert "TOKENS" in result.stdout
    assert "Total Tokens" in result.stdout
    assert "Avg Tokens/Day" in result.stdout
    assert "Median Tokens/Active Session" in result.stdout
    assert "MODEL USAGE" in result.stdout
    assert "TOOL USAGE" in result.stdout
    assert "anthropic/claude-sonnet" in result.stdout


def test_stats_command_defaults_tools_to_five(monkeypatch) -> None:
    async def fake_storage_init() -> None:
        return None

    async def fake_aggregate_stats(days, project):
        return SessionStats(
            total_sessions=1,
            total_messages=2,
            total_tokens=TokenStats(input=20, output=10),
            tool_usage={f"tool_{i}": 10 - i for i in range(6)},
            model_usage={},
            days=1,
            tokens_per_day=30.0,
            tokens_per_session=30.0,
            median_tokens_per_session=30.0,
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_aggregate_stats", fake_aggregate_stats)

    result = runner.invoke(stats_cmd.stats_app, [])

    assert result.exit_code == 0
    assert "tool_0" in result.stdout
    assert "tool_4" in result.stdout
    assert "tool_5" not in result.stdout


def test_stats_command_hides_reasoning_when_zero(monkeypatch) -> None:
    async def fake_storage_init() -> None:
        return None

    async def fake_aggregate_stats(days, project):
        assert days is None
        assert project is None
        return SessionStats(
            total_sessions=1,
            total_messages=2,
            total_tokens=TokenStats(input=20, output=10, reasoning=0, cache_read=0, cache_write=0),
            tool_usage={},
            model_usage={},
            days=1,
            tokens_per_day=30.0,
            tokens_per_session=30.0,
            median_tokens_per_session=30.0,
            has_reasoning_tokens=False,
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_aggregate_stats", fake_aggregate_stats)

    result = runner.invoke(stats_cmd.stats_app, [])

    assert result.exit_code == 0
    assert "Reasoning" not in result.stdout
