from typing import Dict, Any, List, Union

from pydantic import BaseModel

from flocks.permission.rule import PermissionRule, PermissionLevel, PermissionScope

Ruleset = List[PermissionRule]

_LEGACY_PERMISSION_NAMES = {
    "task": "delegate_task",
    "todowrite": "todo",
    "todoread": "todo",
}


def _merge_legacy_permission(existing: Any, incoming: Any) -> Any:
    """Merge aliases without allowing a legacy deny to become an allow."""
    def contains_deny(value: Any) -> bool:
        raw_value = getattr(value, "value", value)
        if raw_value == "deny":
            return True
        if isinstance(raw_value, dict):
            return any(contains_deny(item) for item in raw_value.values())
        return False

    if contains_deny(existing) or contains_deny(incoming):
        return "deny"
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for pattern, action in incoming.items():
            if pattern in merged:
                merged[pattern] = _merge_legacy_permission(
                    merged[pattern], action,
                )
            else:
                merged[pattern] = action
        return merged
    return existing


def _canonicalize_permission_config(config: Dict[str, Any]) -> Dict[str, Any]:
    canonical: Dict[str, Any] = {}
    for key, value in config.items():
        name = _LEGACY_PERMISSION_NAMES.get(key, key)
        if name in canonical:
            canonical[name] = _merge_legacy_permission(canonical[name], value)
        else:
            canonical[name] = value
    return canonical


def from_config(permission_config: Union[Dict[str, Any], BaseModel]) -> Ruleset:
    """
    Convert config permission object to Ruleset.

    Matches PermissionNext.fromConfig.
    """
    ruleset: Ruleset = []

    if hasattr(permission_config, "model_dump"):
        config_dict = permission_config.model_dump(exclude_none=True)
    elif isinstance(permission_config, dict):
        config_dict = permission_config
    else:
        return ruleset

    config_dict = _canonicalize_permission_config(config_dict)

    for key, value in config_dict.items():
        if isinstance(value, str) or isinstance(value, PermissionLevel):
            ruleset.append(PermissionRule(
                permission=key,
                level=PermissionLevel(value),
                scope=PermissionScope.GLOBAL,
                pattern="*",
            ))
            continue

        if isinstance(value, dict):
            for pattern, action in value.items():
                ruleset.append(PermissionRule(
                    permission=key,
                    level=PermissionLevel(action),
                    scope=PermissionScope.PATTERN,
                    pattern=pattern,
                ))

    return ruleset


def merge(*rulesets: Ruleset) -> Ruleset:
    """Merge multiple rulesets."""
    result: Ruleset = []
    for ruleset in rulesets:
        result.extend(ruleset)
    return result
