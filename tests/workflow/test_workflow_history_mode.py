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
        retain_history=True,
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
    assert result.history == []
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


def test_run_workflow_can_retain_history_when_requested() -> None:
    workflow = {
        "start": "produce",
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "outputs['value'] = 1",
            },
        ],
        "edges": [],
    }

    result = run_workflow(
        workflow=workflow,
        ensure_requirements=False,
        retain_history=True,
    )

    assert result.status == "SUCCEEDED"
    assert len(result.history) == 1
    assert result.history[0]["node_id"] == "produce"


def test_python_runtime_can_cleanup_node_globals_after_execute() -> None:
    runtime = PythonExecRuntime(cleanup_globals_after_execute=True)

    outputs, _stdout = runtime.execute(
        "temporary_payload = 'x' * 1000\noutputs['ok'] = True",
        {},
    )

    assert outputs == {"ok": True}
    assert "temporary_payload" not in runtime.globals
    assert "inputs" not in runtime.globals
    assert "outputs" not in runtime.globals


def test_mapped_payload_is_not_retained_for_remaining_run() -> None:
    workflow = {
        "start": "produce",
        "metadata": {
            "runtime": {
                "strict_edge_mapping": True,
                "dataflow_mode": "vertex_cache",
            }
        },
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "\n".join(
                    [
                        "import weakref",
                        "class Payload(list): pass",
                        "payload = Payload(range(100000))",
                        "outputs['large_payload'] = payload",
                        "outputs['payload_ref'] = weakref.ref(payload)",
                    ]
                ),
            },
            {
                "id": "inspect",
                "type": "python",
                "code": "\n".join(
                    [
                        "import gc",
                        "gc.collect()",
                        "outputs['large_payload_still_alive'] = inputs['payload_ref']() is not None",
                    ]
                ),
            },
        ],
        "edges": [
            {
                "from": "produce",
                "to": "inspect",
                "mapping": {"payload_ref": "payload_ref"},
            },
        ],
    }

    result = run_workflow(
        workflow=workflow,
        history_mode="summary",
        ensure_requirements=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"large_payload_still_alive": False}


def test_parallel_mapped_payloads_are_released_before_downstream_nodes() -> None:
    producer_code = "\n".join(
        [
            "import weakref",
            "class Payload(list): pass",
            "payload = Payload(range(100000))",
            "outputs['large_payload'] = payload",
            "outputs['payload_ref'] = weakref.ref(payload)",
        ]
    )
    inspect_code = "\n".join(
        [
            "import gc",
            "gc.collect()",
            "outputs['large_payload_still_alive'] = inputs['payload_ref']() is not None",
        ]
    )
    workflow = {
        "start": "seed",
        "metadata": {"runtime": {"dataflow_mode": "vertex_cache"}},
        "nodes": [
            {"id": "seed", "type": "python", "code": "outputs['token'] = True"},
            {"id": "produce_a", "type": "python", "code": producer_code},
            {"id": "produce_b", "type": "python", "code": producer_code},
            {"id": "inspect_a", "type": "python", "code": inspect_code},
            {"id": "inspect_b", "type": "python", "code": inspect_code},
            {
                "id": "collect",
                "type": "python",
                "join": True,
                "code": "outputs.update(inputs)",
            },
        ],
        "edges": [
            {"from": "seed", "to": "produce_a", "mapping": {"token": "token"}},
            {"from": "seed", "to": "produce_b", "mapping": {"token": "token"}},
            {
                "from": "produce_a",
                "to": "inspect_a",
                "mapping": {"payload_ref": "payload_ref"},
            },
            {
                "from": "produce_b",
                "to": "inspect_b",
                "mapping": {"payload_ref": "payload_ref"},
            },
            {
                "from": "inspect_a",
                "to": "collect",
                "mapping": {"alive_a": "large_payload_still_alive"},
            },
            {
                "from": "inspect_b",
                "to": "collect",
                "mapping": {"alive_b": "large_payload_still_alive"},
            },
        ],
    }

    result = run_workflow(
        workflow=workflow,
        history_mode="summary",
        ensure_requirements=False,
        max_parallel_workers=2,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"alive_a": False, "alive_b": False}
