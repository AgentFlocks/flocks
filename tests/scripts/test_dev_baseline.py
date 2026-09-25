"""The developer probe must fail safely without importing application storage."""

import importlib.util
import json
from pathlib import Path
import subprocess


path = Path(__file__).resolve().parents[2] / "scripts" / "dev_baseline.py"
spec = importlib.util.spec_from_file_location("dev_baseline", path)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def test_production_dsn_is_never_used_as_test_fallback(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("FLOCKS_TEST_PG_DSN", raising=False)
    monkeypatch.setenv("FLOCKS_DB_DSN", "postgresql://production-secret")
    monkeypatch.setattr(baseline, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not connect")))
    report = baseline.Report()
    baseline.check_test_postgres(tmp_path, tmp_path, report, required=True)
    output = capsys.readouterr().out
    assert report.failures == 1
    assert "production-secret" not in output
    assert "production DSNs are never substituted" in output


def test_pg_probe_reuses_installer_readonly_check_and_hides_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FLOCKS_TEST_PG_DSN", "postgresql://test-secret")
    calls = []

    def run(command, root):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "secret connection details")

    monkeypatch.setattr(baseline, "run", run)
    report = baseline.Report()
    baseline.check_test_postgres(tmp_path, tmp_path, report, required=True)
    assert "postgres_preflight.py" in calls[0][-1]
    assert "check_connection" in calls[0][-1]
    assert "FLOCKS_DB_DSN" not in calls[0][-1]
    assert "test-secret" not in capsys.readouterr().out
    assert report.failures == 1


def test_import_check_detects_stale_wheel_even_when_cwd_is_source(monkeypatch, tmp_path):
    python = baseline.python_path(tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    commands = []

    def run(command, root):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"origin": "/old/site-packages/flocks/__init__.py", "python": [3, 12]}))

    monkeypatch.setattr(baseline, "run", run)
    report = baseline.Report()
    baseline.check_import(tmp_path, "flocks", tmp_path, report)
    assert "-I" in commands[0]
    assert report.failures == 1


def test_frontend_detects_lock_drift_and_wrong_overlay_link(tmp_path):
    webui = tmp_path / "flocks" / "webui"
    webui.mkdir(parents=True)
    (webui / "package.json").write_text(json.dumps({"dependencies": {"react": "^19.0.0"}}))
    (webui / "package-lock.json").write_text(json.dumps({"packages": {
        "": {"dependencies": {"react": "^18.0.0"}}, "node_modules/react": {"version": "18.0.0"},
    }}))
    pro = tmp_path / "flockspro"
    (pro / "webui-overlay" / "node_modules").mkdir(parents=True)
    issues = baseline.frontend_issues(webui, pro)
    assert any("differs from package-lock" in item for item in issues)
    assert any("locked dependency not installed: react" in item for item in issues)
    assert any("overlay node_modules" in item for item in issues)


def test_environment_check_uses_readonly_frozen_profile(monkeypatch, tmp_path):
    calls = []

    def run(command, root):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "outdated environment")

    monkeypatch.setattr(baseline, "run", run)
    report = baseline.Report()
    baseline.check_environment(tmp_path, report, pro=True)
    baseline.check_environment(tmp_path, report, pro=False)
    assert calls == [
        ["uv", "sync", "--check", "--frozen", "--offline", "--extra", "test"],
        ["uv", "sync", "--check", "--frozen", "--offline", "--group", "dev"],
    ]
    assert report.failures == 2
