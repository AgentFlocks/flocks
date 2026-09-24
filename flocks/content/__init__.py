"""
Content loading through one generic interface.

Agent definitions, Skill bodies, Workflow definitions and tool descriptors are
all fetched the same way, so a deployment can change where content comes from
without touching the code that consumes it.  The local filesystem source is
installed by default; :func:`register_source` adds others.
"""

from __future__ import annotations

from flocks.content.local import LocalContentSource, default_roots
from flocks.content.models import (
    ContentBody,
    ContentEntry,
    ContentKind,
    ContentNotFound,
    ContentRef,
    ContentSourceError,
)
from flocks.content.source import (
    ContentSource,
    fetch_content,
    list_entries,
    register_source,
    registered_sources,
    reset_sources,
    try_fetch_content,
    unregister_source,
)

_DEFAULT_SOURCE_NAME = "local"


def install_default_sources() -> None:
    """Register the local filesystem source if nothing has registered it yet.

    Idempotent, so a host may call it from several bootstrap paths without
    having to coordinate which one runs first.
    """

    if _DEFAULT_SOURCE_NAME in registered_sources():
        return
    # Highest priority number = consulted last, so any source a deployment
    # registers takes precedence over plain local files.
    register_source(LocalContentSource(name=_DEFAULT_SOURCE_NAME), priority=1000)


__all__ = [
    "ContentBody",
    "ContentEntry",
    "ContentKind",
    "ContentNotFound",
    "ContentRef",
    "ContentSource",
    "ContentSourceError",
    "LocalContentSource",
    "default_roots",
    "fetch_content",
    "install_default_sources",
    "list_entries",
    "register_source",
    "registered_sources",
    "reset_sources",
    "try_fetch_content",
    "unregister_source",
]
