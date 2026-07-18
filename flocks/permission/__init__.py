"""
Permission management module.

Unified permission system for agent and session operations.
"""

from flocks.permission.rule import (
    PermissionLevel,
    PermissionScope,
    PermissionRule,
    PermissionRequest,
    PermissionResult,
)
from flocks.permission.manager import PermissionManager, Permission
from flocks.permission.helpers import Ruleset, from_config, merge
from flocks.permission.next import PermissionNext, PermissionRequestInfo, DeniedError

__all__ = [
    "Permission",
    "PermissionManager",
    "PermissionLevel",
    "PermissionScope",
    "PermissionRule",
    "PermissionRequest",
    "PermissionResult",
    "Ruleset",
    "from_config",
    "merge",
    "PermissionRequestInfo",
    "DeniedError",
    "PermissionNext",
]
"""
Permission management module

Unified permission system for agent and session operations.
"""

import asyncio
from enum import Enum
from typing import Optional, Dict, Any, List, Set, Union, Callable, Awaitable

from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.utils.id import Identifier

log = Log.create(service="permission")


class PermissionLevel(str, Enum):
    """Permission level for tool operations"""
    ALLOW = "allow"  # Always allow
    ASK = "ask"      # Ask user before executing
    DENY = "deny"    # Always deny


class PermissionScope(str, Enum):
    """Scope of a permission rule"""
    GLOBAL = "global"       # Applies to all files/operations
    DIRECTORY = "directory"  # Applies to specific directory
    FILE = "file"           # Applies to specific file
    PATTERN = "pattern"     # Applies to file pattern (glob)


class PermissionRule(BaseModel):
    """
    Permission rule definition

    Rules are evaluated in order, and the first matching rule is applied.
    """
    permission: Optional[str] = None  # Permission category (read, edit, etc.)
    level: PermissionLevel = PermissionLevel.ASK
    scope: PermissionScope = PermissionScope.GLOBAL
    pattern: Optional[str] = None  # Glob pattern for PATTERN scope
    path: Optional[str] = None     # Path for DIRECTORY or FILE scope
    tools: List[str] = Field(default_factory=list)  # Specific tools, empty = all
    description: Optional[str] = None


class PermissionRequest(BaseModel):
    """Request for permission check"""
    tool: str
    path: Optional[str] = None
    operation: Optional[str] = None  # read, write, execute, etc.
    context: Dict[str, Any] = Field(default_factory=dict)


class PermissionResult(BaseModel):
    """Result of permission check"""
    allowed: bool
    level: PermissionLevel
    rule: Optional[PermissionRule] = None
    reason: Optional[str] = None
    requires_confirmation: bool = False


class PermissionManager:
    """
    Permission management for agent operations

    Manages permission rules and checks for tool execution.
    """

    def __init__(self):
        self._rules: List[PermissionRule] = []
        self._auto_approved: Set[str] = set()  # Tool+path combinations auto-approved
        self._denied: Set[str] = set()  # Tool+path combinations denied

    def add_rule(self, rule: PermissionRule) -> None:
        """
        Add a permission rule

        Args:
            rule: Permission rule to add
        """
        self._rules.append(rule)
        log.info("permission.rule_added", {
            "level": rule.level.value,
            "scope": rule.scope.value
        })

    def remove_rule(self, index: int) -> bool:
        """
        Remove a rule by index

        Args:
            index: Rule index

        Returns:
            True if removed
        """
        if 0 <= index < len(self._rules):
            self._rules.pop(index)
            return True
        return False

    def clear_rules(self) -> None:
        """Clear all rules"""
        self._rules.clear()
        self._auto_approved.clear()
        self._denied.clear()

    def get_rules(self) -> List[PermissionRule]:
        """Get all rules"""
        return self._rules.copy()

    def check(self, request: PermissionRequest) -> PermissionResult:
        """
        Check permission for a request

        Args:
            request: Permission request

        Returns:
            Permission result
        """
        # Generate key for caching
        key = self._make_key(request.tool, request.path)

        # Check denied cache
        if key in self._denied:
            return PermissionResult(
                allowed=False,
                level=PermissionLevel.DENY,
                reason="Previously denied"
            )

        # Check auto-approved cache
        if key in self._auto_approved:
            return PermissionResult(
                allowed=True,
                level=PermissionLevel.ALLOW,
                reason="Previously approved"
            )

        # Find matching rule
        for rule in self._rules:
            if self._rule_matches(rule, request):
                if rule.level == PermissionLevel.ALLOW:
                    return PermissionResult(
                        allowed=True,
                        level=PermissionLevel.ALLOW,
                        rule=rule,
                        reason="Allowed by rule"
                    )
                if rule.level == PermissionLevel.DENY:
                    return PermissionResult(
                        allowed=False,
                        level=PermissionLevel.DENY,
                        rule=rule,
                        reason="Denied by rule"
                    )
                return PermissionResult(
                    allowed=False,  # Not allowed until confirmed
                    level=PermissionLevel.ASK,
                    rule=rule,
                    reason="Requires user confirmation",
                    requires_confirmation=True
                )

        # Default: require confirmation
        return PermissionResult(
            allowed=False,
            level=PermissionLevel.ASK,
            reason="No matching rule, requires confirmation",
            requires_confirmation=True
        )

    def approve(
        self,
        request: PermissionRequest,
        remember: bool = False
    ) -> None:
        """
        Approve a permission request

        Args:
            request: Permission request
            remember: If True, remember for future requests
        """
        key = self._make_key(request.tool, request.path)

        if remember:
            self._auto_approved.add(key)
            self._denied.discard(key)

        log.info("permission.approved", {
            "tool": request.tool,
            "path": request.path,
            "remember": remember
        })

    def deny(
        self,
        request: PermissionRequest,
        remember: bool = False
    ) -> None:
        """
        Deny a permission request

        Args:
            request: Permission request
            remember: If True, remember for future requests
        """
        key = self._make_key(request.tool, request.path)

        if remember:
            self._denied.add(key)
            self._auto_approved.discard(key)

        log.info("permission.denied", {
            "tool": request.tool,
            "path": request.path,
            "remember": remember
        })

    def _make_key(self, tool: str, path: Optional[str]) -> str:
        """Generate cache key"""
        if path:
            return f"{tool}:{path}"
        return tool

    def _rule_matches(self, rule: PermissionRule, request: PermissionRequest) -> bool:
        """
        Check if a rule matches a request

        Args:
            rule: Permission rule
            request: Permission request

        Returns:
            True if rule matches
        """
        # Check tool filter
        if rule.tools and request.tool not in rule.tools:
            return False

        # Check scope
        if rule.scope == PermissionScope.GLOBAL:
            return True

        if not request.path:
            # Non-path operations only match global rules
            return False

        if rule.scope == PermissionScope.FILE:
            return rule.path == request.path

        if rule.scope == PermissionScope.DIRECTORY:
            # Check if path is under the directory
            if rule.path:
                return request.path.startswith(rule.path.rstrip('/') + '/')
            return False

        if rule.scope == PermissionScope.PATTERN:
            # Check glob pattern
            if rule.pattern:
                import fnmatch
                return fnmatch.fnmatch(request.path, rule.pattern)
            return False

        return False


class Permission:
    """
    Permission namespace for agent operations

    Provides a high-level interface for permission checking.
    """

    _manager: Optional[PermissionManager] = None

    # Default rules for common operations
    DEFAULT_RULES: List[Dict[str, Any]] = [
        # Allow reading any file by default
        {
            "level": "allow",
            "scope": "global",
            "tools": ["read_file", "list_directory", "search_files"],
            "description": "Allow read operations",
        },
        # Ask for write operations
        {
            "level": "ask",
            "scope": "global",
            "tools": ["write_file", "edit_file", "delete_file", "create_file"],
            "description": "Confirm write operations",
        },
        # Deny dangerous patterns
        {
            "level": "deny",
            "scope": "pattern",
            "pattern": "**/.env*",
            "tools": ["write_file", "edit_file"],
            "description": "Protect environment files",
        },
        {
            "level": "deny",
            "scope": "pattern",
            "pattern": "**/*.key",
            "tools": ["write_file", "edit_file", "read_file"],
            "description": "Protect key files",
        },
        # Ask for command execution
        {
            "level": "ask",
            "scope": "global",
            "tools": ["execute_command", "run_shell", "terminal"],
            "description": "Confirm command execution",
        },
    ]

    @classmethod
    def get_manager(cls) -> PermissionManager:
        """Get the permission manager instance"""
        if cls._manager is None:
            cls._manager = PermissionManager()
            cls._load_default_rules()
        return cls._manager

    @classmethod
    def _load_default_rules(cls) -> None:
        """Load default permission rules"""
        manager = cls._manager
        if not manager:
            return

        for rule_data in cls.DEFAULT_RULES:
            rule = PermissionRule(
                level=PermissionLevel(rule_data["level"]),
                scope=PermissionScope(rule_data.get("scope", "global")),
                pattern=rule_data.get("pattern"),
                path=rule_data.get("path"),
                tools=rule_data.get("tools", []),
                description=rule_data.get("description"),
            )
            manager.add_rule(rule)

    @classmethod
    def check(cls, tool: str, path: Optional[str] = None, **kwargs) -> PermissionResult:
        """
        Check permission for a tool operation

        Args:
            tool: Tool name
            path: Optional file/directory path
            **kwargs: Additional context

        Returns:
            Permission result
        """
        manager = cls.get_manager()
        request = PermissionRequest(tool=tool, path=path, context=kwargs)
        return manager.check(request)

    @classmethod
    def approve(cls, tool: str, path: Optional[str] = None, remember: bool = False) -> None:
        """
        Approve a tool operation

        Args:
            tool: Tool name
            path: Optional file/directory path
            remember: If True, remember for future
        """
        manager = cls.get_manager()
        request = PermissionRequest(tool=tool, path=path)
        manager.approve(request, remember)

    @classmethod
    def deny(cls, tool: str, path: Optional[str] = None, remember: bool = False) -> None:
        """
        Deny a tool operation

        Args:
            tool: Tool name
            path: Optional file/directory path
            remember: If True, remember for future
        """
        manager = cls.get_manager()
        request = PermissionRequest(tool=tool, path=path)
        manager.deny(request, remember)

    @classmethod
    def add_rule(cls, rule: PermissionRule) -> None:
        """Add a permission rule"""
        manager = cls.get_manager()
        manager.add_rule(rule)

    @classmethod
    def get_rules(cls) -> List[PermissionRule]:
        """Get all permission rules"""
        manager = cls.get_manager()
        return manager.get_rules()

    @classmethod
    def reset(cls) -> None:
        """Reset permission manager to defaults"""
        cls._manager = None


# ============================================================================
# Compatibility Helpers (matching PermissionNext)
# ============================================================================

Ruleset = List[PermissionRule]


def from_config(permission_config: Union[Dict[str, Any], BaseModel]) -> Ruleset:
    """
    Convert config permission object to Ruleset

    Matches PermissionNext.fromConfig

    Args:
        permission_config: Permission configuration from Config

    Returns:
        List of permission rules
    """
    ruleset: Ruleset = []

    if hasattr(permission_config, "model_dump"):
        config_dict = permission_config.model_dump(exclude_none=True)
    elif isinstance(permission_config, dict):
        config_dict = permission_config
    else:
        return ruleset

    for key, value in config_dict.items():
        if isinstance(value, str) or isinstance(value, PermissionLevel):
            # Simple permission: "read": "allow" -> permission="read", action="allow", pattern="*"
            ruleset.append(PermissionRule(
                permission=key,
                level=PermissionLevel(value),
                scope=PermissionScope.GLOBAL,
                pattern="*"
            ))
            continue

        if isinstance(value, dict):
            # Complex permission: "exclude": {"*.txt": "deny"}
            for pattern, action in value.items():
                ruleset.append(PermissionRule(
                    permission=key,
                    level=PermissionLevel(action),
                    scope=PermissionScope.PATTERN,
                    pattern=pattern
                ))

    return ruleset


def merge(*rulesets: Ruleset) -> Ruleset:
    """
    Merge multiple rulesets

    Matches PermissionNext.merge
    """
    result = []
    for ruleset in rulesets:
        result.extend(ruleset)
    return result


# ============================================================================
# Permission Request/Reply System (matching PermissionNext.ask)
# ============================================================================

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


class DeniedError(Exception):
    """Exception raised when permission is denied"""

    def __init__(self, rules: List[PermissionRule]):
        self.rules = rules
        super().__init__(f"Permission denied by rules: {rules}")


class PermissionNext:
    """
    Next-generation permission system

    Combines agent permission evaluation and permission request/reply handling.
    """

    # Pending permission requests (request_id -> {"info": PermissionRequestInfo, "future": asyncio.Future})
    _pending: Dict[str, Dict[str, Any]] = {}

    # Session-scoped permissions (session_id -> {permission: action})
    _session_permissions: Dict[str, Dict[str, str]] = {}

    # Permanent rules (permission -> action)
    _permanent_rules: Dict[str, str] = {}

    # Event callbacks
    _on_permission_asked: Optional[Callable[[PermissionRequestInfo], Awaitable[None]]] = None
    _on_permission_replied: Optional[Callable[[str, str, str], Awaitable[None]]] = None

    @classmethod
    def set_callbacks(
        cls,
        on_asked: Optional[Callable[[PermissionRequestInfo], Awaitable[None]]] = None,
        on_replied: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
    ) -> None:
        """Set event callbacks for permission events"""
        cls._on_permission_asked = on_asked
        cls._on_permission_replied = on_replied

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
        Ask for permission to perform an action

        Ported from original PermissionNext.ask().
        """
        import os

        metadata = metadata or {}
        always_patterns = always or []

        # Auto-approve for CI/TUI or testing modes
        if os.environ.get("FLOCKS_AUTO_APPROVE") == "true":
            log.debug("permission.auto_approved", {
                "permission": permission,
                "reason": "FLOCKS_AUTO_APPROVE=true"
            })
            return

        # Check session-scoped permissions first
        session_perms = cls._session_permissions.get(session_id, {})
        if permission in session_perms:
            action = session_perms[permission]
            if action == "allow":
                return
            if action == "deny":
                raise DeniedError([])

        # Check permanent rules
        if permission in cls._permanent_rules:
            action = cls._permanent_rules[permission]
            if action in ("allow", "always"):
                return
            if action in ("deny", "never"):
                raise DeniedError([])

        # Evaluate ruleset
        if ruleset:
            action = cls._evaluate(permission, patterns[0] if patterns else "*", ruleset)
            if action == "allow":
                return
            if action == "deny":
                matching_rules = [
                    rule for rule in ruleset
                    if cls._pattern_matches(permission, rule.permission or "*") and
                    cls._pattern_matches(patterns[0] if patterns else "*", rule.pattern or "*")
                ]
                raise DeniedError(matching_rules)

        # Check always patterns
        if always_patterns:
            for pattern in always_patterns:
                if cls._pattern_matches(patterns[0] if patterns else "*", pattern):
                    return

        # Need to ask user - create request and wait
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

        # Trigger callback/event
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

        # Wait for reply
        try:
            reply = await asyncio.wait_for(future, timeout=300)  # 5 min timeout
        except asyncio.TimeoutError:
            if req_id in cls._pending:
                del cls._pending[req_id]
            raise PermissionError(f"Permission request timed out: {permission}")

        # Process reply
        if reply in ("allow", "once"):
            return
        if reply in ("deny", "reject"):
            raise DeniedError([])
        if reply == "always":
            cls._permanent_rules[permission] = "allow"
            return
        if reply == "never":
            cls._permanent_rules[permission] = "deny"
            raise DeniedError([])
        if reply == "allow_session":
            if session_id not in cls._session_permissions:
                cls._session_permissions[session_id] = {}
            cls._session_permissions[session_id][permission] = "allow"
            return

        raise PermissionError(f"Unknown permission reply: {reply}")

    @classmethod
    def reply(
        cls,
        request_id: str,
        reply: str,
        session_id: Optional[str] = None,
    ) -> None:
        """
        Reply to a permission request.

        Accepts: allow, deny, always, never, allow_session, once, reject.
        """
        if request_id not in cls._pending:
            log.warn("permission.reply.not_found", {"request_id": request_id})
            return

        pending = cls._pending[request_id]
        future = pending["future"]
        request_info = pending["info"]

        log.info("permission.replied", {
            "request_id": request_id,
            "reply": reply,
        })

        if not future.done():
            future.set_result(reply)

        # Trigger callback (async-friendly)
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
    def _evaluate(
        cls,
        permission: str,
        pattern: str,
        ruleset: Ruleset,
    ) -> str:
        """
        Evaluate permission action for a pattern.

        Uses Flocks's "last matching rule wins" behavior.
        """
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
    def from_config(cls, permission_config: Union[Dict[str, Any], BaseModel]) -> Ruleset:
        """Alias for from_config function"""
        return from_config(permission_config)

    @classmethod
    def merge(cls, *rulesets: Ruleset) -> Ruleset:
        """Alias for merge function"""
        return merge(*rulesets)


__all__ = [
    "Permission",
    "PermissionManager",
    "PermissionLevel",
    "PermissionScope",
    "PermissionRule",
    "PermissionRequest",
    "PermissionResult",
    "Ruleset",
    "from_config",
    "merge",
    "PermissionRequestInfo",
    "DeniedError",
    "PermissionNext",
]
