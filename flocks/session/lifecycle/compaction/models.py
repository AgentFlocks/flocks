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
