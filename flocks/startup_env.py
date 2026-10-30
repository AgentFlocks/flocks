"""
The ``.env`` a Flocks process loads into its environment when it starts.

One file, found one way, loaded once: the ``.env`` beside the source tree,
otherwise the nearest one above the package.  Values already in the
environment win, as with ``python-dotenv``'s ``load_dotenv``.  ``flocks``
loads it on import, before anything decides on what the environment says --
the database backend first of all -- and the CLI and the server ask here
instead of looking for a ``.env`` of their own, so every reader sees the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_loaded = False
_path: Optional[Path] = None


def dotenv_path(package: Optional[Path] = None) -> Optional[Path]:
    """The ``.env`` to load, or ``None`` when there is none."""
    package = (package or Path(__file__).resolve().parent)
    beside = package.parent / ".env"
    if beside.is_file():
        return beside
    for directory in (package, *package.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load() -> Optional[Path]:
    """Load the ``.env`` (first call only); the file loaded, if any."""
    global _loaded, _path
    if _loaded:
        return _path
    _loaded = True
    path = dotenv_path()
    if path is not None:
        try:
            from dotenv import load_dotenv
        except ImportError:  # nothing to load it with; the environment is what it is
            return None
        load_dotenv(path, override=False)
        _path = path
    return _path


__all__ = ["dotenv_path", "load"]
