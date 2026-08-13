"""Dream self-improvement pipeline."""

from .common import (
    DreamBridgeResult,
    DreamTarget,
    EvolutionCheckpointStore,
    SourceSnapshot,
)
from .dream import (
    DREAM_SYSTEM_PROMPT,
    DREAM_USER_PROMPT,
    list_dream_targets,
    run_dream_bridge,
)
from .scheduler import MemoryEvolutionScheduler

__all__ = [
    "DREAM_SYSTEM_PROMPT",
    "DREAM_USER_PROMPT",
    "DreamBridgeResult",
    "DreamTarget",
    "EvolutionCheckpointStore",
    "MemoryEvolutionScheduler",
    "SourceSnapshot",
    "list_dream_targets",
    "run_dream_bridge",
]
