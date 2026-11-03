import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from flocks.server.routes import workflow as routes
from flocks.workflow import fs_store
from flocks.workflow.definition_io import write_definition


@pytest.fixture
def workflows(tmp_path, monkeypatch):
    root = tmp_path.resolve() / ".flocks" / "plugins" / "workflows"
    folder = root / "broken"
    folder.mkdir(parents=True)
    path = folder / "workflow.json"
    graph = {"name": "Example", "start": "n1", "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}], "edges": []}
    write_definition(path, json.dumps(graph), create_only=True)
    path.write_text('{"name": "broken"\n')
    monkeypatch.setattr(fs_store, "workflow_scan_dirs", lambda: [(root, "project")])
    monkeypatch.setattr(routes, "_all_scan_dirs", lambda: [(root, "project")])
    monkeypatch.setattr(routes, "_migrate_storage_to_filesystem", AsyncMock())
    monkeypatch.setattr(routes, "_get_workflow_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "publish_event", AsyncMock())
    monkeypatch.setattr(routes, "_get_workflow_integration_status", AsyncMock(return_value={
        "api": {"configured": False, "state": "unconfigured"},
        "trigger": {"configured": False, "state": "unconfigured"},
    }))
    return folder


async def test_broken_definition_stays_in_list_and_details(workflows):
    detail = await routes.get_workflow("broken")
    assert detail.definitionError["file"] == "workflow.json"
    assert detail.definitionError["line"] == 2
    assert detail.definitionError["canRestore"] is True
    summaries = await routes.list_workflow_summaries(category=None, status=None, exclude_id=None)
    assert summaries[0].id == "broken"
    assert summaries[0].definitionError == detail.definitionError
    listed = await routes.list_workflows(category=None, status=None, exclude_id=None)
    assert listed[0].definitionError == detail.definitionError


async def test_execution_rejects_invalid_definition_with_precise_error(workflows):
    with pytest.raises(HTTPException) as error:
        await routes.run_workflow_endpoint("broken", routes.WorkflowRunRequest())
    assert error.value.status_code == 422
    assert "workflow.json" in error.value.detail and "line 2" in error.value.detail


async def test_missing_workflow_remains_404(workflows):
    with pytest.raises(HTTPException) as error:
        await routes.get_workflow("missing")
    assert error.value.status_code == 404


async def test_explicit_restore_recovers_original_definition(workflows):
    result = await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    assert result.definitionError is None
    assert result.workflowJson["name"] == "Example"
    assert json.loads((workflows / "workflow.json.last-valid").read_text())["name"] == "Example"


async def test_restore_does_not_accept_a_corrupt_backup(workflows):
    (workflows / "workflow.json.last-valid").write_text("bad")
    before = (workflows / "workflow.json").read_bytes()
    with pytest.raises(HTTPException) as error:
        await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    assert error.value.status_code == 422
    assert (workflows / "workflow.json").read_bytes() == before


async def test_metadata_corruption_identifies_the_right_file_and_can_restore(workflows):
    await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    write_definition(workflows / "meta.json", '{"name":"Example","group":"Team"}', create_only=True)
    (workflows / "meta.json").write_text("[]")
    detail = await routes.get_workflow("broken")
    assert detail.definitionError["file"] == "meta.json"
    recovered = await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest(file="meta.json"))
    assert recovered.definitionError is None and recovered.group == "Team"


async def test_bad_user_override_never_falls_back_to_bundled_definition(workflows, monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    folder = bundled / "broken"
    folder.mkdir(parents=True)
    (folder / "workflow.json").write_text('{"name":"Bundled","start":"n1","nodes":[],"edges":[]}')
    monkeypatch.setattr(fs_store, "workflow_scan_dirs", lambda: [(bundled, "project"), (workflows.parent, "global")])
    assert fs_store.read_workflow_from_fs("broken") is None
    detail = await routes.get_workflow("broken")
    assert detail.source == "global" and detail.definitionError is not None


async def test_hidden_broken_workflow_is_not_exposed_in_lists(workflows):
    (workflows / "meta.json").write_text('{"hidden": true}')
    assert await routes.list_workflows(category=None, status=None, exclude_id=None) == []


async def test_full_definition_update_can_repair_broken_json(workflows):
    graph = json.loads((workflows / "workflow.json.last-valid").read_text())
    result = await routes.update_workflow("broken", routes.WorkflowUpdateRequest(workflowJson=graph))
    assert result.definitionError is None
    assert (await routes.get_workflow("broken")).definitionError is None


async def test_managed_workflow_cannot_be_restored(workflows, monkeypatch):
    monkeypatch.setattr(routes, "is_managed_workflow", lambda _id: True)
    with pytest.raises(HTTPException) as error:
        await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    assert error.value.status_code == 403


async def test_restore_cannot_overwrite_a_definition_repaired_since_page_load(workflows):
    graph = json.loads((workflows / "workflow.json.last-valid").read_text())
    graph["name"] = "Newer valid repair"
    write_definition(workflows / "workflow.json", json.dumps(graph))
    before = (workflows / "workflow.json").read_bytes()
    with pytest.raises(HTTPException) as error:
        await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    assert error.value.status_code == 409
    assert (workflows / "workflow.json").read_bytes() == before


async def test_symlink_backup_is_rejected_without_changing_target(workflows, tmp_path):
    backup = workflows / "workflow.json.last-valid"
    backup.unlink()
    outside = tmp_path / "private.json"
    outside.write_text("private")
    try:
        backup.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    before = (workflows / "workflow.json").read_bytes()
    with pytest.raises(HTTPException) as error:
        await routes.restore_workflow_definition("broken", routes.WorkflowRestoreRequest())
    assert error.value.status_code == 409
    assert outside.read_text() == "private"
    assert (workflows / "workflow.json").read_bytes() == before
