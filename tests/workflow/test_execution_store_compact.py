"""Regression tests for ``compact_outputs_for_storage`` and
``compact_history_for_storage`` in ``flocks.workflow.execution_store``.

These helpers protect the ``workflow_execution`` SQLite row from being
inflated to tens of MB per syslog message: each execution of
``stream_alert_dedup`` (and similar streaming workflows) can produce
``enriched_alerts``/``unique_alerts`` lists with thousands of items that
are already persisted to JSONL on disk.  Without compaction, those lists
end up duplicated both in the final ``outputResults`` and in every
intermediate ``executionLog`` snapshot written by ``_on_step_complete``,
which is the root cause of the syslog-driven memory blow-up.

The tests below pin the externally observable contract so future
refactors don't accidentally drop the protection or, conversely, start
stripping legitimately small metadata lists.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from flocks.workflow.execution_store import (
    DEFAULT_COMPACT_SIZE_THRESHOLD,
    DEFAULT_GENERIC_SEQUENCE_THRESHOLD,
    DEFAULT_LARGE_LIST_KEYS,
    DEFAULT_MAX_INLINE_COLLECTION_BYTES,
    ExecutionProgressWriter,
    ExecutionStepRecorder,
    compact_history_for_storage,
    compact_execution_summary,
    compact_outputs_for_storage,
    compact_step_for_storage,
    create_execution_record,
    record_execution_result,
    workflow_execution_step_key,
)
from flocks.workflow.store import WorkflowStore


def _make_alerts(n: int) -> List[Dict[str, Any]]:
    return [{"sip": f"1.2.3.{i % 256}", "url": f"/p/{i}"} for i in range(n)]


# ── compact_outputs_for_storage ───────────────────────────────────────────────


def test_compact_outputs_strips_large_alert_lists() -> None:
    big = _make_alerts(5_000)
    outputs = {
        "enriched_alerts": big,
        "unique_alerts": big[:1_000],
        "dedup_key": "abc",
        "stats": {"raw_count": 5_000},
    }

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["_enriched_alerts_count"] == 5_000
    assert compacted["_unique_alerts_count"] == 1_000
    assert "enriched_alerts" not in compacted
    assert "unique_alerts" not in compacted
    # Non-list metadata is preserved verbatim.
    assert compacted["dedup_key"] == "abc"
    assert compacted["stats"] == {"raw_count": 5_000}


def test_compact_outputs_keeps_small_lists_verbatim() -> None:
    """A list whose key matches but stays below the size threshold is
    passed through unchanged: small metadata arrays (e.g. error details)
    must remain inspectable in the execution-history UI.
    """
    small = _make_alerts(10)
    outputs = {"enriched_alerts": small, "stats": {"raw_count": 10}}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["enriched_alerts"] == small
    assert "_enriched_alerts_count" not in compacted


def test_compact_outputs_strips_small_count_large_alert_lists() -> None:
    large_record = {"body": "x" * DEFAULT_MAX_INLINE_COLLECTION_BYTES}
    outputs = {"enriched_alerts_with_triage": [large_record]}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted == {"_enriched_alerts_with_triage_count": 1}


def test_compact_outputs_summarizes_small_count_large_unknown_sequences() -> None:
    outputs = {"unknown_payload": [{"body": "x" * DEFAULT_MAX_INLINE_COLLECTION_BYTES}]}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["unknown_payload"]["_type"] == "list"
    assert compacted["unknown_payload"]["count"] == 1


def test_compact_outputs_summarizes_unknown_large_sequences() -> None:
    big_unknown = _make_alerts(DEFAULT_GENERIC_SEQUENCE_THRESHOLD + 1)
    outputs = {"some_other_alerts": big_unknown}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["some_other_alerts"]["_type"] == "list"
    assert compacted["some_other_alerts"]["count"] == DEFAULT_GENERIC_SEQUENCE_THRESHOLD + 1
    assert len(compacted["some_other_alerts"]["preview"]) == 3
    assert compacted["some_other_alerts"] is not big_unknown


def test_compact_outputs_summarizes_large_strings() -> None:
    outputs = {"huge_text": "x" * 25_000}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["huge_text"]["_type"] == "string"
    assert compacted["huge_text"]["chars"] == 25_000
    assert len(compacted["huge_text"]["preview"]) < 25_000


def test_compact_outputs_accepts_custom_keys_and_threshold() -> None:
    big = _make_alerts(150)
    outputs = {"custom_payload": big, "enriched_alerts": _make_alerts(50)}

    compacted = compact_outputs_for_storage(
        outputs,
        keys={"custom_payload"},
        size_threshold=100,
    )

    assert compacted["_custom_payload_count"] == 150
    # Default key is no longer in the override set so its list is kept.
    assert compacted["enriched_alerts"] == _make_alerts(50)


def test_compact_outputs_compacts_tuple_sequences() -> None:
    """``tuple`` values whose key is in the default set must be compacted just
    like ``list`` values, since some serialisation paths (e.g. ``exec()``
    return values) may produce tuples instead of lists.
    """
    big_tuple = tuple(_make_alerts(5_000))
    outputs = {"enriched_alerts": big_tuple, "dedup_key": "x"}

    compacted = compact_outputs_for_storage(outputs)

    assert compacted["_enriched_alerts_count"] == 5_000
    assert "enriched_alerts" not in compacted
    assert compacted["dedup_key"] == "x"


def test_compact_outputs_handles_non_dict_input() -> None:
    assert compact_outputs_for_storage(None) == {}
    assert compact_outputs_for_storage([1, 2, 3]) == {}
    assert compact_outputs_for_storage("oops") == {}


def test_compact_outputs_does_not_mutate_input() -> None:
    big = _make_alerts(5_000)
    outputs = {"enriched_alerts": big, "dedup_key": "abc"}

    compact_outputs_for_storage(outputs)

    assert "enriched_alerts" in outputs
    assert outputs["enriched_alerts"] is big
    assert outputs["dedup_key"] == "abc"


def test_compact_outputs_drastically_reduces_serialised_size() -> None:
    """End-to-end size guarantee: the typical 10K-alert payload should
    shrink by more than 1000x once compacted, which is what makes the
    SQLite row size bounded under syslog throughput.
    """
    import json

    big = [
        {
            "sip": f"1.2.3.{i % 256}",
            "req_http_url": "/admin?id=" + "x" * 200,
            "req_body": "b" * 300,
            "dedup_key": "abc" * 10,
        }
        for i in range(10_000)
    ]
    outputs = {"enriched_alerts": big, "unique_alerts": big[:2_000]}

    before = len(json.dumps(outputs).encode())
    after = len(json.dumps(compact_outputs_for_storage(outputs)).encode())

    assert before > 1_000_000  # ≥ 1 MB before
    assert after < 1_000  # < 1 KB after
    assert before / after > 1_000


# ── compact_history_for_storage ───────────────────────────────────────────────


def test_compact_step_compacts_inputs_and_outputs() -> None:
    big = _make_alerts(5_000)
    step = {
        "node_id": "normalize",
        "inputs": {"raw_alerts": big, "source": "syslog"},
        "outputs": {"normalized_alerts": big, "message": "ok"},
    }

    compacted = compact_step_for_storage(step)

    assert compacted["inputs"] == {"_raw_alerts_count": 5_000, "source": "syslog"}
    assert compacted["outputs"] == {"_normalized_alerts_count": 5_000, "message": "ok"}


def test_compact_history_compacts_each_step_outputs() -> None:
    big = _make_alerts(5_000)
    history = [
        {"node_id": "receive", "outputs": {"raw_alerts": big}},
        {"node_id": "normalize", "outputs": {"normalized_alerts": big}},
        {"node_id": "dedup", "outputs": {"enriched_alerts": big, "dedup_key": "x"}},
    ]

    compacted = compact_history_for_storage(history)

    assert compacted[0]["outputs"] == {"_raw_alerts_count": 5_000}
    assert compacted[1]["outputs"] == {"_normalized_alerts_count": 5_000}
    assert compacted[2]["outputs"]["_enriched_alerts_count"] == 5_000
    assert compacted[2]["outputs"]["dedup_key"] == "x"
    # Top-level keys (node_id) untouched.
    assert [s["node_id"] for s in compacted] == ["receive", "normalize", "dedup"]


def test_compact_history_passes_through_falsy_history() -> None:
    assert compact_history_for_storage(None) == []
    assert compact_history_for_storage([]) == []


def test_compact_history_does_not_mutate_input() -> None:
    big = _make_alerts(5_000)
    history = [{"node_id": "x", "outputs": {"enriched_alerts": big}}]

    compact_history_for_storage(history)

    assert history[0]["outputs"]["enriched_alerts"] is big


def test_compact_history_tolerates_non_dict_steps() -> None:
    """Defensive: a malformed step entry should pass through rather than
    crash the syslog/HTTP execution recorder.
    """
    history = [
        "not-a-dict",
        {"node_id": "ok", "outputs": {"enriched_alerts": _make_alerts(5_000)}},
        42,
    ]

    compacted = compact_history_for_storage(history)

    assert compacted[0] == "not-a-dict"
    assert compacted[2] == 42
    assert compacted[1]["outputs"]["_enriched_alerts_count"] == 5_000


def test_compact_history_skips_step_with_non_dict_outputs() -> None:
    history = [{"node_id": "weird", "outputs": "string-output"}]

    compacted = compact_history_for_storage(history)

    # Non-dict outputs are left as-is (defensive pass-through).
    assert compacted[0]["outputs"] == "string-output"


def test_compact_step_accepts_pydantic_like_model_dump() -> None:
    class StepLike:
        def model_dump(self, mode: str = "python") -> Dict[str, Any]:
            assert mode == "json"
            return {
                "node_id": "step-1",
                "outputs": {"raw_alerts": _make_alerts(150)},
            }

    compacted = compact_step_for_storage(StepLike())

    assert compacted["node_id"] == "step-1"
    assert compacted["outputs"] == {"_raw_alerts_count": 150}


def test_compact_execution_summary_drops_execution_log() -> None:
    exec_data = {
        "id": "exec-1",
        "workflowId": "wf",
        "executionLog": [{"node_id": "a"}],
        "stepCount": 1,
    }

    summary = compact_execution_summary(exec_data)

    assert summary["executionLog"] == []
    assert summary["stepCount"] == 1
    assert exec_data["executionLog"] == [{"node_id": "a"}]


def test_workflow_execution_step_key_is_append_only_namespaced() -> None:
    assert workflow_execution_step_key("exec-1", 12) == "workflow_execution_step/exec-1/00000012"


@pytest.mark.asyncio
async def test_create_execution_record_can_skip_initial_database_write() -> None:
    upsert_execution = AsyncMock(return_value=None)

    with patch.object(WorkflowStore, "upsert_execution", upsert_execution):
        record = await create_execution_record(
            "wf-trigger",
            input_params={"message": "hello"},
            exec_id="exec-trigger",
            persist=False,
        )

    assert record["id"] == "exec-trigger"
    assert record["currentPhase"] == "queued"
    upsert_execution.assert_not_awaited()


def test_execution_step_recorder_collects_steps_without_storage_calls() -> None:
    record_step = AsyncMock(return_value=None)
    record_steps = AsyncMock(return_value=None)
    recorder = ExecutionStepRecorder(exec_id="exec-batch")

    with (
        patch.object(WorkflowStore, "record_step", record_step),
        patch.object(WorkflowStore, "record_steps", record_steps),
    ):
        recorder.on_step_complete({"node_id": "n1", "outputs": {"ok": 1}})
        recorder.on_step_complete({"node_id": "n2", "outputs": {"ok": 2}})

    assert recorder.step_count == 2
    assert recorder.summary["currentNodeId"] == "n2"
    assert recorder.take_steps() == [
        (1, {"node_id": "n1", "outputs": {"ok": 1}}),
        (2, {"node_id": "n2", "outputs": {"ok": 2}}),
    ]
    assert recorder.take_steps() == []
    record_step.assert_not_awaited()
    record_steps.assert_not_awaited()


@pytest.mark.asyncio
async def test_four_trigger_workers_collect_steps_without_storage() -> None:
    """Four trigger threads collect complete batches without callback SQL."""
    record_step = AsyncMock(return_value=None)
    record_steps = AsyncMock(return_value=None)
    recorders = [ExecutionStepRecorder(exec_id=f"exec-trigger-{worker}") for worker in range(4)]

    def _run_seven_steps(recorder: ExecutionStepRecorder) -> None:
        for step in range(7):
            recorder.on_step_complete(
                {"node_id": f"node-{step}", "outputs": {"ok": True}}
            )

    with (
        patch.object(WorkflowStore, "record_step", record_step),
        patch.object(WorkflowStore, "record_steps", record_steps),
    ):
        await asyncio.gather(
            *(asyncio.to_thread(_run_seven_steps, recorder) for recorder in recorders)
        )

    batches = [recorder.take_steps() for recorder in recorders]
    assert [len(batch) for batch in batches] == [7, 7, 7, 7]
    assert [recorder.step_count for recorder in recorders] == [7, 7, 7, 7]
    record_step.assert_not_awaited()
    record_steps.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_writer_submits_without_waiting_and_coalesces_updates() -> None:
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    writes: List[Dict[str, Any]] = []
    active_writes = 0
    max_active_writes = 0

    async def blocked_upsert(summary: Dict[str, Any]) -> None:
        nonlocal active_writes, max_active_writes
        active_writes += 1
        max_active_writes = max(max_active_writes, active_writes)
        writes.append(dict(summary))
        try:
            if len(writes) == 1:
                write_started.set()
                await release_write.wait()
        finally:
            active_writes -= 1

    writer = ExecutionProgressWriter(
        {
            "id": "exec-progress",
            "workflowId": "wf-progress",
            "status": "running",
            "executionLog": [{"node_id": "ignored"}],
        }
    )

    with patch.object(WorkflowStore, "upsert_execution", side_effect=blocked_upsert):
        writer.submit({"currentNodeId": "node-1", "currentStepIndex": 1})
        await write_started.wait()
        await asyncio.wait_for(
            asyncio.to_thread(
                writer.submit,
                {"currentNodeId": "node-2", "currentStepIndex": 2},
            ),
            timeout=0.1,
        )
        await asyncio.to_thread(
            writer.submit,
            {"currentNodeId": "node-3", "currentStepIndex": 3},
        )
        release_write.set()
        await writer.close_and_drain()

    assert max_active_writes == 1
    assert len(writes) == 2
    assert writes[0]["currentNodeId"] == "node-1"
    assert writes[-1]["currentNodeId"] == "node-3"
    assert writes[-1]["currentStepIndex"] == 3
    assert writes[-1]["executionLog"] == []


@pytest.mark.asyncio
async def test_progress_writer_awaited_update_is_ordered_and_close_rejects_late_updates() -> None:
    writes: List[Dict[str, Any]] = []

    async def capture_upsert(summary: Dict[str, Any]) -> None:
        writes.append(dict(summary))

    writer = ExecutionProgressWriter(
        {
            "id": "exec-cancelling",
            "workflowId": "wf-cancelling",
            "status": "running",
            "currentPhase": "queued",
            "executionLog": [],
        }
    )

    with patch.object(WorkflowStore, "upsert_execution", side_effect=capture_upsert):
        await asyncio.to_thread(
            writer.submit,
            {"currentPhase": "running", "currentNodeId": "node-1"},
        )
        await writer.update({"currentPhase": "cancelling"})
        await writer.close_and_drain()
        writer.submit({"currentPhase": "running", "currentNodeId": "late-node"})
        await asyncio.sleep(0)

    assert writes[-1]["currentPhase"] == "cancelling"
    assert writes[-1]["currentNodeId"] == "node-1"
    assert all(write.get("currentNodeId") != "late-node" for write in writes)


@pytest.mark.asyncio
async def test_progress_writer_logs_write_failures_without_raising() -> None:
    writer = ExecutionProgressWriter(
        {
            "id": "exec-write-failure",
            "workflowId": "wf-write-failure",
            "status": "running",
            "executionLog": [],
        }
    )

    with patch.object(
        WorkflowStore,
        "upsert_execution",
        AsyncMock(side_effect=RuntimeError("database locked")),
    ):
        await writer.update({"currentPhase": "running"})
        await writer.close_and_drain()


@pytest.mark.asyncio
async def test_record_execution_result_backfills_execution_log_steps() -> None:
    calls: List[str] = []

    async def complete_execution(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("complete")

    async def increment_stats(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("stats")

    async def trim_executions(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("trim")
        return []

    complete_execution_mock = AsyncMock(side_effect=complete_execution)
    increment_stats_mock = AsyncMock(side_effect=increment_stats)
    trim_executions_mock = AsyncMock(side_effect=trim_executions)
    record_audit = AsyncMock(return_value=None)
    exec_data = {
        "id": "exec-1",
        "workflowId": "wf",
        "status": "success",
        "duration": 1.0,
        "executionLog": [
            {"node_id": "step-1", "outputs": {"raw_alerts": _make_alerts(150)}},
            {"node_id": "step-2", "inputs": {"filtered_alerts": _make_alerts(150)}},
        ],
    }

    def raise_create_task(coro, *args, **kwargs):  # noqa: ANN001, ARG001
        coro.close()
        raise RuntimeError

    with (
        patch.object(WorkflowStore, "complete_execution", complete_execution_mock),
        patch.object(WorkflowStore, "increment_stats", increment_stats_mock),
        patch.object(WorkflowStore, "trim_executions", trim_executions_mock),
        patch("flocks.session.recorder.Recorder.record_workflow_execution", record_audit),
        patch("flocks.workflow.execution_store.asyncio.create_task", side_effect=raise_create_task),
    ):
        await record_execution_result("wf", "exec-1", exec_data)

    assert calls == ["complete", "stats", "trim"]
    complete_execution_mock.assert_awaited_once()
    summary, steps = complete_execution_mock.await_args.args
    assert complete_execution_mock.await_args.kwargs == {}
    assert steps[0][0] == 1
    assert steps[0][1]["outputs"] == {"_raw_alerts_count": 150}
    assert steps[1][0] == 2
    assert steps[1][1]["inputs"] == {"_filtered_alerts_count": 150}
    assert summary["executionLog"] == []
    assert summary["stepCount"] == 2
    increment_stats_mock.assert_awaited_once_with("wf", success=True, duration=1.0)
    trim_executions_mock.assert_awaited_once_with("wf", keep=30)
    audit_data = record_audit.await_args.kwargs["run_result"]
    assert audit_data["executionLog"] == [step for _, step in steps]


@pytest.mark.asyncio
async def test_record_execution_result_accepts_explicit_step_batch() -> None:
    complete_execution = AsyncMock(return_value=None)
    increment_stats = AsyncMock(return_value=None)
    trim_executions = AsyncMock(return_value=[])
    record_audit = AsyncMock(return_value=None)
    explicit_steps = [
        (2, {"node_id": "step-2", "outputs": {"ok": True}}),
        (1, {"node_id": "step-1", "outputs": {"ok": True}}),
    ]
    exec_data = {
        "id": "exec-trigger",
        "workflowId": "wf-trigger",
        "status": "success",
        "duration": 0.01,
        "executionLog": [],
        "stepCount": 2,
    }

    def raise_create_task(coro, *args, **kwargs):  # noqa: ANN001, ARG001
        coro.close()
        raise RuntimeError

    with (
        patch.object(WorkflowStore, "complete_execution", complete_execution),
        patch.object(WorkflowStore, "increment_stats", increment_stats),
        patch.object(WorkflowStore, "trim_executions", trim_executions),
        patch("flocks.session.recorder.Recorder.record_workflow_execution", record_audit),
        patch("flocks.workflow.execution_store.asyncio.create_task", side_effect=raise_create_task),
    ):
        await record_execution_result(
            "wf-trigger",
            "exec-trigger",
            exec_data,
            steps=explicit_steps,
        )

    summary, persisted_steps = complete_execution.await_args.args
    assert complete_execution.await_args.kwargs == {}
    assert summary["executionLog"] == []
    assert persisted_steps == explicit_steps
    increment_stats.assert_awaited_once_with("wf-trigger", success=True, duration=0.01)
    trim_executions.assert_awaited_once_with("wf-trigger", keep=30)
    audit_data = record_audit.await_args.kwargs["run_result"]
    assert [step["node_id"] for step in audit_data["executionLog"]] == [
        "step-1",
        "step-2",
    ]


@pytest.mark.asyncio
async def test_record_execution_result_stats_failure_does_not_block_retention() -> None:
    complete_execution = AsyncMock(return_value=None)
    increment_stats = AsyncMock(side_effect=RuntimeError("stats locked"))
    trim_executions = AsyncMock(return_value=[])

    def raise_create_task(coro, *args, **kwargs):  # noqa: ANN001, ARG001
        coro.close()
        raise RuntimeError

    with (
        patch.object(WorkflowStore, "complete_execution", complete_execution),
        patch.object(WorkflowStore, "increment_stats", increment_stats),
        patch.object(WorkflowStore, "trim_executions", trim_executions),
        patch("flocks.session.recorder.Recorder.record_workflow_execution", AsyncMock(return_value=None)),
        patch("flocks.workflow.execution_store.asyncio.create_task", side_effect=raise_create_task),
    ):
        await record_execution_result(
            "wf-stats-failure",
            "exec-stats-failure",
            {
                "id": "exec-stats-failure",
                "workflowId": "wf-stats-failure",
                "status": "success",
                "duration": 0.5,
                "executionLog": [],
            },
        )

    complete_execution.assert_awaited_once()
    increment_stats.assert_awaited_once()
    trim_executions.assert_awaited_once_with("wf-stats-failure", keep=30)


@pytest.mark.asyncio
async def test_record_execution_result_retention_failure_keeps_committed_execution() -> None:
    complete_execution = AsyncMock(return_value=None)
    increment_stats = AsyncMock(return_value=None)
    trim_executions = AsyncMock(side_effect=RuntimeError("retention locked"))

    def raise_create_task(coro, *args, **kwargs):  # noqa: ANN001, ARG001
        coro.close()
        raise RuntimeError

    with (
        patch.object(WorkflowStore, "complete_execution", complete_execution),
        patch.object(WorkflowStore, "increment_stats", increment_stats),
        patch.object(WorkflowStore, "trim_executions", trim_executions),
        patch("flocks.session.recorder.Recorder.record_workflow_execution", AsyncMock(return_value=None)),
        patch("flocks.workflow.execution_store.asyncio.create_task", side_effect=raise_create_task),
    ):
        await record_execution_result(
            "wf-retention-failure",
            "exec-retention-failure",
            {
                "id": "exec-retention-failure",
                "workflowId": "wf-retention-failure",
                "status": "error",
                "duration": 0.5,
                "executionLog": [],
            },
        )

    complete_execution.assert_awaited_once()
    increment_stats.assert_awaited_once_with(
        "wf-retention-failure",
        success=False,
        duration=0.5,
    )
    trim_executions.assert_awaited_once_with("wf-retention-failure", keep=30)


def test_compact_history_compacts_each_step_inputs() -> None:
    big = _make_alerts(5_000)
    history = [
        {
            "node_id": "dedup",
            "inputs": {"enriched_alerts": big, "dedup_key": "x"},
            "outputs": {"unique_alerts": big},
        },
    ]

    compacted = compact_history_for_storage(history)

    assert compacted[0]["inputs"] == {"_enriched_alerts_count": 5_000, "dedup_key": "x"}
    assert compacted[0]["outputs"] == {"_unique_alerts_count": 5_000}


# ── Defaults exposed to callers ───────────────────────────────────────────────


def test_default_large_list_keys_cover_stream_alert_dedup_outputs() -> None:
    """The default key set must include every large list produced by the
    stream_alert_dedup workflow; otherwise syslog memory growth regresses
    silently.
    """
    expected = {
        "enriched_alerts",
        "unique_alerts",
        "raw_alerts",
        "normalized_alerts",
        "filtered_alerts",
        "enriched_alerts_with_triage",
    }
    assert expected <= DEFAULT_LARGE_LIST_KEYS


def test_default_compact_size_threshold_is_reasonable() -> None:
    # The threshold must be high enough to keep ordinary metadata lists
    # (a few dozen items at most) intact, but low enough that megabyte-
    # scale payloads get compacted on every triggered execution.
    assert 1 <= DEFAULT_COMPACT_SIZE_THRESHOLD <= 1_000


# ── create_execution_record compacts inputParams ─────────────────────────────


def test_compact_outputs_covers_input_params_batch_key() -> None:
    """HTTP /run batch calls may pass a large ``alerts`` list as inputParams.
    ``compact_outputs_for_storage`` must compact it when the key is in
    ``DEFAULT_LARGE_LIST_KEYS`` – this is what ``create_execution_record``
    now does before writing to SQLite.
    """
    batch_inputs = {
        "alerts": _make_alerts(5_000),
        "filter_enabled": True,
        "threshold": 0.7,
    }

    compacted = compact_outputs_for_storage(batch_inputs)

    assert "_alerts_count" not in compacted, (
        "'alerts' is not in DEFAULT_LARGE_LIST_KEYS so it should pass through unchanged"
    )
    # Scalar fields must survive unchanged.
    assert compacted["filter_enabled"] is True
    assert compacted["threshold"] == 0.7


def test_compact_outputs_covers_raw_alerts_in_input_params() -> None:
    """When inputParams contains ``raw_alerts`` (a key that IS in
    DEFAULT_LARGE_LIST_KEYS), it must be compacted.
    """
    batch_inputs = {
        "raw_alerts": _make_alerts(5_000),
        "source_log_type": "tdp",
    }

    compacted = compact_outputs_for_storage(batch_inputs)

    assert "_raw_alerts_count" in compacted
    assert compacted["_raw_alerts_count"] == 5_000
    assert "raw_alerts" not in compacted
    assert compacted["source_log_type"] == "tdp"


@pytest.mark.asyncio
async def test_record_execution_result_deletes_jsonl_for_trimmed_executions(tmp_path) -> None:
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    trimmed_paths = [workflow_dir / "exec-00.jsonl", workflow_dir / "exec-01.jsonl"]
    retained_path = workflow_dir / "exec-02.jsonl"
    for record_path in [*trimmed_paths, retained_path]:
        record_path.write_text('{"type":"workflow.summary"}\n', encoding="utf-8")

    complete_execution = AsyncMock(return_value=None)
    increment_stats = AsyncMock(return_value=None)
    trim_executions = AsyncMock(return_value=["exec-00", "exec-01"])
    record_audit = AsyncMock(return_value=None)
    created_tasks: List[asyncio.Task[None]] = []
    real_create_task = asyncio.create_task

    def capture_create_task(coro, *args, **kwargs):  # noqa: ANN001
        task = real_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    with (
        patch.object(WorkflowStore, "complete_execution", complete_execution),
        patch.object(WorkflowStore, "increment_stats", increment_stats),
        patch.object(WorkflowStore, "trim_executions", trim_executions),
        patch("flocks.session.recorder.Recorder.record_workflow_execution", record_audit),
        patch(
            "flocks.workflow.execution_store.Recorder.paths",
            return_value=SimpleNamespace(workflow_dir=workflow_dir),
        ),
        patch(
            "flocks.workflow.execution_store.asyncio.create_task",
            side_effect=capture_create_task,
        ),
    ):
        await record_execution_result(
            "wf-trim",
            "exec-32",
            {
                "id": "exec-32",
                "workflowId": "wf-trim",
                "status": "success",
                "duration": 0.25,
                "executionLog": [],
            },
            steps=[(1, {"node_id": "node-1", "outputs": {"ok": True}})],
        )
        await asyncio.gather(*created_tasks)

    trim_executions.assert_awaited_once_with("wf-trim", keep=30)
    record_audit.assert_awaited_once()
    assert all(not path.exists() for path in trimmed_paths)
    assert retained_path.exists()
