"""SOC case commands through Flocks' existing authenticated tool lifecycle.

Case approval records a human decision about an immutable plan. It never grants
permission to bypass a device tool's own policy, licence or audit hooks.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, get_current_auth_user
from flocks.auth.service import AuthService
from flocks.hooks.execution import current_execution_context
from flocks.session.execution_profile import profile_from_session
from flocks.session.message import Message
from flocks.session.policy import SessionPolicy
from flocks.session.session import Session
from flocks.tool.registry import (
    ParameterType, ToolCategory, ToolContext, ToolParameter, ToolRegistry, ToolResult,
)
from flocks.utils.id import Identifier


def _load_store():
    path = Path(__file__).with_name("_store.py").resolve()
    source = path.read_bytes()
    identity = hashlib.sha256(str(path).encode() + b"\x00" + source).hexdigest()[:24]
    name = "_flocks_soc_case_store_" + identity
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("SOC case store is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        # Execute the exact bytes whose identity we cached. A Hub update can
        # replace a same-length helper within the filesystem timestamp window;
        # SourceFileLoader may otherwise reuse its stale bytecode cache.
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


_domain = _load_store()
Actor, CaseError, CaseStore = _domain.Actor, _domain.CaseError, _domain.CaseStore
QUERIES = frozenset({"list", "get", "recall"})
OPERATIONS = (
    "list", "get", "recall", "create", "update", "add_evidence", "add_hypothesis",
    "decide_hypothesis", "propose_action", "decide_action", "execute_action",
    "verify_action", "record_review", "add_improvement", "add_improvement_evidence",
    "update_improvement", "close", "reopen",
)
HUMAN_OPERATIONS = frozenset({"decide_action", "verify_action"})
_NO_SESSION = frozenset({"", "default", "http-tool"})
_SECRET_KEYS = frozenset({
    "authorization", "cookie", "setcookie", "password", "passwd", "secret",
    "token", "apikey", "accesskey", "privatekey", "clientsecret", "credentials",
    "accesstoken", "refreshtoken", "secretkey", "accesskeyid", "accesskeysecret",
})
_DATA_HELP = """Operation-specific data (never pass actor, role, session or credentials):
list/recall: query (text search), status, limit (max 100), cursor?; get: limit, cursor?, timeline_cursor?.
Pages contain complete records within a byte budget. Continue with next_cursor and, for get, the
independent timeline_next_cursor. Omit cursors for the first page; pass returned opaque strings to
continue and "end" for an exhausted stream (a null next cursor). On cursor_stale discard accumulated
pages and restart from the first page. Legacy offset/timeline_offset are supported only through 10000;
prefer cursors and never infer the next position from limit. Read all needed pages before conclusions.
create: title, summary?, severity=low|medium|high|critical, alert_id? (snapshot existing alert).
update: title?, summary?, assignee?, status? (forward transitions only).
add_evidence: title, source, content, observed_at?, query?, entity?, supersedes_id?.
add_hypothesis: statement, evidence_ids, gaps?.
decide_hypothesis: hypothesis_id, verdict=supported|refuted|inconclusive, rationale, evidence_ids, gaps?.
propose_action: title, tool_name (registered device tool), parameters (include explicit device_id),
target, reason, evidence_ids, reversible, rollback_plan, verification_plan. The complete plan is frozen.
Use canonical device parameter names and types; a policy patch or coercion that changes the approved
arguments blocks execution and requires a corrected plan.
decide_action: action_id, decision=approve|reject, reason. Authenticated human UI only; agents cannot approve.
execute_action: action_id ONLY; requires a writable real session/message in the tool HTTP envelope or agent
context. Uses the stored approved plan and all device tool gates. A blocked action may be retried with a
NEW idempotency key in the SAME session after policy approval; running/unknown actions cannot be retried.
verify_action: action_id, verdict=verified|failed|inconclusive, summary, fresh evidence_ids. Human only.
For an unknown/running attempt proven NOT executed, also set execution_occurred=false and verdict=failed;
this permits a human rejection, never an automatic retry.
If fresh evidence proves execution occurred but its effect failed or is inconclusive, explicitly set
execution_occurred=true with that verdict. Omit this field while the execution fact remains unknown.
record_review: root_cause, lessons_learned, recommendations, evidence_ids.
add_improvement: title, owner, due_at?, acceptance_criteria.
add_improvement_evidence: improvement_id plus the add_evidence fields above. Evidence is scoped to that
improvement; it cannot establish an investigation verdict, action outcome or case review.
update_improvement: improvement_id, status=open|in_progress|verified|deferred, result?, evidence_ids?.
Only humans may mark an improvement verified. Improvement updates and their scoped evidence may be
added after case closure; this does not reopen or rewrite the incident conclusion.
close: category=true_positive|false_positive|inconclusive, reason, accepted_gaps?; requires review and
resolved actions/hypotheses. reopen: reason. Read get after mutations to obtain the current case version.
"""


def _error(code: str, message: str, status_code: int = 400) -> ToolResult:
    return ToolResult(
        success=False, error=message,
        output={"ok": False, "error": {"code": code, "message": message, "status_code": status_code}},
        metadata={"code": code, "status_code": status_code},
    )


def _input_data(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CaseError("invalid_data", "data must be a JSON object")
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CaseError("invalid_data", "data must contain finite JSON values") from exc
    if len(encoded.encode()) > 65536:
        raise CaseError("input_too_large", "Case commands accept at most 64 KiB of data")
    forbidden = {"actor", "user_id", "principal", "kind", "is_admin", "session_id", "execution_profile"}
    if forbidden.intersection(data):
        raise CaseError("untrusted_identity", "Identity and session must come from the authenticated runtime")
    return json.loads(encoded)


async def _actor_for(ctx: ToolContext, operation: str):
    # These markers are created by server/routes/tool.py and the streaming
    # processor, never accepted as tool arguments. Auth context alone is not
    # human proof: an agent task can inherit its request's ContextVars.
    is_http = isinstance(ctx.extra.get("http_permission_denied"), dict)
    workflow_context = ctx.extra.get("workflow_context")
    is_agent = ctx.extra.get("agent_execution_session") is True or (
        isinstance(workflow_context, dict) and workflow_context.get("source") == "workflow_runtime"
    )
    if not is_http and not is_agent:
        raise CaseError("untrusted_context", "Use the authenticated tool API or an agent session", 403)
    principal = get_current_auth_user()
    session = None
    session_id = str(ctx.session_id or "").strip()
    if session_id not in _NO_SESSION:
        session = await Session.get_by_id(session_id)
        if session is None:
            raise CaseError("session_not_found", "Execution session does not exist", 404)
        if not await Message.get(session_id, str(ctx.message_id or "")):
            raise CaseError("message_not_found", "Execution message does not belong to this session", 403)
    if principal is None and is_agent and session is not None:
        # Background agent work resolves a current account from the stored
        # session owner; request-supplied profiles cannot mint an identity.
        owner = str(session.owner_user_id or "")
        if owner and owner != API_TOKEN_SERVICE_USER_ID:
            account = await AuthService.get_user_by_id(owner)
            principal = account.to_auth_user() if account is not None else None
    if principal is None or principal.status != "active" or principal.must_reset_password:
        raise CaseError("authentication_required", "An active authenticated account is required", 403)
    if is_agent and session is None:
        raise CaseError("session_required", "Agent case commands require a persisted session", 403)
    if session is not None:
        allowed = SessionPolicy.can_read(session, principal) if operation in QUERIES else SessionPolicy.can_write(session, principal)
        if not allowed:
            raise CaseError("session_forbidden", "This account cannot access the execution session", 403)
    service = principal.id == API_TOKEN_SERVICE_USER_ID
    kind = "service" if service else "agent" if is_agent else "human"
    if operation in HUMAN_OPERATIONS and kind != "human":
        raise CaseError("human_required", "Approval and outcome verification require a human in the authenticated UI", 403)
    if operation == "execute_action" and session is None:
        raise CaseError("session_required", "Device actions require a writable real session and message", 403)
    actor = Actor(user_id=principal.id, kind=kind, is_admin=principal.role == "admin" and not service)
    return actor, session


def _check_parameters(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise CaseError("invalid_parameters", "Device parameters exceed the nesting limit")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _SECRET_KEYS or normalized.endswith(("password", "secret", "token", "privatekey")):
                raise CaseError("sensitive_parameters", "Store credentials in the device configuration, never in a case plan")
            _check_parameters(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_parameters(item, depth + 1)
    elif isinstance(value, str) and (
        re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+|\{env:|\$\{", value, re.I)
        or re.search(r"[a-z][a-z0-9+.-]*://[^/\s]+:[^/\s]+@", value, re.I)
    ):
        raise CaseError("sensitive_parameters", "Case plans cannot contain credentials or environment references")


_JSON_TYPES = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def _check_schema(tool, params: dict[str, Any]) -> None:
    """The frozen arguments must already be exactly what the tool accepts.

    Registry would otherwise refuse them before the handler runs, or rename and
    coerce them, which the frozen-plan guard then blocks; either way an
    approved plan could never run.
    """
    schema = tool.info.get_schema()
    properties = schema.properties if isinstance(schema.properties, dict) else {}
    unknown = sorted(set(params) - set(properties)) if properties else []
    if unknown:
        raise CaseError("invalid_parameters", f"Unknown parameters for {tool.info.name}: {', '.join(unknown)}")
    missing = sorted(name for name in schema.required if name not in params)
    if missing:
        raise CaseError("invalid_parameters", f"Missing parameters for {tool.info.name}: {', '.join(missing)}")
    for name, value in params.items():
        declared = (properties.get(name) or {}).get("type")
        kinds = [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []
        checks = [_JSON_TYPES[kind] for kind in kinds if kind in _JSON_TYPES]
        if checks and not any(check(value) for check in checks):
            raise CaseError("invalid_parameters", f"Parameter {name} must be {' or '.join(map(str, kinds))}")


def _device_tool(plan: dict[str, Any]):
    name = plan.get("tool_name")
    params = plan.get("parameters")
    if not isinstance(name, str) or not isinstance(params, dict):
        raise CaseError("invalid_action", "A device action needs tool_name and parameters")
    tool = ToolRegistry.get(name)
    if tool is None or tool.info.source != "device":
        raise CaseError("unsupported_tool", "Only registered security device tools may execute case actions")
    if not tool.info.enabled:
        raise CaseError("tool_disabled", "The action's device tool is disabled", 409)
    if not isinstance(params.get("device_id"), str) or not params["device_id"].strip():
        raise CaseError("device_required", "Freeze an explicit parameters.device_id in the action plan")
    _check_parameters(params)
    _check_schema(tool, params)
    return tool


def _child_context(ctx: ToolContext, session, plan: dict[str, Any]) -> ToolContext:
    extra = dict(ctx.extra)
    # A parent tool's permit cannot be carried into a different device call.
    for key in ("tool_gate_decision", "tool_call_id", "tool_source", "tool_category", "http_permission_denied"):
        extra.pop(key, None)
    extra["session_execution_profile"] = profile_from_session(session)
    extension_context = dict(
        extra["extension_context"] if isinstance(extra.get("extension_context"), dict)
        else current_execution_context()
    )
    linkage = {key: plan[key] for key in ("case_id", "plan_hash")}
    linkage["action_id"] = plan["id"]
    extension_context["soc_case"] = linkage
    extra["extension_context"] = extension_context
    extra["soc_case"] = linkage
    expected = copy.deepcopy(plan["parameters"])
    tool = ToolRegistry.get(plan["tool_name"])
    if tool is not None and tool.info.source == "device" and tool.info.provider:
        # Registry binds credentials to this exact device before Tool.execute;
        # device_id is then removed from the actual handler's arguments.
        expected.pop("device_id", None)
    expected_json = json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)

    async def frozen_plan_guard(tool_name: str, parameters: dict[str, Any]) -> bool:
        if tool_name != plan["tool_name"]:
            return False
        # Compare JSON types as well as values (Python's True == 1 must not
        # authorize a policy or coercion change to an approved argument).
        actual = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return actual == expected_json

    return ToolContext(
        session_id=ctx.session_id, message_id=ctx.message_id, agent=ctx.agent,
        call_id=Identifier.create("call"), extra=extra, abort_event=ctx.abort,
        permission_callback=ctx._permission_callback, metadata_callback=ctx._metadata_callback,
        event_publish_callback=ctx.event_publish_callback,
        validated_input_guard=frozen_plan_guard,
    )


def _receipt(
    result: ToolResult | None, child: ToolContext | None, plan: dict[str, Any], outcome: str,
    session_id: str,
) -> dict[str, Any]:
    receipt = {
        "outcome": outcome, "tool_name": plan["tool_name"], "call_id": child.call_id if child else None,
        "session_id": session_id, "device_id": plan["parameters"]["device_id"],
        "verification_required": outcome in {"succeeded", "unknown"},
        "case_id": plan["case_id"], "action_id": plan["id"], "plan_hash": plan["plan_hash"],
    }
    if result is not None:
        # Device output may include sensitive evidence or even echoed headers.
        # Keep a digest and structured policy references, not raw responses.
        try:
            digest = hashlib.sha256()
            for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, default=str).iterencode(result.output):
                digest.update(chunk.encode())
            receipt["output_sha256"] = digest.hexdigest()
        except (TypeError, ValueError, RecursionError):
            receipt["output_digest_unavailable"] = True
        receipt["success"] = bool(result.success)
        meta = result.metadata or {}
        receipt["confirmation_required"] = bool(meta.get("confirmation_required"))
        receipt["blocked_by_hook"] = bool(meta.get("blocked_by_hook"))
        receipt["validated_input_guard_blocked"] = bool(meta.get("validated_input_guard_blocked"))
        decision = meta.get("hook_decision")
        if isinstance(decision, dict):
            receipt["policy"] = {
                key: str(decision[key])[:256]
                for key in ("action", "mode", "grant_ref", "policy_version")
                if isinstance(decision.get(key), (str, int))
            }
    return receipt


def _outcome(result: ToolResult) -> str:
    meta = result.metadata or {}
    if meta.get("confirmation_required") or meta.get("blocked_by_hook") or meta.get("validated_input_guard_blocked"):
        return "blocked"
    if meta.get("schema_precheck"):
        return "unsupported"  # refused by the tool's own argument check; the handler never ran
    if result.success:
        return "succeeded"
    # An unclassified failure after dispatch is ambiguous. It must be
    # reconciled rather than retried automatically.
    return "unknown"


async def _complete(awaitable) -> tuple[Any, bool]:
    """Await ``awaitable`` to its end even if this task is cancelled meanwhile.

    Case store calls commit in a worker thread whatever happens to the caller,
    so their outcome must still be seen: a committed claim nobody finishes
    keeps its action running for good.  Returns the result and whether a
    cancellation arrived, which the caller re-raises.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise


async def _execute(store, actor, ctx, session, case_id, expected_version, idempotency_key, data):
    if set(data) != {"action_id"} or not isinstance(data.get("action_id"), str):
        raise CaseError("invalid_execute_request", "execute_action accepts data.action_id only; stored parameters cannot be overridden")
    try:
        guard_parameter = inspect.signature(ToolContext).parameters.get("validated_input_guard")
    except (TypeError, ValueError):
        guard_parameter = None
    if guard_parameter is None or guard_parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
        # Older cores may install this Hub plugin but cannot bind the approved
        # plan to final policy-patched arguments. Refuse before any DB claim.
        raise CaseError("core_update_required", "Update Flocks Core to support guarded case action execution", 409)
    claim, cancelled = await _complete(asyncio.to_thread(
        store.claim_action, actor, case_id, data["action_id"], expected_version,
        idempotency_key, session_id=ctx.session_id,
    ))
    if not claim["claimed"]:
        if cancelled:
            raise asyncio.CancelledError
        return {"case": claim["case"], "record": claim["action"], "executed": False, "replayed": True}
    plan = claim["action"]
    child = None
    result = None
    outcome = "blocked"  # until the device call is dispatched
    dispatched = False
    if not cancelled:
        try:
            child = _child_context(ctx, session, plan)
            _device_tool(plan)
            if not ctx.aborted:
                parameters = copy.deepcopy(plan["parameters"])
                dispatched = True
                outcome = "unknown"
                result = await ToolRegistry.execute(plan["tool_name"], ctx=child, **parameters)
                outcome = _outcome(result)
        except CaseError:
            outcome = "unsupported"
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            pass  # "unknown" once dispatched, "blocked" before

    async def settle():
        receipt = await asyncio.to_thread(_receipt, result, child, plan, outcome, ctx.session_id)
        receipt["dispatch_started"] = dispatched
        finished = await asyncio.to_thread(
            store.finish_action, actor, case_id, data["action_id"], claim["claim_token"], outcome, receipt,
        )
        return receipt, finished

    (receipt, finished), interrupted = await _complete(settle())
    if cancelled or interrupted:
        raise asyncio.CancelledError
    return {**finished, "executed": outcome == "succeeded", "execution_outcome": outcome, "receipt": receipt}


@ToolRegistry.register_function(
    name="soc_case_manage",
    description=(
        "Manage Flocks SOC cases, immutable evidence, hypotheses, human-reviewed response plans, "
        "verified outcomes and retrospective improvements. Case approval never bypasses device, Pro "
        "or Go execution policy. Agents may investigate and propose; only authenticated humans may "
        "approve/reject actions or verify their effect. Every mutation requires an idempotency key "
        "and current expected_version (except create)."
    ),
    description_cn="管理 SOC 事件、证据、调查假设、处置计划与复盘。人工审批和效果核验不能由 Agent 代做；设备执行仍受原工具与 Pro/Go 策略约束。",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(name="operation", type=ParameterType.STRING, description="Case operation to perform", required=True, enum=list(OPERATIONS)),
        ToolParameter(name="case_id", type=ParameterType.STRING, required=False, description="Persistent case UUID; required except list, recall and create", default=""),
        ToolParameter(name="expected_version", type=ParameterType.INTEGER, required=False, description="Current case.version for every mutation except create; refresh get after a conflict"),
        ToolParameter(name="idempotency_key", type=ParameterType.STRING, required=False, description="Unique non-secret command key. Required for mutations. Same key/content safely replays; use a new key for a new intended command", default=""),
        ToolParameter(name="data", type=ParameterType.OBJECT, required=False, description=_DATA_HELP, json_schema={"type": "object", "additionalProperties": True}),
    ],
    tags=["soc", "cases", "investigation", "evidence", "response", "retrospective"],
)
async def soc_case_manage(
    ctx: ToolContext, operation: str, case_id: str = "", expected_version: int | None = None,
    idempotency_key: str = "", data: dict[str, Any] | None = None,
) -> ToolResult:
    try:
        if operation not in OPERATIONS:
            raise CaseError("unknown_operation", "Unsupported SOC case operation")
        payload = _input_data(data)
        actor, session = await _actor_for(ctx, operation)
        if operation == "propose_action":
            _device_tool(payload)
        store = CaseStore()
        if operation == "execute_action":
            value = await _execute(store, actor, ctx, session, case_id, expected_version, idempotency_key, payload)
        else:
            value = await asyncio.to_thread(
                store.dispatch, operation, actor, case_id=case_id,
                expected_version=expected_version, idempotency_key=idempotency_key, data=payload,
            )
        # The store budgets complete records per page. Compact serialization
        # keeps structured pages below Registry's generic line/byte truncator.
        return ToolResult(
            success=True, output=json.dumps({"ok": True, **value}, ensure_ascii=False, separators=(",", ":")),
            metadata={"operation": operation},
        )
    except CaseError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    except Exception:
        # Internal failures can contain database connection details. Running
        # claims remain unreclaimable until explicitly reconciled.
        return _error("case_unavailable", "SOC case operation could not be completed; refresh before retrying", 503)
