"""Session execution-mode policy derived from OpenCode and Codex."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Iterable

from flocks.permission.helpers import from_config
from flocks.permission.next import PermissionNext


class SessionExecutionMode(str, Enum):
    """Execution mode selected for a user turn."""

    BUILD = "build"
    PLAN = "plan"
    GOAL = "goal"


READ_ONLY_TOOL_NAMES = frozenset(
    {
        "glob",
        "grep",
        "lsp",
        "plan_exit",
        "question",
        "read",
        "tool_search",
        "webfetch",
        "websearch",
    }
)

PLAN_ONLY_TOOL_NAMES = frozenset({"plan_exit"})

PLAN_MODE_PROMPT = """# Plan Mode

You are in a read-only planning turn. You may inspect files, configuration,
types, tests, and documentation, but you must not modify the workspace or
perform any other side effect.

Follow this workflow:

1. Explore first. Ground the plan in the existing environment and resolve
   discoverable facts through inspection before asking the user.
2. Use the question tool only for material ambiguities, preferences, or
   trade-offs that cannot be resolved from the environment. After the user
   answers, continue exploring and planning as needed.
3. Review the proposed approach for remaining gaps. Ask another focused
   question if a decision is still required.
4. Present a decision-complete implementation plan that another engineer can
   execute without making additional design decisions.
5. Immediately after presenting the final plan, call plan_exit. That tool asks
   the user whether to start implementation. If approved, it switches the next
   turn to Build and starts implementing the approved plan. If declined, remain
   in Plan and use the feedback to refine it.

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


@lru_cache(maxsize=1)
def _read_only_rules():
    permission_config = {"*": "deny"}
    permission_config.update({name: "allow" for name in READ_ONLY_TOOL_NAMES})
    return from_config(permission_config)


def is_tool_allowed(value: object, tool_name: str) -> bool:
    """Evaluate a tool against the mode-specific PermissionNext rules."""

    mode = runtime_execution_mode(value)
    if tool_name in PLAN_ONLY_TOOL_NAMES:
        return mode == SessionExecutionMode.PLAN
    if mode == SessionExecutionMode.BUILD:
        return True
    return PermissionNext.evaluate(tool_name, "*", _read_only_rules()) == "allow"


def filter_tool_names(value: object, tool_names: Iterable[str]) -> list[str]:
    """Return only tool names allowed by the selected execution mode."""

    return [name for name in tool_names if is_tool_allowed(value, name)]


def execution_mode_prompt(value: object) -> str:
    """Return the per-turn developer guidance for a mode."""

    mode = runtime_execution_mode(value)
    if mode == SessionExecutionMode.PLAN:
        return PLAN_MODE_PROMPT
    return ""
