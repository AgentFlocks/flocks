"""
Pluggable Agent Loop Engine

Provides a registry of non-native loop engines (e.g. Raptor).
The native engine runs inline via SessionLoop._run_loop() and is NOT registered here.
"""

from .registry import LoopEngineRegistry
from .native import NATIVE_ENGINE_META

__all__ = ["LoopEngineRegistry", "NATIVE_ENGINE_META"]
