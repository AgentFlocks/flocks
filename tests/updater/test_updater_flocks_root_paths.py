"""Backups and the installed-version marker must live under the runtime root.

Regression for the 2026-09 full test pass: ``_BACKUP_DIR`` / ``_VERSION_MARKER_PATH`` were
computed once at import time from ``Path.home()/.flocks`` and ignored ``FLOCKS_ROOT``, so an
instance started with ``FLOCKS_ROOT=/other/root`` read another installation's
``.current_version`` (reporting the wrong current version) and wrote its backups into
``~/.flocks/version``.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

from flocks.updater import updater


@pytest.fixture
def runtime_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "custom-root"
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(updater, "_BACKUP_DIR", None)
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", None)
    return root


def test_version_marker_follows_flocks_root(runtime_root: Path, monkeypatch, tmp_path: Path) -> None:
    home_marker = tmp_path / "home" / ".flocks" / "version" / ".current_version"
    home_marker.parent.mkdir(parents=True)
    home_marker.write_text("v2099.1.1\n", encoding="utf-8")  # another install's marker must be ignored
    monkeypatch.setattr(updater, "_read_pyproject_version", lambda: "2026.8.17")

    assert updater._version_marker_path() == runtime_root / "version" / ".current_version"
    assert updater.get_current_version() == "2026.8.17"

    updater._write_version_marker("2026.9.1")

    assert (runtime_root / "version" / ".current_version").read_text(encoding="utf-8") == "2026.9.1\n"
    assert updater.get_current_version() == "2026.9.1"
    assert home_marker.read_text(encoding="utf-8") == "v2099.1.1\n"


def test_backups_are_written_under_flocks_root(runtime_root: Path, tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    (install_root / "flocks").mkdir(parents=True)
    (install_root / "flocks" / "__init__.py").write_text("", encoding="utf-8")

    backup = updater._backup_current_version(install_root, "2026.8.17", retain_count=1)

    assert backup is not None
    assert backup.parent == runtime_root / "version"
    with tarfile.open(backup, "r:gz") as tar:
        assert "flocks/flocks/__init__.py" in tar.getnames()
    assert not (tmp_path / "home" / ".flocks").exists()


def test_pinned_backup_dir_still_wins_for_tests(runtime_root: Path, monkeypatch, tmp_path: Path) -> None:
    pinned = tmp_path / "pinned"
    monkeypatch.setattr(updater, "_BACKUP_DIR", pinned)

    assert updater._backup_dir() == pinned
    assert updater._version_marker_path() == pinned / ".current_version"


def test_default_root_is_home_dot_flocks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLOCKS_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(updater, "_BACKUP_DIR", None)
    monkeypatch.setattr(updater, "_VERSION_MARKER_PATH", None)

    assert updater._backup_dir() == tmp_path / ".flocks" / "version"


def test_replace_install_dir_keeps_top_level_symlinks_as_links(tmp_path: Path) -> None:
    """Top-level symlink entries were dereferenced by ``copy2`` while nested ones were
    preserved by ``copytree(symlinks=True)``; both levels now copy the link itself."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    (source / "link.txt").symlink_to("real.txt")
    (source / "pkg").mkdir()
    (source / "pkg" / "nested.txt").symlink_to("../real.txt")
    install = tmp_path / "install"
    install.mkdir()

    updater._replace_install_dir(source, install)

    assert (install / "link.txt").is_symlink() and os.readlink(install / "link.txt") == "real.txt"
    assert (install / "pkg" / "nested.txt").is_symlink()
    assert (install / "link.txt").read_text(encoding="utf-8") == "real"
