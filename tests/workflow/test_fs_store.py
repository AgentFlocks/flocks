from __future__ import annotations

import json
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
