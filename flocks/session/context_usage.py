"""Session context-usage snapshot helpers.

This module exposes the context usage shown by the Web UI.  Provider-reported
usage is preferred when it reflects the current prompt, while the existing
message/part estimator is used as the fallback for providers that do not emit
token usage or after compaction changes the prompt shape.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from flocks.provider.provider import Provider
from flocks.session.message import Message
from flocks.session.prompt import SessionPrompt
from flocks.session.session import SessionInfo
from flocks.utils.log import Log


log = Log.create(service="context-usage")

UsageSource = Literal["observed", "estimated"]
DELEGATION_TOOLS = {"delegate_task", "task"}
ZERO_VISIBLE_SEGMENTS = {"agentDelegation"}


class ContextUsageSegment(BaseModel):
    """One row in the context-usage breakdown."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    tokens: int = 0
    included: bool = True
    source: UsageSource = "estimated"


class ContextUsageSnapshot(BaseModel):
    """Current context usage for a session."""

    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    session_id: str = Field(..., alias="sessionID")
    used_tokens: int = Field(0, alias="usedTokens")
    context_window: int = Field(0, alias="contextWindow")
    percent: int = 0
    source: UsageSource = "estimated"
    last_message_id: Optional[str] = Field(None, alias="lastMessageID")
    observed_tokens: Optional[int] = Field(None, alias="observedTokens")
    estimated_tokens: int = Field(0, alias="estimatedTokens")
    compacted_tokens: int = Field(0, alias="compactedTokens")
    provider_id: Optional[str] = Field(None, alias="providerID")
    model_id: Optional[str] = Field(None, alias="modelID")
    segments: List[ContextUsageSegment] = Field(default_factory=list)
    excluded_segments: List[ContextUsageSegment] = Field(default_factory=list, alias="excludedSegments")


class _ObservedTokens(BaseModel):
    message_id: str
    created_ms: int
    used_tokens: int
    prompt_tokens: int


def token_usage_to_dict(tokens: Any) -> Dict[str, Any]:
    """Return a stable token dict for API/SSE payloads."""
    if tokens is None:
        return {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    if hasattr(tokens, "model_dump"):
        data = tokens.model_dump()
    elif isinstance(tokens, dict):
        data = dict(tokens)
    else:
        data = dict(getattr(tokens, "__dict__", {}) or {})

    cache = data.get("cache") or {}
    if hasattr(cache, "model_dump"):
        cache = cache.model_dump()
    elif not isinstance(cache, dict):
        cache = dict(getattr(cache, "__dict__", {}) or {})

    return {
        "input": _coerce_int(data.get("input")),
        "output": _coerce_int(data.get("output")),
        "reasoning": _coerce_int(data.get("reasoning")),
        "cache": {
            "read": _coerce_int(cache.get("read")),
            "write": _coerce_int(cache.get("write")),
        },
    }


async def build_context_usage_snapshot(
    session_id: str,
    *,
    session: Optional[SessionInfo] = None,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> ContextUsageSnapshot:
    """Build a context-usage snapshot for UI display.

    ``usedTokens`` means the best available estimate of the current prompt
    footprint.  If the provider emitted usage for the latest model call and no
    later compaction mutation has changed the prompt, we use that observed
    value.  Otherwise we fall back to ``SessionPrompt.estimate_full_context_tokens``.
    """
    active_messages = await Message.list(session_id)
    all_messages = await Message.list(session_id, include_archived=True)
    archived_messages = [
        msg for msg in all_messages
        if bool(getattr(msg, "compacted", None))
    ]

    breakdown_tokens = await _estimate_message_breakdown(session_id, active_messages)
    message_estimate_tokens = max(
        await _estimate_messages(session_id, active_messages),
        sum(breakdown_tokens.values()),
    )
    compacted_tokens = await _estimate_messages(session_id, archived_messages)
    inferred_provider_id, inferred_model_id = _resolve_message_model(active_messages)
    provider_id = provider_id or inferred_provider_id or getattr(session, "provider", None)
    model_id = model_id or inferred_model_id or getattr(session, "model", None)
    context_window = _resolve_context_window(provider_id, model_id)
    tool_definition_tokens, prompt_tool_names = await _estimate_tool_definition_tokens(
        session_id,
        session=session,
        messages=active_messages,
    )
    system_prompt_tokens = await _estimate_system_prompt_tokens(
        session_id,
        session=session,
        messages=active_messages,
        provider_id=provider_id,
        model_id=model_id,
        prompt_tool_names=prompt_tool_names,
    )
    estimated_tokens = system_prompt_tokens + tool_definition_tokens + message_estimate_tokens

    latest_observed = _latest_fresh_observation(
        active_messages,
        latest_context_mutation_ms=await _latest_context_mutation_ms(session_id, all_messages),
    )

    observed_tokens: Optional[int] = None
    last_message_id: Optional[str] = None
    source: UsageSource = "estimated"
    used_tokens = estimated_tokens
    if latest_observed is not None:
        observed_tokens = latest_observed.used_tokens
        last_message_id = latest_observed.message_id
        used_tokens = max(estimated_tokens, latest_observed.used_tokens)
        source = "observed" if used_tokens == latest_observed.used_tokens else "estimated"

    percent = (
        max(0, min(100, int(((used_tokens / context_window) * 100) + 0.5)))
        if context_window > 0
        else 0
    )

    segments: List[ContextUsageSegment] = []
    segment_tokens = {
        "systemPrompt": system_prompt_tokens,
        "toolDefinitions": tool_definition_tokens,
        **breakdown_tokens,
    }
    unattributed_tokens = used_tokens - sum(segment_tokens.values())
    if unattributed_tokens > 0:
        segment_tokens["conversation"] = segment_tokens.get("conversation", 0) + unattributed_tokens

    for key in (
        "systemPrompt",
        "toolDefinitions",
        "conversation",
        "reasoning",
        "tools",
        "skillLoad",
        "agentDelegation",
    ):
        tokens = segment_tokens.get(key, 0)
        if tokens <= 0 and key not in ZERO_VISIBLE_SEGMENTS:
            continue
        segments.append(
            ContextUsageSegment(
                key=key,
                tokens=tokens,
                included=True,
                source="estimated",
            )
        )

    excluded_segments: List[ContextUsageSegment] = []
    if compacted_tokens > 0:
        excluded_segments.append(
            ContextUsageSegment(
                key="compactedHistory",
                tokens=compacted_tokens,
                included=False,
                source="estimated",
            )
        )

    return ContextUsageSnapshot(
        sessionID=session_id,
        usedTokens=used_tokens,
        contextWindow=context_window,
        percent=percent,
        source=source,
        lastMessageID=last_message_id,
        observedTokens=observed_tokens,
        estimatedTokens=estimated_tokens,
        compactedTokens=compacted_tokens,
        providerID=provider_id,
        modelID=model_id,
        segments=segments,
        excludedSegments=excluded_segments,
    )


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _role_value(message: Any) -> str:
    role = getattr(message, "role", "")
    return getattr(role, "value", role) or ""


def _time_created_ms(message: Any) -> int:
    time_data = getattr(message, "time", None)
    if isinstance(time_data, dict):
        return _coerce_int(time_data.get("created"))
    return _coerce_int(getattr(time_data, "created", 0))


def _is_summary_message(message: Any) -> bool:
    return bool(getattr(message, "summary", None)) or getattr(message, "finish", None) == "summary"


def _observed_tokens_for_message(message: Any) -> Optional[_ObservedTokens]:
    if _role_value(message) != "assistant" or _is_summary_message(message):
        return None

    data = token_usage_to_dict(getattr(message, "tokens", None))
    cache = data.get("cache") or {}
    prompt_tokens = _coerce_int(data.get("input")) + _coerce_int(cache.get("read"))
    used_tokens = (
        prompt_tokens
        + _coerce_int(data.get("output"))
        + _coerce_int(data.get("reasoning"))
    )
    if used_tokens <= 0:
        return None
    return _ObservedTokens(
        message_id=getattr(message, "id", ""),
        created_ms=_time_created_ms(message),
        used_tokens=used_tokens,
        prompt_tokens=prompt_tokens,
    )


def _latest_fresh_observation(
    messages: List[Any],
    *,
    latest_context_mutation_ms: int,
) -> Optional[_ObservedTokens]:
    for message in reversed(messages):
        observation = _observed_tokens_for_message(message)
        if observation is None:
            continue
        if observation.created_ms and observation.created_ms < latest_context_mutation_ms:
            return None
        return observation
    return None


async def _estimate_messages(session_id: str, messages: List[Any]) -> int:
    if not messages:
        return 0
    try:
        return _coerce_int(await SessionPrompt.estimate_full_context_tokens(session_id, messages))
    except Exception as exc:
        log.warn("context_usage.estimate_failed", {
            "session_id": session_id,
            "error": str(exc),
        })
        return 0


async def _estimate_system_prompt_tokens(
    session_id: str,
    *,
    session: Optional[SessionInfo],
    messages: List[Any],
    provider_id: Optional[str],
    model_id: Optional[str],
    prompt_tool_names: Iterable[str] = (),
) -> int:
    if not provider_id or not model_id:
        return 0
    try:
        from flocks.agent.registry import Agent
        from flocks.tool.registry import ToolRegistry

        agent_name = _resolve_agent_name(messages, session) or await Agent.default_agent()
        agent = await Agent.get(agent_name)
        if agent is None:
            agent = await Agent.get("rex")

        prompts = await SessionPrompt.build_system_prompts(
            session_id=session_id,
            session_directory=getattr(session, "directory", None) if session is not None else None,
            agent_name=getattr(agent, "name", agent_name) if agent is not None else agent_name,
            agent_prompt=getattr(agent, "prompt", None) if agent is not None else None,
            provider_id=provider_id,
            model_id=model_id,
            prompt_tool_names=prompt_tool_names,
            tool_revision=ToolRegistry.revision(),
        )
        return sum(SessionPrompt.count_tokens(prompt) for prompt in prompts)
    except Exception as exc:
        log.debug("context_usage.system_prompt_estimate_failed", {
            "session_id": session_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return 0


async def _estimate_tool_definition_tokens(
    session_id: str,
    *,
    session: Optional[SessionInfo],
    messages: List[Any],
) -> tuple[int, tuple[str, ...]]:
    try:
        from flocks.agent.registry import Agent
        from flocks.agent.toolset import resolve_agent_initial_tools
        from flocks.session.callable_schema import (
            _resolve_dynamic_always_load_tool_names,
            resolve_callable_tool_infos,
        )
        from flocks.session.callable_state import get_session_callable_tools
        from flocks.tool.catalog import get_always_load_tool_names

        agent_name = _resolve_agent_name(messages, session) or await Agent.default_agent()
        agent = await Agent.get(agent_name)
        if agent is None:
            agent = await Agent.get("rex")

        callable_tool_names = await get_session_callable_tools(session_id)
        if callable_tool_names:
            effective_tool_names = set(callable_tool_names)
        elif agent is not None:
            initial_tool_names, _permission_rules = resolve_agent_initial_tools(
                getattr(agent, "tools", None),
                getattr(agent, "permission", None),
                getattr(agent, "name", agent_name),
            )
            effective_tool_names = set(initial_tool_names)
        else:
            effective_tool_names = set()

        effective_tool_names.update(get_always_load_tool_names())
        effective_tool_names.update(await _resolve_dynamic_always_load_tool_names())
        tool_infos, _enabled_count = resolve_callable_tool_infos(effective_tool_names)

        tools = []
        for tool_info in tool_infos:
            schema = tool_info.get_schema()
            tools.append({
                "type": "function",
                "function": {
                    "name": tool_info.name,
                    "description": tool_info.description,
                    "parameters": schema.to_json_schema(),
                },
            })
        if not tools:
            return 0, ()

        prompt_tool_names = tuple(sorted(
            str(tool.get("function", {}).get("name", "")).strip()
            for tool in tools
            if isinstance(tool, dict)
        ))
        encoded = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        return SessionPrompt.count_tokens(encoded), tuple(name for name in prompt_tool_names if name)
    except Exception as exc:
        log.debug("context_usage.tool_definition_estimate_failed", {
            "session_id": session_id,
            "error": str(exc),
        })
        return 0, ()


def _resolve_agent_name(messages: List[Any], session: Optional[SessionInfo]) -> Optional[str]:
    for message in reversed(messages):
        agent_name = _field_value(message, "agent")
        if agent_name:
            return str(agent_name)
    agent_name = getattr(session, "agent", None) if session is not None else None
    return str(agent_name) if agent_name else None


async def _estimate_message_breakdown(session_id: str, messages: List[Any]) -> Dict[str, int]:
    tokens_by_key = {
        "conversation": 0,
        "reasoning": 0,
        "tools": 0,
        "skillLoad": 0,
        "agentDelegation": 0,
    }
    for message in messages:
        content = _field_value(message, "content", "")
        tokens_by_key["conversation"] += SessionPrompt.count_tokens(content or "")

        message_id = _field_value(message, "id")
        if not message_id:
            continue
        try:
            parts = await Message.parts(message_id, session_id)
        except Exception as exc:
            log.debug("context_usage.breakdown_parts_failed", {
                "message_id": message_id,
                "error": str(exc),
            })
            continue

        for part in parts:
            part_type = _field_value(part, "type", "")
            if part_type == "text":
                tokens_by_key["conversation"] += SessionPrompt.count_tokens(_field_value(part, "text", "") or "")
                continue
            if part_type in {"reasoning", "thinking"}:
                tokens_by_key["reasoning"] += SessionPrompt.count_tokens(_field_value(part, "text", "") or "")
                continue
            if part_type in {"agent", "subtask"}:
                tokens_by_key["agentDelegation"] += _estimate_subtask_part_tokens(part)
                continue
            if part_type != "tool":
                continue
            state = _field_value(part, "state")
            if state is None:
                continue
            tokens_by_key[_context_key_for_tool(_tool_name_for_part(part))] += _estimate_tool_state_tokens(state)
    return tokens_by_key


def _field_value(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _mapping_value(value: Any, *fields: str) -> Any:
    if not isinstance(value, dict):
        return None
    for field in fields:
        field_value = value.get(field)
        if field_value:
            return field_value
    return None


def _tool_name_for_part(part: Any) -> str:
    direct = _field_value(part, "tool", "")
    if direct:
        return str(direct)

    tool_name_fields = ("tool", "toolName", "tool_name", "name")
    metadata_name = _mapping_value(_field_value(part, "metadata"), *tool_name_fields)
    if metadata_name:
        return str(metadata_name)

    state = _field_value(part, "state")
    state_metadata_name = _mapping_value(_field_value(state, "metadata"), *tool_name_fields)
    if state_metadata_name:
        return str(state_metadata_name)

    return ""


def _context_key_for_tool(tool_name: str) -> str:
    if tool_name == "skill_load":
        return "skillLoad"
    if tool_name in DELEGATION_TOOLS:
        return "agentDelegation"
    return "tools"


def _estimate_subtask_part_tokens(part: Any) -> int:
    total = 0
    for field in ("prompt", "description", "name"):
        value = _field_value(part, field, "")
        total += SessionPrompt.count_tokens(value if isinstance(value, str) else str(value or ""))
    source = _field_value(part, "source")
    if source:
        total += SessionPrompt.count_tokens(source if isinstance(source, str) else str(source))
    return total


def _estimate_tool_state_tokens(state: Any) -> int:
    total = 0
    tool_input = _field_value(state, "input")
    if tool_input:
        total += SessionPrompt.count_tokens(
            tool_input if isinstance(tool_input, str) else str(tool_input)
        )

    time_info = _field_value(state, "time")
    is_compacted = isinstance(time_info, dict) and bool(time_info.get("compacted"))
    if is_compacted:
        return total + 10

    tool_output = _field_value(state, "output")
    if tool_output:
        total += SessionPrompt.count_tokens(
            tool_output if isinstance(tool_output, str) else str(tool_output)
        )
    return total


def _resolve_message_model(messages: List[Any]) -> tuple[Optional[str], Optional[str]]:
    for message in reversed(messages):
        role = _role_value(message)
        if role == "assistant":
            provider_id = getattr(message, "providerID", None)
            model_id = getattr(message, "modelID", None)
            if provider_id and model_id:
                return provider_id, model_id
        if role == "user":
            model = getattr(message, "model", None)
            if isinstance(model, dict):
                provider_id = model.get("providerID") or model.get("provider_id")
                model_id = model.get("modelID") or model.get("model_id")
                if provider_id and model_id:
                    return provider_id, model_id
    return None, None


def _resolve_context_window(provider_id: Optional[str], model_id: Optional[str]) -> int:
    if not provider_id or not model_id:
        return 0
    try:
        context_window, _max_output, _max_input = Provider.resolve_model_info(provider_id, model_id)
        return _coerce_int(context_window)
    except Exception as exc:
        log.debug("context_usage.resolve_window_failed", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return 0


async def _latest_context_mutation_ms(session_id: str, messages: List[Any]) -> int:
    latest = 0
    for message in messages:
        if _is_summary_message(message):
            latest = max(latest, _time_created_ms(message))
        latest = max(latest, await _latest_compacted_part_ms(session_id, message))
    return latest


async def _latest_compacted_part_ms(session_id: str, message: Any) -> int:
    message_id = getattr(message, "id", "")
    if not message_id:
        return 0
    try:
        parts = await Message.parts(message_id, session_id)
    except Exception:
        return 0

    latest = 0
    for part in parts:
        state = getattr(part, "state", None)
        time_info = getattr(state, "time", None) if state is not None else None
        if isinstance(time_info, dict):
            latest = max(latest, _coerce_int(time_info.get("compacted")))
    return latest
