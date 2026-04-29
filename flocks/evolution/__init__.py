"""
Flocks Evolution Module - pluggable agent self-evolution.

Provides 4 stackable layers any of which can be replaced with a YAML/Python
plugin without touching core code:

  L1 CapabilityAcquirer  - close capability gaps (delegate_task interception)
  L2 SkillAuthor         - persist successful experience as SKILL.md
  L3 UsageTracker        - track skill usage telemetry
  L4 Curator             - background skill maintenance (archive / merge)

All layers are accessed through the process-wide EvolutionEngine singleton.
When the module is disabled or a layer is unconfigured, the singleton wires
NoOp implementations so call sites never need to branch on ``is None``.

Public surface (importable by tools, hooks, and third-party plugins):
"""

from flocks.evolution.engine import EvolutionEngine
from flocks.evolution.plugin import StrategySpec
from flocks.evolution.strategies import (
    CapabilityAcquirer,
    Curator,
    NoOpAcquirer,
    NoOpAuthor,
    NoOpCurator,
    NoOpTracker,
    SkillAuthor,
    UsageTracker,
)
from flocks.evolution.types import (
    AcquireContext,
    AcquireResult,
    CapabilityGap,
    CurationReport,
    CuratorContext,
    CuratorState,
    SkillDraft,
    SkillRef,
    SkillScope,
    TransitionCounts,
    UsageRow,
    UsageState,
)

__all__ = [
    # Engine + plugin contract
    "EvolutionEngine",
    "StrategySpec",
    # Abstract bases
    "CapabilityAcquirer",
    "SkillAuthor",
    "UsageTracker",
    "Curator",
    # NoOp defaults
    "NoOpAcquirer",
    "NoOpAuthor",
    "NoOpTracker",
    "NoOpCurator",
    # Data types
    "AcquireContext",
    "AcquireResult",
    "CapabilityGap",
    "CurationReport",
    "CuratorContext",
    "CuratorState",
    "SkillDraft",
    "SkillRef",
    "SkillScope",
    "TransitionCounts",
    "UsageRow",
    "UsageState",
]
