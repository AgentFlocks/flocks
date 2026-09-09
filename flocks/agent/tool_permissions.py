"""Helpers for keeping agent tool selections and permission rules aligned."""

from __future__ import annotations

from typing import Any, Dict, List

from flocks.permission.rule import PermissionLevel, PermissionRule, PermissionScope

QUESTION_TOOL_NAME = "question"
TOOLS_MANAGED_PERMISSION_SOURCE = "agent_tools"


def normalize_permission_items(value: Any) -> List[Dict[str, Any]]:
    """Return stored permission rules in API/storage dict form."""
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        permission = item.get("permission")
        action = item.get("action") or item.get("level")
        action = getattr(action, "value", action)
        pattern = item.get("pattern") or "*"
        if not permission or action not in {"allow", "ask", "deny"}:
            continue

        rule = dict(item)
        rule["permission"] = str(permission)
        rule["action"] = str(action)
        rule["pattern"] = str(pattern)
        normalized.append(rule)

    return normalized


def sync_question_permission_with_tools(
    permissions: Any,
    tools: List[str],
) -> List[Dict[str, Any]]:
    """Make the Agent tools checkbox an effective question allow/deny toggle."""
    normalized = normalize_permission_items(permissions)
    without_managed_question = [
        rule
        for rule in normalized
        if not (
            rule.get("permission") == QUESTION_TOOL_NAME
            and rule.get("pattern", "*") == "*"
            and rule.get("source") == TOOLS_MANAGED_PERMISSION_SOURCE
        )
    ]

    if QUESTION_TOOL_NAME in set(tools):
        return without_managed_question

    if any(
        rule.get("permission") == QUESTION_TOOL_NAME
        and rule.get("action") == "deny"
        and rule.get("pattern", "*") == "*"
        for rule in without_managed_question
    ):
        return without_managed_question

    return without_managed_question + [
        {
            "permission": QUESTION_TOOL_NAME,
            "action": "deny",
            "pattern": "*",
            "source": TOOLS_MANAGED_PERMISSION_SOURCE,
        }
    ]


def permission_items_to_ruleset(value: Any) -> List[PermissionRule]:
    """Convert stored permission dicts to the internal PermissionRule shape."""
    return [
        PermissionRule(
            permission=item["permission"],
            level=PermissionLevel(item["action"]),
            scope=PermissionScope.PATTERN,
            pattern=item.get("pattern") or "*",
        )
        for item in normalize_permission_items(value)
    ]
