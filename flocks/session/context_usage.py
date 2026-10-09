"""Session context-usage snapshot helpers.

This module exposes the context usage shown by the Web UI.  Provider-reported
usage is preferred when it reflects the current prompt, while the existing
message/part estimator is used as the fallback for providers that do not emit
token usage or after compaction changes the prompt shape.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from flocks.provider.provider import Provider
from flocks.session.message import Message
from flocks.session.prompt import SessionPrompt, TurnPromptContext
from flocks.session.session import SessionInfo
from flocks.utils.log import Log


log = Log.create(service="context-usage")

UsageSource = Literal["observed", "estimated"]
DELEGATION_TOOLS = {"delegate_task", "task"}
ZERO_VISIBLE_SEGMENTS = {"agentDelegation"}
RECENT_USER_MESSAGE_IDS_LIMIT = 20

# Estimate of the messages that fed an observed model call, recorded the first
# time that observation is examined.  The session loop examines it right after
# the step, before any cleanup runs, so re-estimating the same messages later
# shows how many tokens pruning, truncation or archiving removed since — which
# the provider-reported figure cannot reflect until the next model call.
_OBSERVATION_BASELINES: "OrderedDict[Tuple[str, str], int]" = OrderedDict()
_OBSERVATION_BASELINES_MAX = 1024


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
    # Newest user messages this snapshot counts, so a client can tell which of
    # its optimistic prompt estimates the snapshot already includes.
    recent_user_message_ids: List[str] = Field(default_factory=list, alias="recentUserMessageIDs")


class _ObservedTokens(BaseModel):
    message_id: str
    created_ms: int
    used_tokens: int
    prompt_tokens: int
    index: int = -1


def has_observation_baseline(session_id: str, message_id: str) -> bool:
    """Whether the pre-cleanup estimate for an observed call is known."""
    return (session_id, message_id) in _OBSERVATION_BASELINES


def reset_observation_baselines(session_id: Optional[str] = None) -> None:
    """Drop recorded baselines (all, or one session's)."""
    if session_id is None:
        _OBSERVATION_BASELINES.clear()
        return
    for key in [key for key in _OBSERVATION_BASELINES if key[0] == session_id]:
        _OBSERVATION_BASELINES.pop(key, None)


async def estimate_observed_context_tokens(
    session_id: str,
    messages: List[Any],
    observed_index: int,
    observed_total: int,
) -> Tuple[int, int, int]:
    """Current prompt size derived from the latest provider-reported usage.

    ``observed_total`` is input + cache-read + output + reasoning tokens of
    ``messages[observed_index]``.  The result adds what was produced after
    that call (its tool results and later messages) and subtracts what
    cleanup removed from the earlier messages since the call.  The session
    loop's compaction trigger and the composer meter both use this, so the
    meter shows the number compaction actually acts on.

    Returns ``(effective_tokens, unobserved_tokens, freed_tokens)``.
    """
    observed_id = str(getattr(messages[observed_index], "id", "") or "")
    prompt_tokens_now = await SessionPrompt.estimate_full_context_tokens(
        session_id,
        messages[:observed_index],
    )
    key = (session_id, observed_id)
    baseline = _OBSERVATION_BASELINES.get(key)
    if baseline is None:
        baseline = prompt_tokens_now
        _OBSERVATION_BASELINES[key] = baseline
        while len(_OBSERVATION_BASELINES) > _OBSERVATION_BASELINES_MAX:
            _OBSERVATION_BASELINES.popitem(last=False)
    else:
        _OBSERVATION_BASELINES.move_to_end(key)
    freed_tokens = max(0, baseline - prompt_tokens_now)
    tool_result_tokens = await SessionPrompt.estimate_tool_result_tokens(
        session_id,
        observed_id,
    )
    later_tokens = await SessionPrompt.estimate_full_context_tokens(
        session_id,
        messages[observed_index + 1:],
    )
    unobserved_tokens = tool_result_tokens + later_tokens
    effective_tokens = max(0, observed_total - freed_tokens) + unobserved_tokens
    return effective_tokens, unobserved_tokens, freed_tokens


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
    footprint.  If the provider emitted usage for a model call we use that
    observed value, adjusted by ``estimate_observed_context_tokens`` for what
    was added or cleaned up since — the same figure the session loop compares
    against the compaction threshold.  Otherwise we fall back to the
    estimate.
    """
    active_messages = await Message.list(session_id)

    breakdown_tokens, latest_compacted_part_ms = await _estimate_message_breakdown(
        session_id,
        active_messages,
    )
    message_estimate_tokens = max(
        await _estimate_messages(session_id, active_messages),
        sum(breakdown_tokens.values()),
    )
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

    latest_observed = latest_observation(active_messages)
    if latest_observed is not None and not has_observation_baseline(
        session_id,
        latest_observed.message_id,
    ):
        latest_context_mutation_ms = max(
            _latest_summary_message_ms(active_messages),
            latest_compacted_part_ms,
        )
        if latest_observed.created_ms and latest_observed.created_ms < latest_context_mutation_ms:
            # Cleanup already changed the prompt and nobody recorded what it
            # looked like before, so the observed figure cannot be adjusted.
            latest_observed = None

    observed_tokens: Optional[int] = None
    last_message_id: Optional[str] = None
    source: UsageSource = "estimated"
    used_tokens = estimated_tokens
    if latest_observed is not None:
        observed_tokens = latest_observed.used_tokens
        last_message_id = latest_observed.message_id
        # Same number the session loop compares against the compaction
        # threshold.  Taking ``max`` with the estimate made the meter read
        # higher whenever the estimate over-counts (history reasoning most
        # adapters never send back, CJK text on thrifty tokenizers), so it
        # could sit near full without compaction ever starting.
        used_tokens, unobserved_tokens, _freed = await estimate_observed_context_tokens(
            session_id,
            active_messages,
            latest_observed.index,
            latest_observed.used_tokens,
        )
        # What cleanup freed is an estimate, and it over-counts history the
        # provider never re-sent (reasoning on most adapters), so after a
        # compaction it could eat into the system prompt and tool definitions,
        # which no cleanup removes.
        used_tokens = max(
            used_tokens,
            system_prompt_tokens + tool_definition_tokens + unobserved_tokens,
        )
        source = "observed"

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

    return ContextUsageSnapshot(
        sessionID=session_id,
        usedTokens=used_tokens,
        contextWindow=context_window,
        percent=percent,
        source=source,
        lastMessageID=last_message_id,
        observedTokens=observed_tokens,
        estimatedTokens=estimated_tokens,
        compactedTokens=0,
        providerID=provider_id,
        modelID=model_id,
        segments=segments,
        excludedSegments=[],
        recentUserMessageIDs=_recent_user_message_ids(active_messages),
    )


def _recent_user_message_ids(messages: List[Any]) -> List[str]:
    ids: List[str] = []
    for message in reversed(messages):
        if _role_value(message) != "user":
            continue
        message_id = getattr(message, "id", None)
        if message_id:
            ids.append(str(message_id))
            if len(ids) >= RECENT_USER_MESSAGE_IDS_LIMIT:
                break
    ids.reverse()
    return ids


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


def _latest_summary_message_ms(messages: List[Any]) -> int:
    latest = 0
    for message in messages:
        if _is_summary_message(message):
            latest = max(latest, _time_created_ms(message))
    return latest


def _observed_tokens_for_message(message: Any, index: int = -1) -> Optional[_ObservedTokens]:
    if _role_value(message) != "assistant" or _is_summary_message(message):
        return None

    data = token_usage_to_dict(getattr(message, "tokens", None))
    cache = data.get("cache") or {}
    # ``input`` excludes cached prompt tokens (Anthropic reports reads and
    # writes separately), so both are added back.  Leaving out writes made a
    # cold-cache call look nearly empty.
    prompt_tokens = (
        _coerce_int(data.get("input"))
        + _coerce_int(cache.get("read"))
        + _coerce_int(cache.get("write"))
    )
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
        index=index,
    )


def latest_observation(messages: List[Any]) -> Optional[_ObservedTokens]:
    """Newest assistant call in ``messages`` that reported provider usage."""
    for index in range(len(messages) - 1, -1, -1):
        observation = _observed_tokens_for_message(messages[index], index)
        if observation is not None:
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

        from flocks.config import Config
        from flocks.project.project import Project

        try:
            config = await Config.get()
            config_instructions = tuple(config.instructions or ())
        except Exception:
            config_instructions = ()

        session_directory = (
            getattr(session, "directory", None) if session is not None else None
        )
        worktree = (
            Project.worktree_for_directory(session_directory)
            if session_directory
            else None
        )
        prompt_blocks = await SessionPrompt.build_system_prompt_blocks(
            session_id=session_id,
            session_directory=session_directory,
            agent_name=getattr(agent, "name", agent_name) if agent is not None else agent_name,
            agent_prompt=getattr(agent, "prompt", None) if agent is not None else None,
            agent_skill_names=getattr(agent, "skills", ()) if agent is not None else (),
            prompt_profile=(
                getattr(agent, "prompt_profile", "standard")
                if agent is not None
                else "standard"
            ),
            provider_id=provider_id,
            model_id=model_id,
            prompt_tool_names=prompt_tool_names,
            turn_context=TurnPromptContext(
                worktree=worktree,
                config_instructions=config_instructions,
                tool_revision=ToolRegistry.revision(),
            ),
        )
        return sum(
            SessionPrompt.count_tokens(block.content)
            for block in prompt_blocks
        )
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


async def _estimate_message_breakdown(session_id: str, messages: List[Any]) -> tuple[Dict[str, int], int]:
    tokens_by_key = {
        "conversation": 0,
        "reasoning": 0,
        "tools": 0,
        "skillLoad": 0,
        "agentDelegation": 0,
    }
    latest_compacted_part_ms = 0
    for message in messages:
        message_id = _field_value(message, "id")
        parts: List[Any] = []
        if message_id:
            try:
                parts = await Message.parts(message_id, session_id)
            except Exception as exc:
                log.debug("context_usage.breakdown_parts_failed", {
                    "message_id": message_id,
                    "error": str(exc),
                })
        if not parts:
            # Same rule as SessionPrompt._tokens_for_message: an in-memory
            # assistant message repeats its text part as ``content``.
            content = _field_value(message, "content", "")
            tokens_by_key["conversation"] += SessionPrompt.count_tokens(content or "")
            continue

        for part in parts:
            part_type = _field_value(part, "type", "")
            if part_type == "text":
                if bool(_field_value(part, "ignored", False)):
                    continue
                tokens_by_key["conversation"] += SessionPrompt.count_tokens(_field_value(part, "text", "") or "")
                continue
            if part_type in {"reasoning", "thinking"}:
                tokens_by_key["reasoning"] += SessionPrompt.count_tokens(_field_value(part, "text", "") or "")
                continue
            if part_type == "agent":
                tokens_by_key["agentDelegation"] += _estimate_agent_part_tokens(part)
                continue
            if part_type != "tool":
                continue
            state = _field_value(part, "state")
            if state is None:
                continue
            latest_compacted_part_ms = max(
                latest_compacted_part_ms,
                _compacted_time_ms(state),
            )
            tokens_by_key[_context_key_for_tool(_tool_name_for_part(part))] += _estimate_tool_state_tokens(state)
    return tokens_by_key, latest_compacted_part_ms


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


def _estimate_agent_part_tokens(part: Any) -> int:
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

    if _compacted_time_ms(state) > 0:
        return total + 10

    tool_output = _field_value(state, "output")
    if tool_output:
        total += SessionPrompt.count_tokens(
            tool_output if isinstance(tool_output, str) else str(tool_output)
        )
    return total


def _compacted_time_ms(state: Any) -> int:
    time_info = _field_value(state, "time")
    if not isinstance(time_info, dict):
        return 0
    return _coerce_int(time_info.get("compacted"))


def _resolve_message_model(messages: List[Any]) -> tuple[Optional[str], Optional[str]]:
    for message in reversed(messages):
        role = _role_value(message)
        if role == "assistant":
            provider_id = getattr(message, "providerID", None)
            model_id = getattr(message, "modelID", None)
            if provider_id == "builtin" and model_id == "command":
                continue
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
