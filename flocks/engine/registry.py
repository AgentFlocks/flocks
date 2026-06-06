"""
Loop Engine Registry

Holds non-native engine registrations.

'native' is deliberately excluded: it runs inline via SessionLoop._run_loop()
and is listed separately as NATIVE_ENGINE_META for the /api/engine/list endpoint.
"""

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import AgentLoopEngine


class LoopEngineRegistry:
    _engines: Dict[str, "AgentLoopEngine"] = {}

    @classmethod
    def register(cls, engine: "AgentLoopEngine") -> None:
        """Register a non-native engine. Overwrites any existing registration."""
        cls._engines[engine.id] = engine

    @classmethod
    def get(cls, engine_id: str) -> "AgentLoopEngine":
        """
        Return a registered engine by id.

        Raises KeyError if not found.  Callers should only invoke this when
        engine_id != 'native' (the SessionLoop dispatch handles that guard).
        """
        if engine_id not in cls._engines:
            raise KeyError(
                f"Agent loop engine '{engine_id}' is not registered. "
                f"Available engines: {list(cls._engines)}"
            )
        return cls._engines[engine_id]

    @classmethod
    def ids(cls) -> set:
        """Return the set of registered non-native engine ids."""
        return set(cls._engines.keys())

    @classmethod
    def list(cls) -> List[Dict[str, str]]:
        """Return metadata dicts for all registered non-native engines."""
        return [
            {
                "id": e.id,
                "name": e.display_name,
                "description": e.description,
            }
            for e in cls._engines.values()
        ]
