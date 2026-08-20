"""Product-facing n8n integration API."""

from __future__ import annotations

import asyncio
import json
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

    base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="baseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    api_key: Optional[str] = Field(None, alias="apiKey")
    clear_api_key: bool = Field(False, alias="clearApiKey")


class N8nConnectionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field(alias="baseUrl")
    api_key_secret_ref: str = Field(alias="apiKeySecretRef")
    api_key_configured: bool = Field(alias="apiKeyConfigured")
    api_key_masked: Optional[str] = Field(None, alias="apiKeyMasked")
    updated_at: Optional[str] = Field(None, alias="updatedAt")
    last_health_status: Optional[str] = Field(None, alias="lastHealthStatus")
    last_health_error: Optional[str] = Field(None, alias="lastHealthError")
    last_checked_at: Optional[str] = Field(None, alias="lastCheckedAt")


class N8nHealthCheckRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: Optional[str] = Field(None, alias="baseUrl")
    api_key_secret_ref: Optional[str] = Field(None, alias="apiKeySecretRef")


class N8nBuildRunCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
    source: str = "manual"
    n8n_workflow_id: str = Field(alias="n8nWorkflowId")
    n8n_base_url: str = Field(DEFAULT_N8N_BASE_URL, alias="n8nBaseUrl")
    api_key_secret_ref: str = Field(DEFAULT_N8N_SECRET_REF, alias="apiKeySecretRef")
    workflow_url: Optional[str] = Field(None, alias="workflowUrl")
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    webhook_path: Optional[str] = Field(None, alias="webhookPath")
    webhook_method: Optional[str] = Field(None, alias="webhookMethod")
    user_request: Optional[str] = Field(None, alias="userRequest")
    ir: Optional[Dict[str, Any]] = None
    workflow_json: Optional[Dict[str, Any]] = Field(None, alias="workflowJson")
    test_cases: List[Dict[str, Any]] = Field(default_factory=list, alias="testCases")


class N8nWorkflowRecordUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: Optional[str] = None
    webhook_url: Optional[str] = Field(None, alias="webhookUrl")
    webhook_path: Optional[str] = Field(None, alias="webhookPath")
    webhook_method: Optional[str] = Field(None, alias="webhookMethod")
    test_cases: Optional[List[Dict[str, Any]]] = Field(None, alias="testCases")


class N8nWorkflowDiscoverRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: Optional[str] = Field(None, alias="baseUrl")
    api_key_secret_ref: Optional[str] = Field(None, alias="apiKeySecretRef")
    prefix: str = "flocks-"
    include_all: bool = Field(False, alias="includeAll")


def _store() -> N8nStateStore:
    return N8nStateStore()


def _connection_response(state: N8nConnectionState) -> N8nConnectionResponse:
    key = resolve_n8n_api_key(secret_ref=state.api_key_secret_ref)
    return N8nConnectionResponse(
        baseUrl=state.base_url,
        apiKeySecretRef=state.api_key_secret_ref,
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


def _record_id_for(workflow_id: str) -> str:
    return f"n8n-{workflow_id}"


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


def _upsert_discovered_record(
    store: N8nStateStore,
    workflow: Dict[str, Any],
    *,
    base_url: str,
    secret_ref: str,
) -> Optional[N8nWorkflowRecord]:
    workflow_id = workflow.get("id")
    if workflow_id is None:
        return None
    workflow_id_text = str(workflow_id)
    record_id = _record_id_for(workflow_id_text)
    existing = store.load_record(record_id)
    webhook_path, webhook_method = _extract_webhook_info(workflow)
    workflow_url = f"{base_url}/workflow/{workflow_id_text}"
    record = existing or N8nWorkflowRecord(
        id=record_id,
        name=str(workflow.get("name") or workflow_id_text),
        source="discovered",
        n8nWorkflowId=workflow_id_text,
        n8nBaseUrl=base_url,
        apiKeySecretRef=secret_ref,
        workflowUrl=workflow_url,
    )
    record.name = str(workflow.get("name") or record.name)
    record.n8n_base_url = base_url
    record.api_key_secret_ref = secret_ref
    record.workflow_url = workflow_url
    record.remote_status = _remote_status_from_workflow(workflow)
    record.webhook_path = webhook_path or record.webhook_path
    record.webhook_method = webhook_method or record.webhook_method
    if webhook_path:
        record.webhook_url = f"{base_url}/webhook/{webhook_path}"
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
    record_id = _record_id_for(workflow_id)
    existing = store.load_record(record_id)
    tests_attempted = bool(run.test_results)
    test_success = tests_attempted and all(item.get("success") for item in run.test_results)
    record = existing or N8nWorkflowRecord(
        id=record_id,
        name=parsed_ir.name,
        source="generated",
        n8nWorkflowId=workflow_id,
        n8nBaseUrl=run.base_url,
        apiKeySecretRef=run.api_key_secret_ref,
        workflowUrl=run.workflow_url or f"{run.base_url}/workflow/{workflow_id}",
    )
    record.name = parsed_ir.name
    record.source = "generated"
    record.n8n_base_url = run.base_url
    record.api_key_secret_ref = run.api_key_secret_ref
    record.workflow_url = run.workflow_url or f"{run.base_url}/workflow/{workflow_id}"
    record.webhook_url = run.webhook_url
    record.webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
    record.webhook_method = parsed_ir.trigger.method
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
        baseUrl=request.base_url.strip().rstrip("/") or DEFAULT_N8N_BASE_URL,
        apiKeySecretRef=request.api_key_secret_ref.strip() or DEFAULT_N8N_SECRET_REF,
    )
    if request.clear_api_key:
        delete_n8n_api_key(state.api_key_secret_ref)
    if request.api_key and request.api_key.strip():
        store_n8n_api_key(state.api_key_secret_ref, request.api_key)
    store.save_connection(state)
    return _connection_response(state)


@router.post("/health-check")
async def health_check(
    request: Optional[N8nHealthCheckRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    state = store.load_connection()
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
    connection = store.load_connection()
    base_url = (request.base_url or connection.base_url or DEFAULT_N8N_BASE_URL).rstrip("/")
    secret_ref = request.api_key_secret_ref or connection.api_key_secret_ref or DEFAULT_N8N_SECRET_REF
    run = N8nBuildRunState(
        runId=store.new_run_id(),
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
        run.lint_issues = [
            issue.to_dict()
            for issue in lint_workflow(
                workflow,
                require_tests=True,
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

        run.current_step = "publish"
        store.save_run(run)
        client = _client_for(base_url, secret_ref)
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

        webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
        run.webhook_url = f"{base_url}/webhook/{webhook_path}"
        if parsed_ir.tests and request.activate:
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
    _admin: object = Depends(require_admin),
):
    return _store().list_records(limit=limit)


@router.post("/workflows", response_model=N8nWorkflowRecord)
async def create_n8n_workflow_record(
    request: N8nWorkflowRecordCreateRequest,
    _admin: object = Depends(require_admin),
):
    store = _store()
    base_url = request.n8n_base_url.rstrip("/") or DEFAULT_N8N_BASE_URL
    workflow_url = request.workflow_url or f"{base_url}/workflow/{request.n8n_workflow_id}"
    record = N8nWorkflowRecord(
        id=_record_id_for(request.n8n_workflow_id),
        name=request.name.strip() or request.n8n_workflow_id,
        source=request.source,
        n8nWorkflowId=request.n8n_workflow_id,
        n8nBaseUrl=base_url,
        apiKeySecretRef=request.api_key_secret_ref,
        workflowUrl=workflow_url,
        webhookUrl=request.webhook_url,
        webhookPath=request.webhook_path,
        webhookMethod=request.webhook_method,
        userRequest=request.user_request,
        ir=request.ir,
        workflowJson=request.workflow_json,
        testCases=request.test_cases,
    )
    return store.save_record(record)


@router.post("/workflows/discover", response_model=List[N8nWorkflowRecord])
async def discover_n8n_workflow_records(
    request: Optional[N8nWorkflowDiscoverRequest] = None,
    _admin: object = Depends(require_admin),
):
    store = _store()
    connection = store.load_connection()
    base_url = ((request.base_url if request and request.base_url else connection.base_url) or DEFAULT_N8N_BASE_URL).rstrip("/")
    secret_ref = (request.api_key_secret_ref if request and request.api_key_secret_ref else connection.api_key_secret_ref) or DEFAULT_N8N_SECRET_REF
    prefix = (request.prefix if request else "flocks-") or "flocks-"
    include_all = bool(request.include_all) if request else False
    client = _client_for(base_url, secret_ref)

    cursor: Optional[str] = None
    for _ in range(20):
        response = await asyncio.to_thread(client.list_workflows, limit=100, cursor=cursor)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        rows = body.get("data") if isinstance(body, dict) else []
        if not isinstance(rows, list):
            break
        for workflow in rows:
            if not isinstance(workflow, dict):
                continue
            workflow_id = workflow.get("id")
            name = str(workflow.get("name") or "")
            existing = store.load_record(_record_id_for(str(workflow_id))) if workflow_id is not None else None
            if include_all or existing or name.startswith(prefix):
                _upsert_discovered_record(store, workflow, base_url=base_url, secret_ref=secret_ref)
        cursor = body.get("nextCursor") if isinstance(body.get("nextCursor"), str) else None
        if not cursor:
            break
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
async def delete_n8n_workflow_record(record_id: str, _admin: object = Depends(require_admin)):
    deleted = _store().delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    return {"success": True}


@router.post("/workflows/{record_id}/sync", response_model=N8nWorkflowRecord)
async def sync_n8n_workflow_record(record_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    try:
        workflow = await asyncio.to_thread(
            _client_for(record.n8n_base_url, record.api_key_secret_ref).get_workflow,
            record.n8n_workflow_id,
        )
        body = workflow.get("body") if isinstance(workflow.get("body"), dict) else {}
        record.remote_status = _remote_status_from_workflow(body)
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
    if not record.webhook_path and not record.webhook_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n webhook path is missing")
    if not record.test_cases:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="n8n test cases are missing")

    method = record.webhook_method or "POST"
    webhook_path = record.webhook_path
    if not webhook_path and record.webhook_url:
        webhook_path = record.webhook_url.rstrip("/").split("/webhook/")[-1]
    assert webhook_path is not None
    client = _client_for(record.n8n_base_url, record.api_key_secret_ref)
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


@router.post("/workflows/{record_id}/cleanup", response_model=N8nWorkflowRecord)
async def cleanup_n8n_workflow(record_id: str, _admin: object = Depends(require_admin)):
    store = _store()
    record = store.load_record(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="n8n workflow record not found")
    try:
        result = await asyncio.to_thread(
            cleanup_workflows,
            _client_for(record.n8n_base_url, record.api_key_secret_ref),
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
    parsed_ir = N8nIR.model_validate(run.ir)
    client = _client_for(run.base_url, run.api_key_secret_ref)
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
    client = _client_for(run.base_url, run.api_key_secret_ref)
    run.current_step = "cleanup"
    run.cleanup = await asyncio.to_thread(cleanup_workflows, client, [run.n8n_workflow_id])
    run.status = "cleaned" if all(item.get("success") for item in run.cleanup) else "cleanup_failed"
    run.current_step = "complete"
    _write_run_artifacts(run)
    return store.save_run(run)
