import shutil
import subprocess
from pathlib import Path

import pytest

from flocks.cli import service_manager
from flocks.updater import updater


def test_run_handles_none_process_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    code, stdout, stderr = updater._run(["npm", "run", "build"], cwd=tmp_path)

    assert code == 0
    assert stdout == ""
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_async_handles_none_process_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    code, stdout, stderr = await updater._run_async(["npm", "run", "build"], cwd=tmp_path)

    assert code == 0
    assert stdout == ""
    assert stderr == ""


def test_find_executable_checks_windows_scripts_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    uv_cmd = scripts_dir / "uv.cmd"
    uv_cmd.write_text("", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)

    assert updater._find_executable("uv.cmd") == str(uv_cmd)


def test_find_executable_ignores_wsl_mnt_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    uv_bin = bin_dir / "uv"
    uv_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/mnt/c/Users/test/{name}")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)

    assert updater._find_executable("uv") == str(uv_bin)


def test_upgrade_page_html_contains_marker_and_version() -> None:
    html = updater._upgrade_page_html("2026.3.31.1")

    assert "flocks-upgrade-in-progress" in html
    assert "v2026.3.31.1" in html
    assert "window.location.reload()" in html


def test_rmtree_onerror_retries_before_logging_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []
    warnings: list[tuple[str, dict[str, str]]] = []

    def fake_remove(path: str) -> None:
        attempts.append(path)
        raise OSError("locked")

    import time as time_module

    monkeypatch.setattr(updater.os, "chmod", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(time_module, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater.log, "warning", lambda event, payload: warnings.append((event, payload)))

    updater._rmtree_onerror(fake_remove, "/tmp/locked", None)

    assert attempts == ["/tmp/locked"] * 5
    assert warnings == [("updater.rmtree.skip_locked", {"path": "/tmp/locked"})]


def test_safe_remove_renames_locked_file_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "locked.exe"
    target.write_text("old", encoding="utf-8")
    original_unlink = Path.unlink

    def fake_unlink(self: Path, *args, **kwargs) -> None:
        if self == target:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(Path, "unlink", fake_unlink)

    updater._safe_remove(target)

    leftovers = list(tmp_path.glob("locked.exe.flocks_old_*"))
    assert not target.exists()
    assert len(leftovers) == 1


def test_prepare_upgrade_handover_writes_state_and_stops_frontend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    paths = service_manager.RuntimePaths(
        root=tmp_path / ".flocks",
        run_dir=tmp_path / ".flocks" / "run",
        log_dir=tmp_path / ".flocks" / "logs",
        backend_pid=tmp_path / ".flocks" / "run" / "backend.pid",
        frontend_pid=tmp_path / ".flocks" / "run" / "webui.pid",
        backend_log=tmp_path / ".flocks" / "logs" / "backend.log",
        frontend_log=tmp_path / ".flocks" / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(updater, "_current_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda config, version: {"upgrade_server_pid": 321, "page_dir": str(tmp_path / "page"), "page_log": str(tmp_path / "upgrade.log")},
    )
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "_recorded_port", lambda _pid_file, default: default)
    monkeypatch.setattr(
        service_manager,
        "stop_one",
        lambda port, _pid_file, name, _console: calls.append((port, name)),
    )

    payload = updater._prepare_upgrade_handover("2026.3.31.1")

    assert calls == [(5173, "WebUI")]
    assert payload["upgrade_server_pid"] == 321
    assert updater._read_upgrade_state()["version"] == "2026.3.31.1"


def test_recover_upgrade_state_restarts_frontend_and_clears_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    started: list[tuple[int, bool]] = []
    stopped: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda: stopped.append("stop"))
    monkeypatch.setattr(
        service_manager,
        "start_frontend",
        lambda config, _console: started.append((config.frontend_port, config.skip_frontend_build)),
    )
    updater._write_upgrade_state(
        {
            "version": "2026.3.31.1",
            "backend_host": "127.0.0.1",
            "backend_port": 8000,
            "frontend_host": "127.0.0.1",
            "frontend_port": 5173,
            "skip_frontend_build": True,
        }
    )

    updater.recover_upgrade_state()

    assert stopped == ["stop"]
    assert started == [(5173, True)]
    assert updater._read_upgrade_state() is None
