"""Session Compaction package.

Re-exports all public symbols so that existing imports like
``from flocks.session.lifecycle.compaction import SessionCompaction``
continue to work unchanged.
"""

from flocks.session.lifecycle.compaction.policy import (
    ContextTier,
    CompactionPolicy,
    _BOUNDS,
    _DEFAULT_RATIOS,
    _TIER_OVERRIDES,
    _TIER_PRESERVE_LAST,
)
from flocks.session.lifecycle.compaction.models import (
    CompactionResult,
    TokenInfo,
    ModelLimits,
    PRUNE_MINIMUM,
    PRUNE_PROTECT,
    PRESERVE_LAST_STEPS,
    DEFAULT_COMPACTION_PROMPT,
    DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS,
    ITERATIVE_SUMMARY_REBUILD_INTERVAL,
    TOOL_RESULT_PRESERVE_USER_TURNS,
    TOOL_PRUNE_POLICY,
    resolve_tool_preserve_turns,
)
from flocks.session.lifecycle.compaction.compaction import SessionCompaction
from flocks.session.lifecycle.compaction.orchestrator import (
    build_compaction_policy,
    run_compaction,
)

__all__ = [
    # Policy
    "ContextTier",
    "CompactionPolicy",
    "_BOUNDS",
    "_DEFAULT_RATIOS",
    "_TIER_OVERRIDES",
    "_TIER_PRESERVE_LAST",
    # Models & constants
    "CompactionResult",
    "TokenInfo",
    "ModelLimits",
    "PRUNE_MINIMUM",
    "PRUNE_PROTECT",
    "PRESERVE_LAST_STEPS",
    "DEFAULT_COMPACTION_PROMPT",
    "DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS",
    "ITERATIVE_SUMMARY_REBUILD_INTERVAL",
    "TOOL_RESULT_PRESERVE_USER_TURNS",
    "TOOL_PRUNE_POLICY",
    "resolve_tool_preserve_turns",
    # Orchestrator
    "SessionCompaction",
    "build_compaction_policy",
    "run_compaction",
]
