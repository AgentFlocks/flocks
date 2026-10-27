import json

from typer.testing import CliRunner

from flocks.cli.commands.workflow import workflow_app


def _write_workflow(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({
        "name": "stream_alert_dispose_gt", "start": "mark_processed",
        "nodes": [{"id": "mark_processed", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }), encoding="utf-8")
    return path


def test_repair_defaults_to_preview_and_preserves_original(tmp_path):
    path = _write_workflow(tmp_path)
    original = path.read_bytes()
    result = CliRunner().invoke(workflow_app, ["repair", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["apply"] is False
    assert path.read_bytes() == original


def test_repair_rejects_changed_preview_digest(tmp_path):
    path = _write_workflow(tmp_path)
    original = path.read_bytes()
    result = CliRunner().invoke(workflow_app, ["repair", str(path), "--apply", "--expected-sha256", "0" * 64])
    assert result.exit_code == 1, result.output
    assert path.read_bytes() == original


def test_repair_default_discovers_known_ids_only(tmp_path, monkeypatch):
    from flocks.workflow import fs_store

    path = tmp_path / "backlog_monitor_gt" / "workflow.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"name": "backlog_monitor_gt", "nodes": [], "edges": []}), encoding="utf-8")
    monkeypatch.setattr(fs_store, "workflow_scan_dirs", lambda: [(tmp_path, "global")])
    result = CliRunner().invoke(workflow_app, ["repair"])
    assert result.exit_code == 0, result.output
    reports = json.loads(result.stdout)["workflows"]
    assert len(reports) == 1


def test_repair_refuses_symlink_without_changing_target(tmp_path):
    path = _write_workflow(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    original = path.read_bytes()
    result = CliRunner().invoke(workflow_app, ["repair", str(link), "--apply"])
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert path.read_bytes() == original and link.is_symlink()


def test_batch_repair_reports_applied_files_when_another_fails(tmp_path, monkeypatch):
    from flocks.workflow import fs_store, repairs

    for name in ("backlog_monitor_gt", "stream_alert_dispose_gt"):
        path = tmp_path / name / "workflow.json"
        path.parent.mkdir()
        path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fs_store, "workflow_scan_dirs", lambda: [(tmp_path, "global")])

    def repair(path, **kwargs):
        if path.parent.name == "stream_alert_dispose_gt":
            raise PermissionError("locked by editor")
        return {"path": str(path), "applied": True, "backup_path": "preserved-backup"}

    monkeypatch.setattr(repairs, "repair_workflow_file", repair)
    result = CliRunner().invoke(workflow_app, ["repair", "--apply"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["errors"] == 1
    assert report["workflows"][0]["backup_path"] == "preserved-backup"
    assert report["workflows"][1]["error"] == "locked by editor"
