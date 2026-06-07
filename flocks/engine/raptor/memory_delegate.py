"""In-memory delegated agent runner for the raptor engine.

This module is intentionally used only by ``RaptorStreamProcessor``.  It does
not register or alter the global ``delegate_task`` tool, so the native loop keeps
its existing behavior.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional

from flocks.agent.registry import Agent, is_delegatable
from flocks.agent.toolset import agent_declares_tool
from flocks.engine.raptor.tool_fold import (
    PROXY_TOOL_NAMES,
    handle_tool_describe,
    handle_tool_search,
    maybe_fold_tools,
)
from flocks.provider.options import build_provider_options
from flocks.provider.provider import ChatMessage, Provider
from flocks.engine.raptor.retry import (
    RaptorRetryContext,
    looks_like_context_overflow,
    looks_like_rate_limit,
)
from flocks.session.callable_schema import resolve_dynamic_always_load_tool_names
from flocks.session.lifecycle.compaction import CompactionPolicy
from flocks.session.lifecycle.compaction.models import (
    DEFAULT_COMPACTION_PROMPT,
    resolve_tool_preserve_turns,
)
from flocks.session.lifecycle.compaction.summary import (
    build_fallback_summary,
    summarize_single_pass,
)
from flocks.session.prompt import SessionPrompt
from flocks.skill.skill import Skill
from flocks.tool.catalog import (
    annotate_tool_description_with_provider_version,
    get_always_load_tool_names,
    get_tool_catalog_metadata,
)
from flocks.tool.delegate_task_constants import (
    CATEGORY_PROMPT_APPENDS,
    DEFAULT_CATEGORIES,
)
from flocks.tool.registry import ToolRegistry, ToolResult
from flocks.utils.id import Identifier
from flocks.utils.log import Log

log = Log.create(service="engine.raptor.memory_delegate")

MAX_MEMORY_DELEGATE_STEPS = 70
MAX_MEMORY_DELEGATE_OUTPUT_CHARS = 20_000
MIN_MEMORY_COMPACTION_MESSAGES = 6
SUMMARY_INPUT_TOKEN_BUDGET_RATIO = 0.60
COMPACTED_TOOL_OUTPUT = "[tool output compacted by memory delegate policy]"

MemoryToolExecutor = Callable[
    [str, Dict[str, Any], str, str, Optional[Callable[[Dict[str, Any]], None]]],
    Awaitable[ToolResult],
]


async def _resolve_skill_content(load_skills: Optional[List[str]]) -> str:
    if isinstance(load_skills, str):
        load_skills = [load_skills]
    chunks: List[str] = []
    for raw_name in load_skills or []:
        name = str(raw_name).strip()
        if not name:
            continue
        try:
            skill = await Skill.get(name)
            if skill and getattr(skill, "content", None):
                chunks.append(str(skill.content))
        except Exception as exc:
            log.debug("memory_delegate.skill_load_failed", {
                "skill": name,
                "error": str(exc),
            })
    return "\n\n".join(chunks)


def _parse_model(raw: Optional[str]) -> Dict[str, Optional[str]]:
    if not raw:
        return {"provider_id": None, "model_id": None}
    if "/" in raw:
        provider_id, model_id = raw.split("/", 1)
        return {"provider_id": provider_id or None, "model_id": model_id or None}
    return {"provider_id": None, "model_id": raw}


async def _resolve_delegate_agent(
    *,
    category: Optional[str],
    subagent_type: Optional[str],
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    if category and subagent_type:
        raise ValueError("Provide either category or subagent_type, not both.")
    if not category and not subagent_type:
        raise ValueError("Must provide either category or subagent_type.")

    if subagent_type:
        if not is_delegatable(subagent_type):
            raise ValueError(f"Agent '{subagent_type}' cannot be delegated to.")
        return subagent_type, None, None, None

    from flocks.config.config import Config

    cfg = await Config.get()
    category_configs = {**DEFAULT_CATEGORIES, **(cfg.categories or {})}
    config = category_configs.get(category or "")
    if not config:
        available = ", ".join(category_configs.keys())
        raise ValueError(f"Unknown category '{category}'. Available: {available}")

    raw_model = config.get("model") if isinstance(config, dict) else getattr(config, "model", None)
    parsed = _parse_model(raw_model)
    prompt_append = (
        (config.get("prompt_append") if isinstance(config, dict) else getattr(config, "prompt_append", None))
        or CATEGORY_PROMPT_APPENDS.get(category or "")
    )
    return "rex-junior", parsed["provider_id"], parsed["model_id"], prompt_append


async def _build_tool_schema(
    agent_name: str,
) -> tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    agent = await Agent.get(agent_name)
    tools: List[Dict[str, Any]] = []
    core_names: set[str] = set(PROXY_TOOL_NAMES)
    dynamic_always_load_names = await resolve_dynamic_always_load_tool_names()
    always_load_names = get_always_load_tool_names() | dynamic_always_load_names
    for tool_info in ToolRegistry.list_tools():
        if not getattr(tool_info, "enabled", True):
            continue
        if tool_info.name in {"invalid", "_noop", "delegate_task", "task"}:
            continue
        metadata = get_tool_catalog_metadata(tool_info.name, tool_info)
        agent_declared = bool(agent and agent_declares_tool(agent, tool_info.name))
        always_load = bool(metadata.always_load or tool_info.name in always_load_names)
        if agent and not (agent_declared or always_load):
            continue
        if agent_declared or always_load:
            core_names.add(tool_info.name)
        schema = tool_info.get_schema()
        tools.append({
            "type": "function",
            "function": {
                "name": tool_info.name,
                "description": annotate_tool_description_with_provider_version(
                    tool_info,
                    tool_info.description,
                ),
                "parameters": schema.to_json_schema(),
            },
        })
    return maybe_fold_tools(tools, frozenset(core_names))


def _tool_calls_from_response(response: Any) -> List[Dict[str, Any]]:
    raw_calls = getattr(response, "tool_calls", None) or []
    normalized: List[Dict[str, Any]] = []
    for idx, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name")
        args = fn.get("arguments") if "arguments" in fn else call.get("arguments", {})
        parse_error = None
        arguments_for_replay = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception as exc:
                parse_error = str(exc)
                args = {"_raw_arguments": args}
        if not isinstance(args, dict):
            args = {}
        if name:
            normalized.append({
                "id": call.get("id") or Identifier.create("call"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments_for_replay,
                },
                "_name": name,
                "_args": args,
                "_parse_error": parse_error,
                "_index": idx,
            })
    return normalized


def _looks_like_tool_intent_without_call(
    *,
    prompt: str,
    content: str,
    tools: List[Dict[str, Any]],
    tool_count: int,
) -> bool:
    """Detect an intermediate acknowledgement that should not end delegation."""
    if tool_count > 0 or not tools:
        return False
    text = (content or "").strip()
    if not text or len(text) > 1200:
        return False

    lowered = text.lower()
    future_ack = bool(re.search(
        r"\b(i['’]ll|i will|let me|i(?:'|’)?m going to|i will start|i can do that)\b",
        lowered,
    ))
    chinese_future_ack = any(marker in text for marker in ("我将", "我会", "让我", "先调用", "开始调用"))
    if not (future_ack or chinese_future_ack):
        return False

    action_markers = (
        "call",
        "use",
        "invoke",
        "look into",
        "look at",
        "inspect",
        "scan",
        "check",
        "analyz",
        "review",
        "explore",
        "read",
        "open",
        "run",
        "test",
        "debug",
        "search",
        "find",
        "query",
        "summarize",
        "调用",
        "使用",
        "检查",
        "查询",
        "搜索",
        "分析",
        "读取",
    )
    if not any(marker in lowered or marker in text for marker in action_markers):
        return False

    tool_names = {
        str((tool.get("function") or {}).get("name") or "").lower()
        for tool in tools
        if isinstance(tool, dict)
    }
    tool_names.discard("")
    mentions_tool_name = any(name and name in lowered for name in tool_names)
    mentions_tool_protocol = any(marker in lowered for marker in ("tool", "function", "api", "call `"))

    task_markers = (
        "device",
        "alert",
        "threat",
        "ioc",
        "host",
        "endpoint",
        "repo",
        "repository",
        "codebase",
        "file",
        "path",
        "日志",
        "告警",
        "设备",
        "主机",
        "威胁",
        "文件",
    )
    prompt_text = (prompt or "").lower()
    task_likely_needs_tools = any(marker in prompt_text for marker in task_markers)
    return mentions_tool_name or mentions_tool_protocol or task_likely_needs_tools


def _strip_think_blocks(text: str) -> str:
    text = text or ""
    return re.sub(
        r"<(?:think|thinking|reasoning)[^>]*>.*?</(?:think|thinking|reasoning)>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()


def _catalog_contains_tool(
    catalog: Optional[List[Dict[str, Any]]],
    tool_name: str,
) -> bool:
    if catalog is None:
        return True
    return any(
        item.get("function", {}).get("name") == tool_name
        for item in catalog
    )


def _resolve_proxy_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    fold_catalog: Optional[List[Dict[str, Any]]],
) -> tuple[str, Dict[str, Any], Optional[ToolResult], str]:
    """Resolve in-memory proxy tools to local results or real tool calls."""
    if tool_name not in PROXY_TOOL_NAMES:
        return tool_name, tool_args, None, tool_name

    if fold_catalog is None:
        return tool_name, tool_args, ToolResult(
            success=False,
            error=f"Proxy tool '{tool_name}' called without an available folded catalog.",
        ), tool_name

    if tool_name == "raptor_tool_search":
        return tool_name, tool_args, ToolResult(
            success=True,
            output=handle_tool_search(str(tool_args.get("query") or ""), fold_catalog),
            title="raptor_tool_search",
        ), tool_name

    if tool_name == "raptor_tool_describe":
        return tool_name, tool_args, ToolResult(
            success=True,
            output=handle_tool_describe(str(tool_args.get("name") or ""), fold_catalog),
            title="raptor_tool_describe",
        ), tool_name

    real_name = str(tool_args.get("name") or "").strip()
    real_args = tool_args.get("args") or {}
    if not real_name:
        return tool_name, tool_args, ToolResult(
            success=False,
            error="raptor_tool_call: 'name' argument is required.",
        ), tool_name
    if not isinstance(real_args, dict):
        return tool_name, tool_args, ToolResult(
            success=False,
            error="raptor_tool_call: 'args' must be an object.",
        ), tool_name
    if not _catalog_contains_tool(fold_catalog, real_name):
        return tool_name, tool_args, ToolResult(
            success=False,
            error=f"raptor_tool_call: tool '{real_name}' is not available.",
        ), tool_name
    return real_name, real_args, None, tool_name


def _message_content_text(message: ChatMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _estimate_chat_tokens(messages: List[ChatMessage], tools: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += SessionPrompt.estimate_tokens(_message_content_text(message))
        if message.tool_calls:
            total += SessionPrompt.estimate_tokens(
                json.dumps(message.tool_calls, ensure_ascii=False, default=str)
            )
    if tools:
        total += SessionPrompt.estimate_tokens(
            json.dumps(tools, ensure_ascii=False, default=str)
        )
    return total


def _message_token_estimate(message: ChatMessage) -> int:
    total = SessionPrompt.estimate_tokens(_message_content_text(message))
    if message.tool_calls:
        total += SessionPrompt.estimate_tokens(
            json.dumps(message.tool_calls, ensure_ascii=False, default=str)
        )
    return total


def _resolve_compaction_policy(provider_id: str, model_id: str) -> CompactionPolicy:
    try:
        context_window, max_output, max_input = Provider.resolve_model_info(provider_id, model_id)
    except Exception:
        context_window, max_output, max_input = 0, 0, None
    return CompactionPolicy.from_model(
        context_window=context_window or 128_000,
        max_output_tokens=max_output or 4096,
        max_input_tokens=max_input,
    )


async def _resolve_provider_client(provider_id: str) -> Any:
    provider = Provider.get(provider_id)
    if not provider:
        raise ValueError(f"Provider '{provider_id}' not found.")
    try:
        await Provider.apply_config(provider_id=provider_id)
    except Exception as exc:
        log.debug("memory_delegate.provider_config_failed", {
            "provider": provider_id,
            "error": str(exc),
        })
    if not provider.is_configured():
        raise ValueError(f"Provider '{provider_id}' not configured.")
    return provider


async def _call_provider_stream(
    provider: Any,
    model_id: str,
    messages: List[ChatMessage],
    tools: List[Dict[str, Any]],
    provider_options: Dict[str, Any],
) -> tuple[str, Optional[List[Dict[str, Any]]]]:
    """Call the provider via chat_stream and return (text_content, tool_calls).

    Using chat_stream reuses the provider's existing think_extractor logic so
    that reasoning/<think> blocks are naturally separated from text, and
    tool_calls are accumulated from the terminal streaming chunk — the same
    path used by the main session runner.
    """
    text_parts: List[str] = []
    tool_calls: Optional[List[Dict[str, Any]]] = None

    async for chunk in provider.chat_stream(
        model_id, messages, tools=tools, **provider_options
    ):
        event_type = getattr(chunk, "event_type", None)
        # Collect only text deltas; skip reasoning/think chunks.
        if event_type not in ("reasoning", "think"):
            delta = getattr(chunk, "delta", "") or ""
            if delta:
                text_parts.append(delta)
        if chunk.tool_calls:
            tool_calls = chunk.tool_calls

    return "".join(text_parts), tool_calls


def _is_assistant_with_tool_calls(message: ChatMessage) -> bool:
    return message.role == "assistant" and bool(message.tool_calls)


def _is_tool_message(message: ChatMessage) -> bool:
    return message.role == "tool"


def _assistant_tool_call_ids(message: ChatMessage) -> List[str]:
    ids: List[str] = []
    for call in message.tool_calls or []:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids


def _tool_message_call_id(message: ChatMessage) -> Optional[str]:
    call_id = getattr(message, "tool_call_id", None)
    return str(call_id) if call_id else None


def _advance_past_tool_block(messages: List[ChatMessage], start: int) -> int:
    idx = start
    while idx < len(messages) and _is_tool_message(messages[idx]):
        idx += 1
    return idx


def _assistant_pair_is_complete(messages: List[ChatMessage], assistant_idx: int) -> bool:
    expected = set(_assistant_tool_call_ids(messages[assistant_idx]))
    if not expected:
        return True
    found: set[str] = set()
    idx = assistant_idx + 1
    while idx < len(messages) and _is_tool_message(messages[idx]):
        call_id = _tool_message_call_id(messages[idx])
        if call_id:
            found.add(call_id)
        idx += 1
    return expected.issubset(found)


def _compact_tool_message(message: ChatMessage) -> ChatMessage:
    tool_name = _tool_prune_name(message)
    return message.model_copy(update={
        "content": f"{COMPACTED_TOOL_OUTPUT} tool={tool_name}",
    })


def _tool_prune_name(message: ChatMessage) -> str:
    custom = message.custom_settings or {}
    raptor_meta = custom.get("raptor") if isinstance(custom, dict) else None
    if isinstance(raptor_meta, dict):
        real_tool = raptor_meta.get("real_tool")
        if real_tool:
            return str(real_tool)
    return message.name or "unknown"


def _make_child_metadata_callback(
    parent_callback: Optional[Callable[[Dict[str, Any]], None]],
    *,
    tool_name: str,
    call_id: str,
) -> Optional[Callable[[Dict[str, Any]], None]]:
    if parent_callback is None:
        return None

    def _callback(metadata: Dict[str, Any]) -> None:
        parent_callback({
            "memoryTool": {
                "name": tool_name,
                "callID": call_id,
                "metadata": metadata,
            }
        })

    return _callback


def _result_to_tool_content(result: ToolResult) -> str:
    tool_output = result.output if result.success else f"Error: {result.error}"
    if not isinstance(tool_output, str):
        tool_output = json.dumps(tool_output, ensure_ascii=False, default=str)
    return tool_output[:MAX_MEMORY_DELEGATE_OUTPUT_CHARS]


def _apply_tool_prune_policy(messages: List[ChatMessage]) -> tuple[List[ChatMessage], int]:
    """Apply the shared per-tool retention policy to in-memory tool results."""
    pruned: List[ChatMessage] = list(messages)
    user_turns_seen = 0
    compacted = 0

    for idx in range(len(pruned) - 1, -1, -1):
        message = pruned[idx]
        if message.role == "user":
            user_turns_seen += 1
            continue
        if not _is_tool_message(message):
            continue

        tool_name = _tool_prune_name(message)
        keep_turns = resolve_tool_preserve_turns(tool_name)
        if keep_turns == -1:
            continue
        if user_turns_seen < keep_turns:
            continue
        if _message_content_text(message).startswith(COMPACTED_TOOL_OUTPUT):
            continue

        pruned[idx] = _compact_tool_message(message)
        compacted += 1

    return pruned, compacted


def _safe_tail_start(messages: List[ChatMessage], requested_tail: int) -> int:
    """Return a tail boundary that does not split assistant/tool-call pairs."""
    if requested_tail <= 0 or requested_tail >= len(messages):
        return 0

    start = len(messages) - requested_tail

    while start < len(messages):
        # Never start with a tool result whose assistant tool_call was compacted.
        if _is_tool_message(messages[start]):
            start = _advance_past_tool_block(messages, start)
            continue

        # If the message before the boundary has tool_calls and we are cutting
        # between that assistant and its tool results, move past the whole tool
        # result block.
        prev_idx = start - 1
        if prev_idx >= 0 and _is_assistant_with_tool_calls(messages[prev_idx]):
            advanced = _advance_past_tool_block(messages, start)
            if advanced != start:
                start = advanced
                continue

        # Every assistant retained in the tail must have all of its tool result
        # messages retained as well. If not, drop that incomplete assistant/tool
        # block from the preserved tail.
        invalid_idx: Optional[int] = None
        for idx in range(start, len(messages)):
            if _is_assistant_with_tool_calls(messages[idx]) and not _assistant_pair_is_complete(messages, idx):
                invalid_idx = idx
                break
        if invalid_idx is None:
            return min(start, len(messages))

        start = _advance_past_tool_block(messages, invalid_idx + 1)

    return len(messages)


def _bounded_summary_messages(
    messages: List[ChatMessage],
    token_budget: int,
) -> List[ChatMessage]:
    """Keep summary input within a global budget while preserving recent signal."""
    if token_budget <= 0:
        return messages

    selected: List[ChatMessage] = []
    total = 0
    for message in reversed(messages):
        cost = _message_token_estimate(message)
        if selected and total + cost > token_budget:
            break
        if not selected and cost > token_budget:
            content = _message_content_text(message)
            char_budget = max(1_000, token_budget * 4)
            trimmed = (
                content[: char_budget // 2]
                + "\n...[truncated for summary budget]...\n"
                + content[-char_budget // 2 :]
            )
            selected.append(ChatMessage(role=message.role, content=trimmed))
            break
        selected.append(message)
        total += cost

    selected.reverse()
    while selected and _is_tool_message(selected[0]):
        selected.pop(0)
    return selected or messages[-1:]


async def _compact_memory_messages_if_needed(
    *,
    messages: List[ChatMessage],
    tools: List[Dict[str, Any]],
    provider_client: Any,
    provider_id: str,
    model_id: str,
    policy: CompactionPolicy,
    metadata_callback: Optional[Callable[[Dict[str, Any]], None]],
    compaction_count: int,
    force: bool = False,
) -> tuple[List[ChatMessage], int, bool]:
    if not force and len(messages) < MIN_MEMORY_COMPACTION_MESSAGES:
        return messages, compaction_count, False

    estimated_tokens = _estimate_chat_tokens(messages, tools)
    if not force and estimated_tokens < policy.preemptive_threshold:
        return messages, compaction_count, False

    pruned_messages, tool_outputs_compacted = _apply_tool_prune_policy(messages)
    if tool_outputs_compacted:
        pruned_tokens = _estimate_chat_tokens(pruned_messages, tools)
        log.info("memory_delegate.tool_outputs_compacted", {
            "provider": provider_id,
            "model": model_id,
            "tool_outputs": tool_outputs_compacted,
            "tokens_before": estimated_tokens,
            "tokens_after": pruned_tokens,
        })
        if not force and pruned_tokens < policy.preemptive_threshold:
            return pruned_messages, compaction_count, True
        messages = pruned_messages
        estimated_tokens = pruned_tokens

    preserve_last = 2 if force else max(2, policy.preserve_last)
    tail_start = _safe_tail_start(messages, preserve_last)
    if tail_start <= 1:
        return messages, compaction_count, bool(tool_outputs_compacted)

    system_message = messages[0] if messages and messages[0].role == "system" else None
    preserved_tail = messages[tail_start:]
    compactable_start = 1 if system_message else 0
    compactable = messages[compactable_start:tail_start]
    if not compactable:
        return messages, compaction_count, bool(tool_outputs_compacted)

    if metadata_callback:
        metadata_callback({
            "contextCompaction": {
                "mode": "memory",
                "estimatedTokens": estimated_tokens,
                "threshold": policy.preemptive_threshold,
                "count": compaction_count + 1,
            }
        })

    summary_token_budget = max(
        4_000,
        int(policy.usable_context * SUMMARY_INPUT_TOKEN_BUDGET_RATIO),
    )
    summary_messages = _bounded_summary_messages(compactable, summary_token_budget)
    target_chars = max(8_000, summary_token_budget * 4)
    summary_text = await summarize_single_pass(
        conversation_text="",
        prompt_text=DEFAULT_COMPACTION_PROMPT,
        target_chars=target_chars,
        provider_client=provider_client,
        model_id=model_id,
        max_tokens=policy.summary_max_tokens,
        chat_messages=summary_messages,
    )
    if not summary_text:
        summary_text = build_fallback_summary(compactable)

    summary_block = (
        "\n\n<InMemoryCompactionSummary>\n"
        "The delegated agent's earlier in-memory context was compacted. "
        "Continue using this summary as authoritative context.\n\n"
        f"{summary_text}\n"
        "</InMemoryCompactionSummary>"
    )
    next_messages: List[ChatMessage] = []
    if system_message:
        next_messages.append(system_message.model_copy(update={
            "content": _message_content_text(system_message) + summary_block,
        }))
    else:
        next_messages.append(ChatMessage(role="system", content=summary_block.strip()))
    next_messages.extend(preserved_tail)

    log.info("memory_delegate.context_compacted", {
        "provider": provider_id,
        "model": model_id,
        "before_messages": len(messages),
        "after_messages": len(next_messages),
        "estimated_tokens": estimated_tokens,
        "threshold": policy.preemptive_threshold,
        "count": compaction_count + 1,
    })
    return next_messages, compaction_count + 1, True


async def run_memory_delegate_task(
    *,
    parent_agent: str,
    provider_id: str,
    model_id: str,
    prompt: str,
    description: Optional[str],
    category: Optional[str],
    subagent_type: Optional[str],
    load_skills: Optional[List[str]],
    abort_event: Optional[Any],
    metadata_callback: Optional[Callable[[Dict[str, Any]], None]],
    tool_executor: MemoryToolExecutor,
) -> ToolResult:
    """Run a delegated task without creating a child session."""
    try:
        agent_name, category_provider, category_model, category_prompt = await _resolve_delegate_agent(
            category=category,
            subagent_type=subagent_type,
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    agent = await Agent.get(agent_name)
    if agent is None:
        return ToolResult(success=False, error=f"Delegated agent '{agent_name}' not found.")

    effective_provider = category_provider or provider_id
    effective_model = category_model or model_id
    if agent and getattr(agent, "model", None):
        model_cfg = agent.model
        effective_provider = getattr(model_cfg, "provider_id", None) or effective_provider
        effective_model = getattr(model_cfg, "model_id", None) or effective_model

    try:
        provider = await _resolve_provider_client(effective_provider)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))

    metadata_state: Dict[str, Any] = {
        "title": description or "Delegated task",
        "agent": agent_name,
        "mode": "memory",
    }

    def emit_metadata(update: Dict[str, Any]) -> None:
        if metadata_callback is None:
            return
        metadata_state.update(update)
        metadata_callback(dict(metadata_state))

    emit_metadata({})

    skill_content = await _resolve_skill_content(load_skills)
    system_parts = [
        (
            "You are a delegated in-memory agent. Complete the task and return a concise final result. "
            "Use the available tools whenever the task requires inspecting, searching, querying, "
            "or validating information. Do not describe future tool use; emit the tool call instead. "
            "Only provide a final result after completing required tool calls or when no tool is relevant. "
            "Do NOT use bash or any shell command merely to echo, announce, or stage actions — "
            "call the required data-gathering tools directly."
        ),
        f"Parent agent: {parent_agent}",
    ]
    if agent and getattr(agent, "prompt", None):
        system_parts.append(str(agent.prompt))
    if category_prompt:
        system_parts.append(category_prompt)
    if skill_content:
        system_parts.append(skill_content)

    messages: List[ChatMessage] = [
        ChatMessage(role="system", content="\n\n".join(system_parts)),
        ChatMessage(role="user", content=prompt),
    ]
    tools, fold_catalog = await _build_tool_schema(agent_name)
    active_provider = effective_provider
    active_model = effective_model
    provider_options = build_provider_options(active_provider, active_model)
    compaction_policy = _resolve_compaction_policy(active_provider, active_model)
    retry_ctx = RaptorRetryContext(active_provider, active_model)
    compaction_count = 0
    empty_response_retries = 0

    final_content = ""
    tool_count = 0
    intermediate_ack_retries = 0
    for step in range(MAX_MEMORY_DELEGATE_STEPS):
        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
            return ToolResult(success=False, error="Delegated task aborted.")

        messages, compaction_count, _ = await _compact_memory_messages_if_needed(
            messages=messages,
            tools=tools,
            provider_client=provider,
            provider_id=active_provider,
            model_id=active_model,
            policy=compaction_policy,
            metadata_callback=emit_metadata,
            compaction_count=compaction_count,
        )
        while True:
            try:
                content, raw_tool_calls = await _call_provider_stream(
                    provider,
                    active_model,
                    messages,
                    tools,
                    provider_options,
                )
                break
            except Exception as exc:
                err_text = str(exc)
                if looks_like_rate_limit(err_text) and retry_ctx.try_next_fallback():
                    active_provider = retry_ctx.current_provider
                    active_model = retry_ctx.current_model
                    try:
                        provider = await _resolve_provider_client(active_provider)
                    except Exception as provider_exc:
                        return ToolResult(success=False, error=str(provider_exc))
                    provider_options = build_provider_options(active_provider, active_model)
                    compaction_policy = _resolve_compaction_policy(active_provider, active_model)
                    emit_metadata({
                        "providerID": active_provider,
                        "modelID": active_model,
                        "fallback": True,
                    })
                    continue
                if not looks_like_context_overflow(err_text):
                    raise
                compacted_messages, next_count, changed = await _compact_memory_messages_if_needed(
                    messages=messages,
                    tools=tools,
                    provider_client=provider,
                    provider_id=active_provider,
                    model_id=active_model,
                    policy=compaction_policy,
                    metadata_callback=emit_metadata,
                    compaction_count=compaction_count,
                    force=True,
                )
                if not changed:
                    return ToolResult(success=False, error=err_text)
                messages = compacted_messages
                compaction_count = next_count
                try:
                    content, raw_tool_calls = await _call_provider_stream(
                        provider,
                        active_model,
                        messages,
                        tools,
                        provider_options,
                    )
                    break
                except Exception as retry_exc:
                    retry_text = str(retry_exc)
                    if looks_like_rate_limit(retry_text) and retry_ctx.try_next_fallback():
                        active_provider = retry_ctx.current_provider
                        active_model = retry_ctx.current_model
                        try:
                            provider = await _resolve_provider_client(active_provider)
                        except Exception as provider_exc:
                            return ToolResult(success=False, error=str(provider_exc))
                        provider_options = build_provider_options(active_provider, active_model)
                        compaction_policy = _resolve_compaction_policy(active_provider, active_model)
                        emit_metadata({
                            "providerID": active_provider,
                            "modelID": active_model,
                            "fallback": True,
                        })
                        continue
                    if looks_like_context_overflow(retry_text):
                        return ToolResult(
                            success=False,
                            error=(
                                "Delegated task context is still too large after "
                                f"in-memory compaction: {retry_exc}"
                            ),
                        )
                    raise
        calls = _tool_calls_from_response(
            SimpleNamespace(tool_calls=raw_tool_calls)
        )
        if not content.strip() and not calls:
            if empty_response_retries >= 1:
                return ToolResult(
                    success=False,
                    error="Delegated task received an empty model response after retry.",
                )
            empty_response_retries += 1
            messages.append(ChatMessage(
                role="user",
                content=(
                    "Your previous response was empty. Continue the delegated "
                    "task and either provide a final answer or call a tool."
                ),
            ))
            continue
        if not calls:
            visible_content = content.strip()
            looks_like_tool_intent = _looks_like_tool_intent_without_call(
                prompt=prompt,
                content=content,
                tools=tools,
                tool_count=tool_count,
            )
            if intermediate_ack_retries < 3 and (looks_like_tool_intent or not visible_content):
                intermediate_ack_retries += 1
                log.info("memory_delegate.intermediate_ack_continue", {
                    "agent": agent_name,
                    "provider": active_provider,
                    "model": active_model,
                    "step": step,
                    "attempt": intermediate_ack_retries,
                })
                messages.append(ChatMessage(role="assistant", content=content))
                messages.append(ChatMessage(
                    role="user",
                    content=(
                        "Continue now. Execute the required tool call(s) using the available "
                        "tool interface. Do not merely describe which tool you will call. "
                        "Only send the final answer after completing the delegated task."
                    ),
                ))
                continue
            if looks_like_tool_intent or not visible_content:
                return ToolResult(
                    success=False,
                    error=(
                        "Delegated task did not emit a real tool call after repeated continuation prompts. "
                        "The model only produced tool-use intent text."
                    ),
                    title=description or "Delegated task",
                    metadata={
                        "mode": "memory",
                        "agent": agent_name,
                        "toolCalls": tool_count,
                        "compactions": compaction_count,
                        "providerID": active_provider,
                        "modelID": active_model,
                    },
                )
            final_content = content
            break

        messages.append(ChatMessage(
            role="assistant",
            content=content,
            tool_calls=[
                {
                    "id": call["id"],
                    "type": "function",
                    "function": call["function"],
                }
                for call in calls
            ],
        ))

        for call in calls:
            tool_name = call["_name"]
            tool_args = call["_args"]
            call_id = call["id"]
            if call.get("_parse_error"):
                effective_tool_name = tool_name
                display_tool_name = tool_name
                result = ToolResult(
                    success=False,
                    error=(
                        "Invalid JSON tool arguments. Re-emit this tool call "
                        f"with valid JSON arguments. Parser error: {call['_parse_error']}"
                    ),
                )
            else:
                effective_tool_name, effective_args, early_result, display_tool_name = _resolve_proxy_tool(
                    tool_name,
                    tool_args,
                    fold_catalog,
                )
                if early_result is not None:
                    result = early_result
                elif effective_tool_name in {"delegate_task", "task"}:
                    result = ToolResult(
                        success=False,
                        error="In-memory delegates cannot spawn nested delegate_task calls.",
                    )
                else:
                    child_metadata_callback = _make_child_metadata_callback(
                        emit_metadata,
                        tool_name=effective_tool_name,
                        call_id=call_id,
                    )
                    result = await tool_executor(
                        effective_tool_name,
                        effective_args,
                        call_id,
                        agent_name,
                        child_metadata_callback,
                    )
            tool_count += 1
            messages.append(ChatMessage(
                role="tool",
                content=_result_to_tool_content(result),
                tool_call_id=call_id,
                name=display_tool_name,
                custom_settings={
                    "raptor": {
                        "real_tool": effective_tool_name,
                    }
                } if effective_tool_name != display_tool_name else {},
            ))
    else:
        return ToolResult(
            success=False,
            error=f"Delegated task exceeded {MAX_MEMORY_DELEGATE_STEPS} in-memory steps.",
        )

    return ToolResult(
        success=True,
        output=final_content or "(delegated task completed without text output)",
        title=description or "Delegated task",
        metadata={
            "mode": "memory",
            "agent": agent_name,
            "toolCalls": tool_count,
            "compactions": compaction_count,
            "providerID": active_provider,
            "modelID": active_model,
        },
    )
