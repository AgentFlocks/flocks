"""
Evolution strategies - abstract bases and NoOp implementations.

Each layer (acquirer / author / tracker / curator) defines:
  - An abstract base class that third-party plugins inherit from.
  - A NoOp implementation that is the default when the layer is disabled.

NoOp instances are returned by EvolutionEngine when no real strategy is
configured, so callers can write `await engine.acquirer.acquire(...)`
unconditionally without `if engine.acquirer is None` branching.
"""

from flocks.evolution.strategies.acquirer import CapabilityAcquirer, NoOpAcquirer
from flocks.evolution.strategies.author import SkillAuthor, NoOpAuthor
from flocks.evolution.strategies.tracker import UsageTracker, NoOpTracker
from flocks.evolution.strategies.curator import Curator, NoOpCurator

__all__ = [
    "CapabilityAcquirer",
    "NoOpAcquirer",
    "SkillAuthor",
    "NoOpAuthor",
    "UsageTracker",
    "NoOpTracker",
    "Curator",
    "NoOpCurator",
]
