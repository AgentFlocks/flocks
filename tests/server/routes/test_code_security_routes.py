from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from flocks.server.routes import code_security


def test_web_detail_uses_the_documented_browser_dto() -> None:
    detail = {
        "schema_version": "flocks.code-security.tool.v1",
        "scan": {
            "scan_id": "scan_demo",
            "started_at": "2026-08-21T00:00:00+00:00",
            "finished_at": None,
            "elapsed_ms": 1200,
            "latest_event_seq": 7,
        },
        "target": {"display_name": "demo"},
        "counts": {"candidates": 1},
        "finding_summary": {"total": 0},
        "coverage_summary": {"completeness": "partial"},
        "dynamic_validation": {"status": "skipped"},
        "phase_runs": [{"phase_run_id": "phase_1"}],
        "workers": [{"work_unit_id": "unit_1"}],
        "artifacts": [{"kind": "snapshot_summary"}],
        "server_time": "2026-08-21T00:00:01+00:00",
        "workspace_url": "/contracts/webui/workspaces/code_security/code-security-workspace",
    }

    payload = code_security._web_detail(detail)

    assert payload["schemaVersion"] == "flocks.code-security.tool.v1"
    assert payload["phaseRuns"] == detail["phase_runs"]
    assert payload["workers"] == detail["workers"]
    assert payload["latestEventSeq"] == 7
    assert payload["timing"]["elapsedMs"] == 1200
    assert "phase_runs" not in payload
    assert "server_time" not in payload


def test_resolve_target_rejects_paths_and_symlinks_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as traversal:
        code_security._resolve_target(workspace, "../outside")
    assert traversal.value.status_code == 400

    with pytest.raises(HTTPException) as symlink_escape:
        code_security._resolve_target(workspace, "escape")
    assert symlink_escape.value.status_code == 400


def test_create_scan_request_has_no_vote_configuration() -> None:
    payload = code_security.CreateScanRequest(
        workspaceId="workspace-1",
    )

    assert "verification_votes" not in type(payload).model_fields
    assert "poc_enabled" not in type(payload).model_fields
    assert "pocEnabled" not in code_security.CreateScanRequest(
        workspaceId="workspace-1", pocEnabled=False,
    ).model_dump(by_alias=True)
    assert payload.copy_source is True
    assert payload.max_file_bytes is None
    assert code_security.CreateScanRequest(
        workspaceId="workspace-1",
        copySource=False,
    ).copy_source is False
    large_file_request = code_security.CreateScanRequest(
        workspaceId="workspace-1",
        maxFileBytes=64 * 1024 * 1024,
        maxTotalBytes=8 * 1024**3,
    )
    assert large_file_request.max_file_bytes == 64 * 1024 * 1024
    assert large_file_request.max_total_bytes == 8 * 1024**3
    with pytest.raises(ValueError):
        code_security.CreateScanRequest(workspaceId="workspace-1", maxTotalBytes=0)
    legacy = code_security.CreateScanRequest(workspaceId="workspace-1", verificationVotes=6)
    assert "verificationVotes" not in legacy.model_dump(by_alias=True)




@pytest.mark.asyncio
async def test_delete_scan_requires_admin_and_returns_no_content(monkeypatch) -> None:
    caller_values = {}

    class FakeCaller:
        def __init__(self, **values):
            caller_values.update(values)

    class FakeServiceError(Exception):
        pass

    class FakeService:
        async def delete_scan(self, scan_id, _caller):
            assert scan_id == "scan_demo"

    monkeypatch.setattr(
        code_security,
        "require_admin",
        lambda _request: SimpleNamespace(id="admin-1", role="admin"),
    )
    monkeypatch.setattr(
        code_security,
        "_service_types",
        lambda _request=None: (FakeService(), FakeCaller, object, FakeServiceError),
    )

    response = await code_security.delete_scan(SimpleNamespace(), "scan_demo")

    assert response.status_code == 204
    assert caller_values["subject"] == "admin-1"
    assert caller_values["is_admin"] is True


def test_cleanup_request_flag_is_strict_and_defaults_off() -> None:
    from pydantic import ValidationError

    assert code_security.CreateScanRequest(workspaceId="workspace").cleanup_intermediates is False
    assert code_security.CreateScanRequest(workspaceId="workspace", cleanupIntermediates=True).cleanup_intermediates is True
    with pytest.raises(ValidationError):
        code_security.CreateScanRequest(workspaceId="workspace", cleanupIntermediates="false")


@pytest.mark.asyncio
async def test_multipart_zip_import_roundtrip(tmp_path, monkeypatch):
    import io
    import zipfile
    from unittest.mock import AsyncMock
    import httpx
    from fastapi import FastAPI
    from flocks.project import source_import

    monkeypatch.setattr(code_security, 'require_admin', lambda request: SimpleNamespace(id='owner'))
    monkeypatch.setattr(source_import.WorkspaceManager, 'get_instance', lambda: SimpleNamespace(get_workspace_dir=lambda: tmp_path))
    monkeypatch.setenv('FLOCKS_PROJECT_ROOTS', str(tmp_path))
    create = AsyncMock(side_effect=lambda **kwargs: {'id': 'imported', **kwargs})
    monkeypatch.setattr(source_import.Project, 'create', create)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('demo/main.py', 'print(1)')
    app = FastAPI()
    app.include_router(code_security.router)
    route = next(route.path for route in app.routes if route.name == 'import_source_project')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(route, data={'kind': 'zip', 'name': 'Demo'}, files={'file': ('demo.zip', data.getvalue(), 'application/zip')})
    assert response.status_code == 200, response.text
    from pathlib import Path
    root = Path(response.json()['worktree'])
    assert root.is_relative_to(tmp_path / 'code-audit/imports')
    assert (root / 'main.py').read_text() == 'print(1)'
    assert (root.parent.parent / 'archive/source.zip').read_bytes() == data.getvalue()
    assert response.json()['owner_id'] == 'owner'


@pytest.mark.asyncio
async def test_scan_list_excludes_deleted_projects_and_refills_page(monkeypatch):
    from unittest.mock import AsyncMock
    user = SimpleNamespace(id='owner')
    service = SimpleNamespace(list_scans=AsyncMock(side_effect=[
        {'items': [{'scan_id': 'removed', 'workspace_ref': 'deleted'}], 'next_cursor': 'next'},
        {'items': [{'scan_id': 'kept', 'workspace_ref': 'active'}], 'next_cursor': None},
    ]))
    monkeypatch.setattr(code_security, 'require_user', lambda request: user)
    monkeypatch.setattr(code_security, '_service_types', lambda request: (service, object, object, ValueError))
    monkeypatch.setattr(code_security, '_caller', lambda *args, **kwargs: user)
    monkeypatch.setattr(code_security.Project, 'removed_project_ids', lambda: {'deleted'})
    result = await code_security.list_scans(SimpleNamespace(path_params={}), status_filter=None, cursor=None, limit=1)
    assert result == {'items': [{'scan_id': 'kept', 'workspace_ref': 'active'}], 'next_cursor': None}
    assert service.list_scans.await_args_list[1].kwargs['cursor'] == 'next'


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_project_purge_only_removes_registration_after_cleanup(monkeypatch, fails):
    from unittest.mock import AsyncMock
    user = SimpleNamespace(id='owner')
    item = SimpleNamespace(id='project', worktree='/source')
    purge = AsyncMock()
    cleanup = AsyncMock(side_effect=ValueError('busy') if fails else None)
    monkeypatch.setattr(code_security, 'require_admin', lambda request: user)
    monkeypatch.setattr(code_security.Project, 'get', AsyncMock(return_value=item))
    monkeypatch.setattr(code_security.Project, 'audit_history_project_ids', lambda *args: {'project', 'old'})
    monkeypatch.setattr(code_security.Project, 'purge_registrations', purge)
    monkeypatch.setattr(code_security, '_service_types', lambda request: (SimpleNamespace(delete_project_audits=cleanup), object, object, ValueError))
    monkeypatch.setattr(code_security, '_caller', lambda *args, **kwargs: user)
    monkeypatch.setattr(code_security, '_map_service_error', lambda exc, error_type: HTTPException(409, detail=str(exc)))
    if fails:
        with pytest.raises(HTTPException):
            await code_security.delete_audit_project(SimpleNamespace(), 'project')
        purge.assert_not_awaited()
    else:
        await code_security.delete_audit_project(SimpleNamespace(), 'project')
        cleanup.assert_awaited_once_with({'project', 'old'}, user)
        purge.assert_awaited_once_with('owner', {'project', 'old'})


@pytest.mark.asyncio
async def test_docx_download_has_word_media_type_and_attachment_name(monkeypatch):
    from unittest.mock import AsyncMock
    user = SimpleNamespace(id="owner")
    service = SimpleNamespace(download_artifact=AsyncMock(return_value=("report.docx", b"word document")))
    monkeypatch.setattr(code_security, "require_user", lambda request: user)
    monkeypatch.setattr(code_security, "_service_types", lambda request: (service, object, object, ValueError))
    monkeypatch.setattr(code_security, "_caller", lambda *args, **kwargs: user)
    response = await code_security.download_artifact(SimpleNamespace(), "scan", "report.docx")
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response.headers["content-disposition"] == 'attachment; filename="report.docx"'
    service.download_artifact.assert_awaited_once_with("scan", "report.docx", user)


@pytest.mark.parametrize("field", [
    "dynamicEnabled", "dynamic_enabled", "dynamicConfirmed", "dynamic_confirmed",
    "cybergymManifest", "cybergym_manifest",
])
def test_removed_dynamic_launch_fields_are_rejected(field):
    with pytest.raises(ValueError, match="not supported on this branch"):
        code_security.CreateScanRequest(workspaceId="workspace-1", **{field: True})


def test_cybergym_scan_mode_is_rejected():
    with pytest.raises(ValueError):
        code_security.CreateScanRequest(workspaceId="workspace-1", scanMode="cybergym")
