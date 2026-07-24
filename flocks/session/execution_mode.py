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
    ASK = "ask"
    PLAN = "plan"
    GOAL = "goal"


READ_ONLY_TOOL_NAMES = frozenset(
    {
        "glob",
        "grep",
        "lsp",
        "question",
        "read",
        "tool_search",
        "webfetch",
        "websearch",
    }
)

ASK_MODE_PROMPT = """# Ask Mode

Answer the user's question directly. You may inspect the workspace and use
read-only search or research tools when evidence is needed.

Do not edit files, run shell commands, change configuration, delegate work, or
perform any other side effect. Do not turn the answer into an implementation
plan unless the user explicitly asks for one.
"""

PLAN_MODE_PROMPT = """# Plan Mode

You are in a read-only planning turn. You may inspect files, configuration,
types, tests, and documentation, but you must not modify the workspace or
perform any other side effect.

First ground the plan in the existing environment. Resolve facts through
inspection before asking the user. Clarify only decisions that cannot be
discovered. Produce a decision-complete implementation plan that another
engineer can execute without making additional design decisions.
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
    if mode == SessionExecutionMode.BUILD:
        return True
    return PermissionNext.evaluate(tool_name, "*", _read_only_rules()) == "allow"


def filter_tool_names(value: object, tool_names: Iterable[str]) -> list[str]:
    """Return only tool names allowed by the selected execution mode."""

    return [name for name in tool_names if is_tool_allowed(value, name)]


def execution_mode_prompt(value: object) -> str:
    """Return the per-turn developer guidance for a mode."""

    mode = runtime_execution_mode(value)
    if mode == SessionExecutionMode.ASK:
        return ASK_MODE_PROMPT
    if mode == SessionExecutionMode.PLAN:
        return PLAN_MODE_PROMPT
    return ""
