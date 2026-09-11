"""SQLite persistence for scans, snapshots, bindings, and audit facts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from flocks.utils.process_identity import process_identity

from flocks_code_security.coverage import (
    CoverageBlockedError,
    merge_analysis_coverage,
    normalize_open_questions,
)
from flocks_code_security.execution import (
    ExecutionCapsuleError,
    MAX_FRESH_ATTEMPTS,
    MAX_SAME_SESSION_RESUMES,
    scope_digest,
    toolset_digest,
)
from flocks_code_security.models import (
    CoverageAttestation,
    CoverageRecord,
    ExecutionCapsule,
    ManifestComponent,
    RepositoryManifest,
    SessionBinding,
    SnapshotFile,
    SnapshotOmission,
    SnapshotRef,
)
from flocks_code_security.poc import (
    POC_FILE_ENCODINGS,
    decode_poc_bytes,
    require_cybergym_submission_input,
)


THREAT_MODEL_REQUIRED_LIST_FIELDS = (
    "assets",
    "trustBoundaries",
    "attackerCapabilities",
    "securityObjectives",
)
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_KNOWLEDGE_BASE_BYTES = 32 * 1024
MAX_POC_BUNDLE_BYTES = 512 * 1024
MAX_POC_FILE_BYTES = 128 * 1024
MAX_GLOBAL_ACTIVE_WORKERS = 10
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
WORKER_ROLE_AGENTS = {
    "threat_modeler": "code-security-threat-modeler",
    "baseline": "code-security-baseline",
    "investigator": "code-security-investigator",
    "verifier": "code-security-verifier",
    "prober": "code-security-prober",
    "cybergym_solver": "code-security-cybergym-solver",
    "poc_generator": "code-security-poc-generator",
}
DYNAMIC_EXECUTION_CATEGORIES = (
    "code-execution",
    "code-injection",
    "command-injection",
    "rce",
    "remote-code-execution",
    "template-injection",
    "unsafe-deserialization",
)


def _cybergym_poc_input_limit(manifest: dict[str, Any]) -> int:
    """Return the byte limit that both PoC storage and CyberGym can accept."""
    limits = manifest.get("limits")
    contract = manifest.get("input_contract")
    if not isinstance(limits, dict) or (contract is not None and not isinstance(contract, dict)):
        raise ValueError("CyberGym task manifest is invalid")
    artifact_limit = limits.get("max_artifact_bytes")
    contract_limit = contract.get("max_bytes") if contract is not None else None
    if type(artifact_limit) is not int or artifact_limit < 1:
        raise ValueError("CyberGym task artifact limit is invalid")
    if contract_limit is not None and (type(contract_limit) is not int or contract_limit < 0):
        raise ValueError("CyberGym task input contract limit is invalid")
    return min(
        MAX_POC_FILE_BYTES,
        artifact_limit,
        artifact_limit if contract_limit is None else contract_limit,
    )


# Bump this whenever initialize() adds or changes schema migrations.
STORE_SCHEMA_VERSION = 9
SQLITE_BUSY_TIMEOUT_MS = 120_000


class WorkerCapacityUnavailable(RuntimeError):
    """The process-shared code-audit worker budget is currently full."""


def _ranges_cover(
    ranges: Iterable[tuple[int | None, int | None]],
    start_line: int,
    end_line: int,
) -> bool:
    """Return whether sorted source-read intervals cover one evidence range."""
    covered_through = start_line - 1
    for start, end in ranges:
        if start is None or end is None or start > covered_through + 1:
            break
        covered_through = max(covered_through, end)
        if covered_through >= end_line:
            return True
    return False


@contextmanager
def _cross_process_lock(path: Path) -> Iterator[None]:
    """Serialize schema initialization across independent audit processes."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_is_running(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _scan_owner_is_running(
    pid: Any,
    owner_token: Any,
    owner_identity: Any,
    *,
    active_owner_tokens: set[str] | None,
) -> bool:
    if not _pid_is_running(pid):
        return False
    if isinstance(owner_identity, str) and process_identity(pid) != owner_identity:
        return False
    if active_owner_tokens is not None and pid == os.getpid():
        return isinstance(owner_token, str) and owner_token in active_owner_tokens
    return True


class ScanStore:
    def __init__(self, database_path: Path, *, read_only: bool = False):
        self.read_only = read_only
        self.retain_ui_history = os.environ.get("FLOCKS_CODE_SECURITY_RETAIN_UI_HISTORY") == "1"
        self.database_path = database_path
        self._lock = threading.RLock()
        self.worker_limit = int(os.environ.get("FLOCKS_CODE_SECURITY_WORKERS", MAX_GLOBAL_ACTIVE_WORKERS))
        if self.worker_limit < 1:
            raise ValueError("Worker limit must be positive")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path.as_uri() + "?mode=ro" if self.read_only else self.database_path,
            uri=self.read_only,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        if not self.read_only:
            self.database_path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _restrict_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.database_path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    @staticmethod
    def _migrate_legacy_coverage(connection: sqlite3.Connection) -> None:
        """Convert model-authored legacy coverage into explicit reanalysis state."""
        legacy_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'coverage'"
        ).fetchone()
        if legacy_table is None:
            return
        legacy_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(coverage)").fetchall()
        }
        if not {"scan_id", "work_unit_id", "payload_json", "updated_at"} <= legacy_columns:
            return

        legacy_rows = connection.execute(
            "SELECT scan_id, work_unit_id, payload_json, updated_at "
            "FROM coverage ORDER BY scan_id, work_unit_id"
        ).fetchall()
        for legacy in legacy_rows:
            existing = connection.execute(
                "SELECT 1 FROM coverage_attestations WHERE work_unit_id = ? LIMIT 1",
                (legacy["work_unit_id"],),
            ).fetchone()
            if existing is not None:
                continue
            unit = connection.execute(
                "SELECT wu.paths_json, wu.role, s.snapshot_id, s.coverage_policy "
                "FROM work_units wu JOIN scans s ON s.scan_id = wu.scan_id "
                "WHERE wu.work_unit_id = ? AND wu.scan_id = ?",
                (legacy["work_unit_id"], legacy["scan_id"]),
            ).fetchone()
            attempt = connection.execute(
                "SELECT attempt_id FROM work_attempts WHERE work_unit_id = ? "
                "ORDER BY ordinal DESC LIMIT 1",
                (legacy["work_unit_id"],),
            ).fetchone()
            if unit is None or attempt is None or unit["role"] not in {
                "baseline",
                "investigator",
            }:
                continue
            try:
                assigned_scopes = json.loads(unit["paths_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(assigned_scopes, list):
                continue

            def in_scope(path: str) -> bool:
                return any(
                    scope == "." or path == scope or path.startswith(f"{scope}/")
                    for scope in assigned_scopes
                    if isinstance(scope, str)
                )

            # Legacy analyzed/failed arrays had no host receipts, so none are trusted.
            records: list[dict[str, Any]] = []
            files = connection.execute(
                "SELECT relative_path, size_bytes FROM snapshot_files "
                "WHERE snapshot_id = ? ORDER BY relative_path",
                (unit["snapshot_id"],),
            ).fetchall()
            for item in files:
                if not in_scope(item["relative_path"]):
                    continue
                is_empty = int(item["size_bytes"]) == 0
                records.append(
                    {
                        "relative_path": item["relative_path"],
                        "state": "not_applicable" if is_empty else "unexamined",
                        "reason": (
                            "not_applicable_empty"
                            if is_empty
                            else "legacy_coverage_requires_reanalysis"
                        ),
                        "receipt_digest": None,
                    }
                )
            omissions = connection.execute(
                "SELECT relative_path, reason FROM snapshot_omissions "
                "WHERE snapshot_id = ? ORDER BY relative_path",
                (unit["snapshot_id"],),
            ).fetchall()
            for item in omissions:
                if in_scope(item["relative_path"]):
                    records.append(
                        {
                            "relative_path": item["relative_path"],
                            "state": "failed",
                            "reason": f"snapshot_omission:{item['reason']}",
                            "receipt_digest": None,
                        }
                    )
            records.sort(key=lambda item: item["relative_path"])

            legacy_questions: list[dict[str, Any]] = []
            try:
                payload = json.loads(legacy["payload_json"])
                if isinstance(payload, dict):
                    legacy_questions = normalize_open_questions(
                        payload.get("open_questions", [])
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            warning = {
                "question": (
                    "Coverage was stored in a legacy untrusted format and requires "
                    "source reanalysis."
                ),
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": [],
                "follow_up": "Re-run this work unit against the immutable snapshot.",
            }
            questions = [warning, *legacy_questions[:99]]
            policy = str(unit["coverage_policy"])
            completeness = "blocked" if policy == "exhaustive" else "partial"
            attestation_id = f"attestation_{uuid.uuid4().hex}"
            digest_payload = {
                "work_unit_id": legacy["work_unit_id"],
                "attempt_id": attempt["attempt_id"],
                "policy": policy,
                "completeness": completeness,
                "records": records,
                "open_questions": questions,
            }
            attestation_digest = hashlib.sha256(
                json.dumps(
                    digest_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO coverage_attestations ("
                "attestation_id, scan_id, work_unit_id, attempt_id, policy, "
                "completeness, assigned_count, read_complete_count, failed_count, "
                "unexamined_count, attestation_digest, open_questions_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                (
                    attestation_id,
                    legacy["scan_id"],
                    legacy["work_unit_id"],
                    attempt["attempt_id"],
                    policy,
                    completeness,
                    len(records),
                    sum(item["state"] == "failed" for item in records),
                    sum(
                        item["state"]
                        not in {"read_complete", "failed", "not_applicable"}
                        for item in records
                    ),
                    attestation_digest,
                    json.dumps(questions, ensure_ascii=False, sort_keys=True),
                    legacy["updated_at"] or _now(),
                ),
            )
            connection.executemany(
                "INSERT INTO coverage_records ("
                "attestation_id, relative_path, state, reason, receipt_digest"
                ") VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        attestation_id,
                        item["relative_path"],
                        item["state"],
                        item["reason"],
                        item["receipt_digest"],
                    )
                    for item in records
                ],
            )

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database_path.parent.chmod(0o700)
        lock_path = self.database_path.with_name(
            f"{self.database_path.name}.initialize.lock"
        )
        with (
            self._lock,
            _cross_process_lock(lock_path),
            self._connect() as connection,
        ):
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if schema_version >= STORE_SCHEMA_VERSION:
                self._restrict_database_files()
                return
            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).casefold()
            if journal_mode != "wal":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    repository_identity TEXT NOT NULL,
                    source_revision TEXT,
                    target_kind TEXT NOT NULL DEFAULT 'directory_snapshot',
                    display_name TEXT NOT NULL DEFAULT 'snapshot',
                    include_paths_json TEXT NOT NULL DEFAULT '["."]',
                    exclude_patterns_json TEXT NOT NULL DEFAULT '[]',
                    tree_digest TEXT NOT NULL,
                    scope_digest TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    omitted_file_count INTEGER NOT NULL DEFAULT 0,
                    copy_source INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS snapshot_omissions (
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    size_bytes INTEGER,
                    PRIMARY KEY (snapshot_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS snapshot_files (
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    blob_digest TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    line_count INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    is_binary INTEGER NOT NULL,
                    PRIMARY KEY (snapshot_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS repository_manifests (
                    manifest_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL UNIQUE REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
                    manifest_digest TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    omitted_file_count INTEGER NOT NULL,
                    languages_json TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    mode TEXT NOT NULL,
                    dynamic_enabled INTEGER NOT NULL DEFAULT 0,
                    poc_enabled INTEGER NOT NULL DEFAULT 0,
                    coverage_policy TEXT NOT NULL DEFAULT 'evidence_backed_partial',
                    verification_vote_count INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    ruleset_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_knowledge_base (
                    scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_units (
                    work_unit_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    phase TEXT NOT NULL,
                    role TEXT NOT NULL,
                    paths_json TEXT NOT NULL,
                    session_id TEXT,
                    background_task_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    assignment_digest TEXT
                );
                CREATE TABLE IF NOT EXISTS work_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    session_id TEXT NOT NULL UNIQUE,
                    background_task_id TEXT,
                    agent_name TEXT NOT NULL,
                    provider_id TEXT,
                    model_id TEXT,
                    toolset_digest TEXT NOT NULL,
                    scope_digest TEXT NOT NULL,
                    capsule_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    failure_class TEXT,
                    resume_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(work_unit_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS worker_capacity_leases (
                    work_unit_id TEXT PRIMARY KEY REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    acquired_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_batches (
                    batch_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_batch_units (
                    batch_id TEXT NOT NULL REFERENCES worker_batches(batch_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    subject_id TEXT,
                    vote_index INTEGER,
                    PRIMARY KEY (batch_id, work_unit_id)
                );
                CREATE TABLE IF NOT EXISTS session_bindings (
                    session_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT,
                    attempt_id TEXT REFERENCES work_attempts(attempt_id),
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manifest_access (
                    manifest_id TEXT NOT NULL REFERENCES repository_manifests(manifest_id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES work_attempts(attempt_id),
                    session_id TEXT NOT NULL REFERENCES session_bindings(session_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    manifest_digest TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (attempt_id, manifest_id)
                );
                CREATE TABLE IF NOT EXISTS threat_models (
                    scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL UNIQUE REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threat_model_access (
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, work_unit_id)
                );
                CREATE TABLE IF NOT EXISTS knowledge_base_access (
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES session_bindings(session_id) ON DELETE CASCADE,
                    work_unit_id TEXT REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT,
                    role TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    blob_digest TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    excerpt_hash TEXT NOT NULL,
                    ordinal INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS poc_bundles (
                    poc_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status IN ('generated', 'rejected')),
                    bundle_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poc_validations (
                    validation_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    poc_id TEXT NOT NULL REFERENCES poc_bundles(poc_id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    validator TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('verified', 'unverified', 'failed', 'not_run')
                    ),
                    artifact_id TEXT,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scan_id, poc_id, validator)
                );
                CREATE TABLE IF NOT EXISTS verifications (
                    verification_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT,
                    verdict TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    counter_evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_votes (
                    vote_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    vote_index INTEGER NOT NULL CHECK (vote_index BETWEEN 1 AND 5),
                    verdict TEXT NOT NULL CHECK (
                        verdict IN ('confirmed', 'rejected', 'insufficient_evidence')
                    ),
                    rationale TEXT NOT NULL,
                    counter_evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(candidate_id, vote_index)
                );
                CREATE TABLE IF NOT EXISTS verification_subject_access (
                    attempt_id TEXT PRIMARY KEY REFERENCES work_attempts(attempt_id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    accessed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_conflicts (
                    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coverage_attestations (
                    attestation_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES work_attempts(attempt_id),
                    policy TEXT NOT NULL CHECK (
                        policy IN ('evidence_backed_partial', 'exhaustive')
                    ),
                    completeness TEXT NOT NULL CHECK (
                        completeness IN ('complete', 'partial', 'blocked')
                    ),
                    assigned_count INTEGER NOT NULL,
                    read_complete_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    unexamined_count INTEGER NOT NULL,
                    attestation_digest TEXT NOT NULL,
                    open_questions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coverage_records (
                    attestation_id TEXT NOT NULL REFERENCES coverage_attestations(attestation_id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'read_complete', 'read_partial', 'located',
                            'inventoried', 'failed', 'not_applicable',
                            'unexamined'
                        )
                    ),
                    reason TEXT,
                    receipt_digest TEXT,
                    PRIMARY KEY (attestation_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS source_access (
                    access_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL REFERENCES work_attempts(attempt_id),
                    session_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    operation TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    blob_digest TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submission_rejections (
                    rejection_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL REFERENCES work_attempts(attempt_id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    violations_json TEXT NOT NULL,
                    retryable INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adjudications (
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    adjudication_round INTEGER NOT NULL CHECK (adjudication_round IN (1, 2)),
                    action TEXT NOT NULL CHECK (action IN ('finalize', 'targeted_rescan')),
                    accepted_candidate_ids_json TEXT NOT NULL,
                    rejected_candidates_json TEXT NOT NULL,
                    rescan_json TEXT,
                    dynamic_assessments_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, adjudication_round)
                );
                CREATE TABLE IF NOT EXISTS dynamic_runs (
                    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    probe_work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (
                        status IN ('ready', 'not_runnable', 'completed', 'inconclusive')
                    ),
                    probe_json TEXT NOT NULL,
                    run_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cybergym_tasks (
                    scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('active', 'submitting', 'submitted', 'failed_no_artifact')
                    ),
                    final_artifact_id TEXT,
                    selected_poc_id TEXT REFERENCES poc_bundles(poc_id),
                    local_validation TEXT CHECK (
                        local_validation IN ('verified', 'unverified', 'failed_no_artifact')
                    ),
                    selection_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cybergym_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES cybergym_tasks(scan_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (
                        kind IN ('seed', 'corpus', 'crash', 'minimized', 'dictionary')
                    ),
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    raw_bytes BLOB NOT NULL,
                    parent_id TEXT REFERENCES cybergym_artifacts(artifact_id),
                    provenance_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(scan_id, sha256, kind, parent_id)
                );
                CREATE TABLE IF NOT EXISTS cybergym_runs (
                    run_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES cybergym_tasks(scan_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('replay', 'gdb', 'fuzz', 'minimize')),
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                    idempotency_key TEXT,
                    owner_token TEXT,
                    owner_lease_expires_at TEXT,
                    container_name TEXT,
                    input_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cybergym_budget (
                    scan_id TEXT NOT NULL REFERENCES cybergym_tasks(scan_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('replay', 'gdb', 'fuzz', 'minimize')),
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    PRIMARY KEY (scan_id, kind)
                );
                CREATE TABLE IF NOT EXISTS cybergym_submissions (
                    scan_id TEXT PRIMARY KEY REFERENCES cybergym_tasks(scan_id) ON DELETE CASCADE,
                    submission_id TEXT NOT NULL UNIQUE,
                    artifact_id TEXT NOT NULL REFERENCES cybergym_artifacts(artifact_id),
                    local_validation TEXT NOT NULL CHECK (local_validation IN ('verified', 'unverified')),
                    selection_reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    official_result_json TEXT,
                    status TEXT NOT NULL CHECK (status IN ('submitting', 'completed')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS scan_phase_runs (
                    phase_run_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    phase TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scan_id, phase, ordinal)
                );
                CREATE TABLE IF NOT EXISTS scan_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    phase_run_id TEXT REFERENCES scan_phase_runs(phase_run_id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scan_events_scan_seq
                    ON scan_events(scan_id, seq);
                CREATE INDEX IF NOT EXISTS scan_events_scan_created
                    ON scan_events(scan_id, created_at);
                CREATE INDEX IF NOT EXISTS scan_events_scan_type
                    ON scan_events(scan_id, event_type);
                """
            )
            file_columns = {row["name"] for row in connection.execute("PRAGMA table_info(snapshot_files)")}
            if "executable_mode" not in file_columns:
                connection.execute("ALTER TABLE snapshot_files ADD COLUMN executable_mode INTEGER NOT NULL DEFAULT 0")
            scan_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scans)").fetchall()}
            if "dynamic_enabled" not in scan_columns:
                connection.execute("ALTER TABLE scans ADD COLUMN dynamic_enabled INTEGER NOT NULL DEFAULT 0")
            if "poc_enabled" not in scan_columns:
                connection.execute("ALTER TABLE scans ADD COLUMN poc_enabled INTEGER NOT NULL DEFAULT 0")
            if "coverage_policy" not in scan_columns:
                connection.execute(
                    "ALTER TABLE scans ADD COLUMN coverage_policy TEXT NOT NULL "
                    "DEFAULT 'evidence_backed_partial'"
                )
            if "verification_vote_count" not in scan_columns:
                connection.execute(
                    "ALTER TABLE scans ADD COLUMN verification_vote_count "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            scan_column_definitions = (
                ("bash_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("web_search_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("owner_subject", "TEXT"),
                ("request_source", "TEXT NOT NULL DEFAULT 'cli'"),
                ("workspace_ref", "TEXT"),
                ("idempotency_key", "TEXT"),
                ("request_digest", "TEXT"),
                ("current_phase", "TEXT"),
                ("failure_code", "TEXT"),
                ("failure_summary", "TEXT"),
                ("finished_at", "TEXT"),
                ("task_owner_pid", "INTEGER"),
                ("task_owner_token", "TEXT"),
                ("task_owner_identity", "TEXT"),
                ("output_dir", "TEXT"),
                ("cleanup_intermediates", "INTEGER NOT NULL DEFAULT 0"),
                ("cleanup_summary_json", "TEXT NOT NULL DEFAULT '{}'"),
            )
            for column, definition in scan_column_definitions:
                if column not in scan_columns:
                    connection.execute(f"ALTER TABLE scans ADD COLUMN {column} {definition}")
            work_unit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(work_units)").fetchall()}
            for column, definition in (
                ("started_at", "TEXT"),
                ("finished_at", "TEXT"),
                ("assignment_digest", "TEXT"),
            ):
                if column not in work_unit_columns:
                    connection.execute(
                        f"ALTER TABLE work_units ADD COLUMN {column} {definition}"
                    )
            session_binding_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(session_bindings)"
                ).fetchall()
            }
            if "attempt_id" not in session_binding_columns:
                connection.execute(
                    "ALTER TABLE session_bindings ADD COLUMN attempt_id TEXT"
                )
            worker_batch_unit_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(worker_batch_units)"
                ).fetchall()
            }
            if "vote_index" not in worker_batch_unit_columns:
                connection.execute(
                    "ALTER TABLE worker_batch_units ADD COLUMN vote_index INTEGER"
                )
            connection.execute(
                "UPDATE worker_batch_units SET vote_index = 1 "
                "WHERE vote_index IS NULL AND work_unit_id IN ("
                "SELECT work_unit_id FROM work_units WHERE phase = 'verification'"
                ")"
            )
            manifest_access_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(manifest_access)"
                ).fetchall()
            }
            if "attempt_id" not in manifest_access_columns:
                connection.execute(
                    "ALTER TABLE manifest_access ADD COLUMN attempt_id TEXT"
                )
            source_access_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(source_access)"
                ).fetchall()
            }
            if "attempt_id" not in source_access_columns:
                connection.execute(
                    "ALTER TABLE source_access ADD COLUMN attempt_id TEXT"
                )
            cybergym_task_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(cybergym_tasks)").fetchall()
            }
            if "selected_poc_id" not in cybergym_task_columns:
                connection.execute("ALTER TABLE cybergym_tasks ADD COLUMN selected_poc_id TEXT")
            cybergym_run_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(cybergym_runs)").fetchall()
            }
            cybergym_run_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cybergym_runs'"
            ).fetchone()
            needs_cybergym_run_rebuild = (
                "idempotency_key" not in cybergym_run_columns
                or "owner_token" not in cybergym_run_columns
                or "owner_lease_expires_at" not in cybergym_run_columns
                or "container_name" not in cybergym_run_columns
                or cybergym_run_schema is None
                or "cancelled" not in str(cybergym_run_schema["sql"] or "").casefold()
            )
            if needs_cybergym_run_rebuild:
                connection.execute(
                    """
                    CREATE TABLE cybergym_runs_v6 (
                        run_id TEXT PRIMARY KEY,
                        scan_id TEXT NOT NULL REFERENCES cybergym_tasks(scan_id) ON DELETE CASCADE,
                        kind TEXT NOT NULL CHECK (kind IN ('replay', 'gdb', 'fuzz', 'minimize')),
                        status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                        idempotency_key TEXT,
                        owner_token TEXT,
                        owner_lease_expires_at TEXT,
                        container_name TEXT,
                        input_json TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                idempotency_select = (
                    "idempotency_key"
                    if "idempotency_key" in cybergym_run_columns
                    else "NULL"
                )
                owner_token_select = "owner_token" if "owner_token" in cybergym_run_columns else "NULL"
                owner_lease_select = (
                    "owner_lease_expires_at"
                    if "owner_lease_expires_at" in cybergym_run_columns
                    else "NULL"
                )
                container_name_select = (
                    "container_name" if "container_name" in cybergym_run_columns else "NULL"
                )
                connection.execute(
                    "INSERT INTO cybergym_runs_v6 "
                    "(run_id, scan_id, kind, status, idempotency_key, owner_token, "
                    "owner_lease_expires_at, container_name, input_json, result_json, created_at, updated_at) "
                    "SELECT run_id, scan_id, kind, status, "
                    f"{idempotency_select}, {owner_token_select}, {owner_lease_select}, "
                    f"{container_name_select}, input_json, result_json, created_at, updated_at FROM cybergym_runs"
                )
                connection.execute("DROP TABLE cybergym_runs")
                connection.execute("ALTER TABLE cybergym_runs_v6 RENAME TO cybergym_runs")
            legacy_bindings = connection.execute(
                """
                SELECT sb.session_id, sb.scan_id, sb.work_unit_id,
                       sb.snapshot_id, sb.role, sb.created_at,
                       wu.phase, wu.paths_json, wu.assignment_digest,
                       wu.background_task_id, wu.status, wu.started_at,
                       wu.finished_at, wu.updated_at,
                       s.ruleset_digest,
                       COALESCE(rm.manifest_digest, sn.tree_digest)
                           AS manifest_digest
                FROM session_bindings sb
                JOIN work_units wu ON wu.work_unit_id = sb.work_unit_id
                JOIN scans s ON s.scan_id = sb.scan_id
                JOIN snapshots sn ON sn.snapshot_id = sb.snapshot_id
                LEFT JOIN repository_manifests rm
                    ON rm.snapshot_id = sb.snapshot_id
                WHERE sb.role != 'coordinator' AND sb.attempt_id IS NULL
                ORDER BY sb.created_at, sb.session_id
                """
            ).fetchall()
            for legacy in legacy_bindings:
                attempt_id = f"attempt_{uuid.uuid4().hex}"
                ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 "
                        "FROM work_attempts WHERE work_unit_id = ?",
                        (legacy["work_unit_id"],),
                    ).fetchone()[0]
                )
                migrated_toolset_digest = toolset_digest(())
                migrated_scope_digest = scope_digest(
                    snapshot_id=legacy["snapshot_id"],
                    manifest_digest=legacy["manifest_digest"],
                    paths=json.loads(legacy["paths_json"]),
                    assignment_digest=legacy["assignment_digest"],
                )
                capsule = ExecutionCapsule(
                    scan_id=legacy["scan_id"],
                    snapshot_id=legacy["snapshot_id"],
                    work_unit_id=legacy["work_unit_id"],
                    attempt_id=attempt_id,
                    phase=legacy["phase"],
                    role=legacy["role"],
                    agent_name=WORKER_ROLE_AGENTS[legacy["role"]],
                    session_id=legacy["session_id"],
                    provider_id=None,
                    model_id=None,
                    toolset_digest=migrated_toolset_digest,
                    ruleset_digest=legacy["ruleset_digest"],
                    scope_digest=migrated_scope_digest,
                )
                connection.execute(
                    """
                    INSERT INTO work_attempts (
                        attempt_id, work_unit_id, ordinal, session_id,
                        background_task_id, agent_name, provider_id, model_id,
                        toolset_digest, scope_digest, capsule_digest, status,
                        failure_class, resume_count, created_at, updated_at,
                        started_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?,
                              NULL, 0, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        legacy["work_unit_id"],
                        ordinal,
                        legacy["session_id"],
                        legacy["background_task_id"],
                        WORKER_ROLE_AGENTS[legacy["role"]],
                        migrated_toolset_digest,
                        migrated_scope_digest,
                        capsule.digest(),
                        legacy["status"],
                        legacy["created_at"],
                        legacy["updated_at"],
                        legacy["started_at"],
                        legacy["finished_at"],
                    ),
                )
                connection.execute(
                    "UPDATE session_bindings SET attempt_id = ? "
                    "WHERE session_id = ?",
                    (attempt_id, legacy["session_id"]),
                )
                connection.execute(
                    "UPDATE source_access SET attempt_id = ? "
                    "WHERE session_id = ? AND attempt_id IS NULL",
                    (attempt_id, legacy["session_id"]),
                )
                connection.execute(
                    "UPDATE manifest_access SET attempt_id = ? "
                    "WHERE session_id = ? AND attempt_id IS NULL",
                    (attempt_id, legacy["session_id"]),
                )
            self._migrate_legacy_coverage(connection)
            connection.execute(
                "INSERT OR IGNORE INTO worker_capacity_leases (work_unit_id, acquired_at) "
                "SELECT DISTINCT wa.work_unit_id, ? FROM work_attempts wa "
                "JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id "
                "JOIN scans s ON s.scan_id = wu.scan_id "
                "WHERE wa.status IN ('pending', 'running', 'recovering') "
                "AND wu.status IN ('pending', 'running') "
                "AND s.status IN ('running', 'reducing', 'cancelling')",
                (_now(),),
            )
            connection.execute(
                "DELETE FROM coverage_attestations WHERE EXISTS ("
                "SELECT 1 FROM coverage_attestations newer "
                "WHERE newer.work_unit_id = coverage_attestations.work_unit_id "
                "AND newer.attempt_id = coverage_attestations.attempt_id "
                "AND (newer.created_at > coverage_attestations.created_at "
                "OR (newer.created_at = coverage_attestations.created_at "
                "AND newer.attestation_id > coverage_attestations.attestation_id))"
                ")"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS coverage_attestations_unit_attempt "
                "ON coverage_attestations(work_unit_id, attempt_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS work_attempts_one_active "
                "ON work_attempts(work_unit_id) "
                "WHERE status IN ('pending', 'running', 'recovering')"
            )
            self._reconcile_duplicate_active_worker_batches(connection)
            # The read-before-insert check in create_worker_batch is useful for
            # diagnostics, but it is not a cross-process synchronization
            # primitive.  Keep the invariant in SQLite as well so two runner
            # processes cannot create the same active phase concurrently.
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS worker_batches_one_active_phase "
                "ON worker_batches(scan_id, phase) "
                "WHERE status IN ('pending', 'running')"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS source_access_attempt_path_op "
                "ON source_access(attempt_id, relative_path, operation)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS submission_rejections_attempt_created "
                "ON submission_rejections(attempt_id, created_at, rejection_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS coverage_attestations_scan_unit "
                "ON coverage_attestations(scan_id, work_unit_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS coverage_records_state "
                "ON coverage_records(attestation_id, state)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS verification_votes_scan_candidate "
                "ON verification_votes(scan_id, candidate_id, vote_index)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS cybergym_artifacts_scan_created "
                "ON cybergym_artifacts(scan_id, created_at, artifact_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS cybergym_runs_scan_created "
                "ON cybergym_runs(scan_id, created_at, run_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS cybergym_runs_idempotency "
                "ON cybergym_runs(scan_id, kind, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS poc_bundles_scan_created "
                "ON poc_bundles(scan_id, created_at, candidate_id)"
            )
            connection.execute(
                "UPDATE work_units SET started_at = created_at WHERE started_at IS NULL AND status != 'pending'"
            )
            connection.execute(
                "UPDATE work_units SET finished_at = updated_at "
                "WHERE finished_at IS NULL "
                "AND status IN ('completed', 'failed', 'cancelled')"
            )
            connection.execute(
                "UPDATE scans SET finished_at = updated_at "
                "WHERE finished_at IS NULL "
                "AND status IN ('completed', 'failed', 'cancelled', 'interrupted')"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS scans_owner_idempotency "
                "ON scans(owner_subject, idempotency_key) "
                "WHERE owner_subject IS NOT NULL AND idempotency_key IS NOT NULL"
            )
            adjudication_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(adjudications)").fetchall()
            }
            if "dynamic_assessments_json" not in adjudication_columns:
                connection.execute("ALTER TABLE adjudications ADD COLUMN dynamic_assessments_json TEXT")
            snapshot_columns = {row["name"] for row in connection.execute("PRAGMA table_info(snapshots)").fetchall()}
            if "omitted_file_count" not in snapshot_columns:
                connection.execute("ALTER TABLE snapshots ADD COLUMN omitted_file_count INTEGER NOT NULL DEFAULT 0")
            for column, definition in (
                ("target_kind", "TEXT NOT NULL DEFAULT 'directory_snapshot'"),
                ("display_name", "TEXT NOT NULL DEFAULT 'snapshot'"),
                ("include_paths_json", "TEXT NOT NULL DEFAULT '[\".\"]'"),
                ("exclude_patterns_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("copy_source", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if column not in snapshot_columns:
                    connection.execute(f"ALTER TABLE snapshots ADD COLUMN {column} {definition}")
            evidence_columns = {row["name"] for row in connection.execute("PRAGMA table_info(evidence)").fetchall()}
            if "ordinal" not in evidence_columns:
                connection.execute("ALTER TABLE evidence ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0")
                connection.execute("UPDATE evidence SET ordinal = rowid")
            duplicate_candidates = connection.execute(
                "SELECT candidate_id FROM verifications GROUP BY candidate_id HAVING COUNT(*) > 1"
            ).fetchall()
            for duplicate in duplicate_candidates:
                candidate_id = duplicate["candidate_id"]
                rows = connection.execute(
                    "SELECT * FROM verifications WHERE candidate_id = ? ORDER BY created_at, verification_id",
                    (candidate_id,),
                ).fetchall()
                serialized = [dict(row) for row in rows]
                connection.execute(
                    "INSERT OR IGNORE INTO verification_conflicts VALUES (?, ?, ?)",
                    (
                        candidate_id,
                        json.dumps(serialized, ensure_ascii=False, sort_keys=True),
                        _now(),
                    ),
                )
                connection.execute(
                    "DELETE FROM verifications WHERE candidate_id = ? AND verification_id != ?",
                    (candidate_id, rows[0]["verification_id"]),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS verifications_one_per_candidate ON verifications(candidate_id)"
            )
            connection.execute("DROP INDEX IF EXISTS verification_subject_once")
            connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
            self._restrict_database_files()

    @staticmethod
    def _reconcile_duplicate_active_worker_batches(connection: sqlite3.Connection) -> None:
        """Make a pre-v7 duplicate batch state safe to constrain.

        Older releases only checked phase exclusivity in application code, so a
        database shared by two parents can contain duplicate active batches.
        Keep the oldest batch as the established owner and terminalize later
        duplicate units before creating the partial unique index.  This is a
        migration repair, not normal scheduling behaviour.
        """
        duplicates = connection.execute(
            "SELECT scan_id, phase FROM worker_batches "
            "WHERE status IN ('pending', 'running') "
            "GROUP BY scan_id, phase HAVING COUNT(*) > 1"
        ).fetchall()
        now = _now()
        for duplicate in duplicates:
            batches = connection.execute(
                "SELECT batch_id FROM worker_batches WHERE scan_id = ? AND phase = ? "
                "AND status IN ('pending', 'running') ORDER BY created_at, batch_id",
                (duplicate["scan_id"], duplicate["phase"]),
            ).fetchall()
            for row in batches[1:]:
                batch_id = row["batch_id"]
                unit_rows = connection.execute(
                    "SELECT work_unit_id FROM worker_batch_units WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
                unit_ids = [item["work_unit_id"] for item in unit_rows]
                connection.execute(
                    "UPDATE worker_batches SET status = 'failed', updated_at = ? "
                    "WHERE batch_id = ? AND status IN ('pending', 'running')",
                    (now, batch_id),
                )
                if unit_ids:
                    placeholders = ", ".join("?" for _ in unit_ids)
                    connection.execute(
                        "UPDATE work_units SET status = 'failed', finished_at = COALESCE(finished_at, ?), "
                        "updated_at = ? WHERE work_unit_id IN (" + placeholders + ") "
                        "AND status IN ('pending', 'running')",
                        (now, now, *unit_ids),
                    )
                    connection.execute(
                        "UPDATE work_attempts SET status = 'failed', "
                        "failure_class = COALESCE(failure_class, 'duplicate_active_batch_recovered'), "
                        "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                        "WHERE work_unit_id IN (" + placeholders + ") "
                        "AND status IN ('pending', 'running', 'recovering')",
                        (now, now, *unit_ids),
                    )
                connection.execute(
                    "INSERT INTO scan_events "
                    "(scan_id, phase_run_id, event_type, level, title, payload_json, created_at) "
                    "VALUES (?, NULL, 'worker.batch_recovered', 'warning', ?, ?, ?)",
                    (
                        duplicate["scan_id"],
                        "Recovered duplicate active worker batch",
                        json.dumps(
                            {"batch_id": batch_id, "phase": duplicate["phase"]},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )

    def save_snapshot(
        self,
        snapshot: SnapshotRef,
        files: Iterable[SnapshotFile],
        omissions: Iterable[SnapshotOmission] = (),
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, repository_identity, source_revision,
                    target_kind, display_name, include_paths_json,
                    exclude_patterns_json,
                    tree_digest, scope_digest, file_count, total_bytes,
                    created_at, root_path, omitted_file_count, copy_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.repository_identity,
                    snapshot.source_revision,
                    snapshot.target_kind,
                    snapshot.display_name,
                    json.dumps(snapshot.include_paths, ensure_ascii=False),
                    json.dumps(snapshot.exclude_patterns, ensure_ascii=False),
                    snapshot.tree_digest,
                    snapshot.scope_digest,
                    snapshot.file_count,
                    snapshot.total_bytes,
                    snapshot.created_at,
                    snapshot.root_path,
                    snapshot.omitted_file_count,
                    int(snapshot.copy_source),
                ),
            )
            connection.executemany(
                """
                INSERT INTO snapshot_files (snapshot_id, relative_path, blob_digest, size_bytes, line_count, language, is_binary, executable_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.snapshot_id,
                        item.relative_path,
                        item.blob_digest,
                        item.size_bytes,
                        item.line_count,
                        item.language,
                        int(item.is_binary),
                        item.executable_mode,
                    )
                    for item in files
                ],
            )
            connection.executemany(
                "INSERT INTO snapshot_omissions VALUES (?, ?, ?, ?)",
                [
                    (
                        snapshot.snapshot_id,
                        item.relative_path,
                        item.reason,
                        item.size_bytes,
                    )
                    for item in omissions
                ],
            )

    def get_snapshot(self, snapshot_id: str) -> SnapshotRef | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["include_paths"] = tuple(json.loads(payload.pop("include_paths_json")))
        payload["exclude_patterns"] = tuple(json.loads(payload.pop("exclude_patterns_json")))
        payload["copy_source"] = bool(payload["copy_source"])
        return SnapshotRef(**payload)

    def save_repository_manifest(self, manifest: RepositoryManifest) -> None:
        with self._lock, self._connect() as connection:
            snapshot = connection.execute(
                "SELECT file_count, total_bytes, omitted_file_count FROM snapshots WHERE snapshot_id = ?",
                (manifest.snapshot_id,),
            ).fetchone()
            if snapshot is None:
                raise ValueError("Manifest snapshot not found")
            if (
                manifest.file_count != snapshot["file_count"]
                or manifest.total_bytes != snapshot["total_bytes"]
                or manifest.omitted_file_count != snapshot["omitted_file_count"]
            ):
                raise ValueError("Repository manifest counts do not match the snapshot")
            connection.execute(
                "INSERT INTO repository_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(snapshot_id) DO NOTHING",
                (
                    manifest.manifest_id,
                    manifest.snapshot_id,
                    manifest.manifest_digest,
                    manifest.file_count,
                    manifest.total_bytes,
                    manifest.omitted_file_count,
                    json.dumps(dict(manifest.languages), ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        [item.public_dict() for item in manifest.components],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    manifest.created_at,
                ),
            )
            persisted = connection.execute(
                "SELECT manifest_id, manifest_digest FROM repository_manifests WHERE snapshot_id = ?",
                (manifest.snapshot_id,),
            ).fetchone()
            if persisted is None or (
                persisted["manifest_id"] != manifest.manifest_id
                or persisted["manifest_digest"] != manifest.manifest_digest
            ):
                raise ValueError("Snapshot already has a different repository manifest")

    def get_repository_manifest(self, snapshot_id: str) -> RepositoryManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_manifests WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        languages = json.loads(row["languages_json"])
        components = json.loads(row["components_json"])
        return RepositoryManifest(
            manifest_id=row["manifest_id"],
            snapshot_id=row["snapshot_id"],
            manifest_digest=row["manifest_digest"],
            file_count=row["file_count"],
            total_bytes=row["total_bytes"],
            omitted_file_count=row["omitted_file_count"],
            languages=tuple(sorted((str(key), int(value)) for key, value in languages.items())),
            components=tuple(ManifestComponent(**item) for item in components),
            created_at=row["created_at"],
            files=tuple(self.list_snapshot_files(snapshot_id)),
            omissions=tuple(self.list_snapshot_omissions(snapshot_id)),
        )

    def list_snapshot_files(self, snapshot_id: str) -> list[SnapshotFile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, blob_digest, size_bytes, line_count, language, is_binary, executable_mode
                FROM snapshot_files WHERE snapshot_id = ? ORDER BY relative_path
                """,
                (snapshot_id,),
            ).fetchall()
        return [
            SnapshotFile(
                relative_path=row["relative_path"],
                blob_digest=row["blob_digest"],
                size_bytes=row["size_bytes"],
                line_count=row["line_count"],
                language=row["language"],
                is_binary=bool(row["is_binary"]),
                executable_mode=row["executable_mode"],
            )
            for row in rows
        ]

    def list_snapshot_omissions(self, snapshot_id: str) -> list[SnapshotOmission]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, reason, size_bytes
                FROM snapshot_omissions
                WHERE snapshot_id = ? ORDER BY relative_path
                """,
                (snapshot_id,),
            ).fetchall()
        return [SnapshotOmission(**dict(row)) for row in rows]

    def get_snapshot_file(self, snapshot_id: str, relative_path: str) -> SnapshotFile | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relative_path, blob_digest, size_bytes, line_count, language, is_binary, executable_mode
                FROM snapshot_files WHERE snapshot_id = ? AND relative_path = ?
                """,
                (snapshot_id, relative_path),
            ).fetchone()
        if not row:
            return None
        return SnapshotFile(
            relative_path=row["relative_path"],
            blob_digest=row["blob_digest"],
            size_bytes=row["size_bytes"],
            line_count=row["line_count"],
            language=row["language"],
            is_binary=bool(row["is_binary"]),
            executable_mode=row["executable_mode"],
        )

    def create_scan(
        self,
        *,
        parent_session_id: str,
        snapshot_id: str,
        mode: str,
        ruleset_digest: str,
        cleanup_intermediates: bool = False,
        dynamic_enabled: bool = False,
        poc_enabled: bool = False,
        coverage_policy: str = "evidence_backed_partial",
        verification_vote_count: int = 1,
        owner_subject: str | None = None,
        request_source: str = "cli",
        workspace_ref: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
        task_owner_pid: int | None = None,
        task_owner_token: str | None = None,
        task_owner_identity: str | None = None,
    ) -> str:
        if type(cleanup_intermediates) is not bool:
            raise ValueError("cleanup_intermediates must be a boolean")
        if coverage_policy not in {"evidence_backed_partial", "exhaustive"}:
            raise ValueError("Unsupported coverage policy")
        if (
            not isinstance(verification_vote_count, int)
            or isinstance(verification_vote_count, bool)
            or not 1 <= verification_vote_count <= 5
        ):
            raise ValueError("verification_vote_count must be between 1 and 5")
        scan_id = f"scan_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO scans ("
                "scan_id, parent_session_id, snapshot_id, mode, "
                "dynamic_enabled, poc_enabled, coverage_policy, verification_vote_count, "
                "status, ruleset_digest, created_at, updated_at, "
                "owner_subject, request_source, workspace_ref, idempotency_key, "
                "request_digest, current_phase, task_owner_pid, task_owner_token, "
                "task_owner_identity, cleanup_intermediates"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    parent_session_id,
                    snapshot_id,
                    mode,
                    int(bool(dynamic_enabled)),
                    int(bool(poc_enabled)),
                    coverage_policy,
                    verification_vote_count,
                    "running",
                    ruleset_digest,
                    now,
                    now,
                    owner_subject,
                    request_source,
                    workspace_ref,
                    idempotency_key,
                    request_digest,
                    "snapshot",
                    task_owner_pid,
                    task_owner_token,
                    task_owner_identity,
                    int(cleanup_intermediates),
                ),
            )
        return scan_id

    def create_work_unit(
        self,
        *,
        scan_id: str,
        phase: str,
        role: str,
        paths: list[str],
        status: str = "pending",
        assignment_digest: str | None = None,
    ) -> str:
        if role not in {"threat_modeler", "baseline", "investigator", "verifier", "prober", "cybergym_solver", "poc_generator"}:
            raise ValueError("Unsupported work-unit role")
        if not paths or not all(isinstance(path, str) and path for path in paths):
            raise ValueError("Work units require at least one path")
        if status not in {"pending", "running", "completed", "failed", "cancelled"}:
            raise ValueError("Unsupported work-unit status")
        if assignment_digest is not None and (
            len(assignment_digest) != 64
            or any(character not in "0123456789abcdef" for character in assignment_digest)
        ):
            raise ValueError("assignment_digest must be a lowercase SHA-256 digest")
        work_unit_id = f"unit_{uuid.uuid4().hex}"
        now = _now()
        started_at = now if status != "pending" else None
        finished_at = now if status in {"completed", "failed", "cancelled"} else None
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            connection.execute(
                """
                INSERT INTO work_units (
                    work_unit_id, scan_id, phase, role, paths_json,
                    session_id, background_task_id, status, created_at,
                    updated_at, started_at, finished_at, assignment_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_unit_id,
                    scan_id,
                    phase,
                    role,
                    json.dumps(paths, ensure_ascii=False, sort_keys=True),
                    None,
                    None,
                    status,
                    now,
                    now,
                    started_at,
                    finished_at,
                    assignment_digest,
                ),
            )
        return work_unit_id

    def create_worker_batch(
        self,
        *,
        scan_id: str,
        phase: str,
        units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if phase not in {
            "threat_modeling",
            "baseline",
            "investigation",
            "verification",
            "probing",
            "targeted_rescan",
            "cybergym_solving",
            "poc_generation",
        }:
            raise ValueError("Unsupported worker phase")
        if not units or len(units) > 32:
            raise ValueError("A worker batch must contain between 1 and 32 units")
        batch_id = f"batch_{uuid.uuid4().hex}"
        now = _now()
        created_units: list[dict[str, Any]] = []
        with self._lock, self._connect() as connection:
            # Serialize the phase-exclusivity check with the insert.  The
            # partial unique index below is the final cross-process guard;
            # this immediate transaction makes the error deterministic and
            # avoids constructing a half batch before that guard fires.
            connection.execute("BEGIN IMMEDIATE")
            scan = self._require_scan_status(connection, scan_id, {"running"})
            if phase == "probing" and not bool(scan["dynamic_enabled"]):
                raise ValueError("Dynamic validation is not enabled for this scan")
            if phase == "cybergym_solving" and scan["mode"] != "cybergym_level1":
                raise ValueError("CyberGym solver requires cybergym_level1 scan mode")
            if phase == "poc_generation" and not bool(scan["poc_enabled"]):
                raise ValueError("PoC generation is not enabled for this scan")
            active = connection.execute(
                "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = ? AND status IN ('pending', 'running')",
                (scan_id, phase),
            ).fetchone()
            if active is not None:
                raise ValueError(f"A {phase} worker batch is already active")
            if phase == "threat_modeling":
                existing_model = connection.execute(
                    "SELECT 1 FROM threat_models WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()
                if existing_model is not None:
                    raise ValueError("Threat model has already been created")
            if phase == "baseline":
                self._require_threat_model_ready(connection, scan_id)
                if len(units) != 1 or units[0].get("paths") != ["."]:
                    raise ValueError("Standard baseline must be one repository-level work unit")
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'baseline'",
                    (scan_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("Baseline workers have already been created")
            if phase == "investigation":
                self._require_threat_model_ready(connection, scan_id)
                baseline = connection.execute(
                    "SELECT status FROM work_units WHERE scan_id = ? AND phase = 'baseline'",
                    (scan_id,),
                ).fetchall()
                if len(baseline) != 1 or baseline[0]["status"] in {"pending", "running"}:
                    raise ValueError("Investigation requires one terminated baseline work unit")
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'investigation'",
                    (scan_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("A focused investigator has already been created")
                if len(units) != 1:
                    raise ValueError("Investigation must contain exactly one work unit")
            if phase == "verification":
                analysis = connection.execute(
                    "SELECT role, status FROM work_units WHERE scan_id = ? "
                    "AND role IN ('baseline', 'investigator')",
                    (scan_id,),
                ).fetchall()
                if not any(item["role"] == "baseline" for item in analysis):
                    raise ValueError("Verification requires a baseline analysis work unit")
                if any(item["status"] in {"pending", "running"} for item in analysis):
                    raise ValueError("Verification requires all analysis workers to terminate")
            if phase == "targeted_rescan":
                self._require_threat_model_ready(connection, scan_id)
                directive = self._require_targeted_rescan_directive(
                    connection,
                    scan_id,
                )
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'targeted_rescan'",
                    (scan_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("A targeted rescan has already been created")
                if len(units) != 1 or units[0].get("paths") != directive["paths"]:
                    raise ValueError("Targeted-rescan scope must exactly match the adjudication")
            if phase == "cybergym_solving":
                task = connection.execute(
                    "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
                ).fetchone()
                finalized = connection.execute(
                    "SELECT 1 FROM adjudications WHERE scan_id = ? AND action = 'finalize'",
                    (scan_id,),
                ).fetchone()
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'cybergym_solving'",
                    (scan_id,),
                ).fetchone()
                if task is None or task["status"] != "active" or finalized is None or prior is not None:
                    raise ValueError("CyberGym solving requires one active task after final adjudication")
                if len(units) != 1 or units[0].get("paths") != ["."]:
                    raise ValueError("CyberGym solving requires exactly one task-level work unit")
            if phase == "poc_generation":
                finalized = connection.execute(
                    "SELECT 1 FROM adjudications WHERE scan_id = ? AND action = 'finalize'",
                    (scan_id,),
                ).fetchone()
                if finalized is None:
                    raise ValueError("PoC generation requires final static adjudication")
            connection.execute(
                "INSERT INTO worker_batches VALUES (?, ?, ?, ?, ?, ?)",
                (batch_id, scan_id, phase, "pending", now, now),
            )
            for unit in units:
                role = str(unit.get("role") or "")
                paths = unit.get("paths")
                subject_id = unit.get("subject_id")
                vote_index = unit.get("vote_index")
                assignment_digest = unit.get("assignment_digest")
                if role not in {"threat_modeler", "baseline", "investigator", "verifier", "prober", "cybergym_solver", "poc_generator"}:
                    raise ValueError("Unsupported work-unit role")
                expected_role = {
                    "threat_modeling": "threat_modeler",
                    "baseline": "baseline",
                    "investigation": "investigator",
                    "verification": "verifier",
                    "probing": "prober",
                    "targeted_rescan": "baseline",
                    "cybergym_solving": "cybergym_solver",
                    "poc_generation": "poc_generator",
                }[phase]
                if role != expected_role:
                    raise ValueError("Work-unit role does not match its phase")
                if (
                    not isinstance(paths, list)
                    or not paths
                    or len(paths) > 2_000
                    or not all(isinstance(path, str) and path for path in paths)
                ):
                    raise ValueError("Work units require between 1 and 2000 paths")
                if phase in {"verification", "probing", "poc_generation"} and not subject_id:
                    raise ValueError(f"{phase.title()} work units require a candidate subject")
                if phase not in {"verification", "probing", "poc_generation"} and subject_id is not None:
                    raise ValueError("Only verification, probing, and PoC-generation work units may have a subject")
                if phase == "verification":
                    required_votes = int(scan["verification_vote_count"])
                    if (
                        not isinstance(vote_index, int)
                        or isinstance(vote_index, bool)
                        or not 1 <= vote_index <= required_votes
                    ):
                        raise ValueError("Verification work units require a valid vote_index")
                elif vote_index is not None:
                    raise ValueError("Only verification work units accept vote_index")
                if phase == "baseline" and (
                    not isinstance(assignment_digest, str)
                    or len(assignment_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in assignment_digest
                    )
                ):
                    raise ValueError(
                        "Baseline work units require a valid assignment_digest"
                    )
                if phase != "baseline" and assignment_digest is not None:
                    raise ValueError(
                        "Only baseline work units accept assignment_digest"
                    )
                if phase == "investigation":
                    snapshot_paths = {
                        row["relative_path"]
                        for row in connection.execute(
                            "SELECT relative_path FROM snapshot_files WHERE snapshot_id = ? "
                            "UNION SELECT relative_path FROM snapshot_omissions WHERE snapshot_id = ?",
                            (scan["snapshot_id"], scan["snapshot_id"]),
                        ).fetchall()
                    }
                    invalid_paths = sorted(set(paths) - snapshot_paths)
                    if invalid_paths:
                        raise ValueError(
                            "Investigation paths must be exact snapshot paths: "
                            + ", ".join(invalid_paths[:20])
                        )
                if subject_id is not None:
                    candidate = connection.execute(
                        "SELECT scan_id FROM candidates WHERE candidate_id = ?",
                        (subject_id,),
                    ).fetchone()
                    if candidate is None or candidate["scan_id"] != scan_id:
                        raise ValueError("Work-unit subject does not belong to the scan")
                    if phase == "probing":
                        eligible = connection.execute(
                            """
                            SELECT 1 FROM verifications v
                            LEFT JOIN dynamic_runs d ON d.candidate_id = v.candidate_id
                            WHERE v.candidate_id = ? AND v.scan_id = ?
                              AND v.verdict = 'confirmed' AND d.candidate_id IS NULL
                            """,
                            (subject_id, scan_id),
                        ).fetchone()
                        if eligible is None:
                            raise ValueError("Probing requires a confirmed candidate without a dynamic record")
                    if phase == "poc_generation":
                        eligible = connection.execute(
                            """
                            SELECT 1 FROM verifications v
                            LEFT JOIN poc_bundles p ON p.candidate_id = v.candidate_id
                            WHERE v.candidate_id = ? AND v.scan_id = ?
                              AND v.verdict = 'confirmed' AND p.candidate_id IS NULL
                              AND EXISTS (
                                SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                                WHERE a.scan_id = v.scan_id AND a.action = 'finalize'
                                  AND a.adjudication_round = (
                                    SELECT MAX(latest.adjudication_round)
                                    FROM adjudications latest
                                    WHERE latest.scan_id = v.scan_id AND latest.action = 'finalize'
                                  )
                                  AND accepted.value = v.candidate_id
                              )
                            """,
                            (subject_id, scan_id),
                        ).fetchone()
                        if eligible is None:
                            raise ValueError("PoC generation requires a confirmed candidate without a prior bundle")
                        evidence_paths = connection.execute(
                            "SELECT 1 FROM evidence WHERE candidate_id = ? LIMIT 1",
                            (subject_id,),
                        ).fetchone()
                        if evidence_paths is None or paths != ["."]:
                            raise ValueError(
                                "PoC-generation scope must provide repository context for the candidate"
                            )
                work_unit_id = f"unit_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO work_units (
                        work_unit_id, scan_id, phase, role, paths_json,
                        session_id, background_task_id, status, created_at,
                        updated_at, started_at, finished_at, assignment_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work_unit_id,
                        scan_id,
                        phase,
                        role,
                        json.dumps(paths, ensure_ascii=False, sort_keys=True),
                        None,
                        None,
                        "pending",
                        now,
                        now,
                        None,
                        None,
                        assignment_digest,
                    ),
                )
                connection.execute(
                    "INSERT INTO worker_batch_units "
                    "(batch_id, work_unit_id, subject_id, vote_index) "
                    "VALUES (?, ?, ?, ?)",
                    (batch_id, work_unit_id, subject_id, vote_index),
                )
                created_units.append(
                    {
                        "work_unit_id": work_unit_id,
                        "role": role,
                        "paths": paths,
                        "subject_id": subject_id,
                        "vote_index": vote_index,
                        "assignment_digest": assignment_digest,
                    }
                )
        return {
            "batch_id": batch_id,
            "scan_id": scan_id,
            "phase": phase,
            "status": "pending",
            "units": created_units,
        }

    def get_worker_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            batch = connection.execute(
                "SELECT * FROM worker_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                return None
            units = connection.execute(
                """
                SELECT wu.*, wbu.subject_id, wbu.vote_index,
                       wa.attempt_id AS latest_attempt_id,
                       wa.ordinal AS attempt_ordinal,
                       wa.session_id AS attempt_session_id,
                       wa.background_task_id AS attempt_background_task_id,
                       wa.status AS attempt_status,
                       wa.failure_class AS attempt_failure_class,
                       wa.resume_count AS attempt_resume_count,
                       wa.agent_name AS attempt_agent_name,
                       wa.provider_id AS attempt_provider_id,
                       wa.model_id AS attempt_model_id,
                       wa.toolset_digest AS attempt_toolset_digest,
                       wa.scope_digest AS attempt_scope_digest,
                       wa.capsule_digest AS attempt_capsule_digest,
                       wa.started_at AS attempt_started_at,
                       wa.finished_at AS attempt_finished_at
                FROM worker_batch_units wbu
                JOIN work_units wu ON wu.work_unit_id = wbu.work_unit_id
                LEFT JOIN work_attempts wa ON wa.attempt_id = (
                    SELECT nested.attempt_id FROM work_attempts nested
                    WHERE nested.work_unit_id = wu.work_unit_id
                    ORDER BY nested.ordinal DESC LIMIT 1
                )
                WHERE wbu.batch_id = ? ORDER BY wu.created_at, wu.work_unit_id
                """,
                (batch_id,),
            ).fetchall()
        output = dict(batch)
        output["units"] = []
        for row in units:
            item = dict(row)
            item["paths"] = json.loads(item.pop("paths_json"))
            item["attempt_id"] = item.pop("latest_attempt_id")
            item["session_id"] = item.pop("attempt_session_id") or item["session_id"]
            item["background_task_id"] = (
                item.pop("attempt_background_task_id")
                or item["background_task_id"]
            )
            item["attempt_failure_class"] = item.pop("attempt_failure_class")
            item["resume_count"] = item.pop("attempt_resume_count")
            item["agent_name"] = item.pop("attempt_agent_name")
            item["provider_id"] = item.pop("attempt_provider_id")
            item["model_id"] = item.pop("attempt_model_id")
            item["toolset_digest"] = item.pop("attempt_toolset_digest")
            item["scope_digest"] = item.pop("attempt_scope_digest")
            item["capsule_digest"] = item.pop("attempt_capsule_digest")
            item["started_at"] = item.pop("attempt_started_at") or item["started_at"]
            item["finished_at"] = (
                item.pop("attempt_finished_at") or item["finished_at"]
            )
            output["units"].append(item)
        return output

    def list_worker_batches(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT batch_id FROM worker_batches WHERE scan_id = ? ORDER BY created_at, batch_id",
                (scan_id,),
            ).fetchall()
        return [batch for row in rows if (batch := self.get_worker_batch(row["batch_id"])) is not None]

    def create_work_attempt(
        self,
        *,
        work_unit_id: str,
        session_id: str,
        agent_name: str,
        provider_id: str | None = None,
        model_id: str | None = None,
        toolset_digest_value: str | None = None,
    ) -> dict[str, Any]:
        if not session_id or not agent_name:
            raise ValueError("Work attempts require a session and agent identity")
        persisted_toolset_digest = toolset_digest_value or toolset_digest(())
        if (
            len(persisted_toolset_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in persisted_toolset_digest
            )
        ):
            raise ValueError("toolset_digest must be a lowercase SHA-256 digest")
        now = _now()
        attempt_id = f"attempt_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT wu.*, s.snapshot_id AS scan_snapshot_id,
                       s.ruleset_digest, s.status AS scan_status,
                       COALESCE(rm.manifest_digest, sn.tree_digest)
                           AS manifest_digest
                FROM work_units wu
                JOIN scans s ON s.scan_id = wu.scan_id
                JOIN snapshots sn ON sn.snapshot_id = s.snapshot_id
                LEFT JOIN repository_manifests rm
                    ON rm.snapshot_id = s.snapshot_id
                WHERE wu.work_unit_id = ?
                """,
                (work_unit_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Work unit not found")
            if row["scan_status"] != "running":
                raise ValueError("Only running scans may create work attempts")
            if row["status"] not in {"pending", "running"}:
                raise ValueError("Terminal work units cannot create fresh attempts")
            active = connection.execute(
                "SELECT 1 FROM work_attempts WHERE work_unit_id = ? "
                "AND status IN ('pending', 'running', 'recovering')",
                (work_unit_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("Work unit is already bound to an active attempt")
            self._reserve_worker_capacity(connection, work_unit_id, now)
            ordinal = int(
                connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM work_attempts "
                    "WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()[0]
            )
            if ordinal > MAX_FRESH_ATTEMPTS:
                raise ValueError("ATTEMPTS_EXHAUSTED: fresh attempt budget exhausted")
            paths = json.loads(row["paths_json"])
            persisted_scope_digest = scope_digest(
                snapshot_id=row["scan_snapshot_id"],
                manifest_digest=row["manifest_digest"],
                paths=paths,
                assignment_digest=row["assignment_digest"],
            )
            capsule = ExecutionCapsule(
                scan_id=row["scan_id"],
                snapshot_id=row["scan_snapshot_id"],
                work_unit_id=work_unit_id,
                attempt_id=attempt_id,
                phase=row["phase"],
                role=row["role"],
                agent_name=agent_name,
                session_id=session_id,
                provider_id=provider_id,
                model_id=model_id,
                toolset_digest=persisted_toolset_digest,
                ruleset_digest=row["ruleset_digest"],
                scope_digest=persisted_scope_digest,
            )
            connection.execute(
                """
                INSERT INTO work_attempts (
                    attempt_id, work_unit_id, ordinal, session_id,
                    background_task_id, agent_name, provider_id, model_id,
                    toolset_digest, scope_digest, capsule_digest, status,
                    failure_class, resume_count, created_at, updated_at,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'running',
                          NULL, 0, ?, ?, ?, NULL)
                """,
                (
                    attempt_id,
                    work_unit_id,
                    ordinal,
                    session_id,
                    agent_name,
                    provider_id,
                    model_id,
                    persisted_toolset_digest,
                    persisted_scope_digest,
                    capsule.digest(),
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_bindings (
                    session_id, scan_id, work_unit_id, attempt_id,
                    snapshot_id, role, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row["scan_id"],
                    work_unit_id,
                    attempt_id,
                    row["scan_snapshot_id"],
                    row["role"],
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE work_units SET status = 'running', updated_at = ?, "
                "started_at = COALESCE(started_at, ?) "
                "WHERE work_unit_id = ? AND status IN ('pending', 'running')",
                (now, now, work_unit_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work unit changed while creating its attempt")
        attempt = self.get_work_attempt(attempt_id)
        if attempt is None:
            raise ValueError("Work attempt was not persisted")
        return attempt

    def _reserve_worker_capacity(
        self,
        connection: sqlite3.Connection,
        work_unit_id: str,
        acquired_at: str,
    ) -> bool:
        connection.execute(
            "DELETE FROM worker_capacity_leases WHERE work_unit_id IN ("
            "SELECT leases.work_unit_id FROM worker_capacity_leases leases "
            "JOIN work_units wu ON wu.work_unit_id = leases.work_unit_id "
            "JOIN scans s ON s.scan_id = wu.scan_id "
            "WHERE wu.status NOT IN ('pending', 'running') "
            "OR s.status NOT IN ('running', 'reducing', 'cancelling')"
            ")"
        )
        existing = connection.execute(
            "SELECT 1 FROM worker_capacity_leases WHERE work_unit_id = ?",
            (work_unit_id,),
        ).fetchone()
        if existing is not None:
            return False
        active = int(
            connection.execute(
                "SELECT COUNT(*) FROM worker_capacity_leases leases "
                "JOIN work_units wu ON wu.work_unit_id = leases.work_unit_id "
                "JOIN scans s ON s.scan_id = wu.scan_id "
                "WHERE wu.status IN ('pending', 'running') "
                "AND s.status IN ('running', 'reducing', 'cancelling')"
            ).fetchone()[0]
        )
        if active >= self.worker_limit:
            raise WorkerCapacityUnavailable(f"Global code-audit worker capacity is {self.worker_limit}")
        connection.execute(
            "INSERT INTO worker_capacity_leases (work_unit_id, acquired_at) VALUES (?, ?)",
            (work_unit_id, acquired_at),
        )
        return True

    def reserve_worker_capacity(self, work_unit_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT wu.status AS work_status, s.status AS scan_status "
                "FROM work_units wu JOIN scans s ON s.scan_id = wu.scan_id "
                "WHERE wu.work_unit_id = ?",
                (work_unit_id,),
            ).fetchone()
            if row is None or row["work_status"] not in {"pending", "running"} or row["scan_status"] != "running":
                raise ValueError("Worker capacity requires an active work unit")
            if not self._reserve_worker_capacity(connection, work_unit_id, _now()):
                raise ValueError("Work unit already owns worker capacity")

    def release_worker_capacity(self, work_unit_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM worker_capacity_leases WHERE work_unit_id = ?",
                (work_unit_id,),
            )

    def active_worker_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_capacity_leases leases "
                    "JOIN work_units wu ON wu.work_unit_id = leases.work_unit_id "
                    "JOIN scans s ON s.scan_id = wu.scan_id "
                    "WHERE wu.status IN ('pending', 'running') "
                    "AND s.status IN ('running', 'reducing', 'cancelling')"
                ).fetchone()[0]
            )

    def get_work_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM work_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            capsule = self._execution_capsule(connection, attempt_id)
        item = dict(row)
        item["capsule"] = capsule.public_dict()
        return item

    def list_work_attempts(self, work_unit_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT attempt_id FROM work_attempts WHERE work_unit_id = ? "
                "ORDER BY ordinal",
                (work_unit_id,),
            ).fetchall()
        return [
            attempt
            for row in rows
            if (attempt := self.get_work_attempt(row["attempt_id"])) is not None
        ]

    @staticmethod
    def _execution_capsule(
        connection: sqlite3.Connection,
        attempt_id: str,
    ) -> ExecutionCapsule:
        row = connection.execute(
            """
            SELECT wa.*, wu.scan_id, wu.phase, wu.role,
                   s.snapshot_id, s.ruleset_digest
            FROM work_attempts wa
            JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id
            JOIN scans s ON s.scan_id = wu.scan_id
            WHERE wa.attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Work attempt not found")
        return ExecutionCapsule(
            scan_id=row["scan_id"],
            snapshot_id=row["snapshot_id"],
            work_unit_id=row["work_unit_id"],
            attempt_id=row["attempt_id"],
            phase=row["phase"],
            role=row["role"],
            agent_name=row["agent_name"],
            session_id=row["session_id"],
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            toolset_digest=row["toolset_digest"],
            ruleset_digest=row["ruleset_digest"],
            scope_digest=row["scope_digest"],
        )

    def set_work_attempt_runtime(
        self,
        attempt_id: str,
        *,
        background_task_id: str,
        started_at: str | None,
    ) -> None:
        if started_at is not None:
            parsed = datetime.fromisoformat(started_at)
            if parsed.tzinfo is None:
                raise ValueError("Work-attempt timestamps must include a timezone")
        with self._lock, self._connect() as connection:
            now = _now()
            cursor = connection.execute(
                "UPDATE work_attempts SET background_task_id = ?, status = 'running', "
                "started_at = COALESCE(?, started_at), updated_at = ? "
                "WHERE attempt_id = ? AND status IN ('running', 'recovering')",
                (background_task_id, started_at, now, attempt_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work attempt is not active")

    def prepare_work_attempt_resume(self, attempt_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE work_attempts SET status = 'recovering', "
                "resume_count = resume_count + 1, updated_at = ? "
                "WHERE attempt_id = ? AND status = 'running' "
                "AND resume_count < ?",
                (_now(), attempt_id, MAX_SAME_SESSION_RESUMES),
            )
            if cursor.rowcount != 1:
                raise ValueError("Same-session resume budget is exhausted")
        attempt = self.get_work_attempt(attempt_id)
        if attempt is None:
            raise ValueError("Work attempt not found")
        return attempt

    def finish_work_attempt(
        self,
        attempt_id: str | None,
        *,
        status: str,
        failure_class: str | None = None,
        work_unit_status: str | None = None,
    ) -> None:
        if attempt_id is None:
            raise ValueError("Work attempt is required")
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Unsupported work-attempt terminal status")
        if work_unit_status not in {None, "completed", "failed", "cancelled"}:
            raise ValueError("Unsupported work-unit terminal status")
        if work_unit_status is None and status in {"completed", "cancelled"}:
            work_unit_status = status
        now = _now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT work_unit_id, status FROM work_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Work attempt not found")
            if row["status"] in {"completed", "failed", "cancelled"}:
                if row["status"] == status:
                    connection.execute(
                        "DELETE FROM worker_capacity_leases WHERE work_unit_id = ?",
                        (row["work_unit_id"],),
                    )
                    return
                raise ValueError("Work attempt is already terminal")
            cursor = connection.execute(
                "UPDATE work_attempts SET status = ?, failure_class = ?, "
                "finished_at = ?, updated_at = ? WHERE attempt_id = ? "
                "AND status IN ('pending', 'running', 'recovering')",
                (status, failure_class, now, now, attempt_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work attempt status changed concurrently")
            if work_unit_status is not None:
                connection.execute(
                    "UPDATE work_units SET status = ?, updated_at = ?, finished_at = ? "
                    "WHERE work_unit_id = ? AND status IN ('pending', 'running')",
                    (work_unit_status, now, now, row["work_unit_id"]),
                )
            connection.execute(
                "DELETE FROM worker_capacity_leases WHERE work_unit_id = ?",
                (row["work_unit_id"],),
            )

    def update_worker_batch_status(self, batch_id: str, status: str) -> None:
        if status not in {"pending", "running", "completed", "partial", "failed", "cancelled"}:
            raise ValueError("Unsupported worker batch status")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM worker_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Worker batch not found")
            allowed = {
                "pending": {"running", "failed", "cancelled"},
                "running": {"completed", "partial", "failed", "cancelled"},
            }
            if status == row["status"]:
                return
            if status not in allowed.get(row["status"], set()):
                raise ValueError("Unsupported worker batch status transition")
            cursor = connection.execute(
                "UPDATE worker_batches SET status = ?, updated_at = ? WHERE batch_id = ? AND status = ?",
                (status, _now(), batch_id, row["status"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Worker batch status changed concurrently")

    def work_unit_has_required_facts(
        self,
        work_unit_id: str,
        *,
        role: str,
    ) -> bool:
        with self._connect() as connection:
            if role in {"threat_modeler", "baseline", "investigator", "poc_generator"}:
                guided = connection.execute(
                    "SELECT 1 FROM work_units wu "
                    "JOIN scan_knowledge_base kb ON kb.scan_id = wu.scan_id "
                    "WHERE wu.work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if guided is not None:
                    consumed = connection.execute(
                        "SELECT 1 FROM knowledge_base_access WHERE work_unit_id = ?",
                        (work_unit_id,),
                    ).fetchone()
                    if consumed is None:
                        return False
            if role == "threat_modeler":
                row = connection.execute(
                    "SELECT payload_json, evidence_json FROM threat_models WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
            elif role in {"baseline", "investigator"}:
                row = connection.execute(
                    "SELECT completeness FROM coverage_attestations "
                    "WHERE work_unit_id = ? "
                    "ORDER BY created_at DESC, attestation_id DESC LIMIT 1",
                    (work_unit_id,),
                ).fetchone()
            elif role == "verifier":
                assignment = connection.execute(
                    "SELECT subject_id, vote_index FROM worker_batch_units "
                    "WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if assignment is None or assignment["subject_id"] is None:
                    return False
                row = connection.execute(
                    "SELECT 1 FROM verification_votes WHERE work_unit_id = ? "
                    "AND candidate_id = ? AND vote_index = ?",
                    (
                        work_unit_id,
                        assignment["subject_id"],
                        assignment["vote_index"],
                    ),
                ).fetchone()
            elif role == "prober":
                assignment = connection.execute(
                    "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if assignment is None or assignment["subject_id"] is None:
                    return False
                row = connection.execute(
                    "SELECT 1 FROM dynamic_runs WHERE probe_work_unit_id = ? AND candidate_id = ?",
                    (work_unit_id, assignment["subject_id"]),
                ).fetchone()
            elif role == "cybergym_solver":
                row = connection.execute(
                    "SELECT 1 FROM cybergym_tasks WHERE scan_id = ("
                    "SELECT scan_id FROM work_units WHERE work_unit_id = ?"
                    ") AND status IN ('submitted', 'failed_no_artifact')",
                    (work_unit_id,),
                ).fetchone()
            elif role == "poc_generator":
                assignment = connection.execute(
                    "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if assignment is None or assignment["subject_id"] is None:
                    return False
                row = connection.execute(
                    "SELECT 1 FROM poc_bundles WHERE work_unit_id = ? "
                    "AND candidate_id = ? AND status = 'generated'",
                    (work_unit_id, assignment["subject_id"]),
                ).fetchone()
            else:
                raise ValueError("Unsupported work-unit role")
        if row is None:
            return False
        if role == "threat_modeler":
            try:
                self.validate_threat_model_contract(
                    json.loads(row["payload_json"]),
                    json.loads(row["evidence_json"]),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                return False
        return True

    def work_attempt_has_analysis_progress(self, attempt_id: str) -> bool:
        """Return whether an analysis attempt persisted source access or a candidate."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT wa.work_unit_id, wu.role FROM work_attempts wa "
                "JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id "
                "WHERE wa.attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None or row["role"] not in {"baseline", "investigator"}:
                return False
            source_access = connection.execute(
                "SELECT 1 FROM source_access WHERE attempt_id = ? LIMIT 1",
                (attempt_id,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT 1 FROM candidates WHERE work_unit_id = ? LIMIT 1",
                (row["work_unit_id"],),
            ).fetchone()
        return source_access is not None or candidate is not None

    def list_unverified_candidates(
        self,
        scan_id: str,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            vote_count = int(scan["verification_vote_count"])
            rows = connection.execute(
                """
                SELECT c.* FROM candidates c
                LEFT JOIN verifications v ON v.candidate_id = c.candidate_id
                WHERE c.scan_id = ? AND v.candidate_id IS NULL
                ORDER BY c.created_at, c.candidate_id
                """,
                (scan_id,),
            ).fetchall()
            remaining = max(1, min(int(limit), 32))
            output: list[dict[str, Any]] = []
            for row in rows:
                used_indices = {
                    int(item["vote_index"])
                    for item in connection.execute(
                        "SELECT vote_index FROM verification_votes "
                        "WHERE candidate_id = ?",
                        (row["candidate_id"],),
                    ).fetchall()
                }
                active_indices = {
                    int(item["vote_index"])
                    for item in connection.execute(
                        "SELECT assigned.vote_index FROM worker_batch_units assigned "
                        "JOIN work_units wu ON wu.work_unit_id = assigned.work_unit_id "
                        "WHERE assigned.subject_id = ? "
                        "AND assigned.vote_index IS NOT NULL "
                        "AND wu.status IN ('pending', 'running')",
                        (row["candidate_id"],),
                    ).fetchall()
                }
                pending_indices = [
                    index
                    for index in range(1, vote_count + 1)
                    if index not in used_indices and index not in active_indices
                ][:remaining]
                if not pending_indices:
                    continue
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                item["pending_vote_indices"] = pending_indices
                evidence = connection.execute(
                    "SELECT relative_path, blob_digest, start_line, end_line, "
                    "excerpt_hash, ordinal "
                    "FROM evidence WHERE candidate_id = ? "
                    "ORDER BY ordinal, rowid",
                    (item["candidate_id"],),
                ).fetchall()
                item["evidence"] = [dict(record) for record in evidence]
                output.append(item)
                remaining -= len(pending_indices)
                if remaining == 0:
                    break
        return output

    def list_confirmed_without_dynamic_record(
        self,
        scan_id: str,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 32))
        with self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            if not bool(scan["dynamic_enabled"]):
                return []
            category_placeholders = ", ".join(
                "?" for _ in DYNAMIC_EXECUTION_CATEGORIES
            )
            rows = connection.execute(
                f"""
                SELECT c.* FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                LEFT JOIN dynamic_runs d ON d.candidate_id = c.candidate_id
                WHERE c.scan_id = ? AND v.verdict = 'confirmed'
                  AND d.candidate_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM worker_batch_units assigned
                    JOIN work_units wu ON wu.work_unit_id = assigned.work_unit_id
                    WHERE assigned.subject_id = c.candidate_id
                      AND wu.role = 'prober'
                      AND wu.status IN ('pending', 'running')
                  )
                ORDER BY CASE WHEN LOWER(TRIM(COALESCE(
                    json_extract(c.payload_json, '$.category'), ''
                ))) IN ({category_placeholders}) THEN 0 ELSE 1 END,
                c.created_at, c.candidate_id
                LIMIT ?
                """,
                (scan_id, *DYNAMIC_EXECUTION_CATEGORIES, bounded_limit),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            output.append(item)
        return output

    @staticmethod
    def _decode_dynamic_run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["probe"] = json.loads(item.pop("probe_json"))
        raw_run = item.pop("run_json")
        item["run"] = json.loads(raw_run) if raw_run else None
        return item

    def list_dynamic_runs(
        self,
        scan_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in {
            "ready",
            "not_runnable",
            "completed",
            "inconclusive",
        }:
            raise ValueError("Unsupported dynamic-run status")
        query = "SELECT * FROM dynamic_runs WHERE scan_id = ?"
        values: list[Any] = [scan_id]
        if status is not None:
            query += " AND status = ?"
            values.append(status)
        query += " ORDER BY created_at, candidate_id"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._decode_dynamic_run(row) for row in rows]

    def get_probe_subject(self, binding: SessionBinding) -> dict[str, Any]:
        if binding.role != "prober" or binding.work_unit_id is None:
            raise ValueError("Probe subject requires a prober work unit")
        with self._connect() as connection:
            scan = self._require_scan_status(connection, binding.scan_id, {"running"})
            if not bool(scan["dynamic_enabled"]):
                raise ValueError("Dynamic validation is not enabled for this scan")
            self._require_active_worker_binding(connection, binding)
            assignment = connection.execute(
                "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or not assignment["subject_id"]:
                raise ValueError("Prober work unit has no assigned candidate")
            row = connection.execute(
                """
                SELECT c.*, v.verdict, v.rationale AS verification_rationale
                FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                LEFT JOIN dynamic_runs d ON d.candidate_id = c.candidate_id
                WHERE c.candidate_id = ? AND c.scan_id = ?
                  AND v.verdict = 'confirmed' AND d.candidate_id IS NULL
                """,
                (assignment["subject_id"], binding.scan_id),
            ).fetchone()
            if row is None:
                raise ValueError("Assigned candidate is not eligible for probing")
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def list_confirmed_without_poc_record(
        self,
        scan_id: str,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 32))
        with self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            if not bool(scan["poc_enabled"]):
                return []
            rows = connection.execute(
                """
                SELECT c.* FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                LEFT JOIN poc_bundles p ON p.candidate_id = c.candidate_id
                WHERE c.scan_id = ? AND v.verdict = 'confirmed'
                  AND p.candidate_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                    WHERE a.scan_id = c.scan_id AND a.action = 'finalize'
                      AND a.adjudication_round = (
                        SELECT MAX(latest.adjudication_round)
                        FROM adjudications latest
                        WHERE latest.scan_id = c.scan_id AND latest.action = 'finalize'
                      )
                      AND accepted.value = c.candidate_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM worker_batch_units assigned
                    JOIN work_units wu ON wu.work_unit_id = assigned.work_unit_id
                    WHERE assigned.subject_id = c.candidate_id
                      AND wu.role = 'poc_generator'
                      AND wu.status IN ('pending', 'running', 'failed')
                  )
                ORDER BY c.created_at, c.candidate_id
                LIMIT ?
                """,
                (scan_id, bounded_limit),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                evidence = connection.execute(
                    "SELECT relative_path, blob_digest, start_line, end_line, "
                    "excerpt_hash, ordinal FROM evidence WHERE candidate_id = ? "
                    "ORDER BY ordinal, rowid",
                    (item["candidate_id"],),
                ).fetchall()
                item["evidence"] = [dict(record) for record in evidence]
                output.append(item)
        return output

    def get_poc_subject(self, binding: SessionBinding) -> dict[str, Any]:
        if binding.role != "poc_generator" or binding.work_unit_id is None:
            raise ValueError("PoC subject requires a PoC-generator work unit")
        if binding.attempt_id is None:
            raise ValueError("PoC subject requires a bound work attempt")
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, binding.scan_id, {"running"})
            if not bool(scan["poc_enabled"]):
                raise ValueError("PoC generation is not enabled for this scan")
            self._require_active_worker_binding(connection, binding)
            assignment = connection.execute(
                "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or not assignment["subject_id"]:
                raise ValueError("PoC-generator work unit has no assigned candidate")
            candidate = connection.execute(
                """
                SELECT c.*, v.verdict, v.rationale AS verification_rationale
                FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                WHERE c.candidate_id = ? AND c.scan_id = ? AND v.verdict = 'confirmed'
                  AND EXISTS (
                    SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                    WHERE a.scan_id = c.scan_id AND a.action = 'finalize'
                      AND a.adjudication_round = (
                        SELECT MAX(latest.adjudication_round)
                        FROM adjudications latest
                        WHERE latest.scan_id = c.scan_id AND latest.action = 'finalize'
                      )
                      AND accepted.value = c.candidate_id
                  )
                """,
                (assignment["subject_id"], binding.scan_id),
            ).fetchone()
            if candidate is None:
                raise ValueError("Assigned candidate is not eligible for PoC generation")
            evidence = connection.execute(
                "SELECT relative_path, blob_digest, start_line, end_line, "
                "excerpt_hash, ordinal FROM evidence WHERE candidate_id = ? "
                "ORDER BY ordinal, rowid",
                (assignment["subject_id"],),
            ).fetchall()
            manifest = self.get_repository_manifest(binding.snapshot_id)
            execution_manifest: dict[str, Any] = (
                manifest.public_dict()
                if manifest is not None
                else {"snapshot_id": binding.snapshot_id}
            )
            cybergym = connection.execute(
                "SELECT manifest_json FROM cybergym_tasks WHERE scan_id = ?",
                (binding.scan_id,),
            ).fetchone()
            if cybergym is not None:
                cybergym_manifest = json.loads(cybergym["manifest_json"])
                max_bytes = _cybergym_poc_input_limit(cybergym_manifest)
                execution_manifest["cybergym"] = cybergym_manifest
                execution_manifest["cybergym_poc_contract"] = {
                    "artifact_type": "raw_input",
                    "file_count": 1,
                    "entrypoint_matches_input_path": True,
                    "input_kind": "literal",
                    "max_bytes": max_bytes,
                    "dynamic_phase": "replay, vul/fix confirmation, and bounded refinement",
                }
            output = {
                "candidate_id": candidate["candidate_id"],
                "trust": "untrusted_candidate_claim",
                "claim": json.loads(candidate["payload_json"]),
                "verification": {
                    "verdict": candidate["verdict"],
                    "rationale": candidate["verification_rationale"],
                },
                "evidence": [dict(item) for item in evidence],
                "execution_manifest": execution_manifest,
            }
        return output

    def save_poc_bundle(
        self,
        binding: SessionBinding,
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        if binding.role != "poc_generator" or binding.work_unit_id is None:
            raise ValueError("PoC bundles require a PoC-generator work unit")
        if not isinstance(bundle, dict):
            raise ValueError("poc must be an object")
        candidate_id = bundle.get("candidate_id")
        artifact_type = bundle.get("artifact_type")
        entrypoint = bundle.get("entrypoint")
        files = bundle.get("files")
        source_refs = bundle.get("source_refs")
        rationale = bundle.get("rationale")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("PoC bundle requires candidate_id")
        if artifact_type not in {"raw_input", "source_harness", "request", "bundle"}:
            raise ValueError("Unsupported PoC artifact_type")
        if not isinstance(entrypoint, str) or not entrypoint.strip() or len(entrypoint) > 512:
            raise ValueError("PoC entrypoint must be a bounded non-empty string")
        if not isinstance(files, list) or not 1 <= len(files) <= 8:
            raise ValueError("PoC bundle requires between 1 and 8 files")
        if not isinstance(source_refs, list) or not source_refs:
            raise ValueError("PoC bundle requires source_refs")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4_000:
            raise ValueError("PoC rationale must be a bounded non-empty string")
        canonical_files: list[dict[str, Any]] = []
        file_paths: set[str] = set()
        total_bytes = 0
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Each PoC file must be an object")
            path = item.get("path")
            encoding = item.get("encoding", "utf8")
            data = item.get("data")
            if (
                not isinstance(path, str)
                or not path
                or len(path) > 256
                or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
                or not isinstance(data, str)
                or encoding not in POC_FILE_ENCODINGS
            ):
                raise ValueError("PoC files require safe relative path, encoding, and data")
            raw = decode_poc_bytes(data, encoding)
            if not raw or len(raw) > MAX_POC_FILE_BYTES:
                raise ValueError("PoC file data must be non-empty and <= 128 KiB")
            if path in file_paths:
                raise ValueError("PoC bundle file paths must be unique")
            file_paths.add(path)
            total_bytes += len(raw)
            canonical_files.append(
                {"path": path, "encoding": encoding, "data": data}
            )
        if total_bytes > MAX_POC_BUNDLE_BYTES:
            raise ValueError("PoC bundle files exceed 512 KiB")
        if any(
            not isinstance(ref, dict)
            or not isinstance(ref.get("relative_path"), str)
            or not isinstance(ref.get("blob_digest"), str)
            or not isinstance(ref.get("start_line"), int)
            or isinstance(ref.get("start_line"), bool)
            or not isinstance(ref.get("end_line"), int)
            or isinstance(ref.get("end_line"), bool)
            or ref["start_line"] < 1
            or ref["end_line"] < ref["start_line"]
            for ref in source_refs
        ):
            raise ValueError("PoC source_refs contain invalid line ranges")
        if any(
            item.get("path") == "."
            or PurePosixPath(item["path"]).as_posix() != item["path"]
            or "\\" in item["path"]
            for item in canonical_files
        ):
            raise ValueError("PoC file paths must be canonical relative paths")
        delivery = bundle.get("delivery")
        if delivery is not None:
            if not isinstance(delivery, dict) or len(delivery) > 16:
                raise ValueError("PoC delivery metadata must be a bounded object")
            allowed_delivery_keys = {
                "transport",
                "input_path",
                "argument",
                "content_type",
                "target_language",
                "build_system",
                "input_kind",
            }
            if not set(delivery) <= allowed_delivery_keys:
                raise ValueError("PoC delivery metadata contains unsupported execution fields")
            for key, value in delivery.items():
                if not isinstance(value, str) or not value.strip() or len(value) > 512:
                    raise ValueError(f"PoC delivery field {key} must be a bounded string")
            input_path = delivery.get("input_path")
            if input_path is not None:
                normalized_input_path = PurePosixPath(input_path)
                if (
                    normalized_input_path.is_absolute()
                    or ".." in normalized_input_path.parts
                    or normalized_input_path.as_posix() != input_path
                    or input_path == "."
                    or "\\" in input_path
                ):
                    raise ValueError("PoC delivery input_path must be a canonical relative path")
        self.require_repository_summary_consumed(binding)
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, binding.scan_id, {"running"})
            if not bool(scan["poc_enabled"]):
                raise ValueError("PoC generation is not enabled for this scan")
            if scan["mode"] == "cybergym_level1":
                cybergym = connection.execute(
                    "SELECT manifest_json FROM cybergym_tasks WHERE scan_id = ?",
                    (binding.scan_id,),
                ).fetchone()
                if cybergym is None:
                    raise ValueError("CyberGym task is missing from this scan")
                manifest = json.loads(cybergym["manifest_json"])
                max_bytes = _cybergym_poc_input_limit(manifest)
                require_cybergym_submission_input(
                    bundle,
                    max_bytes=max_bytes,
                    input_contract=manifest.get("input_contract"),
                )
            self._require_active_worker_binding(connection, binding)
            assignment = connection.execute(
                "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or assignment["subject_id"] != candidate_id:
                raise ValueError("Candidate is not assigned to this PoC-generator work unit")
            candidate = connection.execute(
                """
                SELECT 1 FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                WHERE c.candidate_id = ? AND c.scan_id = ? AND v.verdict = 'confirmed'
                  AND EXISTS (
                    SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                    WHERE a.scan_id = c.scan_id AND a.action = 'finalize'
                      AND a.adjudication_round = (
                        SELECT MAX(latest.adjudication_round)
                        FROM adjudications latest
                        WHERE latest.scan_id = c.scan_id AND latest.action = 'finalize'
                      )
                      AND accepted.value = c.candidate_id
                  )
                """,
                (candidate_id, binding.scan_id),
            ).fetchone()
            if candidate is None:
                raise ValueError("Only confirmed candidates may receive PoC bundles")
            evidence_rows = connection.execute(
                "SELECT relative_path, blob_digest, start_line, end_line FROM evidence "
                "WHERE candidate_id = ? ORDER BY ordinal, rowid",
                (candidate_id,),
            ).fetchall()
            evidence_keys = {
                (row["relative_path"], row["blob_digest"], row["start_line"], row["end_line"])
                for row in evidence_rows
            }
            for evidence in evidence_rows:
                accesses = connection.execute(
                    "SELECT start_line, end_line FROM source_access "
                    "WHERE attempt_id = ? AND operation = 'read' "
                    "AND relative_path = ? AND blob_digest = ? "
                    "ORDER BY start_line, end_line",
                    (binding.attempt_id, evidence["relative_path"], evidence["blob_digest"]),
                ).fetchall()
                if not _ranges_cover(
                    [(row["start_line"], row["end_line"]) for row in accesses],
                    evidence["start_line"],
                    evidence["end_line"],
                ):
                    raise ValueError(
                        "PoC generator must read every candidate evidence range before submitting"
                    )
            normalized_refs: list[dict[str, Any]] = []
            for ref in source_refs:
                if not isinstance(ref, dict):
                    raise ValueError("Each source_ref must be an object")
                key = (
                    ref.get("relative_path"),
                    ref.get("blob_digest"),
                    ref.get("start_line"),
                    ref.get("end_line"),
                )
                if key not in evidence_keys:
                    raise ValueError("PoC source_refs must match candidate evidence exactly")
                normalized_refs.append(
                    {
                        "relative_path": ref["relative_path"],
                        "blob_digest": ref["blob_digest"],
                        "start_line": ref["start_line"],
                        "end_line": ref["end_line"],
                    }
                )
            submitted_ref_keys = {
                (
                    ref["relative_path"],
                    ref["blob_digest"],
                    ref["start_line"],
                    ref["end_line"],
                )
                for ref in normalized_refs
            }
            if len(submitted_ref_keys) != len(normalized_refs):
                raise ValueError("PoC source_refs must not contain duplicates")
            if submitted_ref_keys != evidence_keys:
                raise ValueError("PoC source_refs must include every candidate evidence range exactly once")
            payload = {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "artifact_type": artifact_type,
                "entrypoint": entrypoint,
                "files": canonical_files,
                "source_refs": normalized_refs,
                "rationale": rationale,
            }
            if isinstance(bundle.get("language"), str):
                payload["language"] = bundle["language"][:128]
            if isinstance(bundle.get("delivery"), dict):
                payload["delivery"] = bundle["delivery"]
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if len(serialized.encode("utf-8")) > MAX_POC_BUNDLE_BYTES:
                raise ValueError("PoC bundle exceeds 512 KiB")
            poc_id = f"poc_{uuid.uuid4().hex}"
            now = _now()
            try:
                connection.execute(
                    "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
                    (
                        poc_id,
                        binding.scan_id,
                        candidate_id,
                        binding.work_unit_id,
                        serialized,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Candidate already has a PoC bundle") from exc
        return {"poc_id": poc_id, "candidate_id": candidate_id, "status": "generated"}

    def list_poc_bundles(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT poc_id, candidate_id, work_unit_id, status, bundle_json, created_at, updated_at "
                "FROM poc_bundles WHERE scan_id = ? ORDER BY created_at, poc_id",
                (scan_id,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["bundle"] = json.loads(item.pop("bundle_json"))
            output.append(item)
        return output

    def list_accepted_poc_bundles(self, scan_id: str) -> list[dict[str, Any]]:
        """Return generated PoCs for candidates accepted by the latest final adjudication."""
        bundles = self.list_poc_bundles(scan_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT accepted_candidate_ids_json FROM adjudications "
                "WHERE scan_id = ? AND action = 'finalize' "
                "ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
        if row is None:
            return []
        accepted = set(json.loads(row["accepted_candidate_ids_json"]))
        return [
            bundle
            for bundle in bundles
            if bundle["status"] == "generated" and bundle["candidate_id"] in accepted
        ]

    def record_poc_validation(
        self,
        scan_id: str,
        *,
        poc_id: str,
        candidate_id: str,
        validator: str,
        status: str,
        artifact_id: str | None,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"verified", "unverified", "failed", "not_run"}:
            raise ValueError("Unsupported PoC validation status")
        if not isinstance(validator, str) or not validator.strip() or len(validator) > 128:
            raise ValueError("PoC validator must be a bounded non-empty string")
        if not isinstance(evidence, dict):
            raise ValueError("PoC validation evidence must be an object")
        encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValueError("PoC validation evidence is too large")
        with self._lock, self._connect() as connection:
            owner = connection.execute(
                "SELECT candidate_id FROM poc_bundles WHERE scan_id = ? AND poc_id = ?",
                (scan_id, poc_id),
            ).fetchone()
            if owner is None or owner["candidate_id"] != candidate_id:
                raise ValueError("PoC validation does not match the persisted bundle")
            if artifact_id is not None:
                artifact = connection.execute(
                    "SELECT 1 FROM cybergym_artifacts WHERE scan_id = ? AND artifact_id = ?",
                    (scan_id, artifact_id),
                ).fetchone()
                if artifact is None:
                    raise ValueError("PoC validation artifact is not part of the CyberGym task")
            now = _now()
            validation_id = f"poc_validation_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO poc_validations ("
                "validation_id, scan_id, poc_id, candidate_id, validator, status, "
                "artifact_id, evidence_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scan_id, poc_id, validator) DO UPDATE SET "
                "status = excluded.status, artifact_id = excluded.artifact_id, "
                "evidence_json = excluded.evidence_json, updated_at = excluded.updated_at",
                (
                    validation_id,
                    scan_id,
                    poc_id,
                    candidate_id,
                    validator.strip(),
                    status,
                    artifact_id,
                    encoded,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM poc_validations WHERE scan_id = ? AND poc_id = ? AND validator = ?",
                (scan_id, poc_id, validator.strip()),
            ).fetchone()
        if row is None:  # pragma: no cover - defensive persistence boundary
            raise ValueError("PoC validation was not persisted")
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        return item

    def list_poc_validations(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM poc_validations WHERE scan_id = ? "
                "ORDER BY created_at, validation_id",
                (scan_id,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            output.append(item)
        return output

    def get_poc_bundle_candidate(self, scan_id: str, poc_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT candidate_id FROM poc_bundles WHERE scan_id = ? AND poc_id = ?",
                (scan_id, poc_id),
            ).fetchone()
        if row is None:
            raise ValueError("PoC bundle is not available for this scan")
        return str(row["candidate_id"])

    def assert_accepted_poc_bundle(self, scan_id: str, poc_id: str) -> None:
        if not any(item["poc_id"] == poc_id for item in self.list_accepted_poc_bundles(scan_id)):
            raise ValueError("source_poc_id must refer to an accepted generated PoC")

    def cybergym_artifact_poc_id(self, scan_id: str, artifact_id: str) -> str | None:
        """Resolve the generic PoC that produced an artifact through its parent lineage."""
        seen: set[str] = set()
        current = artifact_id
        with self._connect() as connection:
            while current and current not in seen:
                seen.add(current)
                row = connection.execute(
                    "SELECT parent_id, provenance_json FROM cybergym_artifacts "
                    "WHERE scan_id = ? AND artifact_id = ?",
                    (scan_id, current),
                ).fetchone()
                if row is None:
                    return None
                try:
                    provenance = json.loads(row["provenance_json"])
                except (TypeError, json.JSONDecodeError):
                    provenance = {}
                poc_id = provenance.get("poc_id")
                if isinstance(poc_id, str) and poc_id:
                    return poc_id
                poc_ids = provenance.get("poc_ids")
                if isinstance(poc_ids, list) and len(poc_ids) == 1 and isinstance(poc_ids[0], str):
                    return poc_ids[0]
                current = row["parent_id"]
        return None

    def save_dynamic_probe(
        self,
        binding: SessionBinding,
        probe: dict[str, Any],
    ) -> None:
        if binding.role != "prober" or binding.work_unit_id is None:
            raise ValueError("Dynamic probe requires a bound prober work unit")
        candidate_id = probe["candidate_id"]
        persisted_status = "ready" if probe["status"] == "runnable" else "not_runnable"
        now = _now()
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, binding.scan_id, {"running"})
            if not bool(scan["dynamic_enabled"]):
                raise ValueError("Dynamic validation is not enabled for this scan")
            self._require_active_worker_binding(connection, binding)
            assignment = connection.execute(
                "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or assignment["subject_id"] != candidate_id:
                raise ValueError("Candidate is not assigned to this prober work unit")
            candidate = connection.execute(
                """
                SELECT c.scan_id, v.verdict FROM candidates c
                JOIN verifications v ON v.candidate_id = c.candidate_id
                WHERE c.candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if candidate is None or candidate["scan_id"] != binding.scan_id or candidate["verdict"] != "confirmed":
                raise ValueError("Only statically confirmed candidates may have probes")
            try:
                connection.execute(
                    "INSERT INTO dynamic_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate_id,
                        binding.scan_id,
                        binding.work_unit_id,
                        persisted_status,
                        json.dumps(probe, ensure_ascii=False, sort_keys=True),
                        None,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Candidate already has a dynamic run record") from exc

    def complete_dynamic_run(
        self,
        candidate_id: str,
        status: str,
        run: dict[str, Any],
    ) -> None:
        if status not in {"completed", "inconclusive"}:
            raise ValueError("Runner may only persist completed or inconclusive facts")
        if not isinstance(run, dict) or run.get("runner_status") != status:
            raise ValueError("Dynamic-run facts do not match the terminal status")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE dynamic_runs SET status = ?, run_json = ?, updated_at = ? "
                "WHERE candidate_id = ? AND status = 'ready' AND run_json IS NULL",
                (
                    status,
                    json.dumps(run, ensure_ascii=False, sort_keys=True),
                    _now(),
                    candidate_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Dynamic run is not ready or was already completed")

    def assert_dynamic_runs_terminal(self, scan_id: str) -> None:
        with self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            self._require_dynamic_ready(connection, scan_id, scan=scan)

    def create_cybergym_task(self, scan_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        """Persist a host-validated Level 1 task before a solver can access it."""
        from flocks_code_security.cybergym_runtime import CyberGymTargetManifest

        normalized = CyberGymTargetManifest.from_dict(manifest).public_dict()
        now = _now()
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            if scan["mode"] != "cybergym_level1":
                raise ValueError("CyberGym task requires cybergym_level1 scan mode")
            try:
                connection.execute(
                    "INSERT INTO cybergym_tasks (scan_id, task_id, manifest_json, status, "
                    "final_artifact_id, selected_poc_id, local_validation, selection_reason, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'active', NULL, NULL, NULL, NULL, ?, ?)",
                    (
                        scan_id,
                        normalized["task_id"],
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("CyberGym task already exists for this scan") from exc
        task = self.get_cybergym_task(scan_id)
        if task is None:  # pragma: no cover - defensive persistence boundary
            raise ValueError("CyberGym task was not persisted")
        return task

    @staticmethod
    def _decode_cybergym_task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["manifest"] = json.loads(item.pop("manifest_json"))
        return item

    @staticmethod
    def _decode_cybergym_artifact(row: sqlite3.Row, *, include_data: bool = False) -> dict[str, Any]:
        item = dict(row)
        item["size"] = item.pop("size_bytes")
        item["provenance"] = json.loads(item.pop("provenance_json"))
        raw = item.pop("raw_bytes")
        if include_data:
            item["data"] = bytes(raw)
        return item

    @staticmethod
    def _decode_cybergym_run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input"] = json.loads(item.pop("input_json"))
        raw_result = item.pop("result_json")
        item["result"] = json.loads(raw_result) if raw_result else None
        return item

    def get_cybergym_task(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        return self._decode_cybergym_task(row) if row is not None else None

    def set_cybergym_selected_poc(self, scan_id: str, poc_id: str) -> None:
        """Record the generic PoC that produced the final submitted artifact."""
        with self._lock, self._connect() as connection:
            task = connection.execute(
                "SELECT status, selected_poc_id FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not accepting PoC selection")
            owner = connection.execute(
                "SELECT candidate_id FROM poc_bundles WHERE scan_id = ? AND poc_id = ? AND status = 'generated'",
                (scan_id, poc_id),
            ).fetchone()
            if owner is None:
                raise ValueError("Selected PoC is not a generated bundle for this scan")
            if task["selected_poc_id"] not in {None, poc_id}:
                raise ValueError("CyberGym task is already bound to another generic PoC")
            connection.execute(
                "UPDATE cybergym_tasks SET selected_poc_id = ?, updated_at = ? WHERE scan_id = ?",
                (poc_id, _now(), scan_id),
            )

    def get_cybergym_selected_poc(self, scan_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT selected_poc_id FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        return None if row is None else row["selected_poc_id"]

    def list_accepted_poc_contexts(self, scan_id: str) -> list[dict[str, Any]]:
        """Return accepted PoCs with only the finding fields needed for task binding."""
        bundles = self.list_accepted_poc_bundles(scan_id)
        if not bundles:
            return []
        with self._connect() as connection:
            output: list[dict[str, Any]] = []
            for bundle in bundles:
                candidate = connection.execute(
                    "SELECT payload_json FROM candidates WHERE scan_id = ? AND candidate_id = ?",
                    (scan_id, bundle["candidate_id"]),
                ).fetchone()
                evidence = connection.execute(
                    "SELECT relative_path FROM evidence WHERE candidate_id = ? ORDER BY ordinal, rowid",
                    (bundle["candidate_id"],),
                ).fetchall()
                if candidate is None:
                    continue
                output.append(
                    {
                        **bundle,
                        "candidate": json.loads(candidate["payload_json"]),
                        "evidence_paths": [row["relative_path"] for row in evidence],
                    }
                )
        return output

    def cybergym_context(
        self,
        scan_id: str,
        *,
        work_unit_id: str | None = None,
    ) -> dict[str, Any]:
        task = self.get_cybergym_task(scan_id)
        if task is None:
            raise ValueError("CyberGym task is not available for this scan")
        work_unit: dict[str, Any] | None = None
        if work_unit_id is not None:
            work_unit = self.get_work_unit(work_unit_id)
            if work_unit is None or work_unit["scan_id"] != scan_id:
                raise ValueError("CyberGym execution context does not belong to this scan")
        poc_bundles = self.list_accepted_poc_bundles(scan_id)
        artifacts = self.list_cybergym_artifacts(scan_id)
        imported_poc_ids = {
            item.get("provenance", {}).get("poc_id")
            for item in artifacts
            if item.get("provenance", {}).get("operation") == "generic_poc_import"
        }
        generic_pocs = [
            {
                "poc_id": item["poc_id"],
                "candidate_id": item["candidate_id"],
                "artifact_type": item["bundle"].get("artifact_type"),
                "entrypoint": item["bundle"].get("entrypoint"),
                "delivery": item["bundle"].get("delivery"),
                "files": [
                    {
                        "path": file.get("path"),
                        "encoding": file.get("encoding", "utf8"),
                        **({"data": file.get("data")} if item["poc_id"] not in imported_poc_ids else {}),
                    }
                    for file in item["bundle"].get("files", [])
                    if isinstance(file, dict)
                ],
            }
            for item in poc_bundles
        ]
        pocs_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for poc in generic_pocs:
            pocs_by_candidate.setdefault(poc["candidate_id"], []).append(poc)
        with self._connect() as connection:
            adjudication = connection.execute(
                "SELECT accepted_candidate_ids_json FROM adjudications "
                "WHERE scan_id = ? AND action = 'finalize' "
                "ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
            candidate_ids = json.loads(adjudication["accepted_candidate_ids_json"]) if adjudication else []
            candidates: list[dict[str, Any]] = []
            for candidate_id in candidate_ids:
                row = connection.execute(
                    "SELECT candidate_id, payload_json, created_at FROM candidates "
                    "WHERE scan_id = ? AND candidate_id = ?",
                    (scan_id, candidate_id),
                ).fetchone()
                if row is None:
                    continue
                evidence_rows = connection.execute(
                    "SELECT relative_path, blob_digest, start_line, end_line FROM evidence "
                    "WHERE candidate_id = ? ORDER BY ordinal, rowid",
                    (candidate_id,),
                ).fetchall()
                candidate = {
                    "candidate_id": row["candidate_id"],
                    "candidate": json.loads(row["payload_json"]),
                    "evidence": [dict(item) for item in evidence_rows],
                }
                candidate_pocs = pocs_by_candidate.get(row["candidate_id"], [])
                if candidate_pocs:
                    candidate["generic_pocs"] = candidate_pocs
                    if len(candidate_pocs) == 1:
                        candidate["generic_poc"] = candidate_pocs[0]
                candidates.append(candidate)
        runs = self.list_cybergym_runs(scan_id)
        active_fuzz_jobs = [
            {
                "run_id": run["run_id"],
                "poc_id": run["input"].get("poc_id"),
                "updated_at": run["updated_at"],
            }
            for run in runs
            if run["kind"] == "fuzz" and run["status"] == "running"
        ]
        recent_runs: list[dict[str, Any]] = []
        for run in runs[-16:]:
            raw_result = run.get("result")
            result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
            recent_runs.append(
                {
                    "run_id": run["run_id"],
                    "kind": run["kind"],
                    "status": run["status"],
                    "updated_at": run["updated_at"],
                    "summary": {
                        key: result[key]
                        for key in (
                            "status",
                            "outcome",
                            "execution_status",
                            "termination_reason",
                            "failure_code",
                            "crash_candidate_count",
                            "new_corpus_count",
                            "container_cleanup",
                        )
                        if key in result
                    },
                }
            )
        artifact_count = len(artifacts)
        available_actions = (
            ["terminal"]
            if task["status"] != "active"
            else [
                "inspect_poc_states",
                *(["wait_for_fuzz"] if active_fuzz_jobs else []),
                *(["create_or_import_seed"] if artifact_count == 0 else []),
                "replay_or_refine",
                "gdb_or_fuzz",
                "submit",
            ]
        )
        return {
            "task": {
                key: value
                for key, value in task.items()
                if key != "final_artifact_id"
            },
            "candidates": candidates,
            "generic_pocs": generic_pocs,
            "artifacts": artifacts,
            "submission": self.get_cybergym_submission(scan_id),
            "budget": self.cybergym_budget(scan_id),
            "execution_state": {
                "checkpoint_version": 2,
                "work_unit": (
                    {
                        "work_unit_id": work_unit["work_unit_id"],
                        "phase": work_unit["phase"],
                        "status": work_unit["status"],
                    }
                    if work_unit is not None
                    else None
                ),
                "active_fuzz_jobs": active_fuzz_jobs,
                "recent_runs": recent_runs,
                "available_actions": available_actions,
            },
        }

    def save_cybergym_checkpoint(self, scan_id: str, poc_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        self.assert_accepted_poc_bundle(scan_id, poc_id)
        if not isinstance(plan, dict) or set(plan) - {
            "stage", "hypothesis", "constraints", "next_action", "artifact_id", "recipe", "reason_code"
        }:
            raise ValueError("Invalid solver checkpoint fields")
        if plan.get("stage") not in {"input_planning", "materializing", "replaying", "diagnosing", "blocked", "inconclusive"}:
            raise ValueError("Invalid solver checkpoint stage")
        if len(json.dumps(plan, ensure_ascii=False).encode("utf-8")) > 8 * 1024:
            raise ValueError("Solver checkpoint exceeds 8 KiB")
        artifact_id = plan.get("artifact_id")
        if artifact_id is not None and self.cybergym_artifact_poc_id(scan_id, artifact_id) != poc_id:
            raise ValueError("Checkpoint artifact must belong to the same PoC")
        task = self.get_cybergym_task(scan_id)
        if task is None or task["status"] != "active":
            raise ValueError("CyberGym task is not active")
        # Plans are agent claims, not validation facts. Events retain their
        # history; replay/submit never use them as crash evidence.
        payload = {"poc_id": poc_id, "plan": plan}
        prior = self.cybergym_checkpoints(scan_id).get(poc_id)
        if prior == payload:
            return payload
        self.append_scan_event(scan_id, "cybergym.checkpoint", "Solver input plan saved", payload)
        return payload

    def cybergym_checkpoints(self, scan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM scan_events WHERE scan_id = ? "
                "AND event_type = 'cybergym.checkpoint' ORDER BY seq", (scan_id,),
            ).fetchall()
        checkpoints = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            checkpoints[payload["poc_id"]] = payload
        return checkpoints

    def create_cybergym_artifact(
        self,
        scan_id: str,
        *,
        kind: str,
        raw: bytes,
        parent_id: str | None,
        provenance: dict[str, Any],
        max_scan_artifacts: int | None = None,
        max_scan_bytes: int | None = None,
    ) -> dict[str, Any]:
        if kind not in {"seed", "corpus", "crash", "minimized", "dictionary"}:
            raise ValueError("Unsupported CyberGym artifact kind")
        if not isinstance(raw, bytes):
            raise ValueError("CyberGym artifact bytes are required")
        if not isinstance(provenance, dict):
            raise ValueError("CyberGym artifact provenance must be an object")
        encoded_provenance = json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        if len(encoded_provenance.encode("utf-8")) > 16 * 1024:
            raise ValueError("CyberGym artifact provenance is too large")
        artifact_id = f"cybergym_artifact_{uuid.uuid4().hex}"
        digest = hashlib.sha256(raw).hexdigest()
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not accepting new artifacts")
            if parent_id is not None:
                parent = connection.execute(
                    "SELECT provenance_json FROM cybergym_artifacts WHERE artifact_id = ? AND scan_id = ?",
                    (parent_id, scan_id),
                ).fetchone()
                if parent is None:
                    raise ValueError("CyberGym artifact parent is not in this task")
                parent_poc_id = json.loads(parent["provenance_json"]).get("poc_id")
                if parent_poc_id is not None:
                    provenance = {**provenance, "poc_id": parent_poc_id}
                    encoded_provenance = json.dumps(provenance, ensure_ascii=False, sort_keys=True)
                    if len(encoded_provenance.encode("utf-8")) > 16 * 1024:
                        raise ValueError("CyberGym artifact provenance is too large")
            requested_poc_id = provenance.get("poc_id")
            existing_rows = connection.execute(
                "SELECT * FROM cybergym_artifacts WHERE scan_id = ? AND sha256 = ? "
                "AND kind = ? AND parent_id IS ?",
                (scan_id, digest, kind, parent_id),
            ).fetchall()
            for existing in existing_rows:
                existing_provenance = json.loads(existing["provenance_json"])
                existing_poc_id = existing_provenance.get("poc_id")
                if existing_poc_id != requested_poc_id and parent_id is not None:
                    raise ValueError("Identical CyberGym artifacts cannot belong to different generic PoCs")
                if existing_poc_id == requested_poc_id:
                    return self._decode_cybergym_artifact(existing)
            # Corpus can be regenerated; seeds, crashes and minimized inputs
            # are required to continue validation. Give them independent pools
            # so even historical over-quota corpus cannot block recovery.
            count, total_bytes = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM cybergym_artifacts "
                "WHERE scan_id = ? AND (kind = 'corpus') = ?",
                (scan_id, int(kind == "corpus")),
            ).fetchone()
            if ((max_scan_artifacts is not None and count >= max_scan_artifacts)
                    or (max_scan_bytes is not None and total_bytes + len(raw) > max_scan_bytes)):
                raise ValueError("CyberGym scan artifact budget exhausted")
            connection.execute(
                "INSERT INTO cybergym_artifacts (artifact_id, scan_id, kind, sha256, "
                "size_bytes, raw_bytes, parent_id, provenance_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, scan_id, kind, digest, len(raw), raw, parent_id, encoded_provenance, now),
            )
            row = connection.execute(
                "SELECT * FROM cybergym_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - defensive persistence boundary
            raise ValueError("CyberGym artifact was not persisted")
        return self._decode_cybergym_artifact(row)

    def get_cybergym_artifact(
        self,
        scan_id: str,
        artifact_id: str,
        *,
        include_data: bool = False,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cybergym_artifacts WHERE scan_id = ? AND artifact_id = ?",
                (scan_id, artifact_id),
            ).fetchone()
        return self._decode_cybergym_artifact(row, include_data=include_data) if row is not None else None

    def list_cybergym_artifacts(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM cybergym_artifacts WHERE scan_id = ? ORDER BY created_at, artifact_id",
                (scan_id,),
            ).fetchall()
        return [self._decode_cybergym_artifact(row) for row in rows]

    def consume_cybergym_budget(self, scan_id: str, kind: str, limit: int) -> int:
        if kind not in {"replay", "gdb", "fuzz", "minimize"}:
            raise ValueError("Unsupported CyberGym budget kind")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("CyberGym budget limit is invalid")
        with self._lock, self._connect() as connection:
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not active")
            connection.execute(
                "INSERT INTO cybergym_budget (scan_id, kind, used) VALUES (?, ?, 0) "
                "ON CONFLICT(scan_id, kind) DO NOTHING",
                (scan_id, kind),
            )
            cursor = connection.execute(
                "UPDATE cybergym_budget SET used = used + 1 WHERE scan_id = ? "
                "AND kind = ? AND used < ?",
                (scan_id, kind, limit),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"CyberGym {kind} budget is exhausted")
            return int(
                connection.execute(
                    "SELECT used FROM cybergym_budget WHERE scan_id = ? AND kind = ?",
                    (scan_id, kind),
                ).fetchone()[0]
            )

    def cybergym_budget(self, scan_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, used FROM cybergym_budget WHERE scan_id = ?", (scan_id,)
            ).fetchall()
        values = {"replay": 0, "gdb": 0, "fuzz": 0, "minimize": 0}
        values.update({row["kind"]: int(row["used"]) for row in rows})
        return values

    def start_cybergym_run(
        self,
        scan_id: str,
        kind: str,
        input_payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"replay", "gdb", "fuzz", "minimize"} or not isinstance(input_payload, dict):
            raise ValueError("CyberGym run is invalid")
        if idempotency_key is not None:
            if kind != "fuzz":
                raise ValueError("CyberGym idempotency is only supported for fuzz runs")
            if not idempotency_key or len(idempotency_key) > 128:
                raise ValueError("CyberGym fuzz idempotency key is invalid")
        encoded_input = json.dumps(input_payload, ensure_ascii=False, sort_keys=True)
        run_id = f"cybergym_run_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            if idempotency_key is not None:
                connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not active")
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM cybergym_runs WHERE scan_id = ? AND kind = 'fuzz' "
                    "AND idempotency_key = ?",
                    (scan_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["input_json"] != encoded_input:
                        raise ValueError("CyberGym fuzz idempotency key was reused with different input")
                    output = self._decode_cybergym_run(existing)
                    output["reused"] = True
                    return output
            connection.execute(
                "INSERT INTO cybergym_runs "
                "(run_id, scan_id, kind, status, idempotency_key, input_json, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 'running', ?, ?, NULL, ?, ?)",
                (run_id, scan_id, kind, idempotency_key, encoded_input, now, now),
            )
        return {"run_id": run_id, "scan_id": scan_id, "kind": kind, "status": "running", "reused": False}

    def start_cybergym_fuzz_run(
        self,
        scan_id: str,
        input_payload: dict[str, Any],
        *,
        idempotency_key: str,
        budget_limit: int,
        container_name: str,
    ) -> dict[str, Any]:
        """Atomically reuse or create one budgeted fuzz run for a solver request."""
        if not isinstance(input_payload, dict):
            raise ValueError("CyberGym fuzz input is invalid")
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 128
        ):
            raise ValueError("CyberGym fuzz idempotency key is invalid")
        if not isinstance(budget_limit, int) or isinstance(budget_limit, bool) or budget_limit < 1:
            raise ValueError("CyberGym fuzz budget limit is invalid")
        if not isinstance(container_name, str) or not container_name or len(container_name) > 128:
            raise ValueError("CyberGym fuzz container name is invalid")
        encoded_input = json.dumps(input_payload, ensure_ascii=False, sort_keys=True)
        run_id = f"cybergym_run_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not active")
            existing = connection.execute(
                "SELECT * FROM cybergym_runs WHERE scan_id = ? AND kind = 'fuzz' "
                "AND idempotency_key = ?",
                (scan_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["input_json"] != encoded_input:
                    raise ValueError("CyberGym fuzz idempotency key was reused with different input")
                output = self._decode_cybergym_run(existing)
                output["reused"] = True
                return output
            connection.execute(
                "INSERT INTO cybergym_budget (scan_id, kind, used) VALUES (?, 'fuzz', 0) "
                "ON CONFLICT(scan_id, kind) DO NOTHING",
                (scan_id,),
            )
            budget = connection.execute(
                "UPDATE cybergym_budget SET used = used + 1 WHERE scan_id = ? "
                "AND kind = 'fuzz' AND used < ?",
                (scan_id, budget_limit),
            )
            if budget.rowcount != 1:
                raise ValueError("CyberGym fuzz budget is exhausted")
            connection.execute(
                "INSERT INTO cybergym_runs "
                "(run_id, scan_id, kind, status, idempotency_key, owner_token, "
                "owner_lease_expires_at, container_name, input_json, result_json, created_at, updated_at) "
                "VALUES (?, ?, 'fuzz', 'running', ?, NULL, NULL, ?, ?, NULL, ?, ?)",
                (
                    run_id,
                    scan_id,
                    idempotency_key,
                    container_name,
                    encoded_input,
                    now,
                    now,
                ),
            )
        return {
            "run_id": run_id,
            "scan_id": scan_id,
            "kind": "fuzz",
            "status": "running",
            "reused": False,
        }

    def get_cybergym_fuzz_run_by_idempotency(
        self,
        scan_id: str,
        input_payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        if not isinstance(input_payload, dict):
            raise ValueError("CyberGym fuzz input is invalid")
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 128
        ):
            raise ValueError("CyberGym fuzz idempotency key is invalid")
        encoded_input = json.dumps(input_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task is not active")
            row = connection.execute(
                "SELECT * FROM cybergym_runs WHERE scan_id = ? AND kind = 'fuzz' "
                "AND idempotency_key = ?",
                (scan_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if row["input_json"] != encoded_input:
            raise ValueError("CyberGym fuzz idempotency key was reused with different input")
        output = self._decode_cybergym_run(row)
        output["reused"] = True
        return output

    def finish_cybergym_run(self, run_id: str, status: str, result: dict[str, Any]) -> None:
        if status not in {"completed", "failed", "cancelled"} or not isinstance(result, dict):
            raise ValueError("CyberGym run terminal result is invalid")
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValueError("CyberGym run result is too large")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cybergym_runs SET status = ?, result_json = ?, updated_at = ? "
                "WHERE run_id = ? AND status = 'running'",
                (status, encoded, _now(), run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("CyberGym run is not active")

    def record_cybergym_fuzz_cleanup(self, run_id: str, cleanup: dict[str, Any]) -> None:
        """Attach verified container-cleanup diagnostics to a terminal fuzz run.

        Cleanup is operational evidence, not a second outcome dimension: a
        cancelled fuzz job remains cancelled even when Docker removal needs a
        later reaper.  Persisting the distinction prevents an orphaned
        container from being silently treated as successfully cleaned up.
        """
        status = cleanup.get("status") if isinstance(cleanup, dict) else None
        if status not in {"removed", "not_found", "not_requested", "not_supported", "unavailable", "failed"}:
            raise ValueError("CyberGym fuzz cleanup status is invalid")
        detail = cleanup.get("detail")
        if detail is not None and (not isinstance(detail, str) or len(detail) > 1_000):
            raise ValueError("CyberGym fuzz cleanup detail is invalid")
        recorded = {"status": status}
        if isinstance(detail, str) and detail:
            recorded["detail"] = detail
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, result_json FROM cybergym_runs WHERE run_id = ? AND kind = 'fuzz'",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("CyberGym fuzz run is not available")
            if row["status"] == "running":
                raise ValueError("CyberGym fuzz run is still active")
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            result["container_cleanup"] = recorded
            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
            if len(encoded.encode("utf-8")) > 128 * 1024:
                raise ValueError("CyberGym run result is too large")
            connection.execute(
                "UPDATE cybergym_runs SET result_json = ?, updated_at = ? WHERE run_id = ?",
                (encoded, _now(), run_id),
            )

    def get_cybergym_run(self, scan_id: str, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cybergym_runs WHERE scan_id = ? AND run_id = ?", (scan_id, run_id)
            ).fetchone()
        return self._decode_cybergym_run(row) if row is not None else None

    def get_cybergym_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cybergym_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._decode_cybergym_run(row) if row is not None else None

    def assert_cybergym_runs_terminal(self, scan_id: str) -> None:
        with self._connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM cybergym_runs WHERE scan_id = ? AND status = 'running' LIMIT 1",
                (scan_id,),
            ).fetchone()
        if active is not None:
            raise ValueError("CyberGym execution is still running; wait for persisted artifacts before submission")

    def list_cybergym_runs(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM cybergym_runs WHERE scan_id = ? ORDER BY created_at, run_id", (scan_id,)
            ).fetchall()
        return [self._decode_cybergym_run(row) for row in rows]

    def cybergym_artifact_has_stable_crash(self, scan_id: str, artifact_id: str) -> bool:
        return self.cybergym_artifact_evidence(scan_id, artifact_id)["stable_crash"]

    def cybergym_artifact_evidence(self, scan_id: str, artifact_id: str) -> dict[str, Any]:
        replay: list[dict[str, Any]] = []
        gdb: list[dict[str, Any]] = []
        for run in self.list_cybergym_runs(scan_id):
            if run["input"].get("artifact_id") != artifact_id:
                continue
            if run["kind"] == "replay" and run["status"] == "completed":
                replay.append(run["result"] or {})
            elif run["kind"] == "gdb":
                gdb.append(run["result"] or {})
        return {
            "replay": replay,
            "gdb": gdb,
            "stable_crash": any(count >= 2 for count in Counter(
                (item.get("manifest_digest"), item.get("crash_signature"))
                for item in replay if item.get("crash") is True
            ).values()),
            "vulnerable_branch_reached": any(item.get("vulnerable_branch_reached") is True for item in gdb),
            "target_reached": any(item.get("target_reached") is True for item in gdb),
        }

    def select_cybergym_final_artifact(self, scan_id: str) -> dict[str, Any] | None:
        """Return the best locally verified crash artifact for official submission."""
        artifacts = self.list_cybergym_artifacts(scan_id)
        if not artifacts:
            return None
        ranked: list[tuple[tuple[int, str, str], dict[str, Any], dict[str, Any]]] = []
        for artifact in artifacts:
            evidence = self.cybergym_artifact_evidence(scan_id, artifact["artifact_id"])
            if not evidence["stable_crash"]:
                continue
            score = 100
            if evidence["vulnerable_branch_reached"]:
                score += 50
            if evidence["target_reached"]:
                score += 25
            if artifact["kind"] in {"crash", "minimized"}:
                score += 10
            poc_id = self.cybergym_artifact_poc_id(scan_id, artifact["artifact_id"]) or ""
            ranked.append(((-score, poc_id, artifact["artifact_id"]), artifact, evidence))
        if not ranked:
            return None
        _score, artifact, evidence = min(ranked, key=lambda item: item[0])
        validation, reason = "verified", "stable vulnerable-side crash replay"
        return {"artifact": artifact, "local_validation": validation, "selection_reason": reason, "evidence": evidence}

    def reserve_cybergym_submission(
        self,
        scan_id: str,
        *,
        artifact_id: str,
        local_validation: str,
        selection_reason: str,
        evidence: dict[str, Any],
        selected_poc_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        submission_id = f"cybergym_submission_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("CyberGym task has already been finalized")
            artifact = connection.execute(
                "SELECT 1 FROM cybergym_artifacts WHERE scan_id = ? AND artifact_id = ?",
                (scan_id, artifact_id),
            ).fetchone()
            if artifact is None:
                raise ValueError("CyberGym submission artifact is not in this task")
            if selected_poc_id is not None:
                owner = connection.execute(
                    "SELECT 1 FROM poc_bundles WHERE scan_id = ? AND poc_id = ? AND status = 'generated'",
                    (scan_id, selected_poc_id),
                ).fetchone()
                if owner is None:
                    raise ValueError("Selected PoC is not a generated bundle for this scan")
            connection.execute(
                "INSERT INTO cybergym_submissions (scan_id, submission_id, artifact_id, local_validation, "
                "selection_reason, evidence_json, official_result_json, status, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'submitting', ?, NULL)",
                (
                    scan_id, submission_id, artifact_id, local_validation, selection_reason,
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True), now,
                ),
            )
            connection.execute(
                "UPDATE cybergym_tasks SET status = 'submitting', final_artifact_id = ?, "
                "selected_poc_id = ?, local_validation = ?, selection_reason = ?, updated_at = ? WHERE scan_id = ?",
                (artifact_id, selected_poc_id, local_validation, selection_reason, now, scan_id),
            )
        submission = self.get_cybergym_submission(scan_id)
        if submission is None:  # pragma: no cover - defensive persistence boundary
            raise ValueError("CyberGym submission was not persisted")
        return submission

    def complete_cybergym_submission(self, scan_id: str, official_result: dict[str, Any]) -> None:
        if not isinstance(official_result, dict):
            raise ValueError("CyberGym official result must be an object")
        encoded = json.dumps(official_result, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("CyberGym official result is too large")
        now = _now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cybergym_submissions SET official_result_json = ?, status = 'completed', completed_at = ? "
                "WHERE scan_id = ? AND status = 'submitting'",
                (encoded, now, scan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("CyberGym submission is not awaiting an official result")
            connection.execute(
                "UPDATE cybergym_tasks SET status = 'submitted', updated_at = ? WHERE scan_id = ?",
                (now, scan_id),
            )

    def get_cybergym_submission(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cybergym_submissions WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        raw = item.pop("official_result_json")
        item["official_result"] = json.loads(raw) if raw else None
        return item

    def mark_cybergym_failed_no_artifact(
        self,
        scan_id: str,
        *,
        selection_reason: str = "no generated artifact",
    ) -> dict[str, Any]:
        if not isinstance(selection_reason, str) or not selection_reason.strip() or len(selection_reason) > 2_000:
            raise ValueError("selection_reason must be a non-empty string of at most 2000 characters")
        now = _now()
        with self._lock, self._connect() as connection:
            task = connection.execute(
                "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if task is None:
                raise ValueError("CyberGym task is not available")
            active_run = connection.execute(
                "SELECT 1 FROM cybergym_runs WHERE scan_id = ? AND status = 'running' LIMIT 1",
                (scan_id,),
            ).fetchone()
            if active_run is not None:
                raise ValueError("CyberGym execution is still running; wait before marking no artifact")
            if self.select_cybergym_final_artifact(scan_id) is not None:
                raise ValueError("CyberGym task has a verified artifact and must submit it")
            cursor = connection.execute(
                "UPDATE cybergym_tasks SET status = 'failed_no_artifact', local_validation = 'failed_no_artifact', "
                "selection_reason = ?, updated_at = ? WHERE scan_id = ? AND status = 'active'",
                (selection_reason.strip(), now, scan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("CyberGym task has already been finalized")
        task = self.get_cybergym_task(scan_id)
        if task is None:  # pragma: no cover - defensive persistence boundary
            raise ValueError("CyberGym task disappeared")
        return task

    def cancel_scan_work(self, scan_id: str) -> list[str]:
        with self._lock, self._connect() as connection:
            now = _now()
            task_rows = connection.execute(
                "SELECT wa.background_task_id FROM work_attempts wa "
                "JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id "
                "WHERE wu.scan_id = ? "
                "AND wa.status IN ('pending', 'running', 'recovering') "
                "AND wa.background_task_id IS NOT NULL",
                (scan_id,),
            ).fetchall()
            connection.execute(
                "UPDATE work_attempts SET status = 'cancelled', updated_at = ?, "
                "finished_at = ? WHERE work_unit_id IN ("
                "SELECT work_unit_id FROM work_units WHERE scan_id = ?"
                ") AND status IN ('pending', 'running', 'recovering')",
                (now, now, scan_id),
            )
            connection.execute(
                "UPDATE work_units SET status = 'cancelled', updated_at = ?, "
                "finished_at = ? "
                "WHERE scan_id = ? AND status IN ('pending', 'running')",
                (now, now, scan_id),
            )
            connection.execute(
                "UPDATE worker_batches SET status = 'cancelled', updated_at = ? "
                "WHERE scan_id = ? AND status IN ('pending', 'running')",
                (now, scan_id),
            )
            connection.execute(
                "DELETE FROM worker_capacity_leases WHERE work_unit_id IN ("
                "SELECT work_unit_id FROM work_units WHERE scan_id = ?"
                ")",
                (scan_id,),
            )
        return [row["background_task_id"] for row in task_rows]

    @staticmethod
    def _require_analysis_ready(
        connection: sqlite3.Connection,
        scan_id: str,
    ) -> None:
        active = connection.execute(
            "SELECT COUNT(*) FROM work_units WHERE scan_id = ? AND status IN ('pending', 'running')",
            (scan_id,),
        ).fetchone()[0]
        analysis_units = connection.execute(
            "SELECT COUNT(*) FROM work_units WHERE scan_id = ? AND role IN ('baseline', 'investigator')",
            (scan_id,),
        ).fetchone()[0]
        unverified = connection.execute(
            "SELECT COUNT(*) FROM candidates c "
            "LEFT JOIN verifications v ON v.candidate_id = c.candidate_id "
            "WHERE c.scan_id = ? AND v.candidate_id IS NULL",
            (scan_id,),
        ).fetchone()[0]
        conflicts = connection.execute(
            "SELECT COUNT(*) FROM verification_conflicts vc "
            "JOIN candidates c ON c.candidate_id = vc.candidate_id "
            "WHERE c.scan_id = ?",
            (scan_id,),
        ).fetchone()[0]
        if active:
            raise ValueError("Audit workers are still active")
        if not analysis_units:
            raise ValueError("At least one baseline analysis work unit is required")
        if unverified:
            raise ValueError("Every candidate requires an independent verification verdict")
        if conflicts:
            raise ValueError("Verification conflicts must be resolved before adjudication")

    @staticmethod
    def _require_dynamic_ready(
        connection: sqlite3.Connection,
        scan_id: str,
        *,
        scan: sqlite3.Row | None = None,
    ) -> None:
        scan = (
            scan
            or connection.execute(
                "SELECT * FROM scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        )
        if scan is None:
            raise ValueError("Scan not found")
        if not bool(scan["dynamic_enabled"]):
            return
        rows = connection.execute(
            """
            SELECT c.candidate_id, d.status, d.probe_json, d.run_json
            FROM candidates c
            JOIN verifications v ON v.candidate_id = c.candidate_id
            LEFT JOIN dynamic_runs d ON d.candidate_id = c.candidate_id
            WHERE c.scan_id = ? AND v.verdict = 'confirmed'
            ORDER BY c.candidate_id
            """,
            (scan_id,),
        ).fetchall()
        for row in rows:
            if row["status"] is None:
                raise ValueError("Every statically confirmed candidate requires a dynamic run record")
            if row["status"] == "ready":
                raise ValueError("Dynamic probe execution is still pending")
            if row["status"] not in {"not_runnable", "completed", "inconclusive"}:
                raise ValueError("Dynamic run has an invalid terminal status")
            try:
                probe = json.loads(row["probe_json"])
                run = json.loads(row["run_json"]) if row["run_json"] else None
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("Dynamic run contains invalid JSON") from exc
            if row["status"] == "not_runnable":
                if probe.get("status") != "not_runnable" or run is not None:
                    raise ValueError("not_runnable dynamic run is inconsistent")
            elif (
                probe.get("status") != "runnable"
                or not isinstance(run, dict)
                or run.get("runner_status") != row["status"]
            ):
                raise ValueError("Terminal dynamic run facts are inconsistent")

    @staticmethod
    def _next_adjudication_round(
        connection: sqlite3.Connection,
        scan_id: str,
    ) -> int:
        latest = connection.execute(
            "SELECT adjudication_round, action FROM adjudications "
            "WHERE scan_id = ? ORDER BY adjudication_round DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if latest is None:
            return 1
        if latest["action"] == "finalize":
            raise ValueError("A final adjudication has already been submitted")
        if latest["adjudication_round"] != 1:
            raise ValueError("The maximum adjudication round has been reached")
        rescan = connection.execute(
            "SELECT status FROM worker_batches WHERE scan_id = ? "
            "AND phase = 'targeted_rescan' ORDER BY created_at DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        if rescan is None:
            raise ValueError("The directed targeted rescan has not been started")
        if rescan["status"] in {"pending", "running"}:
            raise ValueError("The directed targeted rescan is still active")
        return 2

    @staticmethod
    def _decode_adjudication(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["accepted_candidate_ids"] = json.loads(item.pop("accepted_candidate_ids_json"))
        item["rejected_candidates"] = json.loads(item.pop("rejected_candidates_json"))
        raw_rescan = item.pop("rescan_json")
        item["rescan"] = json.loads(raw_rescan) if raw_rescan else None
        raw_assessments = item.pop("dynamic_assessments_json")
        item["dynamic_assessments"] = json.loads(raw_assessments) if raw_assessments else None
        return item

    def get_latest_adjudication(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM adjudications WHERE scan_id = ? ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
        return self._decode_adjudication(row) if row is not None else None

    def _require_targeted_rescan_directive(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM adjudications WHERE scan_id = ? AND adjudication_round = 1 AND action = 'targeted_rescan'",
            (scan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Targeted rescan requires a round-one direction")
        directive = self._decode_adjudication(row).get("rescan")
        if not isinstance(directive, dict):
            raise ValueError("Targeted-rescan direction is missing")
        return directive

    def get_targeted_rescan_directive(self, scan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            return self._require_targeted_rescan_directive(connection, scan_id)

    def get_adjudication_context(self, scan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            self._require_threat_model_ready(connection, scan_id)
            self._require_analysis_ready(connection, scan_id)
            self._require_dynamic_ready(connection, scan_id)
            adjudication_round = self._next_adjudication_round(
                connection,
                scan_id,
            )
        data = self.report_data(scan_id)
        evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for evidence in data["evidence"]:
            evidence_by_candidate.setdefault(
                evidence["candidate_id"],
                [],
            ).append(evidence)
        verification_by_candidate = {item["candidate_id"]: item for item in data["verifications"]}
        dynamic_by_candidate = {item["candidate_id"]: item for item in data["dynamic_runs"]}
        candidates: list[dict[str, Any]] = []
        for candidate in data["candidates"]:
            item = dict(candidate)
            item["evidence"] = evidence_by_candidate.get(
                candidate["candidate_id"],
                [],
            )
            item["verification"] = verification_by_candidate.get(candidate["candidate_id"])
            item["dynamic_run"] = dynamic_by_candidate.get(candidate["candidate_id"])
            candidates.append(item)
        return {
            "scan_id": scan_id,
            "dynamic_enabled": data["scan"]["dynamic_enabled"],
            "adjudication_round": adjudication_round,
            "threat_model": data["threat_model"],
            "candidates": candidates,
            "coverage": data["coverage"],
            "omissions": data["omissions"],
        }

    @staticmethod
    def _normalize_rescan_path(raw_path: Any) -> str:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("Targeted-rescan paths must be non-empty strings")
        if len(raw_path) > 1_024 or "\x00" in raw_path or "\\" in raw_path:
            raise ValueError("Targeted-rescan path is not canonical")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != raw_path:
            raise ValueError("Targeted-rescan path is not snapshot-relative")
        normalized = path.as_posix()
        if normalized == ".":
            raise ValueError("Targeted rescan requires a narrower path than '.'")
        return normalized

    def save_adjudication(
        self,
        scan_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(decision, dict):
            raise ValueError("decision must be an object")
        action = decision.get("action")
        if action not in {"finalize", "targeted_rescan"}:
            raise ValueError("Adjudication action must be finalize or targeted_rescan")
        accepted: list[str] = []
        normalized_rejected: list[dict[str, str]] = []
        normalized_rescan: dict[str, Any] | None = None
        normalized_assessments: list[dict[str, str]] | None = None
        if action == "finalize":
            static_fields = {
                "action",
                "accepted_candidate_ids",
                "rejected_candidates",
            }
            dynamic_fields = static_fields | {"dynamic_assessments"}
            if frozenset(decision) not in {
                frozenset(static_fields),
                frozenset(dynamic_fields),
            }:
                raise ValueError("Final adjudication has unsupported fields")
            raw_accepted = decision["accepted_candidate_ids"]
            rejected = decision["rejected_candidates"]
            if not isinstance(raw_accepted, list) or not all(isinstance(item, str) and item for item in raw_accepted):
                raise ValueError("accepted_candidate_ids must be an array of identifiers")
            if len(raw_accepted) != len(set(raw_accepted)):
                raise ValueError("accepted_candidate_ids contains duplicates")
            accepted = sorted(raw_accepted)
            if not isinstance(rejected, list):
                raise ValueError("rejected_candidates must be an array")
            for item in rejected:
                if not isinstance(item, dict) or set(item) != {"candidate_id", "reason"}:
                    raise ValueError("Each rejected candidate requires only candidate_id and reason")
                candidate_id = item["candidate_id"]
                reason = item["reason"]
                if not isinstance(candidate_id, str) or not candidate_id:
                    raise ValueError("Rejected candidate_id must be non-empty")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("Every rejected candidate requires a reason")
                if len(reason.strip()) > 4_000:
                    raise ValueError("Rejected-candidate reason is too long")
                normalized_rejected.append({"candidate_id": candidate_id, "reason": reason.strip()})
            rejected_ids = [item["candidate_id"] for item in normalized_rejected]
            if len(rejected_ids) != len(set(rejected_ids)):
                raise ValueError("rejected_candidates contains duplicates")
            if set(accepted) & set(rejected_ids):
                raise ValueError("A candidate cannot be both accepted and rejected")
            normalized_rejected.sort(key=lambda item: item["candidate_id"])
            if "dynamic_assessments" in decision:
                raw_assessments = decision["dynamic_assessments"]
                if not isinstance(raw_assessments, list):
                    raise ValueError("dynamic_assessments must be an array")
                normalized_assessments = []
                for item in raw_assessments:
                    if not isinstance(item, dict) or set(item) != {
                        "candidate_id",
                        "conclusion",
                        "rationale",
                    }:
                        raise ValueError(
                            "Each dynamic assessment requires only candidate_id, conclusion, and rationale"
                        )
                    candidate_id = item["candidate_id"]
                    conclusion = item["conclusion"]
                    rationale = item["rationale"]
                    if not isinstance(candidate_id, str) or not candidate_id:
                        raise ValueError("Dynamic assessment candidate_id is required")
                    if conclusion not in {
                        "reproduced",
                        "not_reproduced",
                        "inconclusive",
                        "not_run",
                    }:
                        raise ValueError("Unsupported dynamic assessment conclusion")
                    if not isinstance(rationale, str) or not rationale.strip() or len(rationale.strip()) > 10_000:
                        raise ValueError("Dynamic assessment rationale must contain 1 to 10000 characters")
                    normalized_assessments.append(
                        {
                            "candidate_id": candidate_id,
                            "conclusion": conclusion,
                            "rationale": rationale.strip(),
                        }
                    )
                assessment_ids = [item["candidate_id"] for item in normalized_assessments]
                if len(assessment_ids) != len(set(assessment_ids)):
                    raise ValueError("dynamic_assessments contains duplicates")
                normalized_assessments.sort(key=lambda item: item["candidate_id"])
        else:
            if set(decision) != {"action", "rescan"}:
                raise ValueError("Targeted rescan requires only the rescan direction")
            raw_rescan = decision["rescan"]
            if not isinstance(raw_rescan, dict) or set(raw_rescan) != {
                "reason",
                "paths",
                "questions",
            }:
                raise ValueError("Targeted rescan requires only reason, paths, and questions")
            reason = raw_rescan["reason"]
            paths = raw_rescan["paths"]
            questions = raw_rescan["questions"]
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 4_000:
                raise ValueError("Targeted-rescan reason must contain 1 to 4000 characters")
            if (
                not isinstance(paths, list)
                or not 1 <= len(paths) <= 32
                or not isinstance(questions, list)
                or not 1 <= len(questions) <= 32
            ):
                raise ValueError("Targeted rescan requires between 1 and 32 paths and questions")
            normalized_paths = sorted(self._normalize_rescan_path(item) for item in paths)
            if len(normalized_paths) != len(set(normalized_paths)):
                raise ValueError("Targeted-rescan paths contain duplicates")
            normalized_questions = []
            for question in questions:
                if not isinstance(question, str) or not question.strip() or len(question.strip()) > 1_000:
                    raise ValueError("Each targeted-rescan question must contain 1 to 1000 characters")
                normalized_questions.append(question.strip())
            normalized_rescan = {
                "reason": reason.strip(),
                "paths": normalized_paths,
                "questions": normalized_questions,
            }

        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            self._require_threat_model_ready(connection, scan_id)
            self._require_analysis_ready(connection, scan_id)
            self._require_dynamic_ready(connection, scan_id, scan=scan)
            adjudication_round = self._next_adjudication_round(connection, scan_id)
            dynamic_enabled = bool(scan["dynamic_enabled"])
            if action == "finalize":
                if dynamic_enabled and normalized_assessments is None:
                    raise ValueError("Dynamic scans require one assessment per confirmed candidate")
                if not dynamic_enabled and normalized_assessments is not None:
                    raise ValueError("Static scans do not accept dynamic assessments")
            if adjudication_round == 2 and action != "finalize":
                raise ValueError("The second adjudication must finalize the scan")
            if action == "finalize":
                candidate_rows = connection.execute(
                    "SELECT c.candidate_id, v.verdict FROM candidates c "
                    "JOIN verifications v ON v.candidate_id = c.candidate_id "
                    "WHERE c.scan_id = ? ORDER BY c.created_at, c.candidate_id",
                    (scan_id,),
                ).fetchall()
                candidate_ids = {row["candidate_id"] for row in candidate_rows}
                rejected_ids = [item["candidate_id"] for item in normalized_rejected]
                if set(accepted) | set(rejected_ids) != candidate_ids:
                    raise ValueError("Every scan candidate must be classified exactly once")
                verdict_by_candidate = {row["candidate_id"]: row["verdict"] for row in candidate_rows}
                invalid_accepts = [
                    candidate_id for candidate_id in accepted if verdict_by_candidate.get(candidate_id) != "confirmed"
                ]
                if invalid_accepts:
                    raise ValueError("Only independently confirmed candidates may be accepted")
                if dynamic_enabled:
                    confirmed_ids = {row["candidate_id"] for row in candidate_rows if row["verdict"] == "confirmed"}
                    assessments = normalized_assessments or []
                    if {item["candidate_id"] for item in assessments} != confirmed_ids:
                        raise ValueError("Every statically confirmed candidate requires exactly one dynamic assessment")
                    run_statuses = {
                        row["candidate_id"]: row["status"]
                        for row in connection.execute(
                            "SELECT candidate_id, status FROM dynamic_runs WHERE scan_id = ?",
                            (scan_id,),
                        ).fetchall()
                    }
                    allowed_conclusions = {
                        "not_runnable": {"not_run"},
                        "inconclusive": {"inconclusive"},
                        "completed": {"reproduced", "not_reproduced"},
                    }
                    for assessment in assessments:
                        status = run_statuses.get(assessment["candidate_id"])
                        if assessment["conclusion"] not in allowed_conclusions.get(status, set()):
                            raise ValueError("Dynamic assessment conclusion does not match run status")
            if normalized_rescan is not None:
                snapshot_paths = {
                    row["relative_path"]
                    for row in connection.execute(
                        "SELECT relative_path FROM snapshot_files WHERE snapshot_id = ?",
                        (scan["snapshot_id"],),
                    ).fetchall()
                }
                snapshot_paths.update(
                    row["relative_path"]
                    for row in connection.execute(
                        "SELECT relative_path FROM snapshot_omissions WHERE snapshot_id = ?",
                        (scan["snapshot_id"],),
                    ).fetchall()
                )
                for path in normalized_rescan["paths"]:
                    if not any(item == path or item.startswith(f"{path}/") for item in snapshot_paths):
                        raise ValueError(f"Targeted-rescan path is outside the snapshot: {path}")
            connection.execute(
                "INSERT INTO adjudications ("
                "scan_id, adjudication_round, action, "
                "accepted_candidate_ids_json, rejected_candidates_json, "
                "rescan_json, dynamic_assessments_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    adjudication_round,
                    action,
                    json.dumps(accepted, ensure_ascii=False),
                    json.dumps(normalized_rejected, ensure_ascii=False),
                    (json.dumps(normalized_rescan, ensure_ascii=False) if normalized_rescan is not None else None),
                    (
                        json.dumps(normalized_assessments, ensure_ascii=False)
                        if normalized_assessments is not None
                        else None
                    ),
                    _now(),
                ),
            )
        saved = self.get_latest_adjudication(scan_id)
        if saved is None:
            raise ValueError("Adjudication was not persisted")
        return saved

    def ensure_ready_to_finalize(self, scan_id: str) -> None:
        with self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            self._require_threat_model_ready(connection, scan_id)
            self._require_analysis_ready(connection, scan_id)
            self._require_dynamic_ready(connection, scan_id)
            if bool(scan["poc_enabled"]):
                missing_poc = connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates c
                    JOIN verifications v ON v.candidate_id = c.candidate_id
                    LEFT JOIN poc_bundles p ON p.candidate_id = c.candidate_id
                    WHERE c.scan_id = ? AND v.verdict = 'confirmed'
                      AND p.candidate_id IS NULL
                      AND EXISTS (
                        SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                        WHERE a.scan_id = c.scan_id AND a.action = 'finalize'
                          AND a.adjudication_round = (
                            SELECT MAX(latest.adjudication_round)
                            FROM adjudications latest
                            WHERE latest.scan_id = c.scan_id AND latest.action = 'finalize'
                          )
                          AND accepted.value = c.candidate_id
                      )
                    """,
                    (scan_id,),
                ).fetchone()[0]
                # Active workers were rejected above. Exhausted PoC failures may
                # be sealed by ReportWriter, which keeps the scan failed.
                if missing_poc and self.list_confirmed_without_poc_record(scan_id):
                    raise ValueError(
                        "PoC generation must produce one bundle for every confirmed candidate "
                        f"before finalization ({missing_poc} remaining)"
                    )
            if scan["mode"] == "cybergym_level1":
                task = connection.execute(
                    "SELECT status FROM cybergym_tasks WHERE scan_id = ?", (scan_id,)
                ).fetchone()
                if task is None or task["status"] not in {"submitted", "failed_no_artifact"}:
                    raise ValueError("CyberGym task must select a final artifact before report finalization")
            latest = connection.execute(
                "SELECT action FROM adjudications WHERE scan_id = ? ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
        if latest is None or latest["action"] != "finalize":
            raise ValueError("A final parent-agent adjudication is required")
        if scan["coverage_policy"] == "exhaustive":
            coverage = self.analysis_coverage_summary(scan_id)
            if coverage["completeness"] != "complete":
                raise CoverageBlockedError(coverage)

    def get_work_unit(self, work_unit_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM work_units WHERE work_unit_id = ?",
                (work_unit_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["paths"] = json.loads(item.pop("paths_json"))
        return item

    def set_work_unit_timing(
        self,
        work_unit_id: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        if started_at is None and finished_at is None:
            return
        for value in (started_at, finished_at):
            if value is None:
                continue
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("Work-unit timestamps must include a timezone")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE work_units SET "
                "started_at = COALESCE(?, started_at), "
                "finished_at = COALESCE(?, finished_at) "
                "WHERE work_unit_id = ?",
                (started_at, finished_at, work_unit_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work unit not found")

    def update_work_unit_status(self, work_unit_id: str, status: str) -> None:
        if status not in {"pending", "running", "completed", "failed", "cancelled"}:
            raise ValueError("Unsupported work-unit status")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM work_units WHERE work_unit_id = ?",
                (work_unit_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Work unit not found")
            allowed = {
                "pending": {"running", "failed", "cancelled"},
                "running": {"completed", "failed", "cancelled"},
            }
            if status == row["status"]:
                return
            if status not in allowed.get(row["status"], set()):
                raise ValueError("Unsupported work-unit status transition")
            now = _now()
            started_at = now if status == "running" else None
            finished_at = now if status in {"completed", "failed", "cancelled"} else None
            cursor = connection.execute(
                "UPDATE work_units SET status = ?, updated_at = ?, "
                "started_at = COALESCE(started_at, ?), "
                "finished_at = COALESCE(finished_at, ?) "
                "WHERE work_unit_id = ? AND status = ?",
                (
                    status,
                    now,
                    started_at,
                    finished_at,
                    work_unit_id,
                    row["status"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work unit status changed concurrently")
            if status in {"completed", "failed", "cancelled"}:
                connection.execute(
                    "UPDATE work_attempts SET status = ?, updated_at = ?, "
                    "finished_at = COALESCE(finished_at, ?) "
                    "WHERE work_unit_id = ? "
                    "AND status IN ('pending', 'running', 'recovering')",
                    (status, now, now, work_unit_id),
                )
                connection.execute(
                    "DELETE FROM worker_capacity_leases WHERE work_unit_id = ?",
                    (work_unit_id,),
                )

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["dynamic_enabled"] = bool(item["dynamic_enabled"])
        item["poc_enabled"] = bool(item.get("poc_enabled", 0))
        item["bash_enabled"] = bool(item.get("bash_enabled", 0))
        item["web_search_enabled"] = bool(item.get("web_search_enabled", 0))
        return item

    def find_scan_by_idempotency(
        self,
        owner_subject: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scans WHERE owner_subject = ? AND idempotency_key = ?",
                (owner_subject, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["dynamic_enabled"] = bool(item["dynamic_enabled"])
        item["poc_enabled"] = bool(item.get("poc_enabled", 0))
        item["bash_enabled"] = bool(item.get("bash_enabled", 0))
        item["web_search_enabled"] = bool(item.get("web_search_enabled", 0))
        return item

    def set_scan_request_metadata(
        self,
        scan_id: str,
        *,
        owner_subject: str,
        request_source: str,
        workspace_ref: str | None,
        idempotency_key: str | None,
        request_digest: str,
        task_owner_pid: int | None = None,
        task_owner_token: str | None = None,
        task_owner_identity: str | None = None,
        knowledge_base: dict[str, Any] | None = None,
        bash_enabled: bool = False,
        web_search_enabled: bool = False,
    ) -> None:
        if knowledge_base is not None:
            display_name = knowledge_base.get("display_name")
            content = knowledge_base.get("content")
            content_sha256 = knowledge_base.get("sha256")
            byte_length = knowledge_base.get("byte_length")
            if not isinstance(display_name, str) or not display_name:
                raise ValueError("Knowledge base display name is invalid")
            if not isinstance(content, str) or not content.strip() or "\0" in content:
                raise ValueError("Knowledge base content is invalid")
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_KNOWLEDGE_BASE_BYTES:
                raise ValueError("Knowledge base may contain at most 32 KiB")
            if byte_length != len(encoded) or content_sha256 != hashlib.sha256(encoded).hexdigest():
                raise ValueError("Knowledge base metadata does not match its content")
        try:
            with self._lock, self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE scans SET owner_subject = ?, request_source = ?, "
                    "workspace_ref = ?, idempotency_key = ?, request_digest = ?, "
                    "task_owner_pid = ?, task_owner_token = ?, task_owner_identity = ?, "
                    "bash_enabled = ?, web_search_enabled = ?, "
                    "current_phase = 'snapshot', updated_at = ? "
                    "WHERE scan_id = ?",
                    (
                        owner_subject,
                        request_source,
                        workspace_ref,
                        idempotency_key,
                        request_digest,
                        task_owner_pid,
                        task_owner_token,
                        task_owner_identity,
                        int(bash_enabled),
                        int(web_search_enabled),
                        _now(),
                        scan_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Scan not found")
                if knowledge_base is not None:
                    connection.execute(
                        "INSERT INTO scan_knowledge_base VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            scan_id,
                            display_name,
                            content,
                            content_sha256,
                            byte_length,
                            _now(),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Idempotency key already belongs to another scan") from exc

    def set_current_phase(self, scan_id: str, phase: str | None) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE scans SET current_phase = ?, updated_at = ? WHERE scan_id = ?",
                (phase, _now(), scan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Scan not found")

    def set_scan_output_dir(self, scan_id: str, output_dir: Path) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE scans SET output_dir = ?, updated_at = ? WHERE scan_id = ?",
                (str(output_dir.resolve()), _now(), scan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Scan not found")

    def mark_scan_terminal(
        self,
        scan_id: str,
        status: str,
        *,
        failure_code: str | None = None,
        failure_summary: str | None = None,
    ) -> bool:
        if status not in TERMINAL_SCAN_STATUSES:
            raise ValueError("Unsupported terminal scan status")
        now = _now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE scans SET status = ?, failure_code = ?, failure_summary = ?, "
                "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                "WHERE scan_id = ? AND status IN ('running', 'reducing', 'cancelling')",
                (status, failure_code, failure_summary, now, now, scan_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    "DELETE FROM worker_capacity_leases WHERE work_unit_id IN ("
                    "SELECT work_unit_id FROM work_units WHERE scan_id = ?"
                    ")",
                    (scan_id,),
                )
                return True
            existing = connection.execute(
                "SELECT status FROM scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            if existing is None:
                raise ValueError("Scan not found")
            return False

    def recover_interrupted_scans(
        self,
        *,
        active_owner_tokens: set[str] | None = None,
    ) -> list[str]:
        now = _now()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT scan_id, task_owner_pid, task_owner_token, task_owner_identity FROM scans "
                "WHERE status IN ('running', 'reducing', 'cancelling')"
            ).fetchall()
            scan_ids = [
                str(row["scan_id"])
                for row in rows
                if not _scan_owner_is_running(
                    row["task_owner_pid"],
                    row["task_owner_token"],
                    row["task_owner_identity"],
                    active_owner_tokens=active_owner_tokens,
                )
            ]
            if scan_ids:
                placeholders = ", ".join("?" for _ in scan_ids)
                connection.execute(
                    "UPDATE scans SET status = 'interrupted', failure_code = 'scan_interrupted', "
                    "failure_summary = 'The audit process stopped before completion.', "
                    "finished_at = ?, updated_at = ? "
                    f"WHERE scan_id IN ({placeholders}) "
                    "AND status IN ('running', 'reducing', 'cancelling')",
                    (now, now, *scan_ids),
                )
                connection.execute(
                    "DELETE FROM worker_capacity_leases WHERE work_unit_id IN ("
                    "SELECT work_unit_id FROM work_units "
                    f"WHERE scan_id IN ({placeholders})"
                    ")",
                    tuple(scan_ids),
                )
        return scan_ids

    def start_phase_run(
        self,
        scan_id: str,
        phase: str,
        *,
        summary: dict[str, Any] | None = None,
        ordinal: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._lock, self._connect() as connection:
            self._require_scan_status(
                connection,
                scan_id,
                {"running", "reducing"},
            )
            if ordinal is None:
                ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM scan_phase_runs WHERE scan_id = ? AND phase = ?",
                        (scan_id, phase),
                    ).fetchone()[0]
                )
            existing = connection.execute(
                "SELECT * FROM scan_phase_runs WHERE scan_id = ? AND phase = ? AND ordinal = ?",
                (scan_id, phase, ordinal),
            ).fetchone()
            if existing is not None:
                return self._decode_phase_run(existing)
            phase_run_id = f"phase_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO scan_phase_runs VALUES (?, ?, ?, ?, 'running', ?, NULL, NULL, ?, ?, ?)",
                (
                    phase_run_id,
                    scan_id,
                    phase,
                    ordinal,
                    now,
                    json.dumps(summary or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE scans SET current_phase = ?, updated_at = ? WHERE scan_id = ?",
                (phase, now, scan_id),
            )
            row = connection.execute(
                "SELECT * FROM scan_phase_runs WHERE phase_run_id = ?",
                (phase_run_id,),
            ).fetchone()
        return self._decode_phase_run(row)

    def finish_phase_run(
        self,
        phase_run_id: str,
        status: str,
        *,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "partial", "failed", "cancelled", "skipped"}:
            raise ValueError("Unsupported phase status")
        now = _now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scan_phase_runs WHERE phase_run_id = ?",
                (phase_run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Phase run not found")
            started_at = row["started_at"] or row["created_at"]
            duration_ms = max(
                0,
                int((datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds() * 1000),
            )
            merged_summary = json.loads(row["summary_json"])
            if summary:
                merged_summary.update(summary)
            connection.execute(
                "UPDATE scan_phase_runs SET status = ?, finished_at = ?, duration_ms = ?, "
                "summary_json = ?, updated_at = ? WHERE phase_run_id = ?",
                (
                    status,
                    now,
                    duration_ms,
                    json.dumps(merged_summary, ensure_ascii=False, sort_keys=True),
                    now,
                    phase_run_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM scan_phase_runs WHERE phase_run_id = ?",
                (phase_run_id,),
            ).fetchone()
        return self._decode_phase_run(updated)

    def list_phase_runs(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_phase_runs WHERE scan_id = ? ORDER BY created_at, phase, ordinal",
                (scan_id,),
            ).fetchall()
        return [self._decode_phase_run(row) for row in rows]

    def record_worker_failure(self, unit: dict[str, Any], task: Any, failure_class: str) -> None:
        """Keep a compact terminal diagnostic after sessions and attempts are pruned."""
        with self._connect() as connection:
            rejections = connection.execute(
                "SELECT tool_name, error_code, violations_json FROM submission_rejections "
                "WHERE attempt_id = ? ORDER BY created_at DESC, rejection_id DESC LIMIT 3",
                (unit.get("attempt_id"),),
            ).fetchall()
        metadata = getattr(task, "execution_metadata", {}) or {}
        self.append_scan_event(
            unit["scan_id"], "worker.execution_failed", "Worker did not submit required facts",
            {
                "work_unit_id": unit["work_unit_id"],
                "attempt_id": unit.get("attempt_id"),
                "candidate_id": unit.get("subject_id"),
                "role": unit["role"],
                "failure_class": failure_class,
                "task_status": task.status,
                "steps": metadata.get("steps"),
                "trace_step": metadata.get("trace_step"),
                "max_steps_reached": metadata.get("max_steps_reached"),
                "error": str(getattr(task, "error", None) or metadata.get("stop_reason") or "")[:1000],
                "last_message": str(getattr(task, "output", None) or "")[-2000:],
                "submission_rejections": [
                    {"tool_name": row["tool_name"], "error_code": row["error_code"],
                     "summary": row["violations_json"][:1000]} for row in rejections
                ],
            }, level="warning",
        )

    def append_scan_event(
        self,
        scan_id: str,
        event_type: str,
        title: str,
        payload: dict[str, Any],
        *,
        level: str = "info",
        phase_run_id: str | None = None,
    ) -> dict[str, Any]:
        if level not in {"info", "warning", "error"}:
            raise ValueError("Unsupported event level")
        title = title.strip()
        if not title or len(title) > 256:
            raise ValueError("Event title must contain 1 to 256 characters")
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(payload_json.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("Event payload exceeds 64 KiB")
        created_at = _now()
        with self._lock, self._connect() as connection:
            self._require_scan_status(
                connection,
                scan_id,
                {"running", "reducing", *TERMINAL_SCAN_STATUSES},
            )
            cursor = connection.execute(
                "INSERT INTO scan_events (scan_id, phase_run_id, event_type, level, title, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    phase_run_id,
                    event_type,
                    level,
                    title,
                    payload_json,
                    created_at,
                ),
            )
            seq = int(cursor.lastrowid)
        return {
            "seq": seq,
            "scan_id": scan_id,
            "phase_run_id": phase_run_id,
            "type": event_type,
            "level": level,
            "title": title,
            "summary": payload,
            "created_at": created_at,
        }

    def list_scan_events(
        self,
        scan_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        if after_seq < 0:
            raise ValueError("after_seq must not be negative")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_events WHERE scan_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (scan_id, after_seq, limit + 1),
            ).fetchall()
            latest = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM scan_events WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()[0]
            )
        has_more = len(rows) > limit
        items = [self._decode_scan_event(row) for row in rows[:limit]]
        return {"items": items, "latest_seq": latest, "has_more": has_more}

    def list_recent_scan_events(
        self,
        scan_id: str,
        *,
        limit: int = 200,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_events WHERE scan_id = ? ORDER BY seq DESC LIMIT ?",
                (scan_id, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        items = [self._decode_scan_event(row) for row in reversed(rows[:limit])]
        latest = items[-1]["seq"] if items else 0
        return {"items": items, "latest_seq": latest, "has_more": has_more}

    def list_scan_events_before(
        self,
        scan_id: str,
        *,
        before_seq: int,
        limit: int = 200,
    ) -> dict[str, Any]:
        if before_seq < 1:
            raise ValueError("before_seq must be positive")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_events WHERE scan_id = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                (scan_id, before_seq, limit + 1),
            ).fetchall()
            latest = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM scan_events WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()[0]
            )
        has_more = len(rows) > limit
        items = [self._decode_scan_event(row) for row in reversed(rows[:limit])]
        return {"items": items, "latest_seq": latest, "has_more": has_more}

    def list_scans(
        self,
        *,
        owner_subject: str | None = None,
        include_all: bool = False,
        statuses: set[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_all:
            if not owner_subject:
                raise ValueError("owner_subject is required")
            clauses.append("s.owner_subject = ?")
            parameters.append(owner_subject)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"s.status IN ({placeholders})")
            parameters.extend(sorted(statuses))
        if cursor:
            cursor_created_at, cursor_scan_id = self._decode_scan_cursor(cursor)
            clauses.append("(s.created_at < ? OR (s.created_at = ? AND s.scan_id < ?))")
            parameters.extend([cursor_created_at, cursor_created_at, cursor_scan_id])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT s.*, p.display_name, p.source_revision, p.tree_digest,
                   p.file_count, p.total_bytes, p.omitted_file_count,
                   (SELECT COUNT(*) FROM candidates c WHERE c.scan_id = s.scan_id) AS candidate_count
            FROM scans s
            JOIN snapshots p ON p.snapshot_id = s.snapshot_id
            {where}
            ORDER BY s.created_at DESC, s.scan_id DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, (*parameters, limit + 1)).fetchall()
        has_more = len(rows) > limit
        items = [self._public_scan_row(row) for row in rows[:limit]]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self._encode_scan_cursor(last["created_at"], last["scan_id"])
        return {"items": items, "next_cursor": next_cursor}

    def get_evidence_record(
        self,
        scan_id: str,
        evidence_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT e.*, c.scan_id FROM evidence e "
                "JOIN candidates c ON c.candidate_id = e.candidate_id "
                "WHERE e.evidence_id = ? AND c.scan_id = ?",
                (evidence_id, scan_id),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _decode_phase_run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json"))
        return item

    @staticmethod
    def _decode_scan_event(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["type"] = item.pop("event_type")
        item["summary"] = json.loads(item.pop("payload_json"))
        return item

    @staticmethod
    def _public_scan_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["dynamic_enabled"] = bool(item["dynamic_enabled"])
        item["poc_enabled"] = bool(item.get("poc_enabled", 0))
        item["bash_enabled"] = bool(item.get("bash_enabled", 0))
        item["web_search_enabled"] = bool(item.get("web_search_enabled", 0))
        return item

    @staticmethod
    def _encode_scan_cursor(created_at: str, scan_id: str) -> str:
        payload = json.dumps([created_at, scan_id], separators=(",", ":")).encode("utf-8")
        return urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_scan_cursor(cursor: str) -> tuple[str, str]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(urlsafe_b64decode(cursor + padding).decode("utf-8"))
        except (BinasciiError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid scan cursor") from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(value, str) and value for value in payload)
        ):
            raise ValueError("Invalid scan cursor")
        return payload[0], payload[1]

    def pending_cleanup_scan_ids(self, *, active_owner_tokens: set[str]) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scan_id, cleanup_summary_json, task_owner_pid, task_owner_token, "
                "task_owner_identity FROM scans WHERE cleanup_intermediates = 1 "
                "AND status IN ('completed', 'failed', 'cancelled', 'interrupted')"
            ).fetchall()
        pending = []
        for row in rows:
            if _scan_owner_is_running(
                row["task_owner_pid"], row["task_owner_token"], row["task_owner_identity"],
                active_owner_tokens=active_owner_tokens,
            ):
                continue
            try:
                summary = json.loads(row["cleanup_summary_json"])
            except (TypeError, ValueError):
                summary = {}
            if not isinstance(summary, dict) or summary.get("status") != "completed":
                pending.append(row["scan_id"])
        return pending

    def cleanup_session_ids(self, scan_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id FROM work_attempts WHERE work_unit_id IN "
                "(SELECT work_unit_id FROM work_units WHERE scan_id = ?) UNION "
                "SELECT session_id FROM work_units WHERE scan_id = ? AND session_id IS NOT NULL",
                (scan_id, scan_id),
            ).fetchall()
        return [row[0] for row in rows if row[0] and not row[0].startswith("attempt_")]

    def record_cleanup_failure(self, scan_id: str, reason: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE scans SET cleanup_summary_json = ? WHERE scan_id = ?",
                (json.dumps({"status": "failed", "reason": reason[:1000]}), scan_id),
            )

    def prune_scan_execution_history(
        self, scan_id: str, *, deleted_sessions: int = 0, deleted_trees: int = 0,
    ) -> dict[str, Any]:
        """Prune transient facts, retaining final semantic artifacts and FK anchors."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            scan = connection.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
            if scan is None or not scan["cleanup_intermediates"]:
                return {"status": "disabled"}
            if scan["status"] not in TERMINAL_SCAN_STATUSES:
                raise ValueError("Only terminal scans can clean execution history")
            prior = json.loads(scan["cleanup_summary_json"])
            if prior.get("status") == "completed":
                return prior
            if connection.execute(
                "SELECT 1 FROM cybergym_runs WHERE scan_id = ? AND status = 'running'", (scan_id,),
            ).fetchone():
                raise ValueError("Cannot clean an active CyberGym run")
            counts = {}
            unit_scope = "SELECT work_unit_id FROM work_units WHERE scan_id = ?"
            attempt_scope = "SELECT attempt_id FROM work_attempts WHERE work_unit_id IN (" + unit_scope + ")"
            # Canonical coverage is a final intermediate artifact, not a retry log.
            cursor = connection.execute(
                "DELETE FROM coverage_attestations WHERE scan_id = ? AND attestation_id NOT IN ("
                "SELECT ca.attestation_id FROM coverage_attestations ca WHERE ca.scan_id = ? "
                "AND ca.attestation_id = (SELECT nested.attestation_id FROM coverage_attestations nested "
                "WHERE nested.work_unit_id = ca.work_unit_id ORDER BY nested.created_at DESC, "
                "nested.attestation_id DESC LIMIT 1))", (scan_id, scan_id),
            )
            counts["coverage_history"] = cursor.rowcount
            for table, scope in (
                ("source_access", "scan_id = ?"),
                ("submission_rejections", f"attempt_id IN ({attempt_scope})"),
                ("manifest_access", f"work_unit_id IN ({unit_scope})"),
                ("knowledge_base_access", "scan_id = ?"),
                ("threat_model_access", "scan_id = ?"),
                ("verification_subject_access", f"attempt_id IN ({attempt_scope})"),
                ("session_bindings", "scan_id = ? AND (work_unit_id IS NOT NULL OR attempt_id IS NOT NULL)"),
                ("worker_capacity_leases", f"work_unit_id IN ({unit_scope})"),
                ("worker_batches", "scan_id = ?"),
                ("cybergym_runs", "scan_id = ?"),
                ("cybergym_budget", "scan_id = ?"),
                ("scan_events", "scan_id = ?"),
                ("scan_phase_runs", "scan_id = ?"),
            ):
                if self.retain_ui_history and table in {"scan_events", "scan_phase_runs", "cybergym_runs"}:
                    continue
                if table == "scan_events":
                    scope += " AND event_type NOT IN ('worker.execution_failed', 'source.archive_exclusions')"
                counts[table] = connection.execute(f"DELETE FROM {table} WHERE {scope}", (scan_id,)).rowcount
            counts["work_attempts"] = connection.execute(
                f"DELETE FROM work_attempts WHERE work_unit_id IN ({unit_scope}) "
                "AND attempt_id NOT IN (SELECT attempt_id FROM coverage_attestations)", (scan_id,),
            ).rowcount
            # Retain only the provenance anchors required by final coverage.
            connection.execute(
                f"UPDATE work_attempts SET session_id = attempt_id, background_task_id = NULL, "
                f"failure_class = NULL, resume_count = 0 WHERE work_unit_id IN ({unit_scope})", (scan_id,),
            )
            connection.execute(
                "UPDATE work_units SET session_id = NULL, background_task_id = NULL, paths_json = '[]' "
                "WHERE scan_id = ?", (scan_id,),
            )
            # Selected bytes and evidence-referenced inputs need their ancestors
            # for provenance. Everything else is a disposable search candidate.
            before = connection.execute("SELECT COUNT(*) FROM cybergym_artifacts WHERE scan_id = ?", (scan_id,)).fetchone()[0]
            connection.execute(
                "WITH RECURSIVE retained(id) AS ("
                "SELECT final_artifact_id FROM cybergym_tasks WHERE scan_id = ? "
                "UNION SELECT artifact_id FROM cybergym_submissions WHERE scan_id = ? "
                "UNION SELECT artifact_id FROM poc_validations WHERE scan_id = ? "
                "UNION SELECT a.parent_id FROM cybergym_artifacts a JOIN retained r ON a.artifact_id = r.id"
                ") DELETE FROM cybergym_artifacts WHERE scan_id = ? "
                "AND artifact_id NOT IN (SELECT id FROM retained WHERE id IS NOT NULL)",
                (scan_id, scan_id, scan_id, scan_id),
            )
            after = connection.execute("SELECT COUNT(*) FROM cybergym_artifacts WHERE scan_id = ?", (scan_id,)).fetchone()[0]
            counts["cybergym_artifacts"] = before - after
            summary = {"status": "completed", "completed_at": _now(), "deleted_rows": counts,
                       "deleted_sessions": deleted_sessions, "deleted_trees": deleted_trees}
            connection.execute("UPDATE scans SET cleanup_summary_json = ? WHERE scan_id = ?",
                               (json.dumps(summary, sort_keys=True), scan_id))
            connection.execute(
                "INSERT INTO scan_events (scan_id, event_type, level, title, payload_json, created_at) "
                "VALUES (?, 'scan.cleanup_completed', 'info', '审计临时执行数据已清理', ?, ?)",
                (scan_id, json.dumps(summary), summary["completed_at"]),
            )
        return summary

    def delete_scan(self, scan_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM scans WHERE scan_id = ?", (scan_id,))

    def delete_terminal_scan(self, scan_id: str) -> dict[str, str | None]:
        """Delete a terminal scan and its unreferenced snapshot in one transaction."""
        with self._lock, self._connect() as connection:
            scan = connection.execute(
                "SELECT status, snapshot_id, output_dir FROM scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            if scan is None:
                raise ValueError("Scan not found")
            if scan["status"] not in TERMINAL_SCAN_STATUSES:
                raise ValueError("Only terminal scans may be deleted")

            snapshot = connection.execute(
                "SELECT root_path, copy_source FROM snapshots WHERE snapshot_id = ?",
                (scan["snapshot_id"],),
            ).fetchone()
            connection.execute("DELETE FROM scans WHERE scan_id = ?", (scan_id,))
            references = connection.execute(
                "SELECT COUNT(*) FROM scans WHERE snapshot_id = ?",
                (scan["snapshot_id"],),
            ).fetchone()[0]
            snapshot_root = None
            if not references:
                connection.execute(
                    "DELETE FROM snapshots WHERE snapshot_id = ?",
                    (scan["snapshot_id"],),
                )
                snapshot_root = (
                    snapshot["root_path"]
                    if snapshot is not None and bool(snapshot["copy_source"])
                    else None
                )

            return {
                "snapshot_id": scan["snapshot_id"],
                "snapshot_root": snapshot_root,
                "output_dir": scan["output_dir"],
            }

    def bind_session(
        self,
        *,
        session_id: str,
        scan_id: str,
        snapshot_id: str,
        role: str,
        work_unit_id: str | None = None,
    ) -> None:
        allowed_roles = {
            "coordinator",
            "threat_modeler",
            "baseline",
            "investigator",
            "verifier",
            "prober",
            "cybergym_solver",
            "poc_generator",
        }
        if role not in allowed_roles:
            raise ValueError("Unsupported session binding role")
        if role == "coordinator" and work_unit_id is not None:
            raise ValueError("Coordinator bindings cannot reference a work unit")
        if role != "coordinator" and work_unit_id is None:
            raise ValueError("Worker bindings require a work unit")
        if role != "coordinator":
            work_unit = self.get_work_unit(work_unit_id)
            scan = self.get_scan(scan_id)
            if work_unit is None:
                raise ValueError("Work unit not found")
            if scan is not None and scan["snapshot_id"] != snapshot_id:
                raise ValueError("Binding snapshot does not belong to the scan")
            if (
                scan is None
                or work_unit["scan_id"] != scan_id
                or work_unit["role"] != role
            ):
                raise ValueError("Work unit does not match the binding")
            self.create_work_attempt(
                work_unit_id=work_unit_id,
                session_id=session_id,
                agent_name=WORKER_ROLE_AGENTS[role],
            )
            return
        with self._lock, self._connect() as connection:
            scan = connection.execute(
                "SELECT snapshot_id, status FROM scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            if scan is None:
                raise ValueError("Scan not found")
            if scan["snapshot_id"] != snapshot_id:
                raise ValueError("Binding snapshot does not belong to the scan")
            if scan["status"] != "running":
                raise ValueError("Only running scans may create session bindings")
            existing = connection.execute(
                "SELECT scan_id, work_unit_id, attempt_id, snapshot_id, role "
                "FROM session_bindings WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            expected = (scan_id, work_unit_id, None, snapshot_id, role)
            if existing is not None and tuple(existing) != expected:
                raise ValueError("Session is already bound to another audit context")
            connection.execute(
                """
                INSERT INTO session_bindings (
                    session_id, scan_id, work_unit_id, attempt_id,
                    snapshot_id, role, created_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    scan_id = excluded.scan_id,
                    work_unit_id = excluded.work_unit_id,
                    attempt_id = excluded.attempt_id,
                    snapshot_id = excluded.snapshot_id,
                    role = excluded.role,
                    created_at = excluded.created_at
                """,
                (session_id, scan_id, work_unit_id, snapshot_id, role, _now()),
            )

    def resolve_binding(self, session_id: str) -> SessionBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, scan_id, work_unit_id, snapshot_id, role,
                       attempt_id
                FROM session_bindings WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return SessionBinding(**dict(row)) if row else None

    def require_binding(self, session_id: str, roles: set[str]) -> SessionBinding:
        binding = self.resolve_binding(session_id)
        if binding is None:
            raise ValueError("This session is not bound to a code-security scan")
        if binding.role not in roles:
            raise ValueError(f"Session role {binding.role!r} cannot perform this operation")
        return binding

    def verify_execution_capsule(
        self,
        binding: SessionBinding,
        *,
        agent_name: str,
        provider_id: str | None,
        model_id: str | None,
        toolset_digest_value: str,
    ) -> dict[str, Any]:
        if binding.work_unit_id is None or binding.attempt_id is None:
            raise ExecutionCapsuleError("worker binding has no work attempt")
        mismatches: list[dict[str, Any]] = []
        capsule: ExecutionCapsule | None = None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT wa.*, wu.scan_id AS unit_scan_id, wu.phase, wu.role,
                       wu.paths_json, wu.assignment_digest,
                       s.snapshot_id AS scan_snapshot_id, s.ruleset_digest,
                       COALESCE(rm.manifest_digest, sn.tree_digest)
                           AS manifest_digest
                FROM work_attempts wa
                JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id
                JOIN scans s ON s.scan_id = wu.scan_id
                JOIN snapshots sn ON sn.snapshot_id = s.snapshot_id
                LEFT JOIN repository_manifests rm
                    ON rm.snapshot_id = s.snapshot_id
                WHERE wa.session_id = ?
                """,
                (binding.session_id,),
            ).fetchone()
            if row is None:
                raise ExecutionCapsuleError("session has no persisted work attempt")
            if row["status"] not in {"running"}:
                raise ExecutionCapsuleError(
                    "work attempt is not active",
                    code="ATTEMPT_NOT_ACTIVE",
                )
            capsule = self._execution_capsule(connection, row["attempt_id"])

            def compare(field: str, expected: Any, actual: Any) -> None:
                if expected != actual:
                    mismatches.append(
                        {"field": field, "expected": expected, "actual": actual}
                    )

            compare("attempt_id", row["attempt_id"], binding.attempt_id)
            compare("work_unit_id", row["work_unit_id"], binding.work_unit_id)
            compare("scan_id", row["unit_scan_id"], binding.scan_id)
            compare("snapshot_id", row["scan_snapshot_id"], binding.snapshot_id)
            compare("role", row["role"], binding.role)
            compare("agent_name", row["agent_name"], agent_name)
            compare(
                "role_agent",
                WORKER_ROLE_AGENTS.get(row["role"]),
                row["agent_name"],
            )
            compare("provider_id", row["provider_id"], provider_id)
            compare("model_id", row["model_id"], model_id)
            compare(
                "toolset_digest",
                row["toolset_digest"],
                toolset_digest_value,
            )
            expected_scope_digest = scope_digest(
                snapshot_id=row["scan_snapshot_id"],
                manifest_digest=row["manifest_digest"],
                paths=json.loads(row["paths_json"]),
                assignment_digest=row["assignment_digest"],
            )
            compare("scope_digest", row["scope_digest"], expected_scope_digest)
            compare("capsule_digest", row["capsule_digest"], capsule.digest())
            if mismatches:
                now = _now()
                connection.execute(
                    "UPDATE work_attempts SET status = 'failed', "
                    "failure_class = 'identity_capsule_mismatch', "
                    "finished_at = ?, updated_at = ? WHERE attempt_id = ? "
                    "AND status IN ('pending', 'running', 'recovering')",
                    (now, now, row["attempt_id"]),
                )
                connection.execute(
                    "UPDATE work_units SET status = 'failed', finished_at = ?, "
                    "updated_at = ? WHERE work_unit_id = ? "
                    "AND status IN ('pending', 'running')",
                    (now, now, row["work_unit_id"]),
                )
                connection.execute(
                    "INSERT INTO scan_events ("
                    "scan_id, phase_run_id, event_type, level, title, "
                    "payload_json, created_at"
                    ") VALUES (?, NULL, 'identity.mismatch', 'error', ?, ?, ?)",
                    (
                        row["unit_scan_id"],
                        "Execution capsule mismatch",
                        json.dumps(
                            {
                                "attempt_id": row["attempt_id"],
                                "work_unit_id": row["work_unit_id"],
                                "mismatches": mismatches,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
        if mismatches:
            snapshot_mismatch = any(
                item["field"] == "snapshot_id" for item in mismatches
            )
            raise ExecutionCapsuleError(
                "persisted execution identity does not match runtime context",
                code=("SNAPSHOT_MISMATCH" if snapshot_mismatch else "IDENTITY_CAPSULE_MISMATCH"),
            )
        if capsule is None:
            raise ExecutionCapsuleError("execution capsule is unavailable")
        return capsule.public_dict()

    def get_knowledge_base_metadata(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT display_name, content_sha256, byte_length FROM scan_knowledge_base WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "display_name": row["display_name"],
            "sha256": row["content_sha256"],
            "byte_length": row["byte_length"],
            "trust": "untrusted_external_hypothesis",
        }

    def read_knowledge_base(self, binding: SessionBinding) -> dict[str, Any] | None:
        allowed_roles = {
            "coordinator",
            "threat_modeler",
            "baseline",
            "investigator",
            "poc_generator",
        }
        if binding.role not in allowed_roles:
            raise ValueError("This session role cannot read the audit knowledge base")
        with self._lock, self._connect() as connection:
            persisted = connection.execute(
                "SELECT scan_id, work_unit_id, snapshot_id, role FROM session_bindings WHERE session_id = ?",
                (binding.session_id,),
            ).fetchone()
            if persisted is None or tuple(persisted) != (
                binding.scan_id,
                binding.work_unit_id,
                binding.snapshot_id,
                binding.role,
            ):
                raise ValueError("Session binding changed before knowledge-base access")
            row = connection.execute(
                "SELECT display_name, content, content_sha256, byte_length FROM scan_knowledge_base WHERE scan_id = ?",
                (binding.scan_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "INSERT INTO knowledge_base_access VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scan_id, session_id) DO NOTHING",
                (
                    binding.scan_id,
                    binding.session_id,
                    binding.work_unit_id,
                    binding.role,
                    row["content_sha256"],
                    _now(),
                ),
            )
        return {
            "display_name": row["display_name"],
            "content": row["content"],
            "sha256": row["content_sha256"],
            "byte_length": row["byte_length"],
        }

    def require_knowledge_base_consumed(self, binding: SessionBinding) -> None:
        with self._connect() as connection:
            knowledge_base = connection.execute(
                "SELECT content_sha256 FROM scan_knowledge_base WHERE scan_id = ?",
                (binding.scan_id,),
            ).fetchone()
            if knowledge_base is None:
                return
            access = connection.execute(
                "SELECT 1 FROM knowledge_base_access "
                "WHERE scan_id = ? AND session_id = ? AND role = ? AND content_sha256 = ?",
                (
                    binding.scan_id,
                    binding.session_id,
                    binding.role,
                    knowledge_base["content_sha256"],
                ),
            ).fetchone()
        if access is None:
            raise ValueError("This guided audit requires audit_knowledge_base before submitting this phase")

    def record_manifest_access(
        self,
        binding: SessionBinding,
        manifest: RepositoryManifest,
    ) -> None:
        if (
            binding.role not in {"threat_modeler", "poc_generator"}
            or binding.work_unit_id is None
            or binding.attempt_id is None
        ):
            raise ValueError("Repository summary access requires a threat-modeler or PoC-generator work unit")
        if manifest.snapshot_id != binding.snapshot_id:
            raise ValueError("Repository manifest does not match the bound snapshot")
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            connection.execute(
                "INSERT INTO manifest_access ("
                "manifest_id, attempt_id, session_id, work_unit_id, "
                "manifest_digest, accessed_at"
                ") VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    manifest.manifest_id,
                    binding.attempt_id,
                    binding.session_id,
                    binding.work_unit_id,
                    manifest.manifest_digest,
                    _now(),
                ),
            )

    def require_repository_summary_consumed(self, binding: SessionBinding) -> None:
        if (
            binding.role not in {"threat_modeler", "poc_generator"}
            or binding.work_unit_id is None
            or binding.attempt_id is None
        ):
            raise ValueError("Repository summary consumption requires a threat-modeler or PoC-generator work unit")
        with self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            manifest = connection.execute(
                "SELECT manifest_id, manifest_digest FROM repository_manifests WHERE snapshot_id = ?",
                (binding.snapshot_id,),
            ).fetchone()
            access = None
            if manifest is not None:
                access = connection.execute(
                    "SELECT 1 FROM manifest_access "
                    "WHERE manifest_id = ? AND attempt_id = ? "
                    "AND session_id = ? AND work_unit_id = ? "
                    "AND manifest_digest = ?",
                    (
                        manifest["manifest_id"],
                        binding.attempt_id,
                        binding.session_id,
                        binding.work_unit_id,
                        manifest["manifest_digest"],
                    ),
                ).fetchone()
            if manifest is None:
                snapshot = connection.execute(
                    "SELECT tree_digest FROM snapshots WHERE snapshot_id = ?",
                    (binding.snapshot_id,),
                ).fetchone()
                access = connection.execute(
                    "SELECT 1 FROM source_access "
                    "WHERE work_unit_id = ? AND session_id = ? "
                    "AND operation = 'repository_summary' AND relative_path = '.' "
                    "AND blob_digest = ?",
                    (
                        binding.work_unit_id,
                        binding.session_id,
                        snapshot["tree_digest"] if snapshot is not None else None,
                    ),
                ).fetchone()
        if access is None:
            raise ValueError(
                "Canonical repository summary has not been consumed; call audit_repository_summary before submitting"
            )

    def save_threat_model(
        self,
        binding: SessionBinding,
        payload: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> bool:
        if binding.role != "threat_modeler" or binding.work_unit_id is None:
            raise ValueError("Threat models require a threat-modeler work unit")
        self.validate_threat_model_contract(payload, evidence)
        self.require_repository_summary_consumed(binding)
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            existing = connection.execute(
                "SELECT work_unit_id FROM threat_models WHERE scan_id = ?",
                (binding.scan_id,),
            ).fetchone()
            if existing is not None:
                if existing["work_unit_id"] != binding.work_unit_id:
                    raise ValueError("Threat model has already been submitted")
                connection.execute(
                    "UPDATE threat_models SET payload_json = ?, evidence_json = ?, "
                    "created_at = ? WHERE scan_id = ? AND work_unit_id = ?",
                    (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        _now(),
                        binding.scan_id,
                        binding.work_unit_id,
                    ),
                )
                return True
            connection.execute(
                "INSERT INTO threat_models VALUES (?, ?, ?, ?, ?)",
                (
                    binding.scan_id,
                    binding.work_unit_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
        return False

    @staticmethod
    def validate_threat_model_contract(
        payload: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> None:
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Threat-model summary must be a non-empty string")
        for field in THREAT_MODEL_REQUIRED_LIST_FIELDS:
            values = payload.get(field)
            if not isinstance(values, list) or not values:
                raise ValueError(f"Threat-model field {field} must not be empty")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"Threat-model field {field} must contain non-empty strings")
        assumptions = payload.get("assumptions")
        if not isinstance(assumptions, list) or any(
            not isinstance(item, str) or not item.strip() for item in assumptions
        ):
            raise ValueError("Threat-model field assumptions must contain only non-empty strings")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Threat model requires at least one evidence reference")

    def get_threat_model(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM threat_models WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        if row is None:
            return None
        output = dict(row)
        output["threat_model"] = json.loads(output.pop("payload_json"))
        output["evidence"] = json.loads(output.pop("evidence_json"))
        return output

    def get_threat_model_for_binding(
        self,
        binding: SessionBinding,
    ) -> dict[str, Any]:
        if binding.role not in {"baseline", "investigator", "verifier"}:
            raise ValueError("Session role cannot consume the scan threat model")
        if binding.work_unit_id is None:
            raise ValueError("Threat-model access requires a worker work unit")
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            row = self._require_threat_model_ready(connection, binding.scan_id)
            threat_model = json.loads(row["payload_json"])
            evidence = json.loads(row["evidence_json"])
            work_unit = connection.execute(
                "SELECT phase FROM work_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            rescan = (
                self._require_targeted_rescan_directive(
                    connection,
                    binding.scan_id,
                )
                if work_unit is not None and work_unit["phase"] == "targeted_rescan"
                else None
            )
            connection.execute(
                "INSERT INTO threat_model_access VALUES (?, ?, ?) "
                "ON CONFLICT(scan_id, work_unit_id) DO UPDATE SET "
                "accessed_at = excluded.accessed_at",
                (binding.scan_id, binding.work_unit_id, _now()),
            )
        output = {
            "scan_id": binding.scan_id,
            "threat_model": threat_model,
            "evidence": evidence,
        }
        if rescan is not None:
            output["targeted_rescan"] = rescan
        return output

    def require_threat_model_ready(self, scan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._require_threat_model_ready(connection, scan_id)
        return {
            "scan_id": row["scan_id"],
            "work_unit_id": row["work_unit_id"],
            "threat_model": json.loads(row["payload_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "created_at": row["created_at"],
        }

    def require_threat_model_consumed(self, binding: SessionBinding) -> None:
        if binding.role not in {"baseline", "investigator"}:
            raise ValueError("Session role does not consume threat-model context")
        with self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            self._require_threat_model_access(connection, binding)

    @staticmethod
    def _require_threat_model_ready(
        connection: sqlite3.Connection,
        scan_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT tm.* FROM threat_models tm "
            "JOIN work_units wu ON wu.work_unit_id = tm.work_unit_id "
            "WHERE tm.scan_id = ? AND wu.scan_id = tm.scan_id "
            "AND wu.role = 'threat_modeler' AND wu.status = 'completed'",
            (scan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("A completed source-backed threat model is required")
        try:
            ScanStore.validate_threat_model_contract(
                json.loads(row["payload_json"]),
                json.loads(row["evidence_json"]),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"Completed threat model failed contract validation: {exc}") from exc
        return row

    @staticmethod
    def _require_threat_model_access(
        connection: sqlite3.Connection,
        binding: SessionBinding,
    ) -> None:
        if binding.work_unit_id is None:
            raise ValueError("Threat-model access requires a worker work unit")
        accessed = connection.execute(
            "SELECT 1 FROM threat_model_access WHERE scan_id = ? AND work_unit_id = ?",
            (binding.scan_id, binding.work_unit_id),
        ).fetchone()
        if accessed is None:
            raise ValueError("Baseline workers must read the stored threat-model context first")

    @staticmethod
    def _require_scan_status(
        connection: sqlite3.Connection,
        scan_id: str,
        allowed_statuses: set[str],
    ) -> sqlite3.Row:
        scan = connection.execute(
            "SELECT * FROM scans WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise ValueError("Scan not found")
        if scan["status"] not in allowed_statuses:
            raise ValueError(f"Scan status {scan['status']!r} does not allow this operation")
        return scan

    @staticmethod
    def _require_active_worker_binding(
        connection: sqlite3.Connection,
        binding: SessionBinding,
    ) -> None:
        if binding.work_unit_id is None:
            raise ValueError("Worker operation requires a bound work unit")
        active = connection.execute(
            """
            SELECT wu.scan_id, wu.role, wu.status AS work_unit_status,
                   wa.session_id, wa.status AS attempt_status
            FROM work_units wu
            JOIN work_attempts wa ON wa.work_unit_id = wu.work_unit_id
            WHERE wu.work_unit_id = ? AND wa.attempt_id = ?
            """,
            (binding.work_unit_id, binding.attempt_id),
        ).fetchone()
        if (
            active is None
            or active["scan_id"] != binding.scan_id
            or active["role"] != binding.role
            or active["session_id"] != binding.session_id
            or active["work_unit_status"] != "running"
            or active["attempt_status"] != "running"
        ):
            raise ValueError("Worker binding is not active")

    def save_candidate(
        self,
        binding: SessionBinding,
        payload: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> str:
        candidate_id = f"cand_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            if binding.role in {"baseline", "investigator"}:
                self._require_threat_model_access(connection, binding)
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate_id,
                    binding.scan_id,
                    binding.work_unit_id,
                    binding.role,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            connection.executemany(
                "INSERT INTO evidence ("
                "evidence_id, candidate_id, relative_path, blob_digest, "
                "start_line, end_line, excerpt_hash, ordinal"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"evidence_{uuid.uuid4().hex}",
                        candidate_id,
                        item["relative_path"],
                        item["blob_digest"],
                        item["start_line"],
                        item["end_line"],
                        item["excerpt_hash"],
                        ordinal,
                    )
                    for ordinal, item in enumerate(evidence)
                ],
            )
        return candidate_id

    def record_source_access(
        self,
        binding: SessionBinding,
        *,
        operation: str,
        relative_path: str,
        blob_digest: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> None:
        self.record_source_accesses(
            binding,
            [
                {
                    "operation": operation,
                    "relative_path": relative_path,
                    "blob_digest": blob_digest,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            ],
        )

    def record_source_accesses(
        self,
        binding: SessionBinding,
        accesses: list[dict[str, Any]],
    ) -> None:
        if not accesses:
            return
        if binding.attempt_id is None:
            raise ValueError("Source access requires a bound work attempt")
        operations = {str(item.get("operation") or "") for item in accesses}
        if not operations <= {"repository_summary", "inventory", "read", "search"}:
            raise ValueError("Unsupported source access operation")
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            now = _now()
            connection.executemany(
                "INSERT INTO source_access ("
                "access_id, attempt_id, session_id, scan_id, work_unit_id, "
                "operation, relative_path, blob_digest, start_line, end_line, "
                "created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"access_{uuid.uuid4().hex}",
                        binding.attempt_id,
                        binding.session_id,
                        binding.scan_id,
                        binding.work_unit_id,
                        item["operation"],
                        item["relative_path"],
                        item.get("blob_digest"),
                        item.get("start_line"),
                        item.get("end_line"),
                        now,
                    )
                    for item in accesses
                ],
            )

    def list_source_accesses(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT operation, relative_path, blob_digest, start_line, "
                "end_line FROM source_access WHERE attempt_id = ? "
                "ORDER BY created_at, access_id",
                (attempt_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_submission_rejection(
        self,
        binding: SessionBinding,
        *,
        tool_name: str,
        error_code: str,
        violations: list[dict[str, Any]],
        retryable: bool,
    ) -> dict[str, Any]:
        if binding.attempt_id is None or binding.work_unit_id is None:
            raise ValueError("Submission rejection requires a bound work attempt")
        rejection_id = f"rejection_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            connection.execute(
                "INSERT INTO submission_rejections VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rejection_id,
                    binding.attempt_id,
                    tool_name,
                    error_code,
                    json.dumps(violations, ensure_ascii=False, sort_keys=True),
                    int(retryable),
                    now,
                ),
            )
            event_payload = {
                "rejection_id": rejection_id,
                "attempt_id": binding.attempt_id,
                "work_unit_id": binding.work_unit_id,
                "tool": tool_name,
                "error_code": error_code,
                "retryable": retryable,
                "violation_count": len(violations),
            }
            connection.execute(
                "INSERT INTO scan_events ("
                "scan_id, phase_run_id, event_type, level, title, "
                "payload_json, created_at"
                ") VALUES (?, NULL, 'submission.rejected', 'warning', ?, ?, ?)",
                (
                    binding.scan_id,
                    "提交校验被拒绝",
                    json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return {
            **event_payload,
            "violations": violations,
            "created_at": now,
        }

    def require_verifier_source_access(
        self,
        connection: sqlite3.Connection,
        binding: SessionBinding,
        candidate_id: str,
    ) -> None:
        evidence = connection.execute(
            "SELECT relative_path, blob_digest, start_line, end_line FROM evidence WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchall()
        if not evidence:
            raise ValueError("Candidate has no evidence to verify")
        reads = connection.execute(
            "SELECT relative_path, blob_digest, start_line, end_line "
            "FROM source_access WHERE attempt_id = ? "
            "AND operation = 'read'",
            (binding.attempt_id,),
        ).fetchall()
        for item in evidence:
            independently_read = any(
                read["relative_path"] == item["relative_path"]
                and read["blob_digest"] == item["blob_digest"]
                and read["start_line"] is not None
                and read["end_line"] is not None
                and read["start_line"] <= item["start_line"]
                and read["end_line"] >= item["end_line"]
                for read in reads
            )
            if not independently_read:
                raise ValueError(
                    "Verifier must independently read every candidate evidence range: "
                    f"{item['relative_path']}:{item['start_line']}-{item['end_line']}"
                )

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    def get_verification_subject(self, binding: SessionBinding) -> dict[str, Any]:
        if binding.role != "verifier" or binding.work_unit_id is None:
            raise ValueError("Verification subject requires a verifier work unit")
        if binding.attempt_id is None:
            raise ValueError("Verification subject requires a bound work attempt")
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            assignment = connection.execute(
                "SELECT subject_id, vote_index FROM worker_batch_units "
                "WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or not assignment["subject_id"]:
                raise ValueError("Verifier work unit has no assigned candidate")
            candidate = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ? AND scan_id = ?",
                (assignment["subject_id"], binding.scan_id),
            ).fetchone()
            if candidate is None:
                raise ValueError("Assigned verification candidate was not found")
            evidence = connection.execute(
                "SELECT relative_path, blob_digest, start_line, end_line, "
                "excerpt_hash, ordinal FROM evidence WHERE candidate_id = ? "
                "ORDER BY ordinal, rowid",
                (assignment["subject_id"],),
            ).fetchall()
            threat_model = connection.execute(
                "SELECT payload_json FROM threat_models WHERE scan_id = ?",
                (binding.scan_id,),
            ).fetchone()
            if threat_model is None:
                raise ValueError("Verification requires a trusted threat model")
            threat_payload = json.loads(threat_model["payload_json"])
            output = {
                "candidate_id": candidate["candidate_id"],
                "vote_index": assignment["vote_index"],
                "trust": "untrusted_candidate_claim",
                "claim": json.loads(candidate["payload_json"]),
                "evidence": [dict(item) for item in evidence],
                "threat_context": {
                    key: threat_payload.get(key)
                    for key in (
                        "summary",
                        "trustBoundaries",
                        "attackerCapabilities",
                        "securityObjectives",
                        "assumptions",
                    )
                },
            }
            connection.execute(
                "INSERT INTO verification_subject_access VALUES (?, ?, ?) "
                "ON CONFLICT(attempt_id) DO NOTHING",
                (binding.attempt_id, candidate["candidate_id"], _now()),
            )
        return output

    def save_verification_vote(
        self,
        binding: SessionBinding,
        *,
        candidate_id: str,
        verdict: str,
        rationale: str,
        counter_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        vote_id = f"vote_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            candidate = connection.execute(
                "SELECT scan_id FROM candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if candidate is None or candidate["scan_id"] != binding.scan_id:
                raise ValueError("Candidate does not belong to this scan")
            assignment = connection.execute(
                "SELECT subject_id, vote_index FROM worker_batch_units "
                "WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if assignment is None or not assignment["subject_id"]:
                raise ValueError("Verifier work unit has no assigned candidate")
            if assignment["subject_id"] != candidate_id:
                raise ValueError("Candidate is not assigned to this verifier work unit")
            required_votes = int(scan["verification_vote_count"])
            if assignment["vote_index"] is None:
                raise ValueError("Verifier work unit has no assigned vote index")
            vote_index = int(assignment["vote_index"])
            if not 1 <= vote_index <= required_votes:
                raise ValueError("Verifier vote index is outside the scan policy")
            subject_access = connection.execute(
                "SELECT 1 FROM verification_subject_access "
                "WHERE attempt_id = ? AND candidate_id = ?",
                (binding.attempt_id, candidate_id),
            ).fetchone()
            if subject_access is None:
                raise ValueError(
                    "Verifier must call audit_verification_subject before submitting a verdict"
                )
            self.require_verifier_source_access(connection, binding, candidate_id)
            connection.execute(
                "INSERT INTO verification_votes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vote_id,
                    candidate_id,
                    binding.scan_id,
                    binding.work_unit_id,
                    vote_index,
                    verdict,
                    rationale,
                    json.dumps(counter_evidence, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            votes = connection.execute(
                "SELECT verdict, counter_evidence_json FROM verification_votes "
                "WHERE candidate_id = ? ORDER BY vote_index",
                (candidate_id,),
            ).fetchall()
            verification_id: str | None = None
            consensus: str | None = None
            if len(votes) == required_votes:
                counts = Counter(row["verdict"] for row in votes)
                majority = required_votes // 2 + 1
                if counts["confirmed"] >= majority:
                    consensus = "confirmed"
                elif counts["rejected"] >= majority:
                    consensus = "rejected"
                else:
                    consensus = "insufficient_evidence"
                combined_counter_evidence = {
                    json.dumps(item, ensure_ascii=False, sort_keys=True): item
                    for row in votes
                    for item in json.loads(row["counter_evidence_json"])
                }
                verification_id = f"verify_{uuid.uuid4().hex}"
                consensus_rationale = (
                    f"Host consensus from {required_votes} independent votes: "
                    f"confirmed={counts['confirmed']}, rejected={counts['rejected']}, "
                    "insufficient_evidence="
                    f"{counts['insufficient_evidence']}."
                )
                connection.execute(
                    "INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        verification_id,
                        candidate_id,
                        binding.scan_id,
                        binding.work_unit_id,
                        consensus,
                        consensus_rationale,
                        json.dumps(
                            list(combined_counter_evidence.values()),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        _now(),
                    ),
                )
        return {
            "vote_id": vote_id,
            "vote_index": vote_index,
            "votes_required": required_votes,
            "consensus_verification_id": verification_id,
            "consensus_verdict": consensus,
        }

    def save_coverage_attestation(
        self,
        binding: SessionBinding,
        *,
        attestation: CoverageAttestation,
        records: list[CoverageRecord],
        open_questions: list[dict[str, Any]],
    ) -> None:
        if binding.work_unit_id is None or binding.attempt_id is None:
            raise ValueError("Coverage requires a bound work attempt")
        if (
            attestation.work_unit_id != binding.work_unit_id
            or attestation.attempt_id != binding.attempt_id
        ):
            raise ValueError("Coverage attestation does not match the binding")
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            if binding.role in {"baseline", "investigator"}:
                self._require_threat_model_access(connection, binding)
            work_unit = connection.execute(
                "SELECT scan_id, role FROM work_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if (
                work_unit is None
                or work_unit["scan_id"] != binding.scan_id
                or work_unit["role"] != binding.role
            ):
                raise ValueError("Coverage work unit does not match the binding")
            now = _now()
            connection.execute(
                "DELETE FROM coverage_attestations "
                "WHERE work_unit_id = ? AND attempt_id = ?",
                (binding.work_unit_id, binding.attempt_id),
            )
            connection.execute(
                "INSERT INTO coverage_attestations ("
                "attestation_id, scan_id, work_unit_id, attempt_id, policy, "
                "completeness, assigned_count, read_complete_count, "
                "failed_count, unexamined_count, attestation_digest, "
                "open_questions_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attestation.attestation_id,
                    binding.scan_id,
                    attestation.work_unit_id,
                    attestation.attempt_id,
                    attestation.policy,
                    attestation.completeness,
                    attestation.assigned_count,
                    attestation.read_complete_count,
                    attestation.failed_count,
                    attestation.unexamined_count,
                    attestation.attestation_digest,
                    json.dumps(
                        normalize_open_questions(open_questions),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO coverage_records ("
                "attestation_id, relative_path, state, reason, receipt_digest"
                ") VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        attestation.attestation_id,
                        item.relative_path,
                        item.state,
                        item.reason,
                        item.receipt_digest,
                    )
                    for item in records
                ],
            )

    def list_coverage_records(self, attestation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT relative_path, state, reason, receipt_digest "
                "FROM coverage_records WHERE attestation_id = ? "
                "ORDER BY relative_path",
                (attestation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_latest_coverage(self, scan_id: str) -> list[dict[str, Any]]:
        """Return one canonical attestation with records per analysis work unit."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ca.* FROM coverage_attestations ca "
                "WHERE ca.scan_id = ? AND ca.attestation_id = ("
                "SELECT nested.attestation_id FROM coverage_attestations nested "
                "WHERE nested.work_unit_id = ca.work_unit_id "
                "ORDER BY nested.created_at DESC, nested.attestation_id DESC LIMIT 1"
                ") ORDER BY ca.work_unit_id",
                (scan_id,),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["counts"] = {
                    "assigned": item.pop("assigned_count"),
                    "read_complete": item.pop("read_complete_count"),
                    "failed": item.pop("failed_count"),
                    "unexamined": item.pop("unexamined_count"),
                }
                item["open_questions"] = normalize_open_questions(
                    json.loads(item.pop("open_questions_json"))
                )
                record_rows = connection.execute(
                    "SELECT relative_path, state, reason, receipt_digest "
                    "FROM coverage_records WHERE attestation_id = ? "
                    "ORDER BY relative_path",
                    (item["attestation_id"],),
                ).fetchall()
                item["records"] = [dict(record) for record in record_rows]
                output.append(item)
        return output

    def analysis_coverage_summary(self, scan_id: str) -> dict[str, Any]:
        """Return deduplicated active scan coverage while preserving raw attestations."""
        scan = self.get_scan(scan_id)
        if scan is None:
            raise ValueError("Scan not found")
        attestations = self.list_latest_coverage(scan_id)
        with self._connect() as connection:
            automatic_exclusions = [
                item
                for row in connection.execute(
                    "SELECT payload_json FROM scan_events WHERE scan_id = ? "
                    "AND event_type = 'source.archive_exclusions'", (scan_id,),
                )
                for item in json.loads(row["payload_json"])["exclusions"]
                if item["reason"] == "external_symlink_auto"
            ]
            unit_rows = connection.execute(
                "SELECT work_unit_id, phase, role, paths_json FROM work_units "
                "WHERE scan_id = ? AND role IN ('baseline', 'investigator') "
                "ORDER BY created_at, work_unit_id",
                (scan_id,),
            ).fetchall()
            snapshot_rows = connection.execute(
                "SELECT relative_path FROM snapshot_files WHERE snapshot_id = ? "
                "UNION SELECT relative_path FROM snapshot_omissions WHERE snapshot_id = ? "
                "ORDER BY relative_path",
                (scan["snapshot_id"], scan["snapshot_id"]),
            ).fetchall()
        units = {row["work_unit_id"]: dict(row) for row in unit_rows}
        augmented = []
        for attestation in attestations:
            unit = units.get(attestation["work_unit_id"])
            augmented.append(
                {
                    **attestation,
                    "role": None if unit is None else unit["role"],
                    "phase": None if unit is None else unit["phase"],
                    "paths": [] if unit is None else json.loads(unit["paths_json"]),
                }
            )
        merged = merge_analysis_coverage(augmented)
        snapshot_paths = [row["relative_path"] for row in snapshot_rows]
        records_by_path = {
            item["relative_path"]: item for item in merged["records"]
        }
        covered_units = {item["work_unit_id"] for item in attestations}
        missing_attestations = sorted(set(units) - covered_units)
        active_gap_paths = [
            path
            for path in snapshot_paths
            if records_by_path.get(path, {}).get("state")
            not in {"read_complete", "not_applicable"}
        ]
        completeness = merged["completeness"]
        if missing_attestations or active_gap_paths or any(
            item.get("blocking") is True for item in merged["open_questions"]
        ):
            completeness = (
                "blocked"
                if scan["coverage_policy"] == "exhaustive"
                else "partial"
            )
        elif units and covered_units == set(units):
            completeness = "complete"
        if automatic_exclusions:
            completeness = "blocked" if scan["coverage_policy"] == "exhaustive" else "partial"
        counts = {
            "assigned": len(snapshot_paths),
            "read_complete": sum(
                item.get("state") == "read_complete" for item in records_by_path.values()
            ),
            "failed": sum(
                item.get("state") == "failed" for item in records_by_path.values()
            ),
            "unexamined": sum(
                item.get("state") in {"unexamined", "inventoried", "located", "read_partial"}
                for item in records_by_path.values()
            )
            + sum(path not in records_by_path for path in snapshot_paths),
            "active_gaps": len(active_gap_paths),
        }
        return {
            **merged,
            **({"source_exclusions": automatic_exclusions} if automatic_exclusions else {}),
            "policy": scan["coverage_policy"],
            "completeness": completeness,
            "counts": counts,
            "active_gap_paths": active_gap_paths,
            "missing_attestation_work_unit_ids": missing_attestations,
        }

    def scan_status(self, scan_id: str) -> dict[str, Any]:
        scan = self.get_scan(scan_id)
        if scan is None:
            raise ValueError("Scan not found")
        with self._connect() as connection:
            counts = {
                "work_units": connection.execute(
                    "SELECT COUNT(*) FROM work_units WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "candidates": connection.execute(
                    "SELECT COUNT(*) FROM candidates WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "verifications": connection.execute(
                    "SELECT COUNT(*) FROM verifications WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "coverage_records": connection.execute(
                    "SELECT COUNT(*) FROM coverage_records cr "
                    "JOIN coverage_attestations ca "
                    "ON ca.attestation_id = cr.attestation_id "
                    "WHERE ca.scan_id = ? AND ca.attestation_id = ("
                    "SELECT nested.attestation_id FROM coverage_attestations nested "
                    "WHERE nested.work_unit_id = ca.work_unit_id "
                    "ORDER BY nested.created_at DESC, nested.attestation_id DESC LIMIT 1"
                    ")",
                    (scan_id,),
                ).fetchone()[0],
                "threat_models": connection.execute(
                    "SELECT COUNT(*) FROM threat_models WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "unverified_candidates": connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates c
                    LEFT JOIN verifications v ON v.candidate_id = c.candidate_id
                    WHERE c.scan_id = ? AND v.candidate_id IS NULL
                    """,
                    (scan_id,),
                ).fetchone()[0],
                "active_work_units": connection.execute(
                    "SELECT COUNT(*) FROM work_units WHERE scan_id = ? AND status IN ('pending', 'running')",
                    (scan_id,),
                ).fetchone()[0],
                "adjudications": connection.execute(
                    "SELECT COUNT(*) FROM adjudications WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()[0],
                "dynamic_runs": connection.execute(
                    "SELECT COUNT(*) FROM dynamic_runs WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()[0],
                "ready_dynamic_runs": connection.execute(
                    "SELECT COUNT(*) FROM dynamic_runs WHERE scan_id = ? AND status = 'ready'",
                    (scan_id,),
                ).fetchone()[0],
                "terminal_dynamic_runs": connection.execute(
                    "SELECT COUNT(*) FROM dynamic_runs "
                    "WHERE scan_id = ? AND status IN "
                    "('not_runnable', 'completed', 'inconclusive')",
                    (scan_id,),
                ).fetchone()[0],
                "poc_bundles": connection.execute(
                    "SELECT COUNT(*) FROM poc_bundles WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "poc_validations": connection.execute(
                    "SELECT COUNT(*) FROM poc_validations WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0],
                "verified_poc_validations": connection.execute(
                    "SELECT COUNT(*) FROM poc_validations WHERE scan_id = ? AND status = 'verified'",
                    (scan_id,),
                ).fetchone()[0],
                "cybergym_imported_pocs": connection.execute(
                    "SELECT COUNT(*) FROM cybergym_artifacts WHERE scan_id = ? "
                    "AND json_extract(provenance_json, '$.operation') = 'generic_poc_import'",
                    (scan_id,),
                ).fetchone()[0],
                "confirmed_without_poc_bundle": connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates c
                    JOIN verifications v ON v.candidate_id = c.candidate_id
                    LEFT JOIN poc_bundles p ON p.candidate_id = c.candidate_id
                    WHERE c.scan_id = ? AND v.verdict = 'confirmed'
                      AND p.candidate_id IS NULL
                      AND EXISTS (
                        SELECT 1 FROM adjudications a, json_each(a.accepted_candidate_ids_json) accepted
                        WHERE a.scan_id = c.scan_id AND a.action = 'finalize'
                          AND a.adjudication_round = (
                            SELECT MAX(latest.adjudication_round)
                            FROM adjudications latest
                            WHERE latest.scan_id = c.scan_id AND latest.action = 'finalize'
                          )
                          AND accepted.value = c.candidate_id
                      )
                    """,
                    (scan_id,),
                ).fetchone()[0] if scan["poc_enabled"] else 0,
                "confirmed_without_dynamic_record": (
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM candidates c
                        JOIN verifications v ON v.candidate_id = c.candidate_id
                        LEFT JOIN dynamic_runs d ON d.candidate_id = c.candidate_id
                        WHERE c.scan_id = ? AND v.verdict = 'confirmed'
                          AND d.candidate_id IS NULL
                        """,
                        (scan_id,),
                    ).fetchone()[0]
                    if scan["dynamic_enabled"]
                    else 0
                ),
            }
            batch_rows = connection.execute(
                "SELECT batch_id, phase, status FROM worker_batches WHERE scan_id = ? ORDER BY created_at, batch_id",
                (scan_id,),
            ).fetchall()
            threat_model_row = connection.execute(
                "SELECT tm.created_at, tm.payload_json, tm.evidence_json, "
                "wu.status AS work_unit_status "
                "FROM threat_models tm "
                "JOIN work_units wu ON wu.work_unit_id = tm.work_unit_id "
                "WHERE tm.scan_id = ?",
                (scan_id,),
            ).fetchone()
            threat_model_batch_row = connection.execute(
                "SELECT status FROM worker_batches "
                "WHERE scan_id = ? AND phase = 'threat_modeling' "
                "ORDER BY created_at DESC, batch_id DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
            adjudication_row = connection.execute(
                "SELECT adjudication_round, action FROM adjudications "
                "WHERE scan_id = ? ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
        threat_model_validation_error: str | None = None
        if threat_model_row is not None and threat_model_row["work_unit_status"] == "completed":
            try:
                self.validate_threat_model_contract(
                    json.loads(threat_model_row["payload_json"]),
                    json.loads(threat_model_row["evidence_json"]),
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                threat_model_status = "invalid"
                threat_model_validation_error = str(exc)
            else:
                threat_model_status = "completed"
        elif threat_model_batch_row is not None:
            threat_model_status = threat_model_batch_row["status"]
        elif threat_model_row is not None:
            threat_model_status = "submitted"
        else:
            threat_model_status = "missing"
        integrity_status = "pending"
        integrity_errors: list[str] = []
        integrity_artifacts: dict[str, str] = {}
        if threat_model_status == "invalid":
            integrity_status = "invalid"
            integrity_errors.append(
                "Threat model failed contract validation"
                + (f": {threat_model_validation_error}" if threat_model_validation_error else "")
            )
        elif scan["status"] == "completed" or (scan["status"] == "failed" and scan.get("output_dir")):
            if threat_model_status == "completed":
                from flocks_code_security.artifact_integrity import (
                    verify_artifact_bundle,
                )

                verified = verify_artifact_bundle(
                    scan_id,
                    Path(scan["output_dir"]) if scan.get("output_dir") else None,
                )
                integrity_status = verified.status
                integrity_errors.extend(verified.errors)
                integrity_artifacts = verified.digests
            else:
                integrity_status = "invalid"
                integrity_errors.append("Completed scan does not have a valid completed threat model")
        knowledge_base = self.get_knowledge_base_metadata(scan_id)
        cybergym_task = self.get_cybergym_task(scan_id)
        cybergym_submission = self.get_cybergym_submission(scan_id) if cybergym_task is not None else None
        return {
            **scan,
            "audit_mode": (
                "cybergym_level1"
                if scan["mode"] == "cybergym_level1"
                else "knowledge_guided" if knowledge_base else "standard"
            ),
            "knowledge_base": knowledge_base,
            "counts": counts,
            "threat_model_status": threat_model_status,
            "integrity_status": integrity_status,
            "integrity_errors": integrity_errors,
            "integrity_artifacts": integrity_artifacts,
            "adjudication": (dict(adjudication_row) if adjudication_row is not None else None),
            "worker_batches": [dict(row) for row in batch_rows],
            "cybergym": (
                {
                    "task_id": cybergym_task["task_id"],
                    "status": cybergym_task["status"],
                    "final_artifact_id": cybergym_task.get("final_artifact_id"),
                    "selected_poc_id": cybergym_task.get("selected_poc_id"),
                    "local_validation": cybergym_task.get("local_validation"),
                    "selection_reason": cybergym_task.get("selection_reason"),
                    "submission": cybergym_submission,
                    "artifact_count": len(self.list_cybergym_artifacts(scan_id)),
                }
                if cybergym_task is not None
                else None
            ),
        }

    def report_data(self, scan_id: str) -> dict[str, Any]:
        scan = self.get_scan(scan_id)
        if scan is None:
            raise ValueError("Scan not found")
        with self._connect() as connection:
            candidate_rows = connection.execute(
                "SELECT * FROM candidates WHERE scan_id = ? ORDER BY created_at", (scan_id,)
            ).fetchall()
            verification_rows = connection.execute(
                "SELECT * FROM verifications WHERE scan_id = ? ORDER BY created_at", (scan_id,)
            ).fetchall()
            verification_vote_rows = connection.execute(
                "SELECT * FROM verification_votes WHERE scan_id = ? "
                "ORDER BY candidate_id, vote_index",
                (scan_id,),
            ).fetchall()
            work_unit_rows = connection.execute(
                "SELECT * FROM work_units WHERE scan_id = ? ORDER BY created_at, work_unit_id",
                (scan_id,),
            ).fetchall()
            work_attempt_rows = connection.execute(
                """
                SELECT wa.work_unit_id, wa.ordinal, wa.provider_id,
                       wa.model_id, wa.status, wa.resume_count
                FROM work_attempts wa
                JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id
                WHERE wu.scan_id = ?
                ORDER BY wu.created_at, wu.work_unit_id, wa.ordinal
                """,
                (scan_id,),
            ).fetchall()
            exclusion_rows = connection.execute(
                "SELECT payload_json FROM scan_events WHERE scan_id = ? "
                "AND event_type = 'source.archive_exclusions' ORDER BY seq", (scan_id,),
            ).fetchall()
            omission_rows = connection.execute(
                """
                SELECT relative_path, reason, size_bytes
                FROM snapshot_omissions WHERE snapshot_id = ? ORDER BY relative_path
                """,
                (scan["snapshot_id"],),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT e.* FROM evidence e
                JOIN candidates c ON c.candidate_id = e.candidate_id
                WHERE c.scan_id = ? ORDER BY e.candidate_id, e.ordinal, e.rowid
                """,
                (scan_id,),
            ).fetchall()
            verification_conflict_rows = connection.execute(
                """
                SELECT vc.candidate_id, vc.payload_json, vc.detected_at
                FROM verification_conflicts vc
                JOIN candidates c ON c.candidate_id = vc.candidate_id
                WHERE c.scan_id = ? ORDER BY vc.candidate_id
                """,
                (scan_id,),
            ).fetchall()
            threat_model_row = connection.execute(
                "SELECT * FROM threat_models WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            adjudication_rows = connection.execute(
                "SELECT * FROM adjudications WHERE scan_id = ? ORDER BY adjudication_round",
                (scan_id,),
            ).fetchall()
            dynamic_run_rows = connection.execute(
                "SELECT * FROM dynamic_runs WHERE scan_id = ? ORDER BY created_at, candidate_id",
                (scan_id,),
            ).fetchall()
            poc_rows = connection.execute(
                "SELECT * FROM poc_bundles WHERE scan_id = ? ORDER BY created_at, candidate_id",
                (scan_id,),
            ).fetchall()
            poc_validation_rows = connection.execute(
                "SELECT * FROM poc_validations WHERE scan_id = ? ORDER BY created_at, validation_id",
                (scan_id,),
            ).fetchall()
            source_access_count_rows = connection.execute(
                "SELECT work_unit_id, operation, COUNT(*) AS count "
                "FROM source_access WHERE scan_id = ? "
                "GROUP BY work_unit_id, operation ORDER BY work_unit_id, operation",
                (scan_id,),
            ).fetchall()
            submission_rejection_rows = connection.execute(
                "SELECT sr.*, wa.work_unit_id FROM submission_rejections sr "
                "JOIN work_attempts wa ON wa.attempt_id = sr.attempt_id "
                "JOIN work_units wu ON wu.work_unit_id = wa.work_unit_id "
                "WHERE wu.scan_id = ? "
                "ORDER BY sr.created_at, sr.rejection_id",
                (scan_id,),
            ).fetchall()
        candidates = []
        for row in candidate_rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            candidates.append(item)
        verifications = []
        for row in verification_rows:
            item = dict(row)
            item["counter_evidence"] = json.loads(item.pop("counter_evidence_json"))
            verifications.append(item)
        verification_votes = []
        for row in verification_vote_rows:
            item = dict(row)
            item["counter_evidence"] = json.loads(
                item.pop("counter_evidence_json")
            )
            verification_votes.append(item)
        work_units = []
        for row in work_unit_rows:
            item = dict(row)
            item["paths"] = json.loads(item.pop("paths_json"))
            work_units.append(item)
        threat_model = None
        if threat_model_row is not None:
            threat_model = dict(threat_model_row)
            threat_model["threat_model"] = json.loads(threat_model.pop("payload_json"))
            threat_model["evidence"] = json.loads(threat_model.pop("evidence_json"))
        source_access_counts: dict[str, dict[str, int]] = {}
        for row in source_access_count_rows:
            source_access_counts.setdefault(row["work_unit_id"], {})[row["operation"]] = row["count"]
        return {
            "scan": scan,
            "knowledge_base": self.get_knowledge_base_metadata(scan_id),
            "threat_model": threat_model,
            "candidates": candidates,
            "evidence": [dict(row) for row in evidence_rows],
            "verifications": verifications,
            "verification_votes": verification_votes,
            "coverage": self.list_latest_coverage(scan_id),
            "work_units": work_units,
            "work_attempts": [dict(row) for row in work_attempt_rows],
            "source_access_counts": source_access_counts,
            "source_exclusions": [item for row in exclusion_rows
                                  for item in json.loads(row["payload_json"])["exclusions"]],
            "submission_rejections": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "rejection_id",
                            "attempt_id",
                            "work_unit_id",
                            "tool_name",
                            "error_code",
                            "created_at",
                        )
                    },
                    "retryable": bool(row["retryable"]),
                    "violations": json.loads(row["violations_json"]),
                }
                for row in submission_rejection_rows
            ],
            "omissions": [dict(row) for row in omission_rows],
            "verification_conflicts": [
                {
                    "candidate_id": row["candidate_id"],
                    "verifications": json.loads(row["payload_json"]),
                    "detected_at": row["detected_at"],
                }
                for row in verification_conflict_rows
            ],
            "adjudications": [self._decode_adjudication(row) for row in adjudication_rows],
            "dynamic_runs": [self._decode_dynamic_run(row) for row in dynamic_run_rows],
            "poc_bundles": [
                {
                    **{key: row[key] for key in ("poc_id", "scan_id", "candidate_id", "work_unit_id", "status", "created_at", "updated_at")},
                    "bundle": json.loads(row["bundle_json"]),
                }
                for row in poc_rows
            ],
            "poc_validations": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "validation_id",
                            "scan_id",
                            "poc_id",
                            "candidate_id",
                            "validator",
                            "status",
                            "artifact_id",
                            "created_at",
                            "updated_at",
                        )
                    },
                    "evidence": json.loads(row["evidence_json"]),
                }
                for row in poc_validation_rows
            ],
        }

    def transition_scan_status(
        self,
        scan_id: str,
        *,
        from_statuses: set[str],
        to_status: str,
    ) -> None:
        allowed_transitions = {
            "running": {"reducing", "cancelled", "failed"},
            "reducing": {"completed", "failed"},
        }
        if not from_statuses:
            raise ValueError("A source scan status is required")
        if any(to_status not in allowed_transitions.get(status, set()) for status in from_statuses):
            raise ValueError("Unsupported scan status transition")
        placeholders = ", ".join("?" for _ in from_statuses)
        values = sorted(from_statuses)
        with self._lock, self._connect() as connection:
            finished_at = _now() if to_status in TERMINAL_SCAN_STATUSES else None
            cursor = connection.execute(
                f"UPDATE scans SET status = ?, updated_at = ?, "
                f"finished_at = COALESCE(?, finished_at) "
                f"WHERE scan_id = ? AND status IN ({placeholders})",
                (to_status, _now(), finished_at, scan_id, *values),
            )
            if cursor.rowcount != 1:
                scan = connection.execute(
                    "SELECT status FROM scans WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()
                if scan is None:
                    raise ValueError("Scan not found")
                raise ValueError(f"Scan status {scan['status']!r} cannot transition to {to_status!r}")

    def delete_snapshot(self, snapshot_id: str) -> None:
        with self._lock, self._connect() as connection:
            references = connection.execute(
                "SELECT COUNT(*) FROM scans WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()[0]
            if references:
                raise ValueError("Cannot delete a snapshot referenced by a scan")
            connection.execute(
                "DELETE FROM snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            )
