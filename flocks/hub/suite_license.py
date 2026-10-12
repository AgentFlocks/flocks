"""Licensing of "pro" edition scene suites.

Each pro-edition scene suite is licensed on its own: the registered licence
checker answers for it under the feature ``scene_suite:<suite id>``. Whether
the installation's own licence also has to be in force for a suite is the
checker's decision, not this module's.

The built-in checker (status "oss") licenses no suite: a pro-edition suite
does not install under it, and one already present (a development checkout,
typically) is left alone at run time. Suite licences are enforced on use only
when a checker that licenses suites is registered.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from flocks.hub.catalog import list_catalog, load_manifest, manifest_path, scene_suite_workspace_owners
from flocks.hub.sealed import sealed_page_ids, sealed_signature
from flocks.license import is_pro_edition_status, license_feature_status, license_status
from flocks.utils.log import Log

log = Log.create(service="hub.suite-license")

SUITE_FEATURE_PREFIX = "scene_suite:"


def suite_feature(suite_id: str) -> str:
    """The licence feature a suite's own licence answers for."""
    return f"{SUITE_FEATURE_PREFIX}{suite_id}"


@dataclass(frozen=True)
class SuiteLicense:
    """A suite's licence verdict, as the scene views show it."""

    licensed: bool
    state: Optional[str] = None
    expires_at: Optional[int] = None  # epoch ms; None when there is no expiry
    license_id: Optional[str] = None


async def edition_status() -> dict[str, Any]:
    """The platform licence status; ``{}`` when the checker cannot answer."""
    try:
        return dict(await license_status() or {})
    except Exception as exc:  # a checker that cannot answer is not an active licence
        log.warning("hub.suite_license.status_failed", {"error": str(exc)})
        return {}


def is_open_edition(status: dict[str, Any]) -> bool:
    """True under the built-in checker, which reports status "oss"."""
    return str(status.get("status") or "").lower() == "oss"


def pro_platform_active(status: dict[str, Any]) -> bool:
    return is_pro_edition_status(status)


async def suite_license(suite_id: str) -> SuiteLicense:
    """The current verdict on one suite's own licence."""
    try:
        verdict = dict(await license_feature_status(suite_feature(suite_id)) or {})
    except Exception as exc:
        log.warning("hub.suite_license.feature_failed", {"suite": suite_id, "error": str(exc)})
        return SuiteLicense(licensed=False, state="unavailable")
    details = verdict.get("details") or {}
    expires = verdict.get("expires_unix_ms") or None
    return SuiteLicense(
        licensed=bool(verdict.get("active")),
        state=str(verdict.get("status") or "") or None,
        expires_at=int(expires) if expires else None,
        license_id=str(details.get("license_id") or "") or None,
    )


class SuiteOwnershipUnknown(RuntimeError):
    """Whether something belongs to a pro-edition suite could not be decided.

    Kept apart from "belongs to no such suite": when a licence is enforced,
    an undecided question must not read as "no licence needed".
    """


def _plugin_edition(plugin_type: str, plugin_id: str) -> Optional[str]:
    """The plugin's edition; None when no such plugin exists.

    Raises SuiteOwnershipUnknown when the plugin exists but its manifest
    cannot be read: that is undecided, not "open edition".
    """
    try:
        return getattr(load_manifest(plugin_type, plugin_id), "edition", "oss")
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise SuiteOwnershipUnknown(f"{plugin_type} {plugin_id}: {exc}") from exc


def _suite_edition(suite_id: str) -> str:
    """A suite's edition; raises SuiteOwnershipUnknown when it cannot be read."""
    try:
        return getattr(load_manifest("component", suite_id), "edition", "oss")
    except Exception as exc:
        raise SuiteOwnershipUnknown(f"suite {suite_id}: {exc}") from exc


def _suite_name(suite_id: str) -> str:
    try:
        manifest = load_manifest("component", suite_id)
    except Exception:
        return suite_id
    return manifest.nameCn or manifest.name or suite_id


def owning_pro_suites(plugin_type: str, plugin_id: str, edition: Optional[str] = None) -> list[str]:
    """The pro-edition suites whose licence covers this plugin.

    A pro-edition suite covers itself. A pro-edition plugin shipped inside
    such suites is covered by those suites; one shipped by no suite needs only
    the installation's licence. An open-edition plugin is never gated, even
    when a pro-edition suite also ships it.

    *edition* is the plugin's edition when the caller has read it already;
    otherwise it is read here, and a manifest that cannot be read raises
    SuiteOwnershipUnknown rather than counting as open edition.
    """
    if edition is None:
        edition = _plugin_edition(plugin_type, plugin_id)
    if edition != "pro":
        return []
    if plugin_type == "component":
        return [plugin_id]
    owners: list[str] = []
    for entry in list_catalog(plugin_type="component"):
        if entry.edition != "pro":
            continue
        try:
            refs = load_manifest("component", entry.id).components
        except Exception as exc:
            # A pro suite whose manifest cannot be read might own this plugin.
            raise SuiteOwnershipUnknown(f"suite {entry.id}: {exc}") from exc
        if any((ref.type, ref.id) == (plugin_type, plugin_id) for ref in refs):
            owners.append(entry.id)
    return owners


@dataclass(frozen=True)
class InstallDecision:
    allowed: bool
    reason: Optional[str] = None  # "edition_required" | "suite_unlicensed" | "ownership_unknown"
    message: Optional[str] = None


async def install_decision(plugin_type: str, plugin_id: str) -> InstallDecision:
    """May this plugin be installed or updated now?

    Pro-edition plugins need an active licence from a registered checker;
    those that belong to pro-edition suites need each of those suites
    licensed as well. The caller words the edition refusal.
    """
    status = await edition_status()
    try:
        edition = await asyncio.to_thread(_plugin_edition, plugin_type, plugin_id)
    except SuiteOwnershipUnknown as exc:
        if is_open_edition(status):
            return InstallDecision(allowed=True)
        log.warning("hub.suite_license.ownership_unknown", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        return InstallDecision(
            allowed=False,
            reason="ownership_unknown",
            message="暂时无法确认这个插件属于哪个场景套件，请稍后重试。",
        )
    if edition != "pro":
        return InstallDecision(allowed=True)
    if not pro_platform_active(status):
        return InstallDecision(allowed=False, reason="edition_required")
    # Reads every suite manifest in the catalog: off the event loop.
    try:
        # The edition read above, not a second read that could fail
        # differently and come back as "open edition".
        owners = await asyncio.to_thread(owning_pro_suites, plugin_type, plugin_id, edition)
    except SuiteOwnershipUnknown as exc:
        log.warning("hub.suite_license.ownership_unknown", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        return InstallDecision(
            allowed=False,
            reason="ownership_unknown",
            message="暂时无法确认这个插件属于哪个场景套件，请稍后重试。",
        )
    for suite_id in owners:
        verdict = await suite_license(suite_id)
        if not verdict.licensed:
            return InstallDecision(
                allowed=False,
                reason="suite_unlicensed",
                message=(
                    f"「{_suite_name(suite_id)}」需要单独授权，当前未授权或授权已失效（{verdict.state or 'unlicensed'}）。"
                    "请在系统设置的「场景套件授权」中导入该套件的授权文件。"
                ),
            )
    return InstallDecision(allowed=True)


# Page id -> owning suite id, so the per-request page routes do not walk the
# page roots and the catalog on every asset. Short-lived, bounded, and only
# for pages that exist: installing or removing a suite changes the answer,
# and ids made up by a client must not grow it. Each entry also carries the
# ownership signature it was read under (see _ownership_signature).
_PAGE_SUITE_TTL_S = 10.0
_PAGE_SUITE_CACHE_MAX = 512
_page_suite_cache: dict[str, tuple[float, tuple, Optional[str]]] = {}


class _PageIndex(dict):
    """Page id -> the pro-edition suite shipping it, as read at ``read_at``
    (``time.monotonic()``). The read time travels with the index, so an
    answer taken from it expires with it, whichever index is cached by the
    time that answer is stored."""

    def __init__(self, read_at: float) -> None:
        super().__init__()
        self.read_at = read_at


# Page ids each pro-edition suite ships, read from the hub's packages and the
# installed workspaces, with the ownership signature they were read under.
# The signature catches changes to what the hub ships; pages added to an
# installed workspace (or copied out of one) change none of it, so the index
# also expires after _PAGE_SUITE_TTL_S, like the page cache above.
_shipped_pages: Optional[tuple[tuple, _PageIndex]] = None
# Bumped on every clear, so an index built from data read before a clear is
# not written back over it.
_shipped_generation = 0
_cache_lock = threading.Lock()


def _ownership_signature() -> tuple:
    """What decides which pages the pro-edition suites ship: the sealed
    packages on offer, every hub index (the bundled one and each registered
    extension root, so registering or dropping a root counts) and the install
    records. Read from shared state on every lookup, so a change made by
    another worker, or without anyone clearing the caches, is seen as well."""
    from flocks.hub.catalog import _index_signatures, _path_signature
    from flocks.hub.local import _record_path

    return (sealed_signature(), _index_signatures(), _path_signature(_record_path()))


def clear_page_suite_cache() -> None:
    global _shipped_pages, _shipped_generation
    with _cache_lock:
        _page_suite_cache.clear()
        _shipped_pages = None
        _shipped_generation += 1


def is_pro_suite(suite_id: Optional[str]) -> bool:
    """Whether the suite is a pro-edition one, licensed on its own.

    Raises SuiteOwnershipUnknown when the suite's manifest cannot be read.
    """
    return bool(suite_id) and _suite_edition(suite_id) == "pro"


def pro_suite_for_workspace(workspace_id: Optional[str]) -> Optional[str]:
    """The pro-edition suite that ships this workspace, if one does."""
    if not workspace_id:
        return None
    suite_id = scene_suite_workspace_owners().get(workspace_id)
    return suite_id if is_pro_suite(suite_id) else None


def _bundled_page_ids(webui_id: str) -> set[str]:
    """Page ids inside a webui package as the hub ships it.

    A sealed package lists its pages itself; while a sealed source cannot say
    what it has, this raises rather than answer "no pages".
    """
    sealed_ids = sealed_page_ids(webui_id)
    if sealed_ids is not None:
        return sealed_ids
    root = manifest_path("webui", webui_id).parent
    if not root.is_dir():
        return set()
    ids: set[str] = set()
    for page_manifest in root.rglob("manifest.json"):
        if page_manifest.parent == root:
            continue
        page_id = json.loads(page_manifest.read_text(encoding="utf-8")).get("id")
        if isinstance(page_id, str) and page_id:
            ids.add(page_id)
    return ids


def _installed_page_ids(workspace_id: str, store: Any) -> set[str]:
    """Page ids inside the installed workspace of that id, if it is installed."""
    for workspace in store.list_workspaces():
        if workspace.id == workspace_id:
            return {page.id for page in workspace.pages}
    return set()


def shipped_page_owners(store: Any) -> dict[str, str]:
    """Page id -> pro-edition suite, for every page such a suite ships.

    Ownership follows the page id, not where the page happens to live: a
    suite page copied to the user root on save, or imported again as a
    standalone page under the same id, is still that suite's page. A suite's
    pages are the ones in its package as the hub ships it plus the ones in
    its installed workspace, so either source alone is enough. The index
    says when it was read (``read_at``).
    """
    global _shipped_pages
    signature = _ownership_signature()
    read_at = time.monotonic()
    with _cache_lock:
        if (
            _shipped_pages is not None
            and _shipped_pages[0] == signature
            and read_at - _shipped_pages[1].read_at < _PAGE_SUITE_TTL_S
        ):
            return _shipped_pages[1]
        generation = _shipped_generation
    owners = _PageIndex(read_at)
    try:
        for entry in list_catalog(plugin_type="component"):
            if entry.edition != "pro":
                continue
            for ref in load_manifest("component", entry.id).components:
                if ref.type == "webui":
                    for page_id in _bundled_page_ids(ref.id) | _installed_page_ids(ref.id, store):
                        owners.setdefault(page_id, entry.id)
    except Exception as exc:
        raise SuiteOwnershipUnknown(f"suite pages: {exc}") from exc
    with _cache_lock:
        if generation == _shipped_generation:
            _shipped_pages = (signature, owners)
    return owners


def pro_suite_for_page(page_id: str, store: Any) -> Optional[str]:
    """The pro-edition suite a page belongs to, or None when it belongs to none.

    Raises SuiteOwnershipUnknown when that cannot be decided; nothing
    undecided is cached.
    """
    now = time.monotonic()
    try:
        signature = _ownership_signature()
    except Exception as exc:
        raise SuiteOwnershipUnknown(f"page {page_id}: {exc}") from exc
    with _cache_lock:
        hit = _page_suite_cache.get(page_id)
    if hit is not None and now - hit[0] < _PAGE_SUITE_TTL_S and hit[1] == signature:
        return hit[2]
    try:
        owners = shipped_page_owners(store)
        suite_id = owners.get(page_id)
        if suite_id is None:
            suite_id = pro_suite_for_workspace(store.workspace_id_for_page(page_id))
        exists = suite_id is not None or store.page_exists(page_id)
    except SuiteOwnershipUnknown:
        raise
    except Exception as exc:
        raise SuiteOwnershipUnknown(f"page {page_id}: {exc}") from exc
    if exists:
        # An answer taken from the index is no fresher than the index: it
        # expires with it, not a full TTL after this lookup. (An index that
        # does not say when it was read counts from this lookup.)
        read_at = getattr(owners, "read_at", None)
        stamp = min(now, read_at) if read_at is not None else now
        with _cache_lock:
            if len(_page_suite_cache) >= _PAGE_SUITE_CACHE_MAX:
                for key, (entry_stamp, read_under, _) in list(_page_suite_cache.items()):
                    if now - entry_stamp >= _PAGE_SUITE_TTL_S or read_under != signature:
                        del _page_suite_cache[key]
                if len(_page_suite_cache) >= _PAGE_SUITE_CACHE_MAX:
                    _page_suite_cache.clear()
            _page_suite_cache[page_id] = (stamp, signature, suite_id)
    return suite_id


async def runtime_suite_license(suite_id: str, status: Optional[dict[str, Any]] = None) -> Optional[SuiteLicense]:
    """A pro-edition suite's verdict for using it now; None when nothing is enforced.

    Nothing is enforced under the built-in checker (see the module docstring).
    """
    status = await edition_status() if status is None else status
    if is_open_edition(status):
        return None
    return await suite_license(suite_id)
