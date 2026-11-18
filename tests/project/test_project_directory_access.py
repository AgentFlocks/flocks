"""The allowed roots and the service account's access to a project directory.

They are separate settings an administrator changes (FLOCKS_PROJECT_ROOTS; the
folder's permissions or ACL), so a directory the account running Flocks cannot
reach is reported as such, never as missing, and both are named when both are
missing (INC-61BE1F94).
"""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

import flocks.project.project as project_module
from flocks.project.project import Project

posix_permissions = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX directory permissions enforced for this account",
)


@contextmanager
def _mode(path: Path, mode: int):
    original = path.stat().st_mode & 0o777
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(original)


@pytest.fixture
def allowed_root(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(root))
    return root


@posix_permissions
def test_unreachable_directory_is_not_reported_missing(allowed_root):
    parent = allowed_root / "svc-denied"
    worktree = parent / "repo"
    worktree.mkdir(parents=True)

    with _mode(parent, 0o600):
        with pytest.raises(ValueError, match=r"^Project directory is not readable$"):
            Project.validate_worktree(str(worktree))
        assert Project._path_status(str(worktree)) == "unreadable"


@posix_permissions
def test_missing_root_and_missing_permission_are_both_named(allowed_root, tmp_path):
    parent = tmp_path / "elsewhere"
    worktree = parent / "repo"
    worktree.mkdir(parents=True)

    with _mode(parent, 0o600):
        with pytest.raises(
            ValueError,
            match=r"^Project directory is outside the allowed roots and is not readable$",
        ):
            Project.validate_worktree(str(worktree))
    with pytest.raises(ValueError, match=r"^Project directory is outside the allowed roots$"):
        Project.validate_worktree(str(worktree))


@posix_permissions
def test_creating_under_an_unreachable_parent_is_a_permission_error(allowed_root):
    parent = allowed_root / "svc-denied"
    parent.mkdir()

    with _mode(parent, 0o600):
        with pytest.raises(ValueError, match=r"^Project directory is not readable$"):
            Project.validate_worktree(str(parent / "repo"), create_if_missing=True)
    assert not (parent / "repo").exists()


@posix_permissions
def test_listing_is_checked_where_permission_bits_allow_it(allowed_root, monkeypatch):
    """Windows' os.access ignores the ACL that leaves the service account out."""

    worktree = allowed_root / "acl-denied"
    worktree.mkdir()
    real_access = os.access

    def access_ignoring_acl(path, mode, *args, **kwargs):
        if Path(path) == worktree:
            return True
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(project_module.os, "access", access_ignoring_acl)
    with _mode(worktree, 0o000):
        assert Project._path_status(str(worktree)) == "unreadable"
        with pytest.raises(ValueError, match=r"^Project directory is not readable$"):
            Project.validate_worktree(str(worktree))
    assert Project._path_status(str(worktree)) == "available"


def test_what_did_not_change(allowed_root):
    path = allowed_root / "file.txt"
    path.write_text("x", encoding="utf-8")
    readable = allowed_root / "repo"
    readable.mkdir()

    with pytest.raises(ValueError, match=r"^Project path must be a directory$"):
        Project.validate_worktree(str(path))
    with pytest.raises(ValueError, match=r"^Project directory does not exist$"):
        Project.validate_worktree(str(allowed_root / "absent"))
    assert Project.validate_worktree(str(readable)) == os.path.normcase(str(readable.resolve()))
    created = allowed_root / "new" / "nested"
    assert Project.validate_worktree(str(created), create_if_missing=True) == os.path.normcase(
        str(created.resolve())
    )
    assert Project._path_status(str(readable)) == "available"
    assert Project._path_status(str(allowed_root / "absent")) == "missing"
