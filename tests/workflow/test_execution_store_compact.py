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

from typing import Any, Dict, List

import pytest

from flocks.workflow.execution_store import (
    DEFAULT_COMPACT_SIZE_THRESHOLD,
    DEFAULT_LARGE_LIST_KEYS,
    compact_history_for_storage,
    compact_outputs_for_storage,
)


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
