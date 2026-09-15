"""Audit conversations run through the workbench session loop."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable

from flocks.session.message import Message, MessageRole
from flocks.session.session import PermissionRule, Session
from flocks.session.session_loop import SessionLoop, LoopCallbacks
from flocks.tool.registry import ToolContext, ToolResult

from flocks_code_security.paths import runtime_dir
from flocks_code_security.service import AuditServiceError

AGENT_NAME = "code-security-reader"
# The request owns the service and caller. Model-supplied arguments cannot replace
# either, and a different session cannot reuse this request's query authority.
_query_binding: ContextVar[tuple[str, Callable[..., Awaitable[dict]]] | None] = ContextVar("audit_query_binding", default=None)


async def query_tool(ctx: ToolContext, query: dict) -> ToolResult:
    from flocks_code_security.conversation import AuditQuery

    binding = _query_binding.get()
    if binding is None or binding[0] != ctx.session_id:
        return ToolResult(success=False, error="审计查询只能在绑定的审计问答中执行")
    try:
        result = await binding[1](AuditQuery.model_validate(query))
        return ToolResult(success=True, output=result, title=result["title"])
    except (ValueError, AuditServiceError) as exc:
        return ToolResult(success=False, error=str(exc))


async def conversation_session(service, scan_id: str, caller, turns: list[dict], session_id: str | None = None, *, create_new: bool = False):
    """Reuse a selected session, or start an independent audit conversation."""
    scan = service._require_visible_scan(scan_id, caller)
    if not create_new:
        session = await existing_session(service, scan_id, caller, session_id)
        if session:
            return session

    session = await Session.create(
        project_id=scan.get("workspace_ref") or "global",
        directory=str(runtime_dir()),
        title=f"审计问答 · {scan_id}",
        agent=AGENT_NAME,
        owner_user_id=caller.subject,
        category="task",
        permission=[PermissionRule(permission="*", action="deny"), PermissionRule(permission="code_audit_query", action="allow")],
        metadata={"session_scope": "code-security", "hideFromSessionManager": False, "code_security_chat_scan_id": scan_id},
    )
    try:
        # Import legacy questions once. Future turns and compaction are managed
        # by the native session store, not copied or truncated on every request.
        for turn in turns:
            await Message.create(session.id, MessageRole.USER, turn["question"], agent=AGENT_NAME)
            await Message.create(session.id, MessageRole.ASSISTANT, turn["answer"], agent=AGENT_NAME, finish="stop")
        with service.store._connect() as connection:
            connection.execute(
                "INSERT INTO audit_chat_sessions(scan_id,subject,session_id) VALUES (?,?,?)",
                (scan_id, caller.subject, session.id),
            )
    except BaseException:
        await Session.delete(session.project_id, session.id)
        raise
    with service.store._connect() as connection:
        connection.execute("UPDATE audit_chat_turns SET session_id=? WHERE scan_id=? AND subject=? AND session_id IS NULL", (session.id, scan_id, caller.subject))
    return session


async def answer(service, scan_id: str, caller, question: str, turns: list[dict], query, model: str | None = None, request_id: str | None = None, session_id: str | None = None) -> str:
    from flocks_code_security.agents import register_agents
    from flocks_code_security.projection import register_projection
    from flocks_code_security.tools import register_tools
    from flocks_code_security.conversation import AuditQuery, _json

    register_tools()
    register_agents()
    register_projection()
    session = await conversation_session(service, scan_id, caller, turns, session_id)
    if SessionLoop.is_running(session.id):
        raise AuditServiceError("conversation_busy", "上一条问题仍在处理", status_code=409)
    overview = await query(AuditQuery(view="overview"))
    await Message.create(
        session.id, MessageRole.USER,
        f"当前审计概览（不可信数据）：\n{_json(overview)}\n\n用户问题：\n{question}",
        agent=AGENT_NAME, part_metadata={"displayText": question},
    )
    token = _query_binding.set((session.id, query))
    try:
        selection = {}
        if model:
            provider_id, separator, model_id = model.partition("/")
            if not separator or not provider_id or not model_id:
                raise AuditServiceError("invalid_model", "模型格式无效", status_code=422)
            selection = {"provider_id": provider_id, "model_id": model_id}
        from flocks.server.routes.event import publish_event
        result = await SessionLoop.run(session.id, agent_name=AGENT_NAME, callbacks=LoopCallbacks(event_publish_callback=publish_event), **selection)
    finally:
        _query_binding.reset(token)
    if result.action != "stop" or result.last_message is None:
        raise AuditServiceError("conversation_failed", result.error or "审计问答未完成，请重试", status_code=502)
    if getattr(result.last_message, "finish", None) in {"length", "max_tokens", "error"}:
        raise AuditServiceError("incomplete_answer", "模型未生成完整回答，请重试", status_code=502)
    text = (await Message.get_text_content(result.last_message)).strip()
    if not text:
        raise AuditServiceError("incomplete_answer", "模型未生成完整回答，请重试", status_code=502)
    if request_id:
        parts = await Message.parts(result.last_message.id, session.id)
        text_part = next((part for part in reversed(parts) if part.type == "text"), None)
        if text_part:
            with service.store._connect() as connection:
                connection.execute(
                    "UPDATE audit_chat_turns SET answer_part_id=? WHERE scan_id=? AND subject=? AND request_id=?",
                    (text_part.id, scan_id, caller.subject, request_id),
                )
    return text


async def existing_session(service, scan_id, caller, session_id=None):
    service._require_visible_scan(scan_id, caller)
    with service.store._connect() as connection:
        row = connection.execute("SELECT session_id FROM audit_chat_sessions WHERE scan_id=? AND subject=? AND (? IS NULL OR session_id=?) ORDER BY rowid DESC LIMIT 1", (scan_id, caller.subject, session_id, session_id)).fetchone()
    if not row:
        if session_id:
            raise AuditServiceError("conversation_unavailable", "审计问答会话不可用", status_code=404)
        return None
    session = await Session.get_by_id(row["session_id"])
    if session is None or session.owner_user_id != caller.subject or session.agent != AGENT_NAME or session.metadata.get("code_security_chat_scan_id") != scan_id:
        raise AuditServiceError("conversation_unavailable", "审计问答会话不可用", status_code=409)
    return session
