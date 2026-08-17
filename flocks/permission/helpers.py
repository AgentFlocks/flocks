from typing import Dict, Any, List, Union

from pydantic import BaseModel

from flocks.permission.rule import PermissionRule, PermissionLevel, PermissionScope

Ruleset = List[PermissionRule]


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
