from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_bash_installer_stops_processes_before_retrying_locked_operations() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert "stop_flocks_processes()" in script
    assert 'flocks stop >/dev/null 2>&1 || true' in script
    assert 'pkill -f "flocks.server.app"' in script
    assert "is_lock_error_output()" in script
    assert 'run_with_lock_retry "Python 后端依赖安装" uv sync --group dev' in script
    assert 'run_with_lock_retry "flocks 全局 CLI 安装" uv tool install --editable "$ROOT_DIR" --force' in script
    assert "os\\ error\\ 5" in script


def test_powershell_installer_stops_processes_before_retrying_locked_operations() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Stop-FlocksProcesses" in script
    assert "& $flocksCommand.Source stop" in script
    assert 'Join-Path $runDir "upgrade_server.pid"' in script
    assert "Get-CimInstance Win32_Process" in script
    assert 'Invoke-InstallerCommandWithLockRetry -Description "Python 后端依赖安装"' in script
    assert 'Invoke-InstallerCommandWithLockRetry -Description "flocks 全局 CLI 安装"' in script
    assert "Failed to update Windows PE resources" in script
