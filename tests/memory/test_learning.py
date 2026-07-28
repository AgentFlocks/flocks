"""Tests for scheduled Dream bridging and turn-driven skill evolution."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.builtin.session_learning import SessionLearningHook
from flocks.hooks.pipeline import HookContext, HookStage
from flocks.memory.config import MemoryConfig
from flocks.memory.learning import (
    LearningCheckpointStore,
    SkillProposalStore,
    SourceSnapshot,
    _apply_memory_operations,
    _apply_pending_proposal,
    _build_turn_review,
    _daily_delta,
    _hash_text,
    _prepare_skill_proposal,
    _redact_sensitive,
    _session_delta,
    process_skill_turn,
    recover_pending_skill_proposals,
    run_dream_bridge,
)
from flocks.memory.learning_scheduler import (
    MemoryLearningScheduler,
    _LAST_SUCCESS_KEY,
    _TICK_SECONDS,
)
from flocks.session.background_tasks import pending_background_tasks
from flocks.session.message import (
    TextPart,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
)
from flocks.session.prompt import SessionPrompt
from flocks.storage import Storage


def _message(
    message_id: str,
    role: str,
    *parts: object,
    finish: str | None = None,
    error: object = None,
    summary: object = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            id=message_id,
            role=role,
            finish=finish,
            error=error,
            summary=summary,
        ),
        parts=list(parts),
    )


def _text(
    message_id: str,
    text: str,
    *,
    synthetic: bool = False,
    ignored: bool = False,
) -> TextPart:
    return TextPart(
        sessionID="ses_test",
        messageID=message_id,
        text=text,
        synthetic=synthetic,
        ignored=ignored,
    )


def _completed_tool(
    message_id: str,
    call_id: str,
    *,
    tool: str = "shell",
    input_data: dict | None = None,
    output: object = "ok",
    part_metadata: dict | None = None,
) -> ToolPart:
    return ToolPart(
        sessionID="ses_test",
        messageID=message_id,
        callID=call_id,
        tool=tool,
        state=ToolStateCompleted(
            input=input_data or {},
            output=output,
            title=tool,
            metadata={},
            time={},
        ),
        metadata=part_metadata,
    )


def _failed_tool(message_id: str, call_id: str) -> ToolPart:
    return ToolPart(
        sessionID="ses_test",
        messageID=message_id,
        callID=call_id,
        tool="shell",
        state=ToolStateError(
            input={"cmd": "bad"},
            error="failed",
            metadata={},
            time={},
        ),
    )


def _skill_document(name: str, body: str = "Run the proven workflow.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use this skill when a repeatable tested workflow is needed.\n"
        "---\n\n"
        f"# {name}\n\n"
        f"{body}\n"
    )


def _review(
    *,
    session_id: str = "ses_test",
    user_message_id: str = "msg_1",
    assistant_message_id: str = "msg_2",
) -> SimpleNamespace:
    content = "user: run it\nassistant: done"
    source = SourceSnapshot(
        source_type="session",
        source_key=session_id,
        content=content,
        content_hash=_hash_text(content),
        line_count=2,
        last_message_id=assistant_message_id,
    )
    return SimpleNamespace(
        source=source,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        trigger_reasons=("completed_tool_threshold",),
        content=content,
    )


def test_memory_operations_apply_structured_changes_and_deduplicate() -> None:
    current = "# Memory\n\n- editor: vim\n- stale fact\n"
    response = {
        "action": "update",
        "operations": [
            {
                "type": "replace",
                "old": "- editor: vim",
                "new": "- editor: neovim",
            },
            {"type": "remove", "old": "- stale fact"},
            {"type": "add", "content": "- language: Python"},
            {"type": "add", "content": "- language: Python"},
        ],
    }

    updated = _apply_memory_operations(current, response)

    assert "- editor: neovim" in updated
    assert "- stale fact" not in updated
    assert updated.count("- language: Python") == 1


def test_memory_add_does_not_treat_substring_as_duplicate() -> None:
    updated = _apply_memory_operations(
        "- language: Pythonic\n",
        {
            "action": "update",
            "operations": [
                {"type": "add", "content": "- language: Python"},
            ],
        },
    )

    assert updated.splitlines() == [
        "- language: Pythonic",
        "",
        "- language: Python",
    ]


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


def test_prompt_injects_uppercase_user_profile_before_memory() -> None:
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


@pytest.mark.asyncio
async def test_session_delta_is_incremental_and_filters_non_text() -> None:
    messages = [
        _message("msg_1", "user", _text("msg_1", "old")),
        _message("msg_2", "assistant", _text("msg_2", "new answer")),
        _message(
            "msg_3",
            "user",
            _text("msg_3", "hidden", synthetic=True),
        ),
        _message("msg_4", "assistant", _completed_tool("msg_4", "call_1")),
        _message("msg_5", "user", _text("msg_5", "new question")),
    ]
    checkpoint = {"last_message_id": "msg_1"}

    with patch(
        "flocks.memory.learning.Message.list_with_parts",
        new=AsyncMock(return_value=messages),
    ):
        snapshot, backlog = await _session_delta(
            "ses_test",
            checkpoint,
            max_messages=3,
            max_chars=10_000,
        )

    assert snapshot is not None
    assert "new answer" in snapshot.content
    assert "hidden" not in snapshot.content
    assert "call_1" not in snapshot.content
    assert snapshot.last_message_id == "msg_4"
    assert backlog is True


@pytest.mark.asyncio
async def test_session_delta_keeps_normal_user_summary_but_skips_compaction() -> None:
    messages = [
        _message(
            "msg_1",
            "user",
            _text("msg_1", "keep this user message"),
            summary=SimpleNamespace(title="Normal user title"),
        ),
        _message(
            "msg_2",
            "assistant",
            _text("msg_2", "compaction summary"),
            finish="summary",
            summary=True,
        ),
    ]

    with patch(
        "flocks.memory.learning.Message.list_with_parts",
        new=AsyncMock(return_value=messages),
    ):
        snapshot, _ = await _session_delta(
            "ses_test",
            None,
            max_messages=10,
            max_chars=10_000,
        )

    assert snapshot is not None
    assert "keep this user message" in snapshot.content
    assert "compaction summary" not in snapshot.content


def test_daily_delta_uses_appended_suffix_and_detects_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "2026-07-28.md"
    path.write_text("line one\nline two\n", encoding="utf-8")
    checkpoint = {
        "line_count": 1,
        "content_hash": _hash_text("line one\n"),
    }

    appended, backlog = _daily_delta(path, checkpoint, max_chars=10_000)
    assert appended is not None
    assert appended.content == "line two\n"
    assert appended.line_count == 2
    assert backlog is False

    path.write_text("rewritten\n", encoding="utf-8")
    rewritten, _ = _daily_delta(path, checkpoint, max_chars=10_000)
    assert rewritten is not None
    assert rewritten.content == "rewritten\n"
    assert rewritten.line_count == 1


@pytest.mark.asyncio
async def test_dream_bridge_updates_both_files_and_commits_cursors(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream.db")
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    (memory_root / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (memory_root / "USER.md").write_text("# User\n", encoding="utf-8")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_test",
        content="user: remember Ruff",
        content_hash="delta",
        line_count=1,
        last_message_id="msg_2",
    )
    config = MemoryConfig()
    response = {
        "action": "update",
        "operations": [
            {
                "target": "memory",
                "type": "add",
                "content": "- Project uses Ruff",
            },
            {
                "target": "user",
                "type": "add",
                "content": "- Prefers concise answers",
            },
        ],
    }

    with (
        patch(
            "flocks.memory.learning.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.learning.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.learning.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.learning._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [("project", "/workspace")])),
        ),
        patch(
            "flocks.memory.learning._chat_json",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "flocks.memory.learning._sync_memory_indexes",
            new=AsyncMock(),
        ),
    ):
        result = await run_dream_bridge()

    assert result.changed is True
    assert "Project uses Ruff" in (memory_root / "MEMORY.md").read_text()
    assert "Prefers concise answers" in (memory_root / "USER.md").read_text()
    checkpoint = await LearningCheckpointStore.get(
        "dream",
        "session",
        "ses_test",
    )
    assert checkpoint is not None
    assert checkpoint["last_message_id"] == "msg_2"


@pytest.mark.asyncio
async def test_dream_bridge_rolls_back_files_when_index_sync_fails(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-rollback.db")
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    memory_path = memory_root / "MEMORY.md"
    user_path = memory_root / "USER.md"
    memory_path.write_text("old memory\n", encoding="utf-8")
    user_path.write_text("old user\n", encoding="utf-8")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_test",
        content="new evidence",
        content_hash="delta",
        line_count=1,
        last_message_id="msg_2",
    )
    config = MemoryConfig()
    response = {
        "action": "update",
        "operations": [
            {
                "target": "memory",
                "type": "add",
                "content": "- new memory",
            },
            {
                "target": "user",
                "type": "add",
                "content": "- new user",
            },
        ],
    }
    sync = AsyncMock(side_effect=[RuntimeError("index failed"), None])

    with (
        patch(
            "flocks.memory.learning.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.learning.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.learning.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.learning._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [])),
        ),
        patch(
            "flocks.memory.learning._chat_json",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "flocks.memory.learning._sync_memory_indexes",
            new=sync,
        ),
    ):
        with pytest.raises(RuntimeError, match="index failed"):
            await run_dream_bridge()

    assert memory_path.read_text() == "old memory\n"
    assert user_path.read_text() == "old user\n"
    assert await LearningCheckpointStore.get("dream", "session", "ses_test") is None


@pytest.mark.asyncio
async def test_dream_bridge_rolls_back_files_when_checkpoint_commit_fails(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-checkpoint-rollback.db")
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    memory_path = memory_root / "MEMORY.md"
    user_path = memory_root / "USER.md"
    memory_path.write_text("old memory\n", encoding="utf-8")
    user_path.write_text("old user\n", encoding="utf-8")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_test",
        content="new evidence",
        content_hash="delta",
        line_count=1,
        last_message_id="msg_2",
    )
    response = {
        "action": "update",
        "operations": [
            {
                "target": "memory",
                "type": "add",
                "content": "- new memory",
            }
        ],
    }

    with (
        patch(
            "flocks.memory.learning.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=MemoryConfig())),
        ),
        patch(
            "flocks.memory.learning.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.learning.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.learning._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [])),
        ),
        patch(
            "flocks.memory.learning._chat_json",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "flocks.memory.learning._sync_memory_indexes",
            new=AsyncMock(),
        ),
        patch.object(
            LearningCheckpointStore,
            "commit",
            new=AsyncMock(side_effect=RuntimeError("checkpoint failed")),
        ),
    ):
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await run_dream_bridge()

    assert memory_path.read_text() == "old memory\n"
    assert user_path.read_text() == "old user\n"


@pytest.mark.asyncio
async def test_turn_review_detects_failure_recovery_and_redacts_trace() -> None:
    messages = [
        _message("msg_1", "assistant", _text("msg_1", "previous")),
        _message("msg_2", "user", _text("msg_2", "不对，应该用新的流程")),
        _message(
            "msg_3",
            "assistant",
            _failed_tool("msg_3", "call_1"),
            _completed_tool(
                "msg_3",
                "call_2",
                input_data={"api_key": "secret-value"},
                output="Authorization: Bearer abcdefghijklmnop",
            ),
            _text("msg_3", "done"),
            finish="stop",
        ),
    ]
    config = MemoryConfig(learning={"skill": {"min_completed_tools": 10}})

    with patch(
        "flocks.memory.learning.Message.list_with_parts",
        new=AsyncMock(return_value=messages),
    ):
        review = await _build_turn_review(
            session_id="ses_test",
            user_message_id="msg_2",
            assistant_message_id="msg_3",
            config=config,
        )

    assert review is not None
    assert set(review.trigger_reasons) == {
        "failure_then_success",
        "user_correction",
    }
    assert "call_1" not in review.content
    assert '"index": 1' in review.content
    assert "secret-value" not in review.content
    assert "abcdefghijklmnop" not in review.content
    assert "[REDACTED]" in review.content


@pytest.mark.asyncio
async def test_turn_review_requires_a_configured_signal() -> None:
    messages = [
        _message("msg_1", "user", _text("msg_1", "run it")),
        _message(
            "msg_2",
            "assistant",
            _completed_tool("msg_2", "call_1"),
            _completed_tool(
                "msg_2",
                "call_ignored",
                part_metadata={"ignored": True},
            ),
            finish="stop",
        ),
    ]
    config = MemoryConfig(learning={"skill": {"min_completed_tools": 2}})

    with patch(
        "flocks.memory.learning.Message.list_with_parts",
        new=AsyncMock(return_value=messages),
    ):
        review = await _build_turn_review(
            session_id="ses_test",
            user_message_id="msg_1",
            assistant_message_id="msg_2",
            config=config,
        )

    assert review is None


@pytest.mark.asyncio
async def test_turn_review_triggers_at_completed_tool_threshold() -> None:
    tools = tuple(_completed_tool("msg_2", f"call_{index}") for index in range(10))
    messages = [
        _message("msg_1", "user", _text("msg_1", "run the workflow")),
        _message("msg_2", "assistant", *tools, finish="stop"),
    ]
    config = MemoryConfig(learning={"skill": {"min_completed_tools": 10}})

    with patch(
        "flocks.memory.learning.Message.list_with_parts",
        new=AsyncMock(return_value=messages),
    ):
        review = await _build_turn_review(
            session_id="ses_test",
            user_message_id="msg_1",
            assistant_message_id="msg_2",
            config=config,
        )

    assert review is not None
    assert review.trigger_reasons == ("completed_tool_threshold",)
    for index in range(10):
        assert f'"index": {index + 1}' in review.content


def test_redaction_handles_nested_keys_and_inline_secrets() -> None:
    value = {
        "nested": {
            "password": "hunter2",
            "cmd": ("TOKEN=abc123 AWS_SECRET_ACCESS_KEY=cloud-secret curl -H 'Bearer secret-token'"),
        }
    }

    redacted = _redact_sensitive(value)

    assert redacted["nested"]["password"] == "[REDACTED]"
    assert "abc123" not in redacted["nested"]["cmd"]
    assert "cloud-secret" not in redacted["nested"]["cmd"]
    assert "secret-token" not in redacted["nested"]["cmd"]


def test_prepare_create_patch_and_edit_proposals(tmp_path: Path) -> None:
    review = _review()
    create = _prepare_skill_proposal(
        response={
            "action": "create",
            "skill_name": "pytest-workflow",
            "content": _skill_document("pytest-workflow"),
        },
        review=review,
        catalog=[],
        related={},
        skill_root=tmp_path,
    )
    assert create is not None
    assert create.action == "create"

    skill_path = tmp_path / "pytest-workflow" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(_skill_document("pytest-workflow"), encoding="utf-8")
    catalog = [
        {
            "name": "pytest-workflow",
            "description": "test",
            "location": str(skill_path),
            "source": "user",
        }
    ]
    related = {
        "pytest-workflow": {
            **catalog[0],
            "content": skill_path.read_text(encoding="utf-8"),
        }
    }
    patched = _prepare_skill_proposal(
        response={
            "action": "patch",
            "skill_name": "pytest-workflow",
            "path": "SKILL.md",
            "old": "Run the proven workflow.",
            "new": "Run the proven workflow and verify the result.",
        },
        review=review,
        catalog=catalog,
        related=related,
        skill_root=tmp_path,
    )
    edited = _prepare_skill_proposal(
        response={
            "action": "edit",
            "skill_name": "pytest-workflow",
            "content": _skill_document(
                "pytest-workflow",
                "Run the edited workflow.",
            ),
        },
        review=review,
        catalog=catalog,
        related=related,
        skill_root=tmp_path,
    )

    assert patched is not None
    assert "verify the result" in patched.proposed_content
    assert edited is not None
    assert "edited workflow" in edited.proposed_content


def test_proposal_rejects_protected_skill_and_name_shadowing(
    tmp_path: Path,
) -> None:
    review = _review()
    protected_path = tmp_path.parent / "protected" / "SKILL.md"
    protected_path.parent.mkdir()
    protected_path.write_text(_skill_document("protected"), encoding="utf-8")
    catalog = [
        {
            "name": "protected",
            "description": "protected",
            "location": str(protected_path),
            "source": "project",
        }
    ]
    related = {
        "protected": {
            **catalog[0],
            "content": protected_path.read_text(encoding="utf-8"),
        }
    }

    with pytest.raises(ValueError, match="only user-managed"):
        _prepare_skill_proposal(
            response={
                "action": "edit",
                "skill_name": "protected",
                "content": _skill_document("protected", "changed"),
            },
            review=review,
            catalog=catalog,
            related=related,
            skill_root=tmp_path,
        )
    with pytest.raises(ValueError, match="cannot shadow"):
        _prepare_skill_proposal(
            response={
                "action": "create",
                "skill_name": "protected",
                "content": _skill_document("protected", "changed"),
            },
            review=review,
            catalog=catalog,
            related={},
            skill_root=tmp_path,
        )


def test_proposal_rejects_symlinked_skill_file(tmp_path: Path) -> None:
    review = _review()
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "linked-skill"
    real_dir = skill_root / "real-skill"
    skill_dir.mkdir(parents=True)
    real_dir.mkdir(parents=True)
    real_path = real_dir / "SKILL.md"
    real_path.write_text(_skill_document("linked-skill"), encoding="utf-8")
    linked_path = skill_dir / "SKILL.md"
    linked_path.symlink_to(real_path)
    catalog = [
        {
            "name": "linked-skill",
            "description": "linked",
            "location": str(linked_path),
            "source": "user",
        }
    ]

    with pytest.raises(ValueError, match="symbolic link"):
        _prepare_skill_proposal(
            response={
                "action": "edit",
                "skill_name": "linked-skill",
                "content": _skill_document("linked-skill", "changed"),
            },
            review=review,
            catalog=catalog,
            related={
                "linked-skill": {
                    **catalog[0],
                    "content": real_path.read_text(encoding="utf-8"),
                }
            },
            skill_root=skill_root,
        )


def test_proposal_requires_strict_frontmatter_and_trigger_description(
    tmp_path: Path,
) -> None:
    review = _review()
    invalid_yaml = "---\nname: invalid-skill\ndescription: [unterminated\n---\n\n# Invalid\n"
    with pytest.raises(ValueError, match="invalid YAML"):
        _prepare_skill_proposal(
            response={
                "action": "create",
                "skill_name": "invalid-skill",
                "content": invalid_yaml,
            },
            review=review,
            catalog=[],
            related={},
            skill_root=tmp_path,
        )

    vague_description = "---\nname: vague-skill\ndescription: Manages things reliably.\n---\n\n# Vague\n"
    with pytest.raises(ValueError, match="what it does and when"):
        _prepare_skill_proposal(
            response={
                "action": "create",
                "skill_name": "vague-skill",
                "content": vague_description,
            },
            review=review,
            catalog=[],
            related={},
            skill_root=tmp_path,
        )


def test_proposal_rejects_symlinked_skill_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-skills"
    real_root.mkdir()
    linked_root = tmp_path / "linked-skills"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="root cannot be a symbolic link"):
        _prepare_skill_proposal(
            response={
                "action": "create",
                "skill_name": "linked-root-skill",
                "content": _skill_document("linked-root-skill"),
            },
            review=_review(),
            catalog=[],
            related={},
            skill_root=linked_root,
        )


@pytest.mark.asyncio
async def test_proposal_is_persisted_before_apply_and_recovers(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "proposal.db")
    skill_root = tmp_path / "skills"
    proposal = _prepare_skill_proposal(
        response={
            "action": "create",
            "skill_name": "pytest-workflow",
            "content": _skill_document("pytest-workflow"),
        },
        review=_review(),
        catalog=[],
        related={},
        skill_root=skill_root,
    )
    assert proposal is not None
    stored = await SkillProposalStore.create_pending(proposal)
    assert stored.status == "pending"

    target = skill_root / "pytest-workflow" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(proposal.proposed_content, encoding="utf-8")
    recovered = await recover_pending_skill_proposals(skill_root=skill_root)

    assert recovered == 1
    final = await SkillProposalStore.get_by_assistant_message("msg_2")
    assert final is not None
    assert final.status == "applied"
    checkpoint = await LearningCheckpointStore.get(
        "skill",
        "session",
        "ses_test",
    )
    assert checkpoint is not None
    assert checkpoint["last_message_id"] == "msg_2"


@pytest.mark.asyncio
async def test_proposal_conflict_does_not_overwrite_user_skill(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "proposal-conflict.db")
    skill_root = tmp_path / "skills"
    skill_path = skill_root / "pytest-workflow" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = _skill_document("pytest-workflow")
    skill_path.write_text(original, encoding="utf-8")
    catalog = [
        {
            "name": "pytest-workflow",
            "description": "test",
            "location": str(skill_path),
            "source": "user",
        }
    ]
    related = {
        "pytest-workflow": {
            **catalog[0],
            "content": original,
        }
    }
    proposal = _prepare_skill_proposal(
        response={
            "action": "edit",
            "skill_name": "pytest-workflow",
            "content": _skill_document("pytest-workflow", "proposal version"),
        },
        review=_review(),
        catalog=catalog,
        related=related,
        skill_root=skill_root,
    )
    assert proposal is not None
    stored = await SkillProposalStore.create_pending(proposal)
    user_version = _skill_document("pytest-workflow", "user version")
    skill_path.write_text(user_version, encoding="utf-8")

    applied = await _apply_pending_proposal(stored, skill_root=skill_root)

    assert applied is False
    assert skill_path.read_text(encoding="utf-8") == user_version
    final = await SkillProposalStore.get_by_assistant_message("msg_2")
    assert final is not None
    assert final.status == "conflict"


@pytest.mark.asyncio
async def test_process_skill_turn_creates_proposal_before_live_skill(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "skill-turn.db")
    config = MemoryConfig()
    review = _review()
    skill_root = tmp_path / "skills"
    chat = AsyncMock(
        side_effect=[
            {
                "action": "evolve",
                "skill_names": [],
                "reason": "new reusable workflow",
            },
            {
                "action": "create",
                "skill_name": "pytest-workflow",
                "content": _skill_document("pytest-workflow"),
            },
        ]
    )
    session = SimpleNamespace(
        id="ses_test",
        category="user",
        status="active",
    )

    with (
        patch(
            "flocks.memory.learning.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.memory.learning._build_turn_review",
            new=AsyncMock(return_value=review),
        ),
        patch(
            "flocks.memory.learning._skill_catalog",
            return_value=[],
        ),
        patch(
            "flocks.memory.learning._chat_json",
            new=chat,
        ),
    ):
        changed = await process_skill_turn(
            session_id="ses_test",
            user_message_id="msg_1",
            assistant_message_id="msg_2",
            provider_id="test-provider",
            model_id="test-model",
            skill_root=skill_root,
        )

    assert changed is True
    assert (skill_root / "pytest-workflow" / "SKILL.md").exists()
    stored = await SkillProposalStore.get_by_assistant_message("msg_2")
    assert stored is not None
    assert stored.status == "applied"
    assert chat.await_count == 2


@pytest.mark.asyncio
async def test_turn_finish_schedules_only_skill_review_without_blocking() -> None:
    hook = SessionLearningHook()
    review = AsyncMock(return_value=False)
    before = set(pending_background_tasks())

    with patch("flocks.memory.learning.process_skill_turn", review):
        await hook.turn_finish(
            HookContext(
                stage=HookStage.TURN_FINISH,
                input={
                    "sessionID": "ses_test",
                    "model": {
                        "providerID": "test-provider",
                        "modelID": "test-model",
                    },
                    "userMessage": {"id": "msg_1", "content": "run it"},
                    "assistantMessage": {"id": "msg_2", "content": "done"},
                },
            )
        )
        created = set(pending_background_tasks()) - before
        assert len(created) == 1
        await created.pop()

    review.assert_awaited_once_with(
        session_id="ses_test",
        user_message_id="msg_1",
        assistant_message_id="msg_2",
        provider_id="test-provider",
        model_id="test-model",
    )


@pytest.mark.asyncio
async def test_scheduler_runs_due_dream_and_persists_success(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "scheduler.db")
    config = MemoryConfig()
    result = SimpleNamespace(
        changed=False,
        processed_sources=0,
        backlog=False,
    )
    MemoryLearningScheduler._retry_after_ts = 0

    with (
        patch(
            "flocks.memory.learning_scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.learning_scheduler.run_dream_bridge",
            new=AsyncMock(return_value=result),
        ) as run,
    ):
        await MemoryLearningScheduler._tick_once(now_ts=1_000)
        await MemoryLearningScheduler._tick_once(now_ts=1_001)

    run.assert_awaited_once()
    assert await Storage.get(_LAST_SUCCESS_KEY) == 1_000


def test_scheduler_defaults_to_daily_run_and_half_hour_checks() -> None:
    config = MemoryConfig()

    assert config.learning.dream.interval_hours == 24
    assert _TICK_SECONDS == 30 * 60


@pytest.mark.asyncio
async def test_scheduler_retries_backlog_without_advancing_interval(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "scheduler-backlog.db")
    config = MemoryConfig()
    result = SimpleNamespace(
        changed=True,
        processed_sources=1,
        backlog=True,
    )
    MemoryLearningScheduler._retry_after_ts = 0

    with (
        patch(
            "flocks.memory.learning_scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.learning_scheduler.run_dream_bridge",
            new=AsyncMock(return_value=result),
        ) as run,
    ):
        await MemoryLearningScheduler._tick_once(now_ts=1_000)
        await MemoryLearningScheduler._tick_once(now_ts=1_060)

    assert run.await_count == 2
    assert await Storage.get(_LAST_SUCCESS_KEY) is None


@pytest.mark.asyncio
async def test_scheduler_waits_fifteen_minutes_after_failure(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "scheduler-failure.db")
    config = MemoryConfig()
    MemoryLearningScheduler._retry_after_ts = 0

    with (
        patch(
            "flocks.memory.learning_scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.learning_scheduler.run_dream_bridge",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ) as run,
    ):
        await MemoryLearningScheduler._tick_once(now_ts=1_000)
        await MemoryLearningScheduler._tick_once(now_ts=1_899)
        await MemoryLearningScheduler._tick_once(now_ts=1_900)

    assert run.await_count == 2
