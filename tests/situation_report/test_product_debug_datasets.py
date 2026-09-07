from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flocks.situation_report.product.debug_datasets import (
    DebugDatasetError,
    list_debug_datasets,
    load_debug_dataset,
)


def _write_dataset(
    root: Path,
    *,
    detail_source_id: str = "source-1",
) -> Path:
    directory = root / "D01"
    directory.mkdir(parents=True)
    template = b"# Template\n\n## Overview\n"
    materials = (
        json.dumps(
            {
                "source_type": "DARKWEB",
                "source_id": "source-1",
                "title": {"zh": "素材一"},
                "summary": {"zh": "摘要一"},
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    details = (
        json.dumps(
            {
                "source_type": "DARKWEB",
                "source_id": detail_source_id,
                "darkweb": {"title": "素材一", "body": "完整正文"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    (directory / "template.md").write_bytes(template)
    (directory / "materials.jsonl").write_bytes(materials)
    (directory / "material-details.jsonl").write_bytes(details)
    (directory / "dataset.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "datasetID": "D01",
                "name": "D01",
                "description": "frozen contract test",
                "language": "zh-CN",
                "sourceSessionID": "ses_source",
                "capturedAt": "2026-09-07T00:00:00+00:00",
                "templateSHA256": hashlib.sha256(template).hexdigest(),
                "materialsSHA256": hashlib.sha256(materials).hexdigest(),
                "materialDetailsSHA256": hashlib.sha256(details).hexdigest(),
                "materialCount": 1,
                "materialDetailCount": 1,
                "sourceCounts": {"DARKWEB": 1},
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_frozen_dataset_loader_verifies_files_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "datasets"
    _write_dataset(root)
    monkeypatch.setenv("SITUATION_REPORT_DEBUG_DATASET_ROOT", str(root))

    dataset = load_debug_dataset("D01")
    assert dataset.manifest.material_count == 1
    assert dataset.manifest.material_detail_count == 1
    assert dataset.manifest.source_counts == {"DARKWEB": 1}
    assert [value.dataset_id for value in list_debug_datasets()] == ["D01"]

    with (root / "D01" / "materials.jsonl").open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(DebugDatasetError, match="hashes or counts"):
        load_debug_dataset("D01")


def test_frozen_dataset_rejects_detail_outside_selected_materials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "datasets"
    _write_dataset(root, detail_source_id="not-selected")
    monkeypatch.setenv("SITUATION_REPORT_DEBUG_DATASET_ROOT", str(root))

    with pytest.raises(DebugDatasetError, match="outside the selected set"):
        load_debug_dataset("D01")


def test_frozen_dataset_listing_is_empty_when_root_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SITUATION_REPORT_DEBUG_DATASET_ROOT", raising=False)
    assert list_debug_datasets() == []
