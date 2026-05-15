"""Shared browser utility helpers."""

import locale
import os
from pathlib import Path


def read_env_text(path: Path) -> str:
    """Read an env file as UTF-8 first, then fall back to the local encoding."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding=locale.getpreferredencoding(False))


def load_env_file(path: Path) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` env file."""
    for line in read_env_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
