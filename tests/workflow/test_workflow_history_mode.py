from flocks.workflow.runner import run_workflow
from flocks.workflow.repl_runtime import PythonExecRuntime


def test_run_workflow_summary_history_does_not_retain_large_step_payloads() -> None:
    workflow = {
        "start": "produce",
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "\n".join(
                    [
                        "outputs['raw_alerts'] = [{'id': i, 'body': 'x' * 1000} for i in range(200)]",
                        "outputs['count'] = len(outputs['raw_alerts'])",
                    ]
                ),
            },
            {
                "id": "consume",
                "type": "python",
                "code": "\n".join(
                    [
                        "alerts = inputs.get('raw_alerts', [])",
                        "outputs['final_count'] = len(alerts)",
                    ]
                ),
            },
        ],
        "edges": [{"from": "produce", "to": "consume"}],
    }

    result = run_workflow(
        workflow=workflow,
        history_mode="summary",
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"final_count": 200}
    assert result.history[0]["outputs"]["raw_alerts"] == {
        "_type": "list",
        "count": 200,
        "preview": [
            {"_type": "dict", "keys": ["id", "body"]},
            {"_type": "dict", "keys": ["id", "body"]},
            {"_type": "dict", "keys": ["id", "body"]},
        ],
    }
    assert result.history[1]["inputs"]["raw_alerts"]["count"] == 200


def test_run_workflow_defaults_to_summary_history_mode() -> None:
    workflow = {
        "start": "produce",
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "outputs['items'] = [{'body': 'x' * 1000} for _ in range(200)]",
            },
        ],
        "edges": [],
    }

    result = run_workflow(workflow=workflow, ensure_requirements=False)

    assert result.status == "SUCCEEDED"
    assert result.outputs["items"] == {
        "_type": "list",
        "count": 200,
        "preview": [
            {"_type": "dict", "keys": ["body"]},
            {"_type": "dict", "keys": ["body"]},
            {"_type": "dict", "keys": ["body"]},
        ],
    }


def test_run_workflow_summary_outputs_do_not_retain_large_final_payloads() -> None:
    workflow = {
        "start": "final",
        "nodes": [
            {
                "id": "final",
                "type": "python",
                "code": "outputs['items'] = [{'body': 'x' * 1000} for _ in range(200)]",
            },
        ],
        "edges": [],
    }

    result = run_workflow(
        workflow=workflow,
        history_mode="summary",
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["items"]["_type"] == "list"
    assert result.outputs["items"]["count"] == 200


def test_python_runtime_can_cleanup_node_globals_after_execute() -> None:
    runtime = PythonExecRuntime(cleanup_globals_after_execute=True)

    outputs, _stdout = runtime.execute(
        "temporary_payload = 'x' * 1000\noutputs['ok'] = True",
        {},
    )

    assert outputs == {"ok": True}
    assert "temporary_payload" not in runtime.globals
    assert runtime.globals["outputs"] == {"ok": True}
