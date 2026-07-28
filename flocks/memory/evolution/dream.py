"""Scheduled Dream bridging for scoped durable Memory."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flocks.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.paths import (
    GLOBAL_MEMORY_FILENAME,
    GLOBAL_SCOPE_ID,
    USER_FILENAME,
    memory_file_path,
)
from flocks.memory.types import MemoryScope

from .common import (
    DreamBridgeResult,
    DreamTarget,
    EvolutionCheckpointStore,
    _PIPELINE_LOCKS,
    _apply_memory_operations,
    _atomic_write,
    _chat_json,
    _collect_dream_sources,
    _restore_memory_files_and_indexes,
    _sync_memory_indexes,
    _truncate_tail,
    list_dream_targets,
)


DREAM_SYSTEM_PROMPT = """
Maintain scoped durable Markdown Memory from incremental evidence.

- Global USER.md contains stable user identity, communication preferences, and
  working habits.
- Global MEMORY.md contains only clearly cross-project preferences and reusable
  rules.
- Project MEMORY.md contains project architecture, conventions, facts,
  decisions, and lessons; project evidence should default there.
- Reject transient task details and session summaries.
- Never infer that project-specific evidence is global.
- A Global-only Dream must skip project-specific information.
- When a Project Dream finds a project-specific entry in Global MEMORY.md, move
  it to Project MEMORY.md only when the current project evidence clearly
  supports that classification; otherwise leave the Global entry unchanged.

Return strict JSON only:
{"action":"skip","reason":"..."}
or
{"action":"update","operations":[
  {"scope":"global|project","path":"USER.md|MEMORY.md",
   "type":"add","content":"..."},
  {"scope":"global|project","path":"USER.md|MEMORY.md",
   "type":"replace","old":"exact text","new":"..."},
  {"scope":"global|project","path":"USER.md|MEMORY.md",
   "type":"remove","old":"exact text"}
]}.

Copy replace/remove targets verbatim.
""".strip()

DREAM_USER_PROMPT = """
Dream target: {target_description}

Incremental Session and mapped Daily evidence:
{source_text}

Current scoped Memory files:
{current_sections}
""".strip()


async def run_dream_bridge(
    target: Optional[DreamTarget] = None,
) -> DreamBridgeResult:
    """Run one incremental Dream batch using the configured default model."""
    target = target or DreamTarget.global_only()
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig):
        return DreamBridgeResult(False, 0, False)
    if not config.enabled or not config.evolution.enabled:
        return DreamBridgeResult(False, 0, False)
    if not config.evolution.dream.enabled:
        return DreamBridgeResult(False, 0, False)

    default_model = await Config.resolve_default_llm()
    provider_id = default_model.get("provider_id") if default_model else None
    model_id = default_model.get("model_id") if default_model else None
    if not provider_id or not model_id:
        raise RuntimeError("no default model is configured for Dream")

    async with _PIPELINE_LOCKS["dream"]:
        sources, backlog, sync_targets = await _collect_dream_sources(
            config,
            target,
        )
        if not sources:
            return DreamBridgeResult(False, 0, backlog)

        source_sections = [
            (f"## {source.source_type}/{source.source_key}\n{source.content}")
            for source in sources
            if source.content.strip()
        ]
        if not source_sections:
            await EvolutionCheckpointStore.commit("dream", sources)
            return DreamBridgeResult(False, len(sources), backlog)

        memory_root = Config.get_data_path() / "memory"
        global_memory_path = memory_file_path(
            memory_root,
            MemoryScope.GLOBAL,
            GLOBAL_SCOPE_ID,
            GLOBAL_MEMORY_FILENAME,
        )
        user_path = memory_file_path(
            memory_root,
            MemoryScope.GLOBAL,
            GLOBAL_SCOPE_ID,
            USER_FILENAME,
        )
        file_targets = {
            (MemoryScope.GLOBAL, USER_FILENAME): user_path,
            (
                MemoryScope.GLOBAL,
                GLOBAL_MEMORY_FILENAME,
            ): global_memory_path,
        }
        if target.scope == MemoryScope.PROJECT:
            file_targets[(MemoryScope.PROJECT, GLOBAL_MEMORY_FILENAME)] = memory_file_path(
                memory_root,
                MemoryScope.PROJECT,
                target.scope_id,
                GLOBAL_MEMORY_FILENAME,
            )

        from flocks.memory.bootstrap import INITIAL_USER_PROFILE

        originals: dict[Path, tuple[bool, str]] = {}
        current_files: dict[tuple[MemoryScope, str], str] = {}
        for key, file_path in file_targets.items():
            existed = file_path.exists()
            if existed:
                content = file_path.read_text(encoding="utf-8")
            elif key == (MemoryScope.GLOBAL, USER_FILENAME):
                content = INITIAL_USER_PROFILE
            elif key == (MemoryScope.PROJECT, GLOBAL_MEMORY_FILENAME):
                from flocks.memory.paths import PROJECT_MEMORY_INITIAL_CONTENT

                content = PROJECT_MEMORY_INITIAL_CONTENT
            else:
                content = ""
            originals[file_path] = (existed, content)
            current_files[key] = content

        source_text = "\n\n".join(source_sections)
        current_sections = "\n\n".join(
            (f"## {scope.value}/{path}\n{_truncate_tail(content, config.evolution.max_input_chars // 6)}")
            for (scope, path), content in current_files.items()
        )
        target_description = (
            f"registered project {target.scope_id}"
            if target.scope == MemoryScope.PROJECT
            else "default Sessions (Global-only)"
        )
        response = await _chat_json(
            provider_id=provider_id,
            model_id=model_id,
            max_tokens=config.evolution.max_output_tokens,
            system=DREAM_SYSTEM_PROMPT,
            user=DREAM_USER_PROMPT.format(
                target_description=target_description,
                source_text=_truncate_tail(
                    source_text,
                    config.evolution.max_input_chars // 2,
                ),
                current_sections=current_sections,
            ),
        )
        updated_files = {
            key: _apply_memory_operations(
                content,
                response,
                dream_target=target,
                file_scope=key[0],
                path=key[1],
            )
            for key, content in current_files.items()
        }
        files_changed = any(updated_files[key] != current_files[key] for key in current_files)

        if files_changed:
            try:
                for key, file_path in file_targets.items():
                    if updated_files[key] != current_files[key]:
                        _atomic_write(file_path, updated_files[key])
                await _sync_memory_indexes(
                    config,
                    sync_targets,
                    fallback_project_id=target.project_id,
                )
                await EvolutionCheckpointStore.commit("dream", sources)
            except BaseException:
                await _restore_memory_files_and_indexes(
                    config=config,
                    sync_targets=sync_targets,
                    fallback_project_id=target.project_id,
                    originals=originals,
                )
                raise
        else:
            await EvolutionCheckpointStore.commit("dream", sources)

        return DreamBridgeResult(files_changed, len(sources), backlog)
