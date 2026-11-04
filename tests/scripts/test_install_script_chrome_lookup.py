"""``resolve_chrome_for_testing_path_from_dir`` must work on macOS' bash 3.2.

Regression for the 2026-09 full test pass: the lookup used ``shopt -s globstar`` and
``**`` globs, which do not exist in bash 3.2 (``/bin/bash`` on macOS), so a freshly
downloaded Chrome for Testing was reported as "could not be located". It now uses
``find``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install.sh"


def _lib(tmp_path: Path) -> Path:
    lib = tmp_path / "installer-lib.sh"
    lib.write_text(INSTALLER.read_text(encoding="utf-8").replace('\nmain "$@"\n', "\n"), encoding="utf-8")
    return lib


def _resolve(tmp_path: Path, browser_dir: Path, bash: str) -> subprocess.CompletedProcess[str]:
    cmd = f'source "{_lib(tmp_path)}"; resolve_chrome_for_testing_path_from_dir "{browser_dir}"'
    return subprocess.run([bash, "-c", cmd], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})


@pytest.mark.parametrize("bash", ["/bin/bash", "bash"])
@pytest.mark.parametrize("layout", [
    ("chrome/mac_arm-1/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
    ("chrome/linux-1/chrome-linux64/chrome"),
    ("chrome/win64-1/chrome-win64/chrome.exe"),
])
def test_lookup_finds_nested_executable_without_globstar(tmp_path: Path, bash: str, layout: str) -> None:
    if not Path(bash).exists() and bash.startswith("/"):
        pytest.skip(f"{bash} not available")
    browser_dir = tmp_path / "browser"
    executable = browser_dir / layout
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    result = _resolve(tmp_path, browser_dir, bash)

    assert result.returncode == 0, result.stderr
    assert result.stdout == str(executable)


def test_lookup_ignores_non_executable_and_missing_dir(tmp_path: Path) -> None:
    browser_dir = tmp_path / "browser"
    (browser_dir / "chrome").mkdir(parents=True)
    (browser_dir / "chrome" / "chrome").write_text("not executable", encoding="utf-8")

    assert _resolve(tmp_path, browser_dir, "/bin/bash").returncode == 1
    assert _resolve(tmp_path, tmp_path / "missing", "/bin/bash").returncode == 1
