"""
Path utility helpers shared across Flocks modules.
"""

from pathlib import Path


def find_flocks_project_root() -> Path | None:
    """Walk upward from cwd and return the first directory that contains ``.flocks/``.

    Unlike :func:`find_project_root`, this returns ``None`` when none is found—no
    fallback to cwd. Use this for security-sensitive checks (e.g. avoid treating
    the whole filesystem as a project when cwd is ``/``).
    """
    current = Path.cwd().resolve()
    for directory in (current, *current.parents):
        if (directory / ".flocks").is_dir():
            return directory
    return None


def find_project_root() -> Path:
    """Walk up from cwd to locate the Flocks project root.

    Mimics how git locates ``.git/`` — searches the current directory and each
    ancestor in turn until it finds a directory that contains ``.flocks/``, or
    until the filesystem root is reached.

    Falls back to ``Path.cwd()`` when nothing is found (e.g. first-run before
    ``.flocks/`` has been created). Prefer :func:`find_flocks_project_root` for
    HTTP file access and other security-sensitive path checks.

    Returns:
        The nearest ancestor directory (inclusive of cwd) that contains a
        ``.flocks/`` sub-directory, or ``Path.cwd()`` as a fallback.
    """
    found = find_flocks_project_root()
    return found if found is not None else Path.cwd().resolve()


__all__ = ["find_project_root", "find_flocks_project_root"]
