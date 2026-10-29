"""Hub plugins an extension provides.

An extension can offer hub plugins of its own, next to what the hub roots
list.  The catalog lists the manifests a provider gives; an install, update or
update preview draws the plugin's files from the provider, which puts them in
place for the length of that operation only and removes them afterwards.
What backs a provider -- a server, an archive it unpacks -- is its own
business.

A provider answers:

* ``signature()``: a cheap token that changes whenever its other answers would;
* ``plugins()``: the manifests of the plugins it provides now, by
  ``(type, id)``; raises :class:`ProviderUnavailable` when it cannot say;
* ``claims(type, id)``: whether that plugin is one only this provider
  delivers, even while it cannot list it -- no other listing of the same id
  then stands in for it;
* ``stage(type, id)``: an async context manager for one operation on that
  plugin, yielding ``{(type, id): (root, manifest)}`` for each plugin of its
  own the operation draws on (a suite's parts as well), with each plugin's
  files under its root.  What it yields is what the operation installs and
  records; it cleans up when the context ends.

A plugin is its provider's own: the first registered provider that lists it
or claims it.  Another provider's copy of the same id never stands in for it.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncContextManager, AsyncIterator, Mapping, Optional, Protocol

from flocks.hub.models import HubPluginManifest
from flocks.hub.paths import safe_relative_path
from flocks.utils.log import Log

log = Log.create(service="hub.providers")

PluginKey = tuple[str, str]


class ProviderUnavailable(RuntimeError):
    """A registered provider cannot say which plugins it provides right now."""


class HubPackageProvider(Protocol):
    name: str

    def signature(self) -> str:
        """Changes whenever ``plugins()`` or ``claims()`` would answer differently. Must be cheap."""

    def plugins(self) -> Mapping[PluginKey, HubPluginManifest]:
        """The plugins provided now. Raises ProviderUnavailable when unknown."""

    def claims(self, plugin_type: str, plugin_id: str) -> bool:
        """Whether only this provider delivers that plugin."""

    def stage(self, plugin_type: str, plugin_id: str) -> AsyncContextManager[Mapping[PluginKey, tuple[Path, HubPluginManifest]]]:
        """Put the files of what an operation on that plugin draws on in place, for the operation."""


_PROVIDERS: list[HubPackageProvider] = []
_PROVIDERS_LOCK = threading.Lock()


def register_provider(provider: HubPackageProvider) -> None:
    with _PROVIDERS_LOCK:
        if provider not in _PROVIDERS:
            _PROVIDERS.append(provider)
    clear_cache()


def unregister_provider(provider: HubPackageProvider) -> None:
    with _PROVIDERS_LOCK:
        if provider in _PROVIDERS:
            _PROVIDERS.remove(provider)
    clear_cache()


def registered_providers() -> tuple[HubPackageProvider, ...]:
    with _PROVIDERS_LOCK:
        return tuple(_PROVIDERS)


def payload_path(manifest: HubPluginManifest) -> Path:
    """Where a provided plugin's files sit, relative to what its provider stages."""
    declared = manifest.source.path
    if declared:
        try:
            return safe_relative_path(declared)
        except ValueError:
            pass
    return Path("plugins") / f"{manifest.type}s" / manifest.id


_INDEX_LOCK = threading.Lock()
_INDEX: Optional[tuple[tuple[str, ...], dict[PluginKey, tuple[HubPackageProvider, HubPluginManifest]]]] = None


def clear_cache() -> None:
    global _INDEX
    with _INDEX_LOCK:
        _INDEX = None


def signature() -> str:
    """A token that changes whenever what the providers offer changes; empty with none."""
    parts = []
    for provider in registered_providers():
        try:
            parts.append(f"{provider.name}={provider.signature()}")
        except Exception as exc:  # a provider that cannot answer is a state of its own
            parts.append(f"{provider.name}=unavailable:{type(exc).__name__}")
    return "\n".join(parts)


def provided_plugins() -> dict[PluginKey, tuple[HubPackageProvider, HubPluginManifest]]:
    """Every plugin a provider provides, by (type, id), with its provider.

    The first registered provider wins a duplicate.  Raises
    ProviderUnavailable when a provider cannot answer: what it would provide
    is then unknown, which is not the same as "nothing".
    """
    global _INDEX
    providers = registered_providers()
    if not providers:
        return {}
    signatures = []
    for provider in providers:
        try:
            signatures.append(f"{provider.name}={provider.signature()}")
        except Exception as exc:
            raise ProviderUnavailable(f"{provider.name}: {exc}") from exc
    key = tuple(signatures)
    with _INDEX_LOCK:
        if _INDEX is not None and _INDEX[0] == key:
            return _INDEX[1]
    index: dict[PluginKey, tuple[HubPackageProvider, HubPluginManifest]] = {}
    for provider in providers:
        try:
            plugins = provider.plugins()
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"{provider.name}: {exc}") from exc
        for plugin_key, manifest in plugins.items():
            index.setdefault(plugin_key, (provider, manifest))
    with _INDEX_LOCK:
        _INDEX = (key, index)
    return index


def provided_plugins_or_empty() -> dict[PluginKey, tuple[HubPackageProvider, HubPluginManifest]]:
    """For listings: what the providers offer, or nothing while one cannot answer."""
    try:
        return provided_plugins()
    except ProviderUnavailable as exc:
        log.warning("hub.providers.unavailable", {"error": str(exc)})
        return {}


def provided_manifest(plugin_type: str, plugin_id: str) -> Optional[HubPluginManifest]:
    """The manifest a provider gives this plugin; None when none does or they cannot answer now."""
    found = provided_plugins_or_empty().get((plugin_type, plugin_id))
    return found[1] if found is not None else None


def claimed(plugin_type: str, plugin_id: str) -> bool:
    """Whether a provider delivers this plugin, as far as can be told now: it provides
    it, or it claims it while it cannot list it."""
    try:
        if (plugin_type, plugin_id) in provided_plugins():
            return True
    except ProviderUnavailable:
        pass
    for provider in registered_providers():
        try:
            if provider.claims(plugin_type, plugin_id):
                return True
        except Exception as exc:
            # Unknown is not "not claimed": the plugin waits for its provider.
            log.warning("hub.providers.claims_failed", {"provider": provider.name, "error": str(exc)})
            return True
    return False


# --- One operation -----------------------------------------------------------


_STAGE: ContextVar[Optional[dict[PluginKey, tuple[Path, HubPluginManifest]]]] = ContextVar(
    "hub_provided_stage", default=None,
)


def staged_root(plugin_type: str, plugin_id: str) -> Optional[Path]:
    """A provided plugin's files during the operation that staged them."""
    staged = (_STAGE.get() or {}).get((plugin_type, plugin_id))
    return staged[0] if staged is not None else None


def staged_manifest(plugin_type: str, plugin_id: str) -> Optional[HubPluginManifest]:
    """The manifest of what was staged for this plugin, while it is staged: a provider that
    lists a newer version meanwhile must not have the staged files recorded under it."""
    staged = (_STAGE.get() or {}).get((plugin_type, plugin_id))
    return staged[1] if staged is not None else None


def _owner(
    plugin_key: PluginKey,
    providers: tuple[HubPackageProvider, ...],
    listings: list[Optional[Mapping[PluginKey, HubPluginManifest]]],
) -> Optional[HubPackageProvider]:
    """The provider whose own the plugin is: the first registered that lists or claims it.

    None when none does.  Raises ProviderUnavailable when one ahead of the
    owner cannot tell whether it claims the plugin: the owner is unknown then.
    """
    for provider, listing in zip(providers, listings):
        if listing is not None and plugin_key in listing:
            return provider
        try:
            if provider.claims(*plugin_key):
                return provider
        except Exception as exc:
            raise ProviderUnavailable(
                f"{provider.name} cannot tell whether {plugin_key[0]} {plugin_key[1]} is its own: {exc}"
            ) from exc
    return None


def _owned_stage(
    providers: tuple[HubPackageProvider, ...],
    offered: list[Mapping[PluginKey, tuple[Path, HubPluginManifest]]],
) -> dict[PluginKey, tuple[Path, HubPluginManifest]]:
    """What the providers staged, each plugin as its own provider staged it.

    A copy from another provider is not taken in its place: when the owner
    staged nothing of a plugin another one did, the operation is refused.
    """
    listings: list[Optional[Mapping[PluginKey, HubPluginManifest]]] = []
    for provider in providers:
        try:
            listings.append(provider.plugins())
        except Exception:
            listings.append(None)  # unknown: what it claims decides
    stage: dict[PluginKey, tuple[Path, HubPluginManifest]] = {}
    for plugin_key in dict.fromkeys(key for staged in offered for key in staged):
        owner = _owner(plugin_key, providers, listings)
        if owner is None:
            # Listed and claimed by none: the first provider that staged it delivers it.
            owner = next(provider for provider, staged in zip(providers, offered) if plugin_key in staged)
        own = next(staged for provider, staged in zip(providers, offered) if provider is owner)
        if plugin_key not in own:
            raise ProviderUnavailable(
                f"{plugin_key[0]} {plugin_key[1]} is delivered by {owner.name}, which cannot deliver it now"
            )
        stage[plugin_key] = own[plugin_key]
    return stage


@asynccontextmanager
async def operation(plugin_type: str, plugin_id: str) -> AsyncIterator[None]:
    """Stage, for one install, update or update preview, what the providers deliver of it.

    For the rest of the operation ``plugin_root`` and ``load_manifest`` answer
    from what was staged, each plugin as its own provider staged it.  Nested
    operations (a suite installing its parts) use the stage of the outer one.
    """
    if _STAGE.get() is not None:
        yield
        return
    providers = registered_providers()
    if not providers:
        yield
        return
    async with AsyncExitStack() as stack:
        offered: list[Mapping[PluginKey, tuple[Path, HubPluginManifest]]] = []
        for provider in providers:
            offered.append(await stack.enter_async_context(provider.stage(plugin_type, plugin_id)) or {})
        # Listings and claims may read the disk or a source: off the event loop.
        stage = await asyncio.to_thread(_owned_stage, providers, offered)
        previous = _STAGE.set(stage)
        try:
            yield
        finally:
            _STAGE.reset(previous)


__all__ = [
    "HubPackageProvider",
    "ProviderUnavailable",
    "claimed",
    "clear_cache",
    "operation",
    "payload_path",
    "provided_manifest",
    "provided_plugins",
    "provided_plugins_or_empty",
    "register_provider",
    "registered_providers",
    "signature",
    "staged_manifest",
    "staged_root",
    "unregister_provider",
]
