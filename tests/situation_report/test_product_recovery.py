from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.input.events import UserInputEvent
from flocks.situation_report.product import orchestrator


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _event(generation_id: str) -> UserInputEvent:
    return UserInputEvent(
        source_type="webui",
        sessionID="ses_recovery",
        text="initial",
        parts=[{"type": "text", "text": "initial"}],
        agent=orchestrator.PRODUCTION_AGENT,
        metadata={
            "situationReport": {
                "generationID": generation_id,
                "operation": "generate",
            }
        },
    )


def test_recovery_event_requires_immediate_sha_guarded_repair(tmp_path: Path) -> None:
    generation_id = "gen_recovery"
    candidate = tmp_path / "work" / generation_id / "report.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("# report\n", encoding="utf-8")
    candidate_sha = orchestrator.file_sha256(candidate)
    _write_json(
        tmp_path / "runs" / generation_id / "validation.json",
        {
            "status": "needs_revision",
            "attempt": 1,
            "candidateSHA256": candidate_sha,
            "issues": [{"code": "material_evidence", "missing": ["material-1"]}],
        },
    )

    recovered = orchestrator._build_agent_recovery_event(
        event=_event(generation_id),
        workspace_dir=tmp_path,
        generation_id=generation_id,
        recovery_turn=1,
    )

    assert recovered is not None
    assert f"expected_sha256={candidate_sha}" in recovered.text
    assert "material-1" in recovered.text
    assert "Do not answer with a plan or promise" in recovered.text
    assert recovered.message_id is None
    assert recovered.synthetic is True
    assert _event(generation_id).synthetic is False


@pytest.mark.asyncio
async def test_agent_runner_continues_after_needs_revision(tmp_path: Path, monkeypatch) -> None:
    generation_id = "gen_recovery"
    session = SimpleNamespace(id="ses_recovery")
    initial_event = _event(generation_id)
    calls: list[UserInputEvent] = []
    recovery_status = AsyncMock()

    async def generic_runner(_session_id, _session, event, _working_directory):
        calls.append(event)
        candidate = tmp_path / "work" / generation_id / "report.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("# report\n", encoding="utf-8")
        candidate_sha = orchestrator.file_sha256(candidate)
        status = "needs_revision" if len(calls) == 1 else "passed"
        _write_json(
            tmp_path / "runs" / generation_id / "validation.json",
            {
                "status": status,
                "attempt": len(calls),
                "candidateSHA256": candidate_sha,
                "issues": [] if status == "passed" else [{"code": "missing"}],
            },
        )

    monkeypatch.setattr(
        orchestrator,
        "_raise_persisted_agent_error",
        AsyncMock(),
    )

    await orchestrator._run_agent_until_candidate_ready(
        session=session,
        event=initial_event,
        generation_id=generation_id,
        workspace_dir=tmp_path,
        working_directory=str(tmp_path),
        generic_runner=generic_runner,
        on_recovery=recovery_status,
    )

    assert len(calls) == 2
    assert calls[1].text.startswith("[SITUATION_REPORT_PRODUCT_RECOVERY_V1]")
    assert calls[1].synthetic is True
    recovery_status.assert_awaited_once_with(1)
