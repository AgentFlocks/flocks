"""
Unified plugin loading system for Flocks.

Each subsystem (Agent, Tool, Hook, ...) registers an **ExtensionPoint** that
declares:
- which module-level attribute to look for (e.g. ``AGENTS``, ``TOOLS``),
- which subdirectory under ``~/.flocks/plugins/`` to scan,
- how to validate items and detect duplicates,
- a *consumer* callback that receives the validated items.

``PluginLoader.load_all()`` is called once during startup.  For every
registered extension point it scans the corresponding subdirectory, loads each
``.py`` module, extracts and validates the attribute, then hands the items to
the consumer.

Safety guarantees
-----------------
- A single module failure is logged and skipped.
- A single extension-point failure inside a module is logged and skipped.
- Duplicate items (by *dedup_key*) are resolved first-wins.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Set, FrozenSet

import yaml

from flocks.utils.log import Log

log = Log.create(service="plugin")

DEFAULT_PLUGIN_ROOT = Path.home() / ".flocks" / "plugins"


# ---------------------------------------------------------------------------
# Low-level helpers (extracted from agent/plugin_loader.py)
# ---------------------------------------------------------------------------


_SUPPORTED_EXTENSIONS = {".py", ".yaml", ".yml"}


def scan_directory(
    directory: Path,
    *,
    recursive: bool = False,
    max_depth: int = 1,
    exclude_subdirs: Optional[Set[str]] = None,
) -> List[str]:
    """Return absolute paths of all plugin files in *directory*.

    Supports ``.py``, ``.yaml``, and ``.yml`` files.
    Skips files whose name starts with ``_`` (e.g. ``__init__.py``,
    ``_provider.yaml``).

    When *recursive* is True, scans subdirectories up to *max_depth* levels
    deep.  ``max_depth=1`` (default) replicates the original one-level
    behaviour; ``max_depth=2`` also descends into provider sub-subdirectories::

        tools/
        ├── standalone.yaml               # depth 0
        ├── api/                          # depth 1 subdir
        │   ├── tool.yaml                 # depth 1
        │   └── threatbook/               # depth 2 subdir
        │       ├── _provider.yaml        # skipped (starts with _)
        │       └── ip_query.yaml         # depth 2
        └── mcp/                          # excluded via *exclude_subdirs*

    *exclude_subdirs* names directories at depth-1 that should be skipped
    entirely (e.g. ``{"mcp", "generated"}``).
    """
    if not directory.is_dir():
        return []

    exclude = exclude_subdirs or set()
    results: List[str] = []
    _scan_recursive(directory, results, depth=0, max_depth=max_depth if recursive else 0, exclude=exclude)
    return sorted(results)


def _scan_recursive(
    directory: Path,
    results: List[str],
    depth: int,
    max_depth: int,
    exclude: Set[str],
) -> None:
    """Recursively collect plugin files up to *max_depth* levels."""
    for item in directory.iterdir():
        if item.is_file() and item.suffix in _SUPPORTED_EXTENSIONS and not item.name.startswith("_"):
            results.append(str(item))
        elif (
            depth < max_depth
            and item.is_dir()
            and not item.name.startswith("_")
            and (depth > 0 or item.name not in exclude)
        ):
            _scan_recursive(item, results, depth + 1, max_depth, exclude)


def load_module(source: str, base_dir: Path) -> ModuleType:
    """Import a Python module from a filesystem path or package path.

    Supported formats::

        "./plugins/my_agent.py"          # relative to *base_dir*
        "/opt/agents/foo.py"             # absolute
        "mycompany.threat_intel_agents"  # installed package
    """
    is_file = source.startswith("./") or source.startswith("/") or source.endswith(".py")
    if is_file:
        path = Path(source) if Path(source).is_absolute() else base_dir / source
        spec = importlib.util.spec_from_file_location(
            f"_flocks_plugin_{path.stem}",
            str(path),
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return importlib.import_module(source)


# ---------------------------------------------------------------------------
# ExtensionPoint & PluginLoader
# ---------------------------------------------------------------------------


@dataclass
class ExtensionPoint:
    """Describes one type of contribution a plugin module can make."""

    attr_name: str
    """Module-level attribute to look for, e.g. ``"AGENTS"``."""

    subdir: str
    """Subdirectory under the plugin root, e.g. ``"agents"``."""

    consumer: Callable[[List[Any], str], None]
    """Callback ``(items, source_path) -> None`` that receives validated items."""

    item_type: Optional[type] = None
    """If set, only items that are ``isinstance(item, item_type)`` are kept."""

    dedup_key: Optional[Callable[[Any], str]] = None
    """If set, used to deduplicate items (first wins)."""

    yaml_item_factory: Optional[Callable[[dict, Path], Any]] = None
    """Optional factory ``(raw_dict, yaml_path) -> item`` for YAML config files.
    When set, ``.yaml``/``.yml`` files in the plugin subdirectory are loaded
    and each document is converted via this factory instead of Python import."""

    recursive: bool = False
    """When True, ``scan_directory`` also scans subdirectories for plugin
    files.  Depth is controlled by *max_depth*."""

    max_depth: int = 1
    """Maximum subdirectory depth when *recursive* is True.
    ``1`` = one level (original behaviour); ``2`` = provider sub-subdirs."""

    exclude_subdirs: Optional[FrozenSet[str]] = None
    """Directory names at depth-1 to skip during scanning.
    E.g. ``frozenset({"mcp", "generated"})`` — these are managed by
    dedicated subsystems rather than the generic PluginLoader."""

    _seen_keys: Set[str] = field(default_factory=set, repr=False)


class PluginLoader:
    """Unified plugin loader with extension-point dispatch."""

    _extension_points: Dict[str, ExtensionPoint] = {}
    _plugin_root: Path = DEFAULT_PLUGIN_ROOT

    # ------------------------------------------------------------------
    # Extension-point registration
    # ------------------------------------------------------------------

    @classmethod
    def register_extension_point(cls, ext: ExtensionPoint) -> None:
        """Register an extension point (idempotent — last registration wins)."""
        cls._extension_points[ext.attr_name] = ext
        log.debug(
            "plugin.ext_point.registered",
            {
                "attr": ext.attr_name,
                "subdir": ext.subdir,
            },
        )

    @classmethod
    def clear_extension_points(cls) -> None:
        """Reset all extension points (useful for testing)."""
        cls._extension_points.clear()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load_all(
        cls,
        extra_sources: Optional[List[str]] = None,
        project_dir: Optional[Path] = None,
    ) -> None:
        """Unified loading entry point.

        For each registered extension point:
        1. Scan ``~/.flocks/plugins/{ext.subdir}/`` for ``.py`` files.
        2. Scan ``<project_dir>/.flocks/plugins/{ext.subdir}/`` for project-level plugins.
        3. Load *extra_sources* (from ``cfg.plugin``) and check for the attribute.
        4. Validate, dedup, and dispatch to the consumer.
        """
        project_dir = project_dir or Path.cwd()
        project_plugin_root = project_dir / ".flocks" / "plugins"

        for ext in cls._extension_points.values():
            ext._seen_keys = set()

            # 1. User-level plugin subdirectory (~/.flocks/plugins/{subdir}/)
            subdir_path = cls._plugin_root / ext.subdir
            default_sources = scan_directory(
                subdir_path,
                recursive=ext.recursive,
                max_depth=ext.max_depth,
                exclude_subdirs=ext.exclude_subdirs,
            )
            if default_sources:
                log.info(
                    "plugin.scan",
                    {
                        "subdir": ext.subdir,
                        "files": [Path(s).name for s in default_sources],
                    },
                )
            cls._load_sources_for_ext(ext, default_sources, subdir_path)

            # 2. Project-level plugin subdirectory (<project>/.flocks/plugins/{subdir}/)
            project_subdir_path = project_plugin_root / ext.subdir
            if project_subdir_path != subdir_path and project_subdir_path.is_dir():
                project_sources = scan_directory(
                    project_subdir_path,
                    recursive=ext.recursive,
                    max_depth=ext.max_depth,
                    exclude_subdirs=ext.exclude_subdirs,
                )
                if project_sources:
                    log.info(
                        "plugin.project.scan",
                        {
                            "subdir": ext.subdir,
                            "project_dir": str(project_dir),
                            "files": [Path(s).name for s in project_sources],
                        },
                    )
                    cls._load_sources_for_ext(ext, project_sources, project_subdir_path)

            # 3. Explicit sources from cfg.plugin
            if extra_sources:
                cls._load_sources_for_ext(ext, extra_sources, project_dir)

    @classmethod
    def load_for_extension(
        cls,
        attr_name: str,
        sources: List[str],
        base_dir: Path,
    ) -> List[Any]:
        """Load plugins for a single extension point and return collected items.

        Convenience wrapper used by backward-compatible shims.
        """
        ext = cls._extension_points.get(attr_name)
        if ext is None:
            log.warn("plugin.ext_point.not_found", {"attr": attr_name})
            return []

        collected: List[Any] = []
        original_consumer = ext.consumer

        def _collecting_consumer(items: List[Any], source: str) -> None:
            collected.extend(items)
            original_consumer(items, source)

        ext.consumer = _collecting_consumer
        ext._seen_keys = set()
        try:
            cls._load_sources_for_ext(ext, sources, base_dir)
        finally:
            ext.consumer = original_consumer
        return collected

    @classmethod
    def load_default_for_extension(cls, attr_name: str) -> List[Any]:
        """Load plugins from the default subdirectory for one extension point."""
        ext = cls._extension_points.get(attr_name)
        if ext is None:
            log.warn("plugin.ext_point.not_found", {"attr": attr_name})
            return []

        subdir_path = cls._plugin_root / ext.subdir
        sources = scan_directory(
            subdir_path,
            recursive=ext.recursive,
            max_depth=ext.max_depth,
            exclude_subdirs=ext.exclude_subdirs,
        )
        if not sources:
            return []

        log.debug(
            "plugin.default_dir.scan",
            {
                "subdir": ext.subdir,
                "files": [Path(s).name for s in sources],
            },
        )
        return cls.load_for_extension(attr_name, sources, subdir_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _load_sources_for_ext(
        cls,
        ext: ExtensionPoint,
        sources: List[str],
        base_dir: Path,
    ) -> None:
        """Load each source module and dispatch matching items to *ext.consumer*."""
        for source in sources:
            source_path = Path(source)
            if source_path.suffix in (".yaml", ".yml"):
                cls._load_yaml_source(ext, source_path)
                continue

            try:
                module = load_module(source, base_dir)
            except BaseException as e:
                log.error(
                    "plugin.load_failed",
                    {
                        "source": source,
                        "error": str(e),
                        "type": type(e).__name__,
                    },
                )
                continue

            raw = getattr(module, ext.attr_name, None)
            if raw is None:
                continue

            if not isinstance(raw, (list, tuple)):
                log.warn(
                    "plugin.attr_not_list",
                    {
                        "source": source,
                        "attr": ext.attr_name,
                    },
                )
                continue

            items = cls._validate_and_dedup(ext, list(raw), source)
            if items:
                ext.consumer(items, source)
                log.info(
                    "plugin.dispatched",
                    {
                        "source": source,
                        "attr": ext.attr_name,
                        "count": len(items),
                    },
                )

    @classmethod
    def _load_yaml_source(cls, ext: ExtensionPoint, yaml_path: Path) -> None:
        """Load a YAML config file and dispatch via the extension point's factory."""
        if ext.yaml_item_factory is None:
            log.warn(
                "plugin.yaml_unsupported",
                {
                    "path": str(yaml_path),
                    "attr": ext.attr_name,
                    "hint": f"Extension point '{ext.attr_name}' has no yaml_item_factory; skipping YAML file",
                },
            )
            return

        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("plugin.yaml_parse_failed", {"path": str(yaml_path), "error": str(e)})
            return

        if not isinstance(raw, dict):
            log.warn("plugin.yaml_invalid", {"path": str(yaml_path), "hint": "Expected a YAML mapping"})
            return

        try:
            item = ext.yaml_item_factory(raw, yaml_path)
        except Exception as e:
            log.error("plugin.yaml_factory_failed", {"path": str(yaml_path), "error": str(e)})
            return

        items = cls._validate_and_dedup(ext, [item], str(yaml_path))
        if items:
            ext.consumer(items, str(yaml_path))
            log.info(
                "plugin.yaml_dispatched",
                {
                    "source": str(yaml_path),
                    "attr": ext.attr_name,
                    "count": len(items),
                },
            )

    @classmethod
    def _validate_and_dedup(
        cls,
        ext: ExtensionPoint,
        raw_items: List[Any],
        source: str,
    ) -> List[Any]:
        """Type-check and deduplicate items for an extension point."""
        if ext.item_type is not None:
            valid = [it for it in raw_items if isinstance(it, ext.item_type)]
            if len(valid) < len(raw_items):
                log.warn(
                    "plugin.invalid_entries",
                    {
                        "source": source,
                        "attr": ext.attr_name,
                        "expected_type": ext.item_type.__name__,
                        "invalid_count": len(raw_items) - len(valid),
                    },
                )
        else:
            valid = raw_items

        if ext.dedup_key is None:
            return valid

        result: List[Any] = []
        for item in valid:
            key = ext.dedup_key(item)
            if key in ext._seen_keys:
                log.warn(
                    "plugin.duplicate",
                    {
                        "source": source,
                        "attr": ext.attr_name,
                        "key": key,
                    },
                )
                continue
            ext._seen_keys.add(key)
            result.append(item)
        return result
