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


def test_create_scan_request_accepts_bounded_verification_votes() -> None:
    payload = code_security.CreateScanRequest(
        workspaceId="workspace-1",
        verificationVotes=3,
    )

    assert payload.verification_votes == 3
    assert payload.copy_source is True
    assert code_security.CreateScanRequest(
        workspaceId="workspace-1",
        copySource=False,
    ).copy_source is False
    with pytest.raises(ValueError):
        code_security.CreateScanRequest(
            workspaceId="workspace-1",
            verificationVotes=6,
        )


@pytest.mark.asyncio
async def test_dynamic_scan_requires_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        code_security,
        "require_admin",
        lambda _request: SimpleNamespace(id="admin-1", role="admin"),
    )
    payload = code_security.CreateScanRequest(
        workspaceId="workspace-1",
        dynamicEnabled=True,
        dynamicConfirmed=False,
    )

    with pytest.raises(HTTPException) as raised:
        await code_security.create_scan(SimpleNamespace(), payload)

    assert raised.value.status_code == 400
    assert raised.value.detail["code"] == "dynamic_confirmation_required"


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
        lambda: (FakeService(), FakeCaller, object, FakeServiceError),
    )

    response = await code_security.delete_scan(SimpleNamespace(), "scan_demo")

    assert response.status_code == 204
    assert caller_values["subject"] == "admin-1"
    assert caller_values["is_admin"] is True
