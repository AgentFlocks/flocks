"""Built-in tools for generating, validating, publishing, and testing n8n workflows."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from flocks.integrations.n8n import (
    N8nClient,
    N8nClientError,
    N8nConfig,
    N8nIR,
    N8nTestCase,
    lint_workflow,
    render_ir_to_workflow,
    run_webhook_tests,
    workflow_to_api_create_payload,
)
from flocks.integrations.n8n.cleanup import cleanup_workflows
from flocks.integrations.n8n.repair import build_repair_context
from flocks.integrations.n8n.renderer import kafka_group_id_for_ir, slugify_webhook_path
from flocks.integrations.n8n.secrets import resolve_n8n_api_key
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.n8n")


def _client(
    *,
    base_url: str = "http://localhost:5678",
    api_key: Optional[str] = None,
    api_key_secret_ref: str = "N8N_API_KEY",
    timeout_s: float = 30.0,
) -> N8nClient:
    key = resolve_n8n_api_key(explicit=api_key, secret_ref=api_key_secret_ref)
    return N8nClient(N8nConfig(base_url=base_url, api_key=key, timeout_s=timeout_s))


def _tool_error(exc: Exception) -> ToolResult:
    if isinstance(exc, N8nClientError):
        return ToolResult(
            success=False,
            error=str(exc),
            output={"status": exc.status, "body": exc.body},
        )
    return ToolResult(success=False, error=str(exc))


def _json_obj(value: Any, *, name: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{name} must be an object or JSON object string")


def _kafka_group_prefixes_from_ir(ir: N8nIR) -> List[str]:
    if ir.trigger.type != "kafka":
        return []
    prefixes: List[str] = []
    if ir.trigger.group_prefix:
        prefixes.append(ir.trigger.group_prefix)
    option_prefix = ir.trigger.options.get("groupPrefix") if isinstance(ir.trigger.options, dict) else None
    if isinstance(option_prefix, str) and option_prefix.strip():
        prefixes.append(option_prefix)
    return prefixes


COMMON_N8N_PARAMS = [
    ToolParameter(
        name="base_url",
        type=ParameterType.STRING,
        description="n8n base URL, for example http://localhost:5678.",
        required=False,
        default="http://localhost:5678",
    ),
    ToolParameter(
        name="api_key_secret_ref",
        type=ParameterType.STRING,
        description="Environment variable or secret reference that contains the n8n API key.",
        required=False,
        default="N8N_API_KEY",
    ),
    ToolParameter(
        name="api_key",
        type=ParameterType.STRING,
        description="Transient n8n API key value. Prefer api_key_secret_ref for production use.",
        required=False,
    ),
]


@ToolRegistry.register_function(
    name="n8n_health_check",
    description="Check whether an n8n instance is reachable.",
    description_cn="检查 n8n 实例是否可访问。",
    category=ToolCategory.CUSTOM,
    parameters=[COMMON_N8N_PARAMS[0]],
)
async def n8n_health_check_tool(ctx: ToolContext, base_url: str = "http://localhost:5678") -> ToolResult:
    try:
        result = N8nClient(N8nConfig(base_url=base_url)).health_check()
        return ToolResult(success=True, output=result)
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_render",
    description="Render stable Flocks n8n IR into native n8n workflow JSON.",
    description_cn="将稳定的 Flocks n8n IR 渲染成 n8n 原生 workflow JSON。",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(
            name="ir",
            type=ParameterType.OBJECT,
            description="n8n IR object.",
            required=True,
            json_schema={"type": "object", "additionalProperties": True},
        ),
        ToolParameter(
            name="workflow_id",
            type=ParameterType.STRING,
            description="Optional workflow id for CLI import payloads.",
            required=False,
        ),
        ToolParameter(
            name="api_payload",
            type=ParameterType.BOOLEAN,
            description="When true, remove fields that n8n Public API marks read-only.",
            required=False,
            default=False,
        ),
    ],
)
async def n8n_workflow_render_tool(
    ctx: ToolContext,
    ir: Dict[str, Any],
    workflow_id: Optional[str] = None,
    api_payload: bool = False,
) -> ToolResult:
    try:
        workflow = render_ir_to_workflow(ir, workflow_id=workflow_id)
        if api_payload:
            workflow = workflow_to_api_create_payload(workflow)
        return ToolResult(success=True, output={"workflow": workflow})
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_lint",
    description="Statically validate generated n8n workflow JSON before publishing.",
    description_cn="发布前静态校验 n8n workflow JSON。",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="n8n workflow object or JSON string.",
            required=True,
            json_schema={
                "anyOf": [
                    {"type": "object", "additionalProperties": True},
                    {"type": "string"},
                ]
            },
        ),
        ToolParameter(name="for_api_create", type=ParameterType.BOOLEAN, required=False, default=False),
        ToolParameter(name="require_tests", type=ParameterType.BOOLEAN, required=False, default=False),
        ToolParameter(
            name="tests",
            type=ParameterType.ARRAY,
            description="Optional test cases used to require runtime assertions.",
            required=False,
            json_schema={"type": "array", "items": {"type": "object"}},
        ),
        ToolParameter(
            name="kafka_group_prefixes",
            type=ParameterType.ARRAY,
            description="Optional Kafka application consumer group prefixes allowed for Kafka Trigger groupId.",
            required=False,
            json_schema={"type": "array", "items": {"type": "string"}},
        ),
    ],
)
async def n8n_workflow_lint_tool(
    ctx: ToolContext,
    workflow: Dict[str, Any] | str,
    for_api_create: bool = False,
    require_tests: bool = False,
    tests: Optional[List[Dict[str, Any]]] = None,
    kafka_group_prefixes: Optional[List[str]] = None,
) -> ToolResult:
    issues = lint_workflow(
        workflow,
        for_api_create=for_api_create,
        require_tests=require_tests,
        tests=tests,
        kafka_group_prefixes=kafka_group_prefixes,
    )
    return ToolResult(
        success=not any(issue.severity == "error" for issue in issues),
        output={"issues": [issue.to_dict() for issue in issues]},
    )


@ToolRegistry.register_function(
    name="n8n_workflow_create",
    description="Create a workflow in n8n through the Public API.",
    description_cn="通过 n8n Public API 创建 workflow。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="Native n8n workflow JSON. Read-only fields are removed before API create.",
            required=True,
            json_schema={"type": "object", "additionalProperties": True},
        ),
    ],
)
async def n8n_workflow_create_tool(
    ctx: ToolContext,
    workflow: Dict[str, Any],
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        payload = workflow_to_api_create_payload(_json_obj(workflow, name="workflow"))
        issues = lint_workflow(payload, for_api_create=True)
        if any(issue.severity == "error" for issue in issues):
            return ToolResult(success=False, error="n8n workflow lint failed", output={"issues": [i.to_dict() for i in issues]})
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).create_workflow(payload)
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_update",
    description="Update an existing n8n workflow through the Public API.",
    description_cn="通过 n8n Public API 更新已有 workflow。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="workflow_id", type=ParameterType.STRING, description="n8n workflow id.", required=True),
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="Native n8n workflow JSON. Read-only fields are removed before API update.",
            required=True,
            json_schema={"type": "object", "additionalProperties": True},
        ),
    ],
)
async def n8n_workflow_update_tool(
    ctx: ToolContext,
    workflow_id: str,
    workflow: Dict[str, Any],
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        payload = workflow_to_api_create_payload(_json_obj(workflow, name="workflow"))
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).update_workflow(workflow_id, payload)
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_activate",
    description="Activate an n8n workflow.",
    description_cn="激活 n8n workflow。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="workflow_id", type=ParameterType.STRING, description="n8n workflow id.", required=True),
    ],
)
async def n8n_workflow_activate_tool(
    ctx: ToolContext,
    workflow_id: str,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).activate_workflow(workflow_id)
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_deactivate",
    description="Deactivate an n8n workflow.",
    description_cn="停用 n8n workflow。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="workflow_id", type=ParameterType.STRING, description="n8n workflow id.", required=True),
    ],
)
async def n8n_workflow_deactivate_tool(
    ctx: ToolContext,
    workflow_id: str,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).deactivate_workflow(workflow_id)
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_delete",
    description="Delete an n8n workflow. Use only for test workflows or explicit cleanup.",
    description_cn="删除 n8n workflow，仅用于测试 workflow 或明确清理。",
    category=ToolCategory.CUSTOM,
    requires_confirmation=True,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="workflow_id", type=ParameterType.STRING, description="n8n workflow id.", required=True),
    ],
)
async def n8n_workflow_delete_tool(
    ctx: ToolContext,
    workflow_id: str,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).delete_workflow(workflow_id)
        return ToolResult(success=True, output=result)
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_workflow_get",
    description="Get an n8n workflow by id.",
    description_cn="按 ID 读取 n8n workflow。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="workflow_id", type=ParameterType.STRING, description="n8n workflow id.", required=True),
    ],
)
async def n8n_workflow_get_tool(
    ctx: ToolContext,
    workflow_id: str,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).get_workflow(workflow_id)
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_execution_get",
    description="Get n8n execution details, including node data when available.",
    description_cn="读取 n8n execution 详情，包括可用的节点数据。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="execution_id", type=ParameterType.STRING, description="n8n execution id.", required=True),
        ToolParameter(name="include_data", type=ParameterType.BOOLEAN, required=False, default=True),
    ],
)
async def n8n_execution_get_tool(
    ctx: ToolContext,
    execution_id: str,
    include_data: bool = True,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        result = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref).get_execution(
            execution_id,
            include_data=include_data,
        )
        return ToolResult(success=True, output=result.get("body"))
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_webhook_call",
    description="Call an n8n production webhook for runtime testing.",
    description_cn="调用 n8n production webhook 做运行时测试。",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(name="base_url", type=ParameterType.STRING, required=False, default="http://localhost:5678"),
        ToolParameter(name="webhook_path", type=ParameterType.STRING, description="Webhook path without /webhook prefix.", required=True),
        ToolParameter(name="payload", type=ParameterType.OBJECT, required=False, json_schema={"type": "object", "additionalProperties": True}),
        ToolParameter(name="headers", type=ParameterType.OBJECT, required=False, json_schema={"type": "object", "additionalProperties": {"type": "string"}}),
        ToolParameter(name="method", type=ParameterType.STRING, required=False, default="POST", enum=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ],
)
async def n8n_webhook_call_tool(
    ctx: ToolContext,
    webhook_path: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
    base_url: str = "http://localhost:5678",
) -> ToolResult:
    try:
        result = N8nClient(N8nConfig(base_url=base_url)).call_webhook(
            webhook_path,
            method=method,
            payload=payload or {},
            headers=headers or {},
        )
        return ToolResult(success=True, output=result)
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_test_run",
    description=(
        "Render IR, lint it, create a test n8n workflow, activate it, call webhook tests, "
        "and optionally clean up the test workflow."
    ),
    description_cn="渲染 IR、静态校验、创建测试 n8n workflow、激活、调用 webhook 测试，并可选清理。",
    category=ToolCategory.CUSTOM,
    parameters=[
        *COMMON_N8N_PARAMS,
        ToolParameter(name="ir", type=ParameterType.OBJECT, description="n8n IR with tests.", required=True, json_schema={"type": "object", "additionalProperties": True}),
        ToolParameter(name="cleanup_on_success", type=ParameterType.BOOLEAN, required=False, default=False),
        ToolParameter(name="wait_for_execution", type=ParameterType.BOOLEAN, required=False, default=False),
    ],
)
async def n8n_test_run_tool(
    ctx: ToolContext,
    ir: Dict[str, Any],
    cleanup_on_success: bool = False,
    wait_for_execution: bool = False,
    base_url: str = "http://localhost:5678",
    api_key_secret_ref: str = "N8N_API_KEY",
    api_key: Optional[str] = None,
) -> ToolResult:
    try:
        parsed_ir = N8nIR.model_validate(ir)
        workflow = render_ir_to_workflow(parsed_ir)
        is_webhook_trigger = parsed_ir.trigger.type == "webhook"
        issues = lint_workflow(
            workflow,
            require_tests=is_webhook_trigger,
            tests=[case.model_dump(by_alias=True) for case in parsed_ir.tests],
            kafka_group_prefixes=_kafka_group_prefixes_from_ir(parsed_ir),
        )
        if any(issue.severity == "error" for issue in issues):
            return ToolResult(success=False, error="n8n workflow lint failed", output={"issues": [i.to_dict() for i in issues], "workflow": workflow})

        client = _client(base_url=base_url, api_key=api_key, api_key_secret_ref=api_key_secret_ref)
        create = client.create_workflow(workflow_to_api_create_payload(workflow))
        created = create.get("body") if isinstance(create.get("body"), dict) else {}
        workflow_id = str(created.get("id") or "")
        if not workflow_id:
            return ToolResult(success=False, error="n8n create response did not contain workflow id", output=create)
        client.activate_workflow(workflow_id)
        if parsed_ir.trigger.type == "kafka":
            cleanup = cleanup_workflows(client, [workflow_id]) if cleanup_on_success else []
            return ToolResult(
                success=True,
                output={
                    "workflow_id": workflow_id,
                    "workflow_url": base_url.rstrip("/") + "/workflow/" + workflow_id,
                    "webhook_url": "",
                    "trigger_type": "kafka",
                    "kafka_topic": parsed_ir.trigger.topic or "",
                    "kafka_group_id": kafka_group_id_for_ir(parsed_ir),
                    "workflow": workflow,
                    "lint_issues": [issue.to_dict() for issue in issues],
                    "test_results": [],
                    "cleanup": cleanup,
                },
            )
        webhook_path = slugify_webhook_path(str(parsed_ir.trigger.path or parsed_ir.name))
        test_results = run_webhook_tests(
            client,
            webhook_path=webhook_path,
            tests=parsed_ir.tests,
            method=parsed_ir.trigger.method,
            workflow_id=workflow_id,
            wait_for_execution=wait_for_execution,
        )
        success = all(result.success for result in test_results)
        cleanup = []
        if success and cleanup_on_success:
            cleanup = cleanup_workflows(client, [workflow_id])
        return ToolResult(
            success=success,
            output={
                "workflow_id": workflow_id,
                "workflow_url": base_url.rstrip("/") + "/workflow/" + workflow_id,
                "webhook_url": base_url.rstrip("/") + "/webhook/" + webhook_path,
                "workflow": workflow,
                "lint_issues": [issue.to_dict() for issue in issues],
                "test_results": [result.to_dict() for result in test_results],
                "cleanup": cleanup,
            },
        )
    except Exception as exc:
        return _tool_error(exc)


@ToolRegistry.register_function(
    name="n8n_repair_context",
    description="Build a sanitized repair context from lint and runtime test failures.",
    description_cn="从 lint 和运行时测试失败中构造脱敏修复上下文。",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(name="user_request", type=ParameterType.STRING, required=True),
        ToolParameter(name="ir", type=ParameterType.OBJECT, required=True, json_schema={"type": "object", "additionalProperties": True}),
        ToolParameter(name="workflow", type=ParameterType.OBJECT, required=True, json_schema={"type": "object", "additionalProperties": True}),
        ToolParameter(name="lint_issues", type=ParameterType.ARRAY, required=False, json_schema={"type": "array", "items": {"type": "object"}}),
        ToolParameter(name="test_results", type=ParameterType.ARRAY, required=False, json_schema={"type": "array", "items": {"type": "object"}}),
        ToolParameter(name="iteration", type=ParameterType.INTEGER, required=False, default=1),
    ],
)
async def n8n_repair_context_tool(
    ctx: ToolContext,
    user_request: str,
    ir: Dict[str, Any],
    workflow: Dict[str, Any],
    lint_issues: Optional[List[Dict[str, Any]]] = None,
    test_results: Optional[List[Dict[str, Any]]] = None,
    iteration: int = 1,
) -> ToolResult:
    try:
        context = build_repair_context(
            user_request=user_request,
            ir=ir,
            workflow=workflow,
            lint_issues=lint_issues or [],
            test_results=test_results or [],
            iteration=iteration,
        )
        return ToolResult(success=True, output=context)
    except Exception as exc:
        return _tool_error(exc)
