"""
Session management tools — 查询、创建、更新、删除 Flocks Session 的元数据。

提供统一工具：
- session_manage(action=...) : list/get/create/update/delete/archive
"""

from __future__ import annotations

from typing import Any, Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.session_manage")


SESSION_MANAGE_ACTIONS = ["list", "get", "create", "update", "delete", "archive"]

SESSION_MANAGE_DESCRIPTION = """\
管理 Flocks Session 元数据。

Use `action` to choose the operation:
- list: 列出 session；可用 project_id/status/category/limit/offset 过滤或分页
- get: 获取单个 session；需要 session_id
- create: 创建 session；可传 title/project_id/directory/agent/parent_id
- update: 更新 session；需要 session_id，可传 title/agent/model/provider/memory_enabled
- delete: 软删除 session 及其子 session；需要 session_id，会请求确认
- archive: 归档或取消归档 session；需要 session_id，archive=false 表示恢复 active
"""


SESSION_MANAGE_PARAMETERS = [
    ToolParameter(
        name="action",
        type=ParameterType.STRING,
        required=True,
        enum=SESSION_MANAGE_ACTIONS,
        description="要执行的 session 操作：list/get/create/update/delete/archive",
    ),
    ToolParameter(
        name="session_id",
        type=ParameterType.STRING,
        required=False,
        description="Session ID；get/update/delete/archive 需要",
    ),
    ToolParameter(
        name="project_id",
        type=ParameterType.STRING,
        required=False,
        description="项目 ID；list 时用于过滤，create 时用于归属项目（默认 default）",
    ),
    ToolParameter(
        name="status",
        type=ParameterType.STRING,
        required=False,
        enum=["active", "archived"],
        description="list 过滤状态：active 或 archived",
    ),
    ToolParameter(
        name="category",
        type=ParameterType.STRING,
        required=False,
        enum=["user", "task"],
        description="list 过滤分类：user（人工会话）或 task（任务触发会话）",
    ),
    ToolParameter(
        name="limit",
        type=ParameterType.INTEGER,
        required=False,
        description="list 最多返回条数（默认 50）",
    ),
    ToolParameter(
        name="offset",
        type=ParameterType.INTEGER,
        required=False,
        description="list 跳过前 N 条（默认 0）",
    ),
    ToolParameter(
        name="title",
        type=ParameterType.STRING,
        required=False,
        description="create/update 的 session 标题",
    ),
    ToolParameter(
        name="directory",
        type=ParameterType.STRING,
        required=False,
        description="create 的工作目录路径（默认当前目录）",
    ),
    ToolParameter(
        name="agent",
        type=ParameterType.STRING,
        required=False,
        description="create/update 的 agent 类型",
    ),
    ToolParameter(
        name="parent_id",
        type=ParameterType.STRING,
        required=False,
        description="create 子 session 时使用的父 session ID",
    ),
    ToolParameter(
        name="model",
        type=ParameterType.STRING,
        required=False,
        description="update 的 model ID",
    ),
    ToolParameter(
        name="provider",
        type=ParameterType.STRING,
        required=False,
        description="update 的 provider ID",
    ),
    ToolParameter(
        name="memory_enabled",
        type=ParameterType.BOOLEAN,
        required=False,
        description="update 时是否启用 memory 系统",
    ),
    ToolParameter(
        name="archive",
        type=ParameterType.BOOLEAN,
        required=False,
        default=True,
        description="archive action: true=归档（默认），false=取消归档",
    ),
]


@ToolRegistry.register_function(
    name="session_manage",
    description=SESSION_MANAGE_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=SESSION_MANAGE_PARAMETERS,
)
async def session_manage(
    ctx: ToolContext,
    action: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    title: Optional[str] = None,
    directory: Optional[str] = None,
    agent: Optional[str] = None,
    parent_id: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    memory_enabled: Optional[bool] = None,
    archive: Optional[bool] = True,
) -> ToolResult:
    """Unified session management tool."""
    if action == "list":
        return await _session_list_impl(
            ctx,
            project_id=project_id,
            status=status,
            category=category,
            limit=limit,
            offset=offset,
        )
    if action == "get":
        if not session_id:
            return ToolResult(success=False, error="action=get 需要 session_id")
        return await _session_get_impl(ctx, session_id=session_id)
    if action == "create":
        return await _session_create_impl(
            ctx,
            title=title,
            project_id=project_id,
            directory=directory,
            agent=agent,
            parent_id=parent_id,
        )
    if action == "update":
        if not session_id:
            return ToolResult(success=False, error="action=update 需要 session_id")
        return await _session_update_impl(
            ctx,
            session_id=session_id,
            title=title,
            agent=agent,
            model=model,
            provider=provider,
            memory_enabled=memory_enabled,
        )
    if action == "delete":
        if not session_id:
            return ToolResult(success=False, error="action=delete 需要 session_id")
        await ctx.ask(
            permission="session_manage",
            patterns=[f"delete:{session_id}"],
            always=[],
            metadata={"action": "delete", "session_id": session_id},
        )
        return await _session_delete_impl(ctx, session_id=session_id)
    if action == "archive":
        if not session_id:
            return ToolResult(success=False, error="action=archive 需要 session_id")
        return await _session_archive_impl(ctx, session_id=session_id, archive=archive)

    return ToolResult(
        success=False,
        error=f"未知 action: {action!r}. 支持: {', '.join(SESSION_MANAGE_ACTIONS)}",
    )


def _session_to_dict(session, bindings: list | None = None) -> dict[str, Any]:
    """Serialize SessionInfo to a readable dict.

    If ``bindings`` is provided (list of SessionBinding for this session),
    a ``channels`` field is appended with the IM platform details.
    """
    d: dict[str, Any] = {
        "id": session.id,
        "slug": session.slug,
        "project_id": session.project_id,
        "title": session.title,
        "status": session.status,
        "category": session.category,
        "agent": session.agent,
        "model": session.model,
        "provider": session.provider,
        "parent_id": session.parent_id,
        "directory": session.directory,
        "memory_enabled": session.memory_enabled,
        "time": {
            "created": session.time.created,
            "updated": session.time.updated,
            "archived": session.time.archived,
        },
        "summary": session.summary.model_dump() if session.summary else None,
    }
    if bindings is not None:
        d["channels"] = [
            {
                "channel_id": b.channel_id,
                "chat_type": b.chat_type.value if b.chat_type else None,
                "chat_id": b.chat_id,
                "account_id": b.account_id,
            }
            for b in bindings
        ]
    return d


async def _enrich_with_channels(sessions_dict: list[dict]) -> list[dict]:
    """Attach channel binding info to a list of serialized session dicts.

    Best-effort: if the binding table is unavailable, sessions are returned as-is
    with an empty ``channels`` list.
    """
    try:
        from flocks.channel.inbound.session_binding import SessionBindingService
        svc = SessionBindingService()
        all_bindings = await svc.list_bindings()
        index: dict[str, list] = {}
        for b in all_bindings:
            index.setdefault(b.session_id, []).append(b)
        for s in sessions_dict:
            s["channels"] = [
                {
                    "channel_id": b.channel_id,
                    "chat_type": b.chat_type.value if b.chat_type else None,
                    "chat_id": b.chat_id,
                    "account_id": b.account_id,
                }
                for b in index.get(s["id"], [])
            ]
    except Exception as e:
        log.debug("session_manage.enrich_channels.error", {"error": str(e)})
        for s in sessions_dict:
            s.setdefault("channels", [])
    return sessions_dict


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

async def _session_list_impl(
    ctx: ToolContext,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> ToolResult:
    from flocks.session.session import SessionInfo
    from flocks.storage.storage import Storage

    # 直接扫描 Storage，支持 active/archived 双状态，且 project_id 过滤始终生效
    try:
        prefix = f"session:{project_id}:" if project_id else "session:"
        keys = await Storage.list_keys(prefix=prefix)
        sessions = []
        for key in keys:
            try:
                s = await Storage.get(key, SessionInfo)
                if s and s.status != "deleted":
                    sessions.append(s)
            except Exception:
                continue
        sessions.sort(key=lambda s: s.time.updated, reverse=True)
    except Exception as e:
        return ToolResult(success=False, error=f"查询 session 列表失败: {e}")

    if status:
        sessions = [s for s in sessions if s.status == status]

    if category:
        sessions = [s for s in sessions if s.category == category]

    total = len(sessions)
    off = offset or 0
    lim = limit or 50
    page = sessions[off: off + lim]

    sessions_dict = await _enrich_with_channels([_session_to_dict(s) for s in page])

    return ToolResult(
        success=True,
        output={
            "total": total,
            "offset": off,
            "limit": lim,
            "sessions": sessions_dict,
        },
    )


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

async def _session_get_impl(ctx: ToolContext, session_id: str) -> ToolResult:
    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if not session:
        return ToolResult(success=False, error=f"未找到 session '{session_id}'")

    result = _session_to_dict(session)
    enriched = await _enrich_with_channels([result])
    return ToolResult(success=True, output=enriched[0])


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

async def _session_create_impl(
    ctx: ToolContext,
    title: Optional[str] = None,
    project_id: Optional[str] = None,
    directory: Optional[str] = None,
    agent: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> ToolResult:
    import os
    from flocks.session.session import Session

    try:
        session = await Session.create(
            project_id=project_id or "default",
            directory=directory or os.getcwd(),
            title=title,
            parent_id=parent_id,
            **({"agent": agent} if agent else {}),
        )
    except Exception as e:
        return ToolResult(success=False, error=f"创建 session 失败: {e}")

    return ToolResult(
        success=True,
        output={
            "message": f"Session 已创建",
            "session": _session_to_dict(session),
        },
    )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

async def _session_update_impl(
    ctx: ToolContext,
    session_id: str,
    title: Optional[str] = None,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    memory_enabled: Optional[bool] = None,
) -> ToolResult:
    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if not session:
        return ToolResult(success=False, error=f"未找到 session '{session_id}'")

    updates: dict[str, Any] = {}
    if title is not None:
        updates["title"] = title
    if agent is not None:
        updates["agent"] = agent
    if model is not None:
        updates["model"] = model
    if provider is not None:
        updates["provider"] = provider
    if memory_enabled is not None:
        updates["memory_enabled"] = memory_enabled

    if not updates:
        return ToolResult(success=False, error="未提供任何要更新的字段")

    try:
        updated = await Session.update(session.project_id, session_id, **updates)
    except Exception as e:
        return ToolResult(success=False, error=f"更新 session 失败: {e}")

    if not updated:
        return ToolResult(success=False, error="更新失败，session 可能已被删除")

    return ToolResult(
        success=True,
        output={
            "message": "Session 已更新",
            "session": _session_to_dict(updated),
        },
    )


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

async def _session_delete_impl(ctx: ToolContext, session_id: str) -> ToolResult:
    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if not session:
        return ToolResult(success=False, error=f"未找到 session '{session_id}'")

    try:
        ok = await Session.delete(session.project_id, session_id)
    except Exception as e:
        return ToolResult(success=False, error=f"删除 session 失败: {e}")

    if not ok:
        return ToolResult(success=False, error="删除失败")

    return ToolResult(
        success=True,
        output=f"Session '{session_id}'（{session.title}）已删除",
    )


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------

async def _session_archive_impl(
    ctx: ToolContext,
    session_id: str,
    archive: Optional[bool] = True,
) -> ToolResult:
    from flocks.session.session import Session, SessionInfo
    from flocks.storage.storage import Storage

    # get_by_id 会跳过 archived session，需直接扫 Storage
    session = None
    keys = await Storage.list_keys(prefix="session:")
    for key in keys:
        try:
            s = await Storage.get(key, SessionInfo)
            if s and s.id == session_id and s.status != "deleted":
                session = s
                break
        except Exception:
            continue

    if not session:
        return ToolResult(success=False, error=f"未找到 session '{session_id}'")

    try:
        if archive is False:
            ok = await Session.unarchive(session.project_id, session_id)
            action = "取消归档"
        else:
            ok = await Session.archive(session.project_id, session_id)
            action = "归档"
    except Exception as e:
        return ToolResult(success=False, error=f"操作失败: {e}")

    if not ok:
        return ToolResult(
            success=False,
            error=f"操作失败，session 当前状态为 '{session.status}'，无法执行{action}",
        )

    return ToolResult(
        success=True,
        output=f"Session '{session_id}'（{session.title}）已{action}",
    )
