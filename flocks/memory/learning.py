"""Scheduled Dream bridging and turn-driven skill self-evolution.

Dream consumes incremental user/assistant messages and daily memory on a
background cadence. Skill evolution reviews only eligible successful turns,
persists a validated proposal, and then atomically applies the resulting
user-managed ``SKILL.md``.
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
import uuid

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
_MAX_SKILL_LINES = 500
_TOOL_PAYLOAD_MIN_CHARS = 256

_CORRECTION_RE = re.compile(
    r"(?:"
    r"不对|错了|不是这样|你理解错|我说的是|纠正一下|"
    r"应该(?:是|用|改成)|请改成|不要这样|别再|"
    r"that(?:'s| is) wrong|not what i (?:said|meant|asked)|"
    r"you misunderstood|correction:|please use .+ instead"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
    r"password|passwd|secret|private[-_]?key|credential|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)"
        r"(\s*[=:]\s*)[^\s,;]+"
    ),
    re.compile(
        r"(?i)\b([a-z0-9_]*(?:secret|token|password|api_key|private_key)"
        r"[a-z0-9_]*)(\s*=\s*)[^\s,;]+"
    ),
)
_DESCRIPTION_TRIGGER_RE = re.compile(
    r"(?:\bwhen\b|\bwhenever\b|\btrigger(?:s|ed)?\b|"
    r"\buse (?:this )?skill\b|\bfor (?:tasks?|requests?|users?)\b|"
    r"当.+时|用于|适用于|用户(?:要求|需要)|请求)",
    re.IGNORECASE,
)

_SCHEMA_DDL = """
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

CREATE TABLE IF NOT EXISTS memory_skill_proposals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    assistant_message_id TEXT NOT NULL UNIQUE,
    trigger_reasons TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    action TEXT NOT NULL,
    base_hash TEXT,
    operation_json TEXT NOT NULL,
    proposed_content TEXT NOT NULL,
    proposed_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_skill_proposals_status
ON memory_skill_proposals(status, created_at);
"""


@dataclass(frozen=True)
class SourceSnapshot:
    """Input delta and the source cursor reached by that delta."""

    source_type: SourceType
    source_key: str
    content: str
    content_hash: str
    line_count: int
    last_message_id: Optional[str] = None
    source_mtime: Optional[float] = None


@dataclass(frozen=True)
class DreamBridgeResult:
    """Result of one bounded Dream bridge batch."""

    changed: bool
    processed_sources: int
    backlog: bool


@dataclass(frozen=True)
class TurnReview:
    """Canonical context and tool trace for one successful turn."""

    source: SourceSnapshot
    user_message_id: str
    assistant_message_id: str
    trigger_reasons: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class SkillProposal:
    """Validated full-document skill proposal."""

    id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    trigger_reasons: tuple[str, ...]
    skill_name: str
    action: Literal["create", "patch", "edit"]
    base_hash: Optional[str]
    operation_json: str
    proposed_content: str
    proposed_hash: str
    status: str = "pending"
    error: Optional[str] = None
    created_at: str = ""
    applied_at: Optional[str] = None


class LearningCheckpointStore:
    """SQLite cursors shared by Dream and skill review."""

    @classmethod
    async def ensure_schema(cls) -> None:
        await Storage._ensure_init()
        async with Storage.connect() as db:
            await db.executescript(_SCHEMA_DDL)
            await db.commit()

    @classmethod
    async def get(
        cls,
        pipeline: Pipeline,
        source_type: SourceType,
        source_key: str,
    ) -> Optional[dict[str, Any]]:
        await cls.ensure_schema()
        async with Storage.connect() as db:
            cursor = await db.execute(
                """
                SELECT content_hash, line_count, last_message_id, source_mtime,
                       processed_at, updated_at
                FROM memory_learning_checkpoints
                WHERE pipeline = ? AND source_type = ? AND source_key = ?
                """,
                (pipeline, source_type, source_key),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "content_hash": row[0],
            "line_count": row[1],
            "last_message_id": row[2],
            "source_mtime": row[3],
            "processed_at": row[4],
            "updated_at": row[5],
        }

    @classmethod
    async def is_current(
        cls,
        pipeline: Pipeline,
        source: SourceSnapshot,
    ) -> bool:
        row = await cls.get(pipeline, source.source_type, source.source_key)
        if row is None:
            return False
        return bool(
            row["content_hash"] == source.content_hash
            and row["line_count"] == source.line_count
            and row["last_message_id"] == source.last_message_id
            and row["source_mtime"] == source.source_mtime
        )

    @classmethod
    async def commit(
        cls,
        pipeline: Pipeline,
        sources: list[SourceSnapshot],
    ) -> None:
        """Atomically advance all source cursors for one successful batch."""
        if not sources:
            return
        await cls.ensure_schema()
        now = _now_iso()
        async with Storage.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                for source in sources:
                    await cls._upsert_in_transaction(db, pipeline, source, now)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    @staticmethod
    async def _upsert_in_transaction(
        db: Any,
        pipeline: Pipeline,
        source: SourceSnapshot,
        now: str,
    ) -> None:
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


class SkillProposalStore:
    """Durable proposal boundary for idempotent skill application."""

    _SELECT_COLUMNS = """
        id, session_id, user_message_id, assistant_message_id,
        trigger_reasons, skill_name, action, base_hash, operation_json,
        proposed_content, proposed_hash, status, error, created_at, applied_at
    """

    @classmethod
    async def create_pending(cls, proposal: SkillProposal) -> SkillProposal:
        await LearningCheckpointStore.ensure_schema()
        now = proposal.created_at or _now_iso()
        async with Storage.connect() as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO memory_skill_proposals (
                    id, session_id, user_message_id, assistant_message_id,
                    trigger_reasons, skill_name, action, base_hash,
                    operation_json, proposed_content, proposed_hash,
                    status, error, created_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)
                """,
                (
                    proposal.id,
                    proposal.session_id,
                    proposal.user_message_id,
                    proposal.assistant_message_id,
                    json.dumps(
                        list(proposal.trigger_reasons),
                        ensure_ascii=False,
                    ),
                    proposal.skill_name,
                    proposal.action,
                    proposal.base_hash,
                    proposal.operation_json,
                    proposal.proposed_content,
                    proposal.proposed_hash,
                    now,
                ),
            )
            await db.commit()
        stored = await cls.get_by_assistant_message(proposal.assistant_message_id)
        if stored is None:
            raise RuntimeError("failed to persist skill proposal")
        return stored

    @classmethod
    async def get_by_assistant_message(
        cls,
        assistant_message_id: str,
    ) -> Optional[SkillProposal]:
        await LearningCheckpointStore.ensure_schema()
        async with Storage.connect() as db:
            cursor = await db.execute(
                f"""
                SELECT {cls._SELECT_COLUMNS}
                FROM memory_skill_proposals
                WHERE assistant_message_id = ?
                """,
                (assistant_message_id,),
            )
            row = await cursor.fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    async def list_pending(cls) -> list[SkillProposal]:
        await LearningCheckpointStore.ensure_schema()
        async with Storage.connect() as db:
            cursor = await db.execute(
                f"""
                SELECT {cls._SELECT_COLUMNS}
                FROM memory_skill_proposals
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            )
            rows = await cursor.fetchall()
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def finish(
        cls,
        proposal: SkillProposal,
        source: SourceSnapshot,
        *,
        status: Literal["applied", "conflict"],
        error: Optional[str] = None,
    ) -> None:
        """Finalize a proposal and its Session review checkpoint together."""
        await LearningCheckpointStore.ensure_schema()
        now = _now_iso()
        applied_at = now if status == "applied" else None
        async with Storage.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    UPDATE memory_skill_proposals
                    SET status = ?, error = ?, applied_at = ?
                    WHERE id = ?
                    """,
                    (status, error, applied_at, proposal.id),
                )
                await LearningCheckpointStore._upsert_in_transaction(
                    db,
                    "skill",
                    source,
                    now,
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    @classmethod
    async def record_pending_error(
        cls,
        proposal_id: str,
        error: str,
    ) -> None:
        await LearningCheckpointStore.ensure_schema()
        async with Storage.connect() as db:
            await db.execute(
                """
                UPDATE memory_skill_proposals
                SET error = ?
                WHERE id = ? AND status = 'pending'
                """,
                (error, proposal_id),
            )
            await db.commit()

    @staticmethod
    def _from_row(row: Any) -> SkillProposal:
        reasons = json.loads(row[4])
        return SkillProposal(
            id=row[0],
            session_id=row[1],
            user_message_id=row[2],
            assistant_message_id=row[3],
            trigger_reasons=tuple(str(item) for item in reasons),
            skill_name=row[5],
            action=row[6],
            base_hash=row[7],
            operation_json=row[8],
            proposed_content=row[9],
            proposed_hash=row[10],
            status=row[11],
            error=row[12],
            created_at=row[13],
            applied_at=row[14],
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _user_skill_root() -> Path:
    """Return the canonical user-managed skill directory."""
    return Path.home() / ".flocks" / "plugins" / "skills"


def _truncate_tail(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[-limit:]


def _truncate_middle(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    marker = "\n...[truncated for learning context]...\n"
    available = max(limit - len(marker), 2)
    head = available // 2
    return content[:head] + marker + content[-(available - head) :]


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
            ChatMessage(
                role="system",
                content=system,
                reasoning=None,
                reasoning_content=None,
                reasoning_details=None,
                reasoning_source=None,
                tool_calls=None,
                tool_call_id=None,
                name=None,
            ),
            ChatMessage(
                role="user",
                content=user,
                reasoning=None,
                reasoning_content=None,
                reasoning_details=None,
                reasoning_source=None,
                tool_calls=None,
                tool_call_id=None,
                name=None,
            ),
        ],
        **options,
        max_tokens=max_tokens,
    )
    return _parse_json_object(response.content)


def _message_role(message: Any) -> str:
    role = getattr(message.info, "role", "")
    return getattr(role, "value", role)


def _real_text(message: Any) -> str:
    if _message_role(message) == "assistant" and (
        getattr(message.info, "summary", False) is True or getattr(message.info, "finish", None) == "summary"
    ):
        return ""
    chunks = [
        part.text.strip()
        for part in message.parts
        if isinstance(part, TextPart) and part.text.strip() and not part.synthetic and not part.ignored
    ]
    return "\n".join(chunks)


async def _session_delta(
    session_id: str,
    checkpoint: Optional[dict[str, Any]],
    *,
    max_messages: int,
    max_chars: int,
) -> tuple[Optional[SourceSnapshot], bool]:
    messages = await Message.list_with_parts(session_id, include_archived=True)
    last_message_id = checkpoint.get("last_message_id") if checkpoint else None
    cursor_index = next(
        (index for index, message in enumerate(messages) if message.info.id == last_message_id),
        None,
    )
    if cursor_index is not None:
        pending = messages[cursor_index + 1 :]
    else:
        pending = [message for message in messages if not last_message_id or message.info.id > last_message_id]
    if not pending:
        return None, False

    blocks: list[str] = []
    consumed: list[Any] = []
    content_length = 0
    for message in pending:
        if len(consumed) >= max_messages:
            break
        role = _message_role(message)
        text = _real_text(message) if role in {"user", "assistant"} else ""
        block = f"{role}: {text}" if text else ""
        if block:
            remaining = max(max_chars - content_length, 1)
            if blocks and len(block) > remaining:
                break
            block = _truncate_middle(block, remaining)
            blocks.append(block)
            content_length += len(block) + 2
        consumed.append(message)
        if content_length >= max_chars:
            break

    if not consumed:
        return None, True
    content = "\n\n".join(blocks)
    snapshot = SourceSnapshot(
        source_type="session",
        source_key=session_id,
        content=content,
        content_hash=_hash_text(content),
        line_count=len(content.splitlines()),
        last_message_id=consumed[-1].info.id,
    )
    return snapshot, len(consumed) < len(pending)


def _recent_daily_paths(memory_root: Path, limit: int) -> list[Path]:
    if limit <= 0:
        return []
    return sorted((memory_root / "daily").glob("*.md"), reverse=True)[:limit]


def _daily_delta(
    path: Path,
    checkpoint: Optional[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[Optional[SourceSnapshot], bool]:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    current_hash = _hash_text(content)
    current_count = len(lines)
    start_line = 0
    if checkpoint:
        old_count = int(checkpoint.get("line_count") or 0)
        old_hash = str(checkpoint.get("content_hash") or "")
        if old_count == current_count and old_hash == current_hash:
            return None, False
        if old_count <= current_count:
            prefix = "".join(lines[:old_count])
            if _hash_text(prefix) == old_hash:
                start_line = old_count

    consumed_lines: list[str] = []
    length = 0
    for line in lines[start_line:]:
        if consumed_lines and length + len(line) > max_chars:
            break
        consumed_lines.append(_truncate_middle(line, max(max_chars - length, 1)))
        length += len(consumed_lines[-1])
        if length >= max_chars:
            break

    consumed_count = start_line + len(consumed_lines)
    cursor_content = "".join(lines[:consumed_count])
    delta_content = "".join(consumed_lines)
    snapshot = SourceSnapshot(
        source_type="daily",
        source_key=path.stem,
        content=delta_content,
        content_hash=_hash_text(cursor_content),
        line_count=consumed_count,
        source_mtime=path.stat().st_mtime,
    )
    return snapshot, consumed_count < current_count


def _contains_memory_entry(current: str, addition: str) -> bool:
    if "\n" not in addition:
        return any(line.strip() == addition for line in current.splitlines())
    blocks = {block.strip() for block in re.split(r"\n\s*\n", current) if block.strip()}
    return addition.strip() in blocks


async def _collect_dream_sources(
    config: MemoryConfig,
) -> tuple[list[SourceSnapshot], bool, list[tuple[str, str]]]:
    """Collect one bounded bridge batch and its MemoryManager sync targets."""
    from flocks.session.session import Session

    learning = config.learning
    sessions = await Session.list_all_unfiltered()
    eligible_sessions = [session for session in sessions if session.category == "user" and session.status != "deleted"]
    session_budget = max(learning.max_input_chars // 3, 1000)
    daily_budget = max(learning.max_input_chars // 3, 1000)
    sources: list[SourceSnapshot] = []
    sync_targets = [(session.project_id, session.directory) for session in eligible_sessions]
    backlog = False
    changed_sessions = 0

    for session in eligible_sessions:
        if learning.catch_up_sessions > 0 and changed_sessions >= learning.catch_up_sessions:
            backlog = True
            break
        if session_budget <= 0:
            backlog = True
            break
        checkpoint = await LearningCheckpointStore.get(
            "dream",
            "session",
            session.id,
        )
        snapshot, source_backlog = await _session_delta(
            session.id,
            checkpoint,
            max_messages=learning.max_session_messages,
            max_chars=session_budget,
        )
        if snapshot is None:
            continue
        sources.append(snapshot)
        changed_sessions += 1
        session_budget -= len(snapshot.content)
        backlog = backlog or source_backlog

    memory_root = Config.get_data_path() / "memory"
    for path in _recent_daily_paths(
        memory_root,
        learning.dream.recent_daily_days,
    ):
        if daily_budget <= 0:
            backlog = True
            break
        checkpoint = await LearningCheckpointStore.get(
            "dream",
            "daily",
            path.stem,
        )
        snapshot, source_backlog = _daily_delta(
            path,
            checkpoint,
            max_chars=daily_budget,
        )
        if snapshot is None:
            continue
        sources.append(snapshot)
        daily_budget -= len(snapshot.content)
        backlog = backlog or source_backlog

    return sources, backlog, sync_targets


def _apply_memory_operations(
    current: str,
    response: dict[str, Any],
    *,
    target: Literal["memory", "user"] = "memory",
) -> str:
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
        operation_target = operation.get("target", "memory")
        if operation_target not in {"memory", "user"}:
            raise ValueError(f"unsupported Dream target: {operation_target}")
        if operation_target != target:
            continue
        operation_type = operation.get("type")
        if operation_type == "add":
            addition = str(operation.get("content") or "").strip()
            if not addition:
                raise ValueError("Dream add requires content")
            if _contains_memory_entry(result, addition):
                continue
            result = result.rstrip() + ("\n\n" if result.strip() else "")
            result += addition + "\n"
            continue
        old = str(operation.get("old") or "")
        if not old or result.count(old) != 1:
            raise ValueError("Dream replace/remove target must match exactly once")
        if operation_type == "replace":
            result = result.replace(old, str(operation.get("new") or ""), 1)
        elif operation_type == "remove":
            result = result.replace(old, "", 1)
        else:
            raise ValueError(f"unsupported Dream operation: {operation_type}")
    return result


async def _sync_memory_indexes(
    config: MemoryConfig,
    sync_targets: list[tuple[str, str]],
) -> None:
    targets_by_project: dict[str, str] = {}
    for project_id, workspace in sync_targets:
        targets_by_project.setdefault(project_id, workspace)
    targets = list(targets_by_project.items())
    if not targets:
        targets = [("default", ".")]
    for project_id, workspace in targets:
        manager = MemoryManager.get_instance(
            project_id=project_id,
            workspace_dir=workspace,
            config=config,
        )
        await manager.sync(reason="dream")


async def _restore_memory_files_and_indexes(
    *,
    config: MemoryConfig,
    sync_targets: list[tuple[str, str]],
    memory_path: Path,
    user_path: Path,
    memory_existed: bool,
    user_existed: bool,
    current_memory: str,
    current_user: str,
) -> None:
    if memory_existed:
        _atomic_write(memory_path, current_memory)
    else:
        memory_path.unlink(missing_ok=True)
    if user_existed:
        _atomic_write(user_path, current_user)
    else:
        user_path.unlink(missing_ok=True)
    try:
        await _sync_memory_indexes(config, sync_targets)
    except Exception as restore_exc:
        log.warn("dream.restore_reindex_failed", {"error": str(restore_exc)})


async def run_dream_bridge() -> DreamBridgeResult:
    """Run one incremental Dream batch using the configured default model."""
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig):
        return DreamBridgeResult(False, 0, False)
    if not config.enabled or not config.learning.enabled:
        return DreamBridgeResult(False, 0, False)
    if not config.learning.dream.enabled:
        return DreamBridgeResult(False, 0, False)

    default_model = await Config.resolve_default_llm()
    provider_id = default_model.get("provider_id") if default_model else None
    model_id = default_model.get("model_id") if default_model else None
    if not provider_id or not model_id:
        raise RuntimeError("no default model is configured for Dream")

    async with _PIPELINE_LOCKS["dream"]:
        sources, backlog, sync_targets = await _collect_dream_sources(config)
        if not sources:
            return DreamBridgeResult(False, 0, backlog)

        source_sections = [
            (f"## {source.source_type}/{source.source_key}\n{source.content}")
            for source in sources
            if source.content.strip()
        ]
        if not source_sections:
            await LearningCheckpointStore.commit("dream", sources)
            return DreamBridgeResult(False, len(sources), backlog)

        memory_root = Config.get_data_path() / "memory"
        memory_path = memory_root / "MEMORY.md"
        user_path = memory_root / "USER.md"
        memory_existed = memory_path.exists()
        user_existed = user_path.exists()
        current_memory = memory_path.read_text(encoding="utf-8") if memory_existed else ""
        if user_existed:
            current_user = user_path.read_text(encoding="utf-8")
        else:
            from flocks.memory.bootstrap import INITIAL_USER_PROFILE

            current_user = INITIAL_USER_PROFILE

        source_text = "\n\n".join(source_sections)
        response = await _chat_json(
            provider_id=provider_id,
            model_id=model_id,
            max_tokens=config.learning.max_output_tokens,
            system=(
                "Maintain two durable Markdown stores from incremental evidence. "
                "USER.md contains stable user identity, preferences, communication "
                "style, working habits, and technical level. MEMORY.md contains "
                "environment facts, project conventions, decisions, and reusable "
                "lessons. Reject transient task details and session summaries. "
                'Return strict JSON only: {"action":"skip","reason":"..."} '
                'or {"action":"update","operations":['
                '{"target":"user|memory","type":"add","content":"..."},'
                '{"target":"user|memory","type":"replace",'
                '"old":"exact text","new":"..."},'
                '{"target":"user|memory","type":"remove",'
                '"old":"exact text"}]}. Copy replace/remove targets verbatim.'
            ),
            user=(
                "Incremental Session and Daily evidence:\n"
                f"{_truncate_tail(source_text, config.learning.max_input_chars // 2)}"
                "\n\nCurrent USER.md:\n"
                f"{_truncate_tail(current_user, config.learning.max_input_chars // 4)}"
                "\n\nCurrent MEMORY.md:\n"
                f"{_truncate_tail(current_memory, config.learning.max_input_chars // 4)}"
            ),
        )
        updated_memory = _apply_memory_operations(
            current_memory,
            response,
            target="memory",
        )
        updated_user = _apply_memory_operations(
            current_user,
            response,
            target="user",
        )
        memory_changed = updated_memory != current_memory
        user_changed = updated_user != current_user
        files_changed = memory_changed or user_changed

        if files_changed:
            try:
                if memory_changed:
                    _atomic_write(memory_path, updated_memory)
                if user_changed or not user_existed:
                    _atomic_write(user_path, updated_user)
                await _sync_memory_indexes(config, sync_targets)
                await LearningCheckpointStore.commit("dream", sources)
            except BaseException:
                await _restore_memory_files_and_indexes(
                    config=config,
                    sync_targets=sync_targets,
                    memory_path=memory_path,
                    user_path=user_path,
                    memory_existed=memory_existed,
                    user_existed=user_existed,
                    current_memory=current_memory,
                    current_user=current_user,
                )
                raise
        else:
            await LearningCheckpointStore.commit("dream", sources)

        return DreamBridgeResult(files_changed, len(sources), backlog)


def _redact_sensitive(value: Any, *, key: Optional[str] = None) -> Any:
    if key and _SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_sensitive(item_value, key=str(item_key)) for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.groups == 1:
            redacted = pattern.sub("[REDACTED]", redacted)
        elif pattern.groups == 2:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


def _is_user_correction(text: str) -> bool:
    return bool(text and _CORRECTION_RE.search(text))


def _is_real_tool_part(part: ToolPart) -> bool:
    metadata = part.metadata or {}
    return not bool(metadata.get("ignored") or metadata.get("synthetic"))


async def _build_turn_review(
    *,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    config: MemoryConfig,
) -> Optional[TurnReview]:
    messages = await Message.list_with_parts(session_id, include_archived=True)
    positions = {message.info.id: index for index, message in enumerate(messages)}
    user_index = positions.get(user_message_id)
    assistant_index = positions.get(assistant_message_id)
    if user_index is None or assistant_index is None or user_index > assistant_index:
        return None

    assistant = messages[assistant_index]
    if (
        _message_role(assistant) != "assistant"
        or getattr(assistant.info, "finish", None) != "stop"
        or getattr(assistant.info, "error", None)
    ):
        return None

    turn_messages = messages[user_index : assistant_index + 1]
    tool_parts: list[ToolPart] = []
    for message in turn_messages:
        tool_parts.extend(part for part in message.parts if isinstance(part, ToolPart) and _is_real_tool_part(part))

    completed_count = sum(part.state.status == "completed" for part in tool_parts)
    seen_error = False
    recovered_after_error = False
    for part in tool_parts:
        if part.state.status == "error":
            seen_error = True
        elif seen_error and part.state.status == "completed":
            recovered_after_error = True

    user_text = _real_text(messages[user_index])
    trigger_reasons: list[str] = []
    if completed_count >= config.learning.skill.min_completed_tools:
        trigger_reasons.append("completed_tool_threshold")
    if recovered_after_error:
        trigger_reasons.append("failure_then_success")
    if _is_user_correction(user_text):
        trigger_reasons.append("user_correction")
    if not trigger_reasons:
        return None

    context_messages = messages[: assistant_index + 1][-config.learning.max_session_messages :]
    context_blocks: list[str] = []
    for message in context_messages:
        role = _message_role(message)
        if role not in {"user", "assistant"}:
            continue
        text = _real_text(message)
        if text:
            context_blocks.append(f"{role}: {text}")
    context_budget = max(config.learning.max_input_chars // 6, 1000)
    context = _truncate_tail("\n\n".join(context_blocks), context_budget)

    trace_budget = max(config.learning.max_input_chars // 2, 1000)
    per_tool_budget = max(
        trace_budget // max(len(tool_parts), 1),
        _TOOL_PAYLOAD_MIN_CHARS,
    )
    trace_blocks: list[str] = []
    for index, part in enumerate(tool_parts, start=1):
        state = part.state
        payload = {
            "index": index,
            "tool": part.tool,
            "status": state.status,
            "input": _redact_sensitive(getattr(state, "input", None)),
            "output": _redact_sensitive(getattr(state, "output", None)),
            "error": _redact_sensitive(getattr(state, "error", None)),
        }
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        trace_blocks.append(_truncate_middle(serialized, per_tool_budget))
    trace = "\n".join(trace_blocks) or "(no tool calls)"
    content = f"## Recent Session context\n{context}\n\n## Current Turn tool trace\n{trace}"
    source = SourceSnapshot(
        source_type="session",
        source_key=session_id,
        content=content,
        content_hash=_hash_text(content),
        line_count=len(content.splitlines()),
        last_message_id=assistant_message_id,
    )
    return TurnReview(
        source=source,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        trigger_reasons=tuple(trigger_reasons),
        content=content,
    )


def _skill_catalog() -> list[dict[str, str]]:
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "location": skill.location,
            "source": str(skill.source or ""),
        }
        for skill in Skill._all_sync()
    ]


def _related_skill_contents(
    names: list[str],
    catalog: list[dict[str, str]],
    limit: int,
) -> dict[str, dict[str, str]]:
    entries = {item["name"]: item for item in catalog}
    result: dict[str, dict[str, str]] = {}
    for name in names[:limit]:
        item = entries.get(name)
        if not item:
            continue
        result[name] = {
            **item,
            "content": Path(item["location"]).read_text(encoding="utf-8"),
        }
    return result


def _validate_skill_document(content: str, expected_name: str) -> None:
    if len(content.splitlines()) > _MAX_SKILL_LINES:
        raise ValueError("SKILL.md exceeds the 500 line limit")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md requires YAML frontmatter")
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise ValueError("SKILL.md YAML frontmatter is not closed")
    try:
        import yaml  # type: ignore[import-untyped]

        metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
    except Exception as exc:
        raise ValueError("SKILL.md contains invalid YAML frontmatter") from exc
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md YAML frontmatter must be an object")
    if metadata.get("name") != expected_name:
        raise ValueError("SKILL.md frontmatter name does not match target skill")
    if not Skill._is_valid_name(expected_name):
        raise ValueError("invalid skill name")
    description = str(metadata.get("description") or "")
    if not Skill._is_valid_description(description):
        raise ValueError("invalid skill description")
    if len(description.strip()) < 20 or not _DESCRIPTION_TRIGGER_RE.search(description):
        raise ValueError("skill description must explain what it does and when to use it")


def _safe_skill_path(root: Path, skill_name: str) -> Path:
    if not _SKILL_NAME_RE.fullmatch(skill_name):
        raise ValueError("invalid skill name")
    if root.is_symlink():
        raise ValueError("user skill root cannot be a symbolic link")
    resolved_root = root.resolve()
    skill_dir = root / skill_name
    if skill_dir.is_symlink():
        raise ValueError("skill directory cannot be a symbolic link")
    resolved_skill_dir = skill_dir.resolve()
    raw_target = skill_dir / "SKILL.md"
    if raw_target.is_symlink():
        raise ValueError("SKILL.md cannot be a symbolic link")
    target = raw_target.resolve()
    if resolved_root not in resolved_skill_dir.parents:
        raise ValueError("skill directory escaped the configured user skill root")
    if resolved_skill_dir not in target.parents:
        raise ValueError("skill path escaped the configured user skill root")
    return target


def _prepare_skill_proposal(
    *,
    response: dict[str, Any],
    review: TurnReview,
    catalog: list[dict[str, str]],
    related: dict[str, dict[str, str]],
    skill_root: Path,
) -> Optional[SkillProposal]:
    action = response.get("action")
    if action == "skip":
        return None
    if action not in {"create", "patch", "edit"}:
        raise ValueError("skill proposal action must be create, patch, edit, or skip")
    skill_name = str(response.get("skill_name") or "")
    target = _safe_skill_path(skill_root, skill_name)
    catalog_entry = next(
        (item for item in catalog if item["name"] == skill_name),
        None,
    )

    base_hash: Optional[str]
    if action == "create":
        if catalog_entry is not None or target.exists():
            raise ValueError("create cannot shadow an existing skill")
        base_hash = None
        proposed_content = str(response.get("content") or "")
    else:
        selected = related.get(skill_name)
        if selected is None:
            raise ValueError("skill edit target was not selected for review")
        if selected.get("source") != "user":
            raise ValueError("only user-managed skills can be changed")
        selected_path = Path(selected["location"]).resolve()
        if selected_path != target:
            raise ValueError("selected skill is outside the user skill root")
        current = target.read_text(encoding="utf-8")
        base_hash = _hash_text(current)
        if action == "edit":
            proposed_content = str(response.get("content") or "")
        else:
            path = str(response.get("path") or "SKILL.md")
            if path != "SKILL.md":
                raise ValueError("skill proposals may only patch SKILL.md")
            old = str(response.get("old") or "")
            if not old or current.count(old) != 1:
                raise ValueError("skill patch target must match exactly once")
            proposed_content = current.replace(
                old,
                str(response.get("new") or ""),
                1,
            )

    _validate_skill_document(proposed_content, skill_name)
    operation_json = json.dumps(response, ensure_ascii=False, sort_keys=True)
    return SkillProposal(
        id=f"skill-proposal-{uuid.uuid4().hex}",
        session_id=review.source.source_key,
        user_message_id=review.user_message_id,
        assistant_message_id=review.assistant_message_id,
        trigger_reasons=review.trigger_reasons,
        skill_name=skill_name,
        action=action,
        base_hash=base_hash,
        operation_json=operation_json,
        proposed_content=proposed_content,
        proposed_hash=_hash_text(proposed_content),
        created_at=_now_iso(),
    )


def _proposal_source(proposal: SkillProposal) -> SourceSnapshot:
    return SourceSnapshot(
        source_type="session",
        source_key=proposal.session_id,
        content=proposal.proposed_content,
        content_hash=proposal.proposed_hash,
        line_count=len(proposal.proposed_content.splitlines()),
        last_message_id=proposal.assistant_message_id,
    )


def _invalidate_skill_caches() -> None:
    Skill.clear_cache()
    try:
        from flocks.agent.registry import Agent

        Agent.invalidate_cache()
    except Exception as exc:
        log.warn("skill_proposal.agent_cache_failed", {"error": str(exc)})


async def _apply_pending_proposal(
    proposal: SkillProposal,
    *,
    skill_root: Path,
) -> bool:
    target = _safe_skill_path(skill_root, proposal.skill_name)
    current = target.read_text(encoding="utf-8") if target.exists() else None
    current_hash = _hash_text(current) if current is not None else None
    source = _proposal_source(proposal)

    if current_hash == proposal.proposed_hash:
        _invalidate_skill_caches()
        await SkillProposalStore.finish(
            proposal,
            source,
            status="applied",
        )
        return True

    base_matches = (
        proposal.action == "create"
        and current is None
        or proposal.action != "create"
        and current_hash == proposal.base_hash
    )
    if not base_matches:
        await SkillProposalStore.finish(
            proposal,
            source,
            status="conflict",
            error="target changed after proposal creation",
        )
        return False

    try:
        _atomic_write(target, proposal.proposed_content)
        _invalidate_skill_caches()
        await SkillProposalStore.finish(
            proposal,
            source,
            status="applied",
        )
    except BaseException as exc:
        await SkillProposalStore.record_pending_error(
            proposal.id,
            str(exc),
        )
        raise
    return True


async def recover_pending_skill_proposals(
    *,
    skill_root: Optional[Path] = None,
) -> int:
    """Idempotently finish proposals interrupted before their DB finalization."""
    root = skill_root or _user_skill_root()
    recovered = 0
    for proposal in await SkillProposalStore.list_pending():
        try:
            if await _apply_pending_proposal(proposal, skill_root=root):
                recovered += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "skill_proposal.recovery_failed",
                {"proposal_id": proposal.id, "error": str(exc)},
            )
    return recovered


async def process_skill_turn(
    *,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    provider_id: str,
    model_id: str,
    skill_root: Optional[Path] = None,
) -> bool:
    """Review one successful turn and apply a validated skill proposal."""
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig):
        return False
    if not config.enabled or not config.learning.enabled or not config.learning.skill.enabled:
        return False

    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if session is None or session.category != "user" or session.status == "deleted":
        return False

    async with _PIPELINE_LOCKS["skill"]:
        checkpoint = await LearningCheckpointStore.get(
            "skill",
            "session",
            session_id,
        )
        last_reviewed = checkpoint.get("last_message_id") if checkpoint else None
        if last_reviewed and last_reviewed >= assistant_message_id:
            return False

        existing = await SkillProposalStore.get_by_assistant_message(assistant_message_id)
        root = skill_root or _user_skill_root()
        if existing is not None:
            if existing.status == "pending":
                return await _apply_pending_proposal(existing, skill_root=root)
            return existing.status == "applied"

        review = await _build_turn_review(
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            config=config,
        )
        if review is None:
            return False

        catalog = _skill_catalog()
        selection = await _chat_json(
            provider_id=provider_id,
            model_id=model_id,
            max_tokens=1000,
            system=(
                "Review a successful tool turn for a reusable workflow. "
                "Prefer improving an existing skill over creating a duplicate. "
                'Return strict JSON only: {"action":"skip","reason":"..."} '
                'or {"action":"evolve","skill_names":["existing-name"],'
                '"reason":"..."}. Select at most the few skills whose full '
                "contents are needed. A new workflow may use an empty list."
            ),
            user=(
                f"Trigger evidence: {', '.join(review.trigger_reasons)}\n\n"
                f"{review.content}"
                "\n\nAvailable skills:\n"
                f"{_truncate_tail(json.dumps(catalog, ensure_ascii=False), config.learning.max_input_chars // 3)}"
            ),
        )
        if selection.get("action") == "skip":
            await LearningCheckpointStore.commit("skill", [review.source])
            return False
        if selection.get("action") != "evolve":
            raise ValueError("skill review action must be skip or evolve")
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
                "Create one validated skill proposal from proven behavior. "
                "Return strict JSON only. Actions: skip; "
                "create(skill_name, content=complete SKILL.md); "
                "edit(skill_name, content=complete SKILL.md); "
                "patch(skill_name, path='SKILL.md', old, new), where old "
                "matches exactly once. The SKILL.md frontmatter must contain "
                "the matching name and a description that states both what "
                "the skill does and when it should trigger. Keep the document "
                "under 500 lines, generalize the workflow, explain why steps "
                "matter, and do not encode secrets or transient facts. Only "
                "selected source='user' skills may be edited; never shadow "
                "project, bundled, or installed skills."
            ),
            user=(
                f"{review.content}"
                "\n\nSelected existing skills:\n"
                f"{_truncate_tail(json.dumps(related, ensure_ascii=False), config.learning.max_input_chars // 3)}"
            ),
        )
        proposal = _prepare_skill_proposal(
            response=response,
            review=review,
            catalog=catalog,
            related=related,
            skill_root=root,
        )
        if proposal is None:
            await LearningCheckpointStore.commit("skill", [review.source])
            return False
        stored = await SkillProposalStore.create_pending(proposal)
        return await _apply_pending_proposal(stored, skill_root=root)
