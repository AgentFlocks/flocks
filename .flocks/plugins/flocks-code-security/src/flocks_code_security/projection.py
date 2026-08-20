"""Subset-only callable-tool projection for code-security agents."""

from __future__ import annotations

from typing import Any, Mapping

from flocks_code_security.tools import is_registered_audit_tool
from flocks.tool.registry import ToolRegistry


RESOLVER_NAME = "flocks-code-security"

# Security ceiling for callable tools. Agent YAML declares the intended tools;
# tests require those declarations to match this independent runtime boundary.
AGENT_TOOLS = {
    "code-security": [
        "audit_prepare",
        "audit_run_workers",
        "audit_wait_workers",
        "audit_status",
        "audit_adjudication_context",
        "audit_submit_adjudication",
        "audit_finalize",
        "audit_cancel",
        "question",
    ],
    "code-security-threat-modeler": [
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_threat_model",
    ],
    "code-security-baseline": [
        "audit_threat_model_context",
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_candidate",
        "audit_submit_coverage",
    ],
    "code-security-verifier": [
        "audit_verification_subject",
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_verdict",
    ],
    "code-security-prober": [
        "audit_probe_subject",
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_probe",
    ],
}


def _is_canonical_question(tool_info: Any) -> bool:
    if getattr(tool_info, "name", None) != "question":
        return False
    from flocks.tool.system.question import question_tool

    current_tool = ToolRegistry.get("question")
    return (
        current_tool is not None
        and current_tool.info is tool_info
        and current_tool.handler is question_tool
    )


def code_security_tool_projection(
    tool_infos: list[Any],
    context: Mapping[str, Any],
) -> list[Any]:
    agent_name = str(context.get("agent") or "")
    allowed = AGENT_TOOLS.get(agent_name)
    if allowed is None:
        return tool_infos
    candidate_names = {
        str(item.get("name") or "")
        for item in context.get("candidates", [])
        if isinstance(item, Mapping)
    }
    if (
        agent_name == "code-security"
        and "audit_submit_adjudication" in candidate_names
        and "audit_prepare" not in candidate_names
    ):
        allowed = [
            "audit_adjudication_context",
            "audit_submit_adjudication",
        ]
    allowed_names = set(allowed)
    return [
        tool
        for tool in tool_infos
        if getattr(tool, "name", None) in allowed_names
        and (
            is_registered_audit_tool(tool)
            or _is_canonical_question(tool)
        )
    ]


def register_projection() -> None:
    from flocks.session.callable_schema import register_callable_tool_resolver

    register_callable_tool_resolver(RESOLVER_NAME, code_security_tool_projection)
