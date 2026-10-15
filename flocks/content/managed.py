"""
Content a deployment manages, as the loaders see it.

The loaders for Agents, Skills and Workflows keep reading the local plugin
directories exactly as before.  What a deployment registers through
:func:`flocks.content.register_source` -- packages a commercial core decrypts,
a central catalogue -- is layered on top of that here:

* an id a registered source lists is **reserved**: the local item with that id
  is not loaded at all, written to or installed over.  A file dropped into a
  plugin directory therefore cannot stand in for something the deployment
  controls.  A reservation outlives the listing: when a source stops listing
  an id the item becomes unavailable, it does not hand the name back to a
  local copy.  Reservations are kept in the database so a restart does not
  forget them either;
* every other local item loads as it always did.

The loaders are mostly synchronous and run often, while the sources are async
services, so the managed side is held as a snapshot: listings for every kind,
plus the bodies of the kinds the loaders need whole (Agent and Workflow
definitions).  :func:`refresh` rebuilds it -- at startup and periodically -- and
a refresh that fails keeps the last good snapshot.  Listeners hear which items
changed, so whoever planned work from an old definition can rebuild.

Skill bodies are not held: a Skill is listed by name and summary and its body
is fetched when it is used, through :func:`fetch_pinned`, from the source that
listed it and no other, keeping one version per task (``scope``) so a task
never switches versions midway.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple, Union

from flocks.content.models import ContentBody, ContentEntry, ContentKind, ContentNotFound, ContentRef
from flocks.utils.log import Log

log = Log.create(service="content.managed")

#: The default local source's name.  It serves the plugin directories the
#: loaders already read, so it is never part of the managed layer.
LOCAL_SOURCE_NAME = "local"

#: Kinds whose bodies the loaders need whole, so they are held in the snapshot.
_BODY_KINDS = (ContentKind.AGENT, ContentKind.WORKFLOW)
_LISTED_KINDS = (ContentKind.AGENT, ContentKind.SKILL, ContentKind.WORKFLOW)

#: How many (scope, kind, id) pins are remembered.  A pin outliving its task
#: costs nothing but memory; the bound keeps that finite.
_MAX_PINS = 20_000

#: Locator a loader records for a managed item instead of a file path.
MANAGED_LOCATION_PREFIX = "content://"

#: Seconds between refreshes; FLOCKS_MANAGED_CONTENT_REFRESH_S overrides.
DEFAULT_REFRESH_INTERVAL_S = 60.0

#: Where reservations are kept across restarts: one row per id
#: (``<prefix><kind>/<quoted id>``), so instances only ever add rows and none
#: can write over another's.
RESERVATIONS_PREFIX = "content/managed/reserved/"

ChangeListener = Callable[[FrozenSet[Tuple[str, str]]], Union[None, Awaitable[None]]]


class ManagedContentUnavailable(RuntimeError):
    """The managed sources could not list their content at startup."""


def managed_location(kind: str, content_id: str) -> str:
    return f"{MANAGED_LOCATION_PREFIX}{kind}/{content_id}"


def is_managed_location(location: object) -> bool:
    return isinstance(location, str) and location.startswith(MANAGED_LOCATION_PREFIX)


def parse_managed_location(location: str) -> Optional[Tuple[str, str]]:
    """``(kind, content_id)`` for a location :func:`managed_location` made."""
    if not is_managed_location(location):
        return None
    kind, _, content_id = location[len(MANAGED_LOCATION_PREFIX):].partition("/")
    if not kind or not content_id:
        return None
    return kind, content_id


def describe_parse_error(exc: BaseException) -> Dict[str, Any]:
    """What may be logged about a definition that failed to parse.

    YAML errors quote the offending line, and that line is the deployment's
    protected content: only the error type and position are kept.
    """
    detail: Dict[str, Any] = {"error_type": type(exc).__name__}
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        detail["line"] = getattr(mark, "line", None)
        detail["column"] = getattr(mark, "column", None)
    return detail


@dataclass
class _Snapshot:
    entries: Dict[str, Dict[str, ContentEntry]] = field(default_factory=dict)
    bodies: Dict[str, Dict[str, ContentBody]] = field(default_factory=dict)
    #: Which registration listed each item; its body comes from there only.
    origins: Dict[str, Dict[str, str]] = field(default_factory=dict)
    refreshed_at: float = 0.0


def _fingerprint(entry: ContentEntry, body: Optional[ContentBody]) -> Tuple[Any, ...]:
    """What counts as a change: the version, the listing, and the body itself."""
    digest = None
    if body is not None:
        raw = body.data if body.data is not None else (body.text or "").encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
    return (
        entry.ref.version,
        entry.name,
        entry.summary,
        repr(sorted((entry.metadata or {}).items())),
        digest,
    )


class ManagedContent:
    """The managed layer: a snapshot of registered sources, reservations and version pins."""

    _snapshot = _Snapshot()
    _lock = threading.Lock()
    _refresh_lock: Optional[asyncio.Lock] = None
    _refresh_lock_loop: Optional[asyncio.AbstractEventLoop] = None
    _pins: "OrderedDict[Tuple[str, str, str], str]" = OrderedDict()
    _reserved: Dict[str, Set[str]] = {}
    #: Reservations not yet written to the database; retried on every refresh.
    _unsaved: Set[Tuple[str, str]] = set()
    _generation = 0
    _listeners: List[ChangeListener] = []

    # -- what is managed --------------------------------------------------

    @classmethod
    def _managed_registrations(cls, kind: str):
        from flocks.content.source import _ContentService

        return [
            registration
            for registration in _ContentService._sources_for(kind)
            if registration.name != LOCAL_SOURCE_NAME
        ]

    @classmethod
    def active(cls) -> bool:
        """Whether any source besides the local files is registered."""
        from flocks.content.source import registered_sources

        return any(name != LOCAL_SOURCE_NAME for name in registered_sources())

    # -- reservations -----------------------------------------------------

    @classmethod
    async def _load_reservations(cls) -> None:
        """Merge in the reservations every instance has recorded.  Raises when it cannot.

        Read on every refresh, not once: another instance may reserve an id
        (and see it withdrawn) while this one runs, and this one must not
        hand that name to a local copy in the meantime.
        """
        from urllib.parse import unquote

        from flocks.storage.storage import Storage

        keys = await Storage.list_keys(RESERVATIONS_PREFIX)
        with cls._lock:
            added = False
            for key in keys:
                kind, _, quoted = key[len(RESERVATIONS_PREFIX):].partition("/")
                if not kind or not quoted:
                    continue
                reserved = cls._reserved.setdefault(kind, set())
                content_id = unquote(quoted)
                if content_id not in reserved:
                    reserved.add(content_id)
                    added = True
            if added:
                # Loaders cached before these were known may still list a
                # local copy under one of them.
                cls._generation += 1

    @classmethod
    async def _save_reservations(cls) -> None:
        """Write reservations not yet in the database; what fails is retried next time."""
        from urllib.parse import quote

        from flocks.storage.storage import Storage

        with cls._lock:
            pending = sorted(cls._unsaved)
        for kind, content_id in pending:
            try:
                await Storage.write(f"{RESERVATIONS_PREFIX}{kind}/{quote(content_id, safe='')}", {"reserved_at": time.time()})
            except Exception as exc:
                log.warning("content.managed.reservation_unsaved", {"kind": kind, "error_type": type(exc).__name__})
                continue
            with cls._lock:
                cls._unsaved.discard((kind, content_id))

    # -- snapshot ---------------------------------------------------------

    @classmethod
    def _lock_for_loop(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if cls._refresh_lock is None or cls._refresh_lock_loop is not loop:
            cls._refresh_lock = asyncio.Lock()
            cls._refresh_lock_loop = loop
        return cls._refresh_lock

    @classmethod
    async def refresh(cls, kinds: Iterable[str] = _LISTED_KINDS) -> bool:
        """Rebuild the snapshot for ``kinds``.  Returns whether anything changed.

        A kind whose listing or a body fails keeps its previous snapshot.  An
        id that stops being listed stays reserved (see the module docstring).
        """
        if not cls.active():
            with cls._lock:
                gone = frozenset(
                    (kind, content_id)
                    for kind, items in cls._snapshot.entries.items()
                    for content_id in items
                )
                cls._snapshot = _Snapshot(refreshed_at=time.time())
                if gone:
                    cls._generation += 1
            await cls._notify(gone)
            return bool(gone)

        try:
            await cls._load_reservations()
        except Exception as exc:
            # This process's reservations still hold; reading is retried.
            log.warning("content.managed.reservations_unreadable", {"error_type": type(exc).__name__})
        changed: Set[Tuple[str, str]] = set()
        async with cls._lock_for_loop():
            for kind in kinds:
                try:
                    entries, bodies, origins = await cls._load_kind(kind)
                except Exception as exc:
                    log.error("content.managed.refresh_failed", {"kind": kind, "error_type": type(exc).__name__})
                    continue
                with cls._lock:
                    previous = cls._snapshot
                    old_entries = previous.entries.get(kind, {})
                    old_bodies = previous.bodies.get(kind, {})
                    kind_changed = {
                        (kind, content_id)
                        for content_id in set(old_entries) | set(entries)
                        if content_id not in old_entries
                        or content_id not in entries
                        or _fingerprint(old_entries[content_id], old_bodies.get(content_id))
                        != _fingerprint(entries[content_id], bodies.get(content_id))
                    }
                    reserved = cls._reserved.setdefault(kind, set())
                    for content_id in set(entries) - reserved:
                        reserved.add(content_id)
                        cls._unsaved.add((kind, content_id))
                    new_entries = dict(previous.entries)
                    new_entries[kind] = entries
                    new_bodies = dict(previous.bodies)
                    if kind in _BODY_KINDS:
                        new_bodies[kind] = bodies
                    new_origins = dict(previous.origins)
                    new_origins[kind] = origins
                    cls._snapshot = _Snapshot(
                        entries=new_entries, bodies=new_bodies, origins=new_origins, refreshed_at=time.time(),
                    )
                    if kind_changed:
                        cls._generation += 1
                changed |= kind_changed
        if cls._unsaved:
            await cls._save_reservations()
        # Every refresh, also one that changed nothing: a listener that could
        # not act on an earlier change retries then.
        await cls._notify(frozenset(changed))
        return bool(changed)

    @classmethod
    async def _load_kind(
        cls, kind: str,
    ) -> Tuple[Dict[str, ContentEntry], Dict[str, ContentBody], Dict[str, str]]:
        from flocks.content.source import _maybe_await

        entries: Dict[str, ContentEntry] = {}
        bodies: Dict[str, ContentBody] = {}
        origins: Dict[str, str] = {}
        for registration in cls._managed_registrations(kind):
            # A source that cannot list is an error for the whole kind: a
            # partial listing would make the missing items unavailable.
            listed = await _maybe_await(registration.source.list_entries(kind))
            for entry in listed or ():
                content_id = entry.ref.content_id
                if content_id in entries:
                    continue  # first source (lowest priority number) wins
                entries[content_id] = (
                    entry
                    if entry.source
                    else ContentEntry(
                        ref=entry.ref,
                        name=entry.name,
                        summary=entry.summary,
                        source=registration.name,
                        metadata=entry.metadata,
                    )
                )
                origins[content_id] = registration.name
                if kind in _BODY_KINDS:
                    body = await _maybe_await(registration.source.fetch(entry.ref))
                    if body is None:
                        raise LookupError(f"{registration.name} listed {kind}/{content_id} but cannot serve it")
                    bodies[content_id] = body if body.source else ContentBody(
                        ref=body.ref,
                        text=body.text,
                        data=body.data,
                        media_type=body.media_type,
                        source=registration.name,
                        metadata=body.metadata,
                    )
        return entries, bodies, origins

    @classmethod
    async def ensure_loaded(cls, *, attempts: int = 5, delay: float = 0.5) -> None:
        """First load, at startup: every kind must list once, or the start fails.

        Until a kind has listed there is no snapshot to fall back on, and
        serving would let local copies stand in for the deployment's items.
        Retries with backoff first: the core may still be coming up.
        """
        if not cls.active():
            return
        try:
            await cls._load_reservations()
        except Exception as exc:
            # Without them a withdrawn item's local copy would load in its place.
            raise ManagedContentUnavailable(
                f"managed content reservations could not be read ({type(exc).__name__}); "
                "refusing to serve local copies in their place"
            ) from exc
        pending = cls._unloaded(_LISTED_KINDS)
        for attempt in range(attempts):
            if not pending:
                break
            await cls.refresh(pending)
            pending = cls._unloaded(pending)
            if pending and attempt + 1 < attempts:
                await asyncio.sleep(min(delay * 2 ** attempt, 5.0))
        if pending:
            raise ManagedContentUnavailable(
                f"managed content could not be listed for: {', '.join(pending)}; "
                "refusing to serve local copies in its place"
            )
        if cls._unsaved:
            # A reservation only this process knows dies with it: withdrawn
            # before a later save, its local copy would load after a restart.
            await cls._save_reservations()
        if cls._unsaved:
            raise ManagedContentUnavailable(
                f"{len(cls._unsaved)} managed content reservation(s) could not be saved; "
                "refusing to start with reservations only this process knows"
            )

    @classmethod
    def _unloaded(cls, kinds: Iterable[str]) -> List[str]:
        with cls._lock:
            return [kind for kind in kinds if kind not in cls._snapshot.entries]

    # -- listeners --------------------------------------------------------

    @classmethod
    def add_change_listener(cls, listener: ChangeListener) -> None:
        """Call *listener* after every refresh with the ``(kind, id)`` pairs it changed or removed.

        The set is empty when nothing changed; a listener that keeps work of
        its own pending (a failed rebuild) retries it then.
        """
        with cls._lock:
            if listener not in cls._listeners:
                cls._listeners.append(listener)

    @classmethod
    def remove_change_listener(cls, listener: ChangeListener) -> None:
        with cls._lock:
            if listener in cls._listeners:
                cls._listeners.remove(listener)

    @classmethod
    async def _notify(cls, changed: FrozenSet[Tuple[str, str]]) -> None:
        with cls._lock:
            listeners = list(cls._listeners)
        for listener in listeners:
            try:
                outcome = listener(changed)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:
                log.error("content.managed.listener_failed", {"error_type": type(exc).__name__})

    # -- reading the snapshot --------------------------------------------

    @classmethod
    def entries(cls, kind: str) -> List[ContentEntry]:
        """The items currently available."""
        with cls._lock:
            return list(cls._snapshot.entries.get(kind, {}).values())

    @classmethod
    def entry(cls, kind: str, content_id: str) -> Optional[ContentEntry]:
        with cls._lock:
            return cls._snapshot.entries.get(kind, {}).get(content_id)

    @classmethod
    def body(cls, kind: str, content_id: str) -> Optional[ContentBody]:
        with cls._lock:
            return cls._snapshot.bodies.get(kind, {}).get(content_id)

    @classmethod
    def bodies(cls, kind: str) -> Dict[str, ContentBody]:
        with cls._lock:
            return dict(cls._snapshot.bodies.get(kind, {}))

    @classmethod
    def ids(cls, kind: str) -> FrozenSet[str]:
        """Every id the deployment controls: available now, or reserved earlier."""
        with cls._lock:
            return frozenset(cls._snapshot.entries.get(kind, {})) | frozenset(cls._reserved.get(kind, ()))

    @classmethod
    def available_ids(cls, kind: str) -> FrozenSet[str]:
        with cls._lock:
            return frozenset(cls._snapshot.entries.get(kind, {}))

    @classmethod
    def generation(cls) -> int:
        """Changes whenever the snapshot's contents change; loaders key caches on it."""
        with cls._lock:
            return cls._generation

    # -- bodies on demand, one version per scope ---------------------------

    @classmethod
    async def _fetch_from_origin(cls, kind: str, content_id: str, version: Optional[str]) -> ContentBody:
        """Fetch from the source that listed the item; never from another one."""
        from flocks.content.source import _maybe_await

        ref = ContentRef(kind=kind, content_id=content_id, version=version)
        with cls._lock:
            origin = cls._snapshot.origins.get(kind, {}).get(content_id)
        if origin is None:
            raise ContentNotFound(ref)
        registration = next((item for item in cls._managed_registrations(kind) if item.name == origin), None)
        if registration is None:
            raise ContentNotFound(ref)
        body = await _maybe_await(registration.source.fetch(ref))
        if body is None or (version and body.ref.version != version):
            # A pinned version must come back as exactly that version; a body
            # that cannot say which version it is cannot be shown to be it.
            raise ContentNotFound(ref)
        return body

    @classmethod
    async def fetch_pinned(cls, kind: str, content_id: str, *, scope: Optional[str]) -> ContentBody:
        """Fetch a managed item, keeping the first version ``scope`` received.

        ``scope`` is the task the content is used for (a session id); without
        one there is nothing to keep consistent and the current version is
        served.  Two first fetches racing in one scope settle on whichever
        pinned first: the other is fetched again at that version.
        """
        if not scope:
            return await cls._fetch_from_origin(kind, content_id, None)
        key = (str(scope), kind, content_id)
        with cls._lock:
            pinned = cls._pins.get(key)
            if pinned is not None:
                cls._pins.move_to_end(key)
        body = await cls._fetch_from_origin(kind, content_id, pinned)
        if pinned is not None or not body.ref.version:
            return body
        with cls._lock:
            winner = cls._pins.setdefault(key, body.ref.version)
            cls._pins.move_to_end(key)
            while len(cls._pins) > _MAX_PINS:
                cls._pins.popitem(last=False)
        if winner != body.ref.version:
            return await cls._fetch_from_origin(kind, content_id, winner)
        return body

    @classmethod
    def reset(cls) -> None:
        """Drop the snapshot, reservations, pins and listeners.  For tests and re-bootstrap."""
        with cls._lock:
            cls._snapshot = _Snapshot()
            cls._pins = OrderedDict()
            cls._reserved = {}
            cls._unsaved = set()
            cls._listeners = []
            cls._generation += 1


def refresh_interval() -> float:
    raw = os.environ.get("FLOCKS_MANAGED_CONTENT_REFRESH_S", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_REFRESH_INTERVAL_S
    except ValueError:
        value = DEFAULT_REFRESH_INTERVAL_S
    return max(value, 1.0)


async def run_refresher(interval: Optional[float] = None) -> None:
    """Keep the snapshot current until cancelled.

    A failed refresh keeps the last good snapshot (see ManagedContent.refresh),
    so the loop only logs and tries again next time.
    """
    period = interval if interval is not None else refresh_interval()
    while True:
        await asyncio.sleep(period)
        try:
            await ManagedContent.refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("content.managed.refresh_loop_failed", {"error_type": type(exc).__name__})


refresh_managed_content = ManagedContent.refresh
managed_entries = ManagedContent.entries
managed_entry = ManagedContent.entry
managed_body = ManagedContent.body
managed_ids = ManagedContent.ids
fetch_managed_pinned = ManagedContent.fetch_pinned
reset_managed_content = ManagedContent.reset


__all__ = [
    "DEFAULT_REFRESH_INTERVAL_S",
    "LOCAL_SOURCE_NAME",
    "MANAGED_LOCATION_PREFIX",
    "ManagedContent",
    "ManagedContentUnavailable",
    "RESERVATIONS_PREFIX",
    "describe_parse_error",
    "fetch_managed_pinned",
    "is_managed_location",
    "managed_body",
    "managed_entries",
    "managed_entry",
    "managed_ids",
    "managed_location",
    "parse_managed_location",
    "refresh_interval",
    "refresh_managed_content",
    "reset_managed_content",
    "run_refresher",
]
