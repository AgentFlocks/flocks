"""SQLite persistence for scans, snapshots, bindings, and audit facts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from flocks_code_security.coverage import normalize_open_questions
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


THREAT_MODEL_REQUIRED_LIST_FIELDS = (
    "assets",
    "trustBoundaries",
    "attackerCapabilities",
    "securityObjectives",
)
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_KNOWLEDGE_BASE_BYTES = 32 * 1024
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
WORKER_ROLE_AGENTS = {
    "threat_modeler": "code-security-threat-modeler",
    "baseline": "code-security-baseline",
    "investigator": "code-security-investigator",
    "verifier": "code-security-verifier",
    "prober": "code-security-prober",
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


def process_identity(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            stat_text = Path(f"/proc/{value}/stat").read_text(encoding="utf-8")
            start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        except (IndexError, OSError):
            return None
        return f"linux:{boot_id}:{start_ticks}"
    if sys.platform == "win32":
        return _windows_process_identity(value)
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(value)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started_at = " ".join(result.stdout.split())
    return f"posix:{started_at}" if result.returncode == 0 and started_at else None


def _windows_process_identity(pid: int) -> str | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
        finally:
            kernel32.CloseHandle(handle)
        created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"windows:{created}"
    except (AttributeError, OSError):
        return None


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
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        self.database_path.chmod(0o600)
        connection.row_factory = sqlite3.Row
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
        with self._lock, self._connect() as connection:
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
                    omitted_file_count INTEGER NOT NULL DEFAULT 0
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
            scan_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scans)").fetchall()}
            if "dynamic_enabled" not in scan_columns:
                connection.execute("ALTER TABLE scans ADD COLUMN dynamic_enabled INTEGER NOT NULL DEFAULT 0")
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
        self._restrict_database_files()

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
                    created_at, root_path, omitted_file_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            connection.executemany(
                """
                INSERT INTO snapshot_files VALUES (?, ?, ?, ?, ?, ?, ?)
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
                SELECT relative_path, blob_digest, size_bytes, line_count, language, is_binary
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
                SELECT relative_path, blob_digest, size_bytes, line_count, language, is_binary
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
        )

    def create_scan(
        self,
        *,
        parent_session_id: str,
        snapshot_id: str,
        mode: str,
        ruleset_digest: str,
        dynamic_enabled: bool = False,
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
                "dynamic_enabled, coverage_policy, verification_vote_count, "
                "status, ruleset_digest, created_at, updated_at, "
                "owner_subject, request_source, workspace_ref, idempotency_key, "
                "request_digest, current_phase, task_owner_pid, task_owner_token, "
                "task_owner_identity"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    parent_session_id,
                    snapshot_id,
                    mode,
                    int(bool(dynamic_enabled)),
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
        if role not in {"threat_modeler", "baseline", "investigator", "verifier", "prober"}:
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
        }:
            raise ValueError("Unsupported worker phase")
        if not units or len(units) > 32:
            raise ValueError("A worker batch must contain between 1 and 32 units")
        batch_id = f"batch_{uuid.uuid4().hex}"
        now = _now()
        created_units: list[dict[str, Any]] = []
        with self._lock, self._connect() as connection:
            scan = self._require_scan_status(connection, scan_id, {"running"})
            if phase == "probing" and not bool(scan["dynamic_enabled"]):
                raise ValueError("Dynamic validation is not enabled for this scan")
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
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'baseline'",
                    (scan_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("Baseline workers have already been created")
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
                if role not in {"threat_modeler", "baseline", "investigator", "verifier", "prober"}:
                    raise ValueError("Unsupported work-unit role")
                expected_role = {
                    "threat_modeling": "threat_modeler",
                    "baseline": "baseline",
                    "investigation": "investigator",
                    "verification": "verifier",
                    "probing": "prober",
                    "targeted_rescan": "baseline",
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
                if phase in {"verification", "probing"} and not subject_id:
                    raise ValueError(f"{phase.title()} work units require a candidate subject")
                if phase not in {"verification", "probing"} and subject_id is not None:
                    raise ValueError("Only verification and probing work units may have a subject")
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
            if role in {"threat_modeler", "baseline", "investigator"}:
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
        elif role in {"baseline", "investigator"}:
            if row["completeness"] == "blocked":
                return False
        return True

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
            self._require_scan_status(connection, scan_id, {"running"})
            self._require_threat_model_ready(connection, scan_id)
            self._require_analysis_ready(connection, scan_id)
            self._require_dynamic_ready(connection, scan_id)
            latest = connection.execute(
                "SELECT action FROM adjudications WHERE scan_id = ? ORDER BY adjudication_round DESC LIMIT 1",
                (scan_id,),
            ).fetchone()
        if latest is None or latest["action"] != "finalize":
            raise ValueError("A final parent-agent adjudication is required")

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

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["dynamic_enabled"] = bool(item["dynamic_enabled"])
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
                "SELECT root_path FROM snapshots WHERE snapshot_id = ?",
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
                snapshot_root = snapshot["root_path"] if snapshot is not None else None

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
            binding.role != "threat_modeler"
            or binding.work_unit_id is None
            or binding.attempt_id is None
        ):
            raise ValueError("Repository summary access requires a threat-modeler work unit")
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
            binding.role != "threat_modeler"
            or binding.work_unit_id is None
            or binding.attempt_id is None
        ):
            raise ValueError("Repository summary consumption requires a threat-modeler work unit")
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
        elif scan["status"] == "completed":
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
        return {
            **scan,
            "audit_mode": "knowledge_guided" if knowledge_base else "standard",
            "knowledge_base": knowledge_base,
            "counts": counts,
            "threat_model_status": threat_model_status,
            "integrity_status": integrity_status,
            "integrity_errors": integrity_errors,
            "integrity_artifacts": integrity_artifacts,
            "adjudication": (dict(adjudication_row) if adjudication_row is not None else None),
            "worker_batches": [dict(row) for row in batch_rows],
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
            "source_access_counts": source_access_counts,
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
