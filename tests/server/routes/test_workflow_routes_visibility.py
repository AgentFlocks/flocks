from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.server.routes import workflow as workflow_routes


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    name: str,
    meta: dict | None = None,
) -> None:
    workflow_dir = root / workflow_id
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
    if meta is not None:
        (workflow_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_list_workflows_from_fs_skips_hidden_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_root = tmp_path / ".flocks" / "plugins" / "workflows"
    _write_workflow(workflow_root, "visible", name="visible")
    _write_workflow(
        workflow_root,
        "__hidden_template",
        name="hidden template",
        meta={"hidden": True, "templateOnly": True},
    )
    monkeypatch.setattr(
        workflow_routes,
        "_all_scan_dirs",
        lambda: [(workflow_root, "project")],
    )

    items = workflow_routes._list_workflows_from_fs()

    assert [item["id"] for item in items] == ["visible"]


@pytest.fixture
def native_group_workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock, Mock
    from flocks.workflow import fs_store

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "legacy-project-workflows"
    user = home / ".flocks" / "plugins" / "workflows"
    project.mkdir()
    user.mkdir(parents=True)
    roots = [(project, "project"), (user, "global")]
    monkeypatch.setattr(fs_store, "resolve_workflow_scan_roots", lambda _workspace: roots)
    monkeypatch.setattr(workflow_routes, "_all_scan_dirs", lambda: roots)
    monkeypatch.setattr(workflow_routes, "_global_workflow_dir", lambda name: user / name)
    monkeypatch.setattr(workflow_routes, "_migrate_storage_to_filesystem", AsyncMock())
    monkeypatch.setattr(workflow_routes, "_get_workflow_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(workflow_routes, "publish_event", AsyncMock())
    monkeypatch.setattr(workflow_routes, "run_workflow", Mock(side_effect=AssertionError("Metadata must not execute")))
    monkeypatch.setattr(workflow_routes, "_get_workflow_integration_status", AsyncMock(return_value={
        "api": {"configured": False, "state": "unconfigured"},
        "trigger": {"configured": False, "state": "unconfigured"},
    }))
    return project, user


@pytest.mark.asyncio
async def test_native_group_workflow_draft_changes_only_meta(client, native_group_workflows):
    project, _ = native_group_workflows
    folder = project / "draft-id"
    folder.mkdir()
    markdown = b"# Draft title\r\n\r\n Do not rewrite this body  \r\n"
    (folder / "workflow.md").write_bytes(markdown)
    (folder / "meta.json").write_text(json.dumps({"x-owner": {"keep": [1, 2]}, "hidden": False}))
    for value, expected in (("  Team A  ", "Team A"), (None, ""), ("", "")):
        response = await client.put("/api/workflow/draft-id", json={"group": value})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == expected
        assert response.json()["status"] == "draft"
        assert response.json()["id"] == "draft-id"
        assert response.json()["source"] == "project"
        assert response.json()["group_readonly"] is False
        assert (folder / "workflow.md").read_bytes() == markdown
        assert not (folder / "workflow.json").exists()
        meta = json.loads((folder / "meta.json").read_text())
        assert meta["x-owner"] == {"keep": [1, 2]}
        assert meta["group"] == expected
        assert (await client.get("/api/workflow/draft-id")).json()["group"] == expected
    renamed = await client.put("/api/workflow/draft-id", json={"name": "Renamed draft"})
    assert renamed.status_code == 200
    assert renamed.json()["group"] == ""
    assert not (folder / "workflow.json").exists()


@pytest.mark.asyncio
async def test_native_group_workflow_preserves_selected_source_definition_and_unknown_meta(client, native_group_workflows):
    project, user = native_group_workflows
    for root, group in ((project, "Project"), (user, "User")):
        _write_workflow(root, "shared-id", name="Shared", meta={"group": group, "custom": {"keep": True}})
    folder = user / "shared-id"
    json_before = (folder / "workflow.json").read_bytes()
    response = await client.put("/api/workflow/shared-id", json={"group": "  Operations  "})
    assert response.status_code == 200, response.text
    assert response.json()["group"] == "Operations"
    assert (folder / "workflow.json").read_bytes() == json_before
    assert json.loads((project / "shared-id" / "meta.json").read_text())["group"] == "Project"
    definition = json.loads(json_before)
    definition["metadata"] = {"group": "Must not override meta", "custom-runtime": True}
    renamed = await client.put("/api/workflow/shared-id", json={"name": "Renamed", "workflowJson": definition})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["group"] == "Operations"
    assert "group" not in renamed.json()["workflowJson"]["metadata"]
    stored = json.loads((folder / "workflow.json").read_text())
    assert "group" not in stored["metadata"]
    assert stored["metadata"]["custom-runtime"] is True
    assert json.loads((folder / "meta.json").read_text())["custom"] == {"keep": True}
    _write_workflow(user, "__hidden", name="Hidden", meta={"group": "Hidden", "hidden": True})
    for endpoint in ("/api/workflow", "/api/workflow-summaries"):
        listed = await client.get(endpoint)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == ["shared-id"]
        assert listed.json()[0]["group"] == "Operations"


@pytest.mark.asyncio
async def test_native_group_workflow_patch_does_not_materialize_or_normalize_other_metadata(client, native_group_workflows):
    _, user = native_group_workflows
    original = {
        "id": "legacy-metadata-id",
        "nameI18n": {"zh-CN": "  保留原始值  "},
        "source": "vendor-value",
        "stats": {"vendor": True},
        "custom": {"keep": [1, 2]},
    }
    _write_workflow(user, "partial-id", name="Derived name", meta=original)
    folder = user / "partial-id"
    source_before = (folder / "workflow.json").read_bytes()
    response = await client.put("/api/workflow/partial-id", json={"group": "Team"})
    assert response.status_code == 200, response.text
    saved = json.loads((folder / "meta.json").read_text())
    assert saved.pop("updatedAt") > 0
    assert saved == {**original, "group": "Team"}
    assert (folder / "workflow.json").read_bytes() == source_before
    renamed = await client.put("/api/workflow/partial-id", json={"name": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    saved = json.loads((folder / "meta.json").read_text())
    saved.pop("updatedAt")
    assert saved == {**original, "group": "Team", "name": "Renamed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Case Sensitive", "", None])
async def test_native_group_workflow_export_import_roundtrip(client, native_group_workflows, group):
    _, user = native_group_workflows
    _write_workflow(user, "export-id", name="Exported", meta={"group": group})
    before = (user / "export-id" / "workflow.json").read_bytes()
    exported = await client.get("/api/workflow/export-id/export")
    assert exported.status_code == 200, exported.text
    assert exported.json()["metadata"]["group"] == (group or "")
    assert (user / "export-id" / "workflow.json").read_bytes() == before
    imported = await client.post("/api/workflow/import", json=exported.json())
    assert imported.status_code == 201, imported.text
    data = imported.json()
    assert data["group"] == (group or "")
    assert "group" not in data["workflowJson"]["metadata"]
    folder = user / data["id"]
    assert json.loads((folder / "meta.json").read_text())["group"] == (group or "")
    assert "group" not in json.loads((folder / "workflow.json").read_text())["metadata"]
    assert (await client.get(f"/api/workflow/{data['id']}")).json()["group"] == (group or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [10, False, [], {}, "a" * 33, "a\x00b", "a\nb", "a\x7fb"])
async def test_native_group_workflow_rejects_invalid_values(client, native_group_workflows, value):
    _, user = native_group_workflows
    _write_workflow(user, "valid-id", name="Valid")
    response = await client.put("/api/workflow/valid-id", json={"group": value})
    assert response.status_code == 422
    assert not (user / "valid-id" / "meta.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["Changed", "", None])
@pytest.mark.parametrize("surface", ["top", "json", "both"])
async def test_shipped_workflow_group_guards_every_edit_surface_before_writes(
    client, native_group_workflows, monkeypatch, value, surface,
):
    from flocks.workflow import fs_store

    project, _user = native_group_workflows
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", project)
    _write_workflow(project, "system", name="System", meta={"group": "Fixed", "x-owner": {"keep": True}})
    folder = project / "system"
    (folder / "workflow.md").write_bytes(b"# Keep this body\r\n")
    before = {path: path.read_bytes() for path in folder.iterdir()}
    payload = {"name": "Must not write", "markdownContent": "Must not write"}
    if surface in {"top", "both"}:
        payload["group"] = "Fixed" if surface == "both" else value
    if surface in {"json", "both"}:
        definition = json.loads(before[folder / "workflow.json"])
        definition["metadata"] = {"group": value, "keep": True}
        payload["workflowJson"] = definition
    response = await client.put("/api/workflow/system", json=payload)
    assert response.status_code == 403, response.text
    assert {path: path.read_bytes() for path in folder.iterdir()} == before
    workflow_routes.publish_event.assert_not_called()
    workflow_routes._get_workflow_stats.assert_not_called()
    detail = (await client.get("/api/workflow/system")).json()
    assert detail["group"] == "Fixed"
    assert detail["group_readonly"] is True


@pytest.mark.asyncio
async def test_shipped_workflow_allows_unchanged_group_and_ordinary_graph_edits(client, native_group_workflows, monkeypatch):
    from flocks.workflow import fs_store

    project, _user = native_group_workflows
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", project)
    _write_workflow(project, "system", name="System", meta={"group": "Fixed", "unknown": {"keep": 1}})
    folder = project / "system"
    for echo_group in (False, True):
        definition = json.loads((folder / "workflow.json").read_text())
        definition["nodes"][0]["code"] = "outputs['edited'] = True"
        definition["metadata"] = {"unknown-runtime": {"keep": True}}
        payload = {"workflowJson": definition, "name": "Ordinary edit"}
        if echo_group:
            payload["group"] = "Fixed"
            definition["metadata"]["group"] = "Fixed"
        response = await client.put("/api/workflow/system", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["group"] == "Fixed"
        assert response.json()["group_readonly"] is True
        stored = json.loads((folder / "workflow.json").read_text())
        assert stored["nodes"][0]["code"] == "outputs['edited'] = True"
        assert stored["metadata"] == {"unknown-runtime": {"keep": True}}
        meta = json.loads((folder / "meta.json").read_text())
        assert meta["group"] == "Fixed"
        assert meta["unknown"] == {"keep": 1}
        assert "group_readonly" not in meta
    for endpoint in ("/api/workflow", "/api/workflow-summaries"):
        response = await client.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.json()[0]["group_readonly"] is True


@pytest.mark.asyncio
async def test_user_workflow_selected_over_shipped_same_id_keeps_group_writable(client, native_group_workflows, monkeypatch):
    from flocks.workflow import fs_store

    project, user = native_group_workflows
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", project)
    for root in (project, user):
        _write_workflow(root, "shared", name="Shared", meta={"group": "Original"})
    response = await client.put("/api/workflow/shared", json={"group": "Custom"})
    assert response.status_code == 200, response.text
    assert response.json()["group_readonly"] is False
    assert json.loads((project / "shared" / "meta.json").read_text())["group"] == "Original"
    assert json.loads((user / "shared" / "meta.json").read_text())["group"] == "Custom"


@pytest.mark.asyncio
async def test_metadata_update_reads_selected_graph_and_body_only_once(client, native_group_workflows, monkeypatch):
    _project, user = native_group_workflows
    _write_workflow(user, "selected", name="Selected")
    folder = user / "selected"
    (folder / "workflow.md").write_text("# Keep\n")
    read_text = Path.read_text
    reads = []
    def tracked_read(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracked_read)
    response = await client.put("/api/workflow/selected", json={"group": "Team"})
    assert response.status_code == 200, response.text
    assert reads.count(folder / "workflow.json") == 1
    assert reads.count(folder / "workflow.md") == 1
    assert "group_readonly" not in json.loads((folder / "meta.json").read_text())


@pytest.fixture
def workflow_migration_env(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from flocks.workflow.center import resolve_project_workflow_roots, resolve_global_workflow_roots

    workspace = tmp_path / "workspace"
    (workspace / ".flocks").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    roots = [(root, "project") for root in resolve_project_workflow_roots(workspace)]
    roots += [(root, "global") for root in resolve_global_workflow_roots()]
    monkeypatch.setattr(workflow_routes, "_find_workspace_root", lambda: workspace)
    monkeypatch.setattr(workflow_routes, "_all_scan_dirs", lambda: roots)
    monkeypatch.setattr(workflow_routes.Storage, "list_keys", AsyncMock(return_value=["workflow/shared"]))
    legacy = {"name": "Stale Storage", "group": "Migrated", "workflowJson": {"nodes": [], "edges": [], "start": ""}}
    monkeypatch.setattr(workflow_routes.Storage, "read", AsyncMock(return_value=legacy))
    return workspace, roots, legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("root_index", range(6))
@pytest.mark.parametrize("filename", ["workflow.json", "workflow.md", "workflow.edit.md"])
async def test_storage_migration_never_overwrites_any_discovered_definition(workflow_migration_env, root_index, filename):
    workspace, roots, _legacy = workflow_migration_env
    folder = roots[root_index][0] / "shared"
    folder.mkdir(parents=True)
    original = b'{"name":"Current","nodes":[]}' if filename.endswith(".json") else b"# Current draft\r\n"
    (folder / filename).write_bytes(original)
    (folder / "meta.json").write_bytes(b'{"group":"Current","unknown":true}')
    before = {path: path.read_bytes() for path in folder.iterdir()}
    await workflow_routes._migrate_storage_to_filesystem()
    assert {path: path.read_bytes() for path in folder.iterdir()} == before
    workflow_routes.Storage.read.assert_not_called()
    destination = workspace / ".flocks" / "plugins" / "workflows" / "shared"
    if destination != folder:
        assert not destination.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("root_index", [2, 5])
async def test_storage_migration_rechecks_after_storage_await(workflow_migration_env, monkeypatch, root_index):
    workspace, roots, legacy = workflow_migration_env
    folder = roots[root_index][0] / "shared"
    async def read_and_create(_key):
        folder.mkdir(parents=True)
        (folder / "workflow.md").write_bytes(b"# Concurrent draft\r\n")
        return legacy
    monkeypatch.setattr(workflow_routes.Storage, "read", read_and_create)
    await workflow_routes._migrate_storage_to_filesystem()
    assert (folder / "workflow.md").read_bytes() == b"# Concurrent draft\r\n"
    assert not (folder / "workflow.json").exists()
    destination = workspace / ".flocks" / "plugins" / "workflows" / "shared"
    if destination != folder:
        assert not destination.exists()


@pytest.mark.asyncio
async def test_storage_migration_create_only_destination_resists_concurrent_create(workflow_migration_env, monkeypatch):
    workspace, _roots, _legacy = workflow_migration_env
    original_write = workflow_routes._write_workflow_to_fs
    destination = workspace / ".flocks" / "plugins" / "workflows" / "shared"
    def concurrent_create(*args, **kwargs):
        assert kwargs["target_dir"] == destination
        assert kwargs["create_only"] is True
        destination.mkdir(parents=True)
        (destination / "workflow.json").write_bytes(b'{"name":"Concurrent"}')
        (destination / "meta.json").write_bytes(b'{"group":"Keep"}')
        original_write(*args, **kwargs)
    monkeypatch.setattr(workflow_routes, "_write_workflow_to_fs", concurrent_create)
    await workflow_routes._migrate_storage_to_filesystem()
    assert (destination / "workflow.json").read_bytes() == b'{"name":"Concurrent"}'
    assert (destination / "meta.json").read_bytes() == b'{"group":"Keep"}'


@pytest.mark.asyncio
async def test_create_only_migration_never_deletes_concurrently_created_legacy_draft(workflow_migration_env, monkeypatch):
    import builtins

    workspace, _roots, _legacy = workflow_migration_env
    destination = workspace / ".flocks" / "plugins" / "workflows" / "shared"
    original_open = builtins.open
    def create_legacy_draft(path, mode="r", *args, **kwargs):
        if Path(path) == destination / "workflow.json" and mode == "x":
            (destination / "workflow.edit.md").write_bytes(b"# Concurrent legacy draft\r\n")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", create_legacy_draft)
    await workflow_routes._migrate_storage_to_filesystem()
    assert (destination / "workflow.edit.md").read_bytes() == b"# Concurrent legacy draft\r\n"


@pytest.mark.asyncio
async def test_storage_migration_uses_explicit_destination_without_update_redirect(workflow_migration_env, monkeypatch):
    from unittest.mock import Mock

    workspace, _roots, legacy = workflow_migration_env
    monkeypatch.setattr(workflow_routes, "_existing_workflow_dir", Mock(side_effect=AssertionError("No update redirect")))
    await workflow_routes._migrate_storage_to_filesystem()
    destination = workspace / ".flocks" / "plugins" / "workflows" / "shared"
    assert json.loads((destination / "workflow.json").read_text()) == legacy["workflowJson"]
    assert json.loads((destination / "meta.json").read_text())["group"] == "Migrated"


@pytest.mark.asyncio
async def test_native_group_workflow_unknown_lookup_does_not_create(client, native_group_workflows):
    _, user = native_group_workflows
    response = await client.put("/api/workflow/unknown", json={"group": "Team"})
    assert response.status_code == 404
    assert not (user / "unknown").exists()
