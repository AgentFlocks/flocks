from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flocks.workflow import fs_store


@pytest.fixture(autouse=True)
def reset_workspace_root_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fs_store, "_workspace_root", None)


def test_all_discoverable_shipped_workflows_have_native_nonempty_group():
    from flocks.workflow.visibility import is_hidden_workflow_data

    loaded = []
    for folder in fs_store._SYSTEM_WORKFLOW_ROOT.iterdir():
        if not folder.is_dir():
            continue
        data = fs_store.read_workflow_dir(folder, folder.name, "project")
        if data is None or is_hidden_workflow_data(data):
            continue
        loaded.append(data)
        assert data.get("group"), folder
        assert data["group_readonly"] is True, folder
    assert len(loaded) >= 2


@pytest.mark.parametrize("draft", [False, True])
def test_native_group_metadata_helper_handles_package_without_meta(tmp_path: Path, draft: bool):
    folder = tmp_path / "installed-workflow"
    folder.mkdir()
    source = folder / ("workflow.md" if draft else "workflow.json")
    payload = b"# Installed draft\r\n  preserve  \r\n" if draft else b'{"name":"Installed","start":"n1","nodes":[],"edges":[]}'
    source.write_bytes(payload)
    result = fs_store.patch_workflow_metadata(folder, {"group": "  Response  ", "vendor": {"keep": 1}})
    assert result["group"] == "Response"
    assert source.read_bytes() == payload
    assert (folder / "workflow.json").exists() is not draft
    fs_store.patch_workflow_metadata(folder, {"group": None})
    metadata = json.loads((folder / "meta.json").read_text())
    assert metadata["group"] == ""
    assert metadata["vendor"] == {"keep": 1}
    assert fs_store.read_workflow_dir(folder, folder.name, "global")["group"] == ""


def test_native_group_metadata_staging_uses_destination_id(tmp_path: Path):
    folder = tmp_path / ".plugin-random-staging"
    folder.mkdir()
    (folder / "workflow.md").write_text("Draft without a title\n", encoding="utf-8")
    result = fs_store.patch_workflow_metadata(folder, {"group": "Preserved"}, workflow_id="real-workflow")
    assert result["name"] == "real-workflow"
    assert not (folder / "workflow.json").exists()
    assert ".plugin-random-staging" not in (folder / "meta.json").read_text()


def test_native_group_meta_is_only_local_authority(tmp_path: Path):
    folder = tmp_path / "workflow"
    folder.mkdir()
    source = folder / "workflow.json"
    source.write_text(json.dumps({"name": "Example", "metadata": {"group": "Transport only"}}))
    before = source.read_bytes()
    loaded = fs_store.read_workflow_dir(folder, "workflow", "global")
    assert not loaded.get("group")
    assert "group" not in loaded["workflowJson"]["metadata"]
    assert source.read_bytes() == before
    assert not (folder / "meta.json").exists()


@pytest.mark.parametrize("group", ["Changed", "", None])
def test_metadata_writer_rejects_shipped_group_before_other_metadata_changes(tmp_path, monkeypatch, group):
    folder = tmp_path / "shipped"
    folder.mkdir()
    (folder / "workflow.md").write_text("# Shipped\n")
    (folder / "meta.json").write_bytes(b'{"group":"Fixed","name":"Original"}')
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", tmp_path)
    before = (folder / "meta.json").read_bytes()
    with pytest.raises(ValueError, match="read-only"):
        fs_store.patch_workflow_metadata(folder, {"group": group, "name": "Must not write"})
    assert (folder / "meta.json").read_bytes() == before
    fs_store.patch_workflow_metadata(folder, {"group": "Fixed", "name": "Allowed"})
    assert fs_store.read_workflow_dir(folder, "shipped", "global")["group_readonly"] is True
    assert json.loads((folder / "meta.json").read_text())["name"] == "Allowed"


def test_group_readonly_comes_from_selected_source_not_project_or_stored_flag(tmp_path, monkeypatch):
    folder = tmp_path / "custom"
    folder.mkdir()
    (folder / "workflow.json").write_text('{"metadata":{"group":"Stale overlay"}}')
    (folder / "meta.json").write_text('{"group":"Native","group_readonly":true}')
    loaded = fs_store.read_workflow_dir(folder, "custom", "project")
    assert loaded["group_readonly"] is False
    assert loaded["group"] == "Native"
    assert "group" not in loaded["workflowJson"]["metadata"]
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", tmp_path)
    (folder / "meta.json").write_text('{"group":"Native","group_readonly":false}')
    assert fs_store.read_workflow_dir(folder, "custom", "global")["group_readonly"] is True


def test_group_lock_follows_real_definition_file_not_symlink_directory(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped"
    folder = shipped / "alias"
    folder.mkdir(parents=True)
    external = tmp_path / "custom.json"
    external.write_text('{"name":"Custom"}')
    (folder / "workflow.json").symlink_to(external)
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", shipped)
    assert fs_store.is_system_workflow_definition(folder) is False
    empty = shipped / "empty"
    empty.mkdir()
    assert fs_store.is_system_workflow_definition(empty) is False


def _write_workflow(base_dir: Path, workflow_id: str, name: str) -> None:
    workflow_dir = base_dir / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "name": name,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )


def test_read_workflow_from_fs_refreshes_cached_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first_workspace = tmp_path / "workspace-a"
    second_workspace = tmp_path / "workspace-b"
    workflow_id = "cache-switch-demo"
    _write_workflow(first_workspace, workflow_id, "workspace-a")
    _write_workflow(second_workspace, workflow_id, "workspace-b")

    monkeypatch.chdir(first_workspace)
    first = fs_store.read_workflow_from_fs(workflow_id)

    monkeypatch.chdir(second_workspace)
    second = fs_store.read_workflow_from_fs(workflow_id)

    assert first is not None
    assert second is not None
    assert first["workflowJson"]["name"] == "workspace-a"
    assert second["workflowJson"]["name"] == "workspace-b"
    assert fs_store.find_workspace_root() == second_workspace


def test_read_workflow_from_fs_prefers_user_workflow_over_project_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workflow_id = "shared-workflow"
    project_root = tmp_path / "project-workflows"
    user_root = tmp_path / "user-workflows"

    for root, name in (
        (project_root, "project bundle"),
        (user_root, "user customization"),
    ):
        workflow_dir = root / workflow_id
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "workflow.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "start": "n1",
                    "nodes": [{"id": "n1", "type": "python", "code": "pass"}],
                    "edges": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        fs_store,
        "resolve_workflow_scan_roots",
        lambda _workspace: [(project_root, "project"), (user_root, "global")],
    )

    workflow = fs_store.read_workflow_from_fs(workflow_id)

    assert workflow is not None
    assert workflow["source"] == "global"
    assert workflow["workflowJson"]["name"] == "user customization"


def test_read_workflow_dir_uses_latest_file_mtime_when_meta_is_stale(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workflow_id = "mtime-sync-demo"
    _write_workflow(workspace, workflow_id, "mtime-demo")
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id

    meta_file = workflow_dir / "meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "name": "mtime-demo",
                "description": "demo",
                "category": "default",
                "status": "draft",
                "createdBy": None,
                "createdAt": 1000,
                "updatedAt": 1000,
            }
        ),
        encoding="utf-8",
    )
    md_file = workflow_dir / "workflow.md"
    md_file.write_text("# demo\n", encoding="utf-8")

    json_file = workflow_dir / "workflow.json"
    os.utime(meta_file, (1, 1))
    os.utime(json_file, (5, 5))
    os.utime(md_file, (9, 9))

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "project")

    assert data is not None
    assert data["updatedAt"] == 9000
    assert data["markdownContent"] == "# demo\n"
    assert data["editMarkdownContent"] == "# demo\n"


def test_read_workflow_dir_uses_legacy_edit_markdown_only_as_fallback(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workflow_id = "legacy-edit-md-demo"
    _write_workflow(workspace, workflow_id, "legacy-demo")
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id

    (workflow_dir / "workflow.edit.md").write_text("# legacy\n", encoding="utf-8")

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "project")

    assert data is not None
    assert data["markdownContent"] == "# legacy\n"
    assert data["editMarkdownContent"] == "# legacy\n"


def test_read_workflow_dir_supports_markdown_only_draft(
    tmp_path: Path,
):
    workflow_id = "domain_intel_query"
    workflow_dir = tmp_path / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.md").write_text(
        "# Domain Intel Query\n\n## Purpose\n\nDraft spec only.\n",
        encoding="utf-8",
    )

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "global")

    assert data is not None
    assert data["id"] == workflow_id
    assert data["name"] == "Domain Intel Query"
    assert data["status"] == "draft"
    assert data["source"] == "global"
    assert data["workflowJson"] == {"start": "", "nodes": [], "edges": []}
    assert data["markdownContent"].startswith("# Domain Intel Query")
    assert data["editMarkdownContent"] == data["markdownContent"]


def test_read_workflow_from_fs_discovers_markdown_only_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace"
    workflow_id = "markdown-only-demo"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.md").write_text("# Markdown Only Demo\n", encoding="utf-8")

    monkeypatch.chdir(workspace)

    data = fs_store.read_workflow_from_fs(workflow_id)

    assert data is not None
    assert data["id"] == workflow_id
    assert data["name"] == "Markdown Only Demo"
    assert data["workflowJson"]["nodes"] == []


def test_resolve_workflow_id_from_markdown_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace"
    workflow_id = "markdown-path-demo"
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    md_file = workflow_dir / "workflow.md"
    md_file.write_text("# Markdown Path Demo\n", encoding="utf-8")

    monkeypatch.chdir(workspace)

    assert fs_store.resolve_workflow_id_from_source(str(md_file)) == workflow_id


def test_read_workflow_dir_exposes_localized_names_from_metadata(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workflow_id = "localized-name-demo"
    _write_workflow(workspace, workflow_id, "Localized Name Demo")
    workflow_dir = workspace / ".flocks" / "plugins" / "workflows" / workflow_id
    workflow_json = json.loads((workflow_dir / "workflow.json").read_text(encoding="utf-8"))
    workflow_json["metadata"] = {
        "nameI18n": {
            "zh-CN": "本地化名称演示",
            "en-US": "Localized Name Demo",
        }
    }
    (workflow_dir / "workflow.json").write_text(json.dumps(workflow_json), encoding="utf-8")

    data = fs_store.read_workflow_dir(workflow_dir, workflow_id, "project")

    assert data is not None
    assert data["nameI18n"] == {
        "zh-CN": "本地化名称演示",
        "en-US": "Localized Name Demo",
    }
