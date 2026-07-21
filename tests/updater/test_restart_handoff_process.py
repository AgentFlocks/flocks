import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flocks.cli import service_manager
from flocks.cli.service_config import service_config_payload


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses Unix executable shims and symlinks")

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALE_HANDOFF_ARGS = [
    pytest.param([], id="english-upgrade"),
    pytest.param(
        [
            "--uv-default-index",
            "https://mirrors.aliyun.com/pypi/simple",
            "--npm-registry",
            "https://registry.npmmirror.com/",
        ],
        id="chinese-upgrade",
    ),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_isolated_runtime(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "pyproject.toml").write_text(
        '[project]\nname = "handoff-process-test"\nversion = "0.0.0"\nrequires-python = ">=3.12"\ndependencies = []\n',
        encoding="utf-8",
    )
    runtime_python = install_root / ".venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.symlink_to(sys.executable)

    uv_path = shutil.which("uv")
    if uv_path is None:
        pytest.skip("uv is required for the real handoff process test")

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "FLOCKS_ROOT": str(tmp_path / "flocks-root"),
            "HOME": str(isolated_home),
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    env.pop("PYTHONPATH", None)
    return install_root, Path(uv_path), env


def _parent_process() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        text=True,
    )


def _base_handoff_command(
    *,
    parent_pid: int,
    install_root: Path,
    uv_path: Path,
    backend_port: int,
    frontend_port: int,
    current_version: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "flocks.updater.restart_handoff",
        "--parent-pid",
        str(parent_pid),
        "--backend-host",
        "127.0.0.1",
        "--backend-port",
        str(backend_port),
        "--frontend-host",
        "127.0.0.1",
        "--frontend-port",
        str(frontend_port),
        "--install-root",
        str(install_root),
        "--uv-path",
        str(uv_path),
        "--sync-timeout",
        "10",
        "--version",
        "2026.7.15",
        "--current-version",
        current_version,
    ]


def _restart_marker_command(install_root: Path, marker: Path, value: str) -> list[str]:
    runtime_python = install_root / ".venv" / "bin" / "python"
    script = f"from pathlib import Path; Path({str(marker)!r}).write_text({value!r}, encoding='utf-8')"
    return [str(runtime_python), "-c", script]


def _run_handoff(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _wait_for_marker(marker: Path, expected: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if marker.is_file() and marker.read_text(encoding="utf-8") == expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"restart marker was not written: {marker}")


@pytest.mark.parametrize("locale_args", LOCALE_HANDOFF_ARGS)
def test_v2026_7_1_real_handoff_command_restarts_with_current_code(
    tmp_path: Path,
    locale_args: list[str],
) -> None:
    install_root, fake_uv, env = _prepare_isolated_runtime(tmp_path)
    marker = tmp_path / "v2026.7.1-restarted"
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    parent = _parent_process()
    command = _base_handoff_command(
        parent_pid=parent.pid,
        install_root=install_root,
        uv_path=fake_uv,
        backend_port=_free_port(),
        frontend_port=_free_port(),
        current_version="2026.7.1",
    )
    install_index = command.index("--install-root")
    command[install_index:install_index] = [
        "--backend-pid-file",
        str(tmp_path / "backend.pid"),
    ]
    command.extend(
        [
            *locale_args,
            "--backup-path",
            str(backup_path),
            "--",
            *_restart_marker_command(install_root, marker, "v2026.7.1"),
        ]
    )

    completed = _run_handoff(command, env=env)
    parent.wait(timeout=5)

    assert completed.returncode == 0, completed.stderr
    _wait_for_marker(marker, "v2026.7.1")


@pytest.mark.parametrize("locale_args", LOCALE_HANDOFF_ARGS)
def test_v2026_7_15_real_handoff_command_restarts_with_current_code(
    tmp_path: Path,
    locale_args: list[str],
) -> None:
    install_root, fake_uv, env = _prepare_isolated_runtime(tmp_path)
    marker = tmp_path / "v2026.7.15-restarted"
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    public_port = _free_port()
    parent = _parent_process()
    command = _base_handoff_command(
        parent_pid=parent.pid,
        install_root=install_root,
        uv_path=fake_uv,
        backend_port=public_port,
        frontend_port=public_port,
        current_version="2026.7.15",
    )
    command.extend(
        [
            *locale_args,
            "--backup-path",
            str(backup_path),
            "--prepare-handover",
            "--",
            *_restart_marker_command(install_root, marker, "v2026.7.15"),
        ]
    )

    completed = _run_handoff(command, env=env)
    parent.wait(timeout=5)

    assert completed.returncode == 0, completed.stderr
    _wait_for_marker(marker, "v2026.7.15")


@pytest.mark.parametrize("locale_args", LOCALE_HANDOFF_ARGS)
def test_current_real_upgrade_handoff_command_restarts_with_replaced_source(
    tmp_path: Path,
    locale_args: list[str],
) -> None:
    install_root, fake_uv, env = _prepare_isolated_runtime(tmp_path)
    marker = tmp_path / "current-restarted"
    env["FLOCKS_RESTART_MARKER"] = str(marker)

    content_root = tmp_path / "staged"
    fake_cli = content_root / "flocks" / "cli"
    fake_cli.mkdir(parents=True)
    (content_root / "flocks" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(install_root / "pyproject.toml", content_root / "pyproject.toml")
    (fake_cli / "__init__.py").write_text("", encoding="utf-8")
    (fake_cli / "main.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['FLOCKS_RESTART_MARKER']).write_text('current', encoding='utf-8')\n",
        encoding="utf-8",
    )
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")

    backend_port = _free_port()
    frontend_port = _free_port()
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=backend_port,
        frontend_host="127.0.0.1",
        frontend_port=frontend_port,
        no_browser=True,
        skip_frontend_build=True,
    )
    parent = _parent_process()
    command = _base_handoff_command(
        parent_pid=parent.pid,
        install_root=install_root,
        uv_path=fake_uv,
        backend_port=backend_port,
        frontend_port=frontend_port,
        current_version="2026.7.15",
    )
    command.extend(
        [
            *locale_args,
            "--mode",
            "upgrade",
            "--content-root",
            str(content_root),
            "--backup-path",
            str(backup_path),
            "--was-running",
            "--service-config-json",
            json.dumps(service_config_payload(config)),
            "--",
            str(install_root / ".venv" / "bin" / "python"),
        ]
    )

    completed = _run_handoff(command, env=env)
    parent.wait(timeout=5)

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == "current"
    assert (install_root / "flocks" / "cli" / "main.py").is_file()
