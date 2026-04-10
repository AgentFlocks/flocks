"""Stats CLI command backed by usage_records."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Sequence

import typer
from rich.console import Console

from flocks.project.project import Project
from flocks.provider.usage_service import (
    BackfillUsageResult,
    backfill_usage_records,
    get_usage_records,
    get_usage_stats,
)
from flocks.session.message import Message
from flocks.session.session import Session, SessionInfo
from flocks.storage.storage import Storage


stats_app = typer.Typer(name="stats", help="Show usage statistics")
console = Console()


@dataclass
class TokenStats:
    """Token usage statistics."""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.reasoning


@dataclass
class ModelUsage:
    """Usage statistics per model."""

    messages: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_by_currency: Dict[str, float] = field(default_factory=dict)


@dataclass
class SessionStats:
    """Aggregated session statistics."""

    total_sessions: int = 0
    total_messages: int = 0
    total_cost_by_currency: Dict[str, float] = field(default_factory=dict)
    cost_per_day_by_currency: Dict[str, float] = field(default_factory=dict)
    total_tokens: TokenStats = field(default_factory=TokenStats)
    tool_usage: Dict[str, int] = field(default_factory=dict)
    model_usage: Dict[str, ModelUsage] = field(default_factory=dict)
    days: int = 0
    tokens_per_day: float = 0.0
    tokens_per_session: float = 0.0
    median_tokens_per_session: float = 0.0


def _format_number(num: int) -> str:
    """Format number with K/M suffixes."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def _render_row(label: str, value: str, width: int = 56) -> str:
    """Render a table row."""
    available_width = width - 1
    padding_needed = available_width - len(label) - len(value)
    padding = max(0, padding_needed)
    return f"│{label}{' ' * padding}{value} │"


def _format_currency(currency: str, amount: float) -> str:
    """Format a currency amount for display."""
    symbol_map = {"USD": "$", "CNY": "¥"}
    symbol = symbol_map.get(currency)
    if symbol:
        return f"{symbol}{amount:.4f}"
    return f"{currency} {amount:.4f}"


async def _get_all_sessions() -> List[SessionInfo]:
    """Get all sessions across all projects."""
    return await Session.list_all()


def _resolve_time_window(days: Optional[int]) -> tuple[Optional[str], Optional[str], int]:
    """Translate a CLI day filter into query bounds."""
    now = datetime.now(UTC)
    if days is None:
        return None, None, 0
    if days == 0:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat(), 1
    start = now - timedelta(days=days)
    return start.isoformat(), now.isoformat(), max(days, 1)


async def _resolve_project_sessions(project_filter: Optional[str]) -> List[SessionInfo]:
    """Resolve session scope for a project filter."""
    sessions = await _get_all_sessions()
    if project_filter is None:
        return sessions
    if project_filter == "":
        result = await Project.from_directory(os.getcwd())
        project_filter = result["project"].id
    return [session for session in sessions if session.project_id == project_filter]


async def _collect_message_metrics(session_ids: Sequence[str]) -> tuple[int, Dict[str, int]]:
    """Count messages and tool usage for the selected sessions."""
    total_messages = 0
    tool_usage: Dict[str, int] = {}
    for session_id in session_ids:
        messages = await Message.list_with_parts(session_id)
        total_messages += len(messages)
        for msg in messages:
            for part in msg.parts:
                if part.type != "tool":
                    continue
                tool_name = getattr(part, "tool", None) or "unknown"
                tool_usage[tool_name] = tool_usage.get(tool_name, 0) + 1
    return total_messages, tool_usage


async def _aggregate_stats(days: Optional[int], project_filter: Optional[str]) -> SessionStats:
    """Aggregate statistics from usage_records."""
    sessions = await _resolve_project_sessions(project_filter)
    session_ids = [session.id for session in sessions]
    start_date, end_date, requested_days = _resolve_time_window(days)
    records = await get_usage_records(
        start_date=start_date,
        end_date=end_date,
        session_ids=session_ids,
    )
    usage_stats = await get_usage_stats(
        start_date=start_date,
        end_date=end_date,
        session_ids=session_ids,
    )

    stats = SessionStats()
    stats.total_cost_by_currency = {
        item.currency: item.total_cost for item in usage_stats.summary.cost_by_currency
    }

    if not records:
        stats.days = requested_days
        return stats

    session_token_totals: Dict[str, int] = {}
    active_session_ids: set[str] = set()
    created_at_values: List[datetime] = []

    for record in records:
        stats.total_tokens.input += record.input_tokens
        stats.total_tokens.output += record.output_tokens
        stats.total_tokens.reasoning += record.reasoning_tokens
        stats.total_tokens.cache_read += record.cached_tokens
        stats.total_tokens.cache_write += getattr(record, "cache_write_tokens", 0)

        if record.session_id:
            active_session_ids.add(record.session_id)
            session_token_totals[record.session_id] = (
                session_token_totals.get(record.session_id, 0) + record.total_tokens
            )

        created_at_values.append(record.created_at)

        model_key = f"{record.provider_id}/{record.model_id}"
        model_usage = stats.model_usage.setdefault(model_key, ModelUsage())
        model_usage.messages += 1
        model_usage.tokens_input += record.input_tokens
        model_usage.tokens_output += record.output_tokens + record.reasoning_tokens
        model_usage.cost_by_currency[record.currency] = (
            model_usage.cost_by_currency.get(record.currency, 0.0) + record.total_cost
        )

    stats.total_sessions = len(active_session_ids)
    stats.total_messages, stats.tool_usage = await _collect_message_metrics(sorted(active_session_ids))

    if requested_days:
        stats.days = requested_days
    else:
        earliest = min(created_at_values)
        latest = max(created_at_values)
        stats.days = max(1, (latest.date() - earliest.date()).days + 1)

    if stats.days > 0:
        stats.tokens_per_day = stats.total_tokens.total / stats.days
        stats.cost_per_day_by_currency = {
            currency: total_cost / stats.days
            for currency, total_cost in stats.total_cost_by_currency.items()
        }

    if stats.total_sessions > 0:
        stats.tokens_per_session = stats.total_tokens.total / stats.total_sessions

    if session_token_totals:
        sorted_totals = sorted(session_token_totals.values())
        mid = len(sorted_totals) // 2
        if len(sorted_totals) % 2 == 0:
            stats.median_tokens_per_session = (
                sorted_totals[mid - 1] + sorted_totals[mid]
            ) / 2
        else:
            stats.median_tokens_per_session = sorted_totals[mid]

    return stats


def _display_cost_section(stats: SessionStats, width: int) -> None:
    """Render grouped cost totals."""
    console.print("┌" + "─" * width + "┐")
    console.print("│" + "COSTS".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    if stats.total_cost_by_currency:
        for currency, total_cost in sorted(stats.total_cost_by_currency.items()):
            console.print(_render_row(f"Total ({currency})", _format_currency(currency, total_cost), width + 2))
            avg_per_day = stats.cost_per_day_by_currency.get(currency, 0.0)
            console.print(_render_row(f"Avg/Day ({currency})", _format_currency(currency, avg_per_day), width + 2))
    else:
        console.print(_render_row("Total", "0", width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()


def _display_stats(stats: SessionStats, tool_limit: Optional[int], model_limit: Optional[int]) -> None:
    """Display statistics in formatted output."""
    width = 56

    console.print("┌" + "─" * width + "┐")
    console.print("│" + "OVERVIEW".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    console.print(_render_row("Sessions", f"{stats.total_sessions:,}", width + 2))
    console.print(_render_row("Messages", f"{stats.total_messages:,}", width + 2))
    console.print(_render_row("Days", str(stats.days), width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()

    console.print("┌" + "─" * width + "┐")
    console.print("│" + "TOKENS".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    console.print(_render_row("Total Tokens", _format_number(stats.total_tokens.total), width + 2))
    console.print(_render_row("Avg Tokens/Day", _format_number(int(stats.tokens_per_day)), width + 2))
    console.print(_render_row("Avg Tokens/Session", _format_number(int(stats.tokens_per_session)), width + 2))
    console.print(_render_row("Median Tokens/Active Session", _format_number(int(stats.median_tokens_per_session)), width + 2))
    console.print(_render_row("Input", _format_number(stats.total_tokens.input), width + 2))
    console.print(_render_row("Output", _format_number(stats.total_tokens.output), width + 2))
    if stats.total_tokens.reasoning > 0:
        console.print(_render_row("Reasoning", _format_number(stats.total_tokens.reasoning), width + 2))
    console.print(_render_row("Cache Read", _format_number(stats.total_tokens.cache_read), width + 2))
    console.print(_render_row("Cache Write", _format_number(stats.total_tokens.cache_write), width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()

    _display_cost_section(stats, width)

    if model_limit is not None and stats.model_usage:
        sorted_models = sorted(
            stats.model_usage.items(),
            key=lambda item: item[1].messages,
            reverse=True,
        )
        if model_limit != float("inf"):
            sorted_models = sorted_models[:model_limit]

        console.print("┌" + "─" * width + "┐")
        console.print("│" + "MODEL USAGE".center(width) + "│")
        console.print("├" + "─" * width + "┤")

        for model, usage in sorted_models:
            console.print(f"│ {model:<{width-2}} │")
            console.print(_render_row("  Messages", f"{usage.messages:,}", width + 2))
            console.print(_render_row("  Input Tokens", _format_number(usage.tokens_input), width + 2))
            console.print(_render_row("  Output Tokens", _format_number(usage.tokens_output), width + 2))
            if usage.cost_by_currency:
                for currency, total_cost in sorted(usage.cost_by_currency.items()):
                    console.print(_render_row(f"  Cost ({currency})", _format_currency(currency, total_cost), width + 2))
            else:
                console.print(_render_row("  Cost", "0", width + 2))
            console.print("├" + "─" * width + "┤")

        console.print("\033[1A└" + "─" * width + "┘")
        console.print()

    if stats.tool_usage:
        sorted_tools = sorted(
            stats.tool_usage.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if tool_limit:
            sorted_tools = sorted_tools[:tool_limit]

        console.print("┌" + "─" * width + "┐")
        console.print("│" + "TOOL USAGE".center(width) + "│")
        console.print("├" + "─" * width + "┤")
        max_count = max(count for _, count in sorted_tools)
        total_tool_usage = sum(stats.tool_usage.values())
        for tool, count in sorted_tools:
            bar_length = max(1, int((count / max_count) * 20))
            bar = "█" * bar_length
            percentage = (count / total_tool_usage) * 100
            tool_name = tool[:18] + ".." if len(tool) > 18 else tool
            content = f" {tool_name:<18} {bar:<20} {count:>3} ({percentage:>4.1f}%)"
            padding = max(0, width - len(content) - 1)
            console.print(f"│{content}{' ' * padding} │")
        console.print("└" + "─" * width + "┘")

    console.print()


@stats_app.callback(invoke_without_command=True)
def show_stats(
    ctx: typer.Context,
    days: Optional[int] = typer.Option(
        None,
        "-d",
        "--days",
        help="Show stats for the last N days (default: all time). Use 0 for today.",
    ),
    tools: int = typer.Option(
        5,
        "-t",
        "--tools",
        help="Number of tools to show (default: top 5; use 0 for all)",
    ),
    models: Optional[int] = typer.Option(
        None,
        "-m",
        "--models",
        help="Show model statistics. Pass a number to limit, or 0 for all.",
    ),
    project: Optional[str] = typer.Option(
        None,
        "-p",
        "--project",
        help="Filter by project (empty string for current project)",
    ),
) -> None:
    """Show token usage and cost statistics."""
    if ctx.invoked_subcommand:
        return
    asyncio.run(_show_stats(days, tools, models, project))


async def _show_stats(
    days: Optional[int],
    tools: Optional[int],
    models: Optional[int],
    project: Optional[str],
) -> None:
    """Internal stats implementation."""
    await Storage.init()
    console.print("[dim]Aggregating statistics...[/dim]")
    stats = await _aggregate_stats(days, project)
    model_limit = None if models is None else (float("inf") if models == 0 else models)
    _display_stats(stats, tools, model_limit)


@stats_app.command("backfill")
def backfill_stats(
    days: Optional[int] = typer.Option(
        None,
        "-d",
        "--days",
        help="Backfill usage for the last N days (default: all time). Use 0 for today.",
    ),
    project: Optional[str] = typer.Option(
        None,
        "-p",
        "--project",
        help="Filter by project (empty string for current project)",
    ),
) -> None:
    """Backfill usage_records from historical assistant message metadata."""
    asyncio.run(_backfill_stats(days, project))


async def _backfill_stats(days: Optional[int], project: Optional[str]) -> BackfillUsageResult:
    """Run historical usage backfill and report the result."""
    await Storage.init()
    sessions = await _resolve_project_sessions(project)
    session_ids = [session.id for session in sessions]
    start_date, end_date, _ = _resolve_time_window(days)
    result = await backfill_usage_records(
        session_ids=session_ids,
        start_date=start_date,
        end_date=end_date,
    )

    width = 56
    console.print("┌" + "─" * width + "┐")
    console.print("│" + "USAGE BACKFILL".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    console.print(_render_row("Scanned Assistant Messages", f"{result.scanned_messages:,}", width + 2))
    console.print(_render_row("Inserted Records", f"{result.inserted_records:,}", width + 2))
    console.print(_render_row("Skipped Existing", f"{result.skipped_existing:,}", width + 2))
    console.print(_render_row("Skipped Missing Data", f"{result.skipped_missing_data:,}", width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()
    return result
