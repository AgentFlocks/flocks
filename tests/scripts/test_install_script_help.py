import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_bootstrap_bash_install_help_mentions_current_directory_default() -> None:
    install_cwd = REPO_ROOT.parent
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=install_cwd,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert str(install_cwd / "flocks") in output
    assert "current working directory" in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to inspect PowerShell help output")
def test_bootstrap_powershell_install_help_mentions_current_directory_default() -> None:
    install_cwd = REPO_ROOT.parent
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(REPO_ROOT / "install.ps1"), "-Help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=install_cwd,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert str(install_cwd / "flocks") in output
    assert "current directory" in output
    assert "Windows System32" in output
    assert "user home directory" in output
    assert "Administrator" in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to inspect PowerShell scriptblock output")
def test_bootstrap_powershell_install_help_supports_scriptblock_invocation() -> None:
    parser_command = (
        "$bytes = [System.IO.File]::ReadAllBytes("
        f"'{(REPO_ROOT / 'install.ps1').as_posix()}'"
        "); "
        "$content = [System.Text.UTF8Encoding]::new($true, $true).GetString($bytes).TrimStart([char]0xFEFF); "
        "& ([scriptblock]::Create($content)) -Help"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", parser_command],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT.parent,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert "Remote usage:" in output
    assert "scriptblock" in output


def test_bootstrap_powershell_relies_on_explicit_version_instead_of_invocation_line() -> None:
    script = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "Resolve-VersionFromInvocationLine" not in script
    assert "$Version = $DefaultBranch" in script




def test_bootstrap_bash_install_zh_help_mentions_gitee_raw_and_current_directory_default() -> None:
    install_cwd = REPO_ROOT.parent
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "install_zh.sh"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=install_cwd,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert str(install_cwd / "flocks") in output
    assert "当前工作目录" in output
    assert "https://gitee.com/flocks/flocks/raw/main/install_zh.sh" in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to inspect PowerShell help output")
def test_bootstrap_powershell_install_zh_help_mentions_gitee_raw_and_current_directory_default() -> None:
    install_cwd = REPO_ROOT.parent
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(REPO_ROOT / "install_zh.ps1"), "-Help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=install_cwd,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert str(install_cwd / "flocks") in output
    assert "当前目录" in output
    assert "Windows System32" in output
    assert "用户主目录" in output
    assert "https://gitee.com/flocks/flocks/raw/main/install_zh.ps1" in output
    assert "管理员" in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to execute PowerShell helper functions")
def test_bootstrap_powershell_install_defaults_to_user_home_when_current_directory_is_system32() -> None:
    script = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    script_without_main = script.rsplit("Main", 1)[0]
    test_script = (
        script_without_main
        + """

$system32Default = Get-DefaultInstallDir -CurrentDirectory 'C:\\Windows\\System32' -HomeDirectory 'C:\\Users\\tester'
if ($system32Default.Replace('/', '\\') -ne 'C:\\Users\\tester\\flocks') {
    Write-Host "system32=$system32Default"
    exit 1
}

$workspaceDefault = Get-DefaultInstallDir -CurrentDirectory 'D:\\workspace' -HomeDirectory 'C:\\Users\\tester'
if ($workspaceDefault.Replace('/', '\\') -ne 'D:\\workspace\\flocks') {
    Write-Host "workspace=$workspaceDefault"
    exit 1
}
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", test_script],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT.parent,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to execute PowerShell helper functions")
def test_bootstrap_powershell_install_zh_defaults_to_user_home_when_current_directory_is_system32() -> None:
    script = (REPO_ROOT / "install_zh.ps1").read_text(encoding="utf-8-sig")
    script_without_main = script.rsplit("Main", 1)[0]
    test_script = (
        script_without_main
        + """

$system32Default = Get-DefaultInstallDir -CurrentDirectory 'C:\\Windows\\System32' -HomeDirectory 'C:\\Users\\tester'
if ($system32Default.Replace('/', '\\') -ne 'C:\\Users\\tester\\flocks') {
    Write-Host "system32=$system32Default"
    exit 1
}

$workspaceDefault = Get-DefaultInstallDir -CurrentDirectory 'D:\\workspace' -HomeDirectory 'C:\\Users\\tester'
if ($workspaceDefault.Replace('/', '\\') -ne 'D:\\workspace\\flocks') {
    Write-Host "workspace=$workspaceDefault"
    exit 1
}
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", test_script],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT.parent,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output


def test_bash_install_help_mentions_optional_tui() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT_DIR / "install.sh"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert "--with-tui" in output
    assert "bun" in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to inspect PowerShell help output")
def test_powershell_install_help_mentions_optional_tui() -> None:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT_DIR / "install.ps1"), "-Help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    assert "-InstallTui" in output
    assert "bun" in output
