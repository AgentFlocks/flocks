"""Owner access on trees Flocks copies out of read-only places.

``shutil.copytree`` and ``shutil.copy2`` give every copy the permission bits
of its source.  A source can be read-only on purpose: an installed package, an
archive unpacked with its modes, a read-only checkout.  The copy then refuses
its own owner the writes Flocks makes next (building pages into it, patching
metadata in it) and the moves and deletes of later updates and uninstalls.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_DIRECTORY_BITS = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
_FILE_BITS = stat.S_IRUSR | stat.S_IWUSR


def _add_mode_bits(path: str, bits: int) -> None:
    """Add *bits* to the permission bits of *path*; a symlink is left alone."""
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        return
    if stat.S_IMODE(mode) & bits != bits:
        os.chmod(path, stat.S_IMODE(mode) | bits)


def ensure_owner_writable(root: Path) -> None:
    """Let the owner read, write and enter everything under *root*.

    For a tree the caller has just copied and owns.  Bits are only added, so
    executables stay executable, and no symlink is followed.
    """
    root_path = os.fspath(root)
    mode = os.lstat(root_path).st_mode
    if stat.S_ISLNK(mode):
        return
    if not stat.S_ISDIR(mode):
        _add_mode_bits(root_path, _FILE_BITS)
        return
    _add_mode_bits(root_path, _DIRECTORY_BITS)
    for directory, dirnames, filenames in os.walk(root_path):
        # A directory opens up before the walk goes into it.
        for name in dirnames:
            _add_mode_bits(os.path.join(directory, name), _DIRECTORY_BITS)
        for name in filenames:
            _add_mode_bits(os.path.join(directory, name), _FILE_BITS)


def remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
    """``shutil.rmtree`` for a copy that may hold read-only directories.

    Removing an entry takes write access to the directory holding it, which a
    copy of a read-only source does not give.
    """
    try:
        ensure_owner_writable(path)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=ignore_errors)
