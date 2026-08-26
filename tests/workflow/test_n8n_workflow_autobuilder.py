from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.workflow.runner import run_workflow


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    from flocks.workspace.manager import WorkspaceManager

    original_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    try:
        yield workspace
    finally:
        WorkspaceManager._instance = original_instance


def test_n8n_workflow_autobuilder_runs_offline_publish_false(isolated_workspace: Path) -> None:
    workflow_path = Path(".flocks/plugins/workflows/n8n_workflow_autobuilder/workflow.json").resolve()
    data = json.loads(workflow_path.read_text(encoding="utf-8"))
    sample = data["metadata"]["sampleInputs"]

    result = run_workflow(
        workflow=str(workflow_path),
        inputs=sample,
        ensure_requirements=False,
        timeout_s=120,
        node_timeout_s=120,
        history_mode="summary",
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["lint_ok"] is True
    assert result.outputs["success"] is True
    assert result.outputs["workflow_id"] == ""
    assert Path(result.outputs["generated_json_path"]).exists()
    assert Path(result.outputs["report_path"]).exists()


def test_n8n_workflow_autobuilder_runs_kafka_offline_publish_false(isolated_workspace: Path) -> None:
    workflow_path = Path(".flocks/plugins/workflows/n8n_workflow_autobuilder/workflow.json").resolve()
    sample = {
        "publish": False,
        "ir": {
            "name": "flocks-kafka-offline",
            "trigger": {
                "type": "kafka",
                "topic": "security-alerts",
                "groupPrefix": "flocks_kafka",
                "credentialRef": {"name": "Kafka Production"},
            },
            "steps": [
                {
                    "id": "normalize",
                    "kind": "code",
                    "js_code": "return $input.all();",
                }
            ],
            "tests": [],
        },
    }

    result = run_workflow(
        workflow=str(workflow_path),
        inputs=sample,
        ensure_requirements=False,
        timeout_s=120,
        node_timeout_s=120,
        history_mode="summary",
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["lint_ok"] is True
    assert result.outputs["success"] is True
    assert result.outputs["trigger_type"] == "kafka"
    assert result.outputs["webhook_url"] == ""
