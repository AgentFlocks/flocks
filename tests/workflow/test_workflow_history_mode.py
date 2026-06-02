from flocks.workflow.runner import run_workflow


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

