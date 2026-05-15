import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"
BASH_SCRIPTS = ("install.sh", "dev.sh")


def test_bash_scripts_parse_without_errors() -> None:
    result = subprocess.run(
        ["bash", "-n", *(str(SCRIPT_DIR / script_name) for script_name in BASH_SCRIPTS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_dev_script_stops_backend_process_tree_on_exit() -> None:
    script = (SCRIPT_DIR / "dev.sh").read_text(encoding="utf-8")

    assert "set -m" in script
    assert 'BACKEND_PGID="$(process_group_id "${BACKEND_PID}")"' in script
    assert "process_group_id()" in script
    assert "collect_process_group_pids()" in script
    assert "stop_process_group()" in script
    assert 'kill -TERM -- "-${pgid}"' in script
    assert 'kill -KILL -- "-${pgid}"' in script
    assert 'if [ -n "${BACKEND_PGID}" ] && [ "${BACKEND_PGID}" != "${shell_pgid}" ]; then' in script
    assert "collect_descendant_pids()" in script
    assert "stop_process_tree()" in script
    assert "trap cleanup EXIT" in script
    assert 'stop_process_group "${BACKEND_PGID}" "后端服务" || stop_process_tree "${BACKEND_PID}" "后端服务"' in script
    assert 'kill -TERM "${kill_targets[@]}"' in script
    assert 'kill -KILL "${remaining[@]}"' in script
    assert 'kill "${BACKEND_PID}" 2>/dev/null || true' not in script
