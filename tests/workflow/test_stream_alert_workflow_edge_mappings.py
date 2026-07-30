from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import flocks.config
import flocks.workspace.manager
import pytest

from flocks.workflow import Workflow, WorkflowEngine
from flocks.workflow.edge_resolver import EdgeResolver
from flocks.workflow.execution_plan import resolve_workflow_dataflow_mode
from flocks.workflow.repl_runtime import PythonExecRuntime
from flocks.workflow.workflow_lint import lint_workflow


WORKFLOW_ROOT = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "flockshub"
    / "plugins"
    / "workflows"
)

EXPECTED_MAPPING_KEYS = {
    "stream_alert_denoise": {
        ("receive_alert", "normalize"): {
            "raw_alerts",
            "input_mode",
            "source_log_type",
            "filter_enabled",
            "dedup_enabled",
            "dedup_threshold",
            "strict_fields",
            "lsh_fields",
            "max_field_len",
            "max_dedup_keys",
            "stats",
        },
        ("normalize", "filter_logs"): {
            "normalized_alerts",
            "input_mode",
            "source_log_type",
            "filter_enabled",
            "dedup_enabled",
            "dedup_threshold",
            "strict_fields",
            "lsh_fields",
            "max_field_len",
            "max_dedup_keys",
            "stats",
        },
        ("filter_logs", "dedup_and_write"): {
            "filtered_alerts",
            "input_mode",
            "dedup_enabled",
            "dedup_threshold",
            "strict_fields",
            "lsh_fields",
            "max_field_len",
            "max_dedup_keys",
            "stats",
        },
    },
    "stream_alert_triage": {
        ("load_dedup_file", "concurrent_triage"): {
            "enriched_alerts",
            "loaded_files",
            "load_stats",
            "concurrency",
            "max_triage_cache_size",
            "input_date",
            "cursor_enabled",
            "cursor_before",
            "pending_cursor",
            "next_cursor",
            "has_more",
            "batch_records",
            "batch_bytes",
            "_triage_persistence_succeeded",
            "_run_id",
            "triage_output_mode",
            "persist_triage_output",
            "soc_db_path",
            "jsonl_output_dir",
        },
        ("concurrent_triage", "commit_cursor"): {
            "cursor_enabled",
            "cursor_before",
            "pending_cursor",
            "next_cursor",
            "has_more",
            "batch_records",
            "batch_bytes",
            "_triage_persistence_succeeded",
            "input_date",
            "load_stats",
            "loaded_files",
            "enriched_alerts_with_triage",
            "triage_results",
            "triage_stats",
            "triage_output_mode",
            "soc_db_result",
            "soc_db_path",
            "output_paths",
            "output_dir",
        },
        ("commit_cursor", "summarize"): {
            "cursor_enabled",
            "cursor_committed",
            "committed_cursor",
            "next_cursor",
            "has_more",
            "batch_records",
            "batch_bytes",
            "input_date",
            "load_stats",
            "loaded_files",
            "enriched_alerts_with_triage",
            "triage_results",
            "triage_stats",
            "triage_output_mode",
            "soc_db_result",
            "soc_db_path",
            "output_paths",
            "output_dir",
        },
    },
}


def _workflow_dict(workflow_id: str) -> dict[str, object]:
    path = WORKFLOW_ROOT / workflow_id / "workflow.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _use_flocks_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    class FakeConfig:
        def get_global(self) -> SimpleNamespace:
            return SimpleNamespace(data_dir=root / "data")

    monkeypatch.setattr(flocks.config, "Config", FakeConfig)


@pytest.mark.parametrize("workflow_id", EXPECTED_MAPPING_KEYS)
def test_stream_workflow_edges_use_strict_explicit_mappings(workflow_id: str) -> None:
    raw = _workflow_dict(workflow_id)
    workflow = Workflow.from_dict(raw)
    actual = {
        (edge["from"], edge["to"]): edge.get("mapping", {})
        for edge in raw["edges"]
    }
    expected = {
        edge: {field: field for field in fields}
        for edge, fields in EXPECTED_MAPPING_KEYS[workflow_id].items()
    }

    assert raw["metadata"]["runtime"] == {
        "strict_edge_mapping": True,
        "dataflow_mode": "vertex_cache",
    }
    assert resolve_workflow_dataflow_mode(workflow.metadata) == "vertex_cache"
    assert actual == expected
    assert lint_workflow(workflow) == []


@pytest.mark.parametrize("workflow_id", EXPECTED_MAPPING_KEYS)
def test_stream_workflow_mappings_drop_unlisted_payload_fields(workflow_id: str) -> None:
    raw = _workflow_dict(workflow_id)
    workflow = Workflow.from_dict(raw)
    nodes = workflow.nodes_by_id()
    resolver = EdgeResolver(dataflow_mode="vertex_cache")

    for edge in workflow.edges:
        source_values = {source: f"value-for-{source}" for source in edge.mapping.values()}
        resolved = resolver.resolve(
            node=nodes[edge.from_],
            node_inputs={"unlisted_large_payload": [object()]},
            node_outputs=source_values,
            edges=[edge],
        )

        assert len(resolved) == 1
        assert resolved[0][1] == {
            destination: source_values[source]
            for destination, source in edge.mapping.items()
        }
        assert "unlisted_large_payload" not in resolved[0][1]


def test_mappings_do_not_forward_obsolete_large_alert_lists() -> None:
    denoise = _workflow_dict("stream_alert_denoise")
    denoise_edges = {
        (edge["from"], edge["to"]): set(edge["mapping"].values())
        for edge in denoise["edges"]
    }
    triage = _workflow_dict("stream_alert_triage")
    triage_edges = {
        (edge["from"], edge["to"]): set(edge["mapping"].values())
        for edge in triage["edges"]
    }

    assert "raw_alerts" not in denoise_edges[("normalize", "filter_logs")]
    assert not {"raw_alerts", "normalized_alerts"} & denoise_edges[("filter_logs", "dedup_and_write")]
    assert "enriched_alerts" not in triage_edges[("concurrent_triage", "commit_cursor")]
    assert "pending_cursor" not in triage_edges[("commit_cursor", "summarize")]


def test_denoise_execution_drops_consumed_payloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_flocks_root(monkeypatch, tmp_path / "flocks-root")
    raw = _workflow_dict("stream_alert_denoise")
    workflow = Workflow.from_dict(raw)
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=SimpleNamespace(cancel_checker=None)),
        node_timeout_s=None,
        history_mode="full",
        dataflow_mode=resolve_workflow_dataflow_mode(workflow.metadata),
        max_parallel_workers=1,
    )

    result = engine.run(
        initial_inputs={
            "alerts": [
                {
                    "net_type": "http",
                    "net_http_url": "/health",
                    "net_real_src_ip": "192.0.2.1",
                    "net_dest_ip": "198.51.100.1",
                    "threat_name": "mapping-test",
                    "threat_type": "web攻击",
                }
            ],
            "filter_enabled": False,
            "dedup_enabled": False,
            "threshold": 0.42,
            "unlisted_large_payload": ["sentinel"],
        },
        retain_history=True,
    )

    steps = {step.node_id: step for step in result.history}
    assert all(step.error is None for step in result.history)
    assert "unlisted_large_payload" not in steps["normalize"].inputs
    assert "raw_alerts" not in steps["filter_logs"].inputs
    assert "normalized_alerts" not in steps["dedup_and_write"].inputs
    assert steps["dedup_and_write"].inputs["dedup_threshold"] == 0.42


def test_triage_execution_drops_loader_payload_before_commit_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _use_flocks_root(monkeypatch, tmp_path / "flocks-root")

    class FakeWorkspaceManager:
        @classmethod
        def get_instance(cls) -> FakeWorkspaceManager:
            return cls()

        def get_workspace_dir(self) -> Path:
            return tmp_path / "workspace"

    monkeypatch.setattr(flocks.workspace.manager, "WorkspaceManager", FakeWorkspaceManager)
    input_path = tmp_path / "dedup_result_001.jsonl"
    input_path.write_text("", encoding="utf-8")
    raw = _workflow_dict("stream_alert_triage")
    workflow = Workflow.from_dict(raw)
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=SimpleNamespace(cancel_checker=None)),
        node_timeout_s=None,
        history_mode="full",
        dataflow_mode=resolve_workflow_dataflow_mode(workflow.metadata),
        max_parallel_workers=1,
    )

    result = engine.run(
        initial_inputs={
            "input_path": str(input_path),
            "triage_output_mode": "none",
            "unlisted_large_payload": ["sentinel"],
        },
        retain_history=True,
    )

    steps = {step.node_id: step for step in result.history}
    assert all(step.error is None for step in result.history)
    assert "unlisted_large_payload" not in steps["concurrent_triage"].inputs
    assert steps["concurrent_triage"].inputs["triage_output_mode"] == "none"
    assert "enriched_alerts" not in steps["commit_cursor"].inputs
    assert steps["commit_cursor"].inputs["_triage_persistence_succeeded"] is True
    assert "pending_cursor" not in steps["summarize"].inputs
    assert "enriched_alerts_with_triage" in steps["summarize"].inputs
