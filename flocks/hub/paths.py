"""Path helpers for the bundled Flocks Hub."""

from __future__ import annotations

import os
from pathlib import Path

from flocks.project.instance import Instance


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("FLOCKS_HUB_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())

    project_dir = Instance.get_directory()
    if project_dir:
        roots.append(Path(project_dir) / ".flocks" / "flockshub")

    cwd = Path.cwd().resolve()
    roots.extend(candidate / ".flocks" / "flockshub" for candidate in [cwd, *cwd.parents])

    package_root = Path(__file__).resolve().parents[2]
    roots.append(package_root / ".flocks" / "flockshub")
    roots.append(package_root.parent / ".flocks" / "flockshub")
    return roots


def get_bundled_hub_root() -> Path:
    for root in _candidate_roots():
        if root.is_dir():
            return root
    return Path.cwd().resolve() / ".flocks" / "flockshub"


def safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be relative and cannot contain '..'")
    return path
