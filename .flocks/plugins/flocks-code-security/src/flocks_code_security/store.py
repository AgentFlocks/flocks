"""SQLite persistence for scans, snapshots, bindings, and audit facts."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from flocks_code_security.models import (
    SessionBinding,
    SnapshotFile,
    SnapshotOmission,
    SnapshotRef,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database_path.parent.chmod(0o700)
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    repository_identity TEXT NOT NULL,
                    source_revision TEXT,
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
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ruleset_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    updated_at TEXT NOT NULL
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
                    PRIMARY KEY (batch_id, work_unit_id)
                );
                CREATE TABLE IF NOT EXISTS session_bindings (
                    session_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
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
                    excerpt_hash TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS verification_conflicts (
                    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coverage (
                    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    work_unit_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, work_unit_id)
                );
                """
            )
            snapshot_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(snapshots)").fetchall()
            }
            if "omitted_file_count" not in snapshot_columns:
                connection.execute(
                    "ALTER TABLE snapshots ADD COLUMN omitted_file_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            duplicate_candidates = connection.execute(
                "SELECT candidate_id FROM verifications GROUP BY candidate_id "
                "HAVING COUNT(*) > 1"
            ).fetchall()
            for duplicate in duplicate_candidates:
                candidate_id = duplicate["candidate_id"]
                rows = connection.execute(
                    "SELECT * FROM verifications WHERE candidate_id = ? "
                    "ORDER BY created_at, verification_id",
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
                    "DELETE FROM verifications WHERE candidate_id = ? "
                    "AND verification_id != ?",
                    (candidate_id, rows[0]["verification_id"]),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "verifications_one_per_candidate ON verifications(candidate_id)"
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
                    tree_digest, scope_digest, file_count, total_bytes,
                    created_at, root_path, omitted_file_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.repository_identity,
                    snapshot.source_revision,
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
            row = connection.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        return SnapshotRef(**dict(row)) if row else None

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
    ) -> str:
        scan_id = f"scan_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    parent_session_id,
                    snapshot_id,
                    mode,
                    "running",
                    ruleset_digest,
                    now,
                    now,
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
    ) -> str:
        if role not in {"baseline", "investigator", "verifier"}:
            raise ValueError("Unsupported work-unit role")
        if not paths or not all(isinstance(path, str) and path for path in paths):
            raise ValueError("Work units require at least one path")
        if status not in {"pending", "running", "completed", "failed", "cancelled"}:
            raise ValueError("Unsupported work-unit status")
        work_unit_id = f"unit_{uuid.uuid4().hex}"
        now = _now()
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            connection.execute(
                "INSERT INTO work_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        if phase not in {"baseline", "investigation", "verification"}:
            raise ValueError("Unsupported worker phase")
        if not units or len(units) > 32:
            raise ValueError("A worker batch must contain between 1 and 32 units")
        batch_id = f"batch_{uuid.uuid4().hex}"
        now = _now()
        created_units: list[dict[str, Any]] = []
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            active = connection.execute(
                "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = ? "
                "AND status IN ('pending', 'running')",
                (scan_id, phase),
            ).fetchone()
            if active is not None:
                raise ValueError(f"A {phase} worker batch is already active")
            if phase == "baseline":
                prior = connection.execute(
                    "SELECT 1 FROM worker_batches WHERE scan_id = ? AND phase = 'baseline'",
                    (scan_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("Baseline workers have already been created")
            connection.execute(
                "INSERT INTO worker_batches VALUES (?, ?, ?, ?, ?, ?)",
                (batch_id, scan_id, phase, "pending", now, now),
            )
            for unit in units:
                role = str(unit.get("role") or "")
                paths = unit.get("paths")
                subject_id = unit.get("subject_id")
                if role not in {"baseline", "investigator", "verifier"}:
                    raise ValueError("Unsupported work-unit role")
                expected_role = {
                    "baseline": "baseline",
                    "investigation": "investigator",
                    "verification": "verifier",
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
                if phase == "verification" and not subject_id:
                    raise ValueError("Verification work units require a candidate subject")
                if phase != "verification" and subject_id is not None:
                    raise ValueError("Only verification work units may have a subject")
                if subject_id is not None:
                    candidate = connection.execute(
                        "SELECT scan_id FROM candidates WHERE candidate_id = ?",
                        (subject_id,),
                    ).fetchone()
                    if candidate is None or candidate["scan_id"] != scan_id:
                        raise ValueError(
                            "Verification subject does not belong to the scan"
                        )
                work_unit_id = f"unit_{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO work_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    ),
                )
                connection.execute(
                    "INSERT INTO worker_batch_units VALUES (?, ?, ?)",
                    (batch_id, work_unit_id, subject_id),
                )
                created_units.append(
                    {
                        "work_unit_id": work_unit_id,
                        "role": role,
                        "paths": paths,
                        "subject_id": subject_id,
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
                SELECT wu.*, wbu.subject_id
                FROM worker_batch_units wbu
                JOIN work_units wu ON wu.work_unit_id = wbu.work_unit_id
                WHERE wbu.batch_id = ? ORDER BY wu.created_at, wu.work_unit_id
                """,
                (batch_id,),
            ).fetchall()
        output = dict(batch)
        output["units"] = []
        for row in units:
            item = dict(row)
            item["paths"] = json.loads(item.pop("paths_json"))
            output["units"].append(item)
        return output

    def list_worker_batches(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT batch_id FROM worker_batches WHERE scan_id = ? "
                "ORDER BY created_at, batch_id",
                (scan_id,),
            ).fetchall()
        return [
            batch
            for row in rows
            if (batch := self.get_worker_batch(row["batch_id"])) is not None
        ]

    def set_work_unit_runtime(
        self,
        work_unit_id: str,
        *,
        session_id: str,
        background_task_id: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE work_units SET background_task_id = ?, updated_at = ? "
                "WHERE work_unit_id = ? AND session_id = ? AND status = 'running'",
                (background_task_id, _now(), work_unit_id, session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work unit is not bound to the worker session")

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
                "UPDATE worker_batches SET status = ?, updated_at = ? "
                "WHERE batch_id = ? AND status = ?",
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
            if role in {"baseline", "investigator"}:
                row = connection.execute(
                    "SELECT 1 FROM coverage WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
            elif role == "verifier":
                assignment = connection.execute(
                    "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if assignment is not None and assignment["subject_id"] is not None:
                    row = connection.execute(
                        "SELECT 1 FROM verifications WHERE work_unit_id = ? "
                        "AND candidate_id = ?",
                        (work_unit_id, assignment["subject_id"]),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT 1 FROM verifications WHERE work_unit_id = ?",
                        (work_unit_id,),
                    ).fetchone()
            else:
                raise ValueError("Unsupported work-unit role")
        return row is not None

    def list_unverified_candidates(
        self,
        scan_id: str,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            rows = connection.execute(
                """
                SELECT c.* FROM candidates c
                LEFT JOIN verifications v ON v.candidate_id = c.candidate_id
                WHERE c.scan_id = ? AND v.candidate_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM worker_batch_units assigned
                    JOIN work_units wu ON wu.work_unit_id = assigned.work_unit_id
                    WHERE assigned.subject_id = c.candidate_id
                      AND wu.status IN ('pending', 'running')
                  )
                ORDER BY c.created_at, c.candidate_id LIMIT ?
                """,
                (scan_id, max(1, min(int(limit), 32))),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                evidence = connection.execute(
                    "SELECT relative_path, blob_digest, start_line, end_line, excerpt_hash "
                    "FROM evidence WHERE candidate_id = ? "
                    "ORDER BY relative_path, start_line, end_line",
                    (item["candidate_id"],),
                ).fetchall()
                item["evidence"] = [dict(record) for record in evidence]
                output.append(item)
        return output

    def cancel_scan_work(self, scan_id: str) -> list[str]:
        with self._lock, self._connect() as connection:
            task_rows = connection.execute(
                "SELECT background_task_id FROM work_units WHERE scan_id = ? "
                "AND status IN ('pending', 'running') AND background_task_id IS NOT NULL",
                (scan_id,),
            ).fetchall()
            connection.execute(
                "UPDATE work_units SET status = 'cancelled', updated_at = ? "
                "WHERE scan_id = ? AND status IN ('pending', 'running')",
                (_now(), scan_id),
            )
            connection.execute(
                "UPDATE worker_batches SET status = 'cancelled', updated_at = ? "
                "WHERE scan_id = ? AND status IN ('pending', 'running')",
                (_now(), scan_id),
            )
        return [row["background_task_id"] for row in task_rows]

    def ensure_ready_to_finalize(self, scan_id: str) -> None:
        with self._connect() as connection:
            self._require_scan_status(connection, scan_id, {"running"})
            active = connection.execute(
                "SELECT COUNT(*) FROM work_units WHERE scan_id = ? "
                "AND status IN ('pending', 'running')",
                (scan_id,),
            ).fetchone()[0]
        if active:
            raise ValueError("Cannot finalize while audit workers are still active")

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
            cursor = connection.execute(
                "UPDATE work_units SET status = ?, updated_at = ? "
                "WHERE work_unit_id = ? AND status = ?",
                (status, _now(), work_unit_id, row["status"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work unit status changed concurrently")

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_scan(self, scan_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM scans WHERE scan_id = ?", (scan_id,))

    def bind_session(
        self,
        *,
        session_id: str,
        scan_id: str,
        snapshot_id: str,
        role: str,
        work_unit_id: str | None = None,
    ) -> None:
        allowed_roles = {"coordinator", "baseline", "investigator", "verifier"}
        if role not in allowed_roles:
            raise ValueError("Unsupported session binding role")
        if role == "coordinator" and work_unit_id is not None:
            raise ValueError("Coordinator bindings cannot reference a work unit")
        if role != "coordinator" and work_unit_id is None:
            raise ValueError("Worker bindings require a work unit")
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
            if work_unit_id is not None:
                work_unit = connection.execute(
                    "SELECT scan_id, role, status, session_id "
                    "FROM work_units WHERE work_unit_id = ?",
                    (work_unit_id,),
                ).fetchone()
                if work_unit is None:
                    raise ValueError("Work unit not found")
                if work_unit["scan_id"] != scan_id or work_unit["role"] != role:
                    raise ValueError("Work unit does not match the binding")
                if work_unit["status"] not in {"pending", "running"}:
                    raise ValueError("Completed work units cannot be rebound")
                if work_unit["session_id"] not in {None, session_id}:
                    raise ValueError("Work unit is already bound to another session")
            existing = connection.execute(
                "SELECT scan_id, work_unit_id, snapshot_id, role "
                "FROM session_bindings WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            expected = (scan_id, work_unit_id, snapshot_id, role)
            if existing is not None and tuple(existing) != expected:
                raise ValueError("Session is already bound to another audit context")
            connection.execute(
                """
                INSERT INTO session_bindings VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    scan_id = excluded.scan_id,
                    work_unit_id = excluded.work_unit_id,
                    snapshot_id = excluded.snapshot_id,
                    role = excluded.role,
                    created_at = excluded.created_at
                """,
                (session_id, scan_id, work_unit_id, snapshot_id, role, _now()),
            )
            if work_unit_id is not None:
                cursor = connection.execute(
                    "UPDATE work_units SET session_id = ?, status = 'running', "
                    "updated_at = ? WHERE work_unit_id = ? "
                    "AND (session_id IS NULL OR session_id = ?)",
                    (session_id, _now(), work_unit_id, session_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Work unit is already bound to another session")

    def resolve_binding(self, session_id: str) -> SessionBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, scan_id, work_unit_id, snapshot_id, role
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
            raise ValueError(
                f"Scan status {scan['status']!r} does not allow this operation"
            )
        return scan

    @staticmethod
    def _require_active_worker_binding(
        connection: sqlite3.Connection,
        binding: SessionBinding,
    ) -> None:
        if binding.work_unit_id is None:
            raise ValueError("Worker operation requires a bound work unit")
        work_unit = connection.execute(
            "SELECT scan_id, role, session_id, status FROM work_units "
            "WHERE work_unit_id = ?",
            (binding.work_unit_id,),
        ).fetchone()
        if (
            work_unit is None
            or work_unit["scan_id"] != binding.scan_id
            or work_unit["role"] != binding.role
            or work_unit["session_id"] != binding.session_id
            or work_unit["status"] != "running"
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
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"evidence_{uuid.uuid4().hex}",
                        candidate_id,
                        item["relative_path"],
                        item["blob_digest"],
                        item["start_line"],
                        item["end_line"],
                        item["excerpt_hash"],
                    )
                    for item in evidence
                ],
            )
        return candidate_id

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    def save_verification(
        self,
        binding: SessionBinding,
        *,
        candidate_id: str,
        verdict: str,
        rationale: str,
        counter_evidence: list[dict[str, Any]],
    ) -> str:
        verification_id = f"verify_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            candidate = connection.execute(
                "SELECT scan_id FROM candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if candidate is None or candidate["scan_id"] != binding.scan_id:
                raise ValueError("Candidate does not belong to this scan")
            assignment = connection.execute(
                "SELECT subject_id FROM worker_batch_units WHERE work_unit_id = ?",
                (binding.work_unit_id,),
            ).fetchone()
            if (
                assignment is not None
                and assignment["subject_id"] is not None
                and assignment["subject_id"] != candidate_id
            ):
                raise ValueError("Candidate is not assigned to this verifier work unit")
            existing = connection.execute(
                "SELECT 1 FROM verifications WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError("Candidate already has a verification verdict")
            connection.execute(
                "INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    verification_id,
                    candidate_id,
                    binding.scan_id,
                    binding.work_unit_id,
                    verdict,
                    rationale,
                    json.dumps(counter_evidence, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
        return verification_id

    def save_coverage(self, binding: SessionBinding, payload: dict[str, Any]) -> None:
        if binding.work_unit_id is None:
            raise ValueError("Coverage requires a bound work unit")
        work_unit_id = binding.work_unit_id
        with self._lock, self._connect() as connection:
            self._require_scan_status(connection, binding.scan_id, {"running"})
            self._require_active_worker_binding(connection, binding)
            work_unit = connection.execute(
                "SELECT scan_id, role FROM work_units WHERE work_unit_id = ?",
                (work_unit_id,),
            ).fetchone()
            if (
                work_unit is None
                or work_unit["scan_id"] != binding.scan_id
                or work_unit["role"] != binding.role
            ):
                raise ValueError("Coverage work unit does not match the binding")
            connection.execute(
                """
                INSERT INTO coverage VALUES (?, ?, ?, ?)
                ON CONFLICT(scan_id, work_unit_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    binding.scan_id,
                    work_unit_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )

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
                    "SELECT COUNT(*) FROM coverage WHERE scan_id = ?", (scan_id,)
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
                    "SELECT COUNT(*) FROM work_units WHERE scan_id = ? "
                    "AND status IN ('pending', 'running')",
                    (scan_id,),
                ).fetchone()[0],
            }
            batch_rows = connection.execute(
                "SELECT batch_id, phase, status FROM worker_batches "
                "WHERE scan_id = ? ORDER BY created_at, batch_id",
                (scan_id,),
            ).fetchall()
        return {
            **scan,
            "counts": counts,
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
            coverage_rows = connection.execute(
                "SELECT * FROM coverage WHERE scan_id = ? ORDER BY work_unit_id", (scan_id,)
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
                WHERE c.scan_id = ? ORDER BY e.candidate_id, e.relative_path, e.start_line
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
        coverage = []
        for row in coverage_rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            coverage.append(item)
        work_units = []
        for row in work_unit_rows:
            item = dict(row)
            item["paths"] = json.loads(item.pop("paths_json"))
            work_units.append(item)
        return {
            "scan": scan,
            "candidates": candidates,
            "evidence": [dict(row) for row in evidence_rows],
            "verifications": verifications,
            "coverage": coverage,
            "work_units": work_units,
            "omissions": [dict(row) for row in omission_rows],
            "verification_conflicts": [
                {
                    "candidate_id": row["candidate_id"],
                    "verifications": json.loads(row["payload_json"]),
                    "detected_at": row["detected_at"],
                }
                for row in verification_conflict_rows
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
            "reducing": {"completed", "partial", "failed"},
        }
        if not from_statuses:
            raise ValueError("A source scan status is required")
        if any(to_status not in allowed_transitions.get(status, set()) for status in from_statuses):
            raise ValueError("Unsupported scan status transition")
        placeholders = ", ".join("?" for _ in from_statuses)
        values = sorted(from_statuses)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE scans SET status = ?, updated_at = ? "
                f"WHERE scan_id = ? AND status IN ({placeholders})",
                (to_status, _now(), scan_id, *values),
            )
            if cursor.rowcount != 1:
                scan = connection.execute(
                    "SELECT status FROM scans WHERE scan_id = ?",
                    (scan_id,),
                ).fetchone()
                if scan is None:
                    raise ValueError("Scan not found")
                raise ValueError(
                    f"Scan status {scan['status']!r} cannot transition to {to_status!r}"
                )

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
