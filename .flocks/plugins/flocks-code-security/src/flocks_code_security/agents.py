"""Static code-security agent definitions."""

from __future__ import annotations

from importlib import resources

from flocks.agent.agent import AgentInfo
from flocks.agent.registry import Agent

from flocks_code_security.paths import runtime_dir


AGENT_TOOLS = {
    "code-security": [
        "audit_prepare",
        "audit_run_workers",
        "audit_wait_workers",
        "audit_status",
        "audit_finalize",
        "audit_cancel",
        "question",
    ],
    "code-security-baseline": [
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_candidate",
        "audit_submit_coverage",
    ],
    "code-security-investigator": [
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_candidate",
        "audit_submit_coverage",
    ],
    "code-security-verifier": [
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_verdict",
    ],
}


def _prompt(name: str) -> str:
    return (
        resources.files("flocks_code_security")
        .joinpath("prompts", f"{name}.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def register_agents() -> None:
    definitions = (
        AgentInfo(
            name="code-security",
            name_cn="代码安全审计",
            description="Coordinate a static source-code security audit without executing target code.",
            description_cn="协调只读静态代码安全审计，不执行目标代码。",
            mode="primary",
            hidden=False,
            delegatable=False,
            tools=AGENT_TOOLS["code-security"],
            prompt=_prompt("coordinator"),
            temperature=0.1,
            steps=64,
            tags=["security", "code-security", "static-analysis"],
            session_directory=str(runtime_dir()),
            memory_enabled=False,
            require_dedicated_session=True,
        ),
        AgentInfo(
            name="code-security-baseline",
            description="Hidden baseline static-audit worker.",
            mode="subagent",
            hidden=True,
            delegatable=False,
            tools=AGENT_TOOLS["code-security-baseline"],
            prompt=_prompt("baseline"),
            temperature=0.1,
            steps=64,
            tags=["security", "code-security", "internal"],
            session_directory=str(runtime_dir()),
            memory_enabled=False,
            require_dedicated_session=True,
        ),
        AgentInfo(
            name="code-security-investigator",
            description="Hidden focused code-security investigator.",
            mode="subagent",
            hidden=True,
            delegatable=False,
            tools=AGENT_TOOLS["code-security-investigator"],
            prompt=_prompt("investigator"),
            temperature=0.1,
            steps=64,
            tags=["security", "code-security", "internal"],
            session_directory=str(runtime_dir()),
            memory_enabled=False,
            require_dedicated_session=True,
        ),
        AgentInfo(
            name="code-security-verifier",
            description="Hidden independent verifier for code-security candidates.",
            mode="subagent",
            hidden=True,
            delegatable=False,
            tools=AGENT_TOOLS["code-security-verifier"],
            prompt=_prompt("verifier"),
            temperature=0.0,
            steps=32,
            tags=["security", "code-security", "internal"],
            session_directory=str(runtime_dir()),
            memory_enabled=False,
            require_dedicated_session=True,
        ),
    )
    for agent in definitions:
        Agent.register(agent.name, agent)
    Agent.invalidate_cache()
