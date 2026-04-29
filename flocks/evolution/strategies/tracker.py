"""
L3 UsageTracker - track per-skill usage telemetry for the curator.

The default ``BuiltinFsUsageTracker`` writes a sidecar JSON per project at
``~/.flocks/data/evolution/usage/{project_id}.json`` (and
``_user.json`` for user-scope skills) with use_count / view_count /
patch_count / last_*_at / state / pinned.

Only skills present in ``authored.jsonl`` (i.e. agent-created via L2) are
tracked; bundled and hub-installed skills are intentionally ignored so
curator never touches upstream-managed content.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from flocks.evolution.types import SkillScope, UsageRow, UsageState


class UsageTracker(ABC):
    """Abstract base for L3 usage tracking strategies."""

    name: str = ""
    is_noop: bool = False

    @abstractmethod
    def bump_view(self, skill_name: str, scope: SkillScope = "user") -> None:
        """Increment view_count and last_viewed_at."""
        ...

    @abstractmethod
    def bump_use(self, skill_name: str, scope: SkillScope = "user") -> None:
        """Increment use_count and last_used_at (called from skill_tool_impl)."""
        ...

    @abstractmethod
    def bump_patch(self, skill_name: str, scope: SkillScope = "user") -> None:
        """Increment patch_count and last_patched_at."""
        ...

    @abstractmethod
    def set_state(self, skill_name: str, state: UsageState, scope: SkillScope = "user") -> None:
        """Set lifecycle state."""
        ...

    @abstractmethod
    def set_pinned(self, skill_name: str, pinned: bool, scope: SkillScope = "user") -> None:
        """Pin / unpin a skill (pinned skills bypass curator transitions)."""
        ...

    @abstractmethod
    def report(self) -> List[UsageRow]:
        """Return all tracked rows for both user and current project scope."""
        ...

    @abstractmethod
    def forget(self, skill_name: str, scope: SkillScope = "user") -> None:
        """Drop a skill's record entirely (called when the skill is deleted)."""
        ...


class NoOpTracker(UsageTracker):
    """Default no-op tracker; bump calls are silently dropped."""

    name = "_noop"
    is_noop = True

    def bump_view(self, skill_name: str, scope: SkillScope = "user") -> None:  # noqa: D401
        return None

    def bump_use(self, skill_name: str, scope: SkillScope = "user") -> None:
        return None

    def bump_patch(self, skill_name: str, scope: SkillScope = "user") -> None:
        return None

    def set_state(self, skill_name: str, state: UsageState, scope: SkillScope = "user") -> None:
        return None

    def set_pinned(self, skill_name: str, pinned: bool, scope: SkillScope = "user") -> None:
        return None

    def report(self) -> List[UsageRow]:
        return []

    def forget(self, skill_name: str, scope: SkillScope = "user") -> None:
        return None
