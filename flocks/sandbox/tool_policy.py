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

    if isinstance(agent_deny, list):
        deny = agent_deny
    elif isinstance(global_deny, list):
        deny = global_deny
    else:
        deny = []

    if isinstance(agent_allow, list):
        allow = agent_allow
    elif isinstance(global_allow, list):
        allow = global_allow
    else:
        allow = []

    return SandboxToolPolicy(allow=allow, deny=deny)


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
