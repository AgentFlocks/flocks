"""Exercise real process exit: CliRunner cannot detect leaked SQLite threads."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("args", "exit_code", "json_output"),
    [
        (["list", "--format", "json"], 0, True),
        (["list", "--type", "scheduled", "--format", "json"], 0, True),
        ([], 0, False),
        (["dashboard"], 0, False),
        (["scheduled"], 0, False),
        (["queue"], 0, False),
        # Queue control talks to the running service; without one it fails cleanly.
        (["queue", "--pause"], 1, False),
        (["create", "exit-test"], 0, False),
        (["show", "missing"], 1, False),
        (["cancel", "missing"], 1, False),
        (["retry", "missing"], 1, False),
        (["rerun", "missing"], 1, False),
        (["create", "exit-test", "--type", "scheduled"], 1, False),
        (["list", "--status", "invalid"], 2, False),
    ],
)
def test_task_command_exits(tmp_path, args, exit_code, json_output):
    root = Path(__file__).resolve().parents[2]
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("FLOCKS_", "XDG_"))
    }
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        FLOCKS_ROOT=str(tmp_path / ".flocks"),
        PYTHONPATH=str(root),
        PYTHON_DOTENV_DISABLED="1",
        PYTHONIOENCODING="utf-8",
        NO_COLOR="1",
    )
    result = subprocess.run(
        [sys.executable, "-m", "flocks.cli.main", "task", *args],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    if json_output:
        assert json.loads(result.stdout) == []
    assert "Event loop is closed" not in result.stderr
