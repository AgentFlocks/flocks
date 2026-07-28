"""Prompt injection for the hidden Dream Agent."""

from flocks.memory.evolution.dream import DREAM_SYSTEM_PROMPT


def inject(agent_info, *_args) -> None:
    """Inject the Dream system prompt."""
    agent_info.prompt = DREAM_SYSTEM_PROMPT
