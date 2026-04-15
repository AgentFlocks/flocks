import json
from pathlib import Path

from flocks.workspace.manager import WorkspaceManager


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "workflows"
    / "loop_host_forensics_fast"
    / "workflow.json"
)


def _load_workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_inspect_host_extracts_verdict_into_lightweight_result(tmp_path: Path) -> None:
    workflow = _load_workflow()
    inspect_host = next(node for node in workflow["nodes"] if node["id"] == "inspect_host")

    per_host_dir = tmp_path / "host_triage"
    batch_report_path = tmp_path / "batch_host_triage_log.md"

    class DummyTool:
        def run_safe(self, *args, **kwargs) -> dict:
            assert args == ("task",)
            assert kwargs["subagent_type"] == "host-forensics-fast"
            return {
                "success": True,
                "text": (
                    "## Host Quick Assessment\n\n"
                    "**Target**: 10.0.0.8\n"
                    "**Verdict**: SUSPICIOUS\n"
                    "**Confidence**: HIGH\n\n"
                    "### Summary\n存在异常外联，需要继续排查。\n"
                ),
            }

    env = {
        "inputs": {
            "hosts": ["10.0.0.8"],
            "host_idx": 0,
            "ssh_user": "root",
            "per_host_dir": str(per_host_dir),
            "batch_report_path": str(batch_report_path),
            "triage_results": [],
        },
        "outputs": {},
        "tool": DummyTool(),
    }

    exec(inspect_host["code"], env, env)

    result = env["outputs"]["triage_results"][0]
    assert result["success"] is True
    assert result["verdict"] == "SUSPICIOUS"
    assert result["per_host_md"].endswith(".md")
    assert Path(result["per_host_md"]).exists()
    assert env["outputs"]["last_verdict"] == "SUSPICIOUS"

    report_text = Path(result["per_host_md"]).read_text(encoding="utf-8")
    assert "- verdict: SUSPICIOUS" in report_text


def test_finalize_summary_persists_verdict_in_results_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workflow = _load_workflow()
    finalize_summary = next(
        node for node in workflow["nodes"] if node["id"] == "finalize_summary"
    )

    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_root))

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    try:
        env = {
            "inputs": {
                "triage_results": [
                    {
                        "host": "10.0.0.8",
                        "ssh_target": "root@10.0.0.8",
                        "success": True,
                        "verdict": "CLEAN",
                        "error": "",
                        "per_host_md": str(tmp_path / "host_triage" / "0001.md"),
                    },
                    {
                        "host": "10.0.0.9",
                        "ssh_target": "root@10.0.0.9",
                        "success": False,
                        "verdict": "UNKNOWN",
                        "error": "ssh timeout",
                        "per_host_md": str(tmp_path / "host_triage" / "0002.md"),
                    },
                ],
                "hosts": ["10.0.0.8", "10.0.0.9"],
                "ssh_user": "root",
                "per_host_dir": str(tmp_path / "host_triage"),
                "batch_report_path": str(tmp_path / "batch_host_triage_log.md"),
            },
            "outputs": {},
        }

        exec(finalize_summary["code"], env, env)

        results_payload = json.loads(
            Path(env["outputs"]["results_json_path"]).read_text(encoding="utf-8")
        )
        manifest_payload = json.loads(
            Path(env["outputs"]["manifest_path"]).read_text(encoding="utf-8")
        )
        index_text = Path(env["outputs"]["index_path"]).read_text(encoding="utf-8")

        assert results_payload["triage_results"][0]["verdict"] == "CLEAN"
        assert results_payload["triage_results"][1]["verdict"] == "UNKNOWN"
        assert manifest_payload["items"][0]["verdict"] == "CLEAN"
        assert manifest_payload["items"][1]["verdict"] == "UNKNOWN"
        assert "- CLEAN: 1 台" in index_text
        assert "- UNKNOWN: 1 台" in index_text
        assert "判定: `CLEAN`" in index_text
    finally:
        WorkspaceManager._instance = previous_instance
