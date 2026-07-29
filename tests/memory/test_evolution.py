"""Tests for scheduled Dream bridging and turn-driven Skill evolution."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from flocks.hooks.builtin.session_evolution import SessionEvolutionHook
from flocks.hooks.pipeline import HookContext, HookStage
from flocks.memory.config import MemoryConfig
from flocks.memory.evolution import (
    DreamTarget,
    EvolutionCheckpointStore,
    SourceSnapshot,
    MemoryEvolutionScheduler,
    process_skill_turn,
    run_dream_bridge,
)
from flocks.memory.evolution.common import (
    SkillEvolutionStateStore,
    TurnEvidence,
    _build_turn_evidence,
    _build_turn_review,
    _collect_dream_sources,
    _daily_delta,
    _hash_text,
    _load_turn_evidence,
    _redact_sensitive,
    _session_delta,
)
from flocks.memory.evolution.dream import DREAM_SYSTEM_PROMPT
from flocks.memory.evolution.skill import (
    SKILL_SYSTEM_PROMPT,
    _serialize_skill_catalog,
    _skill_contents,
    _validate_skill_changes,
    run_manual_skill_evolution,
)
from flocks.memory.evolution.scheduler import (
    _LAST_SUCCESS_KEY,
    _TICK_SECONDS,
)
from flocks.memory.types import MemoryScope
from flocks.session.background_tasks import pending_background_tasks
from flocks.session.message import (
    TextPart,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
)
from flocks.session.prompt import SessionPrompt
from flocks.storage import Storage


def test_memory_config_exposes_evolution_without_learning_alias() -> None:
    properties = MemoryConfig.model_json_schema()["properties"]

    assert "evolution" in properties
    assert "learning" not in properties
    config = MemoryConfig()
    assert config.evolution.dream.interval_hours == 24
    assert config.evolution.skill.tool_iteration_interval == 10
    legacy = MemoryConfig(
        evolution={"skill": {"min_completed_tools": 7}}
    )
    assert legacy.evolution.skill.tool_iteration_interval == 7
    assert not hasattr(config, "learning")


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
        "metadata:\n"
        "  managed_by: flocks\n"
        "---\n\n"
        f"# {name}\n\n"
        f"{body}\n"
    )


def _review(
    *,
    user_message_id: str = "msg_1",
    assistant_message_id: str = "msg_2",
) -> SimpleNamespace:
    content = "user: run it\nassistant: done"
    return SimpleNamespace(
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        trigger_reasons=("tool_iteration_threshold",),
        content=content,
    )


def _evidence(
    tool_iterations: int,
    *,
    user_message_id: str = "msg_1",
    assistant_message_id: str = "msg_2",
) -> TurnEvidence:
    return TurnEvidence(
        session_id="ses_test",
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        messages=(),
        user_index=0,
        assistant_index=0,
        tool_iterations=tool_iterations,
    )


def test_skill_change_validation_restores_unmanaged_preimage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_path = root / "manual-skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = (
        "---\n"
        "name: manual-skill\n"
        "description: A manually maintained Skill.\n"
        "---\n\n"
        "Original workflow.\n"
    )
    skill_path.write_text(original, encoding="utf-8")
    before = _skill_contents(root)
    skill_path.write_text(
        _skill_document("manual-skill", "Unauthorized update."),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not Evolution-managed"):
        _validate_skill_changes(root, before)

    assert skill_path.read_text(encoding="utf-8") == original


def test_dream_prompt_has_explicit_agent_workflow_sections() -> None:
    for heading in (
        "# Role",
        "# Inputs",
        "# Memory destinations",
        "# Rules",
        "# Workflow",
        "# Tool use",
        "# Completion",
    ):
        assert heading in DREAM_SYSTEM_PROMPT
    assert "Return strict JSON" not in DREAM_SYSTEM_PROMPT
    assert "Do not output JSON" in DREAM_SYSTEM_PROMPT
    assert "Use `write` only to create a missing" in DREAM_SYSTEM_PROMPT
    assert "using `edit` for a precise change" in DREAM_SYSTEM_PROMPT
    assert "Assistant text is never" in DREAM_SYSTEM_PROMPT
    assert "not independent corroboration" in DREAM_SYSTEM_PROMPT
    assert "exactly one canonical destination" in DREAM_SYSTEM_PROMPT
    assert "If it describes the user" in DREAM_SYSTEM_PROMPT
    assert "If it applies only to the current project" in DREAM_SYSTEM_PROMPT
    assert "NO_CHANGES" in DREAM_SYSTEM_PROMPT


def test_evolution_prompts_use_agent_workflows_without_proposals() -> None:
    for prompt in (DREAM_SYSTEM_PROMPT, SKILL_SYSTEM_PROMPT):
        assert "# Role" in prompt
        assert "# Workflow" in prompt
        assert "# Tool use" in prompt
        assert "NO_CHANGES" in prompt
        assert "Return strict JSON" not in prompt
    assert "proposal" not in SKILL_SYSTEM_PROMPT.lower()
    assert "metadata.managed_by: flocks" in SKILL_SYSTEM_PROMPT
    assert "Read the complete existing managed" in SKILL_SYSTEM_PROMPT
    assert "# Change decision" in SKILL_SYSTEM_PROMPT
    assert "do not create a competing" in SKILL_SYSTEM_PROMPT


def test_skill_catalog_budget_preserves_valid_complete_json_entries() -> None:
    catalog = [
        {
            "name": "first",
            "description": "First reusable workflow",
            "location": "/skills/first/SKILL.md",
            "source": "global",
            "managed_by": "flocks",
        },
        {
            "name": "second",
            "description": "Second reusable workflow",
            "location": "/skills/second/SKILL.md",
            "source": "project",
            "managed_by": "",
        },
    ]
    first_only = json.dumps(
        [catalog[0]],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    serialized = _serialize_skill_catalog(
        catalog,
        len(first_only),
    )

    assert len(serialized) <= len(first_only)
    assert json.loads(serialized) == [catalog[0]]


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
                "content": "Uses concise commits globally.",
                "inject": True,
            },
            "project_memory": {
                "path": "projects/prj_test/MEMORY.md",
                "content": "Project uses Ruff.",
                "inject": True,
            },
        },
    )

    assert prompts == [
        "## USER.md\n\nPrefers concise answers.",
        "## MEMORY.md\n\nUses concise commits globally.",
        "## projects/prj_test/MEMORY.md\n\nProject uses Ruff.",
    ]


@pytest.mark.asyncio
async def test_checkpoint_is_pipeline_specific_and_detects_changes(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "evolution.db")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_test",
        content="hello",
        content_hash="hash-one",
        line_count=1,
        last_message_id="msg_1",
    )

    assert not await EvolutionCheckpointStore.is_current("dream", source)
    await EvolutionCheckpointStore.commit("dream", [source])
    assert await EvolutionCheckpointStore.is_current("dream", source)


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
        "flocks.memory.evolution.common.Message.list_with_parts",
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
        "flocks.memory.evolution.common.Message.list_with_parts",
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


def test_daily_delta_filters_mapped_session_sections_by_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "2026-01-01.md"
    path.write_text(
        "# Daily Memory - 2026-01-01\n"
        "\n## Session ses_alpha_123456… (date)\n\nalpha note\n"
        "\n## Session ses_beta_1234567… (date)\n\nbeta note\n"
        "\n## Session unknown_12345678… (date)\n\nunknown note\n",
        encoding="utf-8",
    )

    snapshot, backlog = _daily_delta(
        path,
        None,
        max_chars=10_000,
        scope=MemoryScope.PROJECT,
        scope_id="prj_alpha",
        allowed_session_ids={"ses_alpha_123456789"},
        session_prefixes={
            "ses_alpha_123456": "ses_alpha_123456789",
            "ses_beta_1234567": "ses_beta_123456789",
            "unknown_12345678": None,
        },
    )

    assert snapshot is not None
    assert "alpha note" in snapshot.content
    assert "beta note" not in snapshot.content
    assert "unknown note" not in snapshot.content
    assert snapshot.scope == MemoryScope.PROJECT
    assert snapshot.scope_id == "prj_alpha"
    assert snapshot.line_count == len(path.read_text(encoding="utf-8").splitlines(keepends=True))
    assert backlog is False


@pytest.mark.asyncio
async def test_dream_sources_share_budget_and_deduplicate_daily_session(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-sources.db")
    data_dir = tmp_path / "data"
    daily_path = data_dir / "memory" / "daily" / "2026-07-29.md"
    daily_path.parent.mkdir(parents=True)
    session_id = "ses_alpha_123456789"
    daily_path.write_text(
        "\n## Session ses_alpha_123456… (date)\n\nsame evidence\n",
        encoding="utf-8",
    )
    session = SimpleNamespace(
        id=session_id,
        category="user",
        status="active",
        project_id="default",
        directory=str(tmp_path),
    )
    session_source = SourceSnapshot(
        source_type="session",
        source_key=session_id,
        content="user: primary evidence",
        content_hash="session-hash",
        line_count=1,
        last_message_id="msg_2",
    )
    session_delta = AsyncMock(return_value=(session_source, False))

    with (
        patch(
            "flocks.session.session.Session.list_all_unfiltered",
            new=AsyncMock(return_value=[session]),
        ),
        patch(
            "flocks.memory.evolution.common.Config.get_data_path",
            return_value=data_dir,
        ),
        patch(
            "flocks.memory.evolution.common._session_delta",
            new=session_delta,
        ),
    ):
        sources, backlog, _ = await _collect_dream_sources(
            MemoryConfig(),
            DreamTarget.global_only(),
            max_chars=1_000,
        )

    assert session_delta.await_args.kwargs["max_chars"] == 1_000
    assert sources[0] == session_source
    assert sources[1].source_type == "daily"
    assert sources[1].content == ""
    assert backlog is False


@pytest.mark.asyncio
async def test_checkpoint_cursors_are_independent_by_scope(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "checkpoint-scope.db")
    global_source = SourceSnapshot(
        source_type="session",
        source_key="ses_shared",
        content="global",
        content_hash="global-hash",
        line_count=1,
        last_message_id="msg_global",
    )
    project_source = SourceSnapshot(
        source_type="session",
        source_key="ses_shared",
        content="project",
        content_hash="project-hash",
        line_count=1,
        scope=MemoryScope.PROJECT,
        scope_id="prj_test",
        last_message_id="msg_project",
    )

    await EvolutionCheckpointStore.commit("dream", [global_source])
    await EvolutionCheckpointStore.commit("dream", [project_source])

    global_row = await EvolutionCheckpointStore.get(
        "dream",
        "session",
        "ses_shared",
    )
    project_row = await EvolutionCheckpointStore.get(
        "dream",
        "session",
        "ses_shared",
        scope=MemoryScope.PROJECT,
        scope_id="prj_test",
    )
    assert global_row["last_message_id"] == "msg_global"
    assert project_row["last_message_id"] == "msg_project"


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
    async def run_agent(**_: object) -> None:
        (memory_root / "MEMORY.md").write_text(
            "# Memory\n\n- Project uses Ruff\n",
            encoding="utf-8",
        )
        (memory_root / "USER.md").write_text(
            "# User\n\n- Prefers concise answers\n",
            encoding="utf-8",
        )

    with (
        patch(
            "flocks.memory.evolution.dream.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.evolution.dream._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [("project", "/workspace")])),
        ),
        patch(
            "flocks.memory.evolution.dream.run_evolution_agent",
            new=AsyncMock(side_effect=run_agent),
        ),
        patch(
            "flocks.memory.evolution.dream._sync_memory_indexes",
            new=AsyncMock(),
        ),
    ):
        result = await run_dream_bridge()

    assert result.changed is True
    assert "Project uses Ruff" in (memory_root / "MEMORY.md").read_text()
    assert "Prefers concise answers" in (memory_root / "USER.md").read_text()
    checkpoint = await EvolutionCheckpointStore.get(
        "dream",
        "session",
        "ses_test",
    )
    assert checkpoint is not None
    assert checkpoint["last_message_id"] == "msg_2"


@pytest.mark.asyncio
async def test_dream_bridge_supplies_complete_memory_without_syncing_no_changes(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-complete-input.db")
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    memory_content = "# Memory\n\n- head-marker\n" + ("x" * 12_000) + "\n- tail-marker\n"
    (memory_root / "MEMORY.md").write_text(
        memory_content,
        encoding="utf-8",
    )
    (memory_root / "USER.md").write_text(
        "# User\n",
        encoding="utf-8",
    )
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_complete",
        content="user: password=do-not-send",
        content_hash="delta",
        line_count=1,
        last_message_id="msg_complete",
    )
    agent_run = AsyncMock(return_value=False)
    sync = AsyncMock()
    collect = AsyncMock(return_value=([source], False, []))

    with (
        patch(
            "flocks.memory.evolution.dream.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=MemoryConfig())),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.evolution.dream._collect_dream_sources",
            new=collect,
        ),
        patch(
            "flocks.memory.evolution.dream.run_evolution_agent",
            new=agent_run,
        ),
        patch(
            "flocks.memory.evolution.dream._sync_memory_indexes",
            new=sync,
        ),
    ):
        result = await run_dream_bridge()

    assert result.changed is False
    user_prompt = agent_run.await_args.kwargs["prompt"]
    assert "- head-marker" in user_prompt
    assert "- tail-marker" in user_prompt
    assert "do-not-send" not in user_prompt
    assert "[REDACTED]" in user_prompt
    assert collect.await_args.kwargs["max_chars"] > 0
    sync.assert_not_awaited()
    checkpoint = await EvolutionCheckpointStore.get(
        "dream",
        "session",
        "ses_complete",
    )
    assert checkpoint is not None
    assert checkpoint["last_message_id"] == "msg_complete"


@pytest.mark.asyncio
async def test_project_dream_updates_project_and_global_user_memory(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "project-dream.db")
    memory_root = tmp_path / "memory"
    project_path = memory_root / "projects" / "prj_test" / "MEMORY.md"
    project_path.parent.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Global Memory\n",
        encoding="utf-8",
    )
    (memory_root / "USER.md").write_text("# User\n", encoding="utf-8")
    project_path.write_text("# Project Memory\n", encoding="utf-8")
    source = SourceSnapshot(
        source_type="session",
        source_key="ses_project",
        content="user: project uses Ruff",
        content_hash="delta",
        line_count=1,
        scope=MemoryScope.PROJECT,
        scope_id="prj_test",
        last_message_id="msg_project",
    )

    async def apply_dream_updates(**_: object) -> bool:
        project_path.write_text(
            "# Project Memory\n\n- Project uses Ruff\n",
            encoding="utf-8",
        )
        (memory_root / "USER.md").write_text(
            "# User\n\n- Prefers concise answers\n",
            encoding="utf-8",
        )
        return True

    with (
        patch(
            "flocks.memory.evolution.dream.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=MemoryConfig())),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.evolution.dream._collect_dream_sources",
            new=AsyncMock(
                return_value=(
                    [source],
                    False,
                    [("prj_test", "/workspace")],
                )
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.run_evolution_agent",
            new=AsyncMock(side_effect=apply_dream_updates),
        ),
        patch(
            "flocks.memory.evolution.dream._sync_memory_indexes",
            new=AsyncMock(),
        ),
    ):
        result = await run_dream_bridge(DreamTarget.project("prj_test"))

    assert result.changed is True
    assert "Project uses Ruff" in project_path.read_text(encoding="utf-8")
    assert "Project uses Ruff" not in (memory_root / "MEMORY.md").read_text(encoding="utf-8")
    assert "Prefers concise answers" in (memory_root / "USER.md").read_text(encoding="utf-8")
    checkpoint = await EvolutionCheckpointStore.get(
        "dream",
        "session",
        "ses_project",
        scope=MemoryScope.PROJECT,
        scope_id="prj_test",
    )
    assert checkpoint["last_message_id"] == "msg_project"


@pytest.mark.asyncio
async def test_dream_bridge_retries_without_rolling_back_when_index_sync_fails(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-index-retry.db")
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
    sync = AsyncMock(side_effect=RuntimeError("index failed"))

    async def apply_dream_updates(**_: object) -> bool:
        memory_path.write_text("new memory\n", encoding="utf-8")
        user_path.write_text("new user\n", encoding="utf-8")
        return True

    with (
        patch(
            "flocks.memory.evolution.dream.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.evolution.dream._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [])),
        ),
        patch(
            "flocks.memory.evolution.dream.run_evolution_agent",
            new=AsyncMock(side_effect=apply_dream_updates),
        ),
        patch(
            "flocks.memory.evolution.dream._sync_memory_indexes",
            new=sync,
        ),
    ):
        with pytest.raises(RuntimeError, match="index failed"):
            await run_dream_bridge()

    assert memory_path.read_text() == "new memory\n"
    assert user_path.read_text() == "new user\n"
    assert await EvolutionCheckpointStore.get("dream", "session", "ses_test") is None


@pytest.mark.asyncio
async def test_dream_bridge_retries_without_rolling_back_when_checkpoint_commit_fails(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "dream-checkpoint-retry.db")
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

    async def apply_dream_updates(**_: object) -> bool:
        memory_path.write_text("new memory\n", encoding="utf-8")
        return True

    with (
        patch(
            "flocks.memory.evolution.dream.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=MemoryConfig())),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.resolve_default_llm",
            new=AsyncMock(
                return_value={
                    "provider_id": "test-provider",
                    "model_id": "test-model",
                }
            ),
        ),
        patch(
            "flocks.memory.evolution.dream.Config.get_data_path",
            return_value=tmp_path,
        ),
        patch(
            "flocks.memory.evolution.dream._collect_dream_sources",
            new=AsyncMock(return_value=([source], False, [])),
        ),
        patch(
            "flocks.memory.evolution.dream.run_evolution_agent",
            new=AsyncMock(side_effect=apply_dream_updates),
        ),
        patch(
            "flocks.memory.evolution.dream._sync_memory_indexes",
            new=AsyncMock(),
        ),
        patch.object(
            EvolutionCheckpointStore,
            "commit",
            new=AsyncMock(side_effect=RuntimeError("checkpoint failed")),
        ),
    ):
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await run_dream_bridge()

    assert memory_path.read_text() == "new memory\n"
    assert user_path.read_text() == "old user\n"


def test_turn_review_redacts_trace_without_correction_trigger() -> None:
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
    config = MemoryConfig()

    evidence = _build_turn_evidence(
        session_id="ses_test",
        user_message_id="msg_2",
        assistant_message_id="msg_3",
        messages=messages,
    )
    assert evidence is not None
    review = _build_turn_review(
        evidence=evidence,
        config=config,
    )

    assert review is not None
    assert review.trigger_reasons == ("tool_iteration_threshold",)
    assert "call_1" not in review.content
    assert '"index": 1' in review.content
    assert "secret-value" not in review.content
    assert "abcdefghijklmnop" not in review.content
    assert "[REDACTED]" in review.content


def test_turn_evidence_counts_agentloop_rounds_not_calls() -> None:
    parallel_tools = tuple(
        _completed_tool("msg_2", f"call_{index}")
        for index in range(10)
    )
    messages = [
        _message("msg_1", "user", _text("msg_1", "run it")),
        _message(
            "msg_2",
            "assistant",
            *parallel_tools,
            _completed_tool(
                "msg_2",
                "call_ignored",
                part_metadata={"ignored": True},
            ),
        ),
        _message(
            "msg_3",
            "assistant",
            _completed_tool("msg_3", "call_next"),
        ),
        _message(
            "msg_4",
            "assistant",
            _text("msg_4", "done"),
            finish="stop",
        ),
    ]

    evidence = _build_turn_evidence(
        session_id="ses_test",
        user_message_id="msg_1",
        assistant_message_id="msg_4",
        messages=messages,
    )

    assert evidence is not None
    assert evidence.tool_iterations == 2


@pytest.mark.asyncio
async def test_load_turn_evidence_reads_session_once() -> None:
    messages = [
        _message("msg_1", "user", _text("msg_1", "run it")),
        _message(
            "msg_2",
            "assistant",
            _completed_tool("msg_2", "call_1"),
        ),
        _message(
            "msg_3",
            "assistant",
            _text("msg_3", "done"),
            finish="stop",
        ),
    ]
    list_messages = AsyncMock(return_value=messages)

    with patch(
        "flocks.memory.evolution.common.Message.list_with_parts",
        new=list_messages,
    ):
        evidence = await _load_turn_evidence(
            session_id="ses_test",
            user_message_id="msg_1",
            assistant_message_id="msg_3",
        )

    assert evidence is not None
    assert evidence.tool_iterations == 1
    list_messages.assert_awaited_once_with(
        "ses_test",
        include_archived=True,
    )


def test_turn_review_requires_tool_trace_unless_manual() -> None:
    messages = [
        _message("msg_1", "user", _text("msg_1", "run the workflow")),
        _message(
            "msg_2",
            "assistant",
            _text("msg_2", "done"),
            finish="stop",
        ),
    ]
    config = MemoryConfig()

    evidence = _build_turn_evidence(
        session_id="ses_test",
        user_message_id="msg_1",
        assistant_message_id="msg_2",
        messages=messages,
    )
    assert evidence is not None
    automatic = _build_turn_review(
        evidence=evidence,
        config=config,
    )
    manual = _build_turn_review(
        evidence=evidence,
        config=config,
        force=True,
    )

    assert automatic is None
    assert manual is not None
    assert manual.trigger_reasons == ("manual",)


def test_turn_review_starts_after_previous_review_watermark() -> None:
    messages = [
        _message("msg_1", "user", _text("msg_1", "old request")),
        _message(
            "msg_2",
            "assistant",
            _completed_tool(
                "msg_2",
                "old_call",
                output="old tool evidence",
            ),
            finish="stop",
        ),
        _message("msg_3", "user", _text("msg_3", "new request")),
        _message(
            "msg_4",
            "assistant",
            _completed_tool(
                "msg_4",
                "new_call",
                output="new tool evidence",
            ),
            finish="stop",
        ),
    ]

    evidence = _build_turn_evidence(
        session_id="ses_test",
        user_message_id="msg_3",
        assistant_message_id="msg_4",
        messages=messages,
    )
    assert evidence is not None
    review = _build_turn_review(
        evidence=evidence,
        config=MemoryConfig(),
        since_message_id="msg_2",
    )

    assert review is not None
    assert "new request" in review.content
    assert "new tool evidence" in review.content
    assert "old request" not in review.content
    assert "old tool evidence" not in review.content


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


@pytest.mark.asyncio
async def test_evolution_schema_removes_legacy_proposal_table(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "evolution-schema.db")
    async with Storage.connect() as db:
        await db.execute(
            "CREATE TABLE memory_skill_proposals (id TEXT PRIMARY KEY)"
        )
        await db.commit()

    await EvolutionCheckpointStore.ensure_schema()

    async with Storage.connect() as db:
        cursor = await db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'memory_skill_proposals'
            """
        )
        assert await cursor.fetchone() is None
        cursor = await db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
                AND name = 'memory_skill_evolution_state'
            """
        )
        assert await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_process_skill_turn_runs_agent_with_one_loaded_session_snapshot(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "skill-turn.db")
    config = MemoryConfig()
    review = _review()
    skill_root = tmp_path / "skills"
    skill_path = skill_root / "pytest-workflow" / "SKILL.md"
    session = SimpleNamespace(
        id="ses_test",
        category="user",
        status="active",
        project_id="default",
        directory=str(tmp_path),
    )

    async def run_agent(**_: object) -> None:
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            _skill_document("pytest-workflow"),
            encoding="utf-8",
    )

    agent_run = AsyncMock(side_effect=run_agent)
    load_evidence = AsyncMock(return_value=_evidence(10))
    with (
        patch(
            "flocks.memory.evolution.skill.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.memory.evolution.skill._load_turn_evidence",
            new=load_evidence,
        ),
        patch(
            "flocks.memory.evolution.skill._build_turn_review",
            new=Mock(return_value=review),
        ),
        patch(
            "flocks.memory.evolution.skill._skill_catalog",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.memory.evolution.skill.run_evolution_agent",
            new=agent_run,
        ),
        patch(
            "flocks.memory.evolution.skill._invalidate_skill_caches",
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
    assert skill_path.exists()
    agent_run.assert_awaited_once()
    assert agent_run.await_args.kwargs["agent_name"] == "learn"
    assert "skills/*/SKILL.md" in (
        agent_run.await_args.kwargs["write_permission_patterns"]
    )
    load_evidence.assert_awaited_once_with(
        session_id="ses_test",
        user_message_id="msg_1",
        assistant_message_id="msg_2",
    )
    async with Storage.connect() as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM memory_evolution_checkpoints
            WHERE pipeline = 'skill'
            """
        )
        assert (await cursor.fetchone())[0] == 0
    state = await SkillEvolutionStateStore.get("ses_test")
    assert state is not None
    assert state.last_reviewed_message_id == "msg_2"
    assert state.pending_tool_iterations == 0


@pytest.mark.asyncio
async def test_manual_learn_reuses_the_session_messages_it_loaded() -> None:
    session = SimpleNamespace(
        id="ses_test",
        category="user",
        provider="test-provider",
        model="test-model",
    )
    messages = [
        _message("msg_1", "user", _text("msg_1", "run it")),
        _message(
            "msg_2",
            "assistant",
            _text("msg_2", "done"),
            finish="stop",
        ),
    ]
    messages[1].info.providerID = "test-provider"
    messages[1].info.modelID = "test-model"
    list_messages = AsyncMock(return_value=messages)
    process = AsyncMock(return_value=False)

    with (
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.session.message.Message.list_with_parts",
            new=list_messages,
        ),
        patch(
            "flocks.memory.evolution.skill.process_skill_turn",
            new=process,
        ),
    ):
        changed = await run_manual_skill_evolution("ses_test")

    assert changed is False
    list_messages.assert_awaited_once_with(
        "ses_test",
        include_archived=True,
    )
    evidence = process.await_args.kwargs["turn_evidence"]
    assert evidence.messages == tuple(messages)
    assert evidence.user_message_id == "msg_1"
    assert evidence.assistant_message_id == "msg_2"


@pytest.mark.asyncio
async def test_skill_trigger_accumulates_tool_iterations_across_turns(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "skill-iterations.db")
    config = MemoryConfig(
        evolution={"skill": {"tool_iteration_interval": 10}}
    )
    skill_root = tmp_path / "skills"
    skill_path = skill_root / "pytest-workflow" / "SKILL.md"
    session = SimpleNamespace(
        id="ses_test",
        category="user",
        status="active",
        project_id="default",
        directory=str(tmp_path),
    )
    review = _review(assistant_message_id="msg_4")
    evidence = [
        _evidence(6),
        _evidence(
            4,
            user_message_id="msg_3",
            assistant_message_id="msg_4",
        ),
    ]

    async def run_agent(**_: object) -> None:
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            _skill_document("pytest-workflow"),
            encoding="utf-8",
        )

    with (
        patch(
            "flocks.memory.evolution.skill.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.session.session.Session.get_by_id",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "flocks.memory.evolution.skill._load_turn_evidence",
            new=AsyncMock(side_effect=evidence),
        ),
        patch(
            "flocks.memory.evolution.skill._build_turn_review",
            new=Mock(return_value=review),
        ) as build_review,
        patch(
            "flocks.memory.evolution.skill._skill_catalog",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "flocks.memory.evolution.skill.run_evolution_agent",
            new=AsyncMock(side_effect=run_agent),
        ),
        patch(
            "flocks.memory.evolution.skill._invalidate_skill_caches",
        ),
    ):
        first = await process_skill_turn(
            session_id="ses_test",
            user_message_id="msg_1",
            assistant_message_id="msg_2",
            provider_id="test-provider",
            model_id="test-model",
            skill_root=skill_root,
        )
        pending = await SkillEvolutionStateStore.get("ses_test")
        second = await process_skill_turn(
            session_id="ses_test",
            user_message_id="msg_3",
            assistant_message_id="msg_4",
            provider_id="test-provider",
            model_id="test-model",
            skill_root=skill_root,
        )

    assert first is False
    assert pending is not None
    assert pending.pending_tool_iterations == 6
    assert second is True
    build_review.assert_called_once()
    assert (
        build_review.call_args.kwargs["since_message_id"]
        is None
    )
    state = await SkillEvolutionStateStore.get("ses_test")
    assert state is not None
    assert state.pending_tool_iterations == 0
    assert state.last_reviewed_message_id == "msg_4"


@pytest.mark.asyncio
async def test_turn_finish_schedules_only_skill_review_without_blocking() -> None:
    hook = SessionEvolutionHook()
    review = AsyncMock(return_value=False)
    before = set(pending_background_tasks())

    with patch("flocks.memory.evolution.skill.process_skill_turn", review):
        await hook.turn_finish(
            HookContext(
                stage=HookStage.TURN_FINISH,
                input={
                    "sessionID": "ses_test",
                    "sessionCategory": "user",
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
async def test_turn_finish_skips_temporary_agent_sessions() -> None:
    hook = SessionEvolutionHook()

    with patch(
        "flocks.hooks.builtin.session_evolution.schedule_skill_review"
    ) as schedule:
        await hook.turn_finish(
            HookContext(
                stage=HookStage.TURN_FINISH,
                input={
                    "sessionID": "ses_evolution",
                    "sessionCategory": "task",
                },
            )
        )

    schedule.assert_not_called()


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
    MemoryEvolutionScheduler._retry_after_by_target.clear()

    with (
        patch(
            "flocks.memory.evolution.scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.scheduler.run_dream_bridge",
            new=AsyncMock(return_value=result),
        ) as run,
        patch(
            "flocks.memory.evolution.scheduler.list_dream_targets",
            new=AsyncMock(return_value=[DreamTarget.global_only()]),
        ),
    ):
        await MemoryEvolutionScheduler._tick_once(now_ts=1_000)
        await MemoryEvolutionScheduler._tick_once(now_ts=1_001)

    run.assert_awaited_once_with(DreamTarget.global_only())
    assert await Storage.get(_LAST_SUCCESS_KEY) == 1_000


def test_scheduler_defaults_to_daily_run_and_half_hour_checks() -> None:
    config = MemoryConfig()

    assert config.evolution.dream.interval_hours == 24
    assert _TICK_SECONDS == 30 * 60


@pytest.mark.asyncio
async def test_scheduler_waits_before_first_timed_dream() -> None:
    with (
        patch(
            "flocks.memory.evolution.scheduler.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch.object(
            MemoryEvolutionScheduler,
            "_tick_once",
            new=AsyncMock(),
        ) as tick,
    ):
        with pytest.raises(asyncio.CancelledError):
            await MemoryEvolutionScheduler._run_loop()

    tick.assert_not_awaited()


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
    MemoryEvolutionScheduler._retry_after_by_target.clear()

    with (
        patch(
            "flocks.memory.evolution.scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.scheduler.run_dream_bridge",
            new=AsyncMock(return_value=result),
        ) as run,
        patch(
            "flocks.memory.evolution.scheduler.list_dream_targets",
            new=AsyncMock(return_value=[DreamTarget.global_only()]),
        ),
    ):
        await MemoryEvolutionScheduler._tick_once(now_ts=1_000)
        await MemoryEvolutionScheduler._tick_once(now_ts=1_060)

    assert run.await_count == 2
    assert await Storage.get(_LAST_SUCCESS_KEY) is None


@pytest.mark.asyncio
async def test_scheduler_waits_fifteen_minutes_after_failure(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "scheduler-failure.db")
    config = MemoryConfig()
    MemoryEvolutionScheduler._retry_after_by_target.clear()

    with (
        patch(
            "flocks.memory.evolution.scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.scheduler.run_dream_bridge",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ) as run,
        patch(
            "flocks.memory.evolution.scheduler.list_dream_targets",
            new=AsyncMock(return_value=[DreamTarget.global_only()]),
        ),
    ):
        await MemoryEvolutionScheduler._tick_once(now_ts=1_000)
        await MemoryEvolutionScheduler._tick_once(now_ts=1_899)
        await MemoryEvolutionScheduler._tick_once(now_ts=1_900)

    assert run.await_count == 2


@pytest.mark.asyncio
async def test_scheduler_isolates_project_target_failures(
    tmp_path: Path,
) -> None:
    await Storage.init(tmp_path / "scheduler-targets.db")
    config = MemoryConfig()
    global_target = DreamTarget.global_only()
    project_target = DreamTarget.project("prj_test")
    MemoryEvolutionScheduler._retry_after_by_target.clear()

    async def run(target: DreamTarget) -> SimpleNamespace:
        if target == global_target:
            raise RuntimeError("global unavailable")
        return SimpleNamespace(
            changed=True,
            processed_sources=1,
            backlog=False,
        )

    with (
        patch(
            "flocks.memory.evolution.scheduler.Config.get",
            new=AsyncMock(return_value=SimpleNamespace(memory=config)),
        ),
        patch(
            "flocks.memory.evolution.scheduler.list_dream_targets",
            new=AsyncMock(return_value=[global_target, project_target]),
        ),
        patch(
            "flocks.memory.evolution.scheduler.run_dream_bridge",
            new=AsyncMock(side_effect=run),
        ) as bridge,
    ):
        await MemoryEvolutionScheduler._tick_once(now_ts=1_000)

    assert bridge.await_args_list[0].args == (global_target,)
    assert bridge.await_args_list[1].args == (project_target,)
    assert MemoryEvolutionScheduler._retry_after_by_target[global_target.scheduler_key] == 1_900
    project_key = MemoryEvolutionScheduler._last_success_key(project_target)
    assert await Storage.get(project_key) == 1_000
