"""
Stats CLI command

Shows token usage and cost statistics
Ported from original cli/cmd/stats.ts
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Dict, List

import typer
from rich.console import Console

from flocks.session.session import Session, SessionInfo
from flocks.session.message import Message
from flocks.session.prompt import SessionPrompt
from flocks.project.project import Project
from flocks.storage.storage import Storage


stats_app = typer.Typer(
    name="stats",
    help="Show usage statistics",
)

console = Console()


@dataclass
class TokenStats:
    """Token usage statistics"""
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
    """Usage statistics per model"""
    messages: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0


@dataclass
class SessionStats:
    """Aggregated session statistics"""
    total_sessions: int = 0
    total_messages: int = 0
    total_cost: float = 0.0
    total_tokens: TokenStats = field(default_factory=TokenStats)
    tool_usage: Dict[str, int] = field(default_factory=dict)
    model_usage: Dict[str, ModelUsage] = field(default_factory=dict)
    date_range_earliest: int = 0
    date_range_latest: int = 0
    days: int = 0
    cost_per_day: float = 0.0
    tokens_per_day: float = 0.0
    tokens_per_session: float = 0.0
    median_tokens_per_session: float = 0.0
    has_reasoning_tokens: bool = False


def _count_tokens(text: str) -> int:
    """Count tokens using the shared prompt tokenizer fallback."""
    return SessionPrompt.count_tokens(text)


def _stringify_payload(payload: Any) -> str:
    """Convert tool payloads to text for token estimation."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(payload)


def _estimate_message_context_tokens(msg: Message) -> int:
    """Estimate how many tokens this message adds to future prompts."""
    total = 0
    for part in msg.parts:
        if part.type in {"text", "reasoning"}:
            total += _count_tokens(getattr(part, "text", ""))
            continue

        if part.type != "tool":
            continue

        state = getattr(part, "state", None)
        if state is None:
            continue

        total += _count_tokens(_stringify_payload(getattr(state, "input", None)))

        time_info = getattr(state, "time", None)
        is_compacted = isinstance(time_info, dict) and time_info.get("compacted")
        if is_compacted:
            total += 10
        else:
            total += _count_tokens(_stringify_payload(getattr(state, "output", None)))

    return total


def _estimate_assistant_tokens(msg: Message, prior_context_tokens: int) -> TokenStats:
    """Estimate assistant token usage when providers persisted no usage data."""
    estimated_output = 0
    estimated_reasoning = 0

    for part in msg.parts:
        if part.type == "text":
            estimated_output += _count_tokens(getattr(part, "text", ""))
            continue

        if part.type == "reasoning":
            estimated_reasoning += _count_tokens(getattr(part, "text", ""))
            continue

        if part.type != "tool":
            continue

        state = getattr(part, "state", None)
        if state is None:
            continue
        estimated_output += _count_tokens(_stringify_payload(getattr(state, "input", None)))

    if estimated_output == 0 and estimated_reasoning == 0:
        return TokenStats()

    return TokenStats(
        input=prior_context_tokens + 800,
        output=estimated_output,
        reasoning=estimated_reasoning,
    )


def _format_number(num: int) -> str:
    """Format number with K/M suffixes"""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def _render_row(label: str, value: str, width: int = 56) -> str:
    """Render a table row"""
    available_width = width - 1
    padding_needed = available_width - len(label) - len(value)
    padding = max(0, padding_needed)
    return f"│{label}{' ' * padding}{value} │"


async def _get_all_sessions() -> List[SessionInfo]:
    """Get all sessions across all projects"""
    return await Session.list_all()


async def _aggregate_stats(
    days: Optional[int],
    project_filter: Optional[str]
) -> SessionStats:
    """Aggregate statistics from sessions"""
    all_sessions = await _get_all_sessions()
    
    MS_IN_DAY = 24 * 60 * 60 * 1000
    
    # Calculate cutoff time
    if days is None:
        cutoff_time = 0
    elif days == 0:
        # Today only
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_time = int(today.timestamp() * 1000)
    else:
        cutoff_time = int(datetime.now().timestamp() * 1000) - days * MS_IN_DAY
    
    # Filter sessions
    filtered_sessions = all_sessions
    
    if cutoff_time > 0:
        filtered_sessions = [s for s in filtered_sessions if s.time.updated >= cutoff_time]
    
    if project_filter is not None:
        if project_filter == "":
            # Current project
            result = await Project.from_directory(os.getcwd())
            current_project_id = result["project"].id
            filtered_sessions = [s for s in filtered_sessions if s.project_id == current_project_id]
        else:
            filtered_sessions = [s for s in filtered_sessions if s.project_id == project_filter]
    
    # Initialize stats
    stats = SessionStats()
    stats.total_sessions = len(filtered_sessions)
    
    if not filtered_sessions:
        stats.days = days or 0
        return stats
    
    earliest_time = int(datetime.now().timestamp() * 1000)
    latest_time = 0
    session_total_tokens: List[int] = []
    
    # Process each session
    for session in filtered_sessions:
        messages = await Message.list_with_parts(session.id)
        stats.total_messages += len(messages)
        prior_context_tokens = 0
        session_tokens = TokenStats()

        for msg in messages:
            info = msg.info

            if info.role == "assistant":
                cost = getattr(info, "cost", 0.0) or 0.0
                provider_id = getattr(info, "providerID", None)
                model_id = getattr(info, "modelID", None)
                tokens = getattr(info, "tokens", None)
                cache = getattr(tokens, "cache", None) if tokens else None
                cache_read = getattr(cache, "read", 0) if cache else 0
                cache_write = getattr(cache, "write", 0) if cache else 0
                total_recorded_tokens = (
                    (getattr(tokens, "input", 0) if tokens else 0)
                    + (getattr(tokens, "output", 0) if tokens else 0)
                    + (getattr(tokens, "reasoning", 0) if tokens else 0)
                    + cache_read
                    + cache_write
                )
                tokens_to_add = tokens

                stats.total_cost += cost

                # Model usage
                if provider_id and model_id:
                    model_key = f"{provider_id}/{model_id}"
                    if model_key not in stats.model_usage:
                        stats.model_usage[model_key] = ModelUsage()

                    stats.model_usage[model_key].messages += 1
                    stats.model_usage[model_key].cost += cost

                if total_recorded_tokens == 0:
                    tokens_to_add = _estimate_assistant_tokens(msg, prior_context_tokens)

                # Token usage
                if tokens_to_add:
                    cache = getattr(tokens_to_add, "cache", None)
                    cache_read = getattr(cache, "read", 0) if cache else 0
                    cache_write = getattr(cache, "write", 0) if cache else 0

                    session_tokens.input += tokens_to_add.input
                    session_tokens.output += tokens_to_add.output
                    session_tokens.reasoning += tokens_to_add.reasoning
                    session_tokens.cache_read += cache_read
                    session_tokens.cache_write += cache_write
                    if tokens_to_add.reasoning > 0:
                        stats.has_reasoning_tokens = True

                    if provider_id and model_id:
                        model_key = f"{provider_id}/{model_id}"
                        stats.model_usage[model_key].tokens_input += tokens_to_add.input
                        stats.model_usage[model_key].tokens_output += (
                            tokens_to_add.output + tokens_to_add.reasoning
                        )
            
            # Tool usage
            for part in msg.parts:
                if part.type == "tool":
                    tool_name = getattr(part, "tool", None) or "unknown"
                    stats.tool_usage[tool_name] = stats.tool_usage.get(tool_name, 0) + 1

            prior_context_tokens += _estimate_message_context_tokens(msg)
        
        # Aggregate token stats
        stats.total_tokens.input += session_tokens.input
        stats.total_tokens.output += session_tokens.output
        stats.total_tokens.reasoning += session_tokens.reasoning
        stats.total_tokens.cache_read += session_tokens.cache_read
        stats.total_tokens.cache_write += session_tokens.cache_write
        
        if session_tokens.total > 0:
            session_total_tokens.append(session_tokens.total)
        
        # Update time range
        session_time = session.time.updated if cutoff_time > 0 else session.time.created
        earliest_time = min(earliest_time, session_time)
        latest_time = max(latest_time, session.time.updated)
    
    # Calculate derived stats
    stats.date_range_earliest = earliest_time
    stats.date_range_latest = latest_time
    
    range_days = max(1, (latest_time - earliest_time) // MS_IN_DAY)
    stats.days = days if days is not None else range_days
    
    if stats.days > 0:
        stats.cost_per_day = stats.total_cost / stats.days
        stats.tokens_per_day = stats.total_tokens.total / stats.days
    
    if stats.total_sessions > 0:
        stats.tokens_per_session = stats.total_tokens.total / stats.total_sessions
    
    # Calculate median
    if session_total_tokens:
        session_total_tokens.sort()
        mid = len(session_total_tokens) // 2
        if len(session_total_tokens) % 2 == 0:
            stats.median_tokens_per_session = (session_total_tokens[mid - 1] + session_total_tokens[mid]) / 2
        else:
            stats.median_tokens_per_session = session_total_tokens[mid]
    
    return stats


def _display_stats(
    stats: SessionStats,
    tool_limit: Optional[int],
    model_limit: Optional[int]
):
    """Display statistics in formatted output"""
    width = 56
    
    # Overview section
    console.print("┌" + "─" * width + "┐")
    console.print("│" + "OVERVIEW".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    console.print(_render_row("Sessions", f"{stats.total_sessions:,}", width + 2))
    console.print(_render_row("Messages", f"{stats.total_messages:,}", width + 2))
    console.print(_render_row("Days", str(stats.days), width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()
    
    # Tokens section
    console.print("┌" + "─" * width + "┐")
    console.print("│" + "TOKENS".center(width) + "│")
    console.print("├" + "─" * width + "┤")
    
    total_tokens = 0 if stats.total_tokens.total != stats.total_tokens.total else stats.total_tokens.total
    tokens_per_day = 0 if stats.tokens_per_day != stats.tokens_per_day else stats.tokens_per_day
    tokens_per_session = 0 if stats.tokens_per_session != stats.tokens_per_session else stats.tokens_per_session
    median_tokens = 0 if stats.median_tokens_per_session != stats.median_tokens_per_session else stats.median_tokens_per_session
    console.print(_render_row("Total Tokens", _format_number(int(total_tokens)), width + 2))
    console.print(_render_row("Avg Tokens/Day", _format_number(int(tokens_per_day)), width + 2))
    console.print(_render_row("Avg Tokens/Session", _format_number(int(tokens_per_session)), width + 2))
    console.print(_render_row("Median Tokens/Active Session", _format_number(int(median_tokens)), width + 2))
    console.print(_render_row("Input", _format_number(stats.total_tokens.input), width + 2))
    console.print(_render_row("Output", _format_number(stats.total_tokens.output), width + 2))
    if stats.total_tokens.reasoning > 0:
        console.print(_render_row("Reasoning", _format_number(stats.total_tokens.reasoning), width + 2))
    console.print(_render_row("Cache Read", _format_number(stats.total_tokens.cache_read), width + 2))
    console.print(_render_row("Cache Write", _format_number(stats.total_tokens.cache_write), width + 2))
    console.print("└" + "─" * width + "┘")
    console.print()
    
    # Model Usage section
    if model_limit is not None and stats.model_usage:
        sorted_models = sorted(
            stats.model_usage.items(),
            key=lambda x: x[1].messages,
            reverse=True
        )
        
        if model_limit != float('inf'):
            sorted_models = sorted_models[:model_limit]
        
        console.print("┌" + "─" * width + "┐")
        console.print("│" + "MODEL USAGE".center(width) + "│")
        console.print("├" + "─" * width + "┤")
        
        for model, usage in sorted_models:
            console.print(f"│ {model:<{width-2}} │")
            console.print(_render_row("  Messages", f"{usage.messages:,}", width + 2))
            console.print(_render_row("  Input Tokens", _format_number(usage.tokens_input), width + 2))
            console.print(_render_row("  Output Tokens", _format_number(usage.tokens_output), width + 2))
            console.print(_render_row("  Cost", f"${usage.cost:.4f}", width + 2))
            console.print("├" + "─" * width + "┤")
        
        # Remove last separator
        console.print("\033[1A└" + "─" * width + "┘")
        console.print()
    
    # Tool Usage section
    if stats.tool_usage:
        sorted_tools = sorted(
            stats.tool_usage.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        if tool_limit:
            sorted_tools = sorted_tools[:tool_limit]
        
        console.print("┌" + "─" * width + "┐")
        console.print("│" + "TOOL USAGE".center(width) + "│")
        console.print("├" + "─" * width + "┤")
        
        if sorted_tools:
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
    days: Optional[int] = typer.Option(
        None, "-d", "--days",
        help="Show stats for the last N days (default: all time). Use 0 for today."
    ),
    tools: int = typer.Option(
        5, "-t", "--tools",
        help="Number of tools to show (default: top 5; use 0 for all)"
    ),
    models: Optional[int] = typer.Option(
        None, "-m", "--models",
        help="Show model statistics. Pass a number to limit, or 0 for all."
    ),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Filter by project (empty string for current project)"
    ),
):
    """
    Show token usage and cost statistics
    
    Aggregates statistics across sessions including:
    - Session and message counts
    - Token usage (input, output, cache)
    - Cost breakdown
    - Tool usage frequency
    - Model usage statistics
    """
    asyncio.run(_show_stats(days, tools, models, project))


async def _show_stats(
    days: Optional[int],
    tools: Optional[int],
    models: Optional[int],
    project: Optional[str]
):
    """Internal stats implementation"""
    await Storage.init()
    
    console.print("[dim]Aggregating statistics...[/dim]")
    
    stats = await _aggregate_stats(days, project)
    
    # Determine model limit
    model_limit = None
    if models is not None:
        model_limit = float('inf') if models == 0 else models
    
    _display_stats(stats, tools, model_limit)
