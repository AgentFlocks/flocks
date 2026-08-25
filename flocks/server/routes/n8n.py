"""Product-facing n8n integration API."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from flocks.integrations.n8n import (
    N8nClient,
    N8nClientError,
    N8nConfig,
    N8nIR,
    lint_workflow,
    render_ir_to_workflow,
    run_webhook_tests,
    workflow_to_api_create_payload,
)
from flocks.integrations.n8n.cleanup import cleanup_workflows
from flocks.integrations.n8n.credentials import attach_credential_ids_to_workflow, ensure_n8n_credentials
from flocks.integrations.n8n.renderer import slugify_webhook_path
from flocks.integrations.n8n.secrets import (
    delete_n8n_api_key,
    redact_secret,
    resolve_n8n_api_key,
    store_n8n_api_key,
)
from flocks.integrations.n8n.state import (
    DEFAULT_N8N_BASE_URL,
    DEFAULT_N8N_SECRET_REF,
    N8nBuildRunState,
    N8nConnectionState,
    N8nStateStore,
    N8nWorkflowRecord,
    get_n8n_output_dir,
    utc_now_iso,
)
from flocks.server.auth import require_admin


router = APIRouter()


class N8nConnectionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="baseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    api_key: Optional[str] = Field(None, alias="apiKey")
    clear_api_key: bool = Field(False, alias="clearApiKey")
    is_default: bool = Field(False, alias="isDefault")


class N8nConnectionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    base_url: str = Field(alias="baseUrl")
    api_key_secret_ref: str = Field(alias="apiKeySecretRef")
    is_default: bool = Field(False, alias="isDefault")
    status: str = "unknown"
    api_key_configured: bool = Field(alias="apiKeyConfigured")
    api_key_masked: Optional[str] = Field(None, alias="apiKeyMasked")
    updated_at: Optional[str] = Field(None, alias="updatedAt")
    last_health_status: Optional[str] = Field(None, alias="lastHealthStatus")
    last_health_error: Optional[str] = Field(None, alias="lastHealthError")
    last_checked_at: Optional[str] = Field(None, alias="lastCheckedAt")


class N8nHealthCheckRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: Optional[str] = Field(None, alias="connectionId")
    base_url: Optional[str] = Field(None, alias="baseUrl")
    api_key_secret_ref: Optional[str] = Field(None, alias="apiKeySecretRef")


class N8nBuildRunCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: Optional[str] = Field(None, alias="connectionId")
    user_request: str = Field("", alias="userRequest")
    ir: Dict[str, Any]
    base_url: Optional[str] = Field(None, alias="baseUrl")
    api_key_secret_ref: Optional[str] = Field(None, alias="apiKeySecretRef")
    publish: bool = True
    activate: bool = True
    cleanup_on_success: bool = Field(False, alias="cleanupOnSuccess")
    wait_for_execution: bool = Field(True, alias="waitForExecution")


class N8nWorkflowRecordCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    connection_id: Optional[str] = Field(None, alias="connectionId")
    source: str = "manual"
    ownership: str = "managed"
    n8n_workflow_id: str = Field(alias="n8nWorkflowId")
    n8n_base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="n8nBaseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    workflow_url: Optional[str] = Field(None, alias="workflowUrl")
    trigger_type: str = Field("webhook", alias="triggerType")
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    webhook_path: Optional[str] = Field(None, alias="webhookPath")
    webhook_method: Optional[str] = Field(None, alias="webhookMethod")
    kafka_topic: Optional[str] = Field(None, alias="kafkaTopic")
    kafka_group_id: Optional[str] = Field(None, alias="kafkaGroupId")
    kafka_credential_name: Optional[str] = Field(None, alias="kafkaCredentialName")
    user_request: Optional[str] = Field(None, alias="userRequest")
    ir: Optional[Dict[str, Any]] = None
    workflow_json: Optional[Dict[str, Any]] = Field(None, alias="workflowJson")
    test_cases: List[Dict[str, Any]] = Field(default_factory=list, alias="testCases")


class N8nWorkflowRecordUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: Optional[str] = None
    trigger_type: Optional[str] = Field(None, alias="triggerType")
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    webhook_path: Optional[str] = Field(None, alias="webhookPath")
    webhook_method: Optional[str] = Field(None, alias="webhookMethod")
    kafka_topic: Optional[str] = Field(None, alias="kafkaTopic")
    kafka_group_id: Optional[str] = Field(None, alias="kafkaGroupId")
    kafka_credential_name: Optional[str] = Field(None, alias="kafkaCredentialName")
    test_cases: Optional[List[Dict[str, Any]]] = Field(None, alias="testCases")


class N8nWorkflowDiscoverRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: Optional[str] = Field(None, alias="connectionId")
    base_url: Optional[str] = Field(None, alias="baseUrl")
    api_key_secret_ref: Optional[str] = Field(None, alias="apiKeySecretRef")
    prefix: str = "flocks-"
    include_all: bool = Field(False, alias="includeAll")


class N8nSyncRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_ids: Optional[List[str]] = Field(None, alias="connectionIds")
    include_external: bool = Field(False, alias="includeExternal")
    prefix: str = "flocks-"


class N8nWorkflowRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    payload: Any = Field(default_factory=dict)
    headers: Dict[str, str] = Field(default_factory=dict)
    wait_for_execution: bool = Field(True, alias="waitForExecution")


def _store() -> N8nStateStore:
    return N8nStateStore()


def _connection_response(state: N8nConnectionState) -> N8nConnectionResponse:
    key = resolve_n8n_api_key(secret_ref=state.api_key_secret_ref)
    return N8nConnectionResponse(
        id=state.id,
        name=state.name,
        baseUrl=state.base_url,
        apiKeySecretRef=state.api_key_secret_ref,
        isDefault=state.is_default,
        status=state.status,
        apiKeyConfigured=bool(key),
        apiKeyMasked=redact_secret(key),
        updatedAt=state.updated_at,
        lastHealthStatus=state.last_health_status,
        lastHealthError=state.last_health_error,
        lastCheckedAt=state.last_checked_at,
    )


def _client_for(base_url: str, secret_ref: str) -> N8nClient:
    key = resolve_n8n_api_key(secret_ref=secret_ref)
    if not key:
        raise N8nClientError(f"n8n API key is not configured for secret ref {secret_ref!r}")
    return N8nClient(N8nConfig(base_url=base_url, api_key=key))


def _client_for_connection(connection: N8nConnectionState) -> N8nClient:
    return _client_for(connection.base_url, connection.api_key_secret_ref)


def _record_id_for(workflow_id: str, connection_id: str = "default") -> str:
    clean_connection = "".join(ch for ch in str(connection_id or "default") if ch.isalnum() or ch in {"-", "_"})
    clean_workflow = "".join(ch for ch in str(workflow_id) if ch.isalnum() or ch in {"-", "_"})
    return f"n8n-{clean_connection}-{clean_workflow}"


def _load_connection_or_404(store: N8nStateStore, connection_id: Optional[str]) -> N8nConnectionState:
    if not connection_id:
        return store.load_connection()
    connection = store.load_connection_by_id(connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n connection not found")
    return connection


def _legacy_record_id_for(workflow_id: str) -> str:
    return f"n8n-{workflow_id}"


def _load_existing_record_for_workflow(
    store: N8nStateStore,
    workflow_id: str,
    connection: N8nConnectionState,
) -> Optional[N8nWorkflowRecord]:
    scoped = store.load_record(_record_id_for(workflow_id, connection.id))
    if scoped:
        return scoped
    legacy = store.load_record(_legacy_record_id_for(workflow_id))
    if not legacy:
        return None
    legacy_base_url = (legacy.n8n_base_url or "").rstrip("/")
    connection_base_url = (connection.base_url or "").rstrip("/")
    if legacy.connection_id == connection.id or (legacy.connection_id == "default" and connection.id == "default"):
        return legacy
    if connection.id == "default" and legacy_base_url == connection_base_url:
        return legacy
    return None


def _remote_status_from_workflow(workflow: Dict[str, Any]) -> str:
    active = workflow.get("active")
    if active is True:
        return "active"
    if active is False:
        return "inactive"
    return "unknown"


def _latest_execution_id(test_results: List[Dict[str, Any]]) -> Optional[str]:
    for result in reversed(test_results):
        execution = result.get("execution") if isinstance(result, dict) else None
        if isinstance(execution, dict) and execution.get("id") is not None:
            return str(execution["id"])
    return None


def _execution_id_from_result(result: Dict[str, Any]) -> Optional[str]:
    execution = result.get("execution") if isinstance(result, dict) else None
    if isinstance(execution, dict) and execution.get("id") is not None:
        return str(execution["id"])
    return None


def _extract_webhook_info(workflow: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None, None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        parameters = node.get("parameters")
        if not isinstance(parameters, dict):
            return None, None
        path = parameters.get("path")
        method = parameters.get("httpMethod") or parameters.get("method")
        return str(path) if path else None, str(method).upper() if method else None
    return None, None


def _credential_name(credentials: Any, key: str) -> Optional[str]:
    if not isinstance(credentials, dict):
        return None
    value = credentials.get(key)
    if not isinstance(value, dict):
        return None
    name = value.get("name") or value.get("id")
    return str(name) if name else None


def _extract_kafka_info(workflow: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None, None, None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "n8n-nodes-base.kafkaTrigger":
            continue
        parameters = node.get("parameters")
        if not isinstance(parameters, dict):
            return None, None, None
        topic = parameters.get("topic")
        group_id = parameters.get("groupId")
        credential = _credential_name(node.get("credentials"), "kafka")
        return (
            str(topic) if topic else None,
            str(group_id) if group_id else None,
            credential,
        )
    return None, None, None


def _apply_trigger_info(record: N8nWorkflowRecord, workflow: Dict[str, Any], *, base_url: str) -> None:
    webhook_path, webhook_method = _extract_webhook_info(workflow)
    kafka_topic, kafka_group_id, kafka_credential_name = _extract_kafka_info(workflow)
    if kafka_topic or kafka_group_id:
        record.trigger_type = "kafka"
        record.webhook_path = None
        record.webhook_method = None
        record.webhook_url = None
        record.kafka_topic = kafka_topic
        record.kafka_group_id = kafka_group_id
        record.kafka_credential_name = kafka_credential_name
        return
    if not webhook_path and not webhook_method:
        return
    record.trigger_type = "webhook" if webhook_path or webhook_method else record.trigger_type
    record.webhook_path = webhook_path or record.webhook_path
    record.webhook_method = webhook_method or record.webhook_method
    if webhook_path:
        record.webhook_url = f"{base_url}/webhook/{webhook_path}"
    record.kafka_topic = None
    record.kafka_group_id = None
    record.kafka_credential_name = None


def _upsert_discovered_record(
    store: N8nStateStore,
    workflow: Dict[str, Any],
    *,
    connection: N8nConnectionState,
    source: str,
    ownership: str,
) -> Optional[N8nWorkflowRecord]:
    workflow_id = workflow.get("id")
    if workflow_id is None:
        return None
    workflow_id_text = str(workflow_id)
    record_id = _record_id_for(workflow_id_text, connection.id)
    existing = _load_existing_record_for_workflow(store, workflow_id_text, connection)
    workflow_url = f"{connection.base_url}/workflow/{workflow_id_text}"
    record = existing or N8nWorkflowRecord(
        id=record_id,
        name=str(workflow.get("name") or workflow_id_text),
        connectionId=connection.id,
        connectionName=connection.name,
        source=source,
        ownership=ownership,
        n8nWorkflowId=workflow_id_text,
        n8nBaseUrl=connection.base_url,
        apiKeySecretRef=connection.api_key_secret_ref,
        workflowUrl=workflow_url,
    )
    record.name = str(workflow.get("name") or record.name)
    record.connection_id = connection.id
    record.connection_name = connection.name
    record.source = record.source if record.source == "flocks_created" else source
    record.ownership = record.ownership if record.ownership == "managed" else ownership
    record.n8n_base_url = connection.base_url
    record.api_key_secret_ref = connection.api_key_secret_ref
    record.workflow_url = workflow_url
    record.remote_status = _remote_status_from_workflow(workflow)
    _apply_trigger_info(record, workflow, base_url=connection.base_url)
    record.last_synced_at = utc_now_iso()
    record.error = None
    return store.save_record(record)


def _promote_run_to_record(
    store: N8nStateStore,
    run: N8nBuildRunState,
    parsed_ir: N8nIR,
    *,
    activated: bool,
) -> N8nWorkflowRecord:
    if not run.n8n_workflow_id:
        raise N8nClientError("n8n workflow id is missing")
    workflow_id = run.n8n_workflow_id
    connection = store.load_connection_by_id(run.connection_id) or N8nConnectionState(
        id=run.connection_id,
        name=run.connection_id,
        baseUrl=run.base_url,
        apiKeySecretRef=run.api_key_secret_ref,
    )
    record_id = _record_id_for(workflow_id, connection.id)
    existing = _load_existing_record_for_workflow(store, workflow_id, connection)
    tests_attempted = bool(run.test_results)
    test_success = tests_attempted and all(item.get("success") for item in run.test_results)
    record = existing or N8nWorkflowRecord(
        id=record_id,
        name=parsed_ir.name,
        connectionId=connection.id,
        connectionName=connection.name,
        source="flocks_created",
        ownership="managed",
        n8nWorkflowId=workflow_id,
        n8nBaseUrl=run.base_url,
        apiKeySecretRef=run.api_key_secret_ref,
        workflowUrl=run.workflow_url or f"{run.base_url}/workflow/{workflow_id}",
    )
    record.name = parsed_ir.name
    record.connection_id = connection.id
    record.connection_name = connection.name
    record.source = "flocks_created"
    record.ownership = "managed"
    record.n8n_base_url = run.base_url
    record.api_key_secret_ref = run.api_key_secret_ref
    record.workflow_url = run.workflow_url or f"{run.base_url}/workflow/{workflow_id}"
    record.trigger_type = parsed_ir.trigger.type
    if parsed_ir.trigger.type == "webhook":
        record.webhook_url = run.webhook_url
        record.webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
        record.webhook_method = parsed_ir.trigger.method
        record.kafka_topic = None
        record.kafka_group_id = None
        record.kafka_credential_name = None
    else:
        record.webhook_url = None
        record.webhook_path = None
        record.webhook_method = None
        record.kafka_topic = parsed_ir.trigger.topic
        record.kafka_group_id = parsed_ir.trigger.group_id
        credential_ref = parsed_ir.trigger.credential_ref
        record.kafka_credential_name = credential_ref.name if credential_ref else None
    record.remote_status = "active" if activated else "inactive"
    record.test_status = "test_passed" if test_success else ("test_failed" if tests_attempted else "not_tested")
    record.build_status = "success" if run.status in {"test_passed", "rendered", "published"} else run.status
    record.user_request = run.user_request
    record.ir = run.ir
    record.workflow_json = run.workflow
    record.lint_issues = run.lint_issues
    record.test_cases = [case.model_dump(by_alias=True) for case in parsed_ir.tests]
    record.test_results = run.test_results
    record.latest_build_run_id = run.run_id
    record.latest_execution_id = _latest_execution_id(run.test_results)
    record.workflow_json_path = run.workflow_json_path
    record.report_path = run.report_path
    record.last_tested_at = utc_now_iso() if tests_attempted else record.last_tested_at
    record.error = run.error
    saved = store.save_record(record)
    run.record_id = saved.id
    store.save_run(run)
    return saved


@router.get("/connection", response_model=N8nConnectionResponse)
async def get_connection(_admin: object = Depends(require_admin)):
    return _connection_response(_store().load_connection())


@router.put("/connection", response_model=N8nConnectionResponse)
async def update_connection(
    request: N8nConnectionUpdate,
    _admin: object = Depends(require_admin),
):
    store = _store()
    state = N8nConnectionState(
        id="default",
        name=request.name or "Default n8n",
        baseUrl=request.base_url.strip().rstrip("/") or DEFAULT_N8N_BASE_URL,
        apiKeySecretRef=request.api_key_secret_ref.strip() or DEFAULT_N8N_SECRET_REF,
        isDefault=True,
    )
    if request.clear_api_key:
        delete_n8n_api_key(state.api_key_secret_ref)
    if request.api_key and request.api_key.strip():
        store_n8n_api_key(state.api_key_secret_ref, request.api_key)
    store.save_legacy_connection(state)
    return _connection_response(state)


@router.get("/connections", response_model=List[N8nConnectionResponse])
async def list_connections(_admin: object = Depends(require_admin)):
    store = _store()
    return [_connection_response(connection) for connection in store.list_connections() or [store.load_connection()]]


@router.post("/connections", response_model=N8nConnectionResponse)
async def create_connection(
    request: N8nConnectionUpdate,
    _admin: object = Depends(require_admin),
):
    store = _store()
    state = N8nConnectionState(
        id=store.new_connection_id(),
        name=(request.name or "n8n").strip() or "n8n",
        baseUrl=request.base_url,
        apiKeySecretRef=request.api_key_secret_ref,
        isDefault=request.is_default,
    )
    if request.api_key and request.api_key.strip():
        store_n8n_api_key(state.api_key_secret_ref, request.api_key)
    return _connection_response(store.save_connection(state))


@router.put("/connections/{connection_id}", response_model=N8nConnectionResponse)
async def update_connection_by_id(
    connection_id: str,
    request: N8nConnectionUpdate,
    _admin: object = Depends(require_admin),
):
    store = _store()
    existing = store.load_connection_by_id(connection_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n connection not found")
    old_secret_ref = existing.api_key_secret_ref
    existing.name = (request.name or existing.name or "n8n").strip() or "n8n"
    existing.base_url = request.base_url
    existing.api_key_secret_ref = request.api_key_secret_ref
    existing.is_default = request.is_default or existing.is_default
    if request.clear_api_key:
        delete_n8n_api_key(old_secret_ref)
        if existing.api_key_secret_ref != old_secret_ref:
            delete_n8n_api_key(existing.api_key_secret_ref)
    if request.api_key and request.api_key.strip():
        store_n8n_api_key(existing.api_key_secret_ref, request.api_key)
    return _connection_response(store.save_connection(existing))


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    force: bool = Query(False),
    _admin: object = Depends(require_admin),
):
    store = _store()
    records = [record for record in store.list_records(limit=1000) if record.connection_id == connection_id]
    if records and not force:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection still has workflow records")
    deleted = store.delete_connection(connection_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n connection not found")
    if records:
        for record in records:
            record.remote_status = "connection_missing"
            record.error = "n8n connection was deleted"
            store.save_record(record)
    return {"success": True}


@router.post("/connections/{connection_id}/check")
async def health_check_connection(
    connection_id: str,
    _admin: object = Depends(require_admin),
):
    return await health_check(N8nHealthCheckRequest(connectionId=connection_id), _admin)


@router.post("/health-check")
async def health_check(
    request: Optional[N8nHealthCheckRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    state = _load_connection_or_404(store, request.connection_id if request else None)
    base_url = (request.base_url if request and request.base_url else state.base_url).rstrip("/")
    secret_ref = request.api_key_secret_ref if request and request.api_key_secret_ref else state.api_key_secret_ref
    try:
        health_result = await asyncio.to_thread(N8nClient(N8nConfig(base_url=base_url)).health_check)
        key = resolve_n8n_api_key(secret_ref=secret_ref)
        if not key:
            raise N8nClientError(f"n8n API key is not configured for secret ref {secret_ref!r}")
        api_result = await asyncio.to_thread(
            N8nClient(N8nConfig(base_url=base_url, api_key=key)).list_workflows,
            limit=1,
        )
        state.base_url = base_url
        state.api_key_secret_ref = secret_ref
        state.last_health_status = "ok"
        state.status = "healthy"
        state.last_health_error = None
        state.last_checked_at = utc_now_iso()
        store.save_connection(state)
        return {
            "success": True,
            "connection": _connection_response(state).model_dump(by_alias=True),
            "result": {
                "health": health_result,
                "api": {"status": api_result.get("status")},
            },
        }
    except Exception as exc:
        state.base_url = base_url
        state.api_key_secret_ref = secret_ref
        state.last_health_status = "error"
        state.status = "unhealthy"
        state.last_health_error = str(exc)
        state.last_checked_at = utc_now_iso()
        store.save_connection(state)
        return {"success": False, "connection": _connection_response(state).model_dump(by_alias=True), "error": str(exc)}


def _write_run_artifacts(run: N8nBuildRunState) -> None:
    output_dir = get_n8n_output_dir() / run.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    if run.workflow is not None:
        workflow_path = output_dir / "workflow.json"
        run.workflow_json_path = str(workflow_path)
        workflow_path.write_text(json.dumps(run.workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = output_dir / "report.json"
    run.report_path = str(report_path)
    report_path.write_text(json.dumps(run.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _build_run_sync(request: N8nBuildRunCreateRequest) -> N8nBuildRunState:
    store = _store()
    connection = _load_connection_or_404(store, request.connection_id)
    base_url = (request.base_url or connection.base_url or DEFAULT_N8N_BASE_URL).rstrip("/")
    secret_ref = request.api_key_secret_ref or connection.api_key_secret_ref or DEFAULT_N8N_SECRET_REF
    run = N8nBuildRunState(
        runId=store.new_run_id(),
        connectionId=connection.id,
        status="running",
        currentStep="render",
        userRequest=request.user_request,
        baseUrl=base_url,
        apiKeySecretRef=secret_ref,
        ir=request.ir,
    )
    store.save_run(run)

    try:
        parsed_ir = N8nIR.model_validate(request.ir)
        workflow = render_ir_to_workflow(parsed_ir)
        run.workflow = workflow
        run.current_step = "lint"
        is_webhook_trigger = parsed_ir.trigger.type == "webhook"
        run.lint_issues = [
            issue.to_dict()
            for issue in lint_workflow(
                workflow,
                require_tests=is_webhook_trigger,
                tests=[case.model_dump(by_alias=True) for case in parsed_ir.tests],
            )
        ]
        _write_run_artifacts(run)
        store.save_run(run)
        if any(issue["severity"] == "error" for issue in run.lint_issues):
            run.status = "lint_failed"
            run.current_step = "lint"
            store.save_run(run)
            return run

        if not request.publish:
            run.status = "rendered"
            run.current_step = "complete"
            _write_run_artifacts(run)
            store.save_run(run)
            return run

        client = _client_for(base_url, secret_ref)

        if parsed_ir.credential_requirements:
            run.current_step = "credentials"
            store.save_run(run)
            run.credential_results = ensure_n8n_credentials(client, parsed_ir.credential_requirements)
            workflow = attach_credential_ids_to_workflow(workflow, run.credential_results)
            run.workflow = workflow
            _write_run_artifacts(run)
            store.save_run(run)

        run.current_step = "publish"
        store.save_run(run)
        created_response = client.create_workflow(workflow_to_api_create_payload(workflow))
        created = created_response.get("body") if isinstance(created_response.get("body"), dict) else {}
        workflow_id = str(created.get("id") or "")
        if not workflow_id:
            raise N8nClientError("n8n create response did not contain workflow id")
        run.n8n_workflow_id = workflow_id
        run.workflow_url = f"{base_url}/workflow/{workflow_id}"

        if request.activate:
            run.current_step = "activate"
            store.save_run(run)
            client.activate_workflow(workflow_id)

        webhook_path = None
        if is_webhook_trigger:
            webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
            run.webhook_url = f"{base_url}/webhook/{webhook_path}"
        if webhook_path and parsed_ir.tests and request.activate:
            run.current_step = "test"
            store.save_run(run)
            run.test_results = [
                result.to_dict()
                for result in run_webhook_tests(
                    client,
                    webhook_path=webhook_path,
                    tests=parsed_ir.tests,
                    method=parsed_ir.trigger.method,
                    workflow_id=workflow_id,
                    wait_for_execution=request.wait_for_execution,
                )
            ]
        tests_attempted = bool(run.test_results)
        success = all(item.get("success") for item in run.test_results) if tests_attempted else True
        run.status = ("test_passed" if success else "test_failed") if tests_attempted else "published"
        if success and request.cleanup_on_success:
            run.current_step = "cleanup"
            run.cleanup = cleanup_workflows(client, [workflow_id])
            if run.cleanup and all(item.get("success") for item in run.cleanup):
                run.status = "cleaned"
        else:
            _promote_run_to_record(store, run, parsed_ir, activated=request.activate)
        run.current_step = "complete"
        _write_run_artifacts(run)
        store.save_run(run)
        return run
    except Exception as exc:
        run.status = "failed"
        if isinstance(exc, N8nClientError) and exc.body:
            run.error = f"{exc}; body={exc.body[:1000]}"
        else:
            run.error = str(exc)
        _write_run_artifacts(run)
        store.save_run(run)
        return run


@router.post("/build-runs", response_model=N8nBuildRunState)
async def create_build_run(
    request: N8nBuildRunCreateRequest,
    _admin: object = Depends(require_admin),
):
    return await asyncio.to_thread(_build_run_sync, request)


@router.get("/build-runs", response_model=List[N8nBuildRunState])
async def list_build_runs(
    limit: int = Query(20, ge=1, le=100),
    _admin: object = Depends(require_admin),
):
    return _store().list_runs(limit=limit)


@router.get("/workflows", response_model=List[N8nWorkflowRecord])
async def list_n8n_workflows(
    limit: int = Query(100, ge=1, le=500),
    connection_id: Optional[str] = Query(None, alias="connectionId"),
    source: Optional[str] = None,
    _admin: object = Depends(require_admin),
):
    records = _store().list_records(limit=limit)
    if connection_id:
        records = [record for record in records if record.connection_id == connection_id]
    if source:
        records = [record for record in records if record.source == source]
    return records


@router.post("/workflows", response_model=N8nWorkflowRecord)
async def create_n8n_workflow_record(
    request: N8nWorkflowRecordCreateRequest,
    _admin: object = Depends(require_admin),
):
    store = _store()
    connection = _load_connection_or_404(store, request.connection_id)
    base_url = (request.n8n_base_url or connection.base_url).rstrip("/") or DEFAULT_N8N_BASE_URL
    workflow_url = request.workflow_url or f"{base_url}/workflow/{request.n8n_workflow_id}"
    record = N8nWorkflowRecord(
        id=_record_id_for(request.n8n_workflow_id, connection.id),
        name=request.name.strip() or request.n8n_workflow_id,
        connectionId=connection.id,
        connectionName=connection.name,
        source=request.source,
        ownership=request.ownership,
        n8nWorkflowId=request.n8n_workflow_id,
        n8nBaseUrl=base_url,
        apiKeySecretRef=request.api_key_secret_ref,
        workflowUrl=workflow_url,
        triggerType=request.trigger_type,
        webhookUrl=request.webhook_url,
        webhookPath=request.webhook_path,
        webhookMethod=request.webhook_method,
        kafkaTopic=request.kafka_topic,
        kafkaGroupId=request.kafka_group_id,
        kafkaCredentialName=request.kafka_credential_name,
        userRequest=request.user_request,
        ir=request.ir,
        workflowJson=request.workflow_json,
        testCases=request.test_cases,
    )
    return store.save_record(record)


async def _sync_connection(
    store: N8nStateStore,
    connection: N8nConnectionState,
    *,
    prefix: str = "flocks-",
    include_external: bool = False,
) -> Dict[str, Any]:
    client = _client_for_connection(connection)
    seen: set[str] = set()
    created = 0
    updated = 0
    external = 0
    cursor: Optional[str] = None
    truncated = False
    for _ in range(20):
        response = await asyncio.to_thread(client.list_workflows, limit=100, cursor=cursor)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        rows = body.get("data") if isinstance(body, dict) else []
        if not isinstance(rows, list):
            cursor = None
            break
        for workflow in rows:
            if not isinstance(workflow, dict):
                continue
            workflow_id = workflow.get("id")
            if workflow_id is None:
                continue
            workflow_id_text = str(workflow_id)
            seen.add(workflow_id_text)
            existing = _load_existing_record_for_workflow(store, workflow_id_text, connection)
            name = str(workflow.get("name") or "")
            should_import = name.startswith(prefix) or include_external or (
                existing is not None and existing.source != "external"
            )
            if not should_import:
                continue
            source = existing.source if existing else ("discovered" if name.startswith(prefix) else "external")
            ownership = existing.ownership if existing else ("readonly" if source == "external" else "readonly")
            if source == "flocks_created":
                ownership = "managed"
            saved = _upsert_discovered_record(
                store,
                workflow,
                connection=connection,
                source=source,
                ownership=ownership,
            )
            if not saved:
                continue
            if existing:
                updated += 1
            else:
                created += 1
            if saved.source == "external":
                external += 1
        cursor = body.get("nextCursor") if isinstance(body.get("nextCursor"), str) else None
        if not cursor:
            break
    if cursor:
        truncated = True

    missing = 0
    if not truncated:
        for record in store.list_records(limit=1000):
            if record.connection_id != connection.id or not record.n8n_workflow_id:
                continue
            if not include_external and record.source == "external":
                continue
            if record.n8n_workflow_id not in seen and record.remote_status not in {"cleaned", "connection_missing"}:
                record.remote_status = "missing_remote"
                record.last_synced_at = utc_now_iso()
                store.save_record(record)
                missing += 1
    connection.status = "healthy"
    connection.last_health_status = "ok"
    connection.last_health_error = None
    connection.last_checked_at = utc_now_iso()
    store.save_connection(connection)
    return {
        "connectionId": connection.id,
        "connectionName": connection.name,
        "success": True,
        "created": created,
        "updated": updated,
        "missing": missing,
        "external": external,
        "truncated": truncated,
    }


@router.post("/sync")
async def sync_n8n_workflows(
    request: Optional[N8nSyncRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    requested_ids = set(request.connection_ids or []) if request and request.connection_ids else None
    connections = store.list_connections() or [store.load_connection()]
    if requested_ids is not None:
        connections = [connection for connection in connections if connection.id in requested_ids]
    report: Dict[str, Any] = {
        "status": "completed",
        "connectionsTotal": len(connections),
        "connectionsSuccess": 0,
        "connectionsFailed": 0,
        "created": 0,
        "updated": 0,
        "missing": 0,
        "external": 0,
        "errors": [],
        "connections": [],
    }
    for connection in connections:
        try:
            item = await _sync_connection(
                store,
                connection,
                prefix=(request.prefix if request else "flocks-") or "flocks-",
                include_external=bool(request.include_external) if request else False,
            )
            report["connectionsSuccess"] += 1
            for key in ("created", "updated", "missing", "external"):
                report[key] += int(item.get(key) or 0)
            report["connections"].append(item)
        except Exception as exc:
            connection.status = "unhealthy"
            connection.last_health_status = "error"
            connection.last_health_error = str(exc)
            connection.last_checked_at = utc_now_iso()
            store.save_connection(connection)
            report["connectionsFailed"] += 1
            error = {"connectionId": connection.id, "connectionName": connection.name, "error": str(exc)}
            report["errors"].append(error)
            report["connections"].append({**error, "success": False})
    if report["connectionsFailed"]:
        report["status"] = "partial" if report["connectionsSuccess"] else "failed"
    report["records"] = [record.model_dump(by_alias=True) for record in store.list_records(limit=500)]
    return report


@router.post("/workflows/discover", response_model=List[N8nWorkflowRecord])
async def discover_n8n_workflow_records(
    request: Optional[N8nWorkflowDiscoverRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    connection = _load_connection_or_404(store, request.connection_id if request else None)
    if request and (request.base_url or request.api_key_secret_ref):
        connection.base_url = (request.base_url or connection.base_url or DEFAULT_N8N_BASE_URL).rstrip("/")
        connection.api_key_secret_ref = (request.api_key_secret_ref or connection.api_key_secret_ref) or DEFAULT_N8N_SECRET_REF
    prefix = (request.prefix if request else "flocks-") or "flocks-"
    include_all = bool(request.include_all) if request else False
    await _sync_connection(store, connection, prefix=prefix, include_external=include_all)
    return store.list_records(limit=500)


@router.get("/workflows/{record_id}", response_model=N8nWorkflowRecord)
async def get_n8n_workflow_record(record_id: str, _admin: object = Depends(require_admin)):
    record = _store().load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    return record


@router.patch("/workflows/{record_id}", response_model=N8nWorkflowRecord)
async def update_n8n_workflow_record(
    record_id: str,
    request: N8nWorkflowRecordUpdateRequest,
    _admin: object = Depends(require_admin),
):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    updates = request.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(record, key, value)
    return store.save_record(record)


@router.delete("/workflows/{record_id}")
async def delete_n8n_workflow_record(
    record_id: str,
    delete_remote: bool = Query(False, alias="deleteRemote"),
    _admin: object = Depends(require_admin),
):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    remote_cleanup: List[Dict[str, Any]] = []
    if delete_remote:
        connection = store.load_connection_by_id(record.connection_id)
        if not connection:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
        remote_cleanup = await asyncio.to_thread(
            cleanup_workflows,
            _client_for_connection(connection),
            [record.n8n_workflow_id],
        )
        if not all(item.get("success") or item.get("status") == 404 for item in remote_cleanup):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"message": "failed to delete remote n8n workflow", "remoteCleanup": remote_cleanup},
            )
    deleted = store.delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    return {"success": True, "remoteCleanup": remote_cleanup}


@router.post("/workflows/{record_id}/sync", response_model=N8nWorkflowRecord)
async def sync_n8n_workflow_record(record_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    connection = store.load_connection_by_id(record.connection_id)
    if not connection:
        record.remote_status = "connection_missing"
        record.error = "n8n connection is missing"
        record.last_synced_at = utc_now_iso()
        return store.save_record(record)
    try:
        workflow = await asyncio.to_thread(
            _client_for_connection(connection).get_workflow,
            record.n8n_workflow_id,
        )
        body = workflow.get("body") if isinstance(workflow.get("body"), dict) else {}
        record.connection_name = connection.name
        record.n8n_base_url = connection.base_url
        record.api_key_secret_ref = connection.api_key_secret_ref
        record.workflow_url = f"{connection.base_url}/workflow/{record.n8n_workflow_id}"
        record.remote_status = _remote_status_from_workflow(body)
        _apply_trigger_info(record, body, base_url=connection.base_url)
        record.last_synced_at = utc_now_iso()
        record.error = None
    except N8nClientError as exc:
        if exc.status == 404:
            record.remote_status = "missing"
        elif exc.status in {401, 403}:
            record.remote_status = "auth_error"
        else:
            record.remote_status = "sync_error"
        record.last_synced_at = utc_now_iso()
        record.error = str(exc)
    return store.save_record(record)


@router.post("/workflows/{record_id}/open-event")
async def open_n8n_workflow(record_id: str, _admin: object = Depends(require_admin)):
    record = _store().load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    return {"url": record.workflow_url}


@router.post("/workflows/{record_id}/retry-test", response_model=N8nWorkflowRecord)
async def retry_n8n_workflow_tests(record_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    if record.ownership == "readonly" or record.source == "external":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="readonly n8n workflow records cannot be re-tested")
    connection = store.load_connection_by_id(record.connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
    if record.trigger_type != "webhook":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only webhook n8n workflows can be re-tested from Flocks")
    if not record.webhook_path and not record.webhook_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n webhook path is missing")
    if not record.test_cases:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n test cases are missing")

    method = record.webhook_method or "POST"
    webhook_path = record.webhook_path
    if not webhook_path and record.webhook_url:
        webhook_path = record.webhook_url.rstrip("/").split("/webhook/")[-1]
    assert webhook_path is not None
    client = _client_for_connection(connection)
    try:
        record.test_results = [
            result.to_dict()
            for result in await asyncio.to_thread(
                run_webhook_tests,
                client,
                webhook_path=webhook_path,
                tests=record.test_cases,
                method=method,
                workflow_id=record.n8n_workflow_id,
                wait_for_execution=True,
            )
        ]
        record.test_status = "test_passed" if all(item.get("success") for item in record.test_results) else "test_failed"
        record.latest_execution_id = _latest_execution_id(record.test_results)
        record.last_tested_at = utc_now_iso()
        record.error = None
    except Exception as exc:
        record.test_status = "test_error"
        record.last_tested_at = utc_now_iso()
        record.error = str(exc)
    return store.save_record(record)


@router.post("/workflows/{record_id}/run", response_model=N8nWorkflowRecord)
async def run_n8n_workflow(
    record_id: str,
    request: Optional[N8nWorkflowRunRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    if record.source == "external":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="external n8n workflow records cannot be run from Flocks")
    connection = store.load_connection_by_id(record.connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
    if record.trigger_type != "webhook":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only webhook n8n workflows can be run from Flocks")
    if not record.webhook_path and not record.webhook_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n webhook path is missing")

    method = record.webhook_method or "POST"
    webhook_path = record.webhook_path
    if not webhook_path and record.webhook_url:
        webhook_path = record.webhook_url.rstrip("/").split("/webhook/")[-1]
    assert webhook_path is not None
    run_request = request or N8nWorkflowRunRequest()
    client = _client_for_connection(connection)
    started_epoch = time.time()
    started_at = asyncio.get_running_loop().time()
    started_wall = utc_now_iso()
    try:
        response = await asyncio.to_thread(
            client.call_webhook,
            webhook_path,
            method=method,
            payload=run_request.payload,
            headers=run_request.headers,
        )
        duration_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
        execution = None
        execution_error = None
        if run_request.wait_for_execution:
            try:
                execution = await asyncio.to_thread(
                    client.wait_for_recent_execution,
                    workflow_id=record.n8n_workflow_id,
                    since_epoch_s=started_epoch,
                )
            except N8nClientError as exc:
                execution_error = str(exc)
        result = {
            "success": 200 <= int(response.get("status") or 0) < 400,
            "status": response.get("status"),
            "method": method,
            "webhookPath": webhook_path,
            "startedAt": started_wall,
            "durationMs": duration_ms,
            "responseBodyStored": False,
            "execution": execution,
        }
        if execution_error:
            result["executionLookupError"] = execution_error
        record.latest_run_result = result
        record.latest_execution_id = _execution_id_from_result(result) or record.latest_execution_id
        record.last_run_at = utc_now_iso()
        record.error = None if result["success"] else f"n8n webhook returned HTTP {response.get('status')}"
    except N8nClientError as exc:
        duration_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
        record.latest_run_result = {
            "success": False,
            "status": exc.status,
            "method": method,
            "webhookPath": webhook_path,
            "startedAt": started_wall,
            "durationMs": duration_ms,
            "responseBodyStored": False,
            "error": str(exc),
        }
        record.error = str(exc)
        record.last_run_at = utc_now_iso()
    return store.save_record(record)


@router.post("/workflows/{record_id}/cleanup", response_model=N8nWorkflowRecord)
async def cleanup_n8n_workflow(record_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    if record.ownership != "managed" or record.source not in {"flocks_created", "generated"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="only Flocks-managed n8n workflow records can be cleaned remotely")
    connection = store.load_connection_by_id(record.connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
    try:
        result = await asyncio.to_thread(
            cleanup_workflows,
            _client_for_connection(connection),
            [record.n8n_workflow_id],
        )
        if all(item.get("success") for item in result):
            record.remote_status = "cleaned"
            record.error = None
        else:
            record.remote_status = "sync_error"
            record.error = json.dumps(result, ensure_ascii=False)
        record.last_synced_at = utc_now_iso()
    except Exception as exc:
        record.remote_status = "sync_error"
        record.error = str(exc)
        record.last_synced_at = utc_now_iso()
    return store.save_record(record)


@router.get("/build-runs/{run_id}", response_model=N8nBuildRunState)
async def get_build_run(run_id: str, _admin: object = Depends(require_admin)):
    run = _store().load_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n build run not found")
    return run


@router.post("/build-runs/{run_id}/retry-test", response_model=N8nBuildRunState)
async def retry_build_run_tests(run_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n build run not found")
    if not run.n8n_workflow_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n workflow id is missing")
    connection = store.load_connection_by_id(run.connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
    parsed_ir = N8nIR.model_validate(run.ir)
    if parsed_ir.trigger.type != "webhook":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only webhook n8n build runs can be re-tested from Flocks")
    client = _client_for_connection(connection)
    webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
    run.current_step = "test"
    run.test_results = [
        result.to_dict()
        for result in await asyncio.to_thread(
            run_webhook_tests,
            client,
            webhook_path=webhook_path,
            tests=parsed_ir.tests,
            method=parsed_ir.trigger.method,
            workflow_id=run.n8n_workflow_id,
            wait_for_execution=True,
        )
    ]
    run.status = "test_passed" if all(item.get("success") for item in run.test_results) else "test_failed"
    run.current_step = "complete"
    _write_run_artifacts(run)
    return store.save_run(run)


@router.post("/build-runs/{run_id}/cleanup", response_model=N8nBuildRunState)
async def cleanup_build_run(run_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n build run not found")
    if not run.n8n_workflow_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n workflow id is missing")
    connection = store.load_connection_by_id(run.connection_id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="n8n connection is missing")
    client = _client_for_connection(connection)
    run.current_step = "cleanup"
    run.cleanup = await asyncio.to_thread(cleanup_workflows, client, [run.n8n_workflow_id])
    run.status = "cleaned" if all(item.get("success") for item in run.cleanup) else "cleanup_failed"
    run.current_step = "complete"
    _write_run_artifacts(run)
    return store.save_run(run)
