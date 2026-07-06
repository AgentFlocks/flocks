"""Built-in workflow config management tool.

This tool gives Rex a first-class path to workflow config stores from inside the
Flocks backend process. It intentionally reuses the existing workflow route
helpers so the tool and WebUI keep the same config shape, validation rules, and
runtime side effects.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict

from fastapi import HTTPException

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.workflow_config_manage")

_ACTIONS = {"get", "status", "sync", "diff", "put"}
_CONFIG_TYPES = {"integration", "kafka", "poller", "syslog"}
_RUNTIME_CONFIG_KINDS = {
    "kafka": "workflow_kafka_config",
    "poller": "workflow_poller_config",
    "syslog": "workflow_syslog_config",
}
_RUNTIME_TRIGGER_TYPES = {
    "kafka": "kafka",
    "poller": "schedule",
    "syslog": "syslog",
}

DESCRIPTION = """Read, compare, sync, or update workflow configs from the Flocks backend store.

Use this instead of reading server_api_token/service_api_token or curling local backend endpoints when a workflow guide asks to inspect or update workflow publish, trigger, or runtime config.

Actions:
- get: read the effective workflow integration config and runtime summary.
- status: read a compact status summary without exposing the full config.
- diff: compare the current effective config with a proposed config; does not write.
- sync: ensure the config exists in WorkflowStore, migrating config.json fallback when needed.
- put: normalize and save the full proposed config into WorkflowStore.

Config types:
- integration: publish/trigger template config. This is the default for backward compatibility.
- poller: background schedule/poller runtime config.
- syslog: Syslog listener runtime config.
- kafka: Kafka consumer runtime config.

Important:
- This tool manages config reads/writes only. Non-config runtime commands such as API service publishing/unpublishing remain separate runtime operations.
- For sync and put, show the plan/diff to the user first and get confirmation before invoking the tool.
- Do not ask the user for backend API tokens or expose secrets in chat."""

DESCRIPTION_CN = """读取、对比、同步或写入工作流配置库。

当工作流 guide 要求查看或更新发布、触发或运行态配置时，优先使用本工具，不要读取 server_api_token/service_api_token，也不要手工 curl 本机后端接口。

动作：
- get：读取当前生效的集成配置和运行态摘要。
- status：读取简要状态，不返回完整配置。
- diff：将当前配置和候选配置做差异对比，不写入。
- sync：确保配置库存在模板，必要时从 config.json 兜底迁移。
- put：规范化后把完整候选配置写入 WorkflowStore。

配置类型：
- integration：发布/触发模板配置；默认值，用于兼容旧调用。
- poller：后台定时/poller 运行态配置。
- syslog：Syslog listener 运行态配置。
- kafka：Kafka consumer 运行态配置。

注意：本工具只管理配置读写；发布/停止 API 服务等非配置运行态动作仍使用对应运行态接口。"""


def _workflow_routes():
    """Import workflow routes lazily to avoid loading server modules at registry import time."""
    from flocks.server.routes import workflow as workflow_routes

    return workflow_routes


def _json_lines(payload: Any) -> list[str]:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).splitlines(keepends=True)


def _config_diff(current: Dict[str, Any], proposed: Dict[str, Any]) -> str:
    return "".join(
        difflib.unified_diff(
            _json_lines(current),
            _json_lines(proposed),
            fromfile="current_config",
            tofile="proposed_config",
            lineterm="",
        )
    )


def _error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        return json.dumps(detail, ensure_ascii=False, default=str)
    return str(exc)


async def _read_effective_config(workflow_id: str) -> Dict[str, Any]:
    routes = _workflow_routes()
    data = routes._read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    config_path = routes._workflow_config_dir(workflow_id, data) / "config.json"
    runtime = await routes._build_workflow_integration_runtime(workflow_id, data)

    config = await routes._read_stored_workflow_integration_config(workflow_id)
    if config is not None:
        return {
            "exists": True,
            "path": str(config_path),
            "storageKey": routes._workflow_integration_config_key(workflow_id),
            "source": "storage",
            "stored": True,
            "config": config,
            "runtime": runtime,
        }

    config = await routes._read_file_workflow_integration_config(workflow_id, data, config_path)
    if config is not None:
        return {
            "exists": True,
            "path": str(config_path),
            "storageKey": routes._workflow_integration_config_key(workflow_id),
            "source": "file_fallback",
            "stored": False,
            "config": config,
            "runtime": runtime,
        }

    return {
        "exists": False,
        "path": str(config_path),
        "storageKey": routes._workflow_integration_config_key(workflow_id),
        "source": "generated",
        "stored": False,
        "config": await routes._build_workflow_integration_config(workflow_id, data),
        "runtime": runtime,
    }


def _runtime_storage_key(config_type: str, workflow_id: str) -> str:
    return f"{_RUNTIME_CONFIG_KINDS[config_type]}/{workflow_id}"


async def _runtime_status(workflow_id: str, config_type: str) -> Dict[str, Any] | None:
    try:
        if config_type == "kafka":
            from flocks.ingest.kafka.manager import default_manager as kafka_manager

            return kafka_manager.get_consumer_status(workflow_id)
        if config_type == "poller":
            from flocks.workflow.poller_manager import default_manager as poller_manager

            return poller_manager.get_status(workflow_id)
        if config_type == "syslog":
            from flocks.ingest.syslog.manager import default_manager as syslog_manager

            return syslog_manager.get_listener_status(workflow_id)
    except Exception as exc:
        return {"error": str(exc)}
    return None


def _legacy_runtime_config_from_trigger(routes: Any, workflow_id: str, config_type: str, trigger: Any) -> Dict[str, Any]:
    if config_type == "kafka":
        return routes.kafka_trigger_to_legacy_config(workflow_id, trigger)
    if config_type == "poller":
        return routes.schedule_trigger_to_legacy_config(workflow_id, trigger)
    if config_type == "syslog":
        return routes.syslog_trigger_to_legacy_config(workflow_id, trigger)
    raise HTTPException(status_code=422, detail=f"Unsupported runtime config type: {config_type}")


async def _read_runtime_config(workflow_id: str, config_type: str) -> Dict[str, Any]:
    routes = _workflow_routes()
    data = routes._read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")

    kind = _RUNTIME_CONFIG_KINDS[config_type]
    config = await routes.WorkflowStore.get_config(workflow_id, kind=kind)
    if config is not None:
        return {
            "exists": True,
            "path": None,
            "storageKey": _runtime_storage_key(config_type, workflow_id),
            "source": "storage",
            "stored": True,
            "config": config,
            "runtime": await _runtime_status(workflow_id, config_type),
        }

    trigger_type = _RUNTIME_TRIGGER_TYPES[config_type]
    triggers = await routes._get_workflow_trigger_defs(workflow_id, data)
    trigger = next((item for item in triggers if item.type == trigger_type), None)
    if trigger is not None:
        return {
            "exists": True,
            "path": None,
            "storageKey": _runtime_storage_key(config_type, workflow_id),
            "source": "trigger_fallback",
            "stored": False,
            "config": _legacy_runtime_config_from_trigger(routes, workflow_id, config_type, trigger),
            "runtime": await _runtime_status(workflow_id, config_type),
        }

    return {
        "exists": False,
        "path": None,
        "storageKey": _runtime_storage_key(config_type, workflow_id),
        "source": "missing",
        "stored": False,
        "config": None,
        "runtime": await _runtime_status(workflow_id, config_type),
    }


async def _read_config(workflow_id: str, config_type: str) -> Dict[str, Any]:
    if config_type == "integration":
        return await _read_effective_config(workflow_id)
    return await _read_runtime_config(workflow_id, config_type)


def _ensure_runtime_workflow_id(workflow_id: str, config: Dict[str, Any]) -> None:
    candidate = config.get("workflowId")
    if candidate not in (None, workflow_id):
        raise HTTPException(status_code=409, detail="config.workflowId does not match workflow_id")


def _with_existing_updated_at(config: Dict[str, Any], current: Dict[str, Any] | None) -> Dict[str, Any]:
    current_config = (current or {}).get("config")
    if isinstance(current_config, dict) and current_config.get("updatedAt") is not None:
        config["updatedAt"] = current_config["updatedAt"]
    return config


def _without_updated_at(config: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(config)
    payload.pop("updatedAt", None)
    return payload


def _runtime_config_matches_proposed(config: Any, proposed: Dict[str, Any]) -> bool:
    if not isinstance(config, dict):
        return False
    return _without_updated_at(config) == _without_updated_at(proposed)


def _normalize_runtime_config(
    workflow_id: str,
    config_type: str,
    config: Dict[str, Any],
    current: Dict[str, Any] | None,
) -> Dict[str, Any]:
    routes = _workflow_routes()
    _ensure_runtime_workflow_id(workflow_id, config)

    if config_type == "kafka":
        req = routes.KafkaConfigRequest.model_validate(config)
        return _with_existing_updated_at(
            {
                "workflowId": workflow_id,
                "enabled": req.enabled,
                "inputBroker": req.inputBroker,
                "inputTopic": req.inputTopic,
                "inputGroupId": req.inputGroupId,
                "inputKey": req.inputKey,
                "autoOffsetReset": req.autoOffsetReset,
                "inputs": routes._strip_execution_only_comments(req.inputs),
            },
            current,
        )

    if config_type == "poller":
        req = routes.WorkflowPollerConfigRequest.model_validate(config)
        cron_expression = (req.cronExpression or "").strip()
        return _with_existing_updated_at(
            {
                "workflowId": workflow_id,
                "enabled": req.enabled,
                "intervalSeconds": req.intervalSeconds,
                "cronExpression": cron_expression or None,
                "timeoutSeconds": req.timeoutSeconds,
                "noOverlap": req.noOverlap,
                "inputs": req.inputs,
            },
            current,
        )

    if config_type == "syslog":
        req = routes.SyslogConfigRequest.model_validate(config)
        return _with_existing_updated_at(
            {
                "workflowId": workflow_id,
                "enabled": req.enabled,
                "protocol": req.protocol,
                "host": req.host,
                "port": req.port,
                "format": req.msg_format,
                "inputKey": req.input_key,
            },
            current,
        )

    raise HTTPException(status_code=422, detail=f"Unsupported runtime config type: {config_type}")


async def _normalize_proposed_config(
    workflow_id: str,
    config_type: str,
    config: Any,
    current: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if config is None:
        raise HTTPException(status_code=422, detail="config is required for action='diff' or action='put'")
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be a JSON object")

    if config_type != "integration":
        return _normalize_runtime_config(workflow_id, config_type, config, current)

    routes = _workflow_routes()
    data = routes._read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    return routes._normalize_workflow_integration_config_template(workflow_id, data, config)


def _status_payload(config_type: str, response: Dict[str, Any]) -> Dict[str, Any]:
    config = response.get("config")
    config = config if isinstance(config, dict) else {}
    if config_type != "integration":
        return {
            "exists": response.get("exists"),
            "source": response.get("source"),
            "stored": response.get("stored"),
            "storageKey": response.get("storageKey"),
            "workflowId": config.get("workflowId"),
            "configType": config_type,
            "enabled": config.get("enabled"),
            "runtime": response.get("runtime"),
        }

    triggers = config.get("triggers") if isinstance(config.get("triggers"), list) else []
    publish = config.get("publish") if isinstance(config.get("publish"), dict) else {}
    runtime = response.get("runtime") if isinstance(response.get("runtime"), dict) else {}
    runtime_triggers = runtime.get("triggers") if isinstance(runtime.get("triggers"), list) else []

    return {
        "exists": response.get("exists"),
        "source": response.get("source"),
        "stored": response.get("stored"),
        "path": response.get("path"),
        "storageKey": response.get("storageKey"),
        "workflow": config.get("workflow"),
        "publish": publish,
        "triggerCount": len(triggers),
        "triggerTypes": [item.get("type") for item in triggers if isinstance(item, dict)],
        "runtime": {
            "publish": runtime.get("publish"),
            "triggerCount": len(runtime_triggers),
        },
    }


async def _confirm_write(ctx: ToolContext, *, action: str, workflow_id: str, metadata: Dict[str, Any]) -> None:
    await ctx.ask(
        permission="workflow_config",
        patterns=[workflow_id, action],
        always=["*"],
        metadata={
            "workflow_id": workflow_id,
            "action": action,
            **metadata,
        },
    )


async def _sync_runtime_config(workflow_id: str, config_type: str, current: Dict[str, Any]) -> Dict[str, Any]:
    config = current.get("config")
    if current.get("stored"):
        return current
    if not isinstance(config, dict):
        raise HTTPException(status_code=404, detail=f"No {config_type} config exists for workflow: {workflow_id}")

    routes = _workflow_routes()
    await routes.WorkflowStore.put_config(workflow_id, config, kind=_RUNTIME_CONFIG_KINDS[config_type])
    return await _read_runtime_config(workflow_id, config_type)


async def _save_runtime_config(workflow_id: str, config_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    routes = _workflow_routes()
    if config_type == "kafka":
        req = routes.KafkaConfigRequest.model_validate(config)
        return await routes.save_kafka_config(workflow_id, req)
    if config_type == "poller":
        req = routes.WorkflowPollerConfigRequest.model_validate(config)
        return await routes.save_workflow_poller_config(workflow_id, req)
    if config_type == "syslog":
        req = routes.SyslogConfigRequest.model_validate(config)
        return await routes.save_syslog_config(workflow_id, req)
    raise HTTPException(status_code=422, detail=f"Unsupported runtime config type: {config_type}")


@ToolRegistry.register_function(
    name="workflow_config_manage",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="Operation to run: get, status, sync, diff, or put.",
            required=True,
            enum=sorted(_ACTIONS),
        ),
        ToolParameter(
            name="workflow_id",
            type=ParameterType.STRING,
            description="Workflow id, for example stream_alert_denoise or stream_alert_triage.",
            required=True,
        ),
        ToolParameter(
            name="config_type",
            type=ParameterType.STRING,
            description="Config type to manage: integration, poller, syslog, or kafka. Defaults to integration.",
            required=False,
            enum=sorted(_CONFIG_TYPES),
        ),
        ToolParameter(
            name="config",
            type=ParameterType.OBJECT,
            description="Full proposed workflow config. Required for action='diff' and action='put'.",
            required=False,
            json_schema={
                "type": "object",
                "additionalProperties": True,
            },
        ),
    ],
    tags=["workflow", "config", "integration", "trigger", "syslog", "kafka", "poller", "publish"],
)
async def workflow_config_manage(
    ctx: ToolContext,
    action: str,
    workflow_id: str,
    config_type: str = "integration",
    config: Dict[str, Any] | None = None,
) -> ToolResult:
    normalized_action = str(action or "").strip().lower()
    normalized_workflow_id = str(workflow_id or "").strip()
    normalized_config_type = str(config_type or "integration").strip().lower()
    title = f"Workflow config: {normalized_workflow_id or workflow_id} ({normalized_config_type})"

    if normalized_action not in _ACTIONS:
        return ToolResult(
            success=False,
            error=f"Unsupported action: {action!r}. Expected one of: {', '.join(sorted(_ACTIONS))}.",
            title=title,
        )
    if not normalized_workflow_id:
        return ToolResult(success=False, error="workflow_id is required", title=title)
    if normalized_config_type not in _CONFIG_TYPES:
        return ToolResult(
            success=False,
            error=f"Unsupported config_type: {config_type!r}. Expected one of: {', '.join(sorted(_CONFIG_TYPES))}.",
            title=title,
        )

    try:
        if normalized_action == "get":
            response = await _read_config(normalized_workflow_id, normalized_config_type)
            response["configType"] = normalized_config_type
            return ToolResult(success=True, output=response, title=title)

        if normalized_action == "status":
            response = await _read_config(normalized_workflow_id, normalized_config_type)
            return ToolResult(success=True, output=_status_payload(normalized_config_type, response), title=title)

        if normalized_action == "diff":
            current = await _read_config(normalized_workflow_id, normalized_config_type)
            proposed = await _normalize_proposed_config(
                normalized_workflow_id,
                normalized_config_type,
                config,
                current,
            )
            current_config = current.get("config") if isinstance(current.get("config"), dict) else {}
            diff = _config_diff(current_config, proposed)
            return ToolResult(
                success=True,
                title=title,
                output={
                    "workflowId": normalized_workflow_id,
                    "configType": normalized_config_type,
                    "changed": bool(diff),
                    "source": current.get("source"),
                    "storageKey": current.get("storageKey"),
                    "diff": diff,
                    "current": current_config,
                    "proposed": proposed,
                },
            )

        routes = _workflow_routes()

        if normalized_action == "sync":
            current = await _read_config(normalized_workflow_id, normalized_config_type)
            await _confirm_write(
                ctx,
                action=normalized_action,
                workflow_id=normalized_workflow_id,
                metadata={
                    "config_type": normalized_config_type,
                    "storage_key": current.get("storageKey"),
                    "note": f"ensure {normalized_config_type} config exists in WorkflowStore",
                },
            )
            if normalized_config_type == "integration":
                response = await routes.sync_workflow_config(normalized_workflow_id)
            else:
                response = await _sync_runtime_config(normalized_workflow_id, normalized_config_type, current)
            response["configType"] = normalized_config_type
            return ToolResult(success=True, output=response, title=title)

        if normalized_action == "put":
            current = await _read_config(normalized_workflow_id, normalized_config_type)
            proposed = await _normalize_proposed_config(
                normalized_workflow_id,
                normalized_config_type,
                config,
                current,
            )
            current_config = current.get("config") if isinstance(current.get("config"), dict) else {}
            diff = _config_diff(current_config, proposed)
            if normalized_config_type != "integration" and not diff:
                output = {
                    **current,
                    "workflowId": normalized_workflow_id,
                    "configType": normalized_config_type,
                    "changed": False,
                    "applied": False,
                    "runtimeFailed": False,
                    "saveResult": {
                        "ok": True,
                        "skipped": True,
                        "reason": "no_changes",
                    },
                    "diff": diff,
                }
                return ToolResult(success=True, output=output, title=title)

            await _confirm_write(
                ctx,
                action=normalized_action,
                workflow_id=normalized_workflow_id,
                metadata={
                    "config_type": normalized_config_type,
                    "storage_key": current.get("storageKey"),
                    "changed": bool(diff),
                    "diff": diff,
                },
            )
            if normalized_config_type == "integration":
                response = await routes.update_workflow_config(normalized_workflow_id, proposed)
                output = {**response, "configType": normalized_config_type, "diff": diff}
            else:
                try:
                    save_response = await _save_runtime_config(normalized_workflow_id, normalized_config_type, proposed)
                except HTTPException as exc:
                    response = await _read_runtime_config(normalized_workflow_id, normalized_config_type)
                    if _runtime_config_matches_proposed(response.get("config"), proposed):
                        output = {
                            **response,
                            "workflowId": normalized_workflow_id,
                            "configType": normalized_config_type,
                            "changed": bool(diff),
                            "applied": True,
                            "runtimeFailed": True,
                            "runtimeError": _error_message(exc),
                            "saveResult": {
                                "ok": False,
                                "error": _error_message(exc),
                                "status_code": exc.status_code,
                            },
                            "diff": diff,
                        }
                        return ToolResult(success=True, output=output, title=title)
                    raise
                response = await _read_runtime_config(normalized_workflow_id, normalized_config_type)
                output = {
                    **response,
                    "workflowId": normalized_workflow_id,
                    "configType": normalized_config_type,
                    "changed": bool(diff),
                    "applied": True,
                    "runtimeFailed": False,
                    "saveResult": save_response,
                    "diff": diff,
                }
            return ToolResult(success=True, output=output, title=title)

        return ToolResult(success=False, error=f"Unsupported action: {action!r}", title=title)
    except HTTPException as exc:
        return ToolResult(
            success=False,
            error=_error_message(exc),
            title=title,
            metadata={"status_code": exc.status_code},
        )
    except Exception as exc:
        log.error(
            "workflow_config_manage.failed",
            {
                "action": normalized_action,
                "workflow_id": normalized_workflow_id,
                "config_type": normalized_config_type,
                "error": str(exc),
            },
        )
        return ToolResult(success=False, error=_error_message(exc), title=title)
