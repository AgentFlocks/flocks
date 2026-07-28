"""Tests for post-session Dream and skill self-evolution."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.builtin.session_learning import SessionLearningHook
from flocks.hooks.pipeline import HookContext, HookStage
from flocks.memory.learning import (
    LearningCheckpointStore,
    SourceSnapshot,
    _apply_memory_operations,
    _apply_skill_action,
)
from flocks.session.background_tasks import pending_background_tasks
from flocks.session.prompt import SessionPrompt
from flocks.storage import Storage


def test_memory_operations_apply_structured_changes() -> None:
    current = "# Memory\n\n- editor: vim\n- stale fact\n"

    updated = _apply_memory_operations(
        current,
        {
            "action": "update",
            "operations": [
                {
                    "type": "replace",
                    "old": "- editor: vim",
                    "new": "- editor: neovim",
                },
                {"type": "remove", "old": "- stale fact"},
                {"type": "add", "content": "- language: Python"},
            ],
        },
    )

    assert "- editor: neovim" in updated
    assert "- stale fact" not in updated
    assert updated.endswith("- language: Python\n")


def test_memory_operations_route_user_profile_separately() -> None:
    response = {
        "action": "update",
        "operations": [
            {
                "target": "user",
                "type": "add",
                "content": "- Prefers concise answers",
            },
            {
                "target": "memory",
                "type": "add",
                "content": "- Project uses Ruff",
            },
        ],
    }

    updated_user = _apply_memory_operations(
        "# User Profile\n",
        response,
        target="user",
    )
    updated_memory = _apply_memory_operations(
        "# Long-Term Memory\n",
        response,
        target="memory",
    )

    assert "Prefers concise answers" in updated_user
    assert "Project uses Ruff" not in updated_user
    assert "Project uses Ruff" in updated_memory
    assert "Prefers concise answers" not in updated_memory


def test_prompt_injects_user_profile_before_long_term_memory() -> None:
    prompts = SessionPrompt._build_memory_bootstrap_prompts(
        session_id="ses_test",
        memory_bootstrap_data={
            "user_profile": {
                "path": "USER.md",
                "content": "Prefers concise answers.",
                "inject": True,
            },
            "main_memory": {
                "path": "MEMORY.md",
                "content": "Project uses Ruff.",
                "inject": True,
            },
        },
    )

    assert prompts == [
        "## USER.md\n\nPrefers concise answers.",
        "## MEMORY.md\n\nProject uses Ruff.",
    ]


def test_memory_operation_rejects_ambiguous_target() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        _apply_memory_operations(
            "duplicate\nduplicate\n",
            {
                "action": "update",
                "operations": [
                    {"type": "remove", "old": "duplicate"},
                ],
            },
        )


def test_skill_create_and_patch_use_live_skill_directory(tmp_path: Path) -> None:
    created = _apply_skill_action(
        {
            "action": "create",
            "skill_name": "pytest-workflow",
            "content": (
                "---\n"
                "name: pytest-workflow\n"
                "description: Run focused Python tests with pytest.\n"
                "---\n\n"
                "# Pytest workflow\n\nRun the focused tests.\n"
            ),
        },
        tmp_path,
        {},
    )
    skill_file = tmp_path / "pytest-workflow" / "SKILL.md"
    assert created is True
    assert skill_file.exists()

    patched = _apply_skill_action(
        {
            "action": "patch",
            "skill_name": "pytest-workflow",
            "path": "SKILL.md",
            "old": "Run the focused tests.",
            "new": "Run focused tests before the full suite.",
        },
        tmp_path,
        {"pytest-workflow": skill_file.read_text(encoding="utf-8")},
    )
    assert patched is True
    assert "before the full suite" in skill_file.read_text(encoding="utf-8")


def test_skill_write_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the skill directory"):
        _apply_skill_action(
            {
                "action": "write_file",
                "skill_name": "safe-skill",
                "path": "../outside.txt",
                "content": "unsafe",
            },
            tmp_path,
            {"safe-skill": "existing"},
        )


def test_skill_write_rejects_symlinked_skill_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-skill"
    outside.mkdir(exist_ok=True)
    (tmp_path / "safe-skill").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="configured skill root"):
        _apply_skill_action(
            {
                "action": "write_file",
                "skill_name": "safe-skill",
                "path": "notes.md",
                "content": "unsafe",
            },
            tmp_path,
            {"safe-skill": "existing"},
        )


@pytest.mark.asyncio
async def test_checkpoint_is_pipeline_specific_and_detects_changes(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "learning.db")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_test",
        content="hello",
        content_hash="hash-one",
        line_count=1,
        last_message_id="msg_1",
    )

    assert not await LearningCheckpointStore.is_current("dream", source)
    await LearningCheckpointStore.commit("dream", [source])
    assert await LearningCheckpointStore.is_current("dream", source)
    assert not await LearningCheckpointStore.is_current("skill", source)
    assert not await LearningCheckpointStore.is_current(
        "dream",
        SourceSnapshot(
            source_type="session",
            source_key="ses_test",
            content="hello again",
            content_hash="hash-two",
            line_count=1,
            last_message_id="msg_2",
        ),
    )


@pytest.mark.asyncio
async def test_turn_finish_schedules_learning_without_blocking() -> None:
    hook = SessionLearningHook()
    run_learning = AsyncMock(return_value={"dream": False, "skill": False})

    with patch(
        "flocks.memory.learning.process_completed_session",
        run_learning,
    ):
        await hook.turn_finish(
            HookContext(
                stage=HookStage.TURN_FINISH,
                input={
                    "sessionID": "ses_test",
                    "workspace": "/tmp/project",
                    "model": {
                        "providerID": "test-provider",
                        "modelID": "test-model",
                    },
                },
            )
        )
        tasks = list(pending_background_tasks())
        await tasks[-1]

    run_learning.assert_awaited_once_with(
        session_id="ses_test",
        workspace="/tmp/project",
        provider_id="test-provider",
        model_id="test-model",
    )
