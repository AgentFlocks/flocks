import contextlib
import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from flocks.cli import service_manager


class DummyConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.messages.append(" ".join(str(arg) for arg in args))


def test_runtime_paths_follow_flocks_root_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    paths = service_manager.runtime_paths()

    assert paths.run_dir == tmp_path / "run"
    assert paths.log_dir == tmp_path / "logs"
    assert paths.backend_pid == tmp_path / "run" / "backend.pid"
    assert paths.frontend_log == tmp_path / "logs" / "webui.log"


def test_resolve_node_executable_prefers_flocks_node_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    home = tmp_path / "nh"
    home.mkdir()
    if sys.platform == "win32":
        (home / "node.exe").write_bytes(b"")
    else:
        (home / "bin").mkdir()
        (home / "bin" / "node").write_bytes(b"")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(home))

    resolved = service_manager.resolve_node_executable()
    assert resolved is not None
    assert Path(resolved).name in ("node", "node.exe")


def test_resolve_node_executable_prefers_flocks_install_root_tools_node(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    root = tmp_path / "inst"
    node_home = root / "tools" / "node"
    node_home.mkdir(parents=True)
    if sys.platform == "win32":
        (node_home / "node.exe").write_bytes(b"")
    else:
        (node_home / "bin").mkdir(parents=True)
        (node_home / "bin" / "node").write_bytes(b"")
    monkeypatch.setenv("FLOCKS_INSTALL_ROOT", str(root))

    resolved = service_manager.resolve_node_executable()
    assert resolved is not None
    assert Path(resolved).name in ("node", "node.exe")


def test_resolve_node_executable_falls_back_to_which_when_env_absent(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(service_manager, "which", lambda name: "/usr/bin/node" if name == "node" else None)

    assert service_manager.resolve_node_executable() == "/usr/bin/node"


def test_resolve_node_executable_falls_back_to_which_when_path_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(service_manager, "which", lambda name: "/usr/bin/node" if name == "node" else None)

    assert service_manager.resolve_node_executable() == "/usr/bin/node"


def test_resolve_npm_executable_prefers_flocks_node_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    home = tmp_path / "nh"
    home.mkdir()
    if sys.platform == "win32":
        (home / "node.exe").write_bytes(b"")
        bundled_npm = home / "npm.cmd"
    else:
        (home / "bin").mkdir()
        (home / "bin" / "node").write_bytes(b"")
        bundled_npm = home / "bin" / "npm"
    bundled_npm.write_bytes(b"")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(home))
    monkeypatch.setattr(service_manager, "which", lambda name: "/usr/bin/npm")

    resolved = service_manager.resolve_npm_executable()

    assert resolved == str(bundled_npm)


def test_resolve_npm_executable_falls_back_to_which(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(service_manager.sys, "platform", "win32")

    def fake_which(name: str) -> str | None:
        if name == "npm.cmd":
            return r"C:\Program Files\nodejs\npm.cmd"
        return None

    monkeypatch.setattr(service_manager, "which", fake_which)

    assert service_manager.resolve_npm_executable() == r"C:\Program Files\nodejs\npm.cmd"


def test_build_frontend_env_prepends_bundled_node_to_path(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    node_home = tmp_path / "tools" / "node"
    if sys.platform == "win32":
        node_home.mkdir(parents=True)
        (node_home / "node.exe").write_bytes(b"")
    else:
        (node_home / "bin").mkdir(parents=True)
        (node_home / "bin" / "node").write_bytes(b"")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))

    config = service_manager.ServiceConfig(backend_host="127.0.0.1", backend_port=8000)
    env = service_manager.build_frontend_env(config)

    path_entries = env["PATH"].split(service_manager.os.pathsep)
    if sys.platform == "win32":
        assert path_entries[0] == str(node_home)
    else:
        assert path_entries[0] == str(node_home / "bin")


def test_build_frontend_env_no_path_injection_without_bundled_node(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)

    import os as _os
    original_path = _os.environ.get("PATH", "")
    config = service_manager.ServiceConfig(backend_host="127.0.0.1", backend_port=8000)
    env = service_manager.build_frontend_env(config)

    assert env["PATH"] == original_path


def test_cleanup_stale_pid_file_removes_dead_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("999999", encoding="utf-8")

    service_manager.cleanup_stale_pid_file(pid_file)

    assert not pid_file.exists()


def test_pid_is_running_uses_windows_probe(monkeypatch) -> None:
    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    monkeypatch.setattr(service_manager, "_windows_pid_is_running", lambda pid: pid == 123)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("os.kill should not be used on Windows")

    monkeypatch.setattr(service_manager.os, "kill", fail_if_called)

    assert service_manager.pid_is_running(123) is True
    assert service_manager.pid_is_running(456) is False


def test_process_group_is_running_ignores_permission_error_without_live_members(monkeypatch) -> None:
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(
        service_manager.os,
        "killpg",
        lambda _pgid, _sig: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")),
    )
    monkeypatch.setattr(service_manager, "_process_group_member_pids", lambda _pgid: [])

    assert service_manager.process_group_is_running(222) is False


def test_process_group_is_running_checks_members_after_permission_error(monkeypatch) -> None:
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(
        service_manager.os,
        "killpg",
        lambda _pgid, _sig: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")),
    )
    monkeypatch.setattr(service_manager, "_process_group_member_pids", lambda _pgid: [333, 444])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: pid == 444)

    assert service_manager.process_group_is_running(222) is True


def test_read_runtime_record_supports_legacy_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("12345\n", encoding="utf-8")

    record = service_manager.read_runtime_record(pid_file)

    assert record == service_manager.RuntimeRecord(pid=12345)


def test_runtime_record_round_trip_preserves_metadata(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    record = service_manager.RuntimeRecord(
        pid=4321,
        pgid=4321,
        port=8000,
        command=("python", "-m", "uvicorn"),
        started_at=1234.5,
    )

    service_manager.write_runtime_record(pid_file, record)

    assert json.loads(pid_file.read_text(encoding="utf-8")) == {
        "command": ["python", "-m", "uvicorn"],
        "pgid": 4321,
        "pid": 4321,
        "port": 8000,
        "started_at": 1234.5,
    }
    assert service_manager.read_runtime_record(pid_file) == record


def test_runtime_record_round_trip_preserves_host(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    record = service_manager.RuntimeRecord(
        pid=4321,
        pgid=4321,
        host="0.0.0.0",
        port=8000,
        command=("python", "-m", "uvicorn"),
        started_at=1234.5,
    )

    service_manager.write_runtime_record(pid_file, record)

    assert json.loads(pid_file.read_text(encoding="utf-8")) == {
        "command": ["python", "-m", "uvicorn"],
        "host": "0.0.0.0",
        "pgid": 4321,
        "pid": 4321,
        "port": 8000,
        "started_at": 1234.5,
    }
    assert service_manager.read_runtime_record(pid_file) == record


def test_read_runtime_record_rejects_invalid_content(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("{not-json", encoding="utf-8")

    assert service_manager.read_runtime_record(pid_file) is None


def test_cleanup_stale_pid_file_keeps_live_process_group(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    service_manager.write_runtime_record(
        pid_file,
        service_manager.RuntimeRecord(pid=1001, pgid=2002, port=8000),
    )

    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda pgid: pgid == 2002)

    service_manager.cleanup_stale_pid_file(pid_file)

    assert pid_file.exists()


def test_selected_log_paths_support_specific_targets(tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )

    assert service_manager.selected_log_paths(paths, backend=True) == [paths.backend_log]
    assert service_manager.selected_log_paths(paths, webui=True) == [paths.frontend_log]
    assert service_manager.selected_log_paths(paths) == [paths.backend_log, paths.frontend_log]


def test_tail_lines_returns_recent_content(tmp_path: Path) -> None:
    log_file = tmp_path / "backend.log"
    log_file.write_text("a\nb\nc\n", encoding="utf-8")

    assert service_manager.tail_lines(log_file, 2) == ["b", "c"]


def test_parse_windows_netstat_output_extracts_unique_pids() -> None:
    output = """
  TCP    127.0.0.1:8000       0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:8000       0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:5173       0.0.0.0:0              LISTENING       5678
"""

    assert service_manager._parse_windows_netstat_output(output) == [1234, 5678]


def test_wait_for_http_rejects_unreachable_responses(monkeypatch) -> None:
    responses = iter([
        httpx.Response(503, json={"detail": "not found"}),
        httpx.Response(500, json={"status": "starting"}),
        httpx.Response(502, text="bad gateway"),
    ])

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return next(responses)

    monkeypatch.setattr(
        service_manager.httpx,
        "Client",
        lambda *, timeout, trust_env: _FakeClient(),
    )
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    with pytest.raises(service_manager.ServiceError, match="启动超时"):
        service_manager.wait_for_http(
            ["http://127.0.0.1:8000/api/health"],
            "后端服务",
            attempts=3,
            delay=0.0,
        )


def test_wait_for_http_accepts_reachable_json_response(monkeypatch) -> None:
    responses = iter([
        httpx.Response(503, json={"detail": "warming"}),
        httpx.Response(200, json={"name": "Flocks API", "status": "running"}),
    ])

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return next(responses)

    monkeypatch.setattr(
        service_manager.httpx,
        "Client",
        lambda *, timeout, trust_env: _FakeClient(),
    )
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    service_manager.wait_for_http(
        ["http://127.0.0.1:8000/api/health"],
        "后端服务",
        attempts=2,
        delay=0.0,
    )


def test_wait_for_http_accepts_running_status_response(monkeypatch) -> None:
    responses = iter([
        httpx.Response(200, json={"name": "Flocks API", "status": "starting"}),
        httpx.Response(200, json={"name": "Flocks API", "status": "running"}),
    ])

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return next(responses)

    monkeypatch.setattr(
        service_manager.httpx,
        "Client",
        lambda *, timeout, trust_env: _FakeClient(),
    )
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    service_manager.wait_for_http(
        ["http://127.0.0.1:8000"],
        "后端服务",
        attempts=2,
        delay=0.0,
        validator=service_manager._is_running_status_response,
    )


def test_wait_for_http_rejects_missing_running_status_response(monkeypatch) -> None:
    responses = iter([
        httpx.Response(200, json={"name": "Flocks API", "status": "starting"}),
        httpx.Response(200, text="<html>ok</html>"),
    ])

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return next(responses)

    monkeypatch.setattr(
        service_manager.httpx,
        "Client",
        lambda *, timeout, trust_env: _FakeClient(),
    )
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    with pytest.raises(service_manager.ServiceError, match="启动超时"):
        service_manager.wait_for_http(
            ["http://127.0.0.1:8000"],
            "后端服务",
            attempts=2,
            delay=0.0,
            validator=service_manager._is_running_status_response,
        )


def test_wait_for_http_accepts_reachable_html_by_default(monkeypatch) -> None:
    responses = iter([
        httpx.Response(503, text="warming"),
        httpx.Response(200, text="<html>ok</html>"),
    ])

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return next(responses)

    monkeypatch.setattr(
        service_manager.httpx,
        "Client",
        lambda *, timeout, trust_env: _FakeClient(),
    )
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    service_manager.wait_for_http(["http://127.0.0.1:5173"], "WebUI", attempts=2, delay=0.0)


def test_wait_for_http_ignores_proxy_environment(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            return httpx.Response(200, json={"status": "healthy", "version": "v1"})

    def _client_factory(*, timeout, trust_env):
        captured["timeout"] = timeout
        captured["trust_env"] = trust_env
        return _FakeClient()

    monkeypatch.setattr(service_manager.httpx, "Client", _client_factory)

    service_manager.wait_for_http(
        ["http://127.0.0.1:8000/api/health"],
        "后端服务",
        attempts=1,
        delay=0.0,
    )

    assert captured == {"timeout": 2.0, "trust_env": False}


def test_resolve_python_subprocess_command_prefers_venv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    tool_env = tmp_path / "tool-env"
    tool_python = tool_env / "bin" / "python"
    tool_python.parent.mkdir(parents=True)
    tool_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        service_manager,
        "_python_env_root_from_module",
        lambda module_name: tool_env if module_name == "uvicorn" else None,
    )

    assert service_manager.resolve_python_subprocess_command(tmp_path) == [str(venv_python)]


def test_resolve_python_subprocess_command_falls_back_to_module_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tool_env = tmp_path / "tool-env"
    python_exe = tool_env / "bin" / "python"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        service_manager,
        "_python_env_root_from_module",
        lambda module_name: tool_env if module_name == "uvicorn" else None,
    )

    assert service_manager.resolve_python_subprocess_command(tmp_path) == [str(python_exe)]


def test_resolve_flocks_cli_command_prefers_venv_entry_point_unix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    venv_flocks = tmp_path / ".venv" / "bin" / "flocks"
    venv_flocks.parent.mkdir(parents=True)
    venv_flocks.write_text("", encoding="utf-8")

    monkeypatch.setattr(service_manager, "which", lambda name: "/usr/local/bin/flocks" if name == "flocks" else None)

    assert service_manager.resolve_flocks_cli_command(tmp_path) == [str(venv_flocks.resolve())]


def test_resolve_flocks_cli_command_uses_python_module_on_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(service_manager, "which", lambda name: r"C:\tools\flocks.exe" if name == "flocks" else None)

    assert service_manager.resolve_flocks_cli_command(tmp_path) == [
        str(venv_python),
        "-m",
        "flocks.cli.main",
    ]


def test_resolve_flocks_cli_command_falls_back_to_which(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_manager, "which", lambda name: "/usr/local/bin/flocks" if name == "flocks" else None)

    assert service_manager.resolve_flocks_cli_command(tmp_path) == ["/usr/local/bin/flocks"]


def test_resolve_flocks_cli_command_falls_back_to_python_module(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_manager, "_flocks_executable_from_venv", lambda _venv_root: None)
    monkeypatch.setattr(service_manager, "which", lambda _name: None)
    monkeypatch.setattr(service_manager.sys, "argv", ["python"])
    monkeypatch.setattr(service_manager, "resolve_python_subprocess_command", lambda root=None: ["/env/bin/python"])

    assert service_manager.resolve_flocks_cli_command(tmp_path) == [
        "/env/bin/python",
        "-m",
        "flocks.cli.main",
    ]


def test_build_status_lines_reports_running_and_idle_services(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    paths.backend_pid.write_text("111", encoding="utf-8")
    paths.frontend_pid.write_text("222", encoding="utf-8")

    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _: None)
    monkeypatch.setattr(
        service_manager,
        "port_owner_pids",
        lambda port: [111] if port == 8000 else [],
    )
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: pid == 222)

    lines = service_manager.build_status_lines(paths)

    assert "后端运行中" in lines[0]
    assert "WebUI 主进程仍在运行" in lines[1]


def test_build_status_lines_uses_custom_server_and_webui_ports(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    service_manager.write_runtime_record(
        paths.backend_pid,
        service_manager.RuntimeRecord(pid=111, host="0.0.0.0", port=9000),
    )
    service_manager.write_runtime_record(
        paths.frontend_pid,
        service_manager.RuntimeRecord(pid=222, host="0.0.0.0", port=5174),
    )

    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _: None)
    monkeypatch.setattr(
        service_manager,
        "port_owner_pids",
        lambda port: [111] if port in {9000, 5174} else [],
    )
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)

    lines = service_manager.build_status_lines(paths)

    assert "http://127.0.0.1:9000" in lines[0]
    assert "http://127.0.0.1:5174" in lines[1]


def test_start_all_stops_services_before_starting(monkeypatch) -> None:
    call_order: list[str] = []
    paths = service_manager.RuntimePaths(
        root=Path("/tmp"),
        run_dir=Path("/tmp/run"),
        log_dir=Path("/tmp/logs"),
        backend_pid=Path("/tmp/run/backend.pid"),
        frontend_pid=Path("/tmp/run/webui.pid"),
        backend_log=Path("/tmp/logs/backend.log"),
        frontend_log=Path("/tmp/logs/webui.log"),
    )

    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: (call_order.append("ensure_runtime_dirs"), paths)[1])
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call(call_order, "service_lock"))
    monkeypatch.setattr(service_manager, "stop_one", lambda port, _pid_file, _name, _console: call_order.append(f"stop_one:{port}"))
    monkeypatch.setattr(service_manager, "_start_all_without_stop", lambda _config, _console: call_order.append("_start_all_without_stop"))

    service_manager.start_all(service_manager.ServiceConfig(), console=None)

    assert call_order == [
        "ensure_runtime_dirs",
        "service_lock",
        "stop_one:5173",
        "stop_one:8000",
        "_start_all_without_stop",
    ]


def test_restart_all_stops_then_starts_under_lock(monkeypatch) -> None:
    call_order: list[str] = []
    paths = service_manager.RuntimePaths(
        root=Path("/tmp"),
        run_dir=Path("/tmp/run"),
        log_dir=Path("/tmp/logs"),
        backend_pid=Path("/tmp/run/backend.pid"),
        frontend_pid=Path("/tmp/run/webui.pid"),
        backend_log=Path("/tmp/logs/backend.log"),
        frontend_log=Path("/tmp/logs/webui.log"),
    )

    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: (call_order.append("ensure_runtime_dirs"), paths)[1])
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call(call_order, "service_lock"))
    monkeypatch.setattr(service_manager, "stop_one", lambda port, _pid_file, _name, _console: call_order.append(f"stop_one:{port}"))
    monkeypatch.setattr(service_manager, "_start_all_without_stop", lambda _config, _console: call_order.append("_start_all_without_stop"))

    service_manager.restart_all(service_manager.ServiceConfig(), console=None)

    assert call_order == [
        "ensure_runtime_dirs",
        "service_lock",
        "stop_one:5173",
        "stop_one:8000",
        "_start_all_without_stop",
    ]


def test_start_all_stops_on_failure_before_restart(monkeypatch) -> None:
    paths = service_manager.RuntimePaths(
        root=Path("/tmp"),
        run_dir=Path("/tmp/run"),
        log_dir=Path("/tmp/logs"),
        backend_pid=Path("/tmp/run/backend.pid"),
        frontend_pid=Path("/tmp/run/webui.pid"),
        backend_log=Path("/tmp/logs/backend.log"),
        frontend_log=Path("/tmp/logs/webui.log"),
    )
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call([], "service_lock"))
    monkeypatch.setattr(
        service_manager,
        "stop_one",
        lambda *_args: (_ for _ in ()).throw(service_manager.ServiceError("stop failed")),
    )
    monkeypatch.setattr(
        service_manager,
        "_start_all_without_stop",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not start")),
    )

    with pytest.raises(service_manager.ServiceError, match="stop failed"):
        service_manager.start_all(service_manager.ServiceConfig(), console=None)


def test_start_backend_writes_runtime_metadata(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    console = DummyConsole()

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    probe_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        service_manager,
        "wait_for_http",
        lambda urls, name, attempts=30, delay=1.0, validator=None: probe_calls.append({
            "urls": list(urls),
            "name": name,
            "attempts": attempts,
            "delay": delay,
            "validator": validator,
        }),
    )
    monkeypatch.setattr(service_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        service_manager,
        "resolve_flocks_cli_command",
        lambda root=None: ["python", "-m", "flocks.cli.main"],
    )
    spawn_env: dict[str, str] | None = None

    def _capture_spawn(*_args, **kwargs) -> SimpleNamespace:
        nonlocal spawn_env
        spawn_env = kwargs.get("env")
        return SimpleNamespace(pid=2468)

    monkeypatch.setattr(service_manager, "_spawn_process", _capture_spawn)

    service_manager.start_backend(service_manager.ServiceConfig(), console)

    record = service_manager.read_runtime_record(paths.backend_pid)
    assert record is not None
    assert record.pid == 2468
    assert record.pgid == 2468
    assert record.host == "127.0.0.1"
    assert record.port == 8000
    assert record.command == (
        "python",
        "-m",
        "flocks.cli.main",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    )
    assert probe_calls == [{
        "urls": ["http://127.0.0.1:8000"],
        "name": "后端服务",
        "attempts": 30,
        "delay": 3.0,
        "validator": service_manager._is_running_status_response,
    }]
    assert spawn_env is not None
    assert spawn_env.get("PYTHONUNBUFFERED") == "1"


def test_start_backend_rolls_back_when_probe_fails(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    paths.backend_log.write_text("line1\nline2\nboot failed here\n", encoding="utf-8")
    console = DummyConsole()
    stop_calls: list[tuple[int, Path, str]] = []

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        service_manager,
        "resolve_flocks_cli_command",
        lambda root=None: ["python", "-m", "flocks.cli.main"],
    )
    monkeypatch.setattr(
        service_manager,
        "_spawn_process",
        lambda *_args, **_kwargs: SimpleNamespace(pid=2468),
    )
    monkeypatch.setattr(
        service_manager,
        "wait_for_http",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(service_manager.ServiceError("后端服务 启动超时，请检查日志。")),
    )
    monkeypatch.setattr(
        service_manager,
        "stop_one",
        lambda port, pid_file, name, _console: stop_calls.append((port, pid_file, name)),
    )

    with pytest.raises(service_manager.ServiceError, match="启动超时"):
        service_manager.start_backend(service_manager.ServiceConfig(), console)

    assert stop_calls == [(8000, paths.backend_pid, "后端")]
    joined = "\n".join(console.messages)
    assert "近期日志" in joined
    assert "boot failed here" in joined


def test_start_backend_reports_started_after_probe_succeeds(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    console = DummyConsole()
    spawn_calls: list[dict[str, object]] = []

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        service_manager,
        "resolve_flocks_cli_command",
        lambda root=None: ["python", "-m", "flocks.cli.main"],
    )
    monkeypatch.setattr(
        service_manager,
        "_spawn_process",
        lambda *args, **kwargs: spawn_calls.append({"args": args, "kwargs": kwargs}) or SimpleNamespace(pid=2468),
    )
    monkeypatch.setattr(
        service_manager,
        "wait_for_http",
        lambda *_args, **_kwargs: None,
    )

    service_manager.start_backend(service_manager.ServiceConfig(), console)

    record = service_manager.read_runtime_record(paths.backend_pid)
    assert record is not None
    assert record.pid == 2468
    backend_env = spawn_calls[0]["kwargs"]["env"]
    assert backend_env["_FLOCKS_WEBUI_HOST"] == "127.0.0.1"
    assert backend_env["_FLOCKS_WEBUI_PORT"] == "5173"
    assert console.messages[-1] == f"[flocks] 后端已启动，日志: {paths.backend_log}"


def test_build_frontend_env_uses_backend_host_and_port() -> None:
    config = service_manager.ServiceConfig(
        backend_host="10.0.0.8",
        backend_port=9000,
    )

    env = service_manager.build_frontend_env(config)

    assert env["FLOCKS_API_PROXY_TARGET"] == "http://10.0.0.8:9000"
    assert env["VITE_API_BASE_URL"] == "http://10.0.0.8:9000"
    assert env["VITE_WS_BASE_URL"] == "ws://10.0.0.8:9000"


def test_build_frontend_env_brackets_ipv6_backend_host() -> None:
    config = service_manager.ServiceConfig(
        backend_host="2001:db8::1",
        backend_port=9000,
    )

    env = service_manager.build_frontend_env(config)

    assert env["FLOCKS_API_PROXY_TARGET"] == "http://[2001:db8::1]:9000"
    assert env["VITE_API_BASE_URL"] == "http://[2001:db8::1]:9000"
    assert env["VITE_WS_BASE_URL"] == "ws://[2001:db8::1]:9000"


def test_build_frontend_env_uses_loopback_for_wildcard_backend_host() -> None:
    config = service_manager.ServiceConfig(
        backend_host="0.0.0.0",
        backend_port=9000,
    )

    env = service_manager.build_frontend_env(config)

    assert env["FLOCKS_API_PROXY_TARGET"] == "http://127.0.0.1:9000"
    assert "VITE_API_BASE_URL" not in env
    assert "VITE_WS_BASE_URL" not in env


def test_build_frontend_env_keeps_proxy_mode_for_loopback_backend_host(monkeypatch) -> None:
    monkeypatch.setenv("VITE_API_BASE_URL", "http://stale.example:9000")
    monkeypatch.setenv("VITE_WS_BASE_URL", "ws://stale.example:9000")
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=9000,
    )

    env = service_manager.build_frontend_env(config)

    assert env["FLOCKS_API_PROXY_TARGET"] == "http://127.0.0.1:9000"
    assert "VITE_API_BASE_URL" not in env
    assert "VITE_WS_BASE_URL" not in env


def test_start_frontend_passes_backend_urls_to_build_and_preview(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    console = DummyConsole()
    build_calls: list[dict[str, object]] = []
    preview_calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        build_calls.append({"command": command, "kwargs": kwargs})
        return SimpleNamespace(returncode=0)

    def fake_spawn(command, **kwargs):
        preview_calls.append({"command": command, "kwargs": kwargs})
        return SimpleNamespace(pid=2468)

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "wait_for_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)
    monkeypatch.setattr(service_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(service_manager, "_spawn_process", fake_spawn)
    monkeypatch.setenv("__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS", "preview.example.com")

    config = service_manager.ServiceConfig(
        backend_host="10.0.0.8",
        backend_port=9000,
        frontend_host="0.0.0.0",
        frontend_port=5174,
    )
    service_manager.start_frontend(config, console)

    assert build_calls[0]["command"] == ["/usr/bin/npm", "run", "build"]
    assert build_calls[0]["kwargs"]["env"]["FLOCKS_API_PROXY_TARGET"] == "http://10.0.0.8:9000"
    assert build_calls[0]["kwargs"]["env"]["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == "preview.example.com"
    assert build_calls[0]["kwargs"]["env"]["VITE_API_BASE_URL"] == "http://10.0.0.8:9000"
    assert build_calls[0]["kwargs"]["env"]["VITE_WS_BASE_URL"] == "ws://10.0.0.8:9000"

    assert preview_calls[0]["command"] == [
        "/usr/bin/npm",
        "run",
        "preview",
        "--",
        "--host",
        "0.0.0.0",
        "--port",
        "5174",
    ]
    assert preview_calls[0]["kwargs"]["env"]["FLOCKS_API_PROXY_TARGET"] == "http://10.0.0.8:9000"
    assert preview_calls[0]["kwargs"]["env"]["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == "preview.example.com"
    assert preview_calls[0]["kwargs"]["env"]["VITE_API_BASE_URL"] == "http://10.0.0.8:9000"
    assert preview_calls[0]["kwargs"]["env"]["VITE_WS_BASE_URL"] == "ws://10.0.0.8:9000"
    record = service_manager.read_runtime_record(paths.frontend_pid)
    assert record is not None
    assert record.host == "0.0.0.0"
    assert record.port == 5174


def test_start_frontend_prefers_bundled_npm_over_path_lookup(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    console = DummyConsole()
    build_calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        build_calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "wait_for_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(service_manager, "resolve_npm_executable", lambda: r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\node\npm.cmd")
    monkeypatch.setattr(service_manager, "node_version_satisfies_requirement", lambda: True)
    monkeypatch.setattr(service_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(service_manager, "_spawn_process", lambda *_args, **_kwargs: SimpleNamespace(pid=2468))

    service_manager.start_frontend(service_manager.ServiceConfig(), console)

    assert build_calls[0][0] == r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\node\npm.cmd"


def test_start_backend_raises_on_port_record_mismatch(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    service_manager.write_runtime_record(paths.backend_pid, service_manager.RuntimeRecord(pid=1111, port=8000))

    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [9999])

    with pytest.raises(service_manager.ServiceError, match="运行时记录不一致"):
        service_manager.start_backend(service_manager.ServiceConfig(), DummyConsole())


def test_spawn_process_uses_hidden_window_flags_on_windows(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    log_path = tmp_path / "logs" / "backend.log"

    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = 1

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    monkeypatch.setattr(service_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service_manager.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(service_manager.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(service_manager.subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(service_manager.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(service_manager.subprocess, "STARTF_USESHOWWINDOW", 0x1, raising=False)
    monkeypatch.setattr(service_manager.subprocess, "SW_HIDE", 0, raising=False)

    process = service_manager._spawn_process(["python", "-m", "uvicorn"], cwd=tmp_path, log_path=log_path)

    assert process.pid == 4321
    assert captured["args"] == (["python", "-m", "uvicorn"],)
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["creationflags"] == 0x200 | 0x08000000
    assert captured["kwargs"]["creationflags"] & 0x8 == 0
    assert "start_new_session" not in captured["kwargs"]
    assert captured["kwargs"]["stdin"] == service_manager.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == service_manager.subprocess.STDOUT
    assert captured["kwargs"]["startupinfo"].dwFlags == 0x1
    assert captured["kwargs"]["startupinfo"].wShowWindow == 0


def test_spawn_process_uses_new_session_on_non_windows(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    log_path = tmp_path / "logs" / "backend.log"

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=9876)

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager.subprocess, "Popen", fake_popen)

    process = service_manager._spawn_process(["python", "-m", "uvicorn"], cwd=tmp_path, log_path=log_path)

    assert process.pid == 9876
    assert captured["args"] == (["python", "-m", "uvicorn"],)
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["creationflags"] == 0
    assert captured["kwargs"]["start_new_session"] is True
    assert "startupinfo" not in captured["kwargs"]


def test_spawn_process_passes_custom_environment(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    log_path = tmp_path / "logs" / "backend.log"
    env = {"FLOCKS_API_PROXY_TARGET": "http://127.0.0.1:9000"}

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=1111)

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager.subprocess, "Popen", fake_popen)

    process = service_manager._spawn_process(["python", "-m", "uvicorn"], cwd=tmp_path, log_path=log_path, env=env)

    assert process.pid == 1111
    assert captured["kwargs"]["env"] == env


def test_stop_one_prefers_process_group_on_unix(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    service_manager.write_runtime_record(
        pid_file,
        service_manager.RuntimeRecord(pid=111, pgid=222, port=8000),
    )
    console = DummyConsole()
    group_alive = {"value": True}
    group_signals: list[tuple[signal.Signals, int | None]] = []
    pid_signals: list[tuple[signal.Signals, list[int]]] = []

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "collect_process_tree_pids", lambda _pid: [111, 112])
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda pgid: bool(pgid == 222 and group_alive["value"]))

    def fake_signal_group(sig, pgid):
        group_signals.append((sig, pgid))
        if sig == signal.SIGTERM:
            group_alive["value"] = False

    monkeypatch.setattr(service_manager, "signal_process_group", fake_signal_group)
    monkeypatch.setattr(
        service_manager,
        "signal_pid_list",
        lambda sig, pids: pid_signals.append((sig, list(pids))),
    )

    service_manager.stop_one(8000, pid_file, "后端", console)

    assert group_signals == [(signal.SIGTERM, 222)]
    assert pid_signals == []
    assert not pid_file.exists()


def test_stop_one_falls_back_to_pid_signals_without_process_group(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("111", encoding="utf-8")
    console = DummyConsole()
    pid_signals: list[tuple[signal.Signals, list[int]]] = []
    alive = {"value": True}

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "collect_process_tree_pids", lambda _pid: [111, 112])
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: alive["value"])
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda _pgid: False)
    monkeypatch.setattr(
        service_manager,
        "signal_pid_list",
        lambda sig, pids: (
            pid_signals.append((sig, list(pids))),
            alive.__setitem__("value", False),
        ),
    )

    service_manager.stop_one(8000, pid_file, "后端", console)

    assert pid_signals[0] == (signal.SIGTERM, [111, 112])
    assert not pid_file.exists()


def test_stop_one_uses_taskkill_on_windows(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("111", encoding="utf-8")
    console = DummyConsole()
    commands: list[list[str]] = []
    alive = {"value": True}

    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    monkeypatch.setattr(service_manager, "collect_process_tree_pids", lambda _pid: [111, 222])
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: alive["value"])

    def fake_run(args, **kwargs):
        commands.append(list(args))
        alive["value"] = False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service_manager.subprocess, "run", fake_run)

    service_manager.stop_one(8000, pid_file, "后端", console)

    assert commands == [
        ["taskkill", "/PID", "111", "/T", "/F"],
        ["taskkill", "/PID", "222", "/T", "/F"],
    ]


def test_stop_one_force_kill_refreshes_process_group_members(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    service_manager.write_runtime_record(
        pid_file,
        service_manager.RuntimeRecord(pid=111, pgid=222, port=8000),
    )
    console = DummyConsole()
    pid_signals: list[tuple[signal.Signals, list[int]]] = []
    group_signals: list[tuple[signal.Signals, int | None]] = []
    alive_group_members = {333}

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "collect_process_tree_pids", lambda _pid: [111])
    monkeypatch.setattr(service_manager, "_process_group_member_pids", lambda pgid: [333] if pgid == 222 and alive_group_members else [])
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: pid in alive_group_members)
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda pgid: bool(pgid == 222 and alive_group_members))
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    def fake_signal_group(sig, pgid):
        group_signals.append((sig, pgid))

    def fake_signal_pid_list(sig, pids):
        pid_list = list(pids)
        pid_signals.append((sig, pid_list))
        if sig == signal.SIGKILL and 333 in pid_list:
            alive_group_members.clear()

    monkeypatch.setattr(service_manager, "signal_process_group", fake_signal_group)
    monkeypatch.setattr(service_manager, "signal_pid_list", fake_signal_pid_list)

    service_manager.stop_one(8000, pid_file, "后端", console)

    assert (signal.SIGTERM, 222) in group_signals
    assert any(sig == signal.SIGKILL and 333 in pids for sig, pids in pid_signals)
    assert not pid_file.exists()
    assert console.messages[-1] == "[flocks] 后端 已停止。"


def test_stop_one_keeps_runtime_record_when_force_kill_still_times_out(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    service_manager.write_runtime_record(
        pid_file,
        service_manager.RuntimeRecord(pid=111, pgid=222, port=8000),
    )
    console = DummyConsole()

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "collect_process_tree_pids", lambda _pid: [111])
    monkeypatch.setattr(service_manager, "_process_group_member_pids", lambda pgid: [333] if pgid == 222 else [])
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda pgid: pgid == 222)
    monkeypatch.setattr(service_manager, "signal_process_group", lambda *_args: None)
    monkeypatch.setattr(service_manager, "signal_pid_list", lambda *_args: None)
    monkeypatch.setattr(service_manager.time, "sleep", lambda _delay: None)

    with pytest.raises(service_manager.ServiceError, match="未在预期时间内退出"):
        service_manager.stop_one(8000, pid_file, "后端", console)

    assert pid_file.exists()


def test_orphaned_flocks_worker_pids_requires_spawn_marker_and_flocks_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "flocks"
    state = tmp_path / ".flocks"
    repo.mkdir()
    state.mkdir()

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "repo_root", lambda: repo)
    monkeypatch.setattr(service_manager, "flocks_root", lambda: state)
    monkeypatch.setattr(
        service_manager,
        "_process_table_rows",
        lambda: [
            (
                444,
                1,
                "/opt/homebrew/bin/Python -c from multiprocessing.spawn import spawn_main; "
                "spawn_main(tracker_fd=5, pipe_handle=7) --multiprocessing-fork",
            ),
            (
                555,
                1,
                "/opt/homebrew/bin/Python -c from multiprocessing.spawn import spawn_main; "
                "spawn_main(tracker_fd=5, pipe_handle=7) --multiprocessing-fork",
            ),
            (
                666,
                123,
                "/opt/homebrew/bin/Python -c from multiprocessing.spawn import spawn_main; "
                "spawn_main(tracker_fd=5, pipe_handle=7) --multiprocessing-fork",
            ),
            (777, 1, "/opt/homebrew/bin/Python /tmp/other.py"),
        ],
    )
    monkeypatch.setattr(
        service_manager,
        "_process_lsof_output",
        lambda pid: (
            f"Python {pid} cwd DIR {repo}\nPython {pid} txt REG {repo / '.venv' / 'lib'}\n"
            if pid == 444
            else f"Python {pid} cwd DIR /tmp/unrelated\n"
        ),
    )

    assert service_manager.orphaned_flocks_worker_pids() == [444]


def test_stop_one_cleans_orphaned_flocks_workers_without_pid_file_or_listener(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "backend.pid"
    console = DummyConsole()
    alive = {444}
    pid_signals: list[tuple[signal.Signals, list[int]]] = []

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda _port: [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: pid in alive)
    monkeypatch.setattr(service_manager, "process_group_is_running", lambda _pgid: False)
    monkeypatch.setattr(service_manager, "orphaned_flocks_worker_pids", lambda: sorted(alive))

    def fake_signal_pid_list(sig, pids):
        pid_list = list(pids)
        pid_signals.append((sig, pid_list))
        if sig == signal.SIGTERM:
            alive.difference_update(pid_list)

    monkeypatch.setattr(service_manager, "signal_pid_list", fake_signal_pid_list)

    service_manager.stop_one(8000, pid_file, "后端", console)

    assert pid_signals == [(signal.SIGTERM, [444])]
    assert console.messages[-1] == "[flocks] 后端 已停止。"


@contextlib.contextmanager
def _record_call(call_order: list[str], name: str):
    call_order.append(name)
    yield


def test_stop_all_reads_port_from_runtime_record(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    service_manager.write_runtime_record(paths.backend_pid, service_manager.RuntimeRecord(pid=111, port=9995))
    service_manager.write_runtime_record(paths.frontend_pid, service_manager.RuntimeRecord(pid=222, port=9996))
    calls: list[tuple[int, Path, str]] = []

    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call([], "service_lock"))
    monkeypatch.setattr(
        service_manager,
        "stop_one",
        lambda port, pid_file, name, _console: calls.append((port, pid_file, name)),
    )

    service_manager.stop_all(console=None)

    assert calls == [
        (9996, paths.frontend_pid, "WebUI"),
        (9995, paths.backend_pid, "后端"),
    ]


def test_stop_all_falls_back_to_default_port_when_record_missing(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    calls: list[int] = []

    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call([], "service_lock"))
    monkeypatch.setattr(service_manager, "stop_one", lambda port, *_args: calls.append(port))

    service_manager.stop_all(console=None)

    assert calls == [5173, 8000]


def test_stop_all_falls_back_to_default_port_when_record_has_no_port(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.backend_pid.write_text("111", encoding="utf-8")
    paths.frontend_pid.write_text("222", encoding="utf-8")
    calls: list[int] = []

    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "service_lock", lambda _paths: _record_call([], "service_lock"))
    monkeypatch.setattr(service_manager, "stop_one", lambda port, *_args: calls.append(port))

    service_manager.stop_all(console=None)

    assert calls == [5173, 8000]


def test_build_status_lines_reads_port_from_runtime_record(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    service_manager.write_runtime_record(paths.backend_pid, service_manager.RuntimeRecord(pid=111, port=9995))
    service_manager.write_runtime_record(paths.frontend_pid, service_manager.RuntimeRecord(pid=222, port=9996))

    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda port: [port] if port in {9995, 9996} else [])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)

    lines = service_manager.build_status_lines(paths)

    assert "http://127.0.0.1:9995" in lines[0]
    assert "http://127.0.0.1:9996" in lines[1]


def test_build_status_lines_uses_recorded_host(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    service_manager.write_runtime_record(
        paths.backend_pid,
        service_manager.RuntimeRecord(pid=111, host="10.0.0.8", port=9000),
    )
    service_manager.write_runtime_record(
        paths.frontend_pid,
        service_manager.RuntimeRecord(pid=222, host="0.0.0.0", port=5174),
    )

    monkeypatch.setattr(service_manager, "cleanup_stale_pid_file", lambda _path: None)
    monkeypatch.setattr(service_manager, "port_owner_pids", lambda port: [111] if port == 9000 else [222])
    monkeypatch.setattr(service_manager, "pid_is_running", lambda _pid: False)

    lines = service_manager.build_status_lines(paths)

    assert "http://10.0.0.8:9000" in lines[0]
    assert "http://127.0.0.1:5174" in lines[1]


def test_service_lock_prevents_concurrent_operations(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    state = {"locked": False}

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(_handle, operation):
            if operation == FakeFcntl.LOCK_UN:
                state["locked"] = False
                return
            if state["locked"]:
                raise OSError("busy")
            state["locked"] = True

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "fcntl", FakeFcntl)

    with service_manager.service_lock(paths):
        with pytest.raises(service_manager.ServiceError, match="另一个 flocks 命令正在执行"):
            with service_manager.service_lock(paths):
                raise AssertionError("should not acquire nested lock")


def test_service_lock_releases_on_completion(monkeypatch, tmp_path: Path) -> None:
    paths = service_manager.RuntimePaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        backend_pid=tmp_path / "run" / "backend.pid",
        frontend_pid=tmp_path / "run" / "webui.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "webui.log",
    )
    operations: list[int] = []

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(_handle, operation):
            operations.append(operation)

    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "fcntl", FakeFcntl)

    with service_manager.service_lock(paths):
        pass

    assert operations == [FakeFcntl.LOCK_EX | FakeFcntl.LOCK_NB, FakeFcntl.LOCK_UN]


def test_log_startup_config_appends_to_log_file(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    record = service_manager.RuntimeRecord(pid=2468, pgid=2468, host="0.0.0.0", port=8000)

    service_manager._log_startup_config(log_path, "backend", "0.0.0.0", 8000, record)

    content = log_path.read_text(encoding="utf-8")
    assert "backend starting: host=0.0.0.0 port=8000 pid=2468 pgid=2468" in content
