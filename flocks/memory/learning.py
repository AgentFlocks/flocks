"""Post-session Dream and skill self-evolution.

The implementation intentionally uses Flocks' existing session store, Markdown
memory files, skill discovery, model providers, and SQLite database.  There is
no sidecar job directory: a checkpoint is advanced only after the corresponding
live files have been updated successfully.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Literal, Optional

from flocks.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.provider import ChatMessage, Provider
from flocks.provider.options import build_provider_options
from flocks.session.message import Message, TextPart, ToolPart
from flocks.skill.skill import Skill
from flocks.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="memory.learning")
Pipeline = Literal["dream", "skill"]
SourceType = Literal["session", "daily"]
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PIPELINE_LOCKS = {"dream": asyncio.Lock(), "skill": asyncio.Lock()}

_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS memory_learning_checkpoints (
    pipeline TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    line_count INTEGER NOT NULL DEFAULT 0,
    last_message_id TEXT,
    source_mtime REAL,
    processed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline, source_type, source_key)
);
CREATE INDEX IF NOT EXISTS idx_memory_learning_checkpoint_updated
ON memory_learning_checkpoints(pipeline, updated_at);
"""


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable input state used to decide whether a source changed."""

    source_type: SourceType
    source_key: str
    content: str
    content_hash: str
    line_count: int
    last_message_id: Optional[str] = None
    source_mtime: Optional[float] = None


class LearningCheckpointStore:
    """SQLite checkpoints shared by the Dream and skill pipelines."""

    @classmethod
    async def ensure_schema(cls) -> None:
        await Storage._ensure_init()
        async with Storage.connect() as db:
            await db.executescript(_CHECKPOINT_DDL)
            await db.commit()

    @classmethod
    async def is_current(
        cls,
        pipeline: Pipeline,
        source: SourceSnapshot,
    ) -> bool:
        await cls.ensure_schema()
        async with Storage.connect() as db:
            cursor = await db.execute(
                """
                SELECT content_hash, line_count, last_message_id, source_mtime
                FROM memory_learning_checkpoints
                WHERE pipeline = ? AND source_type = ? AND source_key = ?
                """,
                (pipeline, source.source_type, source.source_key),
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return (
            row[0] == source.content_hash
            and row[1] == source.line_count
            and row[2] == source.last_message_id
            and row[3] == source.source_mtime
        )

    @classmethod
    async def commit(
        cls,
        pipeline: Pipeline,
        sources: list[SourceSnapshot],
    ) -> None:
        """Atomically advance all source cursors for one successful run."""
        if not sources:
            return
        await cls.ensure_schema()
        now = datetime.now(UTC).isoformat()
        async with Storage.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                for source in sources:
                    await db.execute(
                        """
                        INSERT INTO memory_learning_checkpoints (
                            pipeline, source_type, source_key, content_hash,
                            line_count, last_message_id, source_mtime,
                            processed_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pipeline, source_type, source_key) DO UPDATE SET
                            content_hash = excluded.content_hash,
                            line_count = excluded.line_count,
                            last_message_id = excluded.last_message_id,
                            source_mtime = excluded.source_mtime,
                            processed_at = excluded.processed_at,
                            updated_at = excluded.updated_at
                        """,
                        (
                            pipeline,
                            source.source_type,
                            source.source_key,
                            source.content_hash,
                            source.line_count,
                            source.last_message_id,
                            source.source_mtime,
                            now,
                            now,
                        ),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _truncate_tail(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[-limit:]


def _atomic_write(path: Path, content: str) -> None:
    """Write UTF-8 text by replacing a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("learning response must be a JSON object")
    return value


async def _chat_json(
    *,
    provider_id: str,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any]:
    config = await Config.get()
    await Provider.apply_config(config, provider_id=provider_id)
    provider = Provider.get(provider_id)
    if provider is None:
        raise RuntimeError(f"provider not found: {provider_id}")
    options = build_provider_options(provider_id, model_id)
    options.pop("max_tokens", None)
    response = await provider.chat(
        model_id=model_id,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ],
        **options,
        max_tokens=max_tokens,
    )
    return _parse_json_object(response.content)


async def _session_snapshot(
    session_id: str,
    max_messages: int,
    max_chars: int,
) -> SourceSnapshot:
    messages = await Message.list_recent_with_parts(
        session_id,
        limit=max_messages,
        include_archived=True,
    )
    with_parts = messages[0]
    lines: list[str] = []
    for message in with_parts:
        role = message.info.role
        for part in message.parts:
            if isinstance(part, TextPart) and part.text.strip():
                lines.append(f"{role}: {part.text.strip()}")
            elif isinstance(part, ToolPart):
                state = part.state
                payload = {
                    "tool": part.tool,
                    "status": state.status,
                    "input": getattr(state, "input", None),
                    "output": getattr(state, "output", None),
                    "error": getattr(state, "error", None),
                }
                lines.append(f"tool: {json.dumps(payload, ensure_ascii=False, default=str)}")
    content = "\n\n".join(lines)
    if len(content) > max_chars:
        content = content[-max_chars:]
    last_message_id = with_parts[-1].info.id if with_parts else None
    return SourceSnapshot(
        source_type="session",
        source_key=session_id,
        content=content,
        content_hash=_hash_text(content),
        line_count=len(content.splitlines()),
        last_message_id=last_message_id,
    )


def _daily_snapshots(memory_root: Path, limit: int) -> list[SourceSnapshot]:
    daily_dir = memory_root / "daily"
    paths = sorted(daily_dir.glob("*.md"), reverse=True)[:limit] if limit else []
    snapshots: list[SourceSnapshot] = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        snapshots.append(
            SourceSnapshot(
                source_type="daily",
                source_key=path.stem,
                content=content,
                content_hash=_hash_text(content),
                line_count=len(content.splitlines()),
                source_mtime=path.stat().st_mtime,
            )
        )
    return snapshots


def _apply_memory_operations(current: str, response: dict[str, Any]) -> str:
    action = response.get("action")
    if action == "skip":
        return current
    if action != "update":
        raise ValueError("Dream action must be 'skip' or 'update'")
    operations = response.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("Dream update requires non-empty operations")
    result = current
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("Dream operation must be an object")
        operation_type = operation.get("type")
        if operation_type == "add":
            content = str(operation.get("content") or "").strip()
            if not content:
                raise ValueError("Dream add requires content")
            result = result.rstrip() + ("\n\n" if result.strip() else "") + content + "\n"
            continue
        old = str(operation.get("old") or "")
        if not old or result.count(old) != 1:
            raise ValueError("Dream replace/remove target must match exactly once")
        if operation_type == "replace":
            replacement = str(operation.get("new") or "")
            result = result.replace(old, replacement, 1)
        elif operation_type == "remove":
            result = result.replace(old, "", 1)
        else:
            raise ValueError(f"unsupported Dream operation: {operation_type}")
    return result


async def _run_dream(
    *,
    source: SourceSnapshot,
    daily_sources: list[SourceSnapshot],
    provider_id: str,
    model_id: str,
    project_id: str,
    workspace: str,
    config: MemoryConfig,
) -> bool:
    sources = [source, *daily_sources]
    if all(await asyncio.gather(
        *(LearningCheckpointStore.is_current("dream", item) for item in sources)
    )):
        return False
    memory_root = Config.get_data_path() / "memory"
    memory_path = memory_root / "MEMORY.md"
    current = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    recent_daily = "\n\n".join(
        f"## daily/{item.source_key}.md\n{item.content}" for item in daily_sources
    )
    response = await _chat_json(
        provider_id=provider_id,
        model_id=model_id,
        max_tokens=config.learning.max_output_tokens,
        system=(
            "You maintain durable long-term memory. Extract only stable preferences, "
            "facts, decisions, and reusable context. Remove stale or contradicted facts. "
            "Return strict JSON only: {\"action\":\"skip\",\"reason\":\"...\"} or "
            "{\"action\":\"update\",\"operations\":[{\"type\":\"add\",\"content\":\"...\"},"
            "{\"type\":\"replace\",\"old\":\"exact text\",\"new\":\"...\"},"
            "{\"type\":\"remove\",\"old\":\"exact text\"}]}. Exact targets must be copied "
            "verbatim from MEMORY.md. Avoid session summaries and transient task details."
        ),
        user=(
            "New completed session:\n"
            f"{_truncate_tail(source.content, config.learning.max_input_chars // 3)}\n\n"
            "Recent daily memory:\n"
            f"{_truncate_tail(recent_daily, config.learning.max_input_chars // 3)}\n\n"
            "Current MEMORY.md:\n"
            f"{_truncate_tail(current, config.learning.max_input_chars // 3)}"
        ),
    )
    updated = _apply_memory_operations(current, response)
    if updated != current:
        _atomic_write(memory_path, updated)
        manager = MemoryManager.get_instance(
            project_id=project_id,
            workspace_dir=workspace,
            config=config,
        )
        try:
            await manager.sync(reason="dream")
        except BaseException:
            _atomic_write(memory_path, current)
            try:
                await manager.sync(reason="dream-restore")
            except Exception as restore_exc:
                log.warn(
                    "dream.restore_reindex_failed",
                    {"error": str(restore_exc)},
                )
            raise
    await LearningCheckpointStore.commit("dream", sources)
    return updated != current


def _skill_catalog() -> list[dict[str, str]]:
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "location": skill.location,
        }
        for skill in Skill._all_sync()
    ]


def _related_skill_contents(
    names: list[str],
    catalog: list[dict[str, str]],
    limit: int,
) -> dict[str, str]:
    locations = {item["name"]: item["location"] for item in catalog}
    result: dict[str, str] = {}
    for name in names[:limit]:
        location = locations.get(name)
        if location:
            result[name] = Path(location).read_text(encoding="utf-8")
    return result


def _validate_skill_document(content: str, expected_name: str) -> None:
    metadata = Skill._parse_frontmatter(content)
    if metadata.get("name") != expected_name:
        raise ValueError("SKILL.md frontmatter name does not match target skill")
    if not Skill._is_valid_name(expected_name):
        raise ValueError("invalid skill name")
    if not Skill._is_valid_description(str(metadata.get("description") or "")):
        raise ValueError("invalid skill description")


def _safe_skill_path(root: Path, skill_name: str, relative: str) -> Path:
    if not _SKILL_NAME_RE.fullmatch(skill_name):
        raise ValueError("invalid skill name")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("skill file path must stay inside the skill directory")
    resolved_root = root.resolve()
    target = (root / skill_name / relative_path).resolve()
    skill_dir = (root / skill_name).resolve()
    if skill_dir != resolved_root and resolved_root not in skill_dir.parents:
        raise ValueError("skill directory escaped the configured skill root")
    if target != skill_dir and skill_dir not in target.parents:
        raise ValueError("skill file path escaped the skill directory")
    return target


def _apply_skill_action(
    response: dict[str, Any],
    skill_root: Path,
    related: dict[str, str],
) -> bool:
    action = response.get("action")
    if action == "skip":
        return False
    skill_name = str(response.get("skill_name") or "")
    if action == "create":
        target = _safe_skill_path(skill_root, skill_name, "SKILL.md")
        if target.exists():
            raise ValueError("create target already exists")
        content = str(response.get("content") or "")
        _validate_skill_document(content, skill_name)
        _atomic_write(target, content)
    elif action == "replace":
        target = _safe_skill_path(skill_root, skill_name, "SKILL.md")
        if skill_name not in related:
            raise ValueError("replace target was not selected as related")
        content = str(response.get("content") or "")
        _validate_skill_document(content, skill_name)
        _atomic_write(target, content)
    elif action == "patch":
        target = _safe_skill_path(
            skill_root,
            skill_name,
            str(response.get("path") or "SKILL.md"),
        )
        if skill_name not in related or not target.exists():
            raise ValueError("patch target was not selected as related")
        current = target.read_text(encoding="utf-8")
        old = str(response.get("old") or "")
        if not old or current.count(old) != 1:
            raise ValueError("skill patch target must match exactly once")
        updated = current.replace(old, str(response.get("new") or ""), 1)
        if target.name == "SKILL.md":
            _validate_skill_document(updated, skill_name)
        _atomic_write(target, updated)
    elif action == "write_file":
        if skill_name not in related:
            raise ValueError("write_file target was not selected as related")
        target = _safe_skill_path(
            skill_root,
            skill_name,
            str(response.get("path") or ""),
        )
        content = str(response.get("content") or "")
        if target.name == "SKILL.md":
            _validate_skill_document(content, skill_name)
        _atomic_write(target, content)
    else:
        raise ValueError("unsupported skill action")
    Skill.clear_cache()
    return True


async def _run_skill_evolution(
    *,
    source: SourceSnapshot,
    provider_id: str,
    model_id: str,
    config: MemoryConfig,
    skill_root: Optional[Path] = None,
) -> bool:
    if await LearningCheckpointStore.is_current("skill", source):
        return False
    catalog = _skill_catalog()
    selection = await _chat_json(
        provider_id=provider_id,
        model_id=model_id,
        max_tokens=1000,
        system=(
            "Decide whether a completed session teaches a reusable procedure that belongs "
            "in an agent skill. Return strict JSON only: {\"action\":\"skip\",\"reason\":\"...\"} "
            "or {\"action\":\"evolve\",\"skill_names\":[\"existing-name\"],"
            "\"reason\":\"...\"}. Select only skills whose full content is needed; use an "
            "empty list when a new skill is warranted."
        ),
        user=(
            "Session including tool calls:\n"
            f"{_truncate_tail(source.content, config.learning.max_input_chars * 2 // 3)}"
            "\n\nAvailable skill names and descriptions:\n"
            f"{_truncate_tail(json.dumps(catalog, ensure_ascii=False), config.learning.max_input_chars // 3)}"
        ),
    )
    if selection.get("action") == "skip":
        await LearningCheckpointStore.commit("skill", [source])
        return False
    if selection.get("action") != "evolve":
        raise ValueError("skill selection action must be skip or evolve")
    names = selection.get("skill_names")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("skill_names must be a string list")
    related = _related_skill_contents(
        names,
        catalog,
        config.learning.skill.max_related_skills,
    )
    response = await _chat_json(
        provider_id=provider_id,
        model_id=model_id,
        max_tokens=config.learning.max_output_tokens,
        system=(
            "Directly evolve live agent skills from proven session behavior. Return strict "
            "JSON only. Actions: skip; create(skill_name, content=complete SKILL.md); "
            "replace(skill_name, content=complete SKILL.md); patch(skill_name, path, old, new) "
            "where old matches exactly once; write_file(skill_name, path, content). Skill "
            "names use lowercase letters, digits, and hyphens. SKILL.md must have valid YAML "
            "frontmatter with matching name and a useful description. Do not create a skill "
            "for one-off facts or transient project details."
        ),
        user=(
            "Session including tool calls:\n"
            f"{_truncate_tail(source.content, config.learning.max_input_chars * 2 // 3)}"
            "\n\nSelected existing skills:\n"
            f"{_truncate_tail(json.dumps(related, ensure_ascii=False), config.learning.max_input_chars // 3)}"
        ),
    )
    root = skill_root or Config.get_config_path() / "plugins" / "skills"
    changed = _apply_skill_action(response, root, related)
    await LearningCheckpointStore.commit("skill", [source])
    return changed


async def process_completed_session(
    *,
    session_id: str,
    workspace: str,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> dict[str, bool]:
    """Run enabled learning pipelines for a completed session."""
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig):
        return {"dream": False, "skill": False}
    learning = config.learning
    if not config.enabled or not learning.enabled:
        return {"dream": False, "skill": False}
    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if session and session.category != "user":
        return {"dream": False, "skill": False}
    if not provider_id or not model_id:
        provider_id = provider_id or (session.provider if session else None)
        model_id = model_id or (session.model if session else None)
    if not provider_id or not model_id:
        default_model = await Config.resolve_default_llm()
        provider_id = provider_id or (
            default_model.get("provider_id") if default_model else None
        )
        model_id = model_id or (
            default_model.get("model_id") if default_model else None
        )
    if not provider_id or not model_id:
        raise RuntimeError("no model is configured for memory learning")
    source = await _session_snapshot(
        session_id,
        learning.max_session_messages,
        learning.max_input_chars,
    )
    if not source.content:
        return {"dream": False, "skill": False}
    results = {"dream": False, "skill": False}
    errors: list[Exception] = []
    if learning.dream.enabled:
        try:
            async with _PIPELINE_LOCKS["dream"]:
                results["dream"] = await _run_dream(
                    source=source,
                    daily_sources=_daily_snapshots(
                        Config.get_data_path() / "memory",
                        learning.dream.recent_daily_days,
                    ),
                    provider_id=provider_id,
                    model_id=model_id,
                    project_id=session.project_id if session else "default",
                    workspace=workspace,
                    config=config,
                )
        except Exception as exc:
            errors.append(exc)
            log.warn(
                "learning.dream.failed",
                {"session_id": session_id, "error": str(exc)},
            )
    if learning.skill.enabled:
        try:
            async with _PIPELINE_LOCKS["skill"]:
                results["skill"] = await _run_skill_evolution(
                    source=source,
                    provider_id=provider_id,
                    model_id=model_id,
                    config=config,
                )
        except Exception as exc:
            errors.append(exc)
            log.warn(
                "learning.skill.failed",
                {"session_id": session_id, "error": str(exc)},
            )
    if errors:
        raise RuntimeError(
            f"{len(errors)} memory learning pipeline(s) failed"
        ) from errors[0]
    return results


async def catch_up_completed_sessions() -> None:
    """Retry recent sessions whose Dream or skill checkpoints are stale."""
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig) or not config.learning.enabled:
        return
    limit = config.learning.catch_up_sessions
    if limit <= 0:
        return

    from flocks.session.session import Session

    sessions = await Session.list_all_unfiltered()
    eligible = [
        session
        for session in sessions
        if session.category == "user" and session.status != "deleted"
    ][:limit]
    for session in eligible:
        try:
            await process_completed_session(
                session_id=session.id,
                workspace=session.directory,
                provider_id=session.provider,
                model_id=session.model,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "learning.catch_up.failed",
                {"session_id": session.id, "error": str(exc)},
            )
