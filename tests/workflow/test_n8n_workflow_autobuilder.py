from __future__ import annotations

import json
from pathlib import Path

from flocks.workflow.runner import run_workflow


def test_n8n_workflow_autobuilder_runs_offline_publish_false() -> None:
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

