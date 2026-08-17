"""Direct tests for Rex and Hephaestus prompt builders."""

import inspect

from flocks.agent.agents.hephaestus.prompt_builder import build_hephaestus_prompt
from flocks.agent.agents.rex.prompt_builder import build_dynamic_rex_prompt


def test_rex_prompt_uses_todos_and_delegate_task_only():
    prompt = build_dynamic_rex_prompt([], [], [], [])

    assert "## Todo Management" in prompt
    assert "YOUR TODO CREATION WOULD BE TRACKED BY HOOK" in prompt
    assert "multiple foreground `delegate_task` tool calls" in prompt
    assert "`delegate_task` / `task`" not in prompt
    assert "After code changes, run the lint/typecheck/tests." in prompt
    assert "If tests fail, iterate until they pass before finalizing." in prompt
    assert "explicit note about pre-existing failures" in prompt
    assert "Verify delegated work against expected behavior" in prompt
    assert "TaskCreate" not in prompt
    assert "TaskUpdate" not in prompt


def test_hephaestus_prompt_uses_existing_todo_discipline():
    prompt = build_hephaestus_prompt([], [], [])

    assert "## Todo Discipline (NON-NEGOTIABLE)" in prompt
    assert "Track ALL multi-step work with todos." in prompt
    assert '`todo(action="write")`' in prompt
    assert "TaskCreate" not in prompt
    assert "TaskUpdate" not in prompt


def test_prompt_builder_signatures_exclude_task_system_flag():
    assert "use_task_system" not in inspect.signature(build_dynamic_rex_prompt).parameters
    assert "use_task_system" not in inspect.signature(build_hephaestus_prompt).parameters
