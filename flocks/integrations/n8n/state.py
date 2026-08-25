"""Local state for the n8n product integration surface."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from flocks.workspace.manager import WorkspaceManager


DEFAULT_N8N_BASE_URL = "http://localhost:5678"
DEFAULT_N8N_SECRET_REF = "N8N_API_KEY"
STATE_DIR_ENV = "FLOCKS_N8N_STATE_DIR"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_state_dir() -> Path:
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        root = Path(override).expanduser()
    else:
        root = Path.home() / ".flocks" / "integrations" / "n8n"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_n8n_output_dir(*, today: Optional[datetime] = None) -> Path:
    day = (today or datetime.now()).date()
    base = WorkspaceManager.get_instance().get_default_outputs_dir(today=day)
    target = base / "n8n"
    target.mkdir(parents=True, exist_ok=True)
    return target


class N8nConnectionState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = "default"
    name: str = "Default n8n"
    base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="baseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    is_default: bool = Field(False, alias="isDefault")
    status: str = "unknown"
    updated_at: Optional[str] = Field(None, alias="updatedAt")
    last_health_status: Optional[str] = Field(None, alias="lastHealthStatus")
    last_health_error: Optional[str] = Field(None, alias="lastHealthError")
    last_checked_at: Optional[str] = Field(None, alias="lastCheckedAt")


class N8nBuildRunState(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    run_id: str = Field(alias="runId")
    connection_id: str = Field("default", alias="connectionId")
    record_id: Optional[str] = Field(None, alias="recordId")
    status: str = "queued"
    current_step: str = Field("queued", alias="currentStep")
    user_request: str = Field("", alias="userRequest")
    base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="baseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    ir: Dict[str, Any] = Field(default_factory=dict)
    workflow: Optional[Dict[str, Any]] = None
    workflow_json_path: Optional[str] = Field(None, alias="workflowJsonPath")
    report_path: Optional[str] = Field(None, alias="reportPath")
    n8n_workflow_id: Optional[str] = Field(None, alias="n8nWorkflowId")
    workflow_url: Optional[str] = Field(None, alias="workflowUrl")
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    lint_issues: List[Dict[str, Any]] = Field(default_factory=list, alias="lintIssues")
    credential_results: List[Dict[str, Any]] = Field(default_factory=list, alias="credentialResults")
    test_results: List[Dict[str, Any]] = Field(default_factory=list, alias="testResults")
    cleanup: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now_iso, alias="updatedAt")


class N8nWorkflowRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    name: str
    engine: str = "n8n"
    connection_id: str = Field("default", alias="connectionId")
    connection_name: Optional[str] = Field(None, alias="connectionName")
    source: str = "flocks_created"
    ownership: str = "managed"
    n8n_workflow_id: str = Field(alias="n8nWorkflowId")
    n8n_base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="n8nBaseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    workflow_url: str = Field(alias="workflowUrl")
    trigger_type: str = Field("webhook", alias="triggerType")
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    webhook_path: Optional[str] = Field(None, alias="webhookPath")
    webhook_method: Optional[str] = Field(None, alias="webhookMethod")
    kafka_topic: Optional[str] = Field(None, alias="kafkaTopic")
    kafka_group_id: Optional[str] = Field(None, alias="kafkaGroupId")
    kafka_credential_name: Optional[str] = Field(None, alias="kafkaCredentialName")
    remote_status: str = Field("unknown", alias="remoteStatus")
    test_status: str = Field("not_tested", alias="testStatus")
    build_status: str = Field("not_built", alias="buildStatus")
    user_request: Optional[str] = Field(None, alias="userRequest")
    ir: Optional[Dict[str, Any]] = None
    workflow_json: Optional[Dict[str, Any]] = Field(None, alias="workflowJson")
    lint_issues: List[Dict[str, Any]] = Field(default_factory=list, alias="lintIssues")
    test_cases: List[Dict[str, Any]] = Field(default_factory=list, alias="testCases")
    test_results: List[Dict[str, Any]] = Field(default_factory=list, alias="testResults")
    latest_run_result: Optional[Dict[str, Any]] = Field(None, alias="latestRunResult")
    latest_build_run_id: Optional[str] = Field(None, alias="latestBuildRunId")
    latest_execution_id: Optional[str] = Field(None, alias="latestExecutionId")
    ir_path: Optional[str] = Field(None, alias="irPath")
    workflow_json_path: Optional[str] = Field(None, alias="workflowJsonPath")
    report_path: Optional[str] = Field(None, alias="reportPath")
    created_at: str = Field(default_factory=utc_now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now_iso, alias="updatedAt")
    last_synced_at: Optional[str] = Field(None, alias="lastSyncedAt")
    last_tested_at: Optional[str] = Field(None, alias="lastTestedAt")
    last_run_at: Optional[str] = Field(None, alias="lastRunAt")
    error: Optional[str] = None


class N8nStateStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or get_state_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "build_runs").mkdir(parents=True, exist_ok=True)
        (self.root / "connections").mkdir(parents=True, exist_ok=True)
        (self.root / "records").mkdir(parents=True, exist_ok=True)

    @property
    def connection_path(self) -> Path:
        return self.root / "connection.json"

    def connection_file_path(self, connection_id: str) -> Path:
        clean = "".join(ch for ch in str(connection_id) if ch.isalnum() or ch in {"-", "_"})
        return self.root / "connections" / f"{clean or 'default'}.json"

    def load_connection(self) -> N8nConnectionState:
        connections = self.list_connections()
        for connection in connections:
            if connection.is_default:
                return connection
        if connections:
            connections[0].is_default = True
            return self.save_connection(connections[0])
        if not self.connection_path.is_file():
            return self.save_connection(N8nConnectionState(id="default", isDefault=True))
        try:
            data = json.loads(self.connection_path.read_text(encoding="utf-8"))
            state = N8nConnectionState.model_validate(data)
            state.id = state.id or "default"
            state.name = state.name or "Default n8n"
            state.is_default = True
            return self.save_connection(state)
        except Exception:
            return self.save_connection(N8nConnectionState(id="default", isDefault=True))

    def load_connection_by_id(self, connection_id: str) -> Optional[N8nConnectionState]:
        path = self.connection_file_path(connection_id)
        if not path.is_file():
            return None
        try:
            return N8nConnectionState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_connections(self) -> List[N8nConnectionState]:
        rows: List[N8nConnectionState] = []
        for path in sorted((self.root / "connections").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                rows.append(N8nConnectionState.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        rows.sort(key=lambda item: (not item.is_default, item.name.lower(), item.id))
        return rows

    def new_connection_id(self) -> str:
        return f"conn-{uuid.uuid4().hex[:12]}"

    def save_connection(self, state: N8nConnectionState) -> N8nConnectionState:
        state.id = state.id or "default"
        state.name = state.name.strip() if state.name else "n8n"
        state.base_url = state.base_url.strip().rstrip("/") or DEFAULT_N8N_BASE_URL
        state.api_key_secret_ref = state.api_key_secret_ref.strip() or DEFAULT_N8N_SECRET_REF
        state.updated_at = utc_now_iso()
        if state.is_default:
            for other in self.list_connections():
                if other.id == state.id or not other.is_default:
                    continue
                other.is_default = False
                self.connection_file_path(other.id).write_text(
                    json.dumps(other.model_dump(by_alias=True), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        elif not self.list_connections():
            state.is_default = True
        self.connection_file_path(state.id).write_text(
            json.dumps(state.model_dump(by_alias=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if state.is_default:
            self.connection_path.write_text(
                json.dumps(state.model_dump(by_alias=True), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return state

    def delete_connection(self, connection_id: str) -> bool:
        path = self.connection_file_path(connection_id)
        if not path.is_file():
            return False
        was_default = False
        existing = self.load_connection_by_id(connection_id)
        if existing:
            was_default = existing.is_default
        path.unlink()
        if was_default:
            connections = self.list_connections()
            if connections:
                connections[0].is_default = True
                self.save_connection(connections[0])
            elif self.connection_path.is_file():
                self.connection_path.unlink()
        return True

    def save_legacy_connection(self, state: N8nConnectionState) -> N8nConnectionState:
        state.id = "default"
        state.name = state.name or "Default n8n"
        state.is_default = True
        self.connection_path.write_text(
            json.dumps(state.model_dump(by_alias=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.save_connection(state)

    def new_run_id(self) -> str:
        return uuid.uuid4().hex

    def run_path(self, run_id: str) -> Path:
        clean = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"-", "_"})
        return self.root / "build_runs" / f"{clean}.json"

    def save_run(self, run: N8nBuildRunState) -> N8nBuildRunState:
        run.updated_at = utc_now_iso()
        self.run_path(run.run_id).write_text(
            json.dumps(run.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return run

    def load_run(self, run_id: str) -> Optional[N8nBuildRunState]:
        path = self.run_path(run_id)
        if not path.is_file():
            return None
        try:
            return N8nBuildRunState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_runs(self, *, limit: int = 20) -> List[N8nBuildRunState]:
        rows: List[N8nBuildRunState] = []
        for path in sorted((self.root / "build_runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            run = self.load_run(path.stem)
            if run:
                rows.append(run)
            if len(rows) >= limit:
                break
        return rows

    def record_path(self, record_id: str) -> Path:
        clean = "".join(ch for ch in str(record_id) if ch.isalnum() or ch in {"-", "_"})
        return self.root / "records" / f"{clean}.json"

    def save_record(self, record: N8nWorkflowRecord) -> N8nWorkflowRecord:
        record.updated_at = utc_now_iso()
        self.record_path(record.id).write_text(
            json.dumps(record.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return record

    def load_record(self, record_id: str) -> Optional[N8nWorkflowRecord]:
        path = self.record_path(record_id)
        if not path.is_file():
            return None
        try:
            return N8nWorkflowRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_records(self, *, limit: int = 100) -> List[N8nWorkflowRecord]:
        rows: List[N8nWorkflowRecord] = []
        for path in sorted((self.root / "records").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            record = self.load_record(path.stem)
            if record:
                rows.append(record)
            if len(rows) >= limit:
                break
        return rows

    def delete_record(self, record_id: str) -> bool:
        path = self.record_path(record_id)
        if not path.is_file():
            return False
        path.unlink()
        return True
