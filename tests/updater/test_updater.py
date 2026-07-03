import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from os import utime
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.cli import service_control, service_manager
from flocks.updater import updater
from tests.helpers.service_supervisor import (
    make_short_runtime_root,
    start_supervisor,
    stop_supervisor,
    wait_for_process_exit,
    wait_for_supervisor,
)


def _write_pyproject_version(pyproject_path: Path, version: str) -> None:
    pyproject_path.write_text(
        '[project]\nname = "flocks"\nversion = "' + version + '"\n',
        encoding="utf-8",
    )


def _prepare_real_restart_runtime(install_root: Path) -> None:
    for python_path in (
        install_root / ".venv" / "bin" / "python",
        install_root / ".venv" / "Scripts" / "python.exe",
    ):
        python_path.parent.mkdir(parents=True, exist_ok=True)
        if python_path.exists() or python_path.is_symlink():
            continue
        symlinked = False
        try:
            python_path.symlink_to(sys.executable)
            symlinked = True
        except OSError:
            shutil.copy2(sys.executable, python_path)
    if not symlinked:
        python_path.chmod(0o755)


def _webui_control_payload(state: str = "healthy", last_error: str | None = None) -> dict[str, object]:
    return {
        "webui": {
            "state": state,
            "last_error": last_error,
        },
    }


def _webui_control_status(
    state: str = "healthy",
    last_error: str | None = None,
) -> service_control.SupervisorStatus:
    return service_control.parse_supervisor_status(_webui_control_payload(state, last_error))


def test_current_service_config_requires_supervisor_control_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_control,
        "read_supervisor_status",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("control down")),
    )

    with pytest.raises(RuntimeError, match="Supervisor control API is unavailable"):
        updater._current_service_config()


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


@pytest.mark.asyncio
async def test_await_ignoring_cancellation_backs_off_on_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shield_calls = 0
    sleep_delays: list[float] = []

    async def fake_shield(task):
        nonlocal shield_calls
        shield_calls += 1
        if shield_calls <= 2:
            raise updater.asyncio.CancelledError
        return await task

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if len(sleep_delays) == 1:
            raise updater.asyncio.CancelledError

    async def critical_step() -> str:
        return "done"

    monkeypatch.setattr(updater.asyncio, "shield", fake_shield)
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)

    result = await updater._await_ignoring_cancellation(critical_step())

    assert result == "done"
    assert shield_calls == 3
    assert sleep_delays == [
        updater._CANCELLATION_RETRY_DELAY_SECONDS,
        updater._CANCELLATION_RETRY_DELAY_SECONDS,
    ]


def test_get_current_version_prefers_higher_marker_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", tmp_path / ".current_version")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    _write_pyproject_version(tmp_path / "pyproject.toml", "v2026.4.1")
    (tmp_path / ".current_version").write_text("2026.4.2\n", encoding="utf-8")

    assert updater.get_current_version() == "2026.4.2"


def test_get_current_version_prefers_higher_pyproject_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", tmp_path / ".current_version")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    _write_pyproject_version(tmp_path / "pyproject.toml", "v2026.5.1")
    (tmp_path / ".current_version").write_text("2026.4.1\n", encoding="utf-8")

    assert updater.get_current_version() == "2026.5.1"


def test_get_current_version_ignores_invalid_marker_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", tmp_path / ".current_version")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)
    _write_pyproject_version(tmp_path / "pyproject.toml", "v2026.4.1")
    (tmp_path / ".current_version").write_bytes(b"v2026.3.9\x93\n")

    assert updater.get_current_version() == "2026.4.1"


def test_get_current_version_returns_empty_when_no_marker_or_pyproject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", tmp_path / ".current_version")
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path)

    assert updater.get_current_version() == ""


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


def test_is_windows_file_in_use_error_detects_winerror32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")

    assert updater._is_windows_file_in_use_error(PermissionError("[WinError 32] file in use")) is True
    assert updater._is_windows_file_in_use_error(PermissionError("[WinError 5] access denied")) is False


def test_is_uv_managed_python_runtime_error_detects_virtualenv_creation_failure() -> None:
    text = (
        "Failed to create temporary virtualenv\n"
        "Could not find a suitable Python executable for the virtual environment "
        "based on the interpreter: "
        r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe"
    )

    assert updater._is_uv_managed_python_runtime_error(text) is True
    assert updater._uv_managed_python_install_dir_from_text(text) == Path(
        r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none"
    )


def test_repair_windows_uv_managed_python_install_removes_cached_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[Path] = []

    monkeypatch.setattr(updater, "_resolve_windows_long_path", lambda path: path)
    monkeypatch.setattr(Path, "exists", lambda self: str(self).lower().endswith("cpython-3.12-windows-x86_64-none"))
    monkeypatch.setattr(updater, "_safe_rmtree", lambda path: removed.append(path))

    text = (
        "Failed to create temporary virtualenv\n"
        "Could not find a suitable Python executable for the virtual environment "
        "based on the interpreter: "
        r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe"
    )

    repaired = updater._repair_windows_uv_managed_python_install(text)

    assert repaired == Path(r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none")
    assert removed == [Path(r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none")]


def test_resolve_npm_executable_prefers_bundled_node_home_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")
    bundled_npm = node_home / "npm.cmd"
    bundled_npm.write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: r"C:\Program Files\nodejs\npm.cmd")

    assert updater._resolve_npm_executable() == str(bundled_npm)


def test_resolve_npm_executable_falls_back_to_find_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.delenv("FLOCKS_NODE_HOME", raising=False)
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)

    def fake_find(name: str) -> str | None:
        if name == "npm.cmd":
            return r"C:\Program Files\nodejs\npm.cmd"
        return None

    monkeypatch.setattr(updater, "_find_executable", fake_find)

    assert updater._resolve_npm_executable() == r"C:\Program Files\nodejs\npm.cmd"


def test_resolve_frontend_npm_candidates_adds_system_fallback_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")
    bundled_npm = node_home / "npm.cmd"
    bundled_npm.write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows\System32")

    def fake_find(name: str) -> str | None:
        if name == "npm.cmd":
            return r"C:\Program Files\nodejs\npm.cmd"
        return None

    monkeypatch.setattr(updater, "_find_executable", fake_find)

    candidates = updater._resolve_frontend_npm_candidates(
        npm_registry="https://registry.npmmirror.com/"
    )

    assert [candidate.npm for candidate in candidates] == [
        str(bundled_npm),
        r"C:\Program Files\nodejs\npm.cmd",
    ]
    assert candidates[0].env is not None
    assert candidates[0].env["PATH"].split(os.pathsep)[0] == str(node_home)
    assert candidates[1].env == {
        "npm_config_registry": "https://registry.npmmirror.com/"
    }


def test_resolve_frontend_npm_candidates_keeps_single_candidate_off_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_home = tmp_path / "tools" / "node"
    node_bin = node_home / "bin"
    node_bin.mkdir(parents=True)
    (node_bin / "node").write_text("", encoding="utf-8")
    bundled_npm = node_bin / "npm"
    bundled_npm.write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setattr(updater, "_find_executable", lambda name: f"/usr/bin/{name}")

    candidates = updater._resolve_frontend_npm_candidates(
        npm_registry="https://registry.npmmirror.com/"
    )

    assert [candidate.npm for candidate in candidates] == [str(bundled_npm)]
    assert candidates[0].source == "bundled"


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


def test_find_executable_probes_user_local_bin_for_uv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When shutil.which fails (e.g. systemd with minimal PATH) and the
    interpreter is inside a uv-tool venv, _find_executable should still
    locate uv in ~/.local/bin/."""
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    uv_bin = local_bin / "uv"
    uv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    uv_bin.chmod(0o755)

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert updater._find_executable("uv") == str(uv_bin)


def test_find_executable_probes_cargo_bin_for_uv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """uv installed via cargo should be found at ~/.cargo/bin/uv."""
    cargo_bin = tmp_path / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    uv_bin = cargo_bin / "uv"
    uv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    uv_bin.chmod(0o755)

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert updater._find_executable("uv") == str(uv_bin)


def test_find_executable_does_not_probe_extra_paths_for_non_uv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The extra path probing should only apply to the 'uv' name."""
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    npm_bin = local_bin / "npm"
    npm_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    npm_bin.chmod(0o755)

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert updater._find_executable("npm") is None


def test_build_uv_sync_env_augments_path_with_missing_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = updater._build_uv_sync_env()

    assert result is not None
    parts = result["PATH"].split(os.pathsep)
    assert "/usr/bin" in parts
    assert str(tmp_path / ".local" / "bin") in parts
    assert str(tmp_path / ".cargo" / "bin") in parts
    assert "/usr/local/bin" in parts


def test_build_uv_sync_env_returns_none_when_all_dirs_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = str(tmp_path)
    full_path = os.pathsep.join([
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".cargo", "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ])
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PATH", full_path)

    assert updater._build_uv_sync_env() is None


def test_build_uv_sync_env_returns_none_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert updater._build_uv_sync_env() is None


def test_build_dependency_sync_command_installs_project_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")

    assert updater._build_dependency_sync_command("uv", uv_default_index="https://mirror.example/simple") == [
        "uv",
        "sync",
        "--frozen",
        "--no-python-downloads",
        "--default-index",
        "https://mirror.example/simple",
    ]


def test_build_dependency_sync_command_keeps_project_install_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "linux")

    assert updater._build_dependency_sync_command("uv") == ["uv", "sync", "--frozen", "--no-python-downloads"]


def test_wheel_build_config_does_not_force_include_flockshub() -> None:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    wheel_config = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert ".flocks/flockshub" not in wheel_config.get("force-include", {})


def test_build_frontend_subprocess_env_prepends_bundled_node_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = updater._build_frontend_subprocess_env(
        npm_registry="https://registry.npmmirror.com/"
    )

    assert result is not None
    assert result["npm_config_registry"] == "https://registry.npmmirror.com/"
    assert result["PATH"].split(os.pathsep)[0] == str(node_home)


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


def test_resolve_update_mirror_profile_uses_cn_defaults_for_zh_locale() -> None:
    profile = updater._resolve_update_mirror_profile(
        ["github", "gitee", "gitlab"],
        locale="zh-CN",
    )

    assert profile.region == "cn"
    assert profile.sources == ["gitee", "github", "gitlab"]
    assert profile.npm_registry == "https://registry.npmmirror.com/"
    assert profile.uv_default_index == "https://mirrors.aliyun.com/pypi/simple"
    assert profile.pip_index_url == "https://mirrors.aliyun.com/pypi/simple"


def test_resolve_update_mirror_profile_prefers_explicit_region_over_locale() -> None:
    profile = updater._resolve_update_mirror_profile(
        ["github", "gitee"],
        region="default",
        locale="zh-CN",
    )

    assert profile.region is None
    assert profile.sources == ["github", "gitee"]
    assert profile.npm_registry is None


def test_gitee_archive_url_uses_web_archive_zip_endpoint() -> None:
    assert updater._gitee_archive_url("flocks/flocks", "2026.4.1", "tar.gz") == (
        "https://gitee.com/flocks/flocks/archive/refs/tags/v2026.4.1.zip"
    )


@pytest.mark.asyncio
async def test_fetch_gitee_release_returns_web_archive_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {
                "tag_name": "v2026.4.1",
                "body": "notes",
                "html_url": "https://gitee.com/flocks/flocks/releases/v2026.4.1",
            }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, follow_redirects=True):
            assert url == "https://gitee.com/api/v5/repos/flocks/flocks/releases/latest"
            assert params == {"access_token": "token"}
            assert follow_redirects is True
            return _FakeResponse()

    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda timeout=15: _FakeClient())

    tag, notes, html_url, zip_url, tar_url = await updater._fetch_gitee_release("flocks/flocks", "token")

    assert tag == "2026.4.1"
    assert notes == "notes"
    assert html_url == "https://gitee.com/flocks/flocks/releases/v2026.4.1"
    assert zip_url == "https://gitee.com/flocks/flocks/archive/refs/tags/v2026.4.1.zip"
    assert tar_url == zip_url


@pytest.mark.asyncio
async def test_download_archive_uses_curl_user_agent_for_gitee_web_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class _FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size=65536):
            assert chunk_size == 65536
            yield b"zip-bytes"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            return _FakeStreamResponse()

    monkeypatch.setattr(
        updater.httpx,
        "AsyncClient",
        lambda timeout, follow_redirects=True: _FakeClient(),
    )

    archive_path = await updater._download_archive(
        "https://gitee.com/flocks/flocks/archive/refs/tags/v2026.4.1.zip",
        token="secret",
        dest_dir=tmp_path,
        filename="flocks-2026.4.1.tar.gz",
    )

    assert archive_path.name == "flocks-2026.4.1.zip"
    assert archive_path.read_bytes() == b"zip-bytes"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://gitee.com/flocks/flocks/archive/refs/tags/v2026.4.1.zip"
    assert captured["headers"] == {"User-Agent": updater._CURL_USER_AGENT}


@pytest.mark.asyncio
async def test_download_archive_keeps_auth_header_for_non_gitee_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class _FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size=65536):
            yield b"archive"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            return _FakeStreamResponse()

    monkeypatch.setattr(
        updater.httpx,
        "AsyncClient",
        lambda timeout, follow_redirects=True: _FakeClient(),
    )

    archive_path = await updater._download_archive(
        "https://github.com/AgentFlocks/Flocks/archive/refs/tags/v2026.4.1.tar.gz",
        token="secret",
        dest_dir=tmp_path,
        filename="flocks-2026.4.1.tar.gz",
    )

    assert archive_path.name == "flocks-2026.4.1.tar.gz"
    assert archive_path.read_bytes() == b"archive"
    assert captured["method"] == "GET"
    assert captured["headers"] == {"Authorization": "Bearer secret"}


def test_build_restart_argv_uses_windows_venv_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(
        updater.sys,
        "argv",
        [r"C:\Users\worker\.local\bin\flocks", "start", "--reload", "--port", "8000"],
    )

    assert updater._build_restart_argv(tmp_path) == [
        str(tmp_path / ".venv" / "Scripts" / "python.exe"),
        "-m",
        "flocks.cli.main",
        "start",
        "--port",
        "8000",
    ]


def test_build_restart_argv_uses_venv_python_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(updater.sys, "argv", ["/usr/local/bin/flocks", "start", "--reload"])

    assert updater._build_restart_argv(tmp_path) == [
        str(venv_python),
        "-m",
        "flocks.cli.main",
        "start",
    ]


def test_build_restart_handoff_argv_rewrites_serve_to_managed_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = service_manager.ServiceConfig(
        backend_host="10.0.0.8",
        backend_port=5273,
        frontend_host="10.0.0.8",
        frontend_port=5273,
        legacy_backend_host="0.0.0.0",
        legacy_backend_port=9000,
    )
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: config)
    monkeypatch.setattr(updater.os, "getpid", lambda: 1234)

    argv = updater._build_restart_handoff_argv(
        ["python", "-m", "flocks.cli.main", "serve", "--host", "0.0.0.0", "--port", "9000"],
        tmp_path,
        uv_path="uv",
        sync_timeout=300,
        version="2026.4.1",
        current_version="2026.3.31",
    )

    assert argv[argv.index("--") + 1 :] == [
        "python",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "10.0.0.8",
        "--port",
        "5273",
        "--server-host",
        "0.0.0.0",
        "--server-port",
        "9000",
    ]


def test_refresh_global_cli_entry_creates_symlink_on_unix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)

    install_root = tmp_path / "project"
    venv_flocks = install_root / ".venv" / "bin" / "flocks"
    venv_flocks.parent.mkdir(parents=True)
    venv_flocks.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    updater._refresh_global_cli_entry(install_root)

    link = tmp_path / "home" / ".local" / "bin" / "flocks"
    assert link.is_symlink()
    assert link.resolve() == venv_flocks.resolve()


def test_refresh_global_cli_entry_creates_cmd_wrapper_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)

    install_root = tmp_path / "project"
    venv_python = install_root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    updater._refresh_global_cli_entry(install_root)

    wrapper = tmp_path / "home" / ".local" / "bin" / "flocks.cmd"
    assert wrapper.exists()
    content = wrapper.read_text(encoding="ascii")
    assert str(venv_python) in content
    assert "-m flocks.cli.main %*" in content


def test_refresh_global_cli_entry_noop_when_venv_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")

    updater._refresh_global_cli_entry(tmp_path / "nonexistent")

    link_dir = tmp_path / "home" / ".local" / "bin"
    assert not (link_dir / "flocks").exists()


def test_refresh_global_cli_entry_defers_legacy_uv_tool_uninstall_for_running_tool_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.sys, "executable", "/Users/test/.local/share/uv/tools/flocks/bin/python")
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: "/usr/local/bin/uv")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="flocks 0.0.0\n", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    install_root = tmp_path / "project"
    venv_flocks = install_root / ".venv" / "bin" / "flocks"
    venv_flocks.parent.mkdir(parents=True)
    venv_flocks.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    updater._refresh_global_cli_entry(install_root)

    link = tmp_path / "home" / ".local" / "bin" / "flocks"
    assert link.is_symlink()
    assert link.resolve() == venv_flocks.resolve()
    assert calls == []


def test_refresh_global_cli_entry_uninstalls_legacy_uv_tool_after_switching_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "project" / ".venv" / "bin" / "python"))
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: "/usr/local/bin/uv")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["/usr/local/bin/uv", "tool", "list"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="flocks 0.0.0\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    install_root = tmp_path / "project"
    venv_flocks = install_root / ".venv" / "bin" / "flocks"
    venv_flocks.parent.mkdir(parents=True)
    venv_flocks.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    updater._refresh_global_cli_entry(install_root)

    assert calls == [
        ["/usr/local/bin/uv", "tool", "list"],
        ["/usr/local/bin/uv", "tool", "uninstall", "flocks"],
    ]


@pytest.mark.asyncio
async def test_validate_restart_runtime_requires_venv_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert await updater._validate_restart_runtime(tmp_path) == (
        f"Restart runtime is missing: {tmp_path / '.venv' / 'Scripts' / 'python.exe'}"
    )


@pytest.mark.asyncio
async def test_validate_restart_runtime_accepts_existing_venv_python_without_importing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    async def fail_run_async(*_args, **_kwargs):
        raise AssertionError("runtime validation should not import project modules")

    monkeypatch.setattr(updater, "_run_async", fail_run_async)

    monkeypatch.setattr(updater.sys, "platform", "win32")

    assert await updater._validate_restart_runtime(tmp_path) is None


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


def test_cleanup_replaced_files_removes_renamed_lock_leftovers(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    leftover = install_root / "webui.flocks_old_123"
    leftover.mkdir(parents=True)
    (leftover / "old.txt").write_text("old", encoding="utf-8")

    updater.cleanup_replaced_files(install_root)

    assert not leftover.exists()


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


@pytest.mark.skipif(sys.platform == "win32", reason="uses the Unix domain socket control API")
def test_prepare_upgrade_handover_writes_state_and_stops_frontend_with_real_control_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_root = make_short_runtime_root("flocks-updater-")
    monkeypatch.setenv("FLOCKS_ROOT", str(short_root))
    paths = service_manager.runtime_paths()
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=9995,
        frontend_host="127.0.0.1",
        frontend_port=9996,
    )
    daemon, thread = start_supervisor(config)
    wait_for_supervisor(paths, running=True)

    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, _version: {
            "upgrade_server_pid": 321,
            "page_dir": str(short_root / "page"),
            "page_log": str(short_root / "logs" / "upgrade.log"),
        },
    )

    try:
        payload = updater._prepare_upgrade_handover("2026.3.31.1")

        status = service_control.read_supervisor_status(paths)
        assert status.backend.paused is True
        assert status.webui.paused is True
        assert payload["upgrade_server_pid"] == 321
        assert payload["backend_port"] == 9995
        assert payload["frontend_port"] == 9996
        assert updater._read_upgrade_state()["version"] == "2026.3.31.1"
    finally:
        stop_supervisor(daemon, thread)
        shutil.rmtree(short_root, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="uses the Unix domain socket control API")
def test_prepare_upgrade_handover_restores_frontend_when_upgrade_page_fails_with_real_control_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_root = make_short_runtime_root("flocks-updater-")
    monkeypatch.setenv("FLOCKS_ROOT", str(short_root))
    paths = service_manager.runtime_paths()
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=9995,
        frontend_host="127.0.0.1",
        frontend_port=9996,
    )
    daemon, thread = start_supervisor(config)
    wait_for_supervisor(paths, running=True)
    calls: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **_kw: calls.append("stop_page"))
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, _version: (_ for _ in ()).throw(RuntimeError("page failed")),
    )

    try:
        with pytest.raises(RuntimeError, match="page failed"):
            updater._prepare_upgrade_handover("2026.3.31.1")

        status = service_control.read_supervisor_status(paths)
        assert calls == ["stop_page"]
        assert status.backend.paused is False
        assert status.backend.pid is not None
        assert status.webui.paused is False
        assert status.webui.pid is None
        assert status.webui.state == "static"
        assert updater._read_upgrade_state() is None
    finally:
        stop_supervisor(daemon, thread)
        shutil.rmtree(short_root, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="uses the Unix domain socket control API")
def test_rollback_failed_update_resumes_backend_when_handoff_tasks_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_root = make_short_runtime_root("flocks-updater-")
    monkeypatch.setenv("FLOCKS_ROOT", str(short_root))
    paths = service_manager.runtime_paths()
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=9995,
        frontend_host="127.0.0.1",
        frontend_port=9996,
    )
    daemon, thread = start_supervisor(config)
    wait_for_supervisor(paths, running=True)
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **_kw: None)

    try:
        updater._write_upgrade_state(
            {
                "version": "2026.4.1",
                "backend_host": "127.0.0.1",
                "backend_port": 9995,
                "frontend_host": "127.0.0.1",
                "frontend_port": 9996,
                "skip_frontend_build": True,
            }
        )
        old_backend = daemon.backend.process
        assert old_backend is not None
        service_control.request_prepare_upgrade(paths=paths)
        wait_for_process_exit(old_backend)

        updater._rollback_failed_update(None, short_root / "install", "2026.3.31")

        status = service_control.read_supervisor_status(paths)
        assert status.backend.paused is False
        assert status.webui.paused is False
        assert status.backend.pid is not None
        assert status.backend.pid != old_backend.pid
        assert status.webui.pid is None
        assert status.webui.state == "static"
        assert updater._read_upgrade_state() is None
    finally:
        stop_supervisor(daemon, thread)
        shutil.rmtree(short_root, ignore_errors=True)


def test_recover_upgrade_state_restarts_frontend_and_clears_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    started: list[tuple[int, bool | None]] = []
    stopped: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: stopped.append("stop"))
    monkeypatch.setattr(
        service_control,
        "request_resume_upgrade",
        lambda config, **_kwargs: started.append((config.frontend_port, config.skip_frontend_build))
        or _webui_control_status(),
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


def test_recover_upgrade_state_retries_frontend_with_build_when_dist_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[tuple[str, bool | None, bool | None]] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: None)

    results = iter([
        _webui_control_payload("degraded", "missing dist"),
        _webui_control_payload(),
    ])

    def fake_resume_upgrade(config, **_kwargs):
        starts.append(("resume", config.skip_frontend_build, None))
        return service_control.parse_supervisor_status(next(results))

    def fake_restart_webui(config, *, force_frontend_build=False, **_kwargs):
        starts.append(("restart_webui", config.skip_frontend_build, force_frontend_build or None))
        return service_control.parse_supervisor_status(next(results))

    monkeypatch.setattr(service_control, "request_resume_upgrade", fake_resume_upgrade)
    monkeypatch.setattr(service_control, "request_restart_webui", fake_restart_webui)
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

    assert starts == [("resume", True, None), ("restart_webui", False, True)]
    assert updater._read_upgrade_state() is None


def test_recover_upgrade_state_restart_failure_clears_state_without_restarting_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[tuple[str, bool | None, bool | None]] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: None)

    def fake_resume_upgrade(config, **_kwargs):
        starts.append(("resume", config.skip_frontend_build, None))
        return _webui_control_status("degraded", "still broken")

    def fake_restart_webui(config, *, force_frontend_build=False, **_kwargs):
        starts.append(("restart_webui", config.skip_frontend_build, force_frontend_build or None))
        return _webui_control_status("degraded", "still broken")

    monkeypatch.setattr(service_control, "request_resume_upgrade", fake_resume_upgrade)
    monkeypatch.setattr(service_control, "request_restart_webui", fake_restart_webui)
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

    with pytest.raises(RuntimeError, match="still broken"):
        updater.recover_upgrade_state()

    assert starts == [("resume", True, None), ("restart_webui", False, True)]
    assert updater._read_upgrade_state() is None


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
        service_manager,
        "resolve_python_subprocess_command",
        lambda _root=None: ["/env/bin/python"],
    )
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
        "/env/bin/python",
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
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: events.append("stop_page"))
    monkeypatch.setattr(updater.shutil, "rmtree", lambda path, ignore_errors=True: events.append(f"rmtree:{Path(path).name}"))

    results = iter([
        _webui_control_payload("degraded", "missing dist"),
        _webui_control_payload(),
    ])

    def fake_resume_upgrade(config, **_kwargs) -> service_control.SupervisorStatus:
        events.append(f"resume:{config.skip_frontend_build}")
        return service_control.parse_supervisor_status(next(results))

    def fake_restart_webui(config, *, force_frontend_build=False, **_kwargs) -> service_control.SupervisorStatus:
        events.append(f"restart_webui:{config.skip_frontend_build}:{force_frontend_build or None}")
        return service_control.parse_supervisor_status(next(results))

    monkeypatch.setattr(service_control, "request_resume_upgrade", fake_resume_upgrade)
    monkeypatch.setattr(service_control, "request_restart_webui", fake_restart_webui)
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
        "resume:True",
        "restart_webui:False:True",
        "rmtree:upgrade-page",
    ]
    assert updater._read_upgrade_state() is None


def test_rollback_failed_update_clears_state_when_restore_and_frontend_both_fail(
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
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: events.append("stop_page"))
    monkeypatch.setattr(updater.shutil, "rmtree", lambda path, ignore_errors=True: events.append(f"rmtree:{Path(path).name}"))

    def fake_resume_upgrade(config, **_kwargs) -> service_control.SupervisorStatus:
        events.append(f"resume:{config.skip_frontend_build}")
        return _webui_control_status("degraded", "frontend still broken")

    def fake_restart_webui(config, **_kwargs) -> service_control.SupervisorStatus:
        events.append(f"restart_webui:{config.skip_frontend_build}")
        return _webui_control_status("degraded", "frontend still broken")

    monkeypatch.setattr(service_control, "request_resume_upgrade", fake_resume_upgrade)
    monkeypatch.setattr(service_control, "request_restart_webui", fake_restart_webui)
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

    assert events == [
        "stop_page",
        "resume:True",
        "rmtree:upgrade-page",
    ]
    assert updater._read_upgrade_state() is None


def test_backup_current_version_excludes_all_dist_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    webui_dist = install_root / "webui" / "dist"
    git_dir = install_root / ".git"
    other_dist = install_root / "dist"
    webui_dist.mkdir(parents=True)
    git_dir.mkdir(parents=True)
    other_dist.mkdir(parents=True)
    (webui_dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (git_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (install_root / "flocks.json").write_text('{"keep": true}', encoding="utf-8")
    (other_dist / "ignored.txt").write_text("nope", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(updater, "_BACKUP_DIR", backup_dir)
    backup_path = updater._backup_current_version(install_root, "2026.4.1", retain_count=1)

    assert backup_path is not None
    with tarfile.open(backup_path, "r:gz") as tar:
        names = tar.getnames()

    assert "flocks/webui/dist/index.html" not in names
    assert "flocks/flocks.json" in names
    assert "flocks/.git/HEAD" not in names
    assert "flocks/dist/ignored.txt" not in names


@pytest.mark.asyncio
async def test_build_updated_frontend_uses_current_install_root_and_region_mirror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    webui_dir = install_root / "webui"
    webui_dir.mkdir(parents=True)
    (webui_dir / "package.json").write_text("{}", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_get_updater_config():
        return SimpleNamespace(
            sources=["github", "gitee"],
        )

    async def fake_build_frontend_workspace(
        webui_dir: Path,
        *,
        npm_registry: str | None = None,
    ) -> str | None:
        captured["webui_dir"] = webui_dir
        captured["npm_registry"] = npm_registry
        return None

    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_build_frontend_workspace", fake_build_frontend_workspace)

    await updater.build_updated_frontend(region="cn")

    assert captured == {
        "webui_dir": webui_dir,
        "npm_registry": "https://registry.npmmirror.com/",
    }


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


def test_replace_install_dir_copies_dot_flocks_plugins_from_source(
    tmp_path: Path,
) -> None:
    """New release plugins under .flocks/plugins must be applied; removed plugins dropped."""
    source_dir = tmp_path / "source"
    install_root = tmp_path / "install"

    src_plugins = source_dir / ".flocks" / "plugins" / "tools" / "api"
    src_plugins.mkdir(parents=True)
    (src_plugins / "fofa" / "_provider.yaml").parent.mkdir(parents=True)
    (src_plugins / "fofa" / "_provider.yaml").write_text("version: new", encoding="utf-8")
    (src_plugins / "new_release_plugin" / "tool.yaml").parent.mkdir(parents=True)
    (src_plugins / "new_release_plugin" / "tool.yaml").write_text("name: new", encoding="utf-8")

    inst_plugins = install_root / ".flocks" / "plugins" / "tools" / "api"
    inst_plugins.mkdir(parents=True)
    (inst_plugins / "fofa" / "_provider.yaml").parent.mkdir(parents=True)
    (inst_plugins / "fofa" / "_provider.yaml").write_text("version: old", encoding="utf-8")
    (inst_plugins / "obsolete_plugin" / "gone.yaml").parent.mkdir(parents=True)
    (inst_plugins / "obsolete_plugin" / "gone.yaml").write_text("removed", encoding="utf-8")

    (source_dir / "flocks.json").write_text('{"version": "new"}', encoding="utf-8")
    (install_root / "flocks.json").write_text('{"keep": true}', encoding="utf-8")
    (install_root / ".git").mkdir()
    (install_root / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")

    updater._replace_install_dir(source_dir, install_root)

    assert (inst_plugins / "fofa" / "_provider.yaml").read_text(encoding="utf-8") == "version: new"
    assert (inst_plugins / "new_release_plugin" / "tool.yaml").read_text(encoding="utf-8") == "name: new"
    assert not (inst_plugins / "obsolete_plugin").exists()
    assert (install_root / "flocks.json").read_text(encoding="utf-8") == '{"version": "new"}'
    assert (install_root / ".git" / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main"


@pytest.mark.asyncio
async def test_perform_update_schedules_handoff_after_handover(
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
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)

    events: list[str] = []
    popen_calls: list[list[str]] = []

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

    async def fake_sleep(_seconds) -> None:
        events.append("sleep")

    monkeypatch.setattr(
        updater,
        "_get_updater_config",
        fake_get_updater_config,
    )
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else "/usr/bin/uv",
    )
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover") or {})
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: events.append("replace")
        or shutil.copytree(staged_webui, install_root / "webui", dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: ["/usr/bin/python3", "-m", "flocks.cli.main", "start"])
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))
    monkeypatch.setattr(updater, "rollback_upgrade_handover", lambda *_args: events.append("rollback_handover"))
    monkeypatch.setattr(
        updater,
        "_spawn_restart_handoff",
        lambda argv, **_kwargs: popen_calls.append(list(argv)) or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(updater.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit, match="0"):
        async for _step in updater.perform_update("2026.4.1"):
            pass

    assert events[:2] == ["replace", "sleep"]
    assert "handover" in events
    assert len(popen_calls) == 1
    handoff_argv = popen_calls[0]
    assert handoff_argv[:3] == ["/usr/bin/python3", "-m", "flocks.updater.restart_handoff"]
    assert "--uv-path" in handoff_argv
    assert "--version" in handoff_argv
    assert handoff_argv[handoff_argv.index("--") + 1 :] == [
        "/usr/bin/python3",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]


@pytest.mark.asyncio
async def test_perform_update_errors_when_handover_fails_before_frontend_build(
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
    install_root = tmp_path / "install-root"
    install_root.mkdir()

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
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
        return None

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
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
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: events.append("replace")
        or shutil.copytree(staged_webui, install_root / "webui", dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: events.append(f"marker:{version}"))
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: ["/usr/bin/python3", "-m", "flocks.cli.main", "start"])
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        updater,
        "_prepare_upgrade_handover",
        lambda _version: (_ for _ in ()).throw(RuntimeError("handover boom")),
    )
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "Failed to prepare WebUI handover: handover boom"
    assert events == ["replace", "restore"]


@pytest.mark.asyncio
async def test_perform_update_uses_cn_mirror_profile_for_sources_and_dependency_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    install_root = tmp_path / "install-root"
    _prepare_real_restart_runtime(install_root)
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    (staged_webui / "package-lock.json").write_text("{}", encoding="utf-8")
    (staged_webui / "dist").mkdir()
    (staged_webui / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    captured: dict[str, object] = {}
    run_calls: list[tuple[list[str], dict[str, str] | None]] = []

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github", "gitee"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_download_with_fallback(**kwargs):
        captured["sources"] = kwargs["sources"]
        return archive_path

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append((list(cmd), env))
        return 0, "", ""

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
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
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)

    progresses = [
        step
        async for step in updater.perform_update(
            "2026.4.1",
            restart=False,
            locale="zh-CN",
        )
    ]

    assert progresses[-1].stage == "done"
    assert captured["sources"] == ["gitee", "github"]
    assert run_calls == [
        (
            [
                "/usr/bin/uv",
                "sync",
                "--frozen",
                "--no-python-downloads",
                "--default-index",
                "https://mirrors.aliyun.com/pypi/simple",
            ],
            None,
        ),
    ]


@pytest.mark.asyncio
async def test_perform_update_retries_cn_uv_sync_with_default_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    install_root = tmp_path / "install-root"
    _prepare_real_restart_runtime(install_root)

    run_calls: list[list[str]] = []

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append(list(cmd))
        if cmd == [
            "/usr/bin/uv",
            "sync",
            "--frozen",
            "--no-python-downloads",
            "--default-index",
            "https://mirrors.aliyun.com/pypi/simple",
        ]:
            return 1, "", "403 Forbidden"
        return 0, "", ""

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    async def fake_sleep(_seconds) -> None:
        pass

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False, locale="zh-CN")]

    assert progresses[-1].stage == "done"
    assert run_calls == [
        [
            "/usr/bin/uv",
            "sync",
            "--frozen",
            "--no-python-downloads",
            "--default-index",
            "https://mirrors.aliyun.com/pypi/simple",
        ],
        ["/usr/bin/uv", "sync", "--frozen", "--no-python-downloads"],
    ]


@pytest.mark.asyncio
async def test_perform_update_prefers_bundled_npm_for_windows_frontend_rebuild(
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
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)

    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")
    bundled_npm = node_home / "npm.cmd"
    bundled_npm.write_text("", encoding="utf-8")

    run_calls: list[tuple[list[str], dict[str, str] | None]] = []

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append((list(cmd), env))
        return 0, "", ""

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda name: r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\uv\uv.exe" if name == "uv" else None)
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: shutil.copytree(staged_webui, install_root / "webui", dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False, locale="zh-CN")]

    assert progresses[-1].stage == "done"
    frontend_calls = [
        call for call in run_calls if call[0][0] == str(bundled_npm)
    ]
    assert [call[0] for call in frontend_calls] == [
        [str(bundled_npm), "install"],
        [str(bundled_npm), "run", "build"],
    ]
    install_env = frontend_calls[0][1]
    build_env = frontend_calls[1][1]
    assert install_env is not None
    assert build_env is not None
    assert install_env["npm_config_registry"] == "https://registry.npmmirror.com/"
    assert build_env["npm_config_registry"] == "https://registry.npmmirror.com/"
    assert install_env["PATH"].split(os.pathsep)[0] == str(node_home)
    assert build_env["PATH"].split(os.pathsep)[0] == str(node_home)


@pytest.mark.asyncio
async def test_perform_update_retries_windows_frontend_with_system_npm_after_bundled_build_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)
    install_webui = install_root / "webui"

    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")
    bundled_npm = node_home / "npm.cmd"
    bundled_npm.write_text("", encoding="utf-8")
    system_npm = r"C:\Program Files\nodejs\npm.cmd"

    run_calls: list[tuple[list[str], int | None, dict[str, str] | None]] = []

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append((list(cmd), timeout, env))
        if cmd == [str(bundled_npm), "install"]:
            bundled_modules = install_webui / "node_modules" / "@esbuild"
            bundled_modules.mkdir(parents=True, exist_ok=True)
            (bundled_modules / "bundled.txt").write_text("bundled", encoding="utf-8")
            return 0, "", ""
        if cmd == [str(bundled_npm), "run", "build"]:
            stale_dist = install_webui / "dist"
            stale_dist.mkdir(exist_ok=True)
            (stale_dist / "stale.txt").write_text("stale", encoding="utf-8")
            return 1, "", "bundled build failed"
        if cmd == [system_npm, "install"]:
            assert not (install_webui / "node_modules").exists()
            assert not (install_webui / "dist").exists()
            return 0, "", ""
        if cmd == [system_npm, "run", "build"]:
            dist_dir = install_webui / "dist"
            dist_dir.mkdir(exist_ok=True)
            (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            return 0, "", ""
        if "sync" in cmd:
            return 0, "", ""
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_find(name: str) -> str | None:
        if name == "npm.cmd":
            return system_npm
        if name == "uv":
            return r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\uv\uv.exe"
        return None

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", fake_find)
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: shutil.copytree(staged_webui, install_webui, dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False, locale="zh-CN")]

    assert progresses[-1].stage == "done"
    frontend_calls = [
        call for call in run_calls if call[0][0] in {str(bundled_npm), system_npm}
    ]
    assert [call[0] for call in frontend_calls] == [
        [str(bundled_npm), "install"],
        [str(bundled_npm), "run", "build"],
        [system_npm, "install"],
        [system_npm, "run", "build"],
    ]
    assert [call[1] for call in frontend_calls] == [300, 300, 300, 300]
    bundled_install_env = frontend_calls[0][2]
    bundled_build_env = frontend_calls[1][2]
    system_install_env = frontend_calls[2][2]
    system_build_env = frontend_calls[3][2]
    assert bundled_install_env is not None
    assert bundled_build_env is not None
    assert bundled_install_env["PATH"].split(os.pathsep)[0] == str(node_home)
    assert bundled_build_env["PATH"].split(os.pathsep)[0] == str(node_home)
    assert system_install_env == {"npm_config_registry": "https://registry.npmmirror.com/"}
    assert system_build_env == {"npm_config_registry": "https://registry.npmmirror.com/"}


@pytest.mark.asyncio
async def test_build_frontend_workspace_retries_npm_ci_before_switching_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    webui_dir = tmp_path / "webui"
    webui_dir.mkdir()
    (webui_dir / "package.json").write_text("{}", encoding="utf-8")
    (webui_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    bundled_npm = str(tmp_path / "bundled-npm")
    system_npm = str(tmp_path / "system-npm")
    run_calls: list[tuple[list[str], dict[str, str] | None]] = []
    journal_entries: list[str] = []

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append((list(cmd), env))
        if cmd == [bundled_npm, "install"]:
            partial_modules = webui_dir / "node_modules" / "@esbuild"
            partial_modules.mkdir(parents=True, exist_ok=True)
            return 1, "", "install failed"
        if cmd == [bundled_npm, "ci"]:
            assert (webui_dir / "node_modules").exists()
            return 0, "", ""
        if cmd == [bundled_npm, "run", "build"]:
            dist_dir = webui_dir / "dist"
            dist_dir.mkdir(exist_ok=True)
            (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            return 0, "", ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        updater,
        "_resolve_frontend_npm_candidates",
        lambda *, npm_registry=None: [
            updater._FrontendNpmCandidate(
                npm=bundled_npm,
                env={"npm_config_registry": npm_registry} if npm_registry else None,
                source="bundled",
            ),
            updater._FrontendNpmCandidate(
                npm=system_npm,
                env=None,
                source="system",
            ),
        ],
    )
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_record_update_journal", journal_entries.append)

    frontend_error = await updater._build_frontend_workspace(
        webui_dir,
        npm_registry="https://registry.npmmirror.com/",
    )

    assert frontend_error is None
    assert [call[0] for call in run_calls] == [
        [bundled_npm, "install"],
        [bundled_npm, "ci"],
        [bundled_npm, "run", "build"],
    ]
    assert all(
        call[1] == {"npm_config_registry": "https://registry.npmmirror.com/"}
        for call in run_calls
    )
    assert journal_entries == [
        (
            "WARN Frontend dependency install failed (npm install): install failed "
            "Retrying npm ci with the same npm/node "
            "after bundled npm install attempt."
        )
    ]


@pytest.mark.asyncio
async def test_build_frontend_workspace_tolerates_windows_node_assertion_after_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    webui_dir = tmp_path / "webui"
    webui_dir.mkdir()
    (webui_dir / "package.json").write_text("{}", encoding="utf-8")
    bundled_npm = str(tmp_path / "npm.cmd")
    run_calls: list[list[str]] = []

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append(list(cmd))
        if cmd == [bundled_npm, "install"]:
            return 0, "", ""
        if cmd == [bundled_npm, "run", "build"]:
            dist_dir = webui_dir / "dist"
            dist_dir.mkdir()
            (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            return (
                3221226505,
                "built in 6.83s",
                "Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\\win\\async.c, line 76",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(
        updater,
        "_resolve_frontend_npm_candidates",
        lambda *, npm_registry=None: [
            updater._FrontendNpmCandidate(npm=bundled_npm, env=None, source="bundled"),
        ],
    )
    monkeypatch.setattr(updater, "_run_async", fake_run_async)

    assert await updater._build_frontend_workspace(webui_dir) is None
    assert run_calls == [[bundled_npm, "install"], [bundled_npm, "run", "build"]]


@pytest.mark.asyncio
async def test_perform_update_retries_windows_frontend_with_full_timeout_after_bundled_install_and_ci_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    (staged_webui / "package-lock.json").write_text("{}", encoding="utf-8")
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)
    install_webui = install_root / "webui"

    node_home = tmp_path / "tools" / "node"
    node_home.mkdir(parents=True)
    (node_home / "node.exe").write_text("", encoding="utf-8")
    bundled_npm = node_home / "npm.cmd"
    bundled_npm.write_text("", encoding="utf-8")
    system_npm = r"C:\Program Files\nodejs\npm.cmd"

    run_calls: list[tuple[list[str], int | None, dict[str, str] | None]] = []

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        run_calls.append((list(cmd), timeout, env))
        if cmd == [str(bundled_npm), "install"]:
            bundled_modules = install_webui / "node_modules" / "@esbuild"
            bundled_modules.mkdir(parents=True, exist_ok=True)
            (bundled_modules / "bundled.txt").write_text("bundled", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        if cmd == [str(bundled_npm), "ci"]:
            assert (install_webui / "node_modules").exists()
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        if cmd == [system_npm, "install"]:
            assert not (install_webui / "node_modules").exists()
            assert not (install_webui / "dist").exists()
            return 0, "", ""
        if cmd == [system_npm, "run", "build"]:
            dist_dir = install_webui / "dist"
            dist_dir.mkdir(exist_ok=True)
            (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            return 0, "", ""
        if "sync" in cmd:
            return 0, "", ""
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_find(name: str) -> str | None:
        if name == "npm.cmd":
            return system_npm
        if name == "uv":
            return r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\uv\uv.exe"
        return None

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", fake_find)
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: shutil.copytree(staged_webui, install_webui, dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False, locale="zh-CN")]

    assert progresses[-1].stage == "done"
    frontend_calls = [
        call for call in run_calls if call[0][0] in {str(bundled_npm), system_npm}
    ]
    assert [call[0] for call in frontend_calls] == [
        [str(bundled_npm), "install"],
        [str(bundled_npm), "ci"],
        [system_npm, "install"],
        [system_npm, "run", "build"],
    ]
    assert [call[1] for call in frontend_calls] == [300, 300, 300, 300]


@pytest.mark.asyncio
async def test_perform_update_errors_when_uv_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When uv is not found, the updater should fail immediately with a clear
    message telling the user to install uv."""
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    install_root = tmp_path / "install-root"
    _prepare_real_restart_runtime(install_root)

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater.sys, "platform", "linux")

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)

    progresses = [
        step
        async for step in updater.perform_update("2026.4.1", restart=False)
    ]

    error_events = [p for p in progresses if p.stage == "error"]
    assert len(error_events) == 1
    assert "uv is required but was not found" in error_events[0].message
    assert "PATH" in error_events[0].message


@pytest.mark.asyncio
async def test_perform_update_retries_uv_sync_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """uv sync should retry once after a transient failure."""
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"

    call_count = 0

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        nonlocal call_count
        if "sync" in cmd:
            call_count += 1
            if call_count == 1:
                return 1, "", "network timeout"
            return 0, "", ""
        return 0, "", ""

    async def fake_download(**_kw):
        return archive_path

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    install_root = tmp_path / "install-root"
    _prepare_real_restart_runtime(install_root)

    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_kw: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_a, **_kw: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    async def fake_sleep(_s):
        pass

    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)

    progresses = [
        step async for step in updater.perform_update("2026.4.1", restart=False)
    ]

    assert progresses[-1].stage == "done"
    assert call_count == 2


@pytest.mark.asyncio
async def test_perform_update_syncs_windows_venv_in_place(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_root.mkdir()
    install_root = tmp_path / "install-root"
    old_python = install_root / ".venv" / "Scripts" / "python.exe"
    old_python.parent.mkdir(parents=True)
    old_python.write_text("old", encoding="utf-8")

    sync_calls: list[tuple[list[str], Path | None]] = []

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

    async def fake_download(**_kw):
        return archive_path

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        if "sync" in cmd:
            sync_calls.append((cmd, cwd))
            assert (install_root / ".venv" / "Scripts" / "python.exe").exists()
        return 0, "", ""

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_kw: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_a, **_kw: staged_root)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: r"C:\tools\uv.exe")
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False)]

    assert progresses[-1].stage == "done"
    assert sync_calls == [([r"C:\tools\uv.exe", "sync", "--frozen", "--no-python-downloads"], install_root)]
    assert (install_root / ".venv" / "Scripts" / "python.exe").read_text(encoding="utf-8") == "old"
    assert not (install_root / ".venv.flocks_backup").exists()


@pytest.mark.asyncio
async def test_perform_update_rolls_back_when_windows_uv_sync_times_out(
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
    install_root = tmp_path / "install-root"
    old_python = install_root / ".venv" / "Scripts" / "python.exe"
    old_python.parent.mkdir(parents=True)
    old_python.write_text("old", encoding="utf-8")

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

    async def fake_download(**_kw):
        return archive_path

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        if "sync" in cmd:
            new_python = install_root / ".venv" / "Scripts" / "python.exe"
            new_python.parent.mkdir(parents=True, exist_ok=True)
            new_python.write_text("new", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0)
        return 0, "", ""

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_kw: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_a, **_kw: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else r"C:\tools\uv.exe",
    )
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_a: events.append("restore"))

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False)]

    assert progresses[-1].stage == "error"
    expected_timeout = updater._dependency_sync_timeout_seconds()
    assert progresses[-1].message == (
        f"Dependency sync timed out after {expected_timeout}s while running uv sync."
    )
    assert events == ["restore"]
    assert (install_root / ".venv" / "Scripts" / "python.exe").read_text(encoding="utf-8") == "new"
    assert not (install_root / ".venv.flocks_failed").exists()
    assert not (install_root / ".venv.flocks_backup").exists()


@pytest.mark.asyncio
async def test_perform_update_fails_after_uv_sync_retry_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When uv sync fails twice, the updater should give up and rollback."""
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    rolled_back = False

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        if "sync" in cmd:
            return 1, "", "resolution failed"
        return 0, "", ""

    async def fake_download(**_kw):
        return archive_path

    def fake_restore(*_args):
        nonlocal rolled_back
        rolled_back = True

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_kw: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_a, **_kw: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    async def fake_sleep(_s):
        pass

    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(updater, "_restore_backup_if_possible", fake_restore)
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)

    progresses = [
        step async for step in updater.perform_update("2026.4.1", restart=False)
    ]

    error_events = [p for p in progresses if p.stage == "error"]
    assert len(error_events) == 1
    assert "resolution failed" in error_events[0].message
    assert rolled_back


@pytest.mark.asyncio
async def test_perform_update_repairs_broken_uv_managed_python_cache_before_retrying_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"

    call_count = 0
    sleep_calls: list[int | float] = []
    repaired_messages: list[str] = []

    async def fake_get_updater_config():
        return SimpleNamespace(
            archive_format="tar.gz",
            sources=["github"],
            repo="AgentFlocks/Flocks",
            token=None,
            gitee_token=None,
            backup_retain_count=3,
            base_url=None,
            gitee_repo=None,
        )

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        nonlocal call_count
        if "sync" in cmd:
            call_count += 1
            if call_count == 1:
                return (
                    1,
                    "",
                    "Failed to create temporary virtualenv\n"
                    "Could not find a suitable Python executable for the virtual environment "
                    "based on the interpreter: "
                    r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe",
                )
            return 0, "", ""
        return 0, "", ""

    async def fake_download(**_kw):
        return archive_path

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    install_root = tmp_path / "install-root"
    _prepare_real_restart_runtime(install_root)

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_a, **_kw: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_a, **_kw: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: r"C:\Users\worker\AppData\Local\Programs\Flocks\tools\uv\uv.exe")
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(updater, "_repair_windows_uv_managed_python_install", lambda text: repaired_messages.append(text) or Path(r"C:\Users\worker\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none"))
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)

    progresses = [
        step async for step in updater.perform_update("2026.4.1", restart=False)
    ]

    assert progresses[-1].stage == "done"
    assert call_count == 2
    assert sleep_calls == [2]
    assert len(repaired_messages) == 1


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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
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
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert "WinError 5" in progresses[-1].message
    assert events == ["restore"]
    assert "handover" not in events


@pytest.mark.asyncio
async def test_perform_update_retries_after_windows_file_lock_and_rolls_back_handover_failures(
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
    replace_attempts = {"count": 0}

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        if cmd[1] == "install":
            events.append("npm-install")
        elif cmd[:3] == ["/usr/bin/npm", "run", "build"]:
            events.append("npm-build")
        else:
            events.append("uv-sync")
        return 0, "", ""

    def fake_replace_install_dir(*_args, **_kwargs):
        replace_attempts["count"] += 1
        events.append(f"replace-{replace_attempts['count']}")
        if replace_attempts["count"] == 1:
            raise PermissionError("[WinError 32] The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(updater.sys, "platform", "win32")
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
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(updater, "_replace_install_dir", fake_replace_install_dir)
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: [r"C:\tool\python.exe", "-m", "flocks.cli.main", "start"])
    monkeypatch.setattr(
        updater,
        "_spawn_restart_handoff",
        lambda *_args, **_kwargs: events.append("popen") or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(updater.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit, match="0"):
        async for _step in updater.perform_update("2026.4.1"):
            pass

    assert events == [
        "replace-1",
        "handover",
        "replace-2",
        "popen",
    ]
    assert "restore" not in events


@pytest.mark.asyncio
async def test_perform_update_reports_frontend_dependency_install_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    (staged_webui / "package-lock.json").write_text("{}", encoding="utf-8")
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)
    install_webui = install_root / "webui"

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        events.append(" ".join(cmd))
        if "sync" in cmd:
            return 0, "", ""
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
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
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: events.append("replace")
        or shutil.copytree(staged_webui, install_webui, dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False)]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "Frontend dependency install timed out after 300s while running npm ci."
    assert events == [
        "replace",
        "/usr/bin/uv sync --frozen --no-python-downloads",
        "/usr/bin/npm install",
        "/usr/bin/npm ci",
        "restore",
    ]


@pytest.mark.asyncio
async def test_perform_update_rolls_back_handover_when_current_frontend_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_webui = staged_root / "webui"
    staged_webui.mkdir(parents=True)
    (staged_webui / "package.json").write_text("{}", encoding="utf-8")
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    _prepare_real_restart_runtime(install_root)
    install_webui = install_root / "webui"

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
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        if cmd[:3] == ["/usr/bin/npm", "run", "build"]:
            events.append("npm-build")
            return 1, "", "boom"
        if cmd[1] == "install":
            events.append("npm-install")
            return 0, "", ""
        events.append("uv-sync")
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(
        updater,
        "_find_executable",
        lambda name: "/usr/bin/npm" if name in {"npm", "npm.cmd"} else "/usr/bin/uv",
    )
    monkeypatch.setattr(
        updater,
        "_replace_install_dir",
        lambda *_args, **_kwargs: events.append("replace")
        or shutil.copytree(staged_webui, install_webui, dirs_exist_ok=True),
    )
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False)]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "Frontend build failed: boom"
    assert events == ["replace", "uv-sync", "npm-install", "npm-build", "restore"]


@pytest.mark.asyncio
async def test_perform_update_no_orphan_state_when_generator_abandoned_before_handover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SSE disconnect (GeneratorExit) at any yield point before handover
    must not leave upgrade state or orphan temp-page processes, because
    the handover now happens after all yields (right before restart spawn)."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
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
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)

    gen = updater.perform_update("2026.4.1")
    async for step in gen:
        if step.stage == "restarting":
            break
    await gen.aclose()

    assert "handover" not in events
    assert updater._read_upgrade_state() is None


@pytest.mark.asyncio
async def test_perform_update_spawns_restart_process_on_windows(
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

    popen_calls: list[tuple[list[str], Path]] = []
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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        return 0, "", ""

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    monkeypatch.setattr(updater.sys, "platform", "win32")
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
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: [r"C:\tool\python.exe", "-m", "flocks.cli.main", "start"])
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(
        updater,
        "_spawn_restart_handoff",
        lambda argv, cwd=None: popen_calls.append((list(argv), cwd)) or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(updater.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr(updater.os, "execv", lambda *_args: events.append("execv"))

    with pytest.raises(SystemExit, match="0"):
        async for _step in updater.perform_update("2026.4.1"):
            pass

    assert len(popen_calls) == 1
    handoff_argv, cwd = popen_calls[0]
    assert cwd == tmp_path / "install-root"
    assert handoff_argv[:3] == [r"C:\tool\python.exe", "-m", "flocks.updater.restart_handoff"]
    assert "--parent-pid" in handoff_argv
    assert "--backend-port" in handoff_argv
    assert handoff_argv[handoff_argv.index("--") + 1 :] == [
        r"C:\tool\python.exe",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]
    assert events == ["handover"]
    assert "execv" not in events


@pytest.mark.asyncio
async def test_perform_update_stops_when_windows_restart_runtime_validation_fails(
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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        return 0, "", ""

    async def fake_validate_restart_runtime(_install_root: Path) -> str | None:
        return "Restart runtime is missing: .venv/Scripts/python.exe"

    monkeypatch.setattr(updater.sys, "platform", "win32")
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
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: events.append("marker"))
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))
    monkeypatch.setattr(updater, "_validate_restart_runtime", fake_validate_restart_runtime)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *_args, **_kwargs: events.append("popen"))

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False)]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "Restart runtime is missing: .venv/Scripts/python.exe"
    assert events == ["restore"]


@pytest.mark.asyncio
async def test_perform_update_yields_error_when_build_restart_argv_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_build_restart_argv raising FileNotFoundError should yield a graceful
    error instead of letting the exception propagate to the route handler."""
    archive_path = tmp_path / "flocks.zip"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
    staged_root.mkdir()

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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        return 0, "", ""

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_find_executable", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: events.append("marker"))
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(
        updater,
        "_build_restart_argv",
        lambda install_root=None: (_ for _ in ()).throw(
            FileNotFoundError("python.exe not found"),
        ),
    )
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert "Failed to build restart command" in progresses[-1].message
    assert "handover" not in events


@pytest.mark.asyncio
async def test_perform_update_yields_error_when_windows_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """subprocess.Popen failure on Windows should yield a graceful error
    instead of re-raising the OSError."""
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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        return 0, "", ""

    monkeypatch.setattr(updater.sys, "platform", "win32")
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
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: [r"C:\tool\python.exe", "-m", "flocks.cli.main"])
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater, "_handoff_service_config", lambda: service_manager.ServiceConfig())
    monkeypatch.setattr(updater, "rollback_upgrade_handover", lambda: events.append("rollback_handover"))
    monkeypatch.setattr(
        updater,
        "_spawn_restart_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert "Failed to restart service" in progresses[-1].message
    assert "rollback_handover" in events
