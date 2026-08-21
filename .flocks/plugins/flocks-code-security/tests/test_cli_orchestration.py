from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult

import flocks_code_security.cli as audit_cli
from flocks_code_security.tools import register_tools


def _result(output: dict) -> ToolResult:
    return ToolResult(success=True, output=output)


def test_cli_preflight_rejects_disabled_required_tool() -> None:
    register_tools()
    audit_read = ToolRegistry.get("audit_read")
    original_enabled = audit_read.info.enabled
    audit_read.info.enabled = False

    try:
        with pytest.raises(RuntimeError, match="audit_read"):
            audit_cli._require_enabled_audit_tools()
    finally:
        audit_read.info.enabled = original_enabled


def test_static_cli_preflight_does_not_require_dynamic_only_tools() -> None:
    register_tools()
    submit_probe = ToolRegistry.get("audit_submit_probe")
    original_enabled = submit_probe.info.enabled
    submit_probe.info.enabled = False

    try:
        audit_cli._require_enabled_audit_tools()
        with pytest.raises(RuntimeError, match="audit_submit_probe"):
            audit_cli._require_enabled_audit_tools(dynamic_enabled=True)
    finally:
        submit_probe.info.enabled = original_enabled


def test_static_cli_preflight_requires_knowledge_tool_only_for_guided_audits() -> None:
    register_tools()
    knowledge_base = ToolRegistry.get("audit_knowledge_base")
    original_enabled = knowledge_base.info.enabled
    knowledge_base.info.enabled = False

    try:
        audit_cli._require_enabled_audit_tools()
        with pytest.raises(RuntimeError, match="audit_knowledge_base"):
            audit_cli._require_enabled_audit_tools(knowledge_base_enabled=True)
    finally:
        knowledge_base.info.enabled = original_enabled


async def _final_adjudication(
    orchestrator: audit_cli.AuditOrchestrator,
    scan_id: str,
    scan_observation,
) -> dict:
    decision = {
        "scan_id": scan_id,
        "adjudication_round": 1,
        "action": "finalize",
        "accepted_candidate_ids": [],
        "rejected_candidates": [],
        "rescan": None,
    }
    audit_cli._emit(
        orchestrator.progress,
        "scan.adjudicated",
        decision,
        observation_parent=scan_observation,
    )
    return decision


@pytest.mark.asyncio
async def test_pipeline_runs_all_required_phases_and_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_cli, "langfuse_is_active", lambda: False)
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

    async def wait_workers(_ctx, batch_id: str, timeout_seconds: int) -> ToolResult:
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
    monkeypatch.setattr(
        audit_cli.AuditOrchestrator,
        "_run_parent_adjudication",
        _final_adjudication,
    )

    events: list[tuple[str, dict]] = []
    result = await audit_cli.AuditOrchestrator(
        ToolContext("session", "message", agent="code-security"),
        Path("/target"),
        lambda event, payload: events.append((event, payload)),
    ).run()

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
        "scan.adjudicated",
        "scan.finalized",
    ]


@pytest.mark.asyncio
async def test_dynamic_pipeline_probes_and_runs_before_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_cli, "langfuse_is_active", lambda: False)
    phases: list[str] = []
    statuses = iter(
        [
            {"threat_model_status": "completed", "counts": {}},
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 1},
            },
            {
                "threat_model_status": "completed",
                "counts": {
                    "unverified_candidates": 0,
                    "confirmed_without_dynamic_record": 1,
                },
            },
            {
                "threat_model_status": "completed",
                "counts": {"confirmed_without_dynamic_record": 0},
            },
            {
                "threat_model_status": "completed",
                "counts": {"terminal_dynamic_runs": 1},
            },
        ]
    )

    async def prepare(_ctx, _target_path: str, *, dynamic_enabled: bool):
        assert dynamic_enabled is True
        return _result({"scan_id": "scan_dynamic"})

    async def run_workers(_ctx, _scan_id: str, phase: str):
        phases.append(phase)
        return _result({"batch_id": f"batch_{phase}", "phase": phase})

    async def wait_workers(_ctx, batch_id: str, timeout_seconds: int):
        return _result(
            {
                "batch_id": batch_id,
                "phase": batch_id.removeprefix("batch_"),
                "status": "completed",
            }
        )

    async def status(_ctx, _scan_id: str):
        return _result(next(statuses))

    async def finalize(_ctx, scan_id: str):
        return _result({"scan_id": scan_id, "status": "completed"})

    class Store:
        def list_dynamic_runs(self, scan_id: str, *, status: str):
            assert (scan_id, status) == ("scan_dynamic", "ready")
            return [{"candidate_id": "cand_test", "status": "ready"}]

        def assert_dynamic_runs_terminal(self, scan_id: str):
            assert scan_id == "scan_dynamic"

    class Runner:
        def __init__(self) -> None:
            self.preflight_called = False
            self.runs = []

        async def preflight(self, *, observation_parent=None):
            del observation_parent
            self.preflight_called = True

        async def run_all(self, runs, *, concurrency: int, observation_parent=None):
            del observation_parent
            assert concurrency == 2
            self.runs = runs

    runner = Runner()
    monkeypatch.setattr(audit_cli, "audit_prepare", prepare)
    monkeypatch.setattr(audit_cli, "audit_run_workers", run_workers)
    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(audit_cli, "audit_finalize", finalize)
    monkeypatch.setattr(
        audit_cli,
        "get_runtime",
        lambda: SimpleNamespace(store=Store()),
    )
    monkeypatch.setattr(
        audit_cli.AuditOrchestrator,
        "_run_parent_adjudication",
        _final_adjudication,
    )

    result = await audit_cli.AuditOrchestrator(
        ToolContext("session", "message", agent="code-security"),
        Path("/target"),
        None,
        dynamic_enabled=True,
        dynamic_runner=runner,
    ).run()

    assert phases == ["threat_modeling", "baseline", "verification", "probing"]
    assert runner.preflight_called is True
    assert runner.runs == [{"candidate_id": "cand_test", "status": "ready"}]
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_dynamic_pipeline_attaches_runner_to_langfuse_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[tuple[dict, object]] = []
    ended: list[tuple[object, dict]] = []

    class Scope:
        def __init__(self) -> None:
            self.observation = object()

        def end(self, **kwargs) -> None:
            ended.append((self.observation, kwargs))

    def start_span(**kwargs):
        scope = Scope()
        spans.append((kwargs, scope.observation))
        return scope

    class Store:
        def list_dynamic_runs(self, scan_id: str, *, status: str):
            assert (scan_id, status) == ("scan_dynamic_trace", "ready")
            return [{"candidate_id": "cand_trace", "status": "ready"}]

        def assert_dynamic_runs_terminal(self, scan_id: str) -> None:
            assert scan_id == "scan_dynamic_trace"

    class Runner:
        def __init__(self) -> None:
            self.observation_parents = []

        async def preflight(self, *, observation_parent=None) -> None:
            assert observation_parent is not None
            self.observation_parents.append(observation_parent)

        async def run_all(
            self,
            runs,
            *,
            concurrency: int,
            observation_parent=None,
        ) -> None:
            assert runs == [{"candidate_id": "cand_trace", "status": "ready"}]
            assert concurrency == 2
            self.observation_parents.append(observation_parent)

    async def status(_ctx, scan_id: str) -> ToolResult:
        assert scan_id == "scan_dynamic_trace"
        return _result(
            {
                "scan_id": scan_id,
                "counts": {"terminal_dynamic_runs": 1},
            }
        )

    monkeypatch.setattr(audit_cli, "span_scope", start_span)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(
        audit_cli,
        "get_runtime",
        lambda: SimpleNamespace(store=Store()),
    )

    root_observation = object()
    runner = Runner()
    result = await audit_cli.AuditOrchestrator(
        ToolContext("session", "message", agent="code-security"),
        Path("/target"),
        None,
        dynamic_enabled=True,
        dynamic_runner=runner,
    )._run_dynamic_remaining(
        "scan_dynamic_trace",
        {"counts": {"confirmed_without_dynamic_record": 0}},
        root_observation,
    )

    dynamic_span = next(item for item in spans if item[0]["name"] == "code-security.phase.dynamic_validation")
    assert dynamic_span[0]["parent"] is root_observation
    assert runner.observation_parents == [dynamic_span[1], dynamic_span[1]]
    assert result["counts"]["terminal_dynamic_runs"] == 1
    assert any(
        observation is dynamic_span[1] and output["output"]["status"] == "completed" for observation, output in ended
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_one_parent_directed_rescan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_cli, "langfuse_is_active", lambda: False)
    phases: list[str] = []
    statuses = iter(
        [
            {"threat_model_status": "completed", "counts": {}},
            {"threat_model_status": "completed", "counts": {}},
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 1},
            },
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 0},
            },
        ]
    )

    async def prepare(_ctx, _target_path: str):
        return _result({"scan_id": "scan_rescan"})

    async def run_workers(_ctx, _scan_id: str, phase: str):
        phases.append(phase)
        return _result({"batch_id": f"batch_{phase}", "phase": phase})

    async def wait_workers(_ctx, batch_id: str, timeout_seconds: int):
        return _result({"batch_id": batch_id, "status": "completed"})

    async def status(_ctx, _scan_id: str):
        return _result(next(statuses))

    async def finalize(_ctx, scan_id: str):
        return _result({"scan_id": scan_id, "status": "completed"})

    decisions = iter(
        [
            {
                "adjudication_round": 1,
                "action": "targeted_rescan",
                "rescan": {
                    "reason": "Resolve one hypothesis.",
                    "paths": ["app.py"],
                    "questions": ["Is this path attacker reachable?"],
                },
            },
            {
                "adjudication_round": 2,
                "action": "finalize",
                "rescan": None,
            },
        ]
    )

    async def adjudicate(self, scan_id: str, scan_observation):
        decision = {"scan_id": scan_id, **next(decisions)}
        audit_cli._emit(
            self.progress,
            "scan.adjudicated",
            decision,
            observation_parent=scan_observation,
        )
        return decision

    monkeypatch.setattr(audit_cli, "audit_prepare", prepare)
    monkeypatch.setattr(audit_cli, "audit_run_workers", run_workers)
    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(audit_cli, "audit_finalize", finalize)
    monkeypatch.setattr(
        audit_cli.AuditOrchestrator,
        "_run_parent_adjudication",
        adjudicate,
    )

    result = await audit_cli.AuditOrchestrator(
        ToolContext("session", "message", agent="code-security"),
        Path("/target"),
        None,
    ).run()

    assert result["status"] == "completed"
    assert phases == [
        "threat_modeling",
        "baseline",
        "targeted_rescan",
        "verification",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("knowledge_base", "expected_tools"),
    [
        (
            None,
            {
                "audit_adjudication_context",
                "audit_submit_adjudication",
            },
        ),
        (
            {"sha256": "a" * 64},
            {
                "audit_knowledge_base",
                "audit_adjudication_context",
                "audit_submit_adjudication",
            },
        ),
    ],
)
async def test_orchestrator_invokes_primary_agent_only_for_adjudication(
    monkeypatch: pytest.MonkeyPatch,
    knowledge_base: dict | None,
    expected_tools: set[str],
) -> None:
    decision = {
        "scan_id": "scan_parent",
        "adjudication_round": 1,
        "action": "finalize",
        "accepted_candidate_ids": [],
        "rejected_candidates": [],
        "rescan": None,
    }

    class _Store:
        calls = 0

        @classmethod
        def get_latest_adjudication(cls, _scan_id: str):
            cls.calls += 1
            return None if cls.calls == 1 else decision

        @staticmethod
        def get_knowledge_base_metadata(_scan_id: str):
            return knowledge_base

    create_message = AsyncMock()
    run_loop = AsyncMock(return_value=SimpleNamespace(action="stop", error=None))
    set_callable_tools = AsyncMock()
    monkeypatch.setattr(
        audit_cli,
        "get_runtime",
        lambda: SimpleNamespace(store=_Store()),
    )
    monkeypatch.setattr(audit_cli.Message, "create", create_message)
    monkeypatch.setattr(audit_cli.SessionLoop, "run", run_loop)
    monkeypatch.setattr(
        audit_cli,
        "set_session_callable_tools",
        set_callable_tools,
    )
    events: list[str] = []
    ctx = ToolContext(
        "coordinator",
        "message",
        agent="code-security",
        extra={"model": {"providerID": "provider", "modelID": "model"}},
    )

    result = await audit_cli.AuditOrchestrator(
        ctx,
        Path("/target"),
        lambda event, _payload: events.append(event),
    )._run_parent_adjudication("scan_parent", None)

    assert result == decision
    assert "host has already completed" in create_message.await_args.kwargs["content"].lower()
    set_callable_tools.assert_awaited_once_with(
        "coordinator",
        expected_tools,
    )
    run_loop.assert_awaited_once_with(
        "coordinator",
        provider_id="provider",
        model_id="model",
        agent_name="code-security",
    )
    assert events == ["adjudication.started", "scan.adjudicated"]


@pytest.mark.asyncio
async def test_pipeline_cancels_when_verification_makes_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_cli, "langfuse_is_active", lambda: False)
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
    monkeypatch.setattr(
        audit_cli.AuditOrchestrator,
        "_run_parent_adjudication",
        _final_adjudication,
    )

    events: list[str] = []
    with pytest.raises(RuntimeError, match="made no progress"):
        await audit_cli.AuditOrchestrator(
            ToolContext("session", "message", agent="code-security"),
            Path("/target"),
            lambda event, _payload: events.append(event),
        ).run()

    assert cancelled == ["scan_stalled"]
    assert events[-1] == "scan.cancelled"


@pytest.mark.asyncio
async def test_pipeline_emits_langfuse_scan_phase_and_progress_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traces: list[tuple[dict, object]] = []
    spans: list[tuple[dict, object]] = []
    ended: list[tuple[object, dict]] = []

    class _Observed:
        next_id = 1

        def __init__(self):
            self.trace_id = "a" * 32
            self.id = f"{_Observed.next_id:016x}"
            _Observed.next_id += 1

    class _Scope:
        def __init__(self, observation: object):
            self.observation = observation

        def end(self, **kwargs):
            ended.append((self.observation, kwargs))

    def start_trace(**kwargs):
        observation = _Observed()
        traces.append((kwargs, observation))
        return _Scope(observation)

    def start_span(**kwargs):
        observation = _Observed()
        spans.append((kwargs, observation))
        return _Scope(observation)

    async def prepare(_ctx, _target_path: str) -> ToolResult:
        return _result(
            {
                "scan_id": "scan_observed",
                "snapshot": {"file_count": 2, "display_name": "target"},
            }
        )

    async def run_workers(_ctx, scan_id: str, phase: str) -> ToolResult:
        assert _ctx.extra["langfuse_trace_context"] == {
            "trace_id": "a" * 32,
            "parent_span_id": _ctx.extra["langfuse_trace_context"]["parent_span_id"],
        }
        return _result(
            {
                "scan_id": scan_id,
                "batch_id": f"batch_{phase}",
                "phase": phase,
                "status": "running",
                "workers": [
                    {
                        "work_unit_id": f"unit_{phase}",
                        "assigned_paths": ["app.py"],
                    }
                ],
            }
        )

    async def wait_workers(_ctx, batch_id: str, timeout_seconds: int) -> ToolResult:
        return _result(
            {
                "batch_id": batch_id,
                "phase": batch_id.removeprefix("batch_"),
                "status": "completed",
            }
        )

    statuses = iter(
        [
            {"threat_model_status": "completed", "counts": {}},
            {
                "threat_model_status": "completed",
                "counts": {"unverified_candidates": 0},
            },
        ]
    )

    async def status(_ctx, _scan_id: str) -> ToolResult:
        return _result(next(statuses))

    async def finalize(_ctx, scan_id: str) -> ToolResult:
        return _result(
            {
                "scan_id": scan_id,
                "status": "completed",
                "finding_count": 1,
                "finding_summaries": [{"finding_id": "finding_1", "severity": "high"}],
            }
        )

    monkeypatch.setattr(audit_cli, "langfuse_is_active", lambda: True)
    monkeypatch.setattr(audit_cli, "trace_scope", start_trace)
    monkeypatch.setattr(audit_cli, "span_scope", start_span)
    monkeypatch.setattr(audit_cli, "audit_prepare", prepare)
    monkeypatch.setattr(audit_cli, "audit_run_workers", run_workers)
    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "audit_status", status)
    monkeypatch.setattr(audit_cli, "audit_finalize", finalize)
    monkeypatch.setattr(
        audit_cli.AuditOrchestrator,
        "_run_parent_adjudication",
        _final_adjudication,
    )

    ctx = ToolContext("session", "message", agent="code-security")
    result = await audit_cli.AuditOrchestrator(
        ctx,
        Path("/target"),
        None,
    ).run()

    assert result["finding_count"] == 1
    assert "langfuse_trace_context" not in ctx.extra
    assert traces[0][0]["name"] == "code-security.scan"
    assert traces[0][0]["session_id"] == "scan_observed"
    span_names = [kwargs["name"] for kwargs, _observation in spans]
    assert "code-security.phase.threat_modeling" in span_names
    assert "code-security.phase.baseline" in span_names
    assert "code-security.progress.batch.started" in span_names
    assert "code-security.progress.scan.finalized" in span_names
    root_observation = traces[0][1]
    assert any(
        observation is root_observation and output["output"]["status"] == "completed" for observation, output in ended
    )


@pytest.mark.asyncio
async def test_wait_for_batch_only_observes_status_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(
        [
            {"status": "running", "status_counts": {"running": 1}},
            {"status": "running", "status_counts": {"running": 1}},
            {"status": "completed", "status_counts": {"completed": 1}},
        ]
    )
    observed: list[dict] = []
    progress_events: list[dict] = []

    async def wait_workers(_ctx, _batch_id, timeout_seconds):
        assert timeout_seconds == 10
        return _result(next(outputs))

    class _Scope:
        observation = object()

        def end(self, **_kwargs):
            return None

    def start_span(**kwargs):
        observed.append(kwargs)
        return _Scope()

    monkeypatch.setattr(audit_cli, "audit_wait_workers", wait_workers)
    monkeypatch.setattr(audit_cli, "span_scope", start_span)

    result = await audit_cli._wait_for_batch(
        ToolContext("session", "message"),
        "batch_test",
        lambda _event, payload: progress_events.append(payload),
        object(),
    )

    assert result["status"] == "completed"
    assert len(progress_events) == 3
    assert len(observed) == 2
