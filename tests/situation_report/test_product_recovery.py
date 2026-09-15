from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.input.events import UserInputEvent
from flocks.session.message import MessageRole
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
    evidence = tmp_path / "work" / generation_id / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    candidate_sha = orchestrator.file_sha256(candidate)
    _write_json(
        tmp_path / "runs" / generation_id / "validation.json",
        {
            "status": "needs_revision",
            "attempt": 1,
            "candidateSHA256": candidate_sha,
            "evidenceSHA256": orchestrator.file_sha256(evidence),
            "issues": [{"code": "evidence_map", "missing": ["material-1"]}],
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
        evidence = tmp_path / "work" / generation_id / "evidence.json"
        evidence.write_text("{}", encoding="utf-8")
        candidate_sha = orchestrator.file_sha256(candidate)
        status = "needs_revision" if len(calls) == 1 else "passed"
        _write_json(
            tmp_path / "runs" / generation_id / "validation.json",
            {
                "status": status,
                "attempt": len(calls),
                "candidateSHA256": candidate_sha,
                "evidenceSHA256": orchestrator.file_sha256(evidence),
                "issues": [] if status == "passed" else [{"code": "missing"}],
            },
        )

    monkeypatch.setattr(
        orchestrator,
        "_raise_persisted_agent_error",
        AsyncMock(),
    )
    monkeypatch.setattr(orchestrator.Message, "list", AsyncMock(return_value=[]))

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


def test_recovery_rewrites_candidate_when_evidence_map_is_missing(tmp_path: Path) -> None:
    generation_id = "gen_missing_evidence"
    candidate = tmp_path / "work" / generation_id / "report.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("## report\n", encoding="utf-8")

    recovered = orchestrator._build_agent_recovery_event(
        event=_event(generation_id),
        workspace_dir=tmp_path,
        generation_id=generation_id,
        recovery_turn=1,
    )

    assert recovered is not None
    assert "internal evidence map is missing" in recovered.text
    assert f"expected_sha256={orchestrator.file_sha256(candidate)}" in recovered.text


def _assistant(message_id: str, error=None):
    return SimpleNamespace(id=message_id, role=MessageRole.ASSISTANT, error=error)


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_error", [{"name": "APIError", "message": "HTTP 400"}, "connection failed"])
async def test_new_task_can_recover_despite_historical_model_error(tmp_path, monkeypatch, historical_error):
    """Deterministic orchestration regression, not a live model quality test."""
    history = [_assistant("msg_old_error", historical_error)]
    list_messages = AsyncMock(side_effect=lambda *args, **kwargs: list(history))
    monkeypatch.setattr(orchestrator.Message, "list", list_messages)
    event = _event("gen_retry")
    recovery = orchestrator._build_agent_recovery_event(
        event=event, workspace_dir=tmp_path, generation_id="gen_retry", recovery_turn=1,
    )
    monkeypatch.setattr(
        orchestrator, "_build_agent_recovery_event",
        lambda **kwargs: recovery if len(history) == 2 else None,
    )
    calls = []

    async def runner(*args):
        calls.append(args[2])
        history.append(_assistant(f"msg_current_{len(calls)}"))

    on_recovery = AsyncMock()
    await orchestrator._run_agent_until_candidate_ready(
        session=SimpleNamespace(id="ses_recovery"), event=event,
        generation_id="gen_retry", workspace_dir=tmp_path,
        working_directory=str(tmp_path), generic_runner=runner, on_recovery=on_recovery,
    )
    assert len(calls) == 2
    assert calls[1].synthetic is True
    assert history[0].error == historical_error  # Do not erase history to permit retry.
    on_recovery.assert_awaited_once_with(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on_call", [1, 2])
async def test_new_model_error_still_fails_in_initial_or_recovery_turn(tmp_path, monkeypatch, fail_on_call):
    error = {"name": "APIError", "message": "HTTP 400"}
    history = [_assistant("msg_old_error", error)]
    monkeypatch.setattr(orchestrator.Message, "list", AsyncMock(side_effect=lambda *args, **kwargs: list(history)))
    calls = []

    async def runner(*args):
        calls.append(args[2])
        if len(calls) == fail_on_call:
            # Same error text but a new message must not be mistaken for history.
            history.append(_assistant("msg_current_error", dict(error)))
            history.append(_assistant("msg_after_error"))

    on_recovery = AsyncMock()
    with pytest.raises(orchestrator.ProductAgentExecutionError, match="APIError: HTTP 400"):
        await orchestrator._run_agent_until_candidate_ready(
            session=SimpleNamespace(id="ses_recovery"), event=_event("gen_current"),
            generation_id="gen_current", workspace_dir=tmp_path,
            working_directory=str(tmp_path), generic_runner=runner, on_recovery=on_recovery,
        )
    assert len(calls) == fail_on_call
    assert on_recovery.await_count == fail_on_call - 1
    assert any(m.id == "msg_current_error" for m in history)
