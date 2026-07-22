from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from flocks.workflow import runner as runner_module
from flocks.workflow.execution_plan import WorkflowExecutionPlan, build_workflow_execution_plan
from flocks.workflow.models import Workflow
from flocks.workflow.runner import run_workflow


def _workflow() -> Workflow:
    return Workflow.from_dict(
        {
            "start": "start",
            "nodes": [
                {
                    "id": "start",
                    "type": "python",
                    "code": "outputs['ok'] = inputs.get('value', 1)",
                }
            ],
            "edges": [],
        }
    )


def test_run_workflow_accepts_execution_plan_without_rebuilding(monkeypatch) -> None:
    plan = build_workflow_execution_plan(_workflow())

    def _fail_build(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("plan should not be rebuilt")

    monkeypatch.setattr(runner_module, "build_workflow_execution_plan", _fail_build)
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "host")
    monkeypatch.setattr(runner_module, "get_tool_registry", lambda tool_context=None: None)

    result = run_workflow(
        workflow=plan,
        inputs={"value": 7},
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"ok": 7}


def test_high_frequency_profile_uses_lightweight_runtime_options(monkeypatch) -> None:
    captured_init: dict[str, Any] = {}
    captured_run: dict[str, Any] = {}

    class FakeEngine:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            captured_init.update(kwargs)

        def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
            captured_run.update(kwargs)
            return SimpleNamespace(
                run_id=kwargs.get("run_id"),
                steps=1,
                last_node_id="start",
                outputs={"ok": True},
                history=[],
            )

    monkeypatch.setattr(runner_module, "WorkflowEngine", FakeEngine)
    monkeypatch.setattr(runner_module, "_resolve_workflow_runtime_preference", lambda _ctx: "host")
    monkeypatch.setattr(runner_module, "get_tool_registry", lambda tool_context=None: None)

    result = run_workflow(
        workflow=_workflow(),
        trace=True,
        node_timeout_s=300,
        history_mode="full",
        retain_history=True,
        execution_profile="high_frequency",
        run_id="exec-1",
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert captured_init["trace"] is False
    assert captured_init["node_timeout_s"] == 300
    assert captured_init["history_mode"] == "summary"
    assert isinstance(captured_init["execution_plan"], WorkflowExecutionPlan)
    assert captured_run["retain_history"] is False
    assert captured_run["run_id"] == "exec-1"
