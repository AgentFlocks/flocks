"""
EvolutionEngine - process-wide singleton coordinating all 4 evolution layers.

Lifecycle:

  1. Module imported → ``EvolutionEngine.get()`` returns an instance with
     all 4 layers wired to NoOp defaults so any code path can call
     ``engine.tracker.bump_use(...)`` etc. without crashing.

  2. ``EvolutionEngine.bootstrap(config)`` is called from
     ``flocks/server/app.py`` (and ``flocks/cli/commands/acp.py``) right
     next to ``register_builtin_hooks()``. It:
       - Registers the 4 ExtensionPoints with PluginLoader so YAML plugins
         (``plugin.kind: evolution.acquirer/author/tracker/curator``) are
         discovered alongside agents/tools/hooks.
       - Reads ``config.evolution`` and instantiates concrete strategies
         using the registered factories. Selection rule per layer:
            - ``use="builtin"`` → built-in implementation
            - ``use="<name>"`` → registered plugin with that name
            - ``use=None`` (or layer disabled) → NoOp
       - Sets ``_initialized = True`` so subsequent bootstrap() calls are
         no-ops (PluginLoader.load_all may run multiple times at startup;
         consumers MUST be idempotent).

The engine never holds references to per-session data; tracker/curator
state lives in their own files under ``~/.flocks/data/evolution/``.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Type

from flocks.evolution.strategies import (
    Curator,
    CapabilityAcquirer,
    NoOpAcquirer,
    NoOpAuthor,
    NoOpCurator,
    NoOpTracker,
    SkillAuthor,
    UsageTracker,
)
from flocks.utils.log import Log

log = Log.create(service="evolution.engine")


# Registries: strategy_name -> factory(config_dict) -> strategy_instance
_AcquirerFactory = Type[CapabilityAcquirer]
_AuthorFactory = Type[SkillAuthor]
_TrackerFactory = Type[UsageTracker]
_CuratorFactory = Type[Curator]


class EvolutionEngine:
    """Process-wide singleton for the evolution module."""

    _instance: Optional["EvolutionEngine"] = None
    _lock = threading.Lock()

    # Class-level factory registries (populated by builtin/__init__.py and plugins)
    _acquirer_factories: Dict[str, _AcquirerFactory] = {}
    _author_factories: Dict[str, _AuthorFactory] = {}
    _tracker_factories: Dict[str, _TrackerFactory] = {}
    _curator_factories: Dict[str, _CuratorFactory] = {}

    def __init__(self) -> None:
        self.acquirer: CapabilityAcquirer = NoOpAcquirer()
        self.author: SkillAuthor = NoOpAuthor()
        self.tracker: UsageTracker = NoOpTracker()
        self.curator: Curator = NoOpCurator()

        self.config: Dict[str, Any] = {}
        self._initialized: bool = False

    @classmethod
    def get(cls) -> "EvolutionEngine":
        """Return the singleton (creating it lazily with NoOp defaults)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test-only: drop the singleton so the next get() rebuilds with NoOps."""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Strategy factory registration (used by builtin/__init__.py + plugins)
    # ------------------------------------------------------------------

    @classmethod
    def register_acquirer(cls, name: str, factory: _AcquirerFactory) -> None:
        if name in cls._acquirer_factories and cls._acquirer_factories[name] is not factory:
            log.debug("acquirer.factory.duplicate", {"name": name})
            return
        cls._acquirer_factories[name] = factory

    @classmethod
    def register_author(cls, name: str, factory: _AuthorFactory) -> None:
        if name in cls._author_factories and cls._author_factories[name] is not factory:
            log.debug("author.factory.duplicate", {"name": name})
            return
        cls._author_factories[name] = factory

    @classmethod
    def register_tracker(cls, name: str, factory: _TrackerFactory) -> None:
        if name in cls._tracker_factories and cls._tracker_factories[name] is not factory:
            log.debug("tracker.factory.duplicate", {"name": name})
            return
        cls._tracker_factories[name] = factory

    @classmethod
    def register_curator(cls, name: str, factory: _CuratorFactory) -> None:
        if name in cls._curator_factories and cls._curator_factories[name] is not factory:
            log.debug("curator.factory.duplicate", {"name": name})
            return
        cls._curator_factories[name] = factory

    # ------------------------------------------------------------------
    # Introspection helpers (used by `flocks evolution status`)
    # ------------------------------------------------------------------

    @classmethod
    def list_strategies(cls) -> Dict[str, Dict[str, Any]]:
        """Return the registered factory names per layer (for the CLI)."""
        return {
            "acquirer": {"registered": sorted(cls._acquirer_factories.keys())},
            "author": {"registered": sorted(cls._author_factories.keys())},
            "tracker": {"registered": sorted(cls._tracker_factories.keys())},
            "curator": {"registered": sorted(cls._curator_factories.keys())},
        }

    def status(self) -> Dict[str, Any]:
        """Return active strategy names per layer."""
        return {
            "initialized": self._initialized,
            "config": self.config,
            "active": {
                "acquirer": self.acquirer.name or self.acquirer.__class__.__name__,
                "author": self.author.name or self.author.__class__.__name__,
                "tracker": self.tracker.name or self.tracker.__class__.__name__,
                "curator": self.curator.name or self.curator.__class__.__name__,
            },
            "noop": {
                "acquirer": self.acquirer.is_noop,
                "author": self.author.is_noop,
                "tracker": self.tracker.is_noop,
                "curator": self.curator.is_noop,
            },
        }

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self, config: Optional[Dict[str, Any]] = None) -> None:
        """
        Wire concrete strategies based on ``config['evolution']``.

        Idempotent: subsequent calls (e.g. when ToolRegistry and
        AgentRegistry both invoke PluginLoader.load_all()) are no-ops.
        """
        if self._initialized:
            log.debug("bootstrap.skip", {"reason": "already_initialized"})
            return

        # Always register ExtensionPoints + builtin factories so plugins
        # discovered by the next PluginLoader.load_all() can resolve them
        # even when the module is currently disabled (allows hot-enable
        # via config reload without re-importing).
        from flocks.evolution.builtin import register_builtin_strategies
        from flocks.evolution.plugin import register_extension_points
        register_extension_points()
        register_builtin_strategies()

        cfg = (config or {}).copy()
        self.config = cfg

        if not cfg.get("enabled", False):
            log.info("bootstrap.disabled", {"hint": "evolution.enabled is false; using NoOp on all 4 layers"})
            self._initialized = True
            return

        # Layer-by-layer wiring. Any unknown strategy name falls back to NoOp
        # with a warning rather than raising, so a misconfigured optional
        # layer never blocks server startup.
        self.acquirer = self._build_layer(
            cfg.get("acquirer") or {},
            self._acquirer_factories,
            NoOpAcquirer,
            layer="acquirer",
        )
        self.author = self._build_layer(
            cfg.get("author") or {},
            self._author_factories,
            NoOpAuthor,
            layer="author",
        )
        self.tracker = self._build_layer(
            cfg.get("tracker") or {},
            self._tracker_factories,
            NoOpTracker,
            layer="tracker",
        )
        self.curator = self._build_layer(
            cfg.get("curator") or {},
            self._curator_factories,
            NoOpCurator,
            layer="curator",
        )

        self._initialized = True
        log.info("bootstrap.ok", {"active": self.status()["active"]})

    def _build_layer(
        self,
        layer_cfg: Dict[str, Any],
        factories: Dict[str, Any],
        noop_cls: Any,
        layer: str,
    ) -> Any:
        """Resolve one layer's factory and instantiate it."""
        if not layer_cfg.get("enabled", True):
            return noop_cls()

        use = layer_cfg.get("use")
        if not use:
            return noop_cls()

        factory = factories.get(use)
        if factory is None:
            log.warn(
                "bootstrap.unknown_strategy",
                {"layer": layer, "use": use, "registered": sorted(factories.keys())},
            )
            return noop_cls()

        try:
            return factory(**(layer_cfg.get("settings") or {}))
        except Exception as exc:  # pragma: no cover - defensive
            log.error(
                "bootstrap.factory_failed",
                {"layer": layer, "use": use, "error": str(exc)},
            )
            return noop_cls()


__all__ = ["EvolutionEngine"]
