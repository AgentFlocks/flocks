"""
Which database backend this process runs on, and the runtime modules it replaces.

SQLite is the runtime's own backend and replaces nothing.  Any other backend
is provided by an installed distribution, registered under the
``flocks.db_backends`` entry-point group with the backend's name.  The object
the entry point names carries ``modules``: for each runtime module the backend
reimplements, the module that takes its place.  A replaced package takes its
submodules with it.

A backend replaces exactly the names in :data:`REPLACEABLE_MODULES`.  That
list is the whole surface a backend takes over; a backend naming anything else,
or leaving any of them out, stops the process rather than run half-replaced.  The entry point
is loaded while ``flocks`` itself is still being imported, so the module it
names should hold the mapping and little else.

Replacement happens when a module is first imported, before any caller has
bound a name from it, so ``flocks/__init__.py`` installs it and this module
reads nothing but the environment and one small file.  The backend is the
first of:

1. ``FLOCKS_DB_BACKEND``;
2. the choice persisted in ``<config dir>/database.json`` -- what switching an
   installation to another backend writes::

       {"backend": "postgres", "dsn_file": "/path/to/a/file/holding/the/dsn"}

3. ``postgres`` when ``FLOCKS_DB_DSN`` or ``FLOCKS_DB_DSN_FILE`` is set, for
   deployments configured before there was a choice to make;
4. ``sqlite``.

``flocks`` loads the process's ``.env`` (``flocks.startup_env``) before it
decides, so a variable set there counts as well.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional

ENTRY_POINT_GROUP = "flocks.db_backends"

BACKEND_ENV = "FLOCKS_DB_BACKEND"
DSN_ENV = "FLOCKS_DB_DSN"
DSN_FILE_ENV = "FLOCKS_DB_DSN_FILE"
CHOICE_FILE_NAME = "database.json"

SQLITE = "sqlite"

#: Runtime modules a backend replaces.  A backend replaces every one of them or
#: none: they share transactions and lease state (a message write checks the
#: session lease in the same transaction), so a partial set would run, and
#: quietly stop checking.  A package listed here is replaced whole, submodules
#: included; a backend cannot name a submodule on its own.
REPLACEABLE_MODULES: frozenset[str] = frozenset({
    # The PostgreSQL runtime itself, for code that imports it by these names.
    "flocks.db",
    "flocks.runtime_state",
    # Storage: key-value documents, transcript search, memory vectors, shared files.
    "flocks.storage.storage",
    "flocks.storage.session_search",
    "flocks.storage.vector",
    "flocks.storage.file_mirror",
    "flocks.storage.file_backfill",
    # Sessions, their messages and who runs them.
    "flocks.session.session",
    "flocks.session.message",
    "flocks.session.message_pages",
    "flocks.session.ownership",
    "flocks.session.turn_receipt",
    "flocks.session.agent_policy",
    "flocks.session.execution_profile",
    # Scheduled tasks and their leases.
    "flocks.task.store",
    "flocks.task.manager",
    "flocks.task.executor",
    "flocks.task.scheduler",
    "flocks.task.lease",
    # Workflows and their claims.
    "flocks.workflow.store",
    "flocks.workflow.execution_store",
    "flocks.workflow.poller_manager",
    "flocks.workflow.ownership",
    "flocks.workflow.soc_store",
    # Memory index and notes shared between nodes.
    "flocks.memory.sync.indexer",
    "flocks.memory.sync.mirror",
    "flocks.memory.sync.ownership",
    "flocks.memory.evolution.common",
    # Retention.
    "flocks.retention.cleanup",
    "flocks.retention.policy",
    "flocks.retention.scheduler",
    # Other stores that write SQL of their own.
    "flocks.auth.service",
    "flocks.channel.inbound.session_binding",
    "flocks.provider.usage_service",
    "flocks.notifications.token_policy",
    "flocks.tool.device.store",
    "flocks.tool.device.models",
    "flocks.tool.device.startup",
    "flocks.tool.device.sync",
    "flocks.contracts.access.driver",
    "flocks.contracts.webui.approvals",
    "flocks.cli.commands.import_",
    # What processes sharing the database agree on (single-process answers here).
    "flocks.coordination",
})


class BackendConfigurationError(RuntimeError):
    """The configured database backend cannot be set up as configured."""


@dataclass(frozen=True)
class BackendChoice:
    """Which backend was chosen, and what chose it."""

    backend: str
    #: The environment variable or file that decided, or ``"default"``.
    source: str
    #: From the persisted choice: the file holding the connection string.
    dsn_file: Optional[str] = None

    @property
    def is_sqlite(self) -> bool:
        return self.backend == SQLITE


@dataclass(frozen=True)
class InstalledBackend:
    """What :func:`install` found and put in place."""

    choice: BackendChoice
    #: ``name==version`` of the distribution that provides the backend, if any.
    provider: Optional[str] = None
    #: Runtime module -> the module that replaces it.
    replacements: Mapping[str, str] = field(default_factory=dict)


def _normalise(name: str) -> str:
    backend = name.strip().lower()
    return "postgres" if backend == "postgresql" else backend


def config_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    """``FLOCKS_CONFIG_DIR``, else ``<FLOCKS_ROOT or ~/.flocks>/config`` (as ``flocks.config``)."""
    source = os.environ if environ is None else environ
    explicit = (source.get("FLOCKS_CONFIG_DIR") or "").strip()
    if explicit:
        return Path(explicit)
    root = (source.get("FLOCKS_ROOT") or "").strip()
    return (Path(root) if root else Path.home() / ".flocks") / "config"


def choice_file(environ: Optional[Mapping[str, str]] = None) -> Path:
    return config_dir(environ) / CHOICE_FILE_NAME


def persisted_choice(environ: Optional[Mapping[str, str]] = None) -> Optional[BackendChoice]:
    """The choice recorded in ``<config dir>/database.json``, or ``None`` when there is none."""
    return _persisted_choice(choice_file(environ))


def _persisted_choice(path: Path) -> Optional[BackendChoice]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise BackendConfigurationError(f"Cannot read the database backend choice in {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BackendConfigurationError(f"The database backend choice in {path} is not valid JSON: {exc}") from None
    backend = data.get("backend") if isinstance(data, dict) else None
    dsn_file = data.get("dsn_file") if isinstance(data, dict) else None
    if not isinstance(backend, str) or not backend.strip() or not (dsn_file is None or isinstance(dsn_file, str)):
        raise BackendConfigurationError(
            f'The database backend choice in {path} must be {{"backend": "<name>", "dsn_file": "<path>"}}'
        )
    return BackendChoice(backend=_normalise(backend), source=str(path), dsn_file=(dsn_file or "").strip() or None)


def backend_choice(environ: Optional[Mapping[str, str]] = None) -> BackendChoice:
    """The backend this process should run on (see the module docstring for the order)."""
    source = os.environ if environ is None else environ
    explicit = (source.get(BACKEND_ENV) or "").strip()
    if explicit:
        return BackendChoice(backend=_normalise(explicit), source=BACKEND_ENV)
    persisted = _persisted_choice(choice_file(source))
    if persisted is not None:
        return persisted
    for name in (DSN_ENV, DSN_FILE_ENV):
        if (source.get(name) or "").strip():
            return BackendChoice(backend="postgres", source=name)
    return BackendChoice(backend=SQLITE, source="default")


def _provider_for(backend: str) -> tuple[Optional[str], Any]:
    try:
        entries = [entry for entry in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if entry.name == backend]
    except Exception as exc:  # unreadable metadata: say so instead of running half-configured
        raise BackendConfigurationError(f"Cannot read the installed database backends: {exc}") from exc
    if not entries:
        return None, None
    if len(entries) > 1:
        owners = sorted(f"{entry.value} ({_distribution(entry)})" for entry in entries)
        raise BackendConfigurationError(f"More than one installed backend is called {backend!r}: {', '.join(owners)}")
    entry = entries[0]
    try:
        return _distribution(entry), entry.load()
    except Exception as exc:
        raise BackendConfigurationError(f"The {backend!r} database backend ({entry.value}) failed to load: {exc}") from exc


def _distribution(entry: importlib.metadata.EntryPoint) -> str:
    dist = getattr(entry, "dist", None)
    if dist is None:
        return "unknown distribution"
    return f"{dist.metadata['Name']}=={dist.version}"


def _replacements(backend: str, provider: Any) -> dict[str, str]:
    modules = getattr(provider, "modules", None)
    if not isinstance(modules, Mapping):
        raise BackendConfigurationError(f"The {backend!r} database backend does not say which modules it replaces")
    replacements: dict[str, str] = {}
    for name, target in modules.items():
        if name not in REPLACEABLE_MODULES:
            raise BackendConfigurationError(
                f"The {backend!r} database backend wants to replace {name!r}, which is not a replaceable runtime module"
            )
        if not isinstance(target, str) or not target or target == "flocks" or target.startswith("flocks."):
            raise BackendConfigurationError(
                f"The {backend!r} database backend must replace {name!r} with a module of its own, not {target!r}"
            )
        replacements[name] = target
    missing = sorted(REPLACEABLE_MODULES - set(replacements))
    if missing:
        raise BackendConfigurationError(
            f"The {backend!r} database backend must replace every replaceable runtime module; it leaves out "
            f"{', '.join(missing)}"
        )
    return replacements


class _ReplacementLoader(importlib.abc.Loader):
    """Hands the import system the replacing module, already imported under its own name."""

    def __init__(self, target: str, target_spec: importlib.machinery.ModuleSpec) -> None:
        self._target = target
        self._target_spec = target_spec
        self._own_spec: Optional[importlib.machinery.ModuleSpec] = None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = importlib.import_module(self._target)
        self._own_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # The import system has just stamped the replaced name's spec on the
        # module; give it back its own, so a reload or a resource lookup finds
        # the replacing module's files.
        module.__spec__ = self._own_spec

    # ``python -m <replaced name>`` runs the replacing module's code: runpy asks
    # the loader for it instead of importing the module.

    def get_code(self, fullname: str) -> Any:
        return self._target_spec.loader.get_code(self._target)

    def get_source(self, fullname: str) -> Optional[str]:
        return self._target_spec.loader.get_source(self._target)

    def is_package(self, fullname: str) -> bool:
        return self._target_spec.submodule_search_locations is not None

    def get_filename(self, fullname: str) -> Optional[str]:
        return self._target_spec.origin


class _ReplacementFinder(importlib.abc.MetaPathFinder):
    def __init__(self, replacements: Mapping[str, str]) -> None:
        self._replacements = dict(replacements)
        # Longest first, so a module listed inside a replaced package resolves to itself.
        self._prefixes = sorted(((f"{name}.", target) for name, target in self._replacements.items()), key=lambda item: -len(item[0]))

    def target(self, fullname: str) -> Optional[str]:
        target = self._replacements.get(fullname)
        if target is not None:
            return target
        if fullname.startswith("flocks."):
            for prefix, package in self._prefixes:
                if fullname.startswith(prefix):
                    return f"{package}.{fullname[len(prefix):]}"
        return None

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Optional[importlib.machinery.ModuleSpec]:
        replacement = self.target(fullname)
        if replacement is None:
            return None
        real = importlib.util.find_spec(replacement)
        if real is None:
            if fullname in self._replacements:
                # Never fall back to the runtime's own module: that would run half-replaced.
                raise ModuleNotFoundError(f"{replacement}, which replaces {fullname}, is not installed", name=fullname)
            # A submodule the replacing package does not have: the path finders look
            # inside that package (its __path__ is the parent's), and find nothing either.
            return None
        is_package = real.submodule_search_locations is not None
        spec = importlib.machinery.ModuleSpec(
            fullname, _ReplacementLoader(replacement, real), origin=real.origin, is_package=is_package,
        )
        if is_package:
            spec.submodule_search_locations = list(real.submodule_search_locations)
        return spec


_installed: Optional[InstalledBackend] = None


def install(environ: Optional[Mapping[str, str]] = None) -> InstalledBackend:
    """Choose the backend and, if it replaces runtime modules, arrange for that.  Idempotent."""
    global _installed
    if _installed is not None:
        return _installed
    choice = backend_choice(environ)
    if choice.is_sqlite:
        _installed = InstalledBackend(choice=choice)
        return _installed
    provider_name, provider = _provider_for(choice.backend)
    if provider is None:
        _installed = InstalledBackend(choice=choice)
        return _installed
    replacements = _replacements(choice.backend, provider)
    already = sorted(name for name in replacements if name in sys.modules)
    if already:
        raise BackendConfigurationError(
            f"The {choice.backend!r} database backend was installed after {', '.join(already)} had been imported"
        )
    finder = _ReplacementFinder(replacements)
    sys.meta_path.insert(0, finder)
    # Checked with the finder in place: looking a target up runs its parent
    # packages, and whatever they import from flocks must already be replaced.
    absent = []
    for name, target in sorted(replacements.items()):
        try:
            found = importlib.util.find_spec(target)
        except Exception:
            found = None
        if found is None:
            absent.append(f"{name} -> {target}")
    if absent:
        sys.meta_path.remove(finder)
        raise BackendConfigurationError(
            f"The {choice.backend!r} database backend names modules that are not installed: {', '.join(absent)}"
        )
    _installed = InstalledBackend(choice=choice, provider=provider_name, replacements=replacements)
    return _installed


def installed() -> Optional[InstalledBackend]:
    """What :func:`install` set up, or ``None`` before it ran."""
    return _installed


def describe() -> dict[str, Any]:
    """For a startup log line: the backend, who provides it, which modules it replaced."""
    state = _installed
    if state is None:
        return {"backend": None}
    return {
        "backend": state.choice.backend,
        "source": state.choice.source,
        "provider": state.provider,
        "replaced": sorted(state.replacements),
    }


__all__ = [
    "BACKEND_ENV",
    "CHOICE_FILE_NAME",
    "DSN_ENV",
    "DSN_FILE_ENV",
    "ENTRY_POINT_GROUP",
    "REPLACEABLE_MODULES",
    "SQLITE",
    "BackendChoice",
    "BackendConfigurationError",
    "InstalledBackend",
    "backend_choice",
    "choice_file",
    "config_dir",
    "describe",
    "install",
    "installed",
    "persisted_choice",
]
