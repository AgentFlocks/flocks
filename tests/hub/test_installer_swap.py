"""Unit tests for the Hub installer's Windows-safe directory swap helpers.

These exercise the atomic-swap path in isolation (explicit ``tmp_path``
dirs, no ``Path.home()`` dependency), covering the WinError 5 aftermath
where a directory watcher or AV scan holds a transient handle on the
freshly staged tree and blocks ``src.replace(dst)``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.hub import installer


def _make_tree(path: Path, marker: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(marker, encoding="utf-8")


def test_replace_dir_swaps_into_place(tmp_path):
    src = tmp_path / ".soc_ui.scratch"
    dst = tmp_path / "soc_ui"
    _make_tree(src, "new")

    installer._replace_dir(src, dst)

    assert (dst / "manifest.json").read_text(encoding="utf-8") == "new"
    assert not src.exists()
    assert not (tmp_path / ".soc_ui.bak").exists()


def test_replace_dir_overwrites_existing_and_removes_backup(tmp_path):
    src = tmp_path / ".soc_ui.scratch"
    dst = tmp_path / "soc_ui"
    _make_tree(src, "new")
    _make_tree(dst, "old")

    installer._replace_dir(src, dst)

    assert (dst / "manifest.json").read_text(encoding="utf-8") == "new"
    assert not (tmp_path / ".soc_ui.bak").exists()


def test_replace_with_retry_recovers_from_transient_permission_error(tmp_path, monkeypatch):
    """A first ``PermissionError`` (WinError 5) is retried, not surfaced."""
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer.time, "sleep", lambda _s: None)

    src = tmp_path / ".soc_ui.scratch"
    dst = tmp_path / "soc_ui"
    _make_tree(src, "new")

    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    installer._replace_with_retry(src, dst)

    assert calls["n"] == 2
    assert (dst / "manifest.json").read_text(encoding="utf-8") == "new"


def test_replace_with_retry_reraises_after_exhausting_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer.time, "sleep", lambda _s: None)

    src = tmp_path / ".soc_ui.scratch"
    dst = tmp_path / "soc_ui"
    _make_tree(src, "new")

    def always_denied(self, target):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(Path, "replace", always_denied)
    with pytest.raises(PermissionError):
        installer._replace_with_retry(src, dst)


def test_purge_stale_scratch_removes_leftovers(tmp_path):
    parent = tmp_path
    _make_tree(parent / ".soc_ui.55ram7wo" / "soc_overview", "stranded")
    _make_tree(parent / ".soc_ui.bak" / "soc_dashboard", "stranded")
    _make_tree(parent / "soc_ui", "live")
    # Unrelated dot-dir for a different plugin must be left untouched.
    _make_tree(parent / ".other.bak", "keep")

    installer._purge_stale_scratch(parent, "soc_ui")

    assert not (parent / ".soc_ui.55ram7wo").exists()
    assert not (parent / ".soc_ui.bak").exists()
    assert (parent / "soc_ui" / "manifest.json").read_text(encoding="utf-8") == "live"
    assert (parent / ".other.bak").exists()


def test_copy_package_purges_stale_scratch_before_staging(tmp_path):
    """A prior failed install's leftovers self-heal on the next install."""
    src = tmp_path / "bundled" / "soc_ui"
    _make_tree(src, "payload")
    (src / "manifest.json").write_text('{"id": "soc_ui"}', encoding="utf-8")
    (src / "src").mkdir()
    (src / "src" / "index.tsx").write_text("export default 1;\n", encoding="utf-8")

    dst = tmp_path / "install" / "soc_ui"
    dst.parent.mkdir(parents=True)
    _make_tree(dst.parent / ".soc_ui.leftover" / "soc_overview", "stranded")

    installer._copy_package(src, dst)

    assert (dst / "src" / "index.tsx").is_file()
    assert not (dst.parent / ".soc_ui.leftover").exists()
    # ``manifest.json`` is intentionally not copied by the installer.
    assert not (dst / "manifest.json").exists()
