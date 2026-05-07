import os
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
async def test_validate_windows_restart_runtime_requires_venv_python(tmp_path: Path) -> None:
    assert await updater._validate_windows_restart_runtime(tmp_path) == (
        f"Windows restart runtime is missing: {tmp_path / '.venv' / 'Scripts' / 'python.exe'}"
    )


@pytest.mark.asyncio
async def test_validate_windows_restart_runtime_reports_import_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        return 1, "", "No module named uvicorn"

    monkeypatch.setattr(updater, "_run_async", fake_run_async)

    result = await updater._validate_windows_restart_runtime(
        tmp_path, max_attempts=1,
    )
    assert result == "Windows restart runtime validation failed: No module named uvicorn"


@pytest.mark.asyncio
async def test_validate_windows_restart_runtime_handles_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        raise subprocess.TimeoutExpired(cmd, timeout or 60)

    monkeypatch.setattr(updater, "_run_async", fake_run_async)

    result = await updater._validate_windows_restart_runtime(
        tmp_path, max_attempts=1, timeout=10,
    )
    assert result is not None
    assert "timed out" in result.lower()


@pytest.mark.asyncio
async def test_validate_windows_restart_runtime_handles_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        raise PermissionError("access denied")

    monkeypatch.setattr(updater, "_run_async", fake_run_async)

    result = await updater._validate_windows_restart_runtime(
        tmp_path, max_attempts=1,
    )
    assert result is not None
    assert "access denied" in result


@pytest.mark.asyncio
async def test_validate_windows_restart_runtime_retries_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python_exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    call_count = 0

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise subprocess.TimeoutExpired(cmd, timeout or 60)
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", fake_run_async)

    result = await updater._validate_windows_restart_runtime(
        tmp_path, max_attempts=2, retry_delay=0.0,
    )
    assert result is None
    assert call_count == 2


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
    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: calls.append(("stop_page", True)))
    monkeypatch.setattr(
        updater,
        "_start_upgrade_page_server",
        lambda _config, _version: (_ for _ in ()).throw(RuntimeError("page failed")),
    )

    with pytest.raises(RuntimeError, match="page failed"):
        updater._prepare_upgrade_handover("2026.3.31.1")

    assert calls == [
        ("stop:WebUI:5173", True),
        ("stop_page", True),
        ("start_frontend", False),
    ]
    assert updater._read_upgrade_state() is None


def test_recover_upgrade_state_restarts_frontend_and_clears_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    started: list[tuple[int, bool]] = []
    stopped: list[str] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: stopped.append("stop"))
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


def test_recover_upgrade_state_retries_frontend_with_build_when_dist_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[bool] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: None)

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


def test_recover_upgrade_state_restart_failure_clears_state_without_restarting_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    starts: list[bool] = []

    monkeypatch.setattr(updater, "_stop_upgrade_page_server", lambda **kw: None)

    def fake_start_frontend(config, _console) -> None:
        starts.append(config.skip_frontend_build)
        raise service_manager.ServiceError("still broken")

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

    with pytest.raises(service_manager.ServiceError, match="still broken"):
        updater.recover_upgrade_state()

    assert starts == [True, False]
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

    assert events == [
        "stop_page",
        "start_frontend:True",
        "rmtree:upgrade-page",
    ]
    assert updater._read_upgrade_state() is None


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

    (install_root / "flocks.json").write_text('{"keep": true}', encoding="utf-8")

    updater._replace_install_dir(source_dir, install_root)

    assert (inst_plugins / "fofa" / "_provider.yaml").read_text(encoding="utf-8") == "version: new"
    assert (inst_plugins / "new_release_plugin" / "tool.yaml").read_text(encoding="utf-8") == "name: new"
    assert not (inst_plugins / "obsolete_plugin").exists()
    assert (install_root / "flocks.json").read_text(encoding="utf-8") == '{"keep": true}'


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
    monkeypatch.setattr(updater, "_refresh_global_cli_entry", lambda _root: None)
    monkeypatch.setattr(updater, "_build_restart_argv", lambda install_root=None: ["/usr/bin/python3", "-m", "flocks.cli.main", "start"])
    monkeypatch.setattr(updater.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))
    monkeypatch.setattr(updater, "rollback_upgrade_handover", lambda *_args: events.append("rollback_handover"))
    monkeypatch.setattr(updater.os, "execv", lambda *_args: (_ for _ in ()).throw(OSError("boom")))

    progresses = []
    async for step in updater.perform_update("2026.4.1"):
        progresses.append(step)

    assert progresses[-1].stage == "error"
    assert "Failed to restart service" in progresses[-1].message
    assert events[:4] == ["npm-install", "npm-build", "replace", "uv-sync"]
    assert "marker:2026.4.1" in events
    assert "handover" in events
    assert events.index("handover") > events.index("uv-sync")
    assert "rollback_handover" in events


@pytest.mark.asyncio
async def test_perform_update_uses_cn_mirror_profile_for_sources_and_dependency_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "flocks.tar.gz"
    archive_path.write_text("archive", encoding="utf-8")
    staged_root = tmp_path / "staged"
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
            ["/usr/bin/npm", "ci"],
            {"npm_config_registry": "https://registry.npmmirror.com/"},
        ),
        (
            ["/usr/bin/npm", "run", "build"],
            {"npm_config_registry": "https://registry.npmmirror.com/"},
        ),
        (
            ["/usr/bin/uv", "sync", "--default-index", "https://mirrors.aliyun.com/pypi/simple"],
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
        if cmd == ["/usr/bin/uv", "sync", "--default-index", "https://mirrors.aliyun.com/pypi/simple"]:
            return 1, "", "403 Forbidden"
        return 0, "", ""

    async def fake_download_with_fallback(**_kwargs):
        return archive_path

    async def fake_sleep(_seconds) -> None:
        pass

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
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
        ["/usr/bin/uv", "sync", "--default-index", "https://mirrors.aliyun.com/pypi/simple"],
        ["/usr/bin/uv", "sync"],
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

    async def fake_validate_windows_restart_runtime(*_args, **_kwargs):
        return None

    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.3.31")
    monkeypatch.setattr(updater, "_download_with_fallback", fake_download_with_fallback)
    monkeypatch.setattr(updater, "_backup_current_version", lambda *_args, **_kwargs: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(updater, "_extract_archive", lambda *_args, **_kwargs: staged_root)
    monkeypatch.setattr(updater, "_run_async", fake_run_async)
    monkeypatch.setattr(updater, "_resolve_npm_executable", lambda: str(bundled_npm))
    monkeypatch.setattr(updater, "_find_executable", lambda name: r"C:\Users\flocks\AppData\Local\Programs\Flocks\tools\uv\uv.exe" if name == "uv" else None)
    monkeypatch.setattr(updater, "_build_uv_sync_env", lambda: None)
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate_windows_restart_runtime)
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "_write_version_marker", lambda _v: None)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("FLOCKS_NODE_HOME", str(node_home))
    monkeypatch.delenv("FLOCKS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    progresses = [step async for step in updater.perform_update("2026.4.1", restart=False, locale="zh-CN")]

    assert progresses[-1].stage == "done"
    assert [call[0] for call in run_calls[:2]] == [
        [str(bundled_npm), "install"],
        [str(bundled_npm), "run", "build"],
    ]
    install_env = run_calls[0][1]
    build_env = run_calls[1][1]
    assert install_env is not None
    assert build_env is not None
    assert install_env["npm_config_registry"] == "https://registry.npmmirror.com/"
    assert build_env["npm_config_registry"] == "https://registry.npmmirror.com/"
    assert install_env["PATH"].split(os.pathsep)[0] == str(node_home)
    assert build_env["PATH"].split(os.pathsep)[0] == str(node_home)


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
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
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
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
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

    async def fake_validate_windows_restart_runtime(_install_root: Path) -> str | None:
        return None

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_get_updater_config", fake_get_updater_config)
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install-root")
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
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate_windows_restart_runtime)
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
    assert events == ["npm-install", "npm-build", "restore"]
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

    async def fake_validate_windows_restart_runtime(_install_root: Path) -> str | None:
        return "No module named uvicorn"

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
    monkeypatch.setattr(updater, "_replace_install_dir", fake_replace_install_dir)
    monkeypatch.setattr(updater, "_rollback_failed_update", lambda *_args: events.append("rollback"))
    monkeypatch.setattr(updater, "_restore_backup_if_possible", lambda *_args: events.append("restore"))
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate_windows_restart_runtime)

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "No module named uvicorn"
    assert events == [
        "npm-install",
        "npm-build",
        "replace-1",
        "handover",
        "replace-2",
        "uv-sync",
        "rollback",
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
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

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
    monkeypatch.setattr(updater, "_replace_install_dir", lambda *_args, **_kwargs: events.append("replace"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "Frontend dependency install timed out after 300s while running npm ci."
    assert events == ["/usr/bin/npm ci"]


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

    async def fake_run_async(cmd, cwd=None, timeout=None, env=None):
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


@pytest.mark.asyncio
async def test_perform_update_no_orphan_state_when_generator_abandoned_before_handover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SSE disconnect (GeneratorExit) at any yield point before handover
    must not leave upgrade state or orphan temp-page processes, because
    the handover now happens after all yields (right before os.execv)."""
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

    popen_calls: list[tuple[list[str], Path, bool]] = []
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

    async def fake_validate_windows_restart_runtime(_install_root: Path) -> str | None:
        return None

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
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate_windows_restart_runtime)
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater.subprocess, "Popen", lambda argv, cwd=None, close_fds=False: popen_calls.append((list(argv), cwd, close_fds)) or SimpleNamespace(pid=4321))
    monkeypatch.setattr(updater.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr(updater.os, "execv", lambda *_args: events.append("execv"))

    with pytest.raises(SystemExit, match="0"):
        async for _step in updater.perform_update("2026.4.1"):
            pass

    assert popen_calls == [
        ([r"C:\tool\python.exe", "-m", "flocks.cli.main", "start"], tmp_path / "install-root", True),
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

    async def fake_validate_windows_restart_runtime(_install_root: Path) -> str | None:
        return "No module named uvicorn"

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
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate_windows_restart_runtime)
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *_args, **_kwargs: events.append("popen"))

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert progresses[-1].message == "No module named uvicorn"
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

    async def fake_validate(_install_root: Path) -> str | None:
        return None

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
    monkeypatch.setattr(updater, "_validate_windows_restart_runtime", fake_validate)
    monkeypatch.setattr(updater, "_prepare_upgrade_handover", lambda _version: events.append("handover"))
    monkeypatch.setattr(updater, "rollback_upgrade_handover", lambda: events.append("rollback_handover"))
    monkeypatch.setattr(
        updater.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    progresses = [step async for step in updater.perform_update("2026.4.1")]

    assert progresses[-1].stage == "error"
    assert "Failed to restart service" in progresses[-1].message
    assert "rollback_handover" in events
