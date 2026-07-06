"""Built-in workflow integration config management tool.

This tool gives Rex a first-class path to the workflow config store from inside
the Flocks backend process. It intentionally reuses the existing workflow route
helpers so the tool and WebUI keep the same config shape and validation rules.
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
DESCRIPTION = """Read, compare, sync, or update a workflow integration config from the Flocks backend store.

Use this instead of reading server_api_token/service_api_token or curling local backend endpoints when a workflow guide asks to inspect or update workflow publish/trigger config.

Actions:
- get: read the effective workflow integration config and runtime summary.
- status: read a compact status summary without exposing the full config.
- diff: compare the current effective config with a proposed config; does not write.
- sync: ensure the config exists in WorkflowStore, migrating config.json fallback when needed.
- put: normalize and save the full proposed config into WorkflowStore.

Important:
- This tool manages the workflow integration template only. Runtime start/stop, Syslog listener bind, and API service publishing remain separate runtime operations.
- For sync and put, show the plan/diff to the user first and get confirmation before invoking the tool.
- Do not ask the user for backend API tokens or expose secrets in chat."""

DESCRIPTION_CN = """读取、对比、同步或写入工作流集成配置库。

当工作流 guide 要求查看或更新发布/触发配置时，优先使用本工具，不要读取 server_api_token/service_api_token，也不要手工 curl 本机后端接口。

动作：
- get：读取当前生效的集成配置和运行态摘要。
- status：读取简要状态，不返回完整配置。
- diff：将当前配置和候选配置做差异对比，不写入。
- sync：确保配置库存在模板，必要时从 config.json 兜底迁移。
- put：规范化后把完整候选配置写入 WorkflowStore。

注意：本工具只管理工作流集成模板；启动/停止 Syslog listener、发布/停止 API 服务等运行态动作仍使用对应运行态接口。"""


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


async def _normalize_proposed_config(workflow_id: str, config: Any) -> Dict[str, Any]:
    if config is None:
        raise HTTPException(status_code=422, detail="config is required for action='diff' or action='put'")
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be a JSON object")

    routes = _workflow_routes()
    data = routes._read_workflow_from_fs(workflow_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    return routes._normalize_workflow_integration_config_template(workflow_id, data, config)


def _status_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    config = response.get("config")
    config = config if isinstance(config, dict) else {}
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
            name="config",
            type=ParameterType.OBJECT,
            description="Full proposed workflow integration config. Required for action='diff' and action='put'.",
            required=False,
            json_schema={
                "type": "object",
                "additionalProperties": True,
            },
        ),
    ],
    tags=["workflow", "config", "integration", "trigger", "syslog", "publish"],
)
async def workflow_config_manage(
    ctx: ToolContext,
    action: str,
    workflow_id: str,
    config: Dict[str, Any] | None = None,
) -> ToolResult:
    normalized_action = str(action or "").strip().lower()
    normalized_workflow_id = str(workflow_id or "").strip()
    title = f"Workflow config: {normalized_workflow_id or workflow_id}"

    if normalized_action not in _ACTIONS:
        return ToolResult(
            success=False,
            error=f"Unsupported action: {action!r}. Expected one of: {', '.join(sorted(_ACTIONS))}.",
            title=title,
        )
    if not normalized_workflow_id:
        return ToolResult(success=False, error="workflow_id is required", title=title)

    try:
        if normalized_action == "get":
            response = await _read_effective_config(normalized_workflow_id)
            return ToolResult(success=True, output=response, title=title)

        if normalized_action == "status":
            response = await _read_effective_config(normalized_workflow_id)
            return ToolResult(success=True, output=_status_payload(response), title=title)

        if normalized_action == "diff":
            current = await _read_effective_config(normalized_workflow_id)
            proposed = await _normalize_proposed_config(normalized_workflow_id, config)
            diff = _config_diff(current["config"], proposed)
            return ToolResult(
                success=True,
                title=title,
                output={
                    "workflowId": normalized_workflow_id,
                    "changed": bool(diff),
                    "source": current.get("source"),
                    "storageKey": current.get("storageKey"),
                    "diff": diff,
                    "current": current["config"],
                    "proposed": proposed,
                },
            )

        routes = _workflow_routes()

        if normalized_action == "sync":
            await _confirm_write(
                ctx,
                action=normalized_action,
                workflow_id=normalized_workflow_id,
                metadata={"note": "ensure workflow integration config exists in WorkflowStore"},
            )
            response = await routes.sync_workflow_config(normalized_workflow_id)
            return ToolResult(success=True, output=response, title=title)

        if normalized_action == "put":
            current = await _read_effective_config(normalized_workflow_id)
            proposed = await _normalize_proposed_config(normalized_workflow_id, config)
            diff = _config_diff(current["config"], proposed)
            await _confirm_write(
                ctx,
                action=normalized_action,
                workflow_id=normalized_workflow_id,
                metadata={
                    "storage_key": current.get("storageKey"),
                    "changed": bool(diff),
                    "diff": diff,
                },
            )
            response = await routes.update_workflow_config(normalized_workflow_id, proposed)
            return ToolResult(success=True, output={**response, "diff": diff}, title=title)

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
                "error": str(exc),
            },
        )
        return ToolResult(success=False, error=_error_message(exc), title=title)
