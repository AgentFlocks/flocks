from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_bash_installer_stops_processes_before_retrying_locked_operations() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert "stop_flocks_processes()" in script
    assert "get_runtime_pid_file_paths()" in script
    assert "get_pid_from_runtime_file()" in script
    assert 'flocks stop >/dev/null 2>&1 || true' in script
    assert "list_flocks_process_ids()" in script
    assert "is_lock_error_output()" in script
    assert 'run_with_lock_retry "Python 后端依赖安装" uv sync --group dev' in script
    assert 'run_with_lock_retry "flocks 全局 CLI 安装" uv tool install --editable "$ROOT_DIR" --force' in script
    assert "os\\ error\\ 5" in script
    assert 'pkill -f "uv sync"' not in script
    assert 'pkill -f "npm run preview"' not in script
    assert 'pkill -f "vite preview"' not in script
    assert "stop_flocks_processes\n    run_with_lock_retry" not in script
    assert "< <(" not in script


def test_powershell_installer_stops_processes_before_retrying_locked_operations() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Stop-FlocksProcesses" in script
    assert "& $flocksCommand.Source stop" in script
    assert 'Join-Path $runDir "upgrade_server.pid"' in script
    assert "Get-CimInstance Win32_Process" in script
    assert 'Invoke-InstallerCommandWithLockRetry -Description "Python 后端依赖安装"' in script
    assert 'Invoke-InstallerCommandWithLockRetry -Description "flocks 全局 CLI 安装"' in script
    assert "Failed to update Windows PE resources" in script
    assert "Stop-FlocksProcesses -Aggressive" not in script
    assert 'Get-Process -Name $name' not in script
    assert '"uv sync"' not in script
    assert '"vite preview"' not in script


def test_powershell_installers_use_utf8_bom_with_crlf() -> None:
    for path in (REPO_ROOT / "install.ps1", SCRIPT_DIR / "install.ps1"):
        data = path.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in data
        assert b"\n" not in data.replace(b"\r\n", b"")
