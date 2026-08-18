from __future__ import annotations

from pathlib import Path

import pytest

from flocks.tool.registry import ToolContext, ToolResult

import flocks_code_security.cli as audit_cli


def _result(output: dict) -> ToolResult:
    return ToolResult(success=True, output=output)


@pytest.mark.asyncio
async def test_pipeline_runs_all_required_phases_and_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases: list[str] = []
    status_outputs = iter(
        [
            {
                "scan_id": "scan_test",
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 0},
            },
            {
                "scan_id": "scan_test",
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 2},
            },
            {
                "scan_id": "scan_test",
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 0},
            },
        ]
    )

    async def prepare(_ctx, target_path: str) -> ToolResult:
        assert target_path == "/target"
        return _result({"scan_id": "scan_test", "snapshot": {"file_count": 4}})

    async def run_workers(_ctx, scan_id: str, phase: str) -> ToolResult:
        assert scan_id == "scan_test"
        phases.append(phase)
        return _result(
            {
                "scan_id": scan_id,
                "batch_id": f"batch_{phase}",
                "phase": phase,
                "status": "running",
                "launched_workers": 1,
            }
        )

    async def wait_workers(
        _ctx, batch_id: str, timeout_seconds: int
    ) -> ToolResult:
        assert timeout_seconds == 10
        return _result(
            {
                "batch_id": batch_id,
                "phase": batch_id.removeprefix("batch_"),
                "status": "completed",
                "status_counts": {"completed": 1},
            }
        )

    async def status(_ctx, scan_id: str) -> ToolResult:
        assert scan_id == "scan_test"
        return _result(next(status_outputs))

    async def finalize(_ctx, scan_id: str) -> ToolResult:
        return _result(
            {
                "scan_id": scan_id,
                "status": "completed",
                "finding_count": 2,
                "report_path": "/output/report.md",
            }
        )

    async def unexpected_cancel(_ctx, _scan_id: str) -> ToolResult:
        pytest.fail("successful pipeline must not be cancelled")

    monkeypatch.setattr(audit_cli, "audit_prepare", prepare)
    monkeypatch.setattr(audit_cli, "audit_run_workers", run_workers)
    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(audit_cli, "audit_finalize", finalize)
    monkeypatch.setattr(audit_cli, "audit_cancel", unexpected_cancel)

    events: list[tuple[str, dict]] = []
    result = await audit_cli._run_pipeline(
        ToolContext("session", "message", agent="code-security"),
        Path("/target"),
        lambda event, payload: events.append((event, payload)),
    )

    assert phases == ["threat_modeling", "baseline", "verification"]
    assert result["report_path"] == "/output/report.md"
    assert [event for event, _payload in events] == [
        "scan.prepared",
        "batch.started",
        "batch.status",
        "scan.status",
        "batch.started",
        "batch.status",
        "scan.status",
        "batch.started",
        "batch.status",
        "scan.status",
        "scan.finalized",
    ]


@pytest.mark.asyncio
async def test_pipeline_cancels_when_verification_makes_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter(
        [
            {"threat_model_status": "completed", "counts": {}},
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 1},
            },
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 1},
            },
        ]
    )
    cancelled: list[str] = []

    async def prepare(_ctx, _target_path: str) -> ToolResult:
        return _result({"scan_id": "scan_stalled"})

    async def run_workers(_ctx, _scan_id: str, phase: str) -> ToolResult:
        return _result({"batch_id": f"batch_{phase}", "phase": phase})

    async def wait_workers(_ctx, batch_id: str, timeout_seconds: int) -> ToolResult:
        return _result({"batch_id": batch_id, "status": "completed"})

    async def status(_ctx, _scan_id: str) -> ToolResult:
        return _result(next(statuses))

    async def cancel(_ctx, scan_id: str) -> ToolResult:
        cancelled.append(scan_id)
        return _result({"scan_id": scan_id, "status": "cancelled"})

    monkeypatch.setattr(audit_cli, "audit_prepare", prepare)
    monkeypatch.setattr(audit_cli, "audit_run_workers", run_workers)
    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(audit_cli, "audit_cancel", cancel)

    events: list[str] = []
    with pytest.raises(RuntimeError, match="made no progress"):
        await audit_cli._run_pipeline(
            ToolContext("session", "message", agent="code-security"),
            Path("/target"),
            lambda event, _payload: events.append(event),
        )

    assert cancelled == ["scan_stalled"]
    assert events[-1] == "scan.cancelled"
