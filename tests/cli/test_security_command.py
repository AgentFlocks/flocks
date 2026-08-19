from __future__ import annotations

import json

from typer.testing import CliRunner

import flocks.cli.commands.security as security_cmd
from flocks.cli.main import app


runner = CliRunner()


def test_security_help_is_registered_on_main_cli() -> None:
    result = runner.invoke(app, ["security", "--help"])

    assert result.exit_code == 0
    assert "audit" in result.stdout
    assert "status" in result.stdout


def test_security_audit_streams_json_progress(monkeypatch, tmp_path) -> None:
    shutdowns: list[bool] = []
    async def run_audit(target, *, model, progress):
        assert target == tmp_path
        assert model == "openai/gpt-test"
        progress("scan.prepared", {"scan_id": "scan_test"})
        progress(
            "batch.status",
            {"batch_id": "batch_test", "phase": "baseline", "status": "running"},
        )
        progress(
            "scan.finalized",
            {"scan_id": "scan_test", "report_path": "/output/report.md"},
        )
        return {"scan_id": "scan_test", "report_path": "/output/report.md"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(
        security_cmd, "shutdown_langfuse", lambda: shutdowns.append(True)
    )

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--model", "openai/gpt-test", "--json"],
    )

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == [
        "scan.prepared",
        "batch.status",
        "scan.finalized",
        "scan.result",
    ]
    assert events[0]["scan_id"] == "scan_test"
    assert shutdowns == [True]


def test_security_status_reads_persisted_progress(monkeypatch) -> None:
    expected = {
        "scan_id": "scan_test",
        "status": "running",
        "threat_model_status": "completed",
        "counts": {"active_work_units": 2},
        "worker_batches": [
            {"batch_id": "batch_test", "phase": "baseline", "status": "running"}
        ],
    }
    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (None, lambda scan_id: expected if scan_id == "scan_test" else {}),
    )

    result = runner.invoke(
        security_cmd.security_app,
        ["status", "scan_test", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == expected


def test_security_audit_reports_failures(monkeypatch, tmp_path) -> None:
    shutdowns: list[bool] = []
    async def fail_audit(_target, *, model, progress):
        raise RuntimeError("worker launch failed")

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (fail_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(
        security_cmd, "shutdown_langfuse", lambda: shutdowns.append(True)
    )

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "worker launch failed"
    assert shutdowns == [True]
