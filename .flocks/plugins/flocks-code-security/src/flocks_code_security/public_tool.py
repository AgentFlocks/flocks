"""Public multi-action code-security audit tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flocks.auth.context import get_current_auth_user
from flocks.auth.service import AuthService
from flocks.session.session import Session
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)

from flocks_code_security.service import (
    AuditCaller,
    AuditServiceError,
    StartScanRequest,
    get_audit_service,
)


PUBLIC_TOOL_NAME = "code_security_audit"
PUBLIC_TOOL_ACTIONS = ["start", "status", "wait", "result", "cancel", "list"]
_REGISTERED_TOOL: Tool | None = None


def _parameter(
    name: str,
    parameter_type: ParameterType,
    description: str,
    *,
    required: bool = False,
    default: Any = None,
    enum: list[Any] | None = None,
    json_schema: dict[str, Any] | None = None,
) -> ToolParameter:
    return ToolParameter(
        name=name,
        type=parameter_type,
        description=description,
        required=required,
        default=default,
        enum=enum,
        json_schema=json_schema,
    )


async def _caller(ctx: ToolContext) -> AuditCaller:
    session = await Session.get_by_id(ctx.session_id)
    auth_user = get_current_auth_user()
    if auth_user is None and getattr(session, "owner_user_id", None):
        auth_user = await AuthService.get_user_by_id(str(session.owner_user_id))
    subject = str(
        getattr(auth_user, "id", None)
        or getattr(session, "owner_user_id", None)
        or getattr(session, "owner_username", None)
        or f"session:{ctx.session_id}"
    )
    workspace_ref = getattr(session, "project_id", None)
    workspace_dir = getattr(session, "directory", None)
    authorized_root = Path(str(workspace_dir)).expanduser().resolve() if workspace_dir else None
    return AuditCaller(
        subject=subject,
        source="tool",
        is_admin=getattr(auth_user, "role", None) == "admin",
        workspace_ref=str(workspace_ref) if workspace_ref else None,
        authorized_root=authorized_root,
    )


async def code_security_audit(
    ctx: ToolContext,
    action: str,
    target_path: str | None = None,
    scan_id: str | None = None,
    model: str | None = None,
    include_paths: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    max_file_bytes: int = 1_048_576,
    dynamic_enabled: bool = False,
    coverage_policy: str = "evidence_backed_partial",
    verification_votes: int = 1,
    idempotency_key: str | None = None,
    after_event_seq: int = 0,
    timeout_seconds: int = 30,
    status_filter: list[str] | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> ToolResult:
    try:
        service = get_audit_service()
        caller = await _caller(ctx)
        if action == "start":
            if not target_path:
                raise AuditServiceError("missing_parameter", "target_path is required for start")
            requested_target = Path(target_path).expanduser()
            if not requested_target.is_absolute():
                raise AuditServiceError("invalid_parameter", "target_path must be absolute")
            target = requested_target.resolve()
            await ctx.ask(
                "code_security.audit.start",
                [str(target)],
                metadata={"action": "start", "dynamic_enabled": bool(dynamic_enabled)},
            )
            if dynamic_enabled:
                await ctx.ask(
                    "code_security.audit.dynamic",
                    [str(target)],
                    metadata={
                        "action": "start",
                        "dynamic_enabled": True,
                        "safety": "network-none, no-host-mounts, read-only-rootfs",
                    },
                )
            detail = await service.start_scan(
                StartScanRequest(
                    target_path=target,
                    model=model,
                    include_paths=tuple(include_paths or ["."]),
                    exclude_patterns=tuple(exclude_patterns or []),
                    max_file_bytes=max_file_bytes,
                    dynamic_enabled=dynamic_enabled,
                    coverage_policy=coverage_policy,
                    verification_votes=verification_votes,
                    idempotency_key=idempotency_key,
                ),
                caller,
            )
            return ToolResult(
                success=True,
                output={
                    "schema_version": detail["schema_version"],
                    "action": "start",
                    "scan": {**detail["scan"], "target": detail["target"]},
                    "workspace_url": detail["workspace_url"],
                },
                title=f"Started code-security audit {detail['scan']['scan_id']}",
                metadata={"scan_id": detail["scan"]["scan_id"]},
            )

        if action in {"status", "wait", "result", "cancel"} and not scan_id:
            raise AuditServiceError("missing_parameter", f"scan_id is required for {action}")

        if action == "status":
            detail = await service.get_scan(str(scan_id), caller)
            output = {
                "schema_version": detail["schema_version"],
                "action": "status",
                "scan": detail["scan"],
                "counts": detail["counts"],
                "finding_summary": detail["finding_summary"],
                "coverage_summary": detail["coverage_summary"],
                "dynamic_validation": detail["dynamic_validation"],
                "phase_runs": detail["phase_runs"],
            }
        elif action == "wait":
            output = await service.wait_scan(
                str(scan_id),
                caller,
                after_seq=after_event_seq,
                timeout_seconds=timeout_seconds,
            )
            output["action"] = "wait"
        elif action == "result":
            output = await service.get_result(str(scan_id), caller)
        elif action == "cancel":
            await ctx.ask(
                "code_security.audit.cancel",
                [str(scan_id)],
                metadata={"action": "cancel", "scan_id": scan_id},
            )
            detail = await service.cancel_scan(str(scan_id), caller)
            output = {
                "schema_version": detail["schema_version"],
                "action": "cancel",
                "scan": detail["scan"],
            }
        elif action == "list":
            output = await service.list_scans(
                caller,
                statuses=set(status_filter or []),
                cursor=cursor,
                limit=limit,
            )
            output["action"] = "list"
        else:
            raise AuditServiceError("invalid_action", f"Unsupported action: {action}")

        return ToolResult(
            success=True,
            output=output,
            title=f"Code-security audit {action}",
            metadata={"scan_id": scan_id} if scan_id else {},
        )
    except AuditServiceError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            title="Code-security audit request failed",
            metadata={"error_code": exc.code, "status_code": exc.status_code},
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            title="Code-security audit request failed",
            metadata={"error_code": "audit_request_failed"},
        )


def register_public_tool() -> None:
    global _REGISTERED_TOOL
    existing = ToolRegistry.get(PUBLIC_TOOL_NAME)
    if existing is not None:
        if _REGISTERED_TOOL is existing:
            return
        raise RuntimeError(f"Refusing to overwrite existing tool registration: {PUBLIC_TOOL_NAME}")

    string_array = {"type": "array", "items": {"type": "string"}}
    tool = Tool(
        info=ToolInfo(
            name=PUBLIC_TOOL_NAME,
            description=(
                "Start and manage a trusted immutable-snapshot code security audit. "
                "Static analysis is the default and never executes target code. "
                "dynamic_enabled=true runs validated probes in restricted local Docker. "
                "Start returns quickly; keep the scan_id for status, wait, result, or cancel."
            ),
            category=ToolCategory.CUSTOM,
            parameters=[
                _parameter(
                    "action", ParameterType.STRING, "Lifecycle action.", required=True, enum=PUBLIC_TOOL_ACTIONS
                ),
                _parameter("target_path", ParameterType.STRING, "Absolute target directory for start."),
                _parameter("scan_id", ParameterType.STRING, "Scan identifier for status, wait, result, or cancel."),
                _parameter("model", ParameterType.STRING, "Optional pinned provider/model."),
                _parameter(
                    "include_paths",
                    ParameterType.ARRAY,
                    "Snapshot-relative paths to include.",
                    json_schema=string_array,
                ),
                _parameter(
                    "exclude_patterns",
                    ParameterType.ARRAY,
                    "Snapshot-relative glob patterns to exclude.",
                    json_schema=string_array,
                ),
                _parameter(
                    "max_file_bytes", ParameterType.INTEGER, "Maximum bytes copied per file.", default=1_048_576
                ),
                _parameter(
                    "dynamic_enabled",
                    ParameterType.BOOLEAN,
                    "Enable restricted local Docker validation.",
                    default=False,
                ),
                _parameter(
                    "coverage_policy",
                    ParameterType.STRING,
                    "Allow trusted partial coverage or require exhaustive terminal dispositions.",
                    default="evidence_backed_partial",
                    enum=["evidence_backed_partial", "exhaustive"],
                ),
                _parameter(
                    "verification_votes",
                    ParameterType.INTEGER,
                    "Independent verifier votes required per candidate (1 to 5).",
                    default=1,
                ),
                _parameter("idempotency_key", ParameterType.STRING, "Caller-scoped start idempotency key."),
                _parameter(
                    "after_event_seq", ParameterType.INTEGER, "Wait after this durable event sequence.", default=0
                ),
                _parameter("timeout_seconds", ParameterType.INTEGER, "Wait duration from 0 to 60 seconds.", default=30),
                _parameter(
                    "status_filter", ParameterType.ARRAY, "Lifecycle filters for list.", json_schema=string_array
                ),
                _parameter("cursor", ParameterType.STRING, "Opaque list cursor."),
                _parameter("limit", ParameterType.INTEGER, "List size from 1 to 100.", default=20),
            ],
            source="plugin_py",
            native=False,
            always_load=False,
            tags=["security", "code-security", "code-audit", "static-analysis", "dynamic-validation"],
        ),
        handler=code_security_audit,
    )
    ToolRegistry.register(tool)
    _REGISTERED_TOOL = tool
