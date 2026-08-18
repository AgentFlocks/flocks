"""Small domain records used by the first static-audit implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotFile:
    relative_path: str
    blob_digest: str
    size_bytes: int
    line_count: int
    language: str
    is_binary: bool


@dataclass(frozen=True)
class SnapshotOmission:
    relative_path: str
    reason: str
    size_bytes: int | None


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    repository_identity: str
    source_revision: str | None
    tree_digest: str
    scope_digest: str
    file_count: int
    total_bytes: int
    created_at: str
    root_path: str
    omitted_file_count: int = 0
    target_kind: str = "directory_snapshot"
    display_name: str = "snapshot"
    include_paths: tuple[str, ...] = (".",)
    exclude_patterns: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("root_path", None)
        return data


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    scan_id: str
    work_unit_id: str | None
    snapshot_id: str
    role: str
