import shutil
import subprocess
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"
POWERSHELL_SCRIPTS = (
    REPO_ROOT / "install.ps1",
    REPO_ROOT / "install_zh.ps1",
    SCRIPT_DIR / "install.ps1",
    SCRIPT_DIR / "install_zh.ps1",
    SCRIPT_DIR / "run.ps1",
)
POWERSHELL_SCRIPT_ENCODINGS = (
    (REPO_ROOT / "install.ps1", False),
    (REPO_ROOT / "install_zh.ps1", False),
    (SCRIPT_DIR / "install.ps1", True),
    (SCRIPT_DIR / "install_zh.ps1", True),
    (SCRIPT_DIR / "run.ps1", True),
)


def _parse_script(script_path: Path) -> subprocess.CompletedProcess[str]:
    parser_command = (
        "$tokens = $null; "
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script_path.as_posix()}', "
        "[ref]$tokens, "
        "[ref]$errors"
        ") | Out-Null; "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { $_.ToString() }; "
        "exit 1 "
        "}"
    )
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", parser_command],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is required to parse PowerShell scripts")
@pytest.mark.parametrize("script_path", POWERSHELL_SCRIPTS)
def test_powershell_scripts_parse_without_errors(script_path: Path) -> None:
    result = _parse_script(script_path)
    assert result.returncode == 0, result.stdout or result.stderr


@pytest.mark.parametrize(("script_path", "expect_bom"), POWERSHELL_SCRIPT_ENCODINGS)
def test_powershell_scripts_use_expected_utf8_encoding_and_crlf(
    script_path: Path, expect_bom: bool
) -> None:
    script_bytes = script_path.read_bytes()

    assert script_bytes.startswith(b"\xef\xbb\xbf") is expect_bom
    assert b"\r\n" in script_bytes
    assert script_bytes.replace(b"\r\n", b"").count(b"\n") == 0


def test_windows_installer_process_match_logic_avoids_matches_auto_variable() -> None:
    script_text = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8")
    function_match = re.search(
        r"function Get-FlocksProcessIds \{.*?^}\s*$",
        script_text,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert function_match is not None
    function_text = function_match.group(0)
    assert "$matches" not in function_text
    assert "[Regex]::IsMatch" in function_text
