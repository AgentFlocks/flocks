"""Tests for bounded Memory snapshot injection."""

from flocks.session.prompt import SessionPrompt


def test_prompt_bounds_memory_snapshots_and_preserves_structure() -> None:
    prompts = SessionPrompt._build_memory_bootstrap_prompts(
        session_id="ses_test",
        memory_bootstrap_data={
            "user_profile": {
                "path": "USER.md",
                "abs_path": "/memory/USER.md",
                "content": (
                    "# User Memory\n\n"
                    "## User Information\n"
                    + ("user detail\n" * 500)
                    + "## Preferences\nPrefers concise answers."
                ),
                "inject": True,
            },
            "main_memory": {
                "path": "MEMORY.md",
                "abs_path": "/memory/MEMORY.md",
                "content": (
                    "# Global Memory\n\n"
                    "## Lessons and Corrections\n"
                    + ("global lesson\n" * 800)
                    + "## References\n"
                    "- [Operations runbook](https://example.test/runbook)"
                ),
                "inject": True,
            },
            "project_memory": {
                "path": "projects/prj_test/MEMORY.md",
                "abs_path": "/memory/projects/prj_test/MEMORY.md",
                "content": (
                    "# Project Memory\n\n"
                    "## Project Context\n"
                    + ("project fact\n" * 800)
                    + "## References\n- See architecture.md (source of truth)"
                ),
                "inject": True,
            },
        },
    )

    assert SessionPrompt.count_tokens(prompts[0]) <= 1000
    assert SessionPrompt.count_tokens(prompts[1]) <= 2000
    assert SessionPrompt.count_tokens(prompts[2]) <= 2000
    assert "## Preferences" in prompts[0]
    assert "## References" in prompts[1]
    assert "[Operations runbook](https://example.test/runbook)" in prompts[1]
    assert "## References" in prompts[2]
    assert "See architecture.md" in prompts[2]
    assert "Use `read` to open the complete file" in prompts[0]
    assert "`/memory/USER.md`" in prompts[0]
    assert "`/memory/MEMORY.md`" in prompts[1]
    assert "`/memory/projects/prj_test/MEMORY.md`" in prompts[2]
