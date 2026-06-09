from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flocks.workflow import fs_store


@pytest.fixture(autouse=True)
def reset_workspace_root_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fs_store, "_workspace_root", None)


def _write_workflow(base_dir: Path, workflow_id: str, name: str) -> None:
    workflow_dir = base_dir / ".flocks" / "plugins" / "workflows" / workflow_id
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


def test_read_workflow_from_fs_refreshes_cached_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first_workspace = tmp_path / "workspace-a"
    second_workspace = tmp_path / "workspace-b"
    workflow_id = "cache-switch-demo"
    _write_workflow(first_workspace, workflow_id, "workspace-a")
    _write_workflow(second_workspace, workflow_id, "workspace-b")

    monkeypatch.chdir(first_workspace)
    first = fs_store.read_workflow_from_fs(workflow_id)

    monkeypatch.chdir(second_workspace)
    second = fs_store.read_workflow_from_fs(workflow_id)

    assert first is not None
    assert second is not None
    assert first["workflowJson"]["name"] == "workspace-a"
    assert second["workflowJson"]["name"] == "workspace-b"
    assert fs_store.find_workspace_root() == second_workspace


def test_read_workflow_dir_uses_latest_file_mtime_when_meta_is_stale(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workflow_id = "mtime-sync-demo"
    _write_workflow(workspace, workflow_id, "mtime-demo")
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id

    meta_file = workflow_dir / "meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "name": "mtime-demo",
                "description": "demo",
                "category": "default",
                "status": "draft",
                "createdBy": None,
                "createdAt": 1000,
                "updatedAt": 1000,
            }
        ),
        encoding="utf-8",
    )
    md_file = workflow_dir / "workflow.md"
    md_file.write_text("# demo\n", encoding="utf-8")

    json_file = workflow_dir / "workflow.json"
    os.utime(meta_file, (1, 1))
    os.utime(json_file, (5, 5))
    os.utime(md_file, (9, 9))

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "project")

    assert data is not None
    assert data["updatedAt"] == 9000
    assert data["markdownContent"] == "# demo\n"
    assert data["editMarkdownContent"] == "# demo\n"


def test_read_workflow_dir_uses_legacy_edit_markdown_only_as_fallback(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workflow_id = "legacy-edit-md-demo"
    _write_workflow(workspace, workflow_id, "legacy-demo")
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id

    (workflow_dir / "workflow.edit.md").write_text("# legacy\n", encoding="utf-8")

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "project")

    assert data is not None
    assert data["markdownContent"] == "# legacy\n"
    assert data["editMarkdownContent"] == "# legacy\n"
