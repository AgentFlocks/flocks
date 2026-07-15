"""
沙箱工具策略

对齐 OpenClaw sandbox/tool-policy.ts：
- 支持 allow/deny 列表
- 支持通配符 (*) 模式匹配
- deny 优先于 allow
"""

import fnmatch
import re
from typing import List, Optional

from .types import SandboxToolPolicy


def is_tool_allowed(policy: SandboxToolPolicy, name: str) -> bool:
    """
    检查工具是否被允许。

    规则（对齐 OpenClaw isToolAllowed）：
    1. 如果在 deny 列表中 → 拒绝
    2. 如果 allow 列表为空 → 允许
    3. 如果在 allow 列表中 → 允许
    4. 否则 → 拒绝

    Args:
        policy: 工具策略
        name: 工具名称

    Returns:
        是否允许
    """
    normalized = name.strip().lower()
    deny = _expand_patterns(policy.deny)
    if _matches_any(normalized, deny):
        return False
    allow_layers = getattr(policy, "allow_layers", None)
    if isinstance(allow_layers, list) and allow_layers:
        return all(
            _matches_any(normalized, _expand_patterns(layer)) for layer in allow_layers if isinstance(layer, list)
        )
    allow = _expand_patterns(policy.allow)
    if not allow:
        return True
    return _matches_any(normalized, allow)


def resolve_tool_policy(
    global_allow: Optional[List[str]] = None,
    global_deny: Optional[List[str]] = None,
    agent_allow: Optional[List[str]] = None,
    agent_deny: Optional[List[str]] = None,
) -> SandboxToolPolicy:
    """
    解析工具策略（agent 覆盖 global）。

    对齐 OpenClaw resolveSandboxToolPolicyForAgent。
    当未配置 allow/deny 时，默认全允许。
    """
    allow: List[str]
    deny: List[str]

    deny = _combine_patterns(global_deny, agent_deny)
    allow = _intersect_allow_patterns(global_allow, agent_allow)
    allow_layers = [patterns for patterns in (_clean_patterns(global_allow), _clean_patterns(agent_allow)) if patterns]

    return SandboxToolPolicy(
        allow=allow,
        deny=deny,
        allow_layers=allow_layers or None,
    )


def _combine_patterns(*pattern_layers: Optional[List[str]]) -> List[str]:
    """Combine deny patterns so either policy layer can reject a tool."""
    combined: List[str] = []
    seen: set[str] = set()
    for patterns in pattern_layers:
        if not isinstance(patterns, list):
            continue
        for pattern in patterns:
            if not isinstance(pattern, str):
                continue
            normalized = pattern.strip()
            key = normalized.lower()
            if not normalized or key in seen:
                continue
            seen.add(key)
            combined.append(normalized)
    return combined


def _intersect_allow_patterns(
    global_allow: Optional[List[str]],
    agent_allow: Optional[List[str]],
) -> List[str]:
    """Return the representable intersection of global and agent allow lists."""
    global_patterns = _expand_patterns(global_allow)
    agent_patterns = _expand_patterns(agent_allow)
    if not global_patterns:
        return _clean_patterns(agent_allow)
    if not agent_patterns:
        return _clean_patterns(global_allow)

    intersection: List[str] = []
    for global_pattern in global_patterns:
        for agent_pattern in agent_patterns:
            candidate = _intersect_pattern_pair(global_pattern, agent_pattern)
            if candidate and candidate not in intersection:
                intersection.append(candidate)
    return intersection


def _clean_patterns(patterns: Optional[List[str]]) -> List[str]:
    if not isinstance(patterns, list):
        return []
    return [pattern.strip() for pattern in patterns if isinstance(pattern, str) and pattern.strip()]


def _intersect_pattern_pair(left: str, right: str) -> Optional[str]:
    if left == "*":
        return right
    if right == "*":
        return left
    if left == right:
        return left
    if "*" not in left and fnmatch.fnmatch(left, right):
        return left
    if "*" not in right and fnmatch.fnmatch(right, left):
        return right
    return None


def _expand_patterns(patterns: Optional[List[str]]) -> List[str]:
    """展开模式列表（去空去重）."""
    if not patterns:
        return []
    return [p.strip().lower() for p in patterns if p and p.strip()]


def _matches_any(name: str, patterns: List[str]) -> bool:
    """检查名称是否匹配任意模式."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if "*" in pattern:
            if fnmatch.fnmatch(name, pattern):
                return True
        elif name == pattern:
            return True
    return False
