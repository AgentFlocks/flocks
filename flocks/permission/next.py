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
    _session_permissions: Dict[str, Dict[str, str]] = {}
    _permanent_rules: Dict[str, str] = {}
    _state_loaded: bool = False

    _PENDING_PREFIX = "permission_pending:"
    _REPLY_PREFIX = "permission_reply:"
    _SESSION_PREFIX = "permission_session:"
    _PERMANENT_PREFIX = "permission_rule:"

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
    async def _ensure_persisted_state_loaded(cls) -> None:
        if cls._state_loaded:
            return

        try:
            permanent_entries = await Storage.list_entries(prefix=cls._PERMANENT_PREFIX)
            session_entries = await Storage.list_entries(prefix=cls._SESSION_PREFIX)
        except Exception as exc:
            log.debug("permission.state_load_failed", {"error": str(exc)})
            return

        permanent_rules: Dict[str, str] = {}
        session_permissions: Dict[str, Dict[str, str]] = {}

        for key, value in permanent_entries:
            permission = key.removeprefix(cls._PERMANENT_PREFIX)
            if isinstance(value, str):
                permanent_rules[permission] = value

        for key, value in session_entries:
            session_id = key.removeprefix(cls._SESSION_PREFIX)
            if isinstance(value, dict):
                session_permissions[session_id] = {
                    str(rule_key): str(rule_value)
                    for rule_key, rule_value in value.items()
                }

        cls._permanent_rules = permanent_rules
        cls._session_permissions = session_permissions
        cls._state_loaded = True

    @classmethod
    def _schedule_persist(cls, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass

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
    async def _persist_permanent_rule(cls, permission: str, action: str) -> None:
        await Storage.set(
            f"{cls._PERMANENT_PREFIX}{permission}",
            action,
            "permission_rule",
        )

    @classmethod
    async def _persist_session_rules(cls, session_id: str) -> None:
        await Storage.set(
            f"{cls._SESSION_PREFIX}{session_id}",
            cls._session_permissions.get(session_id, {}),
            "permission_session",
        )

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
    async def _apply_reply_without_future(
        cls,
        request_info: PermissionRequestInfo,
        reply: str,
        session_id: Optional[str] = None,
    ) -> None:
        resolved_session_id = session_id or request_info.session_id
        permission = request_info.permission

        if reply == "always":
            cls._permanent_rules[permission] = "allow"
            await cls._persist_permanent_rule(permission, "allow")
            return
        if reply == "never":
            cls._permanent_rules[permission] = "deny"
            await cls._persist_permanent_rule(permission, "deny")
            return
        if reply == "allow_session":
            if resolved_session_id not in cls._session_permissions:
                cls._session_permissions[resolved_session_id] = {}
            cls._session_permissions[resolved_session_id][permission] = "allow"
            await cls._persist_session_rules(resolved_session_id)

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
    ) -> None:
        """
        Ask for permission to perform an action.

        Ported from original PermissionNext.ask().
        """
        import os

        await cls._ensure_persisted_state_loaded()
        metadata = metadata or {}
        always_patterns = always or []

        if os.environ.get("FLOCKS_AUTO_APPROVE") == "true":
            log.debug("permission.auto_approved", {
                "permission": permission,
                "reason": "FLOCKS_AUTO_APPROVE=true",
            })
            return

        session_perms = cls._session_permissions.get(session_id, {})
        if permission in session_perms:
            action = session_perms[permission]
            if action == "allow":
                return
            if action == "deny":
                raise DeniedError([])

        if permission in cls._permanent_rules:
            action = cls._permanent_rules[permission]
            if action in ("allow", "always"):
                return
            if action in ("deny", "never"):
                raise DeniedError([])

        if ruleset:
            action = cls._evaluate(permission, patterns[0] if patterns else "*", ruleset)
            if action == "allow":
                return
            if action == "deny":
                matching_rules = [
                    rule for rule in ruleset
                    if cls._pattern_matches(permission, rule.permission or "*")
                    and cls._pattern_matches(patterns[0] if patterns else "*", rule.pattern or "*")
                ]
                raise DeniedError(matching_rules)

        if always_patterns:
            for pattern in always_patterns:
                if cls._pattern_matches(patterns[0] if patterns else "*", pattern):
                    return

        req_id = request_id or Identifier.create("permission")
        request_info = PermissionRequestInfo(
            id=req_id,
            sessionID=session_id,
            permission=permission,
            patterns=patterns,
            metadata=metadata,
            always=always_patterns,
            tool=tool,
        )

        future = asyncio.Future()
        cls._pending[req_id] = {
            "info": request_info,
            "future": future,
        }
        cls._schedule_persist(cls._persist_pending_request(request_info))

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

        timeout_at = asyncio.get_running_loop().time() + 300
        reply: Optional[str] = None
        while reply is None:
            persisted_reply = await cls._consume_persisted_reply(req_id)
            if persisted_reply is not None:
                reply = persisted_reply
                break

            remaining = timeout_at - asyncio.get_running_loop().time()
            if remaining <= 0:
                if req_id in cls._pending:
                    del cls._pending[req_id]
                cls._schedule_persist(cls._delete_pending_request(req_id))
                cls._schedule_persist(cls._delete_reply(req_id))
                raise PermissionError(f"Permission request timed out: {permission}")

            try:
                reply = await asyncio.wait_for(asyncio.shield(future), timeout=min(0.25, remaining))
            except asyncio.TimeoutError:
                continue

        cls._pending.pop(req_id, None)
        cls._schedule_persist(cls._delete_reply(req_id))

        if reply in ("allow", "once"):
            return
        if reply in ("deny", "reject"):
            raise DeniedError([])
        if reply == "always":
            cls._permanent_rules[permission] = "allow"
            cls._schedule_persist(cls._persist_permanent_rule(permission, "allow"))
            return
        if reply == "never":
            cls._permanent_rules[permission] = "deny"
            cls._schedule_persist(cls._persist_permanent_rule(permission, "deny"))
            raise DeniedError([])
        if reply == "allow_session":
            if session_id not in cls._session_permissions:
                cls._session_permissions[session_id] = {}
            cls._session_permissions[session_id][permission] = "allow"
            cls._schedule_persist(cls._persist_session_rules(session_id))
            return

        raise PermissionError(f"Unknown permission reply: {reply}")

    @classmethod
    async def reply(
        cls,
        request_id: str,
        reply: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Reply to a pending permission request."""
        await cls._ensure_persisted_state_loaded()
        pending = cls._pending.get(request_id)
        pending_info = pending.get("info") if pending else await cls.get_pending_info(request_id)
        await cls._delete_pending_request(request_id)

        if pending is None:
            log.warn("permission.reply.not_found", {"request_id": request_id})
            resolved_session_id = session_id or (pending_info.session_id if pending_info else None)
            await cls._persist_reply(request_id, reply, session_id=resolved_session_id)
            if pending_info is not None:
                await cls._apply_reply_without_future(
                    pending_info,
                    reply,
                    session_id=session_id,
                )
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
