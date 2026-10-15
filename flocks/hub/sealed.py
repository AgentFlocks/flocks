"""Hub packages delivered sealed.

An extension can supply hub packages whose contents stay encrypted until they
are installed. The catalog shows what the extension says such a package holds
-- the manifests of the plugins it installs, and the ids of its pages -- and
the plaintext exists only while an install or update runs: the package is
unsealed into a private staging directory, installed from there like a
bundled package, and the staging directory is removed when the operation
ends. The open product holds neither a key nor a copy of such a package.

A source answers three questions: which packages it has (``packages``), a
cheap token that changes whenever that answer would (``signature``), and,
during an install, the package's tree (``unseal``). Its payload layout is the
hub's own: each plugin's files sit at the plugin's ``source.path``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Mapping, Optional, Protocol, Sequence

from flocks.hub.models import HubPluginManifest
from flocks.hub.paths import safe_relative_path
from flocks.utils.log import Log

log = Log.create(service="hub.sealed")

#: Staging directories older than this are leftovers of an operation that
#: never finished (the process died mid-install), not ones still in use.
_STALE_STAGING_S = 3600.0


class SealedSourceUnavailable(RuntimeError):
    """A registered source cannot say which packages it has right now."""


class SealedPayloadUnavailable(RuntimeError):
    """A sealed package could not be unsealed for an install; the message says why."""


@dataclass(frozen=True)
class SealedPackage:
    """One sealed package as its source lists it."""

    id: str
    version: str
    #: Every plugin the package installs, with the manifest the catalog shows.
    plugins: tuple[HubPluginManifest, ...]
    #: Page ids per WebUI plugin id, so pages can be told apart by owner
    #: before anything is installed.
    pages: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


class SealedHubSource(Protocol):
    name: str

    def packages(self) -> Sequence[SealedPackage]:
        """The packages available now. Raises SealedSourceUnavailable when unknown."""

    def signature(self) -> str:
        """Changes whenever ``packages()`` would answer differently. Must be cheap."""

    async def unseal(self, package: SealedPackage, destination: Path) -> None:
        """Write the package's tree into *destination*, an empty private directory.

        Raises SealedPayloadUnavailable with a message for the operator when
        the package cannot be opened (no licence for it, for instance).
        """


_SOURCES: list[SealedHubSource] = []
_SOURCES_LOCK = threading.Lock()


def register_sealed_source(source: SealedHubSource) -> None:
    with _SOURCES_LOCK:
        if source not in _SOURCES:
            _SOURCES.append(source)
    clear_sealed_cache()


def unregister_sealed_source(source: SealedHubSource) -> None:
    with _SOURCES_LOCK:
        if source in _SOURCES:
            _SOURCES.remove(source)
    clear_sealed_cache()


def sealed_sources() -> tuple[SealedHubSource, ...]:
    with _SOURCES_LOCK:
        return tuple(_SOURCES)


def payload_path(manifest: HubPluginManifest) -> Path:
    """Where a plugin's files sit inside its unsealed package."""
    declared = manifest.source.path
    if declared:
        try:
            return safe_relative_path(declared)
        except ValueError:
            pass
    return Path("plugins") / f"{manifest.type}s" / manifest.id


@dataclass(frozen=True)
class SealedPlugin:
    package: SealedPackage
    manifest: HubPluginManifest
    source: SealedHubSource


_INDEX_LOCK = threading.Lock()
_INDEX: Optional[tuple[tuple[str, ...], dict[tuple[str, str], SealedPlugin]]] = None


def clear_sealed_cache() -> None:
    global _INDEX
    with _INDEX_LOCK:
        _INDEX = None


def sealed_signature() -> str:
    """A token that changes whenever the sealed plugins on offer change."""
    parts = []
    for source in sealed_sources():
        try:
            parts.append(f"{source.name}={source.signature()}")
        except Exception as exc:  # a source that cannot answer is a state of its own
            parts.append(f"{source.name}=unavailable:{type(exc).__name__}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16] if parts else ""


def sealed_plugins() -> dict[tuple[str, str], SealedPlugin]:
    """Every plugin a sealed package provides, by (type, id).

    The first registered source wins a duplicate. Raises
    SealedSourceUnavailable when a registered source cannot answer: what it
    would provide is then unknown, which is not the same as "nothing".
    """
    global _INDEX
    sources = sealed_sources()
    if not sources:
        return {}
    signatures = []
    for source in sources:
        try:
            signatures.append(f"{source.name}={source.signature()}")
        except Exception as exc:
            raise SealedSourceUnavailable(f"{source.name}: {exc}") from exc
    signatures = tuple(signatures)
    with _INDEX_LOCK:
        if _INDEX is not None and _INDEX[0] == signatures:
            return _INDEX[1]
    index: dict[tuple[str, str], SealedPlugin] = {}
    for source in sources:
        try:
            packages = source.packages()
        except SealedSourceUnavailable:
            raise
        except Exception as exc:
            raise SealedSourceUnavailable(f"{source.name}: {exc}") from exc
        for package in packages:
            for manifest in package.plugins:
                index.setdefault((manifest.type, manifest.id), SealedPlugin(package, manifest, source))
    with _INDEX_LOCK:
        _INDEX = (signatures, index)
    return index


def sealed_plugins_or_empty() -> dict[tuple[str, str], SealedPlugin]:
    """For listings: what the sources provide, or nothing while one cannot answer."""
    try:
        return sealed_plugins()
    except SealedSourceUnavailable as exc:
        log.warning("hub.sealed.unavailable", {"error": str(exc)})
        return {}


def sealed_manifest(plugin_type: str, plugin_id: str) -> Optional[HubPluginManifest]:
    """The manifest a sealed package gives this plugin; None when none does or
    the sources cannot answer right now."""
    plugin = sealed_plugins_or_empty().get((plugin_type, plugin_id))
    return plugin.manifest if plugin is not None else None


def sealed_page_ids(webui_id: str) -> Optional[set[str]]:
    """Page ids of a sealed WebUI plugin; None when no sealed package provides it.

    Raises SealedSourceUnavailable when a registered source cannot answer.
    """
    plugin = sealed_plugins().get(("webui", webui_id))
    if plugin is None:
        return None
    return set(plugin.package.pages.get(webui_id, ()))


# --- Staging -----------------------------------------------------------------


@dataclass(frozen=True)
class _Staged:
    path: Path
    #: The manifest of what was unsealed, pinned for the whole operation: a
    #: source that lists a newer version meanwhile must not have the old
    #: payload recorded under the new version.
    manifest: HubPluginManifest


_STAGE: ContextVar[Optional[dict[tuple[str, str], _Staged]]] = ContextVar("hub_sealed_stage", default=None)


def staged_root(plugin_type: str, plugin_id: str) -> Optional[Path]:
    """The unsealed payload of a plugin during the operation that staged it."""
    staged = (_STAGE.get() or {}).get((plugin_type, plugin_id))
    return staged.path if staged is not None else None


def staged_manifest(plugin_type: str, plugin_id: str) -> Optional[HubPluginManifest]:
    """The manifest of the payload staged for this plugin, while it is staged."""
    staged = (_STAGE.get() or {}).get((plugin_type, plugin_id))
    return staged.manifest if staged is not None else None


def _staging_parent() -> Path:
    from flocks.config.config import Config

    parent = Config.get_data_path() / "hub" / "staging"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cutoff = time.time() - _STALE_STAGING_S
    for child in parent.iterdir():
        try:
            if child.name.startswith("sealed-") and child.stat().st_mtime < cutoff:
                _remove_staging(child)
        except OSError:
            continue
    return parent


def _remove_staging(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    if root.exists():
        # Plaintext left on disk is worth an operator's attention; the next
        # sealed operation tries again once it is stale.
        log.warning("hub.sealed.staging_left_behind", {"path": str(root)})


def _check_unsealed(plugin: SealedPlugin, root: Path) -> Path:
    path = root / payload_path(plugin.manifest)
    resolved = path.resolve()
    if root.resolve() not in resolved.parents or not resolved.is_dir() or path.is_symlink():
        raise SealedPayloadUnavailable(
            f"sealed package {plugin.package.id}@{plugin.package.version} holds no {plugin.manifest.type} {plugin.manifest.id}"
        )
    shipped = path / "manifest.json"
    if shipped.is_file():
        try:
            inside = HubPluginManifest.model_validate_json(shipped.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SealedPayloadUnavailable(f"{plugin.manifest.type} {plugin.manifest.id}: unreadable manifest in the package: {exc}") from exc
        expected = (plugin.manifest.type, plugin.manifest.id, plugin.manifest.version)
        if (inside.type, inside.id, inside.version) != expected:
            raise SealedPayloadUnavailable(
                f"sealed package {plugin.package.id}@{plugin.package.version} does not match its catalog entry "
                f"for {plugin.manifest.type} {plugin.manifest.id}"
            )
    return path


def _declared_sealed(plugin_type: str, plugin_id: str) -> bool:
    from flocks.hub.catalog import load_manifest

    try:
        return load_manifest(plugin_type, plugin_id).source.kind == "sealed"
    except Exception:
        return False


def _bundled_manifest(plugin_type: str, plugin_id: str) -> Optional[dict]:
    from flocks.hub.catalog import _direct_manifest_path
    from flocks.hub.paths import get_bundled_hub_root

    path = _direct_manifest_path(get_bundled_hub_root(), plugin_type, plugin_id)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _announces_sealed(manifest: Optional[dict]) -> bool:
    return bool(manifest) and (manifest.get("source") or {}).get("kind") == "sealed"


def announced_sealed(plugin_type: str, plugin_id: str) -> bool:
    """Whether the open hub announces this plugin as delivered sealed --
    whatever an extension root lists under the same id. Read from the bundled
    hub only: the plugin's own manifest there says so or, when the open hub
    has none for it, a suite announced as sealed lists it as a part."""
    own = _bundled_manifest(plugin_type, plugin_id)
    if own is not None:
        return _announces_sealed(own)
    from flocks.hub.paths import get_bundled_hub_root

    try:
        index = json.loads((get_bundled_hub_root() / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    for entry in index.get("plugins") or []:
        if not isinstance(entry, dict) or entry.get("type") != "component":
            continue
        suite = _bundled_manifest("component", str(entry.get("id") or ""))
        if not _announces_sealed(suite):
            continue
        for ref in suite.get("components") or []:
            if isinstance(ref, dict) and (ref.get("type"), ref.get("id")) == (plugin_type, plugin_id):
                return True
    return False


def delivered_sealed(plugin_type: str, plugin_id: str) -> bool:
    """Whether this plugin is delivered sealed, as far as can be told now.

    A source provides it, or the manifest in effect says so. While the
    sources cannot answer, the catalog falls back to other listings of the
    same id, so what the open hub announces as sealed counts as sealed then.
    The sources are asked first: a listing the catalog read while they could
    not answer is not taken for theirs, whatever they answer a moment later.
    """
    try:
        provided = sealed_plugins()
    except SealedSourceUnavailable:
        provided = None
    if provided is not None and (plugin_type, plugin_id) in provided:
        return True
    if _declared_sealed(plugin_type, plugin_id):
        return True
    return provided is None and announced_sealed(plugin_type, plugin_id)


def _plan(plugin_type: str, plugin_id: str) -> list[tuple[list[SealedPlugin], bool]]:
    """The sealed packages an operation on this plugin needs, each with the
    plugins wanted from it and whether all of those are optional.

    A required plugin that is delivered sealed but that no source provides is
    refused here; an optional one is left out, and the installer skips it as
    it skips any optional part it cannot install.
    """
    from flocks.hub.catalog import load_manifest

    required = [(plugin_type, plugin_id)]
    optional: list[tuple[str, str]] = []
    if plugin_type == "component":
        try:
            refs = load_manifest("component", plugin_id).components
        except Exception:
            refs = []  # the install itself reports an unreadable suite
        from flocks.hub.installer import _is_managed_ref

        for ref in refs:
            if _is_managed_ref(ref.type, ref.id):
                continue  # served by the deployment: nothing to unseal for it
            (optional if ref.optional else required).append((ref.type, ref.id))

    try:
        plugins = sealed_plugins()
    except SealedSourceUnavailable as exc:
        # What the sources deliver is unknown right now. A plugin the open hub
        # announces as sealed waits for them rather than be installed from
        # another listing of the same id (an extension's sources, say).
        if any(_declared_sealed(*key) or announced_sealed(*key) for key in required):
            raise SealedPayloadUnavailable(f"the sealed packages cannot be listed right now: {exc}") from exc
        return []
    needed: dict[tuple[str, str, str], tuple[list[SealedPlugin], list[bool]]] = {}
    for key in [*required, *optional]:
        provided = plugins.get(key)
        if provided is not None:
            wanted, flags = needed.setdefault(
                (provided.source.name, provided.package.id, provided.package.version), ([], []),
            )
            wanted.append(provided)
            flags.append(key in optional and key not in required)
        elif key in required and _declared_sealed(*key):
            raise SealedPayloadUnavailable(
                f"{key[0]} {key[1]} is delivered as a sealed package, and this installation has no package for it"
            )
    return [(wanted, all(flags)) for wanted, flags in needed.values()]


@asynccontextmanager
async def staged_payloads(plugin_type: str, plugin_id: str) -> AsyncIterator[None]:
    """Unseal, for one install, update or update preview, what it draws on.

    The plugin itself and, for a suite, each plugin the suite installs:
    whichever of them a sealed package provides is unsealed -- once per
    package -- into a private temporary directory, which ``plugin_root``
    returns (and whose manifests ``load_manifest`` returns) for the rest of
    the operation, and which is removed when it ends. A required plugin whose
    manifest says it is delivered sealed but that no source provides is
    refused here, before anything is replaced. The listing, the checks and
    the clean-up touch the disk or the source, so they run off the event loop.
    """
    if _STAGE.get() is not None:
        # Already inside a staged operation (a suite installing its parts).
        yield
        return
    plan = await asyncio.to_thread(_plan, plugin_type, plugin_id)
    if not plan:
        yield
        return

    parent = await asyncio.to_thread(_staging_parent)
    root = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="sealed-", dir=str(parent)))
    try:
        stage: dict[tuple[str, str], _Staged] = {}
        for index, (wanted, only_optional) in enumerate(plan):
            package, source = wanted[0].package, wanted[0].source
            destination = root / str(index)
            destination.mkdir(mode=0o700)
            try:
                await source.unseal(package, destination)
                checked = await asyncio.to_thread(
                    lambda: {
                        (plugin.manifest.type, plugin.manifest.id): _Staged(_check_unsealed(plugin, destination), plugin.manifest)
                        for plugin in wanted
                    }
                )
            except Exception as exc:
                if only_optional:
                    log.warning("hub.sealed.optional_unavailable",
                                {"package": f"{package.id}@{package.version}", "error": str(exc)})
                    continue
                if isinstance(exc, SealedPayloadUnavailable):
                    raise
                raise SealedPayloadUnavailable(f"sealed package {package.id}@{package.version}: {exc}") from exc
            stage.update(checked)
        previous = _STAGE.set(stage)
        try:
            yield
        finally:
            _STAGE.reset(previous)
    finally:
        await asyncio.to_thread(_remove_staging, root)


__all__ = [
    "SealedHubSource",
    "SealedPackage",
    "SealedPayloadUnavailable",
    "SealedPlugin",
    "SealedSourceUnavailable",
    "clear_sealed_cache",
    "payload_path",
    "register_sealed_source",
    "sealed_manifest",
    "sealed_page_ids",
    "sealed_plugins",
    "sealed_plugins_or_empty",
    "sealed_signature",
    "sealed_sources",
    "staged_manifest",
    "staged_payloads",
    "staged_root",
    "unregister_sealed_source",
]
