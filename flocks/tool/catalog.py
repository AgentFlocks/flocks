"""
Tool catalog metadata and search helpers.

This module is the awareness layer of the tool system. It does not decide what
is callable in a session; it only describes and searches the full catalog.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field


class ToolCatalogMetadata(BaseModel):
    always_load: bool = False
    tags: List[str] = Field(default_factory=list)


ALWAYS_LOAD_TOOL_NAMES: Set[str] = {
    "question",
    "tool_search",
}


TOOL_TAGS: Dict[str, List[str]] = {
    "read": ["code-reading", "file-inspection"],
    "read_file": ["code-reading", "file-inspection"],
    "list": ["file-navigation", "workspace"],
    "glob": ["file-search", "workspace"],
    "grep": ["code-search", "text-search"],
    "edit": ["code-editing", "refactor"],
    "multiedit": ["code-editing", "refactor"],
    "write": ["file-creation", "code-editing"],
    "apply_patch": ["patching", "code-editing"],
    "bash": ["terminal", "command-execution"],
    "webfetch": ["web", "http-fetch"],
    "websearch": ["web", "research"],
    "delegate_task": ["agent", "delegation"],
    "call_omo_agent": ["agent", "delegation"],
    "task": ["planning", "task-management"],
    "task_create": ["planning", "task-management"],
    "task_list": ["planning", "task-management"],
    "task_status": ["planning", "task-management"],
    "task_update": ["planning", "task-management"],
    "task_delete": ["planning", "task-management"],
    "task_rerun": ["planning", "task-management"],
    "run_workflow": ["workflow", "execution"],
    "run_workflow_node": ["workflow", "execution"],
    "question": ["user-interaction", "clarification"],
    "skill": ["knowledge", "skill"],
    "tool_search": ["tool-discovery", "capability-search"],
    "session_list": ["session", "history"],
    "session_get": ["session", "history"],
    "session_create": ["session", "management"],
    "session_update": ["session", "management"],
    "session_delete": ["session", "management"],
    "session_archive": ["session", "management"],
    "background_output": ["background-task", "process"],
    "background_cancel": ["background-task", "process"],
    "memory": ["memory", "context"],
    "model_config": ["model", "configuration"],
    "batch": ["batch", "orchestration"],
    "slash_command": ["slash-command", "orchestration"],
    "plan_enter": ["planning", "mode"],
    "plan_exit": ["planning", "mode"],
    "channel_message": ["messaging", "channel"],
    "wecom_mcp": ["enterprise", "wecom"],
}


def get_always_load_tool_names() -> Set[str]:
    return set(ALWAYS_LOAD_TOOL_NAMES)


def get_tool_catalog_metadata(tool_name: str, tool_info: Optional[Any] = None) -> ToolCatalogMetadata:
    tags = list(dict.fromkeys(
        list(TOOL_TAGS.get(tool_name, [])) + list(getattr(tool_info, "tags", None) or [])
    ))
    return ToolCatalogMetadata(
        always_load=(
            getattr(tool_info, "always_load", None)
            if getattr(tool_info, "always_load", None) is not None
            else tool_name in ALWAYS_LOAD_TOOL_NAMES
        ),
        tags=tags,
    )


def apply_tool_catalog_defaults(tool_info: Any) -> Any:
    metadata = get_tool_catalog_metadata(getattr(tool_info, "name", ""), tool_info)
    if getattr(tool_info, "always_load", None) is None:
        tool_info.always_load = metadata.always_load
    if not getattr(tool_info, "tags", None):
        tool_info.tags = list(metadata.tags)
    else:
        tool_info.tags = list(dict.fromkeys(list(tool_info.tags) + list(metadata.tags)))
    return tool_info


def list_tool_catalog_infos(tool_names: Optional[Iterable[str]] = None) -> List[Any]:
    from flocks.tool.registry import ToolRegistry

    wanted = set(tool_names or [])
    result: List[Any] = []
    for tool_info in ToolRegistry.list_tools():
        if tool_info.name in {"invalid", "_noop"} or not getattr(tool_info, "enabled", True):
            continue
        if wanted and tool_info.name not in wanted:
            continue
        result.append(tool_info)
    return result


def canonical_tool_token(value: str) -> str:
    """Normalize tool names and aliases for exact selection/search matching."""
    canonical = "".join(ch.lower() for ch in value if ch.isalnum())
    if canonical.endswith("tool"):
        canonical = canonical[:-4]
    return canonical


def normalize_tool_search_query(query: str) -> str:
    query = query.strip()
    if query.lower().startswith("select:"):
        query = query[len("select:"):]
    return " ".join(
        canonical_tool_token(term)
        for term in re.split(r"[\s,]+", query)
        if term
    )


def _format_tool_catalog_match(tool_info: Any, matched_tags: List[str], score: int) -> Dict[str, Any]:
    metadata = get_tool_catalog_metadata(tool_info.name, tool_info)
    return {
        "name": tool_info.name,
        "description": tool_info.description,
        "category": getattr(tool_info.category, "value", str(tool_info.category)),
        "requires_confirmation": getattr(tool_info, "requires_confirmation", False),
        "source": getattr(tool_info, "source", None),
        "native": getattr(tool_info, "native", False),
        "always_load": metadata.always_load,
        "tags": metadata.tags,
        "matchedTags": matched_tags,
        "score": score,
    }


def _score_tool_catalog_match(query: str, category: Optional[str], tool_info: Any) -> Tuple[int, List[str]]:
    q = (query or "").strip().lower()
    tokens = [token for token in re.split(r"[\s,]+", q) if token]
    canonical_tokens = [canonical_tool_token(token) for token in tokens]
    name = tool_info.name.lower()
    canonical_name = canonical_tool_token(tool_info.name)
    desc = (tool_info.description or "").lower()
    normalized_desc = normalize_tool_search_query(tool_info.description or "")
    source = (getattr(tool_info, "source", None) or "").lower()
    tool_category = getattr(tool_info.category, "value", str(tool_info.category)).lower()
    metadata = get_tool_catalog_metadata(tool_info.name, tool_info)
    tags = [tag.lower() for tag in metadata.tags]
    matched_tags = [tag for tag in metadata.tags if q and tag.lower() in q]

    score = 0
    if not q:
        score += 10
    if q and q in name:
        score += 120
    if canonical_tokens and any(token == canonical_name for token in canonical_tokens):
        score += 140
    if q and any(token in name for token in tokens):
        score += 55
    if canonical_tokens and any(token and token in canonical_name for token in canonical_tokens):
        score += 65
    if q and q in desc:
        score += 40
    if q and any(token in desc for token in tokens):
        score += 20
    if canonical_tokens and any(token and token in normalized_desc for token in canonical_tokens):
        score += 20
    if q and q in source:
        score += 10
    if q and any(token in tag for token in tokens for tag in tags):
        score += 75
        matched_tags = list(dict.fromkeys(
            matched_tags + [tag for tag in metadata.tags if any(token in tag.lower() for token in tokens)]
        ))
    if category and tool_category == category.lower():
        score += 60
    if q and q in tool_category:
        score += 20
    if metadata.always_load:
        score += 5
    if getattr(tool_info, "requires_confirmation", False):
        score -= 5
    return score, matched_tags


def _select_tool_catalog(
    query: str,
    *,
    category: Optional[str],
    limit: int,
) -> Optional[Tuple[List[Dict[str, Any]], List[str]]]:
    lowered = (query or "").strip().lower()
    if not lowered.startswith("select:"):
        return None

    wanted = [
        canonical_tool_token(part)
        for part in lowered[len("select:"):].split(",")
        if part.strip()
    ]
    if not wanted:
        return [], []

    tools_by_canonical = {
        canonical_tool_token(tool_info.name): tool_info
        for tool_info in list_tool_catalog_infos()
        if not category
        or getattr(tool_info.category, "value", str(tool_info.category)).lower() == category.lower()
    }

    matches: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for canonical_name in wanted:
        tool_info = tools_by_canonical.get(canonical_name)
        if tool_info is None or tool_info.name in seen:
            continue
        seen.add(tool_info.name)
        matches.append(_format_tool_catalog_match(tool_info, [], 10_000))
        if len(matches) >= limit:
            break

    return matches, []


def search_tool_catalog(
    query: Optional[str] = None,
    *,
    category: Optional[str] = None,
    limit: int = 8,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    limit = max(1, min(limit or 8, 20))
    selected = _select_tool_catalog(query or "", category=category, limit=limit)
    if selected is not None:
        return selected

    ranked: List[Tuple[int, Any, List[str]]] = []

    for tool_info in list_tool_catalog_infos():
        if category:
            tool_category = getattr(tool_info.category, "value", str(tool_info.category))
            if tool_category.lower() != category.lower():
                continue
        score, matched_tags = _score_tool_catalog_match(query or "", category, tool_info)
        if query and score <= 0:
            continue
        ranked.append((score, tool_info, matched_tags))

    ranked.sort(key=lambda item: (-item[0], item[1].name))
    matches: List[Dict[str, Any]] = []
    matched_tag_set: Set[str] = set()

    for score, tool_info, matched_tags in ranked[:limit]:
        matched_tag_set.update(matched_tags)
        matches.append(_format_tool_catalog_match(tool_info, matched_tags, score))

    return matches, sorted(matched_tag_set)
