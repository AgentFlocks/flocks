from pathlib import Path

from flocks.workspace.manager import WorkspaceManager


def test_workspace_migrate_single_user_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None
    mgr = WorkspaceManager.get_instance()

    root = mgr.get_workspace_dir()
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    (root / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "a.txt").write_text("x", encoding="utf-8")
    (root / "knowledge" / "k.md").write_text("k", encoding="utf-8")

    result = mgr.migrate_root_workspace_to_user("admin-1", dry_run=False)
    assert result["moved_outputs"] is True
    assert result["moved_knowledge"] is True
    assert (root / "users" / "admin-1" / "outputs" / "a.txt").exists()
    assert (root / "shared" / "knowledge" / "k.md").exists()
