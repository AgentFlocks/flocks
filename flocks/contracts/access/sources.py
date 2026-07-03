"""Reusable source helpers for page data access contract bindings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flocks.contracts.webui.store import WebUIPagesStore


@dataclass(frozen=True)
class WebUIPageAssetSource:
    page_id: str
    root: Path
    allowlist_roots: tuple[Path, ...]


class WebUIPageAssetSourceResolver:
    """Resolve a WebUI page assets directory as a contract data source."""

    def __init__(self, store: WebUIPagesStore | None = None) -> None:
        self._store = store or WebUIPagesStore()

    def resolve(self, page_id: str) -> WebUIPageAssetSource:
        root = self._store.asset_path(page_id, "")
        return WebUIPageAssetSource(
            page_id=page_id,
            root=root,
            allowlist_roots=(root,),
        )
