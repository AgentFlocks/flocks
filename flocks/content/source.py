"""
Generic content-source interface and registry.

Flocks loads Agent definitions, Skill bodies, Workflow definitions and tool
descriptors through this one interface.  The default source reads the local
plugin directories; a deployment may register additional sources that resolve
the same refs some other way.  Nothing above this interface knows which source
answered, so callers stay identical across deployments.

Ordering
--------
Sources are consulted by ascending ``priority`` and the first one that resolves
a ref wins.  A source registered as *exclusive* for a kind takes that kind
alone: lower-priority sources are not consulted for it, which is what keeps a
file dropped into a local directory from shadowing a managed item of the same
id.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Protocol, Sequence, runtime_checkable

from flocks.content.models import (
    ContentBody,
    ContentEntry,
    ContentNotFound,
    ContentRef,
    ContentSourceError,
)
from flocks.extensions import ensure_callable_methods
from flocks.utils.log import Log

log = Log.create(service="content")

_SOURCE_METHODS = ("name", "supports", "list_entries", "fetch")


@runtime_checkable
class ContentSource(Protocol):
    """Contract every content source implements."""

    @property
    def name(self) -> str:
        """Stable identifier, used in logs and in results' ``source`` field."""
        ...

    def supports(self, kind: str) -> bool:
        """Whether this source can serve the given content kind."""
        ...

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        """Directory listing for one kind.  Must not read bodies."""
        ...

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        """Resolve one ref, or return ``None`` if this source does not have it."""
        ...


@dataclass(frozen=True)
class _Registration:
    source: ContentSource
    priority: int
    exclusive_kinds: frozenset[str]

    @property
    def name(self) -> str:
        return self.source.name


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class _ContentService:
    """Registry and resolution order for content sources."""

    _registrations: List[_Registration] = []

    # -- registration -----------------------------------------------------

    @classmethod
    def register_source(
        cls,
        source: ContentSource,
        *,
        priority: int = 100,
        exclusive_kinds: Iterable[str] | None = None,
    ) -> None:
        if source is None:
            raise ValueError("content source 不能为空")
        ensure_callable_methods(
            source,
            [name for name in _SOURCE_METHODS if name != "name"],
            label="content source",
        )
        if not getattr(source, "name", None):
            raise ValueError("content source 必须提供 name")

        exclusive = frozenset(exclusive_kinds or ())
        registration = _Registration(source=source, priority=priority, exclusive_kinds=exclusive)

        existing = [item for item in cls._registrations if item.name == registration.name]
        if existing:
            raise ValueError(f"内容来源已注册: {registration.name}")

        cls._registrations = sorted(
            [*cls._registrations, registration],
            key=lambda item: (item.priority, item.name),
        )
        log.info(
            "content.source.registered",
            {
                "source": registration.name,
                "priority": priority,
                "exclusive_kinds": sorted(exclusive),
            },
        )

    @classmethod
    def unregister_source(cls, name: str) -> bool:
        remaining = [item for item in cls._registrations if item.name != name]
        removed = len(remaining) != len(cls._registrations)
        cls._registrations = remaining
        return removed

    @classmethod
    def registered_sources(cls) -> List[str]:
        return [item.name for item in cls._registrations]

    @classmethod
    def reset(cls) -> None:
        """Drop every registration.  Intended for tests and re-bootstrap."""
        cls._registrations = []

    # -- resolution -------------------------------------------------------

    @classmethod
    def _sources_for(cls, kind: str) -> List[_Registration]:
        """Registrations that may answer for ``kind``, in consultation order.

        An exclusive registration truncates the list: it and anything ahead of
        it may answer, everything behind it is shadowed for this kind.
        """
        ordered: List[_Registration] = []
        for registration in cls._registrations:
            try:
                if not registration.source.supports(kind):
                    continue
            except Exception as exc:  # a broken source must not hide the others
                log.error(
                    "content.source.supports_failed",
                    {"source": registration.name, "kind": kind, "error": str(exc)},
                )
                continue
            ordered.append(registration)
            if kind in registration.exclusive_kinds:
                break
        return ordered

    @classmethod
    async def list_entries(cls, kind: str) -> List[ContentEntry]:
        """Merged directory listing for ``kind``, first source wins per id."""
        merged: Dict[str, ContentEntry] = {}
        for registration in cls._sources_for(kind):
            try:
                entries = await _maybe_await(registration.source.list_entries(kind))
            except Exception as exc:
                log.error(
                    "content.source.list_failed",
                    {"source": registration.name, "kind": kind, "error": str(exc)},
                )
                continue
            for entry in entries or ():
                key = entry.ref.content_id
                if key in merged:
                    continue
                merged[key] = (
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
        return list(merged.values())

    @classmethod
    async def fetch(cls, ref: ContentRef) -> ContentBody:
        """Resolve ``ref`` through the first source that has it.

        A source that raises is reported rather than skipped: a source claiming
        a ref and then failing is an error worth surfacing, not a reason to
        silently fall through to a different version of the same content.
        """
        candidates = cls._sources_for(ref.kind)
        if not candidates:
            raise ContentNotFound(ref)

        for registration in candidates:
            try:
                body = await _maybe_await(registration.source.fetch(ref))
            except Exception as exc:
                raise ContentSourceError(registration.name, ref, exc) from exc
            if body is None:
                continue
            if body.source:
                return body
            return ContentBody(
                ref=body.ref,
                text=body.text,
                data=body.data,
                media_type=body.media_type,
                source=registration.name,
                metadata=body.metadata,
            )
        raise ContentNotFound(ref)

    @classmethod
    async def try_fetch(cls, ref: ContentRef) -> Optional[ContentBody]:
        """Same as :meth:`fetch` but returns ``None`` instead of raising on miss."""
        try:
            return await cls.fetch(ref)
        except ContentNotFound:
            return None


register_source = _ContentService.register_source
unregister_source = _ContentService.unregister_source
registered_sources = _ContentService.registered_sources
reset_sources = _ContentService.reset
list_entries = _ContentService.list_entries
fetch_content = _ContentService.fetch
try_fetch_content = _ContentService.try_fetch


__all__ = [
    "ContentSource",
    "fetch_content",
    "list_entries",
    "register_source",
    "registered_sources",
    "reset_sources",
    "try_fetch_content",
    "unregister_source",
]
