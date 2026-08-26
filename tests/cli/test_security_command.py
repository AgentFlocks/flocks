from __future__ import annotations

import builtins
import json
import os
from types import SimpleNamespace

import pytest

from typer.testing import CliRunner

import flocks.cli.commands.security as security_cmd
from flocks.cli.main import app


runner = CliRunner()


def test_plugin_cli_falls_back_to_source_when_plugin_is_missing(monkeypatch) -> None:
    from flocks.tool.registry import ToolRegistry

    real_import = builtins.__import__
    entrypoint_imports = 0
    registrations = 0

    async def run_audit(*_args, **_kwargs):
        return {}

    def scan_status(_scan_id):
        return {}

    def register() -> None:
        nonlocal registrations
        registrations += 1

    def import_with_missing_installed_plugin(name, *args, **kwargs):
        nonlocal entrypoint_imports
        if name == "flocks_code_security.entrypoint":
            entrypoint_imports += 1
            if entrypoint_imports == 1:
                raise ModuleNotFoundError(
                    "No module named 'flocks_code_security'",
                    name="flocks_code_security",
                )
            return SimpleNamespace(register=register)
        if name == "flocks_code_security.cli":
            return SimpleNamespace(
                run_standard_audit=run_audit,
                scan_status=scan_status,
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(builtins, "__import__", import_with_missing_installed_plugin)
    monkeypatch.setattr(security_cmd.sys, "path", list(security_cmd.sys.path))

    loaded_runner, loaded_status = security_cmd._load_plugin_cli()

    assert loaded_runner is run_audit
    assert loaded_status is scan_status
    assert entrypoint_imports == 2
    assert registrations == 1


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


def test_security_audit_forwards_dynamic_opt_in(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    async def run_audit(target, *, model, progress, dynamic_enabled):
        observed.update(
            target=target,
            model=model,
            progress=progress,
            dynamic_enabled=dynamic_enabled,
        )
        return {"scan_id": "scan_dynamic"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--dynamic", "--json"],
    )

    assert result.exit_code == 0
    assert observed["target"] == tmp_path
    assert observed["dynamic_enabled"] is True


def test_security_audit_forwards_direct_source_mode(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    async def run_audit(target, *, model, progress, copy_source):
        observed.update(
            target=target,
            model=model,
            progress=progress,
            copy_source=copy_source,
        )
        return {"scan_id": "scan_direct"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--no-copy", "--json"],
    )

    assert result.exit_code == 0
    assert observed["target"] == tmp_path
    assert observed["copy_source"] is False


def test_security_audit_forwards_coverage_policy(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    async def run_audit(target, *, model, progress, coverage_policy):
        observed.update(
            target=target,
            model=model,
            progress=progress,
            coverage_policy=coverage_policy,
        )
        return {"scan_id": "scan_exhaustive"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        [
            "audit",
            str(tmp_path),
            "--coverage-policy",
            "exhaustive",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed["target"] == tmp_path
    assert observed["coverage_policy"] == "exhaustive"


def test_security_audit_forwards_verification_votes(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    async def run_audit(target, *, model, progress, verification_votes):
        observed.update(
            target=target,
            model=model,
            progress=progress,
            verification_votes=verification_votes,
        )
        return {"scan_id": "scan_consensus"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--verification-votes", "3", "--json"],
    )

    assert result.exit_code == 0
    assert observed["verification_votes"] == 3


def test_security_audit_forwards_captured_knowledge_base(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}
    target = tmp_path / "source"
    target.mkdir()
    knowledge_base = tmp_path / "description.txt"
    knowledge_base.write_bytes(b"\xef\xbb\xbfTarget vulnerability description.\n")

    async def run_audit(target, *, model, progress, knowledge_base):
        observed.update(
            target=target,
            model=model,
            progress=progress,
            knowledge_base=knowledge_base,
        )
        return {"scan_id": "scan_guided"}

    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (run_audit, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(target), "--knowledge-base", str(knowledge_base), "--json"],
    )

    assert result.exit_code == 0
    assert observed["target"] == target
    assert observed["knowledge_base"] == {
        "display_name": "description.txt",
        "content": "Target vulnerability description.\n",
    }


def test_knowledge_base_reader_rejects_unsafe_files(monkeypatch, tmp_path) -> None:
    oversized = tmp_path / "oversized.txt"
    oversized.write_bytes(b"a" * (security_cmd.MAX_KNOWLEDGE_BASE_BYTES + 1))
    with pytest.raises(ValueError, match="32 KiB"):
        security_cmd._read_knowledge_base(oversized)

    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"target\0description")
    with pytest.raises(ValueError, match="UTF-8 text"):
        security_cmd._read_knowledge_base(binary)

    invalid_utf8 = tmp_path / "invalid.txt"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="valid UTF-8"):
        security_cmd._read_knowledge_base(invalid_utf8)

    target = tmp_path / "target.txt"
    target.write_text("description", encoding="utf-8")
    link = tmp_path / "description.txt"
    os.symlink(target, link)
    with pytest.raises(ValueError, match="symbolic link"):
        security_cmd._read_knowledge_base(link)

    fifo = tmp_path / "description.fifo"
    os.mkfifo(fifo)
    with monkeypatch.context() as context:
        context.setattr(
            security_cmd.os,
            "open",
            lambda *_args, **_kwargs: pytest.fail("FIFO must be rejected before open"),
        )
        with pytest.raises(ValueError, match="regular file"):
            security_cmd._read_knowledge_base(fifo)


def test_knowledge_base_reader_checks_the_opened_path_against_target(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "description.txt").write_text("target hypothesis", encoding="utf-8")
    alias = tmp_path / "target-alias"
    os.symlink(target, alias)

    with pytest.raises(ValueError, match="outside the audited source directory"):
        security_cmd._read_knowledge_base(
            alias / "description.txt",
            audited_target=target,
        )


def test_knowledge_base_reader_detects_changes_during_capture(monkeypatch, tmp_path) -> None:
    knowledge_base = tmp_path / "description.txt"
    knowledge_base.write_text("target hypothesis", encoding="utf-8")
    real_fstat = security_cmd.os.fstat
    calls = 0

    def changing_fstat(descriptor):
        nonlocal calls
        calls += 1
        current = real_fstat(descriptor)
        if calls == 1:
            return current
        return SimpleNamespace(
            st_mode=current.st_mode,
            st_dev=current.st_dev,
            st_ino=current.st_ino,
            st_size=current.st_size,
            st_mtime_ns=current.st_mtime_ns + 1,
            st_ctime_ns=current.st_ctime_ns,
        )

    monkeypatch.setattr(security_cmd.os, "fstat", changing_fstat)

    with pytest.raises(ValueError, match="changed while it was read"):
        security_cmd._read_knowledge_base(knowledge_base)


def test_security_audit_rejects_knowledge_base_inside_target(monkeypatch, tmp_path) -> None:
    knowledge_base = tmp_path / "description.txt"
    knowledge_base.write_text("target hypothesis", encoding="utf-8")
    monkeypatch.setattr(
        security_cmd,
        "_load_plugin_cli",
        lambda: (None, lambda _scan_id: {}),
    )
    monkeypatch.setattr(security_cmd, "shutdown_langfuse", lambda: None)

    result = runner.invoke(
        security_cmd.security_app,
        ["audit", str(tmp_path), "--knowledge-base", str(knowledge_base), "--json"],
    )

    assert result.exit_code == 1
    assert "outside the audited source directory" in result.stdout


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
