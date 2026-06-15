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

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from flocks.workflow.execution_store import (
    DEFAULT_COMPACT_SIZE_THRESHOLD,
    DEFAULT_LARGE_LIST_KEYS,
    _trim_execution_history,
    compact_history_for_storage,
    compact_execution_summary,
    compact_outputs_for_storage,
    compact_step_for_storage,
    record_execution_result,
    workflow_execution_step_key,
)
from flocks.storage.storage import Storage


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


def test_compact_outputs_ignores_unknown_keys() -> None:
    big_unknown = _make_alerts(5_000)
    outputs = {"some_other_alerts": big_unknown}

    compacted = compact_outputs_for_storage(outputs)

    # Unknown keys are not in the default large-list set; they must pass
    # through even if huge, so callers don't get surprising drops.
    assert compacted["some_other_alerts"] is big_unknown


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
    assert after < 1_000        # < 1 KB after
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
    assert (
        workflow_execution_step_key("exec-1", 12)
        == "workflow_execution_step/exec-1/00000012"
    )


@pytest.mark.asyncio
async def test_record_execution_result_backfills_execution_log_steps() -> None:
    storage_write = AsyncMock(return_value=None)
    update_stats = AsyncMock(return_value=None)
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

    with patch.object(Storage, "write", storage_write), \
         patch("flocks.workflow.execution_store._update_workflow_stats", update_stats), \
         patch("flocks.session.recorder.Recorder.record_workflow_execution", AsyncMock(return_value=None)), \
         patch("flocks.workflow.execution_store.asyncio.create_task", side_effect=raise_create_task), \
         patch("flocks.workflow.execution_store._trim_execution_history", AsyncMock(return_value=None)):
        await record_execution_result("wf", "exec-1", exec_data)

    write_calls = storage_write.await_args_list
    assert write_calls[0].args[0] == "workflow_execution_step/exec-1/00000001"
    assert write_calls[0].args[1]["outputs"] == {"_raw_alerts_count": 150}
    assert write_calls[1].args[0] == "workflow_execution_step/exec-1/00000002"
    assert write_calls[1].args[1]["inputs"] == {"_filtered_alerts_count": 150}
    assert write_calls[2].args[0] == "workflow_execution/exec-1"
    assert write_calls[2].args[1]["executionLog"] == []
    assert write_calls[2].args[1]["stepCount"] == 2


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
async def test_trim_execution_history_keeps_only_30_and_deletes_matching_jsonl(
    tmp_path,
) -> None:
    workflow_id = "wf-trim"
    entries = []
    for idx in range(32):
        exec_id = f"exec-{idx:02d}"
        entries.append((
            f"workflow_execution/{exec_id}",
            {
                "id": exec_id,
                "workflowId": workflow_id,
                "startedAt": idx,
            },
        ))
        workflow_record = tmp_path / "workflow" / f"{exec_id}.jsonl"
        workflow_record.parent.mkdir(parents=True, exist_ok=True)
        workflow_record.write_text('{"type":"workflow.summary"}\n', encoding="utf-8")

    # Another workflow's record should be ignored entirely.
    entries.append((
        "workflow_execution/other-exec",
        {"id": "other-exec", "workflowId": "wf-other", "startedAt": 0},
    ))
    other_record = tmp_path / "workflow" / "other-exec.jsonl"
    other_record.parent.mkdir(parents=True, exist_ok=True)
    other_record.write_text('{"type":"workflow.summary"}\n', encoding="utf-8")

    remove_mock = AsyncMock(return_value=None)
    raw_entries = [(key, json.dumps(value)) for key, value in entries]

    async def list_raw_side_effect(prefix: str):
        if prefix == "workflow_execution/":
            return raw_entries
        return []

    with patch.object(Storage, "list_raw", AsyncMock(side_effect=list_raw_side_effect)), \
         patch.object(Storage, "remove", remove_mock), \
         patch("flocks.session.recorder._record_dir", return_value=tmp_path):
        await _trim_execution_history(workflow_id)

    removed_keys = [call.args[0] for call in remove_mock.await_args_list]
    assert removed_keys == [
        "workflow_execution/exec-00",
        "workflow_execution/exec-01",
    ]
    assert not (tmp_path / "workflow" / "exec-00.jsonl").exists()
    assert not (tmp_path / "workflow" / "exec-01.jsonl").exists()
    assert (tmp_path / "workflow" / "exec-02.jsonl").exists()
    assert other_record.exists()
