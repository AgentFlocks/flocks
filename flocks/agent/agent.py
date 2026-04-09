"""
Core agent data models.

Consolidates:
  - AgentInfo / AgentModel  (previously flocks.agent.core.agent)
  - AgentPromptMetadata / DelegationTrigger / AvailableAgent /
    AvailableTool / AvailableSkill / AvailableCategory
    (previously flocks.agent.prompts.builder.dynamic)

All other modules should import from here; the old locations are kept
for backward compatibility until the final-cleanup pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from flocks.permission import Ruleset


# ---------------------------------------------------------------------------
# LLM model configuration
# ---------------------------------------------------------------------------

class AgentModel(BaseModel):
    """Agent model configuration (LLM to use for this agent)."""

    model_id: str
    provider_id: str


# ---------------------------------------------------------------------------
# Delegation prompt metadata  (moved from prompts/builder/dynamic.py)
# ---------------------------------------------------------------------------

@dataclass
class DelegationTrigger:
    """A single delegation trigger rule (domain + trigger phrase)."""
    domain: str
    trigger: str


@dataclass
class AgentPromptMetadata:
    """
    Delegation metadata for a subagent.

    Used by Rex / Hephaestus prompt builders to generate the delegation table.
    """
    category: str
    cost: str
    triggers: List[DelegationTrigger] = field(default_factory=list)
    use_when: Optional[List[str]] = None
    avoid_when: Optional[List[str]] = None
    dedicated_section: Optional[str] = None
    prompt_alias: Optional[str] = None
    key_trigger: Optional[str] = None


# ---------------------------------------------------------------------------
# Prompt-builder context types  (moved from prompts/builder/dynamic.py)
# ---------------------------------------------------------------------------

@dataclass
class AvailableAgent:
    """Delegation candidate, passed into prompt_builder inject() functions."""
    name: str
    description: str
    metadata: AgentPromptMetadata


@dataclass
class AvailableTool:
    """Tool available to the current session."""
    name: str
    category: str


@dataclass
class AvailableSkill:
    """Skill available for injection into delegated tasks."""
    name: str
    description: str
    location: str


@dataclass
class AvailableCategory:
    """Task delegation category (model preset + domain label)."""
    name: str
    description: str


@dataclass
class AvailableWorkflow:
    """Workflow available for execution via run_workflow tool."""
    name: str
    description: str
    path: str
    source: str = "project"  # "project" | "global"


# ---------------------------------------------------------------------------
# Core agent configuration model
# ---------------------------------------------------------------------------

class AgentInfo(BaseModel):
    """
    Complete agent configuration.

    One instance per named agent.  Loaded from agent.yaml (static fields) and
    optionally updated by prompt_builder.inject() for dynamic prompts.
    """

    model_config = {"populate_by_name": True}

    name: str
    description: Optional[str] = None
    # Chinese UI label; English ``description`` is used for delegation prompts / tooling.
    description_cn: Optional[str] = None

    # "primary"  – top-level orchestrator shown to the user (e.g. Rex)
    # "subagent" – invocable via delegate_task() only
    # "all"      – can function as either (legacy / plugin default)
    mode: str = "all"

    native: bool = Field(default=False)
    hidden: bool = Field(default=False)
    top_p: Optional[float] = Field(default=None, alias="topP")
    temperature: Optional[float] = None
    color: Optional[str] = None

    # Legacy compatibility only.
    # Runtime tool exposure is driven by `tools` and session callable tools.
    permission: Ruleset = Field(default_factory=list)

    # Concrete callable tool names resolved from agent.yaml.
    tools: Optional[List[str]] = Field(default=None)

    model: Optional[AgentModel] = None
    prompt: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)
    steps: Optional[int] = Field(default=None, description="Max steps")

    # None → auto-derived by model_validator:
    #   mode="primary" → False   (primary agents are not delegation targets)
    #   otherwise      → True    (subagents are delegatable by default)
    delegatable: Optional[bool] = None

    # "module.path:function_name" called during phase-2 prompt injection.
    # Signature: (agent_info, available_agents, tools, skills, categories) → None
    # The function sets agent_info.prompt directly.
    prompt_builder: Optional[str] = Field(default=None)

    # Delegation prompt metadata used by Rex / Hephaestus prompt builders.
    # When absent, a default entry is auto-generated from description.
    prompt_metadata: Optional[AgentPromptMetadata] = Field(default=None)

    # Arbitrary labels for categorising agents.
    # e.g. ["security"] marks an agent as cybersecurity-domain relevant,
    # which the Web UI uses to decide whether to surface the agent.
    tags: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_delegatable(self) -> "AgentInfo":
        if self.delegatable is None:
            self.delegatable = self.mode != "primary"
        return self
