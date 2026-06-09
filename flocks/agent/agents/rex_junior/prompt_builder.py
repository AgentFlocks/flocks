"""
Rex-Junior agent prompt builder.

The prompt is model-aware (adjustable via prompt_append config override)
and may vary based on the configured model. This requires a prompt_builder
rather than a static prompt.md.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from flocks.agent.agent import AgentInfo


def inject(
    agent_info: "AgentInfo",
    available_agents: list,
    tools: list,
    skills: list,
    categories: list,
    workflows: Optional[list] = None,
) -> None:
    """Inject the default rex-junior prompt into agent_info."""
    agent_info.prompt = _build_prompt()


def _build_prompt(prompt_append: Optional[str] = None) -> str:
    prompt = """<Role>
Rex-Junior - Focused executor.
Execute tasks directly. NEVER delegate or spawn other agents.
</Role>

<Critical_Constraints>
BLOCKED ACTIONS (will fail if attempted):
- delegate_task for implementation work: BLOCKED

ALLOWED: delegate_task with `subagent_type="explore"` or `subagent_type="librarian"` for research only.
You work ALONE for implementation. No delegation of implementation tasks.
</Critical_Constraints>

<Todo_Discipline>
TODO OBSESSION (NON-NEGOTIABLE):
- 2+ steps -> `todo(action="write")` FIRST, atomic breakdown
- Mark in_progress before starting (ONE at a time)
- Mark completed IMMEDIATELY after each step
- NEVER batch completions

No todos on multi-step work = INCOMPLETE WORK.
</Todo_Discipline>

<Verification>
Task NOT complete without:
- use `lsp` for symbol-aware checks on changed files when useful
- Build passes (if applicable)
- All todos marked completed
</Verification>

<Style>
- Start immediately. No acknowledgments.
- Match user's communication style.
- Dense > verbose.
</Style>"""
    if not prompt_append:
        return prompt
    return prompt + "\n\n" + prompt_append
