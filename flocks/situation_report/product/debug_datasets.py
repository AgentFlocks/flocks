"""Validated frozen datasets for repeatable situation-report debug Sessions."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .backend_sync import MaterialDetailResponse
from .contracts import SAFE_IDENTIFIER
from .snapshots import SnapshotDownloadError, _validate_materials, _validate_template


class DebugDatasetError(RuntimeError):
    """A frozen debug dataset is missing, unsafe, or internally inconsistent."""


class DebugDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    dataset_id: str = Field(alias="datasetID")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    language: Literal["zh-CN", "en-US"]
    source_session_id: str | None = Field(default=None, alias="sourceSessionID")
    captured_at: str = Field(alias="capturedAt")
    template_sha256: str = Field(alias="templateSHA256")
    materials_sha256: str = Field(alias="materialsSHA256")
    material_details_sha256: str = Field(alias="materialDetailsSHA256")
    material_count: int = Field(alias="materialCount", ge=1)
    material_detail_count: int = Field(alias="materialDetailCount", ge=0)
    source_counts: dict[str, int] = Field(alias="sourceCounts")

    @field_validator("dataset_id")
    @classmethod
    def validate_dataset_id(cls, value: str) -> str:
        if not SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("datasetID is invalid")
        return value

    @field_validator(
        "template_sha256",
        "materials_sha256",
        "material_details_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("dataset SHA-256 fields must contain 64 lowercase hex characters")
        return normalized

    @field_validator("source_counts")
    @classmethod
    def validate_source_counts(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"REPORT", "VULN", "DARKWEB", "TELEGRAM"}
        if set(value) - allowed or any(count < 0 for count in value.values()):
            raise ValueError("sourceCounts contains an invalid source type or count")
        return value


@dataclass(frozen=True)
class LoadedDebugDataset:
    manifest: DebugDatasetManifest
    template: bytes
    materials: bytes
    material_details: bytes


def debug_dataset_root() -> Path | None:
    configured = os.getenv("SITUATION_REPORT_DEBUG_DATASET_ROOT", "").strip()
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _dataset_file(directory: Path, name: str) -> Path:
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise DebugDatasetError(f"Frozen dataset file is missing or unsafe: {name}")
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError as exc:
        raise DebugDatasetError(f"Frozen dataset file escapes its directory: {name}") from exc
    return path


def _parse_materials(content: bytes) -> tuple[set[str], dict[str, int]]:
    rows: set[str] = set()
    counts: dict[str, int] = {}
    # JSONL records are delimited by LF. ``splitlines()`` also treats Unicode
    # controls such as U+0085 as separators, even when they occur inside a
    # valid JSON string returned by the backend.
    for line in content.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        value = json.loads(line)
        source_type = str(value["source_type"])
        source_id = str(value["source_id"]).strip()
        rows.add(f"{source_type}:{source_id}")
        counts[source_type] = counts.get(source_type, 0) + 1
    return rows, counts


def _parse_details(content: bytes) -> set[str]:
    identities: set[str] = set()
    for line_number, line in enumerate(content.decode("utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            detail = MaterialDetailResponse.model_validate_json(line)
        except Exception as exc:
            raise DebugDatasetError(
                f"material-details.jsonl line {line_number} violates the backend contract: {exc}"
            ) from exc
        identity = f"{detail.source_type}:{detail.source_id}"
        if identity in identities:
            raise DebugDatasetError(f"material-details.jsonl duplicates {identity}")
        identities.add(identity)
    return identities


def load_debug_dataset(dataset_id: str) -> LoadedDebugDataset:
    if not SAFE_IDENTIFIER.fullmatch(dataset_id):
        raise DebugDatasetError("datasetID is invalid")
    root = debug_dataset_root()
    if root is None:
        raise DebugDatasetError("SITUATION_REPORT_DEBUG_DATASET_ROOT is not configured")
    candidate = root / dataset_id
    if candidate.is_symlink():
        raise DebugDatasetError("Frozen dataset directory cannot be a symlink")
    directory = candidate.resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise DebugDatasetError("Dataset directory escapes the configured root") from exc
    if not directory.is_dir():
        raise DebugDatasetError(f"Frozen dataset does not exist: {dataset_id}")

    manifest_path = _dataset_file(directory, "dataset.json")
    template_path = _dataset_file(directory, "template.md")
    materials_path = _dataset_file(directory, "materials.jsonl")
    details_path = _dataset_file(directory, "material-details.jsonl")
    try:
        manifest = DebugDatasetManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DebugDatasetError(f"dataset.json is invalid: {exc}") from exc
    if manifest.dataset_id != dataset_id:
        raise DebugDatasetError("dataset.json datasetID does not match its directory")

    template = template_path.read_bytes()
    materials = materials_path.read_bytes()
    material_details = details_path.read_bytes()
    try:
        _validate_template(template_path)
        material_count = _validate_materials(materials_path)
    except (OSError, SnapshotDownloadError) as exc:
        raise DebugDatasetError(f"Frozen template or materials are invalid: {exc}") from exc
    selected_ids, source_counts = _parse_materials(materials)
    detail_ids = _parse_details(material_details)
    undeclared_details = sorted(detail_ids - selected_ids)
    if undeclared_details:
        raise DebugDatasetError(
            f"Material details contain identities outside the selected set: {undeclared_details}"
        )

    actual: dict[str, Any] = {
        "templateSHA256": _digest(template),
        "materialsSHA256": _digest(materials),
        "materialDetailsSHA256": _digest(material_details),
        "materialCount": material_count,
        "materialDetailCount": len(detail_ids),
        "sourceCounts": source_counts,
    }
    expected: dict[str, Any] = {
        "templateSHA256": manifest.template_sha256,
        "materialsSHA256": manifest.materials_sha256,
        "materialDetailsSHA256": manifest.material_details_sha256,
        "materialCount": manifest.material_count,
        "materialDetailCount": manifest.material_detail_count,
        "sourceCounts": manifest.source_counts,
    }
    if actual != expected:
        raise DebugDatasetError(
            "Frozen dataset hashes or counts do not match dataset.json"
        )
    return LoadedDebugDataset(
        manifest=manifest,
        template=template,
        materials=materials,
        material_details=material_details,
    )


def list_debug_datasets() -> list[DebugDatasetManifest]:
    root = debug_dataset_root()
    if root is None:
        return []
    if not root.is_dir():
        raise DebugDatasetError("Configured frozen dataset root does not exist")
    manifests = [
        load_debug_dataset(path.name).manifest
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.is_symlink() and SAFE_IDENTIFIER.fullmatch(path.name)
    ]
    return manifests
