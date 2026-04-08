import re
import shutil
import subprocess
from pathlib import Path

import pytest


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
    assert 'run_with_lock_retry "Python backend dependency installation" uv sync --group dev' in script
    assert 'run_with_lock_retry "Global flocks CLI installation" uv tool install --editable "$ROOT_DIR" --force' in script
    assert "os\\ error\\ 5" in script
    assert 'pkill -f "uv sync"' not in script
    assert 'pkill -f "npm run preview"' not in script
    assert 'pkill -f "vite preview"' not in script
    assert "stop_flocks_processes\n    run_with_lock_retry" not in script
    assert "< <(" not in script


def test_powershell_installer_stops_processes_before_retrying_locked_operations() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Stop-FlocksProcesses" in script
    assert "function Invoke-NativeCommand" in script
    assert "& $flocksCommand.Source stop" in script
    assert 'Join-Path $runDir "upgrade_server.pid"' in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Start-Process" in script
    assert "-RedirectStandardOutput $stdoutPath" in script
    assert "-RedirectStandardError $stderrPath" in script
    assert "Invoke-InstallerCommandWithLockRetry" in script
    assert "$result = Invoke-NativeCommandOrFail" in script
    assert "$null = Invoke-NativeCommandOrFail" in script
    assert '-Description "Python backend dependency installation"' in script
    assert '-Description "Global flocks CLI installation"' in script
    assert "Failed to update Windows PE resources" in script
    assert '& $ScriptBlock 2>&1' not in script
    assert "Stop-FlocksProcesses -Aggressive" not in script
    assert 'Get-Process -Name $name' not in script
    assert '"uv sync"' not in script
    assert '"vite preview"' not in script
    assert "$runtimePid = Get-PidFromRuntimeFile -PidFile $pidFile" in script
    assert "$pid = Get-PidFromRuntimeFile -PidFile $pidFile" not in script


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to execute PowerShell helper functions")
def test_powershell_runtime_pid_file_paths_return_three_string_paths() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")
    script_without_main = re.sub(r"\r?\nMain\s*$", "\n", script)
    test_script = (
        script_without_main
        + """

$env:FLOCKS_ROOT = Join-Path ([System.IO.Path]::GetTempPath()) 'FlocksRoot'
$paths = @(Get-RuntimePidFilePaths)
if ($paths.Count -ne 3) {
    Write-Host "count=$($paths.Count)"
    exit 1
}
if (-not $paths[0].EndsWith([IO.Path]::DirectorySeparatorChar + 'run' + [IO.Path]::DirectorySeparatorChar + 'backend.pid')) {
    Write-Host "path0=$($paths[0])"
    exit 1
}
if (-not $paths[1].EndsWith([IO.Path]::DirectorySeparatorChar + 'run' + [IO.Path]::DirectorySeparatorChar + 'webui.pid')) {
    Write-Host "path1=$($paths[1])"
    exit 1
}
if (-not $paths[2].EndsWith([IO.Path]::DirectorySeparatorChar + 'run' + [IO.Path]::DirectorySeparatorChar + 'upgrade_server.pid')) {
    Write-Host "path2=$($paths[2])"
    exit 1
}
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", test_script],
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output


def test_bootstrap_powershell_installer_uses_utf8_without_bom_with_crlf() -> None:
    data = (REPO_ROOT / "install.ps1").read_bytes()

    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_bootstrap_powershell_installer_unblocks_files_and_bypasses_execution_policy() -> None:
    script = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "function Unblock-InstallFiles" in script
    assert "Unblock-File" in script
    assert "function Invoke-WorkspaceInstaller" in script
    assert "& powershell -NoProfile -ExecutionPolicy Bypass -File $InstallerPath @InstallerArgs" in script
    assert "Invoke-WorkspaceInstaller -InstallerPath $installerPath -InstallerArgs $installerArgs" in script


def test_workspace_powershell_installer_uses_utf8_bom_with_crlf() -> None:
    data = (SCRIPT_DIR / "install.ps1").read_bytes()

    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
