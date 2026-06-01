"""Compaction data models and legacy constants."""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# Legacy constants (kept for backward compatibility)
# ============================================================================

PRUNE_MINIMUM = 20_000
PRUNE_PROTECT = 40_000
PRUNE_PROTECTED_TOOLS = ["skill_load"]
PRESERVE_LAST_STEPS = 10


# ============================================================================
# v2 per-tool retention policy (T1 + T2)
# ----------------------------------------------------------------------------
# See ``docs/design/context-compaction-v2.md`` §T1 / §T2.
#
# Pruning is keyed on the number of *user turns* observed since the most
# recent message, not the number of assistant steps.  This matters because
# tool-heavy turns (e.g. 一次溯源 10+ 个工具调用 in one assistant turn) used
# to bloat the "step" counter and over-eagerly preserve stale tool output.
#
# ``TOOL_PRUNE_POLICY`` lets each tool declare how many user turns its
# completed output is worth keeping for.  The values are:
#
#   -1 → never prune (treat as background knowledge)
#    0 → drop on the same turn (rarely useful — keep at least the current
#         turn to satisfy provider pairing constraints)
#    N → keep the result for at most N user turns
#
# Wildcard keys ending in ``*`` are treated as prefix matches.  ``*`` alone
# is the catch-all fallback.
# ============================================================================

TOOL_RESULT_PRESERVE_USER_TURNS = 3
"""Default user-turn retention window for tool outputs (overridable by policy)."""

TOOL_PRUNE_POLICY: dict[str, int] = {
    # ------------------------------------------------------------------
    # Never prune — anchor / capability tools whose output is the agent's
    # working knowledge.  Removing them mid-session forces the LLM to
    # rediscover capabilities and usually makes things worse, not better.
    # ------------------------------------------------------------------
    "skill_load":          -1,
    "memory_get":          -1,
    "memory_search":       -1,
    "tool_search":         -1,
    "flocks_skills":       -1,
    # ------------------------------------------------------------------
    # Single-turn tools — large outputs whose value to the next turn is
    # near-zero (the agent has already extracted what it needs).
    # ------------------------------------------------------------------
    "bash":                1,
    "read":                1,
    "glob":                1,
    "grep":                1,
    "edit":                1,
    "write":               1,
    "apply_patch":         1,
    "webfetch":            1,
    "doc_parser":          1,
    "run_workflow":        1,
    "run_workflow_node":   1,
    # ------------------------------------------------------------------
    # Investigation / OSINT tools — keep an extra turn so the agent can
    # follow up on the same indicator without re-querying.
    # ------------------------------------------------------------------
    "websearch":           2,
    "tdp_*":               2,
    "threatbook_*":        2,
    "fofa_*":              2,
    "quake_*":             2,
    "ngsoc_*":             2,
    "onesig_*":            2,
    "sangfor_*":           2,
    # ------------------------------------------------------------------
    # Catch-all — also serves as the value returned when a tool name is
    # missing or unknown.
    # ------------------------------------------------------------------
    "*":                   TOOL_RESULT_PRESERVE_USER_TURNS,
}


def resolve_tool_preserve_turns(tool_name: str) -> int:
    """Resolve how many user turns ``tool_name``'s completed output should
    be preserved for.

    Match order: exact → ``prefix*`` → ``*`` catch-all → built-in default.
    Returns ``-1`` to mean "never prune".
    """
    catch_all = TOOL_PRUNE_POLICY.get("*", TOOL_RESULT_PRESERVE_USER_TURNS)
    if not tool_name:
        return catch_all
    exact = TOOL_PRUNE_POLICY.get(tool_name)
    if exact is not None:
        return exact
    for pattern, turns in TOOL_PRUNE_POLICY.items():
        if pattern.endswith("*") and pattern != "*" and tool_name.startswith(pattern[:-1]):
            return turns
    return catch_all

DEFAULT_COMPACTION_PROMPT = """\
Summarize the conversation above into a structured compaction summary. \
The new session will NOT have access to the original conversation, so \
preserve all information needed to continue seamlessly.

Your summary MUST include these sections (use exact headings):

## Decisions
Key decisions made during the conversation (architecture choices, \
approaches selected, trade-offs accepted).

## Current Task
What is currently being worked on — the active goal and its status.

## Open TODOs
Remaining tasks, unresolved issues, or next steps that were planned \
but not yet completed. Use a checklist format.

## Key Files & Identifiers
Exact file paths, function/class/variable names, API endpoints, \
configuration keys, or other identifiers referenced in the conversation. \
Preserve these EXACTLY — do not paraphrase or abbreviate.

## Constraints & Context
Important constraints, user preferences, project conventions, or \
environmental details that affect future work.

Rules:
- Keep the same language as the conversation.
- Be factual — only include information explicitly present in the conversation.
- Preserve exact identifiers (paths, names, commands) without modification.
- Omit sections that have no content rather than writing "None".
"""


# ============================================================================
# v2 iterative summary prompt (E1)
# ----------------------------------------------------------------------------
# See ``docs/design/context-compaction-v2.md`` §E1.
#
# When the previous compaction's summary is available, hand it to the model
# as authoritative prior context.  The model only needs to merge in the
# NEW turns since that summary, which significantly shortens the prompt and
# usually produces a higher-quality summary because the model isn't asked
# to "reconstruct everything from scratch" each round.
#
# Use ``DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS.format(previous_summary=...)``
# at the call site.  The structural section requirements from
# ``DEFAULT_COMPACTION_PROMPT`` are kept verbatim so the output schema
# is unchanged.
# ============================================================================

DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS = """\
Below is the PREVIOUS summary of conversation up to an earlier boundary:

<<<PREVIOUS_SUMMARY>>>
{previous_summary}
<<<END_PREVIOUS_SUMMARY>>>

Below are NEW conversation turns AFTER that boundary that you must merge \
into the summary.  Produce an UPDATED summary that supersedes the previous \
one, preserving ALL critical context from both sides.

""" + DEFAULT_COMPACTION_PROMPT


# How many consecutive ``with_previous`` summaries we allow before forcing
# a full rebuild from scratch (paired with the iterative path in
# ``SessionCompaction.process``).  Five is a conservative default — every
# fifth compaction discards drift and re-aligns with the on-disk transcript.
ITERATIVE_SUMMARY_REBUILD_INTERVAL = 5


# ============================================================================
# Pydantic models
# ============================================================================

class CompactionResult(BaseModel):
    """Result of compaction operation"""
    success: bool = True
    tokens_before: int = 0
    tokens_after: int = 0
    messages_removed: int = 0
    summary_created: bool = False
    summary_text: Optional[str] = None


class TokenInfo(BaseModel):
    """Token usage information matching TypeScript MessageV2.Assistant.tokens"""
    model_config = ConfigDict(populate_by_name=True)

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = Field(0, alias="cache.read")
    cache_write: int = Field(0, alias="cache.write")


class ModelLimits(BaseModel):
    """Model limits information"""
    context: int = 0
    input: int = 0
    output: int = 0
