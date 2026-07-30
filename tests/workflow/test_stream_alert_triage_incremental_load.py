from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import flocks.config
import pytest
from flocks.workflow import Workflow, WorkflowEngine
from flocks.workflow.repl_runtime import PythonExecRuntime


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "flockshub"
    / "plugins"
    / "workflows"
    / "stream_alert_triage"
    / "workflow.json"
)


def _workflow() -> dict[str, object]:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _node_code(node_id: str) -> str:
    workflow = _workflow()
    return next(node["code"] for node in workflow["nodes"] if node["id"] == node_id)


def _run_node(node_id: str, inputs: dict[str, object]) -> dict[str, object]:
    namespace: dict[str, object] = {"inputs": inputs, "outputs": {}}
    exec(compile(_node_code(node_id), str(WORKFLOW_PATH), "exec"), namespace)
    return namespace["outputs"]


def _write_alerts(path: Path, ids: range | list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps({"id": value, "dedup_key": f"key-{value}"}) + "\n" for value in ids),
        encoding="utf-8",
    )


def _write_named_alerts(path: Path, prefix: str, count: int = 20) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps({"id": f"{prefix}-{index:03d}", "dedup_key": f"key-{index:03d}"}) + "\n"
            for index in range(count)
        ),
        encoding="utf-8",
    )


def _use_flocks_root(monkeypatch, root: Path) -> None:
    class FakeConfig:
        def get_global(self) -> SimpleNamespace:
            return SimpleNamespace(data_dir=root / "data")

    monkeypatch.setattr(flocks.config, "Config", FakeConfig)


def _release_loader_lease(outputs: dict[str, object]) -> None:
    lease_fd = outputs.get("_batch_lease_fd")
    if not isinstance(lease_fd, int):
        return
    _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "pending_cursor": None,
            "_batch_lease_fd": lease_fd,
            "batch_lease_token": outputs.get("batch_lease_token"),
        },
    )


def test_explicit_replay_is_bounded_resumable_and_reads_appends(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    _write_alerts(input_path, range(20))

    first = _run_node("load_dedup_file", {"input_path": str(input_path)})
    second = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": first["next_cursor"]},
    )

    assert [item["id"] for item in first["enriched_alerts"]] == list(range(10))
    assert [item["id"] for item in second["enriched_alerts"]] == list(range(10, 20))
    assert first["cursor_enabled"] is False
    assert first["has_more"] is True
    assert second["has_more"] is False

    with input_path.open("a", encoding="utf-8") as stream:
        for value in range(20, 30):
            stream.write(json.dumps({"id": value, "dedup_key": f"key-{value}"}) + "\n")

    appended = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": second["next_cursor"]},
    )
    assert [item["id"] for item in appended["enriched_alerts"]] == list(range(20, 30))


def test_replaced_input_file_invalidates_resume_cursor(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    replacement = tmp_path / "replacement.jsonl"
    _write_named_alerts(input_path, "aaa")

    first = _run_node("load_dedup_file", {"input_path": str(input_path)})
    _write_named_alerts(replacement, "bbb")
    os.replace(replacement, input_path)
    resumed = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": first["next_cursor"]},
    )

    assert [item["id"] for item in resumed["enriched_alerts"]] == [
        f"bbb-{index:03d}" for index in range(10)
    ]
    assert resumed["load_stats"]["cursor_invalidated"] is True


def test_same_inode_rewrite_invalidates_resume_cursor(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    _write_named_alerts(input_path, "aaa")
    first = _run_node("load_dedup_file", {"input_path": str(input_path)})
    original_file_id = input_path.stat().st_ino

    payload = "".join(
        json.dumps({"id": f"bbb-{index:03d}", "dedup_key": f"key-{index:03d}"}) + "\n"
        for index in range(20)
    )
    with input_path.open("r+", encoding="utf-8") as stream:
        stream.seek(0)
        stream.write(payload)
        stream.truncate()
    assert input_path.stat().st_ino == original_file_id

    resumed = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": first["next_cursor"]},
    )

    assert [item["id"] for item in resumed["enriched_alerts"]] == [
        f"bbb-{index:03d}" for index in range(10)
    ]
    assert resumed["load_stats"]["cursor_invalidated"] is True


def test_same_inode_head_rewrite_invalidates_cursor_beyond_boundary_anchor(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    input_path.write_text(
        "".join(
            json.dumps(
                {
                    "id": f"aaa-{index:03d}",
                    "dedup_key": f"key-{index:03d}",
                    "padding": "x" * 1200,
                }
            )
            + "\n"
            for index in range(20)
        ),
        encoding="utf-8",
    )
    first = _run_node("load_dedup_file", {"input_path": str(input_path)})
    assert first["next_cursor"]["byte_offset"] > 8192
    original_file_id = input_path.stat().st_ino

    with input_path.open("r+", encoding="utf-8") as stream:
        payload = stream.read()
        stream.seek(0)
        stream.write(payload.replace("aaa-000", "bbb-000", 1))
        stream.truncate()
    assert input_path.stat().st_ino == original_file_id

    resumed = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": first["next_cursor"]},
    )

    assert resumed["enriched_alerts"][0]["id"] == "bbb-000"
    assert resumed["load_stats"]["cursor_invalidated"] is True


def test_auto_mode_sorts_numeric_sequences_and_spans_files(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    day_dir = root / "workspace" / "workflows" / "stream_alert_denoise" / "2026-07-30"
    _write_alerts(day_dir / "dedup_result_1000.jsonl", range(6, 16))
    _write_alerts(day_dir / "dedup_result_999.jsonl", range(6))
    (day_dir / "dedup_result_bad.jsonl").write_text("{}\n", encoding="utf-8")

    outputs = _run_node(
        "load_dedup_file",
        {"input_date": "2026-07-30", "batch_max_records": 10},
    )

    assert [item["id"] for item in outputs["enriched_alerts"]] == list(range(10))
    assert [Path(path).name for path in outputs["loaded_files"]] == [
        "dedup_result_999.jsonl",
        "dedup_result_1000.jsonl",
    ]
    assert outputs["pending_cursor"]["file_name"] == "dedup_result_1000.jsonl"
    assert outputs["load_stats"]["invalid_file_names"] == 1


def test_complete_bad_lines_advance_but_partial_line_does_not(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    consumed = (
        json.dumps({"_type": "file_header"})
        + "\n\nnot-json\n[]\n"
        + json.dumps({"id": 1})
        + "\n"
    )
    input_path.write_bytes(consumed.encode("utf-8") + b'{"id": 2')

    first = _run_node("load_dedup_file", {"input_path": str(input_path)})

    assert first["enriched_alerts"] == [{"id": 1}]
    assert first["pending_cursor"]["byte_offset"] == len(consumed.encode("utf-8"))
    assert first["load_stats"]["header_skipped"] == 1
    assert first["load_stats"]["empty_lines"] == 1
    assert first["load_stats"]["bad_lines"] == 1
    assert first["load_stats"]["non_object_lines"] == 1
    assert first["load_stats"]["partial_lines"] == 1

    with input_path.open("ab") as stream:
        stream.write(b"}\n")
    second = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "resume_cursor": first["next_cursor"]},
    )
    assert second["enriched_alerts"] == [{"id": 2}]


def test_oversized_line_is_skipped_without_exceeding_batch_accounting(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    oversized = b"x" * 160 + b"\n"
    input_path.write_bytes(oversized + b'{"id": 1}\n')

    first = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "batch_max_bytes": 64},
    )

    assert first["enriched_alerts"] == []
    assert first["batch_bytes"] == 64
    assert first["load_stats"]["oversized_lines"] == 1
    assert first["load_stats"]["oversized_bytes_discarded"] == 64
    assert first["pending_cursor"]["byte_offset"] == 64
    assert first["pending_cursor"]["skipping_oversized_line"] is True
    assert first["has_more"] is True

    second = _run_node(
        "load_dedup_file",
        {
            "input_path": str(input_path),
            "batch_max_bytes": 64,
            "resume_cursor": first["next_cursor"],
        },
    )
    assert second["enriched_alerts"] == []
    assert second["batch_bytes"] == 64
    assert second["load_stats"]["oversized_bytes_discarded"] == 64
    assert second["pending_cursor"]["byte_offset"] == 128
    assert second["pending_cursor"]["skipping_oversized_line"] is True

    third = _run_node(
        "load_dedup_file",
        {
            "input_path": str(input_path),
            "batch_max_bytes": 64,
            "resume_cursor": second["next_cursor"],
        },
    )
    assert third["enriched_alerts"] == [{"id": 1}]
    assert third["batch_bytes"] <= 64
    assert "skipping_oversized_line" not in third["pending_cursor"]


def test_bounded_line_reader_does_not_reread_short_lines() -> None:
    tree = ast.parse(_node_code("load_dedup_file"))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_read_bounded_line"
    )
    namespace = {"_READ_CHUNK_BYTES": 64 * 1024}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(WORKFLOW_PATH), "exec"), namespace)

    class CountingBytesIO(io.BytesIO):
        def __init__(self, initial_bytes: bytes) -> None:
            super().__init__(initial_bytes)
            self.returned_bytes = 0

        def read(self, size: int = -1) -> bytes:
            data = super().read(size)
            self.returned_bytes += len(data)
            return data

        def readline(self, size: int = -1) -> bytes:
            data = super().readline(size)
            self.returned_bytes += len(data)
            return data

    payload = b"\n" * 1024
    stream = CountingBytesIO(payload)
    consumed = 0
    while consumed < len(payload):
        status, line, scanned = namespace["_read_bounded_line"](stream, len(payload) - consumed)
        assert (status, line, scanned) == ("line", b"\n", 1)
        consumed += scanned

    assert stream.returned_bytes == len(payload)


def test_line_that_exceeds_remaining_budget_is_retried_next_batch(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    first_line = b'{"id":1}\n'
    second_line = b'{"id":2,"value":"12345"}\n'
    byte_limit = len(first_line) + len(second_line) - 1
    input_path.write_bytes(first_line + second_line)

    first = _run_node(
        "load_dedup_file",
        {"input_path": str(input_path), "batch_max_bytes": byte_limit},
    )
    second = _run_node(
        "load_dedup_file",
        {
            "input_path": str(input_path),
            "batch_max_bytes": byte_limit,
            "resume_cursor": first["next_cursor"],
        },
    )

    assert first["enriched_alerts"] == [{"id": 1}]
    assert first["pending_cursor"]["byte_offset"] == len(first_line)
    assert first["batch_bytes"] == byte_limit
    assert second["enriched_alerts"] == [{"id": 2, "value": "12345"}]


def test_explicit_missing_path_never_falls_back_to_auto_discovery(tmp_path: Path) -> None:
    outputs = _run_node(
        "load_dedup_file",
        {"input_path": str(tmp_path / "missing.jsonl")},
    )

    assert outputs["cursor_enabled"] is False
    assert outputs["loaded_files"] == []
    assert outputs["enriched_alerts"] == []
    assert outputs["load_stats"]["missing_files"] == 1


def test_production_cursor_is_only_advanced_by_commit_node(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    date = "2026-07-30"
    input_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_denoise"
        / date
        / "dedup_result_001.jsonl"
    )
    _write_alerts(input_path, range(12))
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )

    first = _run_node("load_dedup_file", {"input_date": date})
    with pytest.raises(RuntimeError, match="production_batch_lease_busy"):
        _run_node("load_dedup_file", {"input_date": date})

    assert not cursor_path.exists()

    commit_inputs = dict(first)
    commit_inputs["triage_stats"] = {"triage_failed": 1}
    commit_inputs["_triage_persistence_succeeded"] = True
    committed = _run_node("commit_cursor", commit_inputs)
    remaining = _run_node("load_dedup_file", {"input_date": date})

    saved_cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert committed["cursor_committed"] is True
    assert saved_cursor["byte_offset"] == first["pending_cursor"]["byte_offset"]
    assert saved_cursor["updated_at"]
    assert [item["id"] for item in remaining["enriched_alerts"]] == [10, 11]
    _release_loader_lease(remaining)


def test_triage_timeout_does_not_commit_production_cursor(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )
    pending_cursor = {
        "version": 1,
        "date": "2026-07-30",
        "file_seq": 1,
        "file_name": "dedup_result_001.jsonl",
        "byte_offset": 123,
    }
    workflow = Workflow.from_dict(
        {
            "name": "triage_timeout_cursor_guard",
            "start": "load",
            "nodes": [
                {
                    "id": "load",
                    "type": "python",
                    "description": "Provide a pending production cursor",
                    "code": (
                        "outputs.update({"
                        f"'cursor_enabled': True, 'pending_cursor': {pending_cursor!r}, "
                        "'cursor_before': None, 'next_cursor': None, "
                        "'_triage_persistence_succeeded': False})"
                    ),
                },
                {
                    "id": "triage",
                    "type": "python",
                    "description": "Exceed the node timeout before persistence succeeds",
                    "code": (
                        "import time\n"
                        "triage_outputs = outputs\n"
                        "time.sleep(0.2)\n"
                        "triage_outputs['_triage_persistence_succeeded'] = True"
                    ),
                },
                {
                    "id": "commit",
                    "type": "python",
                    "description": "Use the real cursor commit node",
                    "code": _node_code("commit_cursor"),
                },
            ],
            "edges": [
                {"from": "load", "to": "triage"},
                {"from": "triage", "to": "commit"},
            ],
        }
    )
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=SimpleNamespace(cancel_checker=None)),
        node_timeout_s=0.05,
        history_mode="full",
        max_parallel_workers=1,
    )

    result = engine.run(initial_inputs={}, retain_history=True)
    time.sleep(0.25)

    triage_step = next(step for step in result.history if step.node_id == "triage")
    commit_step = next(step for step in result.history if step.node_id == "commit")
    assert "节点执行超时" in (triage_step.error or "")
    assert commit_step.error is None
    assert commit_step.outputs["cursor_committed"] is False
    assert not cursor_path.exists()


def test_commit_node_does_not_rewrite_cursor_without_new_bytes(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )
    cursor_path.parent.mkdir(parents=True)
    original = {
        "version": 1,
        "date": "2026-07-30",
        "file_seq": 1,
        "file_name": "dedup_result_001.jsonl",
        "byte_offset": 123,
        "updated_at": "2026-07-30T10:00:00+08:00",
    }
    cursor_path.write_text(json.dumps(original), encoding="utf-8")

    outputs = _run_node(
        "commit_cursor",
        {"cursor_enabled": True, "cursor_before": original, "pending_cursor": None},
    )

    assert outputs["cursor_committed"] is False
    assert outputs["committed_cursor"] == original
    assert json.loads(cursor_path.read_text(encoding="utf-8")) == original


def test_cursor_date_change_and_truncation_restart_current_input(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_dir = root / "workspace" / "workflows" / "stream_alert_triage"
    cursor_dir.mkdir(parents=True)
    cursor_path = cursor_dir / ".triage_cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "version": 1,
                "date": "2026-07-29",
                "file_seq": 1,
                "file_name": "dedup_result_001.jsonl",
                "byte_offset": 9999,
            }
        ),
        encoding="utf-8",
    )
    input_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_denoise"
        / "2026-07-30"
        / "dedup_result_001.jsonl"
    )
    _write_alerts(input_path, [1])

    date_reset = _run_node("load_dedup_file", {"input_date": "2026-07-30"})
    assert date_reset["enriched_alerts"] == [{"id": 1, "dedup_key": "key-1"}]
    _release_loader_lease(date_reset)

    cursor_path.write_text(
        json.dumps(
            {
                "version": 1,
                "date": "2026-07-30",
                "file_seq": 1,
                "file_name": "dedup_result_001.jsonl",
                "byte_offset": 9999,
            }
        ),
        encoding="utf-8",
    )
    truncated = _run_node("load_dedup_file", {"input_date": "2026-07-30"})
    assert truncated["enriched_alerts"] == [{"id": 1, "dedup_key": "key-1"}]
    _release_loader_lease(truncated)

    input_path.write_bytes(b'{"id": 2')
    truncated_partial = _run_node("load_dedup_file", {"input_date": "2026-07-30"})

    assert truncated_partial["enriched_alerts"] == []
    assert truncated_partial["load_stats"]["partial_lines"] == 1
    assert truncated_partial["pending_cursor"]["byte_offset"] == 0
    assert truncated_partial["next_cursor"]["byte_offset"] == 0
    assert truncated_partial["has_more"] is True
    _release_loader_lease(truncated_partial)


def test_semantically_invalid_production_cursor_restarts_from_file_head(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    date = "2026-07-30"
    input_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_denoise"
        / date
        / "dedup_result_001.jsonl"
    )
    _write_alerts(input_path, [1, 2])
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )
    cursor_path.parent.mkdir(parents=True)
    base_cursor = {
        "version": 1,
        "date": date,
        "file_seq": 1,
        "file_name": input_path.name,
        "byte_offset": 1,
    }
    malformed_cursors = [
        {**base_cursor, "byte_offset": 1.5},
        {**base_cursor, "byte_offset": True},
        {**base_cursor, "version": 2},
    ]

    for malformed in malformed_cursors:
        cursor_path.write_text(json.dumps(malformed), encoding="utf-8")
        outputs = _run_node("load_dedup_file", {"input_date": date})

        assert outputs["cursor_before"] is None
        assert [item["id"] for item in outputs["enriched_alerts"]] == [1, 2]
        assert outputs["load_stats"]["bad_lines"] == 0
        _release_loader_lease(outputs)


def test_successful_triage_sets_persistence_gate(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)

    outputs = _run_node(
        "concurrent_triage",
        {"enriched_alerts": [], "triage_output_mode": "none"},
    )

    assert outputs["_triage_persistence_succeeded"] is True


def test_production_triage_batch_lease_blocks_overlapping_run(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    date = "2026-07-30"
    input_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_denoise"
        / date
        / "dedup_result_001.jsonl"
    )
    _write_alerts(input_path, [])

    first = _run_node("load_dedup_file", {"input_date": date})
    assert isinstance(first["_batch_lease_fd"], int)

    with pytest.raises(RuntimeError, match="production_batch_lease_busy"):
        _run_node("load_dedup_file", {"input_date": date})

    triaged = _run_node(
        "concurrent_triage",
        {**first, "triage_output_mode": "none"},
    )
    _run_node(
        "commit_cursor",
        {
            **triaged,
            "_triage_persistence_succeeded": True,
        },
    )

    retry = _run_node("load_dedup_file", {"input_date": date})
    _run_node(
        "commit_cursor",
        {
            **retry,
            "pending_cursor": None,
        },
    )


def test_stale_cursor_commit_cannot_overwrite_newer_cursor(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )
    identity = {
        "version": 2,
        "date": "2026-07-30",
        "file_seq": 1,
        "file_name": "dedup_result_001.jsonl",
        "device_id": 1,
        "file_id": 2,
        "head_hash": hashlib.sha256(b"").hexdigest(),
        "boundary_start": 0,
        "boundary_hash": hashlib.sha256(b"").hexdigest(),
    }

    newer = _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_before": None,
            "cursor_revision": None,
            "pending_cursor": {**identity, "byte_offset": 200},
            "_triage_persistence_succeeded": True,
            "_run_id": "newer",
        },
    )
    stale = _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_before": None,
            "cursor_revision": None,
            "pending_cursor": {**identity, "byte_offset": 100},
            "_triage_persistence_succeeded": True,
            "_run_id": "stale",
        },
    )

    assert newer["cursor_committed"] is True
    assert stale["cursor_committed"] is False
    assert stale["cursor_commit_error"] == "stale_cursor_commit"
    assert json.loads(cursor_path.read_text(encoding="utf-8"))["byte_offset"] == 200
    assert not list(cursor_path.parent.glob(".triage_cursor.json.*.tmp"))


def test_invalidated_cursor_reset_requires_cas_and_cannot_move_to_older_date(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )
    identity = {
        "version": 2,
        "date": "2026-07-30",
        "file_seq": 1,
        "file_name": "dedup_result_001.jsonl",
        "device_id": 1,
        "file_id": 2,
        "head_hash": hashlib.sha256(b"old-head").hexdigest(),
        "boundary_start": 0,
        "boundary_hash": hashlib.sha256(b"old-boundary").hexdigest(),
    }
    _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_revision": None,
            "pending_cursor": {**identity, "byte_offset": 200},
            "_triage_persistence_succeeded": True,
        },
    )

    current_revision = hashlib.sha256(cursor_path.read_bytes()).hexdigest()
    reset = _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_revision": current_revision,
            "cursor_invalidated": True,
            "pending_cursor": {
                **identity,
                "byte_offset": 100,
                "device_id": 3,
                "file_id": 4,
                "head_hash": hashlib.sha256(b"new-head").hexdigest(),
                "boundary_hash": hashlib.sha256(b"new-boundary").hexdigest(),
            },
            "_triage_persistence_succeeded": True,
        },
    )
    assert reset["cursor_committed"] is True
    assert json.loads(cursor_path.read_text(encoding="utf-8"))["byte_offset"] == 100

    reset_revision = hashlib.sha256(cursor_path.read_bytes()).hexdigest()
    older = _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_revision": reset_revision,
            "cursor_invalidated": True,
            "pending_cursor": {**identity, "date": "2026-07-29", "byte_offset": 0},
            "_triage_persistence_succeeded": True,
        },
    )
    assert older["cursor_committed"] is False
    assert older["cursor_commit_error"] == "stale_cursor_commit"
    assert json.loads(cursor_path.read_text(encoding="utf-8"))["date"] == "2026-07-30"


def test_workflow_wires_commit_after_persistence_and_has_dynamic_samples() -> None:
    workflow = _workflow()

    assert [node["id"] for node in workflow["nodes"]] == [
        "load_dedup_file",
        "concurrent_triage",
        "commit_cursor",
        "summarize",
    ]
    assert [(edge["from"], edge["to"]) for edge in workflow["edges"]] == [
        ("load_dedup_file", "concurrent_triage"),
        ("concurrent_triage", "commit_cursor"),
        ("commit_cursor", "summarize"),
    ]
    assert "input_date" not in workflow["metadata"]["sampleInputs"]
    assert workflow["metadata"]["sampleInputs"]["batch_max_records"] == 10
    assert workflow["metadata"]["sampleInputs"]["batch_max_bytes"] == 32 * 1024 * 1024
    for trigger in workflow["triggers"]:
        assert trigger["runtime"]["noOverlap"] is True
        assert "input_date" not in trigger["inputs"]
        assert "input_date" not in trigger["testSamples"][0]["payload"]


def test_jsonl_persistence_failure_is_reraised() -> None:
    tree = ast.parse(_node_code("concurrent_triage"))
    persistence_try = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_triage_write_jsonl"
            for child in ast.walk(node)
        )
    )

    assert any(
        isinstance(child, ast.Raise)
        for handler in persistence_try.handlers
        for child in ast.walk(handler)
    )


def test_jsonl_counter_write_failure_is_not_swallowed() -> None:
    tree = ast.parse(_node_code("concurrent_triage"))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_triage_set_counter"
    )

    assert not any(isinstance(node, ast.ExceptHandler) for node in ast.walk(function))


def test_commit_writer_uses_atomic_replace_and_fsync(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "flocks-root"
    _use_flocks_root(monkeypatch, root)
    cursor_path = (
        root
        / "workspace"
        / "workflows"
        / "stream_alert_triage"
        / ".triage_cursor.json"
    )

    outputs = _run_node(
        "commit_cursor",
        {
            "cursor_enabled": True,
            "cursor_revision": None,
            "_triage_persistence_succeeded": True,
            "_run_id": "atomic-test",
            "pending_cursor": {
                "version": 2,
                "date": "2026-07-30",
                "file_seq": 3,
                "file_name": "dedup_result_003.jsonl",
                "byte_offset": 123,
                "device_id": 1,
                "file_id": 2,
                "head_hash": hashlib.sha256(b"").hexdigest(),
                "boundary_start": 0,
                "boundary_hash": hashlib.sha256(b"").hexdigest(),
                "skipping_oversized_line": True,
            },
        },
    )
    committed = outputs["committed_cursor"]

    assert json.loads(cursor_path.read_text(encoding="utf-8")) == committed
    assert committed["updated_at"]
    assert committed["skipping_oversized_line"] is True
    assert not list(cursor_path.parent.glob(".triage_cursor.json.*.tmp"))
