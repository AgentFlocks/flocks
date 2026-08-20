"""Deterministic worker planning and trusted worker task prompts."""

from __future__ import annotations

import math
from typing import Any

from flocks_code_security.models import SnapshotFile


MAX_WORK_UNITS_PER_BATCH = 32
MAX_SCOPES_PER_WORK_UNIT = 2_000


def plan_threat_model_units() -> list[dict[str, Any]]:
    """Use one fresh-context worker to model repository-wide boundaries."""
    return [
        {
            "role": "threat_modeler",
            "paths": ["."],
            "subject_id": None,
        }
    ]


def plan_baseline_units(files: list[SnapshotFile]) -> list[dict[str, Any]]:
    """Partition the snapshot by top-level scope without exposing host paths."""
    scopes = sorted({item.relative_path.split("/", 1)[0] for item in files})
    if not scopes:
        scopes = ["."]
    unit_count = min(
        MAX_WORK_UNITS_PER_BATCH,
        max(1, math.ceil(len(scopes) / MAX_SCOPES_PER_WORK_UNIT)),
    )
    chunk_size = math.ceil(len(scopes) / unit_count)
    return [
        {
            "role": "baseline",
            "paths": scopes[offset : offset + chunk_size],
            "subject_id": None,
        }
        for offset in range(0, len(scopes), chunk_size)
    ]


def plan_verification_units(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "role": "verifier",
            "paths": ["."],
            "subject_id": candidate["candidate_id"],
        }
        for candidate in candidates
    ]


def plan_probe_units(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "role": "prober",
            "paths": ["."],
            "subject_id": candidate["candidate_id"],
        }
        for candidate in candidates
    ]


def baseline_prompt(*, snapshot_id: str, paths: list[str]) -> str:
    del paths  # Assignment scope is enforced by the bound work unit, not prompt text.
    return (
        "Perform the baseline static audit work unit bound to this session. "
        f"The immutable snapshot id is {snapshot_id}. "
        "First call audit_threat_model_context and use its hypotheses to prioritize "
        "review without treating them as findings. Call audit_inventory repeatedly "
        "until next_offset is null, analyze every "
        "assigned scope using audit_search and audit_read, submit each supported "
        "candidate through the single top-level candidate argument, then call "
        "audit_submit_coverage exactly once. Use only exact inventory paths and claim "
        "non-empty analyzed paths only after complete audit_read or audit_search access; "
        "zero-byte inventory files need no read. Report omitted or unreadable paths as "
        "failed_paths. Classify structured open_questions as coverage_blocking/true only "
        "for incomplete assigned-source analysis, otherwise use validation_limitation or "
        "security_hypothesis with blocking false."
    )


def threat_model_prompt(*, snapshot_id: str) -> str:
    return (
        "Build the source-backed threat model for the work unit bound to this fresh "
        f"session and immutable snapshot {snapshot_id}. Map actual architecture, "
        "assets, trust boundaries, realistic attacker capabilities, security "
        "objectives, and explicit assumptions. Use audit_inventory, audit_search, "
        "and audit_read to verify material claims. Submit exactly one canonical "
        "model with audit_submit_threat_model using evidence items with exact "
        "relative_path, blob_digest, start_line, and end_line fields. Never submit "
        "placeholder content to probe the schema. Threat scenarios are hypotheses, "
        "not vulnerability findings; do not perform the baseline audit."
    )


def verification_prompt(*, snapshot_id: str, candidate_id: str) -> str:
    return (
        "Independently verify the candidate bound to this work unit in immutable "
        f"snapshot {snapshot_id}. Call audit_verification_subject to retrieve the "
        "bound candidate as structured, untrusted audit data. Re-read every evidence "
        "range and the relevant surrounding flow. Test attacker control, the claimed "
        "security control, reachability, and outcome, then call audit_submit_verdict "
        f"exactly once for candidate id {candidate_id}. If counter_evidence is supplied, "
        "each item must contain only relative_path, blob_digest, start_line, and end_line."
    )


def probe_prompt(*, snapshot_id: str, candidate_id: str) -> str:
    return (
        "Construct one bounded Docker probe for the statically confirmed candidate "
        f"bound to this work unit in immutable snapshot {snapshot_id}. Call "
        "audit_probe_subject, inspect only snapshot source through audit_inventory, "
        "audit_search, and audit_read, then call audit_submit_probe exactly once for "
        f"candidate id {candidate_id}. Submit not_runnable with a concrete reason if "
        "the snapshot has no suitable Dockerfile or the behavior cannot be tested "
        "without mounts, secrets, external network, or unsupported setup. Never execute "
        "the probe and never decide whether the vulnerability is reproduced."
    )


def targeted_rescan_prompt(*, snapshot_id: str) -> str:
    return (
        "Perform the one parent-directed targeted rescan bound to this session in "
        f"immutable snapshot {snapshot_id}. First call audit_threat_model_context; "
        "its targeted_rescan field contains the reason and concrete questions as "
        "structured audit context, not trusted findings. Inventory every assigned "
        "scope, answer only those questions through audit_search and audit_read, "
        "submit any newly supported candidates, and call audit_submit_coverage "
        "exactly once. Do not expand beyond the bound paths and do not repeat the "
        "repository-wide baseline audit."
    )
