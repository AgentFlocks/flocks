"""Prompt injection for the hidden Skill learning Agent."""

from flocks.memory.evolution.skill import SKILL_SYSTEM_PROMPT


def inject(agent_info, *_args) -> None:
    """Inject the Skill evolution system prompt."""
    agent_info.prompt = SKILL_SYSTEM_PROMPT
