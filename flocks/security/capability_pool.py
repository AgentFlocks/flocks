"""OSS-neutral tool capability resolution and optional hook filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from flocks.hooks.pipeline import HookPipeline, HookStage


_SAFE_SUBJECT_KEYS = frozenset(
    {
        "subject_id",
        "subject_type",
        "display_name",
        "role",
        "status",
        "tenant_id",
        "tenant_ids",
        "department",
        "asset_groups",
        "entry",
        "auth_source",
        "permission_mode",
        "verified",
    }
)


def _normalized_tenant_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            continue
        tenant_id = raw_value.strip()
        if not tenant_id or tenant_id in seen:
            continue
        seen.add(tenant_id)
        normalized.append(tenant_id)
    return tuple(normalized)


def _normalize_tools(tools: Optional[Iterable[Any]]) -> tuple[str, ...]:
    if tools is None:
        return ()
    if isinstance(tools, str):
        tools = (tools,)
    elif isinstance(tools, set):
        tools = tuple(sorted(item for item in tools if isinstance(item, str)))

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tool in tools:
        if not isinstance(raw_tool, str):
            continue
        tool = raw_tool.strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        normalized.append(tool)
    return tuple(normalized)


@dataclass(frozen=True)
class CapabilityPool:
    """An immutable set of tools, with safe count-only filter provenance."""

    tools: tuple[str, ...]
    filtered_by: tuple[str, ...] = ()
    removed_count: int = 0

    @classmethod
    def from_tools(
        cls,
        tools: Optional[Iterable[Any]],
        *,
        context: Optional[Mapping[str, Any]],
    ) -> "CapabilityPool":
        """Create a normalized pool without persisting caller context."""
        _ = context
        return cls(tools=_normalize_tools(tools))

    def intersect(self, other: "CapabilityPool", *, source: str) -> "CapabilityPool":
        """Return only tools present in both pools, preserving this pool's order."""
        allowed_tools = set(other.tools)
        tools = tuple(tool for tool in self.tools if tool in allowed_tools)
        source_name = str(source).strip()
        filtered_by = self.filtered_by + ((source_name,) if source_name else ())
        return CapabilityPool(
            tools=tools,
            filtered_by=filtered_by,
            removed_count=self.removed_count + len(self.tools) - len(tools),
        )

    def as_dict(self) -> dict[str, Any]:
        """Expose hook-safe capability metadata without source inputs or secrets."""
        return {
            "tools": list(self.tools),
            "filtered_by": list(self.filtered_by),
            "removed_count": self.removed_count,
        }


def _safe_subject(value: Any) -> dict[str, Any]:
    if value is None:
        try:
            from flocks.identity.subject import get_current_subject

            value = get_current_subject()
        except Exception:
            value = None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, Mapping):
        return {}
    safe_subject = {key: value[key] for key in _SAFE_SUBJECT_KEYS if key in value}
    tenant_ids = _normalized_tenant_ids(safe_subject.get("tenant_ids"))
    if "tenant_ids" in safe_subject:
        safe_subject["tenant_ids"] = tenant_ids

    explicit_tenant_id = safe_subject.get("tenant_id")
    explicit_tenant_id = (
        explicit_tenant_id.strip()
        if isinstance(explicit_tenant_id, str)
        else ""
    )
    if explicit_tenant_id and (not tenant_ids or explicit_tenant_id in tenant_ids):
        safe_subject["tenant_id"] = explicit_tenant_id
    elif len(tenant_ids) == 1:
        safe_subject["tenant_id"] = tenant_ids[0]
    else:
        safe_subject.pop("tenant_id", None)
    return safe_subject


def _safe_text(value: Any, *, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _hook_input(pool: CapabilityPool, context: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw_context = context if isinstance(context, Mapping) else {}
    subject = _safe_subject(raw_context.get("subject"))
    return {
        "capability_pool": pool.as_dict(),
        "subject": subject,
        "entry": _safe_text(
            raw_context.get("entry"),
            default=_safe_text(subject.get("entry"), default="unknown"),
        ),
        "permission_mode": _safe_text(
            raw_context.get("permission_mode"),
            default=_safe_text(subject.get("permission_mode"), default="default_interactive"),
        ),
        "execution_mode": _safe_text(raw_context.get("execution_mode"), default="default"),
        "agent": _safe_text(raw_context.get("agent"), default="unknown"),
        "workspace": _safe_text(raw_context.get("workspace"), default=""),
        "sessionID": _safe_text(raw_context.get("sessionID"), default=""),
    }


def _requested_pool(requested: Any, context: Optional[Mapping[str, Any]]) -> Optional[CapabilityPool]:
    if not isinstance(requested, Mapping):
        return None
    tools = requested.get("tools")
    if not isinstance(tools, (list, tuple, set)):
        return None
    return CapabilityPool.from_tools(tools, context=context)


def _requested_pools(output: Mapping[str, Any], context: Optional[Mapping[str, Any]]) -> list[CapabilityPool]:
    candidates = output.get("capability_filters")
    if isinstance(candidates, list):
        return [
            pool
            for candidate in candidates
            if (pool := _requested_pool(candidate, context)) is not None
        ]

    requested_pool = _requested_pool(output.get("capability_pool"), context)
    return [requested_pool] if requested_pool is not None else []


async def filter_capability_pool(
    pool: CapabilityPool,
    *,
    context: Optional[Mapping[str, Any]],
) -> CapabilityPool:
    """Apply an optional capability hook without allowing capability expansion."""
    hook_input = _hook_input(pool, context)
    try:
        has_handlers = await HookPipeline.has_stage_handlers(
            HookStage.CAPABILITY_FILTER,
            hook_input,
        )
    except Exception:
        return pool
    if not has_handlers:
        return pool

    try:
        hook_context = await HookPipeline.run_capability_filter(hook_input)
    except Exception:
        return pool

    filtered_pool = pool
    for requested_pool in _requested_pools(hook_context.output, context):
        filtered_pool = filtered_pool.intersect(requested_pool, source=HookStage.CAPABILITY_FILTER)
    return filtered_pool
