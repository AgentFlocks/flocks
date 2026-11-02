import os
import stat
import sys

import pytest

from flocks.utils.file_modes import ensure_owner_writable, remove_tree

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _read_only_copy(root):
    """A copy of a read-only source: no write bit anywhere, one executable."""
    (root / "page" / "src").mkdir(parents=True)
    (root / "page" / "src" / "index.tsx").write_text("export default 1;\n", encoding="utf-8")
    (root / "page" / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "page" / "build.sh").chmod(0o555)
    (root / "page" / "src" / "index.tsx").chmod(0o444)
    for directory in (root / "page" / "src", root / "page", root):
        directory.chmod(0o555)


def test_owner_gets_write_access_and_nothing_else_changes(tmp_path):
    root = tmp_path / "copy"
    _read_only_copy(root)

    ensure_owner_writable(root)

    assert _mode(root) == _mode(root / "page") == _mode(root / "page" / "src") == 0o755
    assert _mode(root / "page" / "src" / "index.tsx") == 0o644
    assert _mode(root / "page" / "build.sh") == 0o755


def test_non_recursive_opens_up_the_root_alone(tmp_path):
    root = tmp_path / "copy"
    _read_only_copy(root)

    ensure_owner_writable(root, recursive=False)

    assert _mode(root) == 0o755
    assert _mode(root / "page") == 0o555
    for directory in (root / "page" / "src", root / "page"):
        directory.chmod(0o755)


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("not ours", encoding="utf-8")
    (outside / "file.txt").chmod(0o444)
    outside.chmod(0o555)
    root = tmp_path / "copy"
    root.mkdir()
    (root / "file-link").symlink_to(outside / "file.txt")
    (root / "dir-link").symlink_to(outside, target_is_directory=True)
    try:
        ensure_owner_writable(root)
        assert _mode(outside) == 0o555
        assert _mode(outside / "file.txt") == 0o444
    finally:
        outside.chmod(0o755)


def test_a_single_file_is_handled(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    path.chmod(0o444)

    ensure_owner_writable(path)

    assert _mode(path) == 0o644


def test_remove_tree_deletes_a_copy_holding_read_only_directories(tmp_path):
    root = tmp_path / "scratch"
    _read_only_copy(root)

    remove_tree(root)

    assert not root.exists()
