import pytest
from typer.testing import CliRunner

import flocks.cli.commands.stats as stats_cmd
from flocks.cli.commands.stats import ModelUsage, SessionStats, TokenStats
from flocks.provider.usage_service import BackfillUsageResult
from flocks.provider.types import UsageRecord
from flocks.session.session import SessionInfo, SessionTime

runner = CliRunner()


def _build_session(session_id: str = "ses_stats_test") -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID="proj_stats",
        directory="/tmp/stats-project",
        title="Stats Session",
        time=SessionTime(created=1_000, updated=2_000),
    )


@pytest.mark.asyncio
async def test_aggregate_stats_uses_usage_records(monkeypatch) -> None:
    session = _build_session()
    record = UsageRecord(
        id="usage-1",
        provider_id="anthropic",
        model_id="claude-sonnet",
        session_id=session.id,
        message_id="msg-1",
        input_tokens=11,
        output_tokens=7,
        cached_tokens=5,
        cache_write_tokens=2,
        reasoning_tokens=3,
        total_tokens=21,
        total_cost=1.25,
        currency="USD",
        source="live",
        created_at=stats_cmd.datetime.now(stats_cmd.UTC),
    )

    async def fake_resolve_project_sessions(project_filter):
        assert project_filter is None
        return [session]

    async def fake_get_usage_records(**kwargs):
        assert kwargs["session_ids"] == [session.id]
        return [record]

    async def fake_get_usage_stats(**kwargs):
        assert kwargs["session_ids"] == [session.id]
        return type("UsageStats", (), {
            "summary": type("Summary", (), {
                "cost_by_currency": [type("CurrencyCost", (), {"currency": "USD", "total_cost": 1.25})()],
            })(),
        })()

    async def fake_collect_message_metrics(session_ids):
        assert session_ids == [session.id]
        return 2, {"bash": 1}

    monkeypatch.setattr(stats_cmd, "_resolve_project_sessions", fake_resolve_project_sessions)
    monkeypatch.setattr(stats_cmd, "get_usage_records", fake_get_usage_records)
    monkeypatch.setattr(stats_cmd, "get_usage_stats", fake_get_usage_stats)
    monkeypatch.setattr(stats_cmd, "_collect_message_metrics", fake_collect_message_metrics)

    result = await stats_cmd._aggregate_stats(days=None, project_filter=None)

    assert result.total_sessions == 1
    assert result.total_messages == 2
    assert result.total_tokens.input == 11
    assert result.total_tokens.output == 7
    assert result.total_tokens.reasoning == 3
    assert result.total_tokens.cache_read == 5
    assert result.total_tokens.cache_write == 2
    assert result.total_cost_by_currency == {"USD": 1.25}
    assert result.tool_usage == {"bash": 1}
    assert result.model_usage["anthropic/claude-sonnet"].messages == 1
    assert result.model_usage["anthropic/claude-sonnet"].tokens_input == 11
    assert result.model_usage["anthropic/claude-sonnet"].tokens_output == 10
    assert result.model_usage["anthropic/claude-sonnet"].cost_by_currency == {"USD": 1.25}


@pytest.mark.asyncio
async def test_aggregate_stats_uses_active_session_median(monkeypatch) -> None:
    first = _build_session("ses_first")
    second = _build_session("ses_second")
    records = [
        UsageRecord(
            id="usage-1",
            provider_id="anthropic",
            model_id="m1",
            session_id=first.id,
            message_id="msg-1",
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            cache_write_tokens=0,
            reasoning_tokens=0,
            total_tokens=15,
            total_cost=0.5,
            currency="USD",
            source="live",
            created_at=stats_cmd.datetime.now(stats_cmd.UTC),
        ),
        UsageRecord(
            id="usage-2",
            provider_id="anthropic",
            model_id="m1",
            session_id=second.id,
            message_id="msg-2",
            input_tokens=20,
            output_tokens=10,
            cached_tokens=0,
            cache_write_tokens=0,
            reasoning_tokens=0,
            total_tokens=30,
            total_cost=1.0,
            currency="USD",
            source="live",
            created_at=stats_cmd.datetime.now(stats_cmd.UTC),
        ),
    ]

    async def fake_resolve_project_sessions(project_filter):
        return [first, second]

    async def fake_get_usage_records(**kwargs):
        return records

    async def fake_get_usage_stats(**kwargs):
        return type("UsageStats", (), {
            "summary": type("Summary", (), {
                "cost_by_currency": [type("CurrencyCost", (), {"currency": "USD", "total_cost": 1.5})()],
            })(),
        })()

    async def fake_collect_message_metrics(session_ids):
        return 4, {}

    monkeypatch.setattr(stats_cmd, "_resolve_project_sessions", fake_resolve_project_sessions)
    monkeypatch.setattr(stats_cmd, "get_usage_records", fake_get_usage_records)
    monkeypatch.setattr(stats_cmd, "get_usage_stats", fake_get_usage_stats)
    monkeypatch.setattr(stats_cmd, "_collect_message_metrics", fake_collect_message_metrics)

    result = await stats_cmd._aggregate_stats(days=None, project_filter=None)

    assert result.total_sessions == 2
    assert result.tokens_per_session == 22.5
    assert result.median_tokens_per_session == 22.5


def test_stats_command_renders_output(monkeypatch) -> None:
    async def fake_storage_init() -> None:
        return None

    async def fake_aggregate_stats(days, project):
        assert days is None
        assert project is None
        return SessionStats(
            total_sessions=2,
            total_messages=4,
            total_cost_by_currency={"USD": 2.5, "CNY": 1.2},
            cost_per_day_by_currency={"USD": 2.5, "CNY": 1.2},
            total_tokens=TokenStats(input=20, output=10, reasoning=5, cache_read=3, cache_write=1),
            tool_usage={"bash": 2},
            model_usage={
                "anthropic/claude-sonnet": ModelUsage(
                    messages=2,
                    tokens_input=20,
                    tokens_output=15,
                    cost_by_currency={"USD": 2.5},
                )
            },
            days=1,
            tokens_per_day=35.0,
            tokens_per_session=17.5,
            median_tokens_per_session=17.5,
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_aggregate_stats", fake_aggregate_stats)

    result = runner.invoke(stats_cmd.stats_app, ["--models", "0", "--tools", "5"])

    assert result.exit_code == 0
    assert "OVERVIEW" in result.stdout
    assert "TOKENS" in result.stdout
    assert "COSTS" in result.stdout
    assert "MODEL USAGE" in result.stdout
    assert "TOOL USAGE" in result.stdout
    assert "anthropic/claude-sonnet" in result.stdout
    assert "$2.5000" in result.stdout
    assert "¥1.2000" in result.stdout


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
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_aggregate_stats", fake_aggregate_stats)

    result = runner.invoke(stats_cmd.stats_app, [])

    assert result.exit_code == 0
    assert "Reasoning" not in result.stdout


def test_stats_backfill_command_renders_summary(monkeypatch) -> None:
    async def fake_storage_init(*args, **kwargs) -> None:
        return None

    async def fake_resolve_project_sessions(project_filter):
        return [_build_session()]

    async def fake_backfill_usage_records(**kwargs):
        assert kwargs["session_ids"] == ["ses_stats_test"]
        return BackfillUsageResult(
            scanned_messages=10,
            inserted_records=4,
            skipped_existing=5,
            skipped_missing_data=1,
        )

    monkeypatch.setattr(stats_cmd.Storage, "init", fake_storage_init)
    monkeypatch.setattr(stats_cmd, "_resolve_project_sessions", fake_resolve_project_sessions)
    monkeypatch.setattr(stats_cmd, "backfill_usage_records", fake_backfill_usage_records)

    result = runner.invoke(stats_cmd.stats_app, ["backfill"])

    assert result.exit_code == 0
    assert "USAGE BACKFILL" in result.stdout
    assert "Inserted Records" in result.stdout
    assert "4" in result.stdout
