"""Filesystem locations owned by the code-security plugin."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def ensure_private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def data_dir() -> Path:
    return Path.home() / ".flocks" / "workspace" / "code-security" / "data"


def snapshots_dir() -> Path:
    return data_dir() / "snapshots"


def runtime_dir() -> Path:
    return Path.home() / ".flocks" / "workspace" / "code-security" / "runtime"


def docker_runtime_dir(scan_id: str) -> Path:
    if not scan_id.startswith("scan_") or not scan_id[5:].isalnum():
        raise ValueError("Invalid scan identifier")
    return runtime_dir() / "docker" / scan_id


def outputs_root() -> Path:
    return Path.home() / ".flocks" / "workspace" / "outputs"


def output_dir(scan_id: str) -> Path:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return (
        Path.home()
        / ".flocks"
        / "workspace"
        / "outputs"
        / today
        / "code-security"
        / scan_id
    )
