"""
Permission handling for session operations.

Ported from original permission/next.ts PermissionNext namespace.
Handles permission requests, replies, and rule evaluation.
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable

from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.permission.interactive import auto_approve_enabled
from flocks.permission.rule import PermissionRule, PermissionLevel
from flocks.permission.helpers import Ruleset, from_config, merge
from flocks.storage.storage import Storage

log = Log.create(service="permission")


class PermissionRequestInfo(BaseModel):
    """Permission request information"""

    model_config = {"populate_by_name": True}

    id: str
    session_id: str = Field(alias="sessionID")
    permission: str
    patterns: List[str]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    always: List[str] = Field(default_factory=list)
    tool: Optional[Dict[str, str]] = None
    time: Dict[str, int] = Field(
        default_factory=lambda: {"created": int(datetime.now().timestamp() * 1000)}
    )


class DeniedError(Exception):
    """Exception raised when permission is denied"""

    def __init__(self, rules: List[PermissionRule]):
        self.rules = rules
        super().__init__(f"Permission denied by rules: {rules}")


class PermissionNext:
    """
    Permission management namespace.

    Handles:
    - Permission rule evaluation
    - Permission request/reply flow
    - Session-scoped permission caching
    """

    _pending: Dict[str, Dict[str, Any]] = {}
    _PENDING_PREFIX = "permission_pending:"
    _REPLY_PREFIX = "permission_reply:"
    _DEFAULT_TIMEOUT_SECONDS = 300.0

    _on_permission_asked: Optional[Callable[[PermissionRequestInfo], Awaitable[None]]] = None
    _on_permission_replied: Optional[Callable[[str, str, str], Awaitable[None]]] = None

    @classmethod
    def set_callbacks(
        cls,
        on_asked: Optional[Callable[[PermissionRequestInfo], Awaitable[None]]] = None,
        on_replied: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
    ) -> None:
        """Set event callbacks for permission events."""
        cls._on_permission_asked = on_asked
        cls._on_permission_replied = on_replied

    @classmethod
    async def _persist_pending_request(cls, request_info: PermissionRequestInfo) -> None:
        await Storage.set(
            f"{cls._PENDING_PREFIX}{request_info.id}",
            request_info.model_dump(by_alias=True),
            "permission_pending",
        )

    @classmethod
    async def _delete_pending_request(cls, request_id: str) -> None:
        await Storage.delete(f"{cls._PENDING_PREFIX}{request_id}")

    @classmethod
    async def _persist_reply(
        cls,
        request_id: str,
        reply: str,
        *,
        session_id: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "reply": reply,
            "time": {"created": int(datetime.now().timestamp() * 1000)},
        }
        if session_id:
            payload["sessionID"] = session_id
        await Storage.set(
            f"{cls._REPLY_PREFIX}{request_id}",
            payload,
            "permission_reply",
        )

    @classmethod
    async def _delete_reply(cls, request_id: str) -> None:
        await Storage.delete(f"{cls._REPLY_PREFIX}{request_id}")

    @classmethod
    async def _consume_persisted_reply(cls, request_id: str) -> Optional[str]:
        stored = await Storage.get(f"{cls._REPLY_PREFIX}{request_id}")
        if stored is None:
            return None
        await cls._delete_reply(request_id)
        if isinstance(stored, dict):
            reply = stored.get("reply")
        else:
            reply = stored
        if not reply:
            return None
        return str(reply)

    @classmethod
    @classmethod
    async def list_pending_infos(cls) -> List[PermissionRequestInfo]:
        pending_infos = [
            pending["info"]
            for pending in cls._pending.values()
            if isinstance(pending, dict) and pending.get("info") is not None
        ]
        try:
            stored_entries = await Storage.list_entries(prefix=cls._PENDING_PREFIX)
        except Exception:
            stored_entries = []

        seen_ids = {info.id for info in pending_infos}
        for _key, value in stored_entries:
            try:
                info = PermissionRequestInfo.model_validate(value)
            except Exception:
                continue
            if info.id not in seen_ids:
                pending_infos.append(info)
                seen_ids.add(info.id)
        return pending_infos

    @classmethod
    async def get_pending_info(cls, request_id: str) -> Optional[PermissionRequestInfo]:
        pending = cls._pending.get(request_id)
        if pending and pending.get("info") is not None:
            return pending["info"]
        stored = await Storage.get(f"{cls._PENDING_PREFIX}{request_id}")
        if stored is None:
            return None
        try:
            return PermissionRequestInfo.model_validate(stored)
        except Exception:
            return None

    @classmethod
    async def ask(
        cls,
        session_id: str,
        permission: str,
        patterns: List[str],
        ruleset: Ruleset,
        metadata: Optional[Dict[str, Any]] = None,
        always: Optional[List[str]] = None,
        tool: Optional[Dict[str, str]] = None,
        request_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """
        Ask for permission to perform an action.

        Ported from original PermissionNext.ask().
        """
        metadata = metadata or {}

        if auto_approve_enabled():
            log.debug("permission.auto_approved", {
                "permission": permission,
                "reason": "FLOCKS_AUTO_APPROVE=true",
            })
            return "allow"

        req_id = request_id or Identifier.create("permission")
        request_info = PermissionRequestInfo(
            id=req_id,
            sessionID=session_id,
            permission=permission,
            patterns=patterns,
            metadata=metadata,
            always=always or [],
            tool=tool,
        )

        # Persist before exposing the request through callbacks/SSE.  A reply
        # can now safely locate this request even when it reaches another
        # process before the in-memory future is visible.
        await cls._persist_pending_request(request_info)
        future = asyncio.Future()
        cls._pending[req_id] = {
            "info": request_info,
            "future": future,
        }

        if cls._on_permission_asked:
            await cls._on_permission_asked(request_info)

        try:
            from flocks.server.routes.event import publish_event
            await publish_event("permission.request", {
                "requestID": req_id,
                "sessionID": session_id,
                "permission": permission,
                "patterns": patterns,
                "metadata": metadata or {},
                "tool": tool,
            })
        except Exception as exc:
            log.debug("permission.request.publish_failed", {"error": str(exc)})

        timeout = (
            cls._DEFAULT_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(float(timeout_seconds), 0.0)
        )
        timeout_at = asyncio.get_running_loop().time() + timeout
        reply: Optional[str] = None
        while reply is None:
            persisted_reply = await cls._consume_persisted_reply(req_id)
            if persisted_reply is not None:
                reply = persisted_reply
                break

            remaining = timeout_at - asyncio.get_running_loop().time()
            if remaining <= 0:
                cls._pending.pop(req_id, None)
                await cls._delete_pending_request(req_id)
                await cls._delete_reply(req_id)
                raise asyncio.TimeoutError(
                    f"Permission request timed out after {timeout:.0f}s: {permission}"
                )

            try:
                reply = await asyncio.wait_for(asyncio.shield(future), timeout=min(0.25, remaining))
            except asyncio.TimeoutError:
                continue

        cls._pending.pop(req_id, None)
        await cls._delete_pending_request(req_id)
        await cls._delete_reply(req_id)
        # OSS only transports the reply; Pro decides whether it means a
        # denial, one-time grant, or durable authorization.
        return str(reply)

    @classmethod
    async def reply(
        cls,
        request_id: str,
        reply: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Reply to a pending permission request."""
        pending = cls._pending.get(request_id)
        pending_info = pending.get("info") if pending else await cls.get_pending_info(request_id)

        if pending is None:
            log.warn("permission.reply.not_found", {"request_id": request_id})
            resolved_session_id = session_id or (pending_info.session_id if pending_info else None)
            await cls._persist_reply(request_id, reply, session_id=resolved_session_id)
            await cls._delete_pending_request(request_id)
            if cls._on_permission_replied and resolved_session_id:
                try:
                    task = cls._on_permission_replied(resolved_session_id, request_id, reply)
                    if asyncio.iscoroutine(task):
                        asyncio.create_task(task)
                except Exception as exc:
                    log.debug("permission.reply.callback_failed", {"error": str(exc)})
            return

        future = pending["future"]
        request_info = pending["info"]

        log.info("permission.replied", {
            "request_id": request_id,
            "reply": reply,
        })

        if not future.done():
            future.set_result(reply)
        await cls._delete_pending_request(request_id)

        if cls._on_permission_replied:
            resolved_session_id = session_id or request_info.session_id
            try:
                task = cls._on_permission_replied(resolved_session_id, request_id, reply)
                if asyncio.iscoroutine(task):
                    asyncio.create_task(task)
            except Exception as exc:
                log.debug("permission.reply.callback_failed", {"error": str(exc)})

        if request_id in cls._pending:
            del cls._pending[request_id]

    @classmethod
    def evaluate(
        cls,
        permission: str,
        pattern: str,
        ruleset: Ruleset,
    ) -> str:
        """
        Public interface: evaluate permission action for a (permission, pattern) pair
        against a ruleset using last-matching-rule-wins semantics.

        Returns one of: 'allow', 'deny', 'ask'.
        """
        return cls._evaluate(permission, pattern, ruleset)

    @classmethod
    def _evaluate(
        cls,
        permission: str,
        pattern: str,
        ruleset: Ruleset,
    ) -> str:
        """Evaluate permission action for a pattern."""
        matched_rule = None
        for rule in reversed(ruleset):
            if not cls._pattern_matches(permission, rule.permission or "*"):
                continue
            if not cls._pattern_matches(pattern, rule.pattern or "*"):
                continue
            matched_rule = rule
            break

        if matched_rule:
            return matched_rule.level.value if hasattr(matched_rule.level, "value") else str(matched_rule.level)

        return "ask"

    @classmethod
    def _pattern_matches(cls, text: str, pattern: str) -> bool:
        """Check if text matches pattern (with wildcard support)."""
        if pattern == "*":
            return True
        if "*" in pattern:
            import fnmatch
            return fnmatch.fnmatch(text, pattern)
        return text == pattern

    @classmethod
    def from_config(cls, permission_config):
        """Alias for from_config function."""
        return from_config(permission_config)

    @classmethod
    def merge(cls, *rulesets: Ruleset) -> Ruleset:
        """Alias for merge function."""
        return merge(*rulesets)


__all__ = ["PermissionNext", "PermissionRequestInfo", "DeniedError", "Ruleset"]
