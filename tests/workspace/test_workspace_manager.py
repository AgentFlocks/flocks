"""
Unit tests for WorkspaceManager

Tests path resolution, directory management, text-file detection,
and security (path-traversal prevention).
"""

from pathlib import Path
import datetime as dt

import pytest


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Provide an isolated workspace directory via FLOCKS_WORKSPACE_DIR.

    Also overrides FLOCKS_DATA_DIR so the memory-dir view never touches ~/.flocks.
    """
    ws = tmp_path / "workspace"
    data = tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    (data / "memory").mkdir()

    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(ws))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))

    # Reset both singletons so env vars are re-read
    from flocks.workspace.manager import WorkspaceManager
    from flocks.config.config import Config
    WorkspaceManager._instance = None
    Config._global_config = None

    yield ws

    WorkspaceManager._instance = None
    Config._global_config = None


@pytest.fixture()
def manager(tmp_workspace: Path):
    from flocks.workspace.manager import WorkspaceManager
    mgr = WorkspaceManager.get_instance()
    mgr.ensure_dirs()
    return mgr


# ─── Singleton ───────────────────────────────────────────────────────────────

class TestSingleton:
    def test_same_instance_returned(self, tmp_workspace: Path):
        from flocks.workspace.manager import WorkspaceManager
        a = WorkspaceManager.get_instance()
        b = WorkspaceManager.get_instance()
        assert a is b

    def test_instance_reset(self, tmp_workspace: Path):
        from flocks.workspace.manager import WorkspaceManager
        a = WorkspaceManager.get_instance()
        WorkspaceManager._instance = None
        b = WorkspaceManager.get_instance()
        assert a is not b


# ─── Directory paths ──────────────────────────────────────────────────────────

class TestDirectoryPaths:
    def test_get_workspace_dir_respects_env(self, tmp_workspace: Path, manager):
        assert manager.get_workspace_dir() == tmp_workspace

    def test_get_memory_dir_points_to_data_memory(self, tmp_workspace: Path, manager):
        mem = manager.get_memory_dir()
        assert mem.name == "memory"
        # Must be outside workspace
        assert not str(mem).startswith(str(tmp_workspace))

    def test_ensure_dirs_creates_convention_dirs(self, tmp_workspace: Path, manager):
        for name in ["outputs", "knowledge"]:
            assert (tmp_workspace / name).is_dir(), f"Missing convention dir: {name}"

    def test_ensure_dirs_idempotent(self, tmp_workspace: Path, manager):
        """Calling ensure_dirs twice must not raise."""
        manager.ensure_dirs()
        manager.ensure_dirs()

    def test_default_outputs_dir_uses_legacy_oss_layout(self, tmp_workspace: Path, manager):
        outputs_dir = manager.get_default_outputs_dir(today=dt.date(2026, 5, 9))
        assert outputs_dir == tmp_workspace / "outputs" / "2026-05-09"
        assert outputs_dir.is_dir()

    def test_default_outputs_dir_supports_username_layout(self, tmp_workspace: Path, manager):
        outputs_dir = manager.get_default_outputs_dir(
            username=" chen/jie ",
            today=dt.date(2026, 5, 9),
        )
        assert outputs_dir == tmp_workspace / "users" / "chen_jie" / "outputs" / "2026-05-09"
        assert outputs_dir.is_dir()


# ─── Path resolution ──────────────────────────────────────────────────────────

class TestPathResolution:
    def test_resolve_simple_relative(self, tmp_workspace: Path, manager):
        resolved = manager.resolve_workspace_path("outputs/report.pdf")
        assert resolved == tmp_workspace / "outputs" / "report.pdf"

    def test_resolve_nested_relative(self, tmp_workspace: Path, manager):
        resolved = manager.resolve_workspace_path("outputs/abc123/result.json")
        assert resolved == tmp_workspace / "outputs" / "abc123" / "result.json"

    def test_resolve_empty_path_returns_workspace_root(self, manager):
        # Empty string resolves to workspace root — that is allowed
        resolved = manager.resolve_workspace_path("")
        assert resolved == manager.get_workspace_dir().resolve()

    def test_reject_absolute_path(self, manager):
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            manager.resolve_workspace_path("/etc/passwd")

    def test_reject_path_traversal_dotdot(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_workspace_path("../../etc/passwd")

    def test_reject_path_traversal_in_subdir(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_workspace_path("outputs/../../secret")

    def test_memory_resolve_simple(self, tmp_workspace: Path, manager):
        memory_root = manager.get_memory_dir()
        resolved = manager.resolve_memory_path("MEMORY.md")
        assert resolved == (memory_root / "MEMORY.md").resolve()

    def test_memory_reject_absolute(self, manager):
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            manager.resolve_memory_path("/etc/passwd")

    def test_memory_reject_traversal(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_memory_path("../../etc/passwd")


# ─── Text-file detection ─────────────────────────────────────────────────────

class TestIsTextFile:
    @pytest.mark.parametrize("filename,expected", [
        ("README.md", True),
        ("notes.txt", True),
        ("server.log", True),
        ("config.json", True),
        ("settings.yaml", True),
        ("settings.yml", True),
        ("pyproject.toml", True),
        ("app.py", True),
        ("index.js", True),
        ("component.ts", True),
        ("component.tsx", True),
        ("deploy.sh", True),
        ("data.csv", True),
        ("report.xml", True),
        ("index.html", True),
        ("style.css", True),
        ("data.sql", True),
        # Binary / non-text
        ("archive.zip", False),
        ("image.png", False),
        ("photo.jpg", False),
        ("document.pdf", False),
        ("binary.exe", False),
        ("disk.dmg", False),
        ("lib.so", False),
        ("data.bin", False),
        # No extension
        ("Makefile", False),
    ])
    def test_extension_detection(self, tmp_path: Path, filename: str, expected: bool):
        from flocks.workspace.manager import WorkspaceManager
        p = tmp_path / filename
        p.touch()
        assert WorkspaceManager.is_text_file(p) == expected
