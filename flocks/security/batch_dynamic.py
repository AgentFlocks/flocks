"""Cleanup-only compatibility for containers left by historical dynamic batches."""

from __future__ import annotations

import re
from pathlib import Path
import subprocess

from flocks.security.batch import read_json

LABEL = "flocks.batch.task"


def docker(*args: str) -> str:
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker is unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Docker {' '.join(args[:2])}: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()


def owner(task_dir: Path) -> str:
    config = read_json(task_dir.parents[1] / "batch.json")
    return f"{config['batch_id']}:{task_dir.name}"


def remove_containers(task_owner: str, name: str | None = None) -> None:
    args = ["ps", "--all", "--quiet", "--filter", f"label={LABEL}={task_owner}"]
    if name:
        args += ["--filter", f"name=^/{re.escape(name)}$"]
    for container in docker(*args).splitlines():
        # A normally exiting --rm container can disappear between listing and removal.
        result = subprocess.run(["docker", "rm", "--force", container], capture_output=True, text=True, timeout=20)
        if result.returncode and "No such container" not in result.stderr:
            raise RuntimeError(f"Container cleanup failed: {result.stderr.strip()[:1000]}")
