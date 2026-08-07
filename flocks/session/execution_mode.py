"""Session execution-mode policy derived from OpenCode and Codex."""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional

from flocks.session.plan_file import (
    SessionPlanFile,
    is_current_plan_path,
    plan_edit_patterns_allowed,
    plan_file_prompt,
)


class SessionExecutionMode(str, Enum):
    """Execution mode selected for a user turn."""

    BUILD = "build"
    PLAN = "plan"
    GOAL = "goal"


PLAN_ONLY_TOOL_NAMES = frozenset({"plan_exit"})
PLAN_DENIED_TOOL_NAMES = frozenset(
    {
        # Explicit slash commands keep their existing direct user-only path.
        "run_slash_command",
    }
)
PLAN_DELEGATION_TOOL_NAMES = frozenset({"delegate_task"})
PLAN_DELEGATABLE_AGENT_NAMES = frozenset({"explore", "librarian"})
PLAN_PATH_SCOPED_TOOL_NAMES = frozenset({"apply_patch", "edit", "write"})

PLAN_MODE_PROMPT = """# Plan Mode

You are in a planning turn. You may inspect files, configuration, types,
tests, and documentation. Bash is available only for read-only exploration
and validation. Do not use shell commands to modify files, configuration,
services, dependencies, version control state, or any other system state.

The only file you may modify is the session plan file named below. The runtime
enforces this boundary for file-editing tools.

Follow this workflow:

1. Explore first. Ground the plan in the existing environment and resolve
   discoverable facts through inspection before asking the user.
   Delegation is limited to the `explore` and `librarian` subagents.
2. Use the question tool only for material ambiguities, preferences, or
   trade-offs that cannot be resolved from the environment. After the user
   answers, continue exploring and planning as needed.
3. Review the proposed approach for remaining gaps. Ask another focused
   question if a decision is still required.
4. Write the decision-complete implementation plan to the session plan file.
   The plan must be detailed enough for another engineer to execute without
   making additional design decisions.
5. Present the final plan to the user, then immediately call plan_exit. That
   tool asks the user whether to start implementation. If approved, it switches
   the next turn to Build and starts implementing the approved plan. If
   declined, remain in Plan and use the feedback to refine it.

Do not ask for implementation approval with ordinary prose or the question
tool; plan_exit owns that transition. A Plan turn may end only by asking a
material clarification question or by calling plan_exit after the final plan.
"""


def coerce_execution_mode(value: object) -> SessionExecutionMode:
    """Return a valid execution mode, defaulting legacy values to Build."""

    if isinstance(value, SessionExecutionMode):
        return value
    try:
        return SessionExecutionMode(str(value or SessionExecutionMode.BUILD.value))
    except ValueError:
        return SessionExecutionMode.BUILD


def runtime_execution_mode(value: object) -> SessionExecutionMode:
    """Resolve the permission mode used while executing a turn."""

    mode = coerce_execution_mode(value)
    if mode == SessionExecutionMode.GOAL:
        return SessionExecutionMode.BUILD
    return mode


def is_tool_allowed(value: object, tool_name: str) -> bool:
    """Evaluate tool visibility against OpenCode-style Plan permissions."""

    mode = runtime_execution_mode(value)
    if tool_name in PLAN_ONLY_TOOL_NAMES:
        return mode == SessionExecutionMode.PLAN
    if mode == SessionExecutionMode.BUILD:
        return True
    return tool_name not in PLAN_DENIED_TOOL_NAMES


def tool_call_denial_reason(
    value: object,
    tool_name: str,
    arguments: dict[str, Any],
    ctx: Any,
) -> Optional[str]:
    """Return a hard Plan-mode denial reason for a concrete tool call."""

    if runtime_execution_mode(value) != SessionExecutionMode.PLAN:
        return None
    if tool_name in PLAN_DELEGATION_TOOL_NAMES:
        subagent_type = str(arguments.get("subagent_type") or "").strip().lower()
        if (
            subagent_type in PLAN_DELEGATABLE_AGENT_NAMES
            and not arguments.get("session_id")
        ):
            return None
        allowed = ", ".join(sorted(PLAN_DELEGATABLE_AGENT_NAMES))
        return (
            f"Tool {tool_name!r} may only delegate to {allowed} via "
            "subagent_type while Plan mode is active."
        )
    if tool_name not in PLAN_PATH_SCOPED_TOOL_NAMES:
        return None

    if tool_name in {"edit", "write"}:
        paths = [arguments.get("filePath")]
    else:
        try:
            from flocks.tool.file.apply_patch import parse_patch

            hunks = parse_patch(str(arguments.get("patchText") or ""))
        except Exception:
            hunks = []
        paths = [
            path
            for hunk in hunks
            for path in (getattr(hunk, "path", None), getattr(hunk, "move_path", None))
            if path
        ]

    if paths and all(is_current_plan_path(ctx, path) for path in paths):
        return None
    return (
        f"Tool {tool_name!r} may only edit the current session plan file "
        "while Plan mode is active."
    )


def is_permission_allowed(
    value: object,
    permission: str,
    patterns: Iterable[object],
    ctx: Any,
) -> bool:
    """Apply the same Plan file boundary at the permission entry point."""

    if runtime_execution_mode(value) != SessionExecutionMode.PLAN:
        return True
    if permission != "edit":
        return True
    return plan_edit_patterns_allowed(ctx, patterns)


def is_plan_file_edit(value: object, ctx: Any, path: object) -> bool:
    """Return whether a read-only sandbox may allow this Plan artifact edit."""

    return (
        runtime_execution_mode(value) == SessionExecutionMode.PLAN
        and is_current_plan_path(ctx, path)
    )


def filter_tool_names(value: object, tool_names: Iterable[str]) -> list[str]:
    """Return only tool names allowed by the selected execution mode."""

    return [name for name in tool_names if is_tool_allowed(value, name)]


def execution_mode_prompt(
    value: object,
    *,
    session: Any = None,
    plan_file: Optional[SessionPlanFile] = None,
) -> str:
    """Return the per-turn developer guidance for a mode."""

    mode = runtime_execution_mode(value)
    if mode == SessionExecutionMode.PLAN:
        file_prompt = (
            plan_file_prompt(session, plan=plan_file)
            if session is not None
            else ""
        )
        return f"{PLAN_MODE_PROMPT.rstrip()}\n\n{file_prompt}".strip()
    return ""
