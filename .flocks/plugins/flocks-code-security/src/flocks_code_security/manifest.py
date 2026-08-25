"""Host-computed repository manifests bound to immutable snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from flocks_code_security.models import (
    ManifestComponent,
    RepositoryManifest,
    SnapshotFile,
    SnapshotOmission,
)
from flocks_code_security.store import ScanStore


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _components(files: list[SnapshotFile]) -> tuple[ManifestComponent, ...]:
    counts: dict[str, list[int]] = {".": [0, 0]}
    children: dict[str, set[str]] = {".": set()}
    for item in files:
        counts["."][0] += 1
        counts["."][1] += item.size_bytes
        parts = item.relative_path.split("/")
        parent = "."
        for depth in range(1, len(parts)):
            path = "/".join(parts[:depth])
            counts.setdefault(path, [0, 0])
            counts[path][0] += 1
            counts[path][1] += item.size_bytes
            children.setdefault(parent, set()).add(path)
            children.setdefault(path, set())
            parent = path
    return tuple(
        ManifestComponent(
            path=path,
            file_count=counts[path][0],
            total_bytes=counts[path][1],
            child_count=len(children.get(path, set())),
        )
        for path in sorted(counts, key=lambda value: (value != ".", value))
    )


def _digest_payload(
    snapshot_id: str,
    tree_digest: str,
    scope_digest: str,
    files: list[SnapshotFile],
    omissions: list[SnapshotOmission],
    languages: tuple[tuple[str, int], ...],
    components: tuple[ManifestComponent, ...],
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "tree_digest": tree_digest,
        "scope_digest": scope_digest,
        "files": [
            {
                "path": item.relative_path,
                "blob_digest": item.blob_digest,
                "size_bytes": item.size_bytes,
                "line_count": item.line_count,
                "language": item.language,
                "is_binary": item.is_binary,
            }
            for item in files
        ],
        "omissions": [
            {
                "path": item.relative_path,
                "reason": item.reason,
                "size_bytes": item.size_bytes,
            }
            for item in omissions
        ],
        "languages": dict(languages),
        "components": [item.public_dict() for item in components],
    }


class RepositoryManifestService:
    def __init__(self, store: ScanStore):
        self.store = store

    def get_or_build(self, snapshot_id: str) -> RepositoryManifest:
        existing = self.store.get_repository_manifest(snapshot_id)
        return existing if existing is not None else self.build(snapshot_id)

    def build(self, snapshot_id: str) -> RepositoryManifest:
        existing = self.store.get_repository_manifest(snapshot_id)
        if existing is not None:
            return existing
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError("Snapshot not found")
        files = self.store.list_snapshot_files(snapshot_id)
        omissions = self.store.list_snapshot_omissions(snapshot_id)
        if (
            len(files) != snapshot.file_count
            or sum(item.size_bytes for item in files) != snapshot.total_bytes
            or len(omissions) != snapshot.omitted_file_count
        ):
            raise ValueError("Snapshot metadata does not match its stored inventory")
        languages = tuple(sorted(Counter(item.language for item in files).items()))
        components = _components(files)
        digest = hashlib.sha256(
            _canonical_bytes(
                _digest_payload(
                    snapshot_id,
                    snapshot.tree_digest,
                    snapshot.scope_digest,
                    files,
                    omissions,
                    languages,
                    components,
                )
            )
        ).hexdigest()
        manifest = RepositoryManifest(
            manifest_id=f"manifest_{digest[:32]}",
            snapshot_id=snapshot_id,
            manifest_digest=digest,
            file_count=len(files),
            total_bytes=sum(item.size_bytes for item in files),
            omitted_file_count=len(omissions),
            languages=languages,
            components=components,
            created_at=datetime.now(timezone.utc).isoformat(),
            files=tuple(files),
            omissions=tuple(omissions),
        )
        self.store.save_repository_manifest(manifest)
        persisted = self.store.get_repository_manifest(snapshot_id)
        if persisted is None:
            raise ValueError("Repository manifest was not persisted")
        return persisted
