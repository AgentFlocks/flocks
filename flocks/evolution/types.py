"""
Evolution Module - Data Types

Pluggable self-evolution capability for Flocks. Defines the data carriers
exchanged between strategies and the EvolutionEngine across all 4 layers:

  L1 CapabilityAcquirer  -> CapabilityGap, AcquireContext, AcquireResult
  L2 SkillAuthor         -> SkillDraft, SkillRef
  L3 UsageTracker        -> UsageRow, UsageState
  L4 Curator             -> CuratorContext, CurationReport, CuratorState

All models are pydantic BaseModel for cross-language schema parity with
the rest of Flocks (mirrors flocks/config/config.py style).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# L1 Capability Acquirer
# ---------------------------------------------------------------------------


class CapabilityGap(BaseModel):
    """Description of a missing capability the agent needs to fill."""

    description: str = Field(..., description="Human-readable description of the gap")
    keywords: List[str] = Field(
        default_factory=list,
        description="Lightweight tags for routing (e.g. ['email', 'smtp'], ['mcp', 'browser'])",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form context for the acquirer (originating task, constraints, etc.)",
    )
    raw_prompt: Optional[str] = Field(
        None,
        description="Original delegate_task prompt verbatim, for acquirers that need full text",
    )

    @classmethod
    def from_prompt(cls, prompt: str, context: Optional[Dict[str, Any]] = None) -> "CapabilityGap":
        """Build a gap from a free-form delegate_task prompt (default L1 path)."""
        kws: List[str] = []
        lowered = (prompt or "").lower()
        for kw in (
            "email", "smtp", "slack", "telegram", "webhook", "excel", "pdf",
            "mcp", "browser", "screenshot", "database", "redis", "kafka",
        ):
            if kw in lowered:
                kws.append(kw)
        return cls(description=prompt[:500], keywords=kws, raw_prompt=prompt, context=context or {})


class AcquireContext(BaseModel):
    """Runtime context passed into CapabilityAcquirer.acquire()."""

    model_config = {"arbitrary_types_allowed": True}

    session_id: Optional[str] = Field(None, description="Calling session id, if any")
    message_id: Optional[str] = Field(None, description="Calling message id, if any")
    agent: Optional[str] = Field(None, description="Calling agent name (e.g. 'rex')")
    extra: Dict[str, Any] = Field(default_factory=dict)


class AcquireResult(BaseModel):
    """Outcome reported back by CapabilityAcquirer.acquire()."""

    acquired: bool = Field(..., description="Whether a usable capability was produced")
    tool_name: Optional[str] = Field(None, description="Name of tool created or installed, if any")
    notes: str = Field("", description="Free-form notes for the calling agent")
    attempted: List[str] = Field(default_factory=list, description="What was tried, in order")
    error: Optional[str] = Field(None, description="Error message when acquired=False")


# ---------------------------------------------------------------------------
# L2 Skill Author
# ---------------------------------------------------------------------------


SkillScope = Literal["user", "project"]


class SkillDraft(BaseModel):
    """Spec for creating a new skill via SkillAuthor.create()."""

    name: str = Field(..., description="Skill name (lowercase, hyphen-separated, must be unique)")
    description: str = Field(..., description="Frontmatter description (1-1024 chars)")
    content: str = Field(..., description="SKILL.md body content (without frontmatter)")
    scope: SkillScope = Field("user", description="user → ~/.flocks; project → <cwd>/.flocks")
    category: Optional[str] = Field(None, description="Optional category for nested layout")
    tags: List[str] = Field(default_factory=list, description="Optional tags for discoverability")
    extra_frontmatter: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional frontmatter keys (merged into the YAML header)",
    )


class SkillRef(BaseModel):
    """Reference to a skill that an SkillAuthor operation produced or modified."""

    name: str
    scope: SkillScope
    location: str = Field(..., description="Absolute path to SKILL.md")
    skill_dir: str = Field(..., description="Absolute path to the skill directory")


# ---------------------------------------------------------------------------
# L3 Usage Tracker
# ---------------------------------------------------------------------------


class UsageState(str, Enum):
    """Lifecycle state of an agent-created skill."""
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class UsageRow(BaseModel):
    """Single tracker record reported by UsageTracker.report()."""

    name: str
    scope: SkillScope
    use_count: int = 0
    view_count: int = 0
    patch_count: int = 0
    last_used_at: Optional[str] = None
    last_viewed_at: Optional[str] = None
    last_patched_at: Optional[str] = None
    created_at: Optional[str] = None
    state: UsageState = UsageState.ACTIVE
    pinned: bool = False
    archived_at: Optional[str] = None


# ---------------------------------------------------------------------------
# L4 Curator
# ---------------------------------------------------------------------------


class CuratorState(BaseModel):
    """Persistent scheduler state for the curator (mirrors hermes .curator_state)."""

    last_run_at: Optional[str] = None
    last_run_duration_seconds: Optional[float] = None
    last_run_summary: Optional[str] = None
    paused: bool = False
    run_count: int = 0


class CuratorContext(BaseModel):
    """Runtime context passed into Curator.run()."""

    model_config = {"arbitrary_types_allowed": True}

    triggered_by: str = Field(..., description="What triggered the run (e.g. 'command:new', 'cli')")
    session_id: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class TransitionCounts(BaseModel):
    """Counter dict from apply_automatic_transitions()."""

    checked: int = 0
    marked_stale: int = 0
    archived: int = 0
    reactivated: int = 0


class CurationReport(BaseModel):
    """Outcome of one curator pass."""

    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0
    auto_transitions: TransitionCounts = Field(default_factory=TransitionCounts)
    archived: List[str] = Field(default_factory=list)
    added: List[str] = Field(default_factory=list)
    state_transitions: List[Dict[str, str]] = Field(default_factory=list)
    llm_summary: str = ""
    llm_error: Optional[str] = None
    report_dir: Optional[str] = Field(None, description="Absolute path to the {stamp}/ report directory")


__all__ = [
    "CapabilityGap",
    "AcquireContext",
    "AcquireResult",
    "SkillScope",
    "SkillDraft",
    "SkillRef",
    "UsageState",
    "UsageRow",
    "CuratorState",
    "CuratorContext",
    "TransitionCounts",
    "CurationReport",
]
