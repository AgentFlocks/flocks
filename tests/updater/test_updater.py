import signal
import shutil
import subprocess
import tarfile
from os import utime
from pathlib import Path
from types import SimpleNamespace

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


def test_run_replaces_invalid_windows_stderr_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout=b"",
            stderr=b"failed\x93output",
        )

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    code, stdout, stderr = updater._run(["npm", "run", "build"], cwd=tmp_path)

    assert code == 1
    assert stdout == ""
    assert stderr == "failed�output"


def test_get_current_version_replaces_invalid_git_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", tmp_path / ".current_version")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=b"v2026.4.1\x93\n",
            stderr=b"",
        )

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    assert updater.get_current_version() == "2026.4.1�"


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


@pytest.mark.asyncio
async def test_run_async_replaces_invalid_windows_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=b"ok\x93done",
            stderr=b"",
        )

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    code, stdout, stderr = await updater._run_async(["npm", "run", "build"], cwd=tmp_path)

    assert code == 0
    assert stdout == "ok�done"
    assert stderr == ""


def test_find_executable_checks_windows_scripts_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    uv_exe = scripts_dir / "uv.exe"
    uv_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(updater.sys, "platform", "win32")

    assert updater._find_executable("uv") == str(uv_exe)


def test_find_executable_checks_windows_cmd_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    npm_cmd = scripts_dir / "npm.cmd"
    npm_cmd.write_text("", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(updater.sys, "platform", "win32")

    assert updater._find_executable("npm") == str(npm_cmd)


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


def test_upgrade_page_probe_urls_support_ipv6_loopback_fallback() -> None:
    assert updater._upgrade_page_probe_urls("::", 5173) == [
        "http://[::1]:5173",
        "http://127.0.0.1:5173",
    ]


def test_build_restart_argv_uses_windows_executable_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\Users\worker\.local\bin\flocks", "start", "--reload", "--port", "8000"],
    )
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: r"C:\Users\worker\.local\bin\flocks.exe" if name in {
            r"C:\Users\worker\.local\bin\flocks",
            r"C:\Users\worker\.local\bin\flocks.exe",
        } else None,
    )

    assert updater._build_restart_argv() == [
        r"C:\Users\worker\.local\bin\flocks.exe",
        "start",
        "--port",
        "8000",
    ]


def test_build_restart_argv_preserves_python_script_path_from_orig_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\Users\worker\.local\bin\flocks", "start"],
    )
    monkeypatch.setattr(
        updater.sys,
        "orig_argv",
        [r"C:\Python312\python.exe", r"C:\Users\worker\.local\bin\flocks", "start"],
    )
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: r"C:\Python312\python.exe" if name == r"C:\Python312\python.exe" else None,
    )

    assert updater._build_restart_argv() == [
        r"C:\Python312\python.exe",
        r"C:\Users\worker\.local\bin\flocks",
        "start",
    ]


def test_build_restart_argv_prefers_windows_orig_argv_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\Users\worker\.local\bin\flocks", "start"],
    )
    monkeypatch.setattr(
        updater.sys,
        "orig_argv",
        [r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe", "start"],
    )
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe" if name == r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe" else None,
    )

    assert updater._build_restart_argv() == [
        r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe",
        "start",
    ]


def test_build_restart_argv_preserves_windows_module_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\repo\flocks\__main__.py", "start", "--reload"],
    )
    monkeypatch.setattr(
        updater.sys,
        "orig_argv",
        [r"C:\Windows\py.exe", "-m", "flocks", "start", "--reload"],
    )
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: r"C:\Windows\py.exe" if name == r"C:\Windows\py.exe" else None,
    )

    assert updater._build_restart_argv() == [
        r"C:\Windows\py.exe",
        "-m",
        "flocks",
        "start",
    ]


def test_build_restart_argv_falls_back_to_path_launcher_name_when_orig_argv_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\Users\worker\.local\bin\flocks", "start"],
    )
    monkeypatch.delattr(updater.sys, "orig_argv", raising=False)
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe" if name in {
            "flocks.exe",
            "flocks",
        } else None,
    )

    assert updater._build_restart_argv() == [
        r"C:\Users\worker\AppData\Roaming\uv\bin\flocks.exe",
        "start",
    ]


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


def test_safe_remove_renames_locked_directory_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "webui"
    target.mkdir()
    (target / "dist").mkdir()
    (target / "dist" / "index.html").write_text("old", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_safe_rmtree", lambda _target: (_ for _ in ()).throw(PermissionError("locked")))

    updater._safe_remove(target)

    leftovers = list(tmp_path.glob("webui.flocks_old_*"))
    assert not target.exists()
    assert len(leftovers) == 1
    assert (leftovers[0] / "dist" / "index.html").exists()


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


def test_prepare_upgrade_handover_restores_frontend_when_upgrade_page_fails(
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

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(updater, "_current_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "_recorded_port", lambda _pid_file, default: default)
    monkeypatch.setattr(
        service_manager,
        "stop_one",
        lambda port, _pid_file, name, _console: calls.append((f"stop:{name}:{port}", True)),
    )

    def fake_start_frontend(config, _console) -> None:
        calls.append(("start_frontend", config.skip_frontend_build))

    monkeypatch.setattr(service_manager, "start_frontend", fake_start_frontend)
    monkeypatch.setattr(
        updater,
        "_stop_upgrade_page_server",
        lambda config: calls.append((f"stop_page:{config.frontend_port}", True)),
    )
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, _version: (_ for _ in ()).throw(RuntimeError("page failed")),
    )

    with pytest.raises(RuntimeError, match="page failed"):
        updater._prepare_upgrade_handover("2026.3.31.1")

    assert calls == [
        ("stop:WebUI:5173", True),
        ("stop_page:5173", True),
        ("start_frontend", False),
    ]
    assert updater._read_upgrade_state() is None


def test_stop_upgrade_page_server_waits_for_port_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    pid_path = tmp_path / ".flocks" / "run" / "upgrade_server.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("321", encoding="utf-8")

    kill_signals: list[tuple[int, signal.Signals]] = []
    listener_states = iter([[321], [321], []])

    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.os, "kill", lambda pid, sig: kill_signals.append((pid, sig)))
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: len(kill_signals) == 0)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: next(listener_states))

    updater._stop_upgrade_page_server(service_manager.ServiceConfig(frontend_port=5173))

    assert kill_signals == [(321, signal.SIGTERM)]
    assert not pid_path.exists()


def test_stop_upgrade_page_server_escalates_when_page_process_does_not_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    pid_path = tmp_path / ".flocks" / "run" / "upgrade_server.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("321", encoding="utf-8")

    kill_signals: list[tuple[int, signal.Signals]] = []
    sleep_calls = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1

    def fake_pid_is_running(_pid: int) -> bool:
        return len(kill_signals) < 2

    def fake_port_owner_pids(_port: int) -> list[int]:
        return [321] if len(kill_signals) < 2 else []

    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.os, "kill", lambda pid, sig: kill_signals.append((pid, sig)))
    monkeypatch.setattr(updater.time, "sleep", fake_sleep)
    monkeypatch.setattr(service_manager, "pid_is_running", fake_pid_is_running)
    monkeypatch.setattr(service_manager, "port_owner_pids", fake_port_owner_pids)

    updater._stop_upgrade_page_server(service_manager.ServiceConfig(frontend_port=5173))

    assert kill_signals == [(321, signal.SIGTERM), (321, signal.SIGKILL)]
    assert sleep_calls["count"] == 20
    assert not pid_path.exists()


def test_stop_upgrade_page_server_raises_when_another_process_keeps_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    pid_path = tmp_path / ".flocks" / "run" / "upgrade_server.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("321", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.os, "kill", lambda *_args: None)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [999])

    with pytest.raises(RuntimeError, match="Frontend port 5173 is still occupied"):
        updater._stop_upgrade_page_server(service_manager.ServiceConfig(frontend_port=5173))

    assert not pid_path.exists()


def test_recover_upgrade_state_restarts_frontend_and_clears_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    started: list[tuple[int, bool]] = []
    stopped: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda config: stopped.append(f"stop:{config.frontend_port}"))
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

    assert stopped == ["stop:5173"]
    assert started == [(5173, True)]
    assert updater._read_upgrade_state() is None


def test_recover_upgrade_state_retries_frontend_with_build_when_dist_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[bool] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda _config: None)

    def fake_start_frontend(config, _console) -> None:
        starts.append(config.skip_frontend_build)
        if config.skip_frontend_build:
            raise service_manager.ServiceError("missing dist")

    monkeypatch.setattr(service_manager, "start_frontend", fake_start_frontend)
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

    assert starts == [True, False]
    assert updater._read_upgrade_state() is None


def test_recover_upgrade_state_restart_failure_restarts_upgrade_page_and_keeps_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[bool] = []
    page_restarts: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda _config: None)

    def fake_start_frontend(config, _console) -> None:
        starts.append(config.skip_frontend_build)
        raise service_manager.ServiceError("still broken")

    monkeypatch.setattr(service_manager, "start_frontend", fake_start_frontend)
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, version: page_restarts.append(version) or {
            "upgrade_server_pid": 654,
            "page_dir": str(tmp_path / "page"),
            "page_log": str(tmp_path / "upgrade.log"),
        },
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

    with pytest.raises(service_manager.ServiceError, match="still broken"):
        updater.recover_upgrade_state()

    assert starts == [True, False]
    assert page_restarts == ["2026.3.31.1"]
    assert updater._read_upgrade_state()["upgrade_server_pid"] == 654


def test_start_upgrade_page_server_binds_configured_frontend_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    flocks_root = tmp_path / ".flocks"
    (flocks_root / "run").mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))

    page_dir = tmp_path / "page"
    page_dir.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(updater, "_write_upgrade_page", lambda _version: page_dir)
    monkeypatch.setattr(updater, "_wait_for_upgrade_page", lambda config: captured.setdefault("wait_host", config.frontend_host))
    monkeypatch.setattr(
        updater,
        "_spawn_detached_process",
        lambda command, *, cwd, log_path: captured.update({
            "command": command,
            "cwd": cwd,
            "log_path": log_path,
        }) or SimpleNamespace(pid=4321),
    )

    config = service_manager.ServiceConfig(frontend_host="0.0.0.0", frontend_port=5173)
    payload = updater._start_upgrade_page_server(config, "2026.4.1")

    assert payload["upgrade_server_pid"] == 4321
    assert captured["command"] == [
        updater.sys.executable,
        "-m",
        "http.server",
        "5173",
        "--bind",
        "0.0.0.0",
        "--directory",
        str(page_dir.resolve()),
    ]
    assert captured["wait_host"] == "0.0.0.0"


def test_wait_for_upgrade_page_uses_access_host_for_local_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            requested_urls.append(url)
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(updater.httpx, "Client", lambda timeout: _FakeClient())
    monkeypatch.setattr(service_manager, "access_host", lambda host: "127.0.0.1" if host == "0.0.0.0" else host)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)

    updater._wait_for_upgrade_page(service_manager.ServiceConfig(frontend_host="0.0.0.0", frontend_port=5173))

    assert requested_urls == ["http://127.0.0.1:5173"]


def test_wait_for_upgrade_page_falls_back_from_ipv6_to_ipv4_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            requested_urls.append(url)
            if url == "http://[::1]:5173":
                raise OSError("ipv6 unavailable")
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(updater.httpx, "Client", lambda timeout: _FakeClient())
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)

    updater._wait_for_upgrade_page(service_manager.ServiceConfig(frontend_host="::", frontend_port=5173))

    assert requested_urls == [
        "http://[::1]:5173",
        "http://127.0.0.1:5173",
    ]


def test_rollback_failed_update_restores_backup_and_rebuilds_frontend_if_needed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    events: list[str] = []

    monkeypatch.setattr(updater, "_restore_backup_archive", lambda backup, root: events.append(f"restore:{backup.name}:{root.name}"))
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: events.append(f"marker:{version}"))
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda _config: events.append("stop_page"))
    monkeypatch.setattr(updater.shutil, "rmtree", lambda path, ignore_errors=True: events.append(f"rmtree:{Path(path).name}"))

    def fake_start_frontend(config, _console) -> None:
        events.append(f"start_frontend:{config.skip_frontend_build}")
        if config.skip_frontend_build:
            raise service_manager.ServiceError("missing dist")

    monkeypatch.setattr(service_manager, "start_frontend", fake_start_frontend)
    updater._write_upgrade_state(
        {
            "version": "2026.4.1",
            "backend_host": "127.0.0.1",
            "backend_port": 8000,
            "frontend_host": "127.0.0.1",
            "frontend_port": 5173,
            "skip_frontend_build": True,
        }
    )

    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    updater._rollback_failed_update(backup_path, tmp_path / "install", "2026.3.31")

    assert events == [
        "restore:backup.tar.gz:install",
        "marker:2026.3.31",
        "stop_page",
        "start_frontend:True",
        "start_frontend:False",
        "rmtree:upgrade-page",
    ]
    assert updater._read_upgrade_state() is None


def test_rollback_failed_update_keeps_state_and_upgrade_page_when_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    events: list[str] = []

    monkeypatch.setattr(
        updater,
        "_restore_backup_archive",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backup broken")),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: events.append(f"marker:{version}"))
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda _config: events.append("stop_page"))
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, version: events.append(f"restart_page:{version}") or {
            "upgrade_server_pid": 777,
            "page_dir": str(tmp_path / "page"),
            "page_log": str(tmp_path / "upgrade.log"),
        },
    )
    monkeypatch.setattr(updater.shutil, "rmtree", lambda path, ignore_errors=True: events.append(f"rmtree:{Path(path).name}"))

    def fake_start_frontend(config, _console) -> None:
        events.append(f"start_frontend:{config.skip_frontend_build}")
        raise service_manager.ServiceError("frontend still broken")

    monkeypatch.setattr(service_manager, "start_frontend", fake_start_frontend)
    updater._write_upgrade_state(
        {
            "version": "2026.4.1",
            "backend_host": "127.0.0.1",
            "backend_port": 8000,
            "frontend_host": "127.0.0.1",
            "frontend_port": 5173,
            "skip_frontend_build": True,
            "phase": "cutover_applied",
        }
    )

    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    updater._rollback_failed_update(backup_path, tmp_path / "install", "2026.3.31")

    payload = updater._read_upgrade_state()
    assert events == [
        "stop_page",
        "start_frontend:True",
        "restart_page:2026.3.31",
    ]
    assert payload is not None
    assert payload["phase"] == "rollback_failed"
    assert payload["upgrade_server_pid"] == 777
    assert "backup broken" in payload["last_error"]


def test_backup_current_version_preserves_webui_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    webui_dist = install_root / "webui" / "dist"
    other_dist = install_root / "dist"
    webui_dist.mkdir(parents=True)
    other_dist.mkdir(parents=True)
    (webui_dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (other_dist / "ignored.txt").write_text("nope", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(updater, "_BACKUP_DIR", backup_dir)
    backup_path = updater._backup_current_version(install_root, "2026.4.1", retain_count=1)

    assert backup_path is not None
    with tarfile.open(backup_path, "r:gz") as tar:
        names = tar.getnames()

    assert "flocks/webui/dist/index.html" in names
    assert "flocks/dist/ignored.txt" not in names


def test_cleanup_old_backups_keeps_latest_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    newest = backup_dir / "flocks-new.tar.gz"
    middle = backup_dir / "flocks-mid.tar.gz"
    oldest = backup_dir / "flocks-old.tar.gz"
    newest.write_text("new", encoding="utf-8")
    middle.write_text("mid", encoding="utf-8")
    oldest.write_text("old", encoding="utf-8")
    utime(oldest, (1, 1))
    utime(middle, (2, 2))
    utime(newest, (3, 3))

    monkeypatch.setattr(updater, "_BACKUP_DIR", backup_dir)

    updater._cleanup_old_backups(1)

    assert newest.exists()
    assert not middle.exists()
    assert not oldest.exists()


def test_replace_install_dir_preserves_webui_node_modules(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    install_root = tmp_path / "install"
    source_webui = source_dir / "webui"
    target_webui = install_root / "webui"

    (source_webui / "dist").mkdir(parents=True)
    (source_webui / "dist" / "index.html").write_text("new", encoding="utf-8")
    (source_webui / "package.json").write_text('{"name":"webui"}', encoding="utf-8")

    (target_webui / "dist").mkdir(parents=True)
    (target_webui / "dist" / "index.html").write_text("old", encoding="utf-8")
    locked_binary = target_webui / "node_modules" / "@esbuild" / "win32-x64" / "esbuild.exe"
    locked_binary.parent.mkdir(parents=True)
    locked_binary.write_text("locked", encoding="utf-8")

    updater._replace_install_dir(source_dir, install_root)

    assert (target_webui / "dist" / "index.html").read_text(encoding="utf-8") == "new"
    assert locked_binary.read_text(encoding="utf-8") == "locked"


@pytest.mark.asyncio
async def test_perform_update_builds_staged_frontend_before_handover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    (staged_webui / "dist").mkdir()
    (staged_webui / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    events: list[str] = []
    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="zip",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_run_async(cmd, cwd=None, timeout=None):
        if cmd[1] == "install":
            events.append("npm-install")
        elif cmd[:3] == ["/usr/bin/npm", "run", "build"]:
            events.append("npm-build")
        else:
            events.append("uv-sync")
        return 0, "", ""

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    async def fake_sleep(_seconds) -> None:
        events.append("sleep")

    monkeypatch.setattr(
        updater,
        "_get_updater_config",
        fake_get_updater_config,
    )
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else "/usr/bin/uv",
    )
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover") or {})
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: events.append("replace"),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: events.append(f"marker:{version}"))
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))
    monkeypatch.setattr(updater.os, "execv", lambda *_args: (_ for _ in ()).throw(OSError("boom")))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert [step.stage for step in progresses][-1] == "error"
    assert events[:5] == ["npm-install", "npm-build", "handover", "replace", "uv-sync"]
    assert "marker:2026.4.1" in events


@pytest.mark.asyncio
async def test_perform_update_rolls_back_when_replace_fails_on_windows_locked_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    (staged_webui / "dist").mkdir()
    (staged_webui / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    events: list[str] = []

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="zip",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    async def fake_run_async(cmd, cwd=None, timeout=None):
        if cmd[1] == "install":
            events.append("npm-install")
        elif cmd[:3] == ["/usr/bin/npm", "run", "build"]:
            events.append("npm-build")
        else:
            events.append("unexpected")
        return 0, "", ""

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else "/usr/bin/uv",
    )
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover") or {})
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError(
                "[WinError 5] Access is denied: 'C:\\Users\\worker\\Desktop\\flocks-main\\webui\\node_modules\\@esbuild\\win32-x64\\esbuild.exe'"
            )
        ),
    )
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert "WinError 5" in progresses[-1].message
    assert events == ["npm-install", "npm-build", "handover", "rollback"]


@pytest.mark.asyncio
async def test_perform_update_does_not_handover_when_staged_frontend_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")

    events: list[str] = []
    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="zip",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    monkeypatch.setattr(
        updater,
        "_get_updater_config",
        fake_get_updater_config,
    )
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)

    async def fake_run_async(cmd, cwd=None, timeout=None):
        if cmd[:3] == ["/usr/bin/npm", "run", "build"]:
            events.append("npm-build")
            return 1, "", "boom"
        if cmd[1] == "install":
            events.append("npm-install")
            return 0, "", ""
        events.append("unexpected")
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else "/usr/bin/uv",
    )
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover") or {})
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: events.append("replace"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert events == ["npm-install", "npm-build"]
