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
}

REGISTERED_CODE_SECURITY_AGENTS: dict[str, AgentInfo] = {}


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
            name="code-security-threat-modeler",
            description="Hidden source-backed threat-modeling worker.",
            mode="subagent",
            hidden=True,
            delegatable=False,
            tools=AGENT_TOOLS["code-security-threat-modeler"],
            prompt=_prompt("threat_modeler"),
            temperature=0.1,
            steps=64,
            tags=["security", "code-security", "threat-model", "internal"],
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
        existing = Agent._custom_agents.get(agent.name)
        registered = REGISTERED_CODE_SECURITY_AGENTS.get(agent.name)
        if existing is not None and registered is not existing:
            raise RuntimeError(
                f"Refusing to overwrite existing agent registration: {agent.name}"
            )
    for agent in definitions:
        existing = Agent._custom_agents.get(agent.name)
        registered = REGISTERED_CODE_SECURITY_AGENTS.get(agent.name)
        if existing is not None:
            if registered is existing:
                continue
        Agent.register(agent.name, agent)
        REGISTERED_CODE_SECURITY_AGENTS[agent.name] = agent
    Agent.invalidate_cache()
