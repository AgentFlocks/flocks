"""Read-only audit transcripts and grounded result conversations.

The answer model can only query evidence belonging to the current audit.
Session data and artifacts are evidence, never instructions.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flocks.provider.provider import ChatMessage, Provider
from flocks.session.message import Message
from flocks.session.session import Session
from flocks_code_security.cli import _resolve_model
from flocks_code_security.service import AuditServiceError, PUBLIC_PHASES

MAX_REPLY_TOKENS = 4096
QUERY_PAGE_CHARS = 12_000

# Same categories as the phase workspace. These are shared scan artifacts,
# not snapshots of individual phase executions.
PHASE_ARTIFACTS = {
    "snapshot": {"snapshot_summary"},
    "threat_modeling": {"threat_model"},
    "baseline": {"candidate_index"},
    "investigation": {"candidate_index"},
    "verification": {"verification_index"},
    "dynamic_validation": {"dynamic_validation"},
    "adjudication": {"adjudication"},
    "targeted_rescan": {"candidate_index", "verification_index"},
    "poc_generation": {"poc_generation"},
    "finalization": {"report_markdown", "report_json", "sarif", "findings", "coverage", "scan_manifest"},
}


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["overview", "phase", "artifact", "session"]
    phase_run_id: str | None = None
    artifact_kind: str | None = None
    session_id: str | None = None
    offset: int = Field(default=0, ge=0, strict=True, description="Character offset; use next_offset to continue reading.")



def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def public_messages(rows: list[Any]) -> list[dict[str, Any]]:
    """Allowlist workbench-visible content; omit system and provider metadata."""
    result = []
    for row in rows:
        value = row.model_dump() if hasattr(row, "model_dump") else row
        info = value["info"]
        if info.get("role") not in {"user", "assistant"}:
            continue
        parts = []
        for index, part in enumerate(value.get("parts", [])):
            if part.get("synthetic") or part.get("ignored"):
                continue
            visible = {"id": part.get("id") or f"{info['id']}-{index}"}
            timing = part.get("time")
            if isinstance(timing, dict):
                visible["time"] = {k: timing[k] for k in ("start", "end") if k in timing}
            if part.get("type") in {"text", "reasoning", "thinking"}:
                parts.append({**visible, "type": part["type"], "text": part.get("text") or part.get("thinking", "")})
            elif part.get("type") == "tool":
                state = part.get("state", {})
                parts.append(
                    {
                        **visible,
                        "type": "tool",
                        "callID": part.get("callID"),
                        "title": state.get("title"),
                        "time": {k: state["time"][k] for k in ("start", "end") if k in state["time"]} if isinstance(state.get("time"), dict) else None,
                        "tool": part.get("tool"),
                        "status": state.get("status"),
                        "input": state.get("input"),
                        "output": state.get("output"),
                        "error": state.get("error"),
                    }
                )
        if parts:
            result.append({"id": info["id"], "role": info["role"], "time": info.get("time", {}), "finish": info.get("finish"), "parts": parts})
    return result


async def _read_session(session_id: str, scan: dict, *, parent: bool = False) -> list[dict] | None:
    session = await Session.get_by_id_unfiltered(session_id)
    if session is None:
        return None
    if not parent and (session.metadata or {}).get("code_security_scan_id") != scan["scan_id"]:
        return None
    return public_messages(await Message.list_with_parts(session_id, include_archived=True))


async def phase_sessions(
    service, scan_id: str, caller, phase_run_id: str | None = None, *, include_messages: bool = True
) -> dict:
    scan = service._require_visible_scan(scan_id, caller)
    phases = service.store.list_phase_runs(scan_id)
    if phase_run_id and not any(p["phase_run_id"] == phase_run_id for p in phases):
        raise AuditServiceError("phase_not_found", "Phase was not found", status_code=404)
    if service.read_only:
        return {"items": [], "complete": False, "reason": "isolated_session_store"}
    if json.loads(scan.get("cleanup_summary_json", "{}")).get("status") == "completed":
        return {"items": [], "complete": False, "reason": "sessions_cleaned"}
    attempts = await asyncio.to_thread(service.store.phase_session_attempts, scan_id)
    items = []
    complete = True
    for attempt in attempts:
        run_id = attempt.get("phase_run_id")
        if not run_id:
            # Legacy data may be associated only when the phase has a single run.
            phase = PUBLIC_PHASES.get(attempt["phase"], attempt["phase"])
            matching = [p for p in phases if p["phase"] == phase]
            run_id = matching[0]["phase_run_id"] if len(matching) == 1 else None
        if run_id is None:
            complete = False
        if phase_run_id and run_id != phase_run_id:
            continue
        messages = await _read_session(attempt["session_id"], scan) if include_messages else None
        if include_messages and messages is None:
            complete = False
        items.append(
            {
                "attempt_id": attempt["attempt_id"],
                "session_id": attempt["session_id"],
                "phase_run_id": run_id,
                "work_unit_id": attempt["work_unit_id"],
                "ordinal": attempt["ordinal"],
                "role": attempt["role"],
                "status": attempt["status"],
                "provider_id": attempt.get("provider_id"),
                "model_id": attempt.get("model_id"),
                **({"available": messages is not None, "messages": messages or []} if include_messages else {}),
            }
        )
    return {"items": items, "complete": complete, "reason": None if complete else "sessions_incomplete"}


async def audit_context(service, scan_id: str, caller) -> dict:
    """Check result readiness without reading transcripts or artifact bodies."""
    detail = await service.get_scan(scan_id, caller)
    if service.read_only:
        raise AuditServiceError("context_not_ready", "独立批次存储暂不支持保存问答", status_code=409)
    if detail["scan"]["lifecycle_status"] != "completed":
        raise AuditServiceError("audit_not_complete", "全部审计流程完成后才能会话", status_code=409)
    if detail["scan"]["integrity_status"] != "valid":
        raise AuditServiceError("context_not_ready", "审计产物尚未通过完整性校验", status_code=409)
    if any(p["status"] not in {"completed", "skipped", "not_runnable"} for p in detail["phase_runs"]):
        raise AuditServiceError("context_not_ready", "存在未完成的审计阶段", status_code=409)
    attempts = await asyncio.to_thread(service.store.phase_session_attempts, scan_id)
    if any(a["status"] in {"running", "pending", "recovering"} for a in attempts):
        raise AuditServiceError("context_not_ready", "审计执行会话尚未结束", status_code=409)
    return detail


def query_page(source_id: str, title: str, content: Any, offset: int, **reference) -> dict:
    text = content if isinstance(content, str) else _json(content)
    end = min(offset + QUERY_PAGE_CHARS, len(text))
    return {
        "id": source_id,
        "title": title,
        **reference,
        "content": text[offset:end],
        "offset": offset,
        "total_chars": len(text),
        "next_offset": end if end < len(text) else None,
        "has_more": end < len(text),
    }


async def code_audit_query(service, scan_id: str, caller, query: AuditQuery) -> dict:
    """Read bounded evidence pages; the caller and scan are server-bound."""
    scan = service._require_visible_scan(scan_id, caller)
    if query.view == "session":
        sessions = await phase_sessions(service, scan_id, caller, include_messages=False)
        item = next((s for s in sessions["items"] if s["session_id"] == query.session_id), None)
        coordinator = bool(scan.get("task_owner_token")) and query.session_id == scan.get("parent_session_id")
        if service.read_only or json.loads(scan.get("cleanup_summary_json", "{}")).get("status") == "completed":
            raise AuditServiceError("session_unavailable", "会话记录不可用，可查询保留的审计产物", status_code=409)
        if item is None and not coordinator:
            raise AuditServiceError("session_not_found", "会话不属于当前审计或已不可用", status_code=404)
        messages = await _read_session(query.session_id, scan, parent=coordinator)
        if messages is None:
            raise AuditServiceError("session_unavailable", "会话记录已不可用，可查询保留的审计产物", status_code=409)
        return query_page(
            f"session:{query.session_id}", f"会话 {query.session_id}", messages, query.offset,
            phase_run_id=item["phase_run_id"] if item else None,
        )
    if query.view == "artifact":
        if not query.artifact_kind:
            raise AuditServiceError("invalid_query", "请提供 artifact_kind")
        artifact = await service.get_artifact(scan_id, query.artifact_kind, caller)
        return query_page(
            f"artifact:{query.artifact_kind}", query.artifact_kind,
            {"state": artifact.get("state"), "content": artifact["content"]}, query.offset,
        )
    detail = await service.get_scan(scan_id, caller)
    if query.view == "phase":
        phase = next((p for p in detail["phase_runs"] if p["phase_run_id"] == query.phase_run_id), None)
        if phase is None:
            raise AuditServiceError("phase_not_found", "Phase was not found", status_code=404)
        sessions = await phase_sessions(service, scan_id, caller, query.phase_run_id, include_messages=False)
        content = {
            "phase": phase,
            "artifacts": [a for a in detail["artifacts"] if a["kind"] in PHASE_ARTIFACTS.get(phase["phase"], set())],
            "artifact_scope": "共享的审计产物，不代表本次阶段执行的独立快照",
            "sessions": sessions,
        }
        return query_page(f"phase:{query.phase_run_id}", phase["phase"], content, query.offset, phase_run_id=query.phase_run_id)
    content = {
        "scan": detail["scan"],
        "counts": detail.get("counts", {}),
        "phases": [{k: p.get(k) for k in ("phase_run_id", "phase", "ordinal", "status")} for p in detail["phase_runs"]],
        "artifacts": detail["artifacts"],
    }
    if scan.get("task_owner_token"):
        content["coordinator_session_id"] = scan["parent_session_id"]
    return query_page("audit", "审计概览", content, query.offset)


def history(service, scan_id: str, caller, session_id: str | None = None) -> list[dict]:
    service._require_visible_scan(scan_id, caller)
    if service.read_only:
        return []
    with service.store._connect() as connection:
        if session_id is None:
            mapped = connection.execute("SELECT session_id FROM audit_chat_sessions WHERE scan_id=? AND subject=? ORDER BY rowid DESC LIMIT 1", (scan_id, caller.subject)).fetchone()
            session_id = mapped[0] if mapped else None
        rows = connection.execute(
            "SELECT request_id, question, answer, sources_json, created_at, answer_part_id FROM audit_chat_turns "
            "WHERE scan_id = ? AND subject = ? AND status = 'completed' AND session_id IS ? ORDER BY created_at, request_id",
            (scan_id, caller.subject, session_id),
        ).fetchall()
    return [
        {
            "request_id": row["request_id"],
            "question": row["question"],
            "answer": row["answer"],
            "answer_part_id": row["answer_part_id"],
            "sources": json.loads(row["sources_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def readiness(service, scan_id: str, caller, session_id: str | None = None) -> dict:
    service._require_visible_scan(scan_id, caller)
    turns = history(service, scan_id, caller, session_id)
    try:
        await audit_context(service, scan_id, caller)
        from flocks_code_security.chat_runtime import existing_session
        session = await existing_session(service, scan_id, caller, session_id)
        from flocks.session.session_loop import SessionLoop
        with service.store._lock, service.store._connect() as connection:
            if not session or not SessionLoop.is_running(session.id):
                connection.execute(
                    "UPDATE audit_chat_turns SET status='failed' WHERE scan_id=? AND subject=? AND status='pending' AND session_id IS ? AND created_at < ?",
                    (scan_id, caller.subject, session.id if session else None, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()),
                )
            pending = connection.execute("SELECT 1 FROM audit_chat_turns WHERE scan_id=? AND subject=? AND status='pending' AND session_id IS ?", (scan_id, caller.subject, session.id if session else None)).fetchone()
        with service.store._connect() as connection:
            sessions = [dict(row) for row in connection.execute("SELECT session_id FROM audit_chat_sessions WHERE scan_id=? AND subject=? ORDER BY rowid DESC", (scan_id, caller.subject))]
        return {"sessions": sessions, "ready": True, "reason": None, "turns": turns, "session_id": session.id if session else None, "processing": bool(pending)}
    except AuditServiceError as exc:
        if exc.status_code not in {409, 413}:
            raise
        return {"ready": False, "reason": str(exc), "code": exc.code, "turns": turns}


async def model_reply(messages: list[ChatMessage], model: str | None = None) -> str:
    """One-shot configuration helper; result conversations use SessionLoop."""
    try:
        provider_id, model_id = await _resolve_model(model)
        provider = Provider.get(provider_id)
        if provider is None:
            raise ValueError("Provider unavailable")
    except Exception as exc:
        raise AuditServiceError("model_unavailable", "会话模型不可用，请检查模型配置", status_code=503) from exc
    try:
        response = await asyncio.wait_for(
            provider.chat(model_id=model_id, messages=messages, tools=None, max_tokens=MAX_REPLY_TOKENS), timeout=120
        )
    except TimeoutError as exc:
        raise AuditServiceError("model_timeout", "模型响应超时，请重试", status_code=504) from exc
    if response.finish_reason in {"length", "max_tokens", "tool_calls"} or not response.content or response.tool_calls:
        raise AuditServiceError("incomplete_answer", "模型未生成完整回答，请重试", status_code=502)
    return response.content


async def ask(service, scan_id: str, caller, question: str, request_id: str, model: str | None = None, session_id: str | None = None) -> dict:
    await audit_context(service, scan_id, caller)
    from flocks_code_security.chat_runtime import existing_session
    selected = await existing_session(service, scan_id, caller, session_id)
    session_id = selected.id if selected else None
    turns = history(service, scan_id, caller, session_id)
    with service.store._lock, service.store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        from flocks.session.session_loop import SessionLoop
        if not selected or not SessionLoop.is_running(selected.id):
            connection.execute(
                "UPDATE audit_chat_turns SET status='failed' WHERE scan_id=? AND subject=? AND status='pending' AND session_id IS ? AND created_at < ?",
                (scan_id, caller.subject, session_id, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()),
            )
        existing = connection.execute(
            "SELECT status, question, session_id FROM audit_chat_turns WHERE scan_id=? AND subject=? AND request_id=?",
            (scan_id, caller.subject, request_id),
        ).fetchone()
        if existing:
            if existing["question"] != question or existing["session_id"] != session_id:
                raise AuditServiceError("request_conflict", "请求编号已用于其他问题", status_code=409)
            if existing["status"] == "completed":
                return next(turn for turn in turns if turn["request_id"] == request_id)
            if existing["status"] == "pending":
                raise AuditServiceError("request_in_progress", "该请求正在处理，请稍后重试", status_code=409)
        if connection.execute(
            "SELECT 1 FROM audit_chat_turns WHERE scan_id=? AND subject=? AND status='pending' AND session_id IS ?",
            (scan_id, caller.subject, session_id),
        ).fetchone():
            raise AuditServiceError("conversation_busy", "上一条问题仍在处理", status_code=409)
        connection.execute(
            "INSERT INTO audit_chat_turns(scan_id,subject,request_id,question,status,created_at,session_id) VALUES (?,?,?,?,'pending',?,?) ON CONFLICT(scan_id,subject,request_id) DO UPDATE SET status='pending',created_at=excluded.created_at",
            (scan_id, caller.subject, request_id, question, datetime.now(timezone.utc).isoformat(), session_id),
        )
    try:
        sources = {source["id"]: source for turn in turns for source in turn["sources"]}

        queried_sources = {}

        async def query(args: AuditQuery) -> dict:
            result = await code_audit_query(service, scan_id, caller, args)
            sources[result["id"]] = {k: v for k, v in result.items() if k in {"id", "title", "phase_run_id"}}
            queried_sources[result["id"]] = sources[result["id"]]
            return result

        from flocks_code_security.chat_runtime import answer as session_answer

        answer = await session_answer(service, scan_id, caller, question, turns, query, request_id=request_id, session_id=session_id, **({"model": model} if model else {}))
        # Recheck access and result integrity after model latency, without loading evidence.
        await audit_context(service, scan_id, caller)
        cited = [s for s in sources.values() if f"[{s['id']}]" in answer]
        unknown = re.findall(r"\[((?:artifact|session|phase):[^\]]+|events|audit)\]", answer)
        if any(value not in sources for value in unknown) or (not cited and not queried_sources):
            raise AuditServiceError("invalid_citation", "回答包含无法验证的引用，请重试", status_code=502)
        if not cited:
            # These are tool provenance, not citations invented on the model's behalf.
            cited = list(queried_sources.values())
        with service.store._lock, service.store._connect() as connection:
            connection.execute(
                "UPDATE audit_chat_turns SET status='completed',answer=?,sources_json=? WHERE scan_id=? AND subject=? AND request_id=?",
                (answer, _json(cited), scan_id, caller.subject, request_id),
            )
        return next(turn for turn in history(service, scan_id, caller, session_id) if turn["request_id"] == request_id)
    except BaseException:
        with service.store._lock, service.store._connect() as connection:
            connection.execute(
                "UPDATE audit_chat_turns SET status='failed' WHERE scan_id=? AND subject=? AND request_id=?",
                (scan_id, caller.subject, request_id),
            )
        raise


async def stop(service, scan_id, caller, session_id=None):
    from flocks_code_security.chat_runtime import existing_session
    from flocks.session.session_loop import SessionLoop
    from flocks.session.runner import SessionRunner
    session = await existing_session(service, scan_id, caller, session_id)
    if session:
        SessionLoop.abort(session.id)
        SessionRunner.cancel(session.id)
    return {"stopped": session is not None}


async def create_conversation(service, scan_id, caller):
    await audit_context(service, scan_id, caller)
    with service.store._connect() as connection:
        if connection.execute("SELECT 1 FROM audit_chat_turns WHERE scan_id=? AND subject=? AND status='pending' AND session_id IS NULL", (scan_id, caller.subject)).fetchone():
            raise AuditServiceError("conversation_busy", "上一条问题仍在准备，请稍后新建会话", status_code=409)
    from flocks_code_security.chat_runtime import conversation_session, existing_session
    if await existing_session(service, scan_id, caller) is None:
        legacy = history(service, scan_id, caller)
        if legacy:
            await conversation_session(service, scan_id, caller, legacy)
    session = await conversation_session(service, scan_id, caller, [], create_new=True)
    return await readiness(service, scan_id, caller, session.id)
