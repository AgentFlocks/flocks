from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.server.routes import workflow as workflow_routes


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    name: str,
    meta: dict | None = None,
) -> None:
    workflow_dir = root / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "name": name,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    if meta is not None:
        (workflow_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_list_workflows_from_fs_skips_hidden_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_root = tmp_path / ".flocks" / "plugins" / "workflows"
    _write_workflow(workflow_root, "visible", name="visible")
    _write_workflow(
        workflow_root,
        "__hidden_template",
        name="hidden template",
        meta={"hidden": True, "templateOnly": True},
    )
    monkeypatch.setattr(
        workflow_routes,
        "_all_scan_dirs",
        lambda: [(workflow_root, "project")],
    )

    items = workflow_routes._list_workflows_from_fs()

    assert [item["id"] for item in items] == ["visible"]
