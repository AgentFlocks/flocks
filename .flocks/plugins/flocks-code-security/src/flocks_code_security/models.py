"""Small domain records used by the first static-audit implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
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
    copy_source: bool = True

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("root_path", None)
        return data


@dataclass(frozen=True)
class ManifestComponent:
    path: str
    file_count: int
    total_bytes: int
    child_count: int

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepositoryManifest:
    manifest_id: str
    snapshot_id: str
    manifest_digest: str
    file_count: int
    total_bytes: int
    omitted_file_count: int
    languages: tuple[tuple[str, int], ...]
    components: tuple[ManifestComponent, ...]
    created_at: str
    files: tuple[SnapshotFile, ...] = ()
    omissions: tuple[SnapshotOmission, ...] = ()

    def public_dict(self, *, component_limit: int = 500) -> dict[str, Any]:
        limit = max(0, component_limit)
        return {
            "manifest_id": self.manifest_id,
            "snapshot_id": self.snapshot_id,
            "manifest_digest": self.manifest_digest,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "omitted_file_count": self.omitted_file_count,
            "languages": dict(self.languages),
            "components": [
                item.public_dict() for item in self.components[:limit]
            ],
            "component_count": len(self.components),
            "components_truncated": len(self.components) > limit,
        }


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    scan_id: str
    work_unit_id: str | None
    snapshot_id: str
    role: str
    attempt_id: str | None = None


@dataclass(frozen=True)
class ExecutionCapsule:
    scan_id: str
    snapshot_id: str
    work_unit_id: str
    attempt_id: str
    phase: str
    role: str
    agent_name: str
    session_id: str
    provider_id: str | None
    model_id: str | None
    toolset_digest: str
    ruleset_digest: str
    scope_digest: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        encoded = json.dumps(
            self.payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def public_dict(self) -> dict[str, Any]:
        return {**self.payload(), "capsule_digest": self.digest()}


@dataclass(frozen=True)
class CoverageRecord:
    relative_path: str
    state: str
    reason: str | None
    receipt_digest: str | None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageAttestation:
    attestation_id: str
    work_unit_id: str
    attempt_id: str
    policy: str
    completeness: str
    assigned_count: int
    read_complete_count: int
    failed_count: int
    unexamined_count: int
    attestation_digest: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "attestation_id": self.attestation_id,
            "work_unit_id": self.work_unit_id,
            "attempt_id": self.attempt_id,
            "policy": self.policy,
            "completeness": self.completeness,
            "counts": {
                "assigned": self.assigned_count,
                "read_complete": self.read_complete_count,
                "failed": self.failed_count,
                "unexamined": self.unexamined_count,
            },
            "attestation_digest": self.attestation_digest,
        }
