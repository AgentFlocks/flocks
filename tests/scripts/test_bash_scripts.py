import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"
BASH_SCRIPTS = ("install.sh",)


def test_bash_scripts_parse_without_errors() -> None:
    result = subprocess.run(
        ["bash", "-n", *(str(SCRIPT_DIR / script_name) for script_name in BASH_SCRIPTS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
