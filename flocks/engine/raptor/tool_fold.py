"""
Raptor dynamic tool folding.

When the agent's tool list is large, sending every schema to the LLM costs
many prompt tokens and increases latency.  This module implements *dynamic
tool folding*: the full list is replaced by three lightweight proxy schemas
that let the model discover and invoke real tools on demand.

Proxy tools
-----------
``raptor_tool_search(query)``
    Fuzzy-match ``query`` against tool names and descriptions.
    Returns a ranked JSON list of ``{name, description}`` entries.

``raptor_tool_describe(name)``
    Return the full parameter schema for one tool by exact name.

``raptor_tool_call(name, args)``
    Execute any tool in the catalog by name with the supplied arguments.
    All Flocks permission and security checks apply.

Folding policy
--------------
* Tools with ``always_load=True`` in the catalog and tools explicitly
  declared by the current agent's ``tools`` list are **never folded** – they
  remain in the visible schema unchanged.
* Folding activates only when ``len(all_tools) > FOLD_THRESHOLD``
  (default 30, overridable via ``RAPTOR_TOOL_FOLD_THRESHOLD``).
* The three proxy schemas are injected alongside the always-visible core
  tools.

Usage in RaptorSessionRunner::

    tools, fold_catalog = maybe_fold_tools(all_tools, core_names)
    # Store fold_catalog on the processor for proxy dispatch.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.tool_fold")


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warn("raptor.tool_fold.invalid_int_env", {
            "name": name,
            "value": raw,
            "default": default,
        })
        return default


# Tool-count threshold above which folding activates.
FOLD_THRESHOLD: int = _read_int_env("RAPTOR_TOOL_FOLD_THRESHOLD", 30)

# Names reserved for the proxy tools injected by raptor.
PROXY_TOOL_NAMES: frozenset = frozenset({
    "raptor_tool_search",
    "raptor_tool_describe",
    "raptor_tool_call",
})


# ---------------------------------------------------------------------------
# Proxy tool schema builders
# ---------------------------------------------------------------------------

def _schema_tool_search() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "raptor_tool_search",
            "description": (
                "Search for available tools by name or description keyword. "
                "Use this when you are unsure which tool handles a task. "
                "Returns a JSON list of matching tool names and short descriptions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to match against tool names and descriptions.",
                    }
                },
                "required": ["query"],
            },
        },
    }


def _schema_tool_describe() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "raptor_tool_describe",
            "description": (
                "Return the full JSON schema (parameters and description) for a specific tool. "
                "Call raptor_tool_search first if you do not know the exact tool name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact name of the tool to inspect.",
                    }
                },
                "required": ["name"],
            },
        },
    }


def _schema_tool_call() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "raptor_tool_call",
            "description": (
                "Execute any available tool by name with the given arguments. "
                "Use raptor_tool_describe to learn the exact parameter schema before calling. "
                "The tool runs with full Flocks security and permission checks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact name of the tool to execute.",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Arguments to pass to the tool. "
                            "Must match the tool's declared parameter schema."
                        ),
                    },
                },
                "required": ["name", "args"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Core folding logic
# ---------------------------------------------------------------------------

def maybe_fold_tools(
    all_tools: List[Dict[str, Any]],
    core_tool_names: Optional[frozenset] = None,
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Conditionally fold a large tool list.

    Parameters
    ----------
    all_tools:
        The full list of tool schemas that would normally be sent to the LLM.
    core_tool_names:
        Set of tool names that must *never* be folded (always-load tools and
        agent-declared tools).

    Returns
    -------
    (schema_for_llm, fold_catalog)
        ``schema_for_llm``  – the list to pass to the provider.
        ``fold_catalog``    – the original full list (stored for proxy dispatch);
                              ``None`` when folding was not applied.
    """
    # Count non-proxy tools to decide whether folding is needed.
    non_proxy = [
        t for t in all_tools
        if t.get("function", {}).get("name", "") not in PROXY_TOOL_NAMES
    ]
    if len(non_proxy) <= FOLD_THRESHOLD:
        return all_tools, None  # No folding needed.

    core_names = core_tool_names or frozenset()
    core_tools: List[Dict[str, Any]] = []
    folded: List[Dict[str, Any]] = []

    for t in all_tools:
        name = t.get("function", {}).get("name", "")
        if name in core_names or name in PROXY_TOOL_NAMES:
            core_tools.append(t)
        else:
            folded.append(t)

    schema_for_llm = core_tools + [
        _schema_tool_search(),
        _schema_tool_describe(),
        _schema_tool_call(),
    ]

    folded_count = len(folded)
    log.info("raptor.tool_fold.activated", {
        "total": len(all_tools),
        "core_kept": len(core_tools),
        "folded": folded_count,
        "threshold": FOLD_THRESHOLD,
    })

    return schema_for_llm, all_tools  # Return full list as the catalog.


# ---------------------------------------------------------------------------
# Proxy handler implementations (called by RaptorStreamProcessor)
# ---------------------------------------------------------------------------

def handle_tool_search(query: str, catalog: List[Dict[str, Any]]) -> str:
    """Return a JSON string listing tools whose name or description match *query*."""
    q = query.lower()
    results = []
    for t in catalog:
        fn = t.get("function", {})
        name: str = fn.get("name", "")
        desc: str = fn.get("description", "")
        if q in name.lower() or q in desc.lower():
            short_desc = (desc[:120] + "…") if len(desc) > 120 else desc
            results.append({"name": name, "description": short_desc})

    if not results:
        return json.dumps(
            {
                "results": [],
                "message": (
                    f"No tools matched '{query}'. "
                    "Try a broader or different keyword."
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)


def handle_tool_describe(name: str, catalog: List[Dict[str, Any]]) -> str:
    """Return the full schema JSON for *name*, or an error with close matches."""
    for t in catalog:
        fn = t.get("function", {})
        if fn.get("name") == name:
            return json.dumps({"tool": name, "schema": fn}, ensure_ascii=False, indent=2)

    close = [
        t.get("function", {}).get("name", "")
        for t in catalog
        if name.lower() in t.get("function", {}).get("name", "").lower()
    ]
    hint = f" Close matches: {close[:5]}." if close else ""
    return json.dumps(
        {"error": f"Tool '{name}' not found in catalog.{hint}"},
        ensure_ascii=False,
    )
