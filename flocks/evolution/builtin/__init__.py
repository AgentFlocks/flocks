"""
Built-in evolution strategies.

Concrete implementations live alongside this package. The module's job
is to register them with ``EvolutionEngine`` factory tables so that
config like ``evolution.acquirer.use = "builtin"`` resolves correctly.

Implementations are filled in by the per-layer todos
(``L1-acquirer``, ``L2-author``, ``L3-tracker``, ``L4-curator-pure``);
this file currently performs no registrations so the scaffold remains
side-effect-free until the real classes land.
"""

from flocks.evolution.builtin.acquirer import BuiltinSelfEnhanceAcquirer
from flocks.evolution.builtin.author import BuiltinSkillAuthor
from flocks.evolution.builtin.curator import BuiltinIdleCurator
from flocks.evolution.builtin.tracker import BuiltinFsUsageTracker
from flocks.evolution.engine import EvolutionEngine


def register_builtin_strategies() -> None:
    """Idempotent helper: registers all builtin factories with EvolutionEngine.

    Safe to call multiple times — register_<layer>() de-duplicates by
    factory identity.
    """
    EvolutionEngine.register_acquirer("builtin", BuiltinSelfEnhanceAcquirer)
    EvolutionEngine.register_author("builtin", BuiltinSkillAuthor)
    EvolutionEngine.register_tracker("builtin", BuiltinFsUsageTracker)
    EvolutionEngine.register_curator("builtin", BuiltinIdleCurator)


__all__ = [
    "BuiltinSelfEnhanceAcquirer",
    "BuiltinSkillAuthor",
    "BuiltinFsUsageTracker",
    "BuiltinIdleCurator",
    "register_builtin_strategies",
]
