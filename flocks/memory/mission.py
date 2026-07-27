"""Filesystem-backed Mission state for long-running sessions."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

import yaml


MISSION_STATUSES = {"active", "ready_to_close", "completed", "aborted"}
TASK_STATUSES = {"pending", "running", "cleared", "failed", "superseded"}
FINDING_STATUSES = {"candidate", "confirmed", "rejected", "superseded"}
ASSERTION_STATUSES = {"pending", "passed", "failed"}
TERMINAL_TASK_STATUSES = {"cleared", "superseded"}
TODO_STATUS_MAP = {
    "pending": "pending",
    "in_progress": "running",
    "completed": "cleared",
    "cancelled": "superseded",
}
VALIDATION_TERMS = (
    "verify",
    "verification",
    "validate",
    "validation",
    "test",
    "check",
    "验证",
    "测试",
    "检查",
)

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").replace("|", r"\|").split())


def _split_table_row(line: str) -> List[str]:
    raw = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", raw)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _section(text: str, heading: str, level: int = 1) -> str:
    marker = "#" * level
    pattern = re.compile(
        rf"(?ms)^{re.escape(marker)} {re.escape(heading)}\s*\n(.*?)(?=^{'#' * level} |\Z)"
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _parse_table(section: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if not cells or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def _parse_list(section: str) -> List[str]:
    return [
        line.strip()[2:].strip()
        for line in section.splitlines()
        if line.strip().startswith("- ") and line.strip()[2:].strip()
    ]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        tmp_path.unlink(missing_ok=True)


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Lock a Mission across threads and, where supported, processes."""
    key = str(path.resolve(strict=False))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            try:
                yield
            finally:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass


class MissionStore:
    """Narrow storage boundary for protected Mission files."""

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve(strict=False)
        self.memory_dir = self.workspace_dir / ".flocks" / "memory"
        self.missions_dir = self.memory_dir / "missions"

    def mission_dir(self, mission_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", mission_id):
            raise ValueError(f"Invalid mission id: {mission_id!r}")
        path = (self.missions_dir / mission_id).resolve(strict=False)
        root = self.missions_dir.resolve(strict=False)
        if self.workspace_dir not in root.parents:
            raise ValueError("Mission root escapes the project workspace")
        if path != root and root not in path.parents:
            raise ValueError("Mission path escapes the project memory directory")
        return path

    def mission_path(self, mission_id: str) -> Path:
        return self.mission_dir(mission_id) / "mission.md"

    def exists(self, mission_id: str) -> bool:
        return self.mission_path(mission_id).is_file()

    @staticmethod
    def generate_id(session_id: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = re.sub(r"[^A-Za-z0-9]", "", session_id)[-8:] or "session"
        return f"mission-{stamp}-{suffix}"

    def create(
        self,
        *,
        mission_id: str,
        session_id: str,
        original_request: str,
        todos: Iterable[Any],
    ) -> Dict[str, Any]:
        """Create a Mission from the first complex Todo list."""
        mission_dir = self.mission_dir(mission_id)
        lock_path = mission_dir / ".mission.lock"
        with _file_lock(lock_path):
            if self.mission_path(mission_id).exists():
                return self.load(mission_id)

            now = utc_now()
            tasks = self._tasks_from_todos(todos)
            contract = [
                {
                    "id": "C001",
                    "assertion": "The original user request is fully satisfied.",
                    "status": "pending",
                    "evidence": [],
                }
            ]
            for index, task in enumerate(tasks, start=2):
                assertion_id = f"C{index:03d}"
                task["targets"] = [assertion_id]
                contract.append(
                    {
                        "id": assertion_id,
                        "assertion": f"Task {task['id']} is completed: {task['body']}",
                        "status": (
                            "passed"
                            if task["status"] == "cleared"
                            else "pending"
                        ),
                        "evidence": [],
                    }
                )
            contract.append(
                {
                    "id": f"C{len(contract) + 1:03d}",
                    "assertion": "Necessary validation has completed successfully.",
                    "status": "pending",
                    "evidence": [],
                }
            )
            state = {
                "meta": {
                    "mission_id": mission_id,
                    "status": "active",
                    "revision": 1,
                    "source_session_id": session_id,
                    "created_at": now,
                    "updated_at": now,
                },
                "original_request": original_request.strip(),
                "scope": str(self.workspace_dir),
                "non_goals": "No additional scope beyond the user request.",
                "contract": contract,
                "tasks": tasks,
                "attention": [],
                "current_state": "Mission created from the current session Todo list.",
                "next_action": self._next_action(tasks),
                "closeout": "",
            }
            mission_dir.mkdir(parents=True, exist_ok=True)
            (mission_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            _atomic_write(self.mission_path(mission_id), self._render_mission(state))
            _atomic_write(mission_dir / "findings.md", "# Findings\n")
            _atomic_write(mission_dir / "progress.md", "# Progress\n")
            _atomic_write(
                mission_dir / "artifacts" / "INDEX.md",
                "# Artifact Index\n\n"
                "| Path | Summary | SHA-256 | Created At | Source | Task | Finding |\n"
                "|---|---|---|---|---|---|---|\n",
            )
            self._append_progress_unlocked(
                mission_id,
                {
                    "session_id": session_id,
                    "task_id": None,
                    "kind": "checkpoint",
                    "summary": "Mission created",
                    "details": "Created from the first complex Todo list.",
                    "status": "active",
                    "finding_id": None,
                    "artifact_path": None,
                    "source_refs": [],
                },
            )
            return state

    def load(self, mission_id: str) -> Dict[str, Any]:
        path = self.mission_path(mission_id)
        if not path.exists():
            raise FileNotFoundError(f"Mission not found: {mission_id}")
        return self._parse_mission(path.read_text(encoding="utf-8"))

    def sync_todos(
        self,
        mission_id: str,
        todos: Iterable[Any],
        *,
        session_id: str,
    ) -> Dict[str, Any]:
        """Synchronize Todo status into the Mission task list."""
        with _file_lock(self.mission_dir(mission_id) / ".mission.lock"):
            state = self.load(mission_id)
            incoming = {task["id"]: task for task in self._tasks_from_todos(todos)}
            existing = {task["id"]: task for task in state["tasks"]}
            started_attempts: List[Dict[str, str]] = []
            for task_id, task in incoming.items():
                if task_id in existing:
                    previous = existing[task_id]["status"]
                    existing[task_id].update(
                        {
                            "body": task["body"],
                            "type": task["type"],
                            "status": task["status"],
                        }
                    )
                    if previous != "running" and task["status"] == "running":
                        existing[task_id]["last_attempt"] = utc_now()
                        started_attempts.append(existing[task_id])
                else:
                    assertion_id = f"C{len(state['contract']) + 1:03d}"
                    task["targets"] = [assertion_id]
                    existing[task_id] = task
                    state["contract"].insert(
                        max(1, len(state["contract"]) - 1),
                        {
                            "id": assertion_id,
                            "assertion": f"Task {task_id} is completed: {task['body']}",
                            "status": "pending",
                            "evidence": [],
                        },
                    )

            incoming_ids = set(incoming)
            for task_id, task in existing.items():
                if task_id not in incoming_ids and task["status"] not in TERMINAL_TASK_STATUSES:
                    task["status"] = "superseded"
            state["tasks"] = list(existing.values())
            self._sync_contract_from_tasks(state)
            all_terminal = bool(state["tasks"]) and all(
                task["status"] in TERMINAL_TASK_STATUSES
                for task in state["tasks"]
            )
            state["meta"]["status"] = "ready_to_close" if all_terminal else "active"
            state["current_state"] = (
                "All planned tasks reached a terminal state."
                if all_terminal
                else "Todo state synchronized; work remains."
            )
            state["next_action"] = (
                "Run the completion gate and resolve any evidence gaps."
                if all_terminal
                else self._next_action(state["tasks"])
            )
            self._save_unlocked(state)
            self._append_progress_unlocked(
                mission_id,
                {
                    "session_id": session_id,
                    "task_id": None,
                    "kind": "checkpoint",
                    "summary": "Todo state synchronized",
                    "details": state["current_state"],
                    "status": state["meta"]["status"],
                    "finding_id": None,
                    "artifact_path": None,
                    "source_refs": [],
                },
            )
            for task in started_attempts:
                self._append_progress_unlocked(
                    mission_id,
                    {
                        "session_id": session_id,
                        "task_id": task["id"],
                        "kind": "progress",
                        "summary": f"Attempt started: {task['body']}",
                        "details": "Task transitioned to running.",
                        "status": "running",
                        "finding_id": None,
                        "artifact_path": None,
                        "source_refs": [],
                    },
                )
            if all_terminal:
                return self._evaluate_completion_unlocked(state, session_id=session_id)
            return {"completed": False, "gaps": [], "state": state}

    def record(
        self,
        mission_id: str,
        *,
        session_id: str,
        kind: str,
        summary: str,
        details: Optional[str] = None,
        task_id: Optional[str] = None,
        finding_id: Optional[str] = None,
        status: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        artifact_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply one normalized Mission record."""
        if kind not in {"progress", "finding", "validation", "artifact", "checkpoint"}:
            raise ValueError(f"Unsupported mission record kind: {kind}")
        with _file_lock(self.mission_dir(mission_id) / ".mission.lock"):
            state = self.load(mission_id)
            if state["meta"]["status"] in {"completed", "aborted"}:
                raise ValueError("The Mission is closed and cannot accept new records")

            refs = [str(ref).strip() for ref in (source_refs or []) if str(ref).strip()]
            stored_artifact: Optional[str] = None
            if artifact_path:
                stored_artifact = self._store_artifact_unlocked(
                    mission_id,
                    artifact_path,
                    summary=summary,
                    session_id=session_id,
                    task_id=task_id,
                    finding_id=finding_id,
                )
                refs.append(stored_artifact)

            if kind == "finding":
                finding_id = finding_id or self._next_finding_id(mission_id)
                finding_status = status or "candidate"
                if finding_status not in FINDING_STATUSES:
                    raise ValueError(f"Invalid finding status: {finding_status}")
                self._upsert_finding_unlocked(
                    mission_id,
                    {
                        "finding_id": finding_id,
                        "summary": summary,
                        "status": finding_status,
                        "affected_scope": details or "",
                        "evidence_refs": refs,
                        "validation_result": "",
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    },
                )

            if kind == "validation":
                validation_status = (status or "passed").lower()
                if validation_status not in {"passed", "failed"}:
                    raise ValueError("Validation status must be passed or failed")
                self._apply_validation(
                    state,
                    status=validation_status,
                    evidence=refs,
                    finding_id=finding_id,
                    details=details or summary,
                )
                if finding_id:
                    self._record_finding_validation_unlocked(
                        mission_id,
                        finding_id=finding_id,
                        passed=validation_status == "passed",
                        result=details or summary,
                        evidence_refs=refs,
                    )

            if task_id and status:
                task_status = self._normalize_task_status(status)
                for task in state["tasks"]:
                    if task["id"] == task_id:
                        task["status"] = task_status
                        task["last_attempt"] = utc_now()
                        break

            if kind == "checkpoint":
                state["current_state"] = details or summary
                state["next_action"] = summary

            if status == "failed":
                attention = f"{task_id or finding_id or kind}: {details or summary}"
                if attention not in state["attention"]:
                    state["attention"].append(attention)
            elif status in {"cleared", "completed", "passed", "confirmed", "rejected"}:
                resolved_prefixes = {
                    value
                    for value in (task_id, finding_id)
                    if value
                }
                state["attention"] = [
                    item
                    for item in state["attention"]
                    if not (
                        (kind == "validation" and "validation" in item.lower())
                        or any(
                            item.startswith(f"{prefix}:")
                            for prefix in resolved_prefixes
                        )
                    )
                ]

            self._sync_contract_from_tasks(state)
            self._save_unlocked(state)
            sequence = self._append_progress_unlocked(
                mission_id,
                {
                    "session_id": session_id,
                    "task_id": task_id,
                    "kind": kind,
                    "summary": summary,
                    "details": details or "",
                    "status": status,
                    "finding_id": finding_id,
                    "artifact_path": stored_artifact,
                    "source_refs": refs,
                },
            )
            return {
                "mission_id": mission_id,
                "revision": state["meta"]["revision"],
                "sequence": sequence,
                "finding_id": finding_id,
                "artifact_path": stored_artifact,
            }

    def evaluate_completion(
        self,
        mission_id: str,
        *,
        session_id: str,
    ) -> Dict[str, Any]:
        with _file_lock(self.mission_dir(mission_id) / ".mission.lock"):
            return self._evaluate_completion_unlocked(
                self.load(mission_id),
                session_id=session_id,
            )

    def render_hot_context(self, mission_id: str) -> str:
        """Render bounded Mission context for the next model call."""
        state = self.load(mission_id)
        mission_text = self._render_mission(state)
        findings = self._load_findings(mission_id)[-3:]
        progress = self._read_progress_entries(mission_id)[-5:]
        parts = ["## Mission Hot Context", mission_text]
        if findings:
            parts.extend(
                [
                    "## Recent Finding Summaries",
                    *[
                        f"- {item['finding_id']} [{item['status']}]: {item['summary']}"
                        for item in findings
                    ],
                ]
            )
        if progress:
            parts.extend(
                [
                    "## Recent Progress",
                    *[
                        f"- #{item.get('sequence')} {item.get('kind')}: "
                        f"{item.get('summary')}"
                        for item in progress
                    ],
                ]
            )
        return "\n\n".join(part for part in parts if part).strip()

    def _evaluate_completion_unlocked(
        self,
        state: Dict[str, Any],
        *,
        session_id: str,
    ) -> Dict[str, Any]:
        state["attention"] = [
            item
            for item in state["attention"]
            if not item.startswith("Completion gap:")
        ]
        self._sync_contract_from_tasks(state)
        gaps: List[str] = []
        active_tasks = [
            task["id"]
            for task in state["tasks"]
            if task["status"] in {"pending", "running", "failed"}
        ]
        if active_tasks:
            gaps.append(f"Tasks not cleared: {', '.join(active_tasks)}")

        for assertion in state["contract"][1:]:
            if assertion["status"] != "passed":
                gaps.append(
                    f"Contract {assertion['id']} is {assertion['status']}: "
                    f"{assertion['assertion']}"
                )

        findings = self._load_findings(state["meta"]["mission_id"])
        candidates = [
            finding["finding_id"]
            for finding in findings
            if finding["status"] == "candidate"
        ]
        if candidates:
            gaps.append(f"Candidate findings unresolved: {', '.join(candidates)}")
        for finding in findings:
            if finding["status"] == "confirmed" and not finding["evidence_refs"]:
                gaps.append(f"Confirmed finding lacks evidence: {finding['finding_id']}")

        validations = [
            entry
            for entry in self._read_progress_entries(state["meta"]["mission_id"])
            if entry.get("kind") == "validation" and entry.get("status") == "passed"
        ]
        if not validations:
            gaps.append("No successful validation record exists")

        artifact_errors = self._verify_artifacts(state["meta"]["mission_id"])
        gaps.extend(artifact_errors)
        if state["attention"]:
            gaps.append("Attention remains: " + "; ".join(state["attention"]))

        if gaps:
            state["meta"]["status"] = "active"
            state["attention"] = list(
                dict.fromkeys(
                    state["attention"]
                    + [f"Completion gap: {gap}" for gap in gaps]
                )
            )
            state["current_state"] = "Completion gate failed."
            state["next_action"] = gaps[0]
            self._save_unlocked(state)
            self._append_progress_unlocked(
                state["meta"]["mission_id"],
                {
                    "session_id": session_id,
                    "task_id": None,
                    "kind": "checkpoint",
                    "summary": "Completion gate failed",
                    "details": "\n".join(gaps),
                    "status": "active",
                    "finding_id": None,
                    "artifact_path": None,
                    "source_refs": [],
                },
            )
            return {"completed": False, "gaps": gaps, "state": state}

        for assertion in state["contract"]:
            assertion["status"] = "passed"
        state["meta"]["status"] = "completed"
        state["current_state"] = "Mission completed."
        state["next_action"] = "Provide the final result to the user."
        state["closeout"] = (
            f"Completed at {utc_now()}. All tasks and contract assertions passed "
            "the evidence gate."
        )
        self._save_unlocked(state)
        self._append_progress_unlocked(
            state["meta"]["mission_id"],
            {
                "session_id": session_id,
                "task_id": None,
                "kind": "checkpoint",
                "summary": "Mission completed",
                "details": state["closeout"],
                "status": "completed",
                "finding_id": None,
                "artifact_path": None,
                "source_refs": [],
            },
        )
        return {"completed": True, "gaps": [], "state": state}

    def _save_unlocked(self, state: Dict[str, Any]) -> None:
        state["meta"]["revision"] = int(state["meta"].get("revision", 0)) + 1
        state["meta"]["updated_at"] = utc_now()
        _atomic_write(
            self.mission_path(state["meta"]["mission_id"]),
            self._render_mission(state),
        )

    @staticmethod
    def _tasks_from_todos(todos: Iterable[Any]) -> List[Dict[str, Any]]:
        tasks: List[Dict[str, Any]] = []
        for item in todos:
            payload = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            content = str(payload.get("content") or "").strip()
            lowered = content.lower()
            tasks.append(
                {
                    "id": str(payload.get("id") or "").strip(),
                    "type": (
                        "validate"
                        if any(term in lowered for term in VALIDATION_TERMS)
                        else "work"
                    ),
                    "body": content,
                    "targets": [],
                    "depends_on": [],
                    "status": TODO_STATUS_MAP.get(
                        str(payload.get("status") or "pending"),
                        "pending",
                    ),
                    "last_attempt": (
                        utc_now()
                        if payload.get("status") == "in_progress"
                        else ""
                    ),
                }
            )
        return tasks

    @staticmethod
    def _next_action(tasks: Iterable[Dict[str, Any]]) -> str:
        for task in tasks:
            if task["status"] in {"running", "pending", "failed"}:
                return f"{task['id']}: {task['body']}"
        return "Run the completion gate."

    @staticmethod
    def _sync_contract_from_tasks(state: Dict[str, Any]) -> None:
        contract_by_id = {item["id"]: item for item in state["contract"]}
        for task in state["tasks"]:
            for assertion_id in task["targets"]:
                assertion = contract_by_id.get(assertion_id)
                if assertion:
                    assertion["status"] = (
                        "passed"
                        if task["status"] == "cleared"
                        else "failed"
                        if task["status"] == "failed"
                        else "pending"
                    )
        if len(state["contract"]) > 1 and all(
            item["status"] == "passed" for item in state["contract"][1:]
        ):
            state["contract"][0]["status"] = "passed"

    @staticmethod
    def _apply_validation(
        state: Dict[str, Any],
        *,
        status: str,
        evidence: List[str],
        finding_id: Optional[str],
        details: str,
    ) -> None:
        target_ids = {ref for ref in evidence if re.fullmatch(r"C\d{3}", ref)}
        targets = [
            item
            for item in state["contract"]
            if item["id"] in target_ids
        ]
        if not targets and state["contract"]:
            targets = [state["contract"][-1]]
        for assertion in targets:
            assertion["status"] = status
            assertion["evidence"] = list(
                dict.fromkeys(assertion["evidence"] + evidence)
            )
        if status == "failed":
            message = f"Validation failed: {details}"
            if message not in state["attention"]:
                state["attention"].append(message)
        elif status == "passed":
            state["attention"] = [
                item for item in state["attention"] if not item.startswith("Validation failed:")
            ]

    @staticmethod
    def _normalize_task_status(status: str) -> str:
        normalized = TODO_STATUS_MAP.get(status, status)
        if normalized not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status}")
        return normalized

    def _append_progress_unlocked(
        self,
        mission_id: str,
        entry: Dict[str, Any],
    ) -> int:
        path = self.mission_dir(mission_id) / "progress.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Progress\n"
        entries = self._read_progress_entries(mission_id)
        sequence = max(
            [int(item.get("sequence", 0)) for item in entries],
            default=0,
        ) + 1
        payload = {
            "sequence": sequence,
            "timestamp": utc_now(),
            **entry,
        }
        block = (
            f"\n## {sequence:06d} — {_one_line(payload['summary'])}\n\n"
            "```yaml\n"
            f"{yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip()}\n"
            "```\n"
        )
        _atomic_write(path, existing.rstrip() + "\n" + block)
        return sequence

    def _read_progress_entries(self, mission_id: str) -> List[Dict[str, Any]]:
        path = self.mission_dir(mission_id) / "progress.md"
        if not path.exists():
            return []
        return self._parse_yaml_blocks(path.read_text(encoding="utf-8"))

    def _load_findings(self, mission_id: str) -> List[Dict[str, Any]]:
        path = self.mission_dir(mission_id) / "findings.md"
        if not path.exists():
            return []
        return self._parse_yaml_blocks(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_yaml_blocks(text: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for raw in re.findall(r"(?ms)^```yaml\s*\n(.*?)^```\s*$", text):
            payload = yaml.safe_load(raw)
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def _next_finding_id(self, mission_id: str) -> str:
        values = []
        for finding in self._load_findings(mission_id):
            match = re.fullmatch(r"F(\d+)", str(finding.get("finding_id", "")))
            if match:
                values.append(int(match.group(1)))
        return f"F{max(values, default=0) + 1:03d}"

    def _upsert_finding_unlocked(
        self,
        mission_id: str,
        finding: Dict[str, Any],
    ) -> None:
        path = self.mission_dir(mission_id) / "findings.md"
        findings = self._load_findings(mission_id)
        replaced = False
        for index, current in enumerate(findings):
            if current.get("finding_id") == finding["finding_id"]:
                finding["created_at"] = current.get("created_at") or finding["created_at"]
                findings[index] = finding
                replaced = True
                break
        if not replaced:
            findings.append(finding)
        parts = ["# Findings", ""]
        for item in findings:
            parts.extend(
                [
                    f"## {item['finding_id']} — {_one_line(item['summary'])}",
                    "",
                    "```yaml",
                    yaml.safe_dump(
                        item,
                        sort_keys=False,
                        allow_unicode=True,
                    ).rstrip(),
                    "```",
                    "",
                ]
            )
        _atomic_write(path, "\n".join(parts).rstrip() + "\n")

    def _record_finding_validation_unlocked(
        self,
        mission_id: str,
        *,
        finding_id: str,
        passed: bool,
        result: str,
        evidence_refs: List[str],
    ) -> None:
        findings = self._load_findings(mission_id)
        for finding in findings:
            if finding.get("finding_id") != finding_id:
                continue
            finding["status"] = "confirmed" if passed else "rejected"
            finding["validation_result"] = result
            finding["evidence_refs"] = list(
                dict.fromkeys(
                    list(finding.get("evidence_refs") or []) + evidence_refs
                )
            )
            finding["updated_at"] = utc_now()
            self._upsert_finding_unlocked(mission_id, finding)
            return
        raise ValueError(f"Finding not found: {finding_id}")

    def _store_artifact_unlocked(
        self,
        mission_id: str,
        source_path: str,
        *,
        summary: str,
        session_id: str,
        task_id: Optional[str],
        finding_id: Optional[str],
    ) -> str:
        source = Path(source_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Artifact source is not a file: {source}")
        artifacts_dir = self.mission_dir(mission_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        stem = re.sub(r"[^A-Za-z0-9._-]", "-", source.stem)[:80] or "artifact"
        suffix = source.suffix[:20]
        destination = artifacts_dir / f"{stem}-{digest[:12]}{suffix}"
        if not destination.exists():
            shutil.copy2(source, destination)
        relative = destination.relative_to(self.mission_dir(mission_id)).as_posix()
        index_path = artifacts_dir / "INDEX.md"
        existing = (
            index_path.read_text(encoding="utf-8")
            if index_path.exists()
            else "# Artifact Index\n\n"
        )
        row = (
            f"| {_one_line(relative)} | {_one_line(summary)} | {digest} | "
            f"{utc_now()} | {_one_line(session_id)} | {_one_line(task_id)} | "
            f"{_one_line(finding_id)} |"
        )
        if relative not in existing:
            _atomic_write(index_path, existing.rstrip() + "\n" + row + "\n")
        return relative

    def _verify_artifacts(self, mission_id: str) -> List[str]:
        index = self.mission_dir(mission_id) / "artifacts" / "INDEX.md"
        if not index.exists():
            return []
        errors: List[str] = []
        for row in _parse_table(index.read_text(encoding="utf-8")):
            if len(row) < 3:
                continue
            relative, expected = row[0], row[2]
            path = (self.mission_dir(mission_id) / relative).resolve(strict=False)
            root = self.mission_dir(mission_id).resolve(strict=False)
            if root not in path.parents:
                errors.append(f"Artifact escapes Mission directory: {relative}")
                continue
            if not path.is_file():
                errors.append(f"Artifact is missing: {relative}")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                errors.append(f"Artifact hash mismatch: {relative}")
        return errors

    @staticmethod
    def _render_mission(state: Dict[str, Any]) -> str:
        meta = yaml.safe_dump(
            state["meta"],
            sort_keys=False,
            allow_unicode=True,
        ).rstrip()
        lines = [
            "---",
            meta,
            "---",
            "",
            "# Mission Brief",
            "",
            "## Original Request",
            state["original_request"],
            "",
            "## Scope",
            state["scope"],
            "",
            "## Non-goals",
            state["non_goals"],
            "",
            "# Contract",
            "",
            "| ID | Assertion | Status | Evidence |",
            "|---|---|---|---|",
        ]
        for item in state["contract"]:
            lines.append(
                f"| {_one_line(item['id'])} | {_one_line(item['assertion'])} | "
                f"{_one_line(item['status'])} | "
                f"{_one_line(', '.join(item['evidence']))} |"
            )
        lines.extend(
            [
                "",
                "# Tasks",
                "",
                "| ID | Type | Task | Targets | Depends On | Status | Last Attempt |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for task in state["tasks"]:
            lines.append(
                f"| {_one_line(task['id'])} | {_one_line(task['type'])} | "
                f"{_one_line(task['body'])} | {_one_line(', '.join(task['targets']))} | "
                f"{_one_line(', '.join(task['depends_on']))} | "
                f"{_one_line(task['status'])} | {_one_line(task['last_attempt'])} |"
            )
        lines.extend(["", "# Attention", ""])
        lines.extend(
            [f"- {item}" for item in state["attention"]]
            or ["No open attention items."]
        )
        lines.extend(
            [
                "",
                "# Current State",
                "",
                state["current_state"],
                "",
                "# Next Action",
                "",
                state["next_action"],
                "",
                "# Closeout",
                "",
                state["closeout"],
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_mission(text: str) -> Dict[str, Any]:
        frontmatter = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n", text)
        if not frontmatter:
            raise ValueError("Mission frontmatter is missing")
        meta = yaml.safe_load(frontmatter.group(1)) or {}
        brief = _section(text, "Mission Brief")
        contract_rows = _parse_table(_section(text, "Contract"))
        task_rows = _parse_table(_section(text, "Tasks"))
        attention_section = _section(text, "Attention")
        attention = _parse_list(attention_section)
        return {
            "meta": meta,
            "original_request": _section(brief, "Original Request", level=2),
            "scope": _section(brief, "Scope", level=2),
            "non_goals": _section(brief, "Non-goals", level=2),
            "contract": [
                {
                    "id": row[0],
                    "assertion": row[1],
                    "status": row[2],
                    "evidence": [
                        value.strip()
                        for value in row[3].split(",")
                        if value.strip()
                    ],
                }
                for row in contract_rows
                if len(row) >= 4
            ],
            "tasks": [
                {
                    "id": row[0],
                    "type": row[1],
                    "body": row[2],
                    "targets": [
                        value.strip() for value in row[3].split(",") if value.strip()
                    ],
                    "depends_on": [
                        value.strip() for value in row[4].split(",") if value.strip()
                    ],
                    "status": row[5],
                    "last_attempt": row[6],
                }
                for row in task_rows
                if len(row) >= 7
            ],
            "attention": attention,
            "current_state": _section(text, "Current State"),
            "next_action": _section(text, "Next Action"),
            "closeout": _section(text, "Closeout"),
        }


def mission_state_path_error(path: str | Path, workspace_dir: str | Path) -> Optional[str]:
    """Return an error when an Agent tries to mutate protected Mission state."""
    target = Path(path).expanduser().resolve(strict=False)
    root = (
        Path(workspace_dir).expanduser().resolve(strict=False)
        / ".flocks"
        / "memory"
        / "missions"
    ).resolve(strict=False)
    if target == root or root in target.parents:
        return (
            "Mission state is protected. Use todo for task state and "
            "mission_record for progress, findings, validation, and artifacts."
        )
    return None


async def agent_mission_mutation_error(
    path: str | Path,
    session_id: str,
) -> Optional[str]:
    """Resolve the session workspace and enforce protected Mission paths."""
    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if session is None:
        return None
    return mission_state_path_error(path, session.directory)
