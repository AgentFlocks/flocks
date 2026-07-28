"""Shared persistence, models, and helpers for Memory evolution.

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
from flocks.memory.paths import (
    GLOBAL_SCOPE_ID,
    is_registered_project_id,
)
from flocks.memory.types import MemoryScope
from flocks.provider import ChatMessage, Provider
from flocks.provider.options import build_provider_options
from flocks.session.message import Message, TextPart, ToolPart
from flocks.skill.skill import Skill
from flocks.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="memory.evolution")
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
_DAILY_SESSION_HEADER_RE = re.compile(r"^## Session (?P<prefix>[A-Za-z0-9_-]+)(?:…|\.\.\.)?")

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS memory_evolution_checkpoints (
    pipeline TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    line_count INTEGER NOT NULL DEFAULT 0,
    last_message_id TEXT,
    source_mtime REAL,
    processed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline, scope, scope_id, source_type, source_key)
);
CREATE INDEX IF NOT EXISTS idx_memory_evolution_checkpoint_updated
ON memory_evolution_checkpoints(pipeline, scope, scope_id, updated_at);

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
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = GLOBAL_SCOPE_ID
    last_message_id: Optional[str] = None
    source_mtime: Optional[float] = None


@dataclass(frozen=True)
class DreamBridgeResult:
    """Result of one bounded Dream bridge batch."""

    changed: bool
    processed_sources: int
    backlog: bool


@dataclass(frozen=True)
class DreamTarget:
    """One independently scheduled Global-only or Project Dream."""

    scope: MemoryScope
    scope_id: str

    @classmethod
    def global_only(cls) -> "DreamTarget":
        return cls(MemoryScope.GLOBAL, GLOBAL_SCOPE_ID)

    @classmethod
    def project(cls, project_id: str) -> "DreamTarget":
        if not is_registered_project_id(project_id):
            raise ValueError(f"Invalid registered project id: {project_id}")
        return cls(MemoryScope.PROJECT, project_id)

    @property
    def project_id(self) -> str:
        return self.scope_id if self.scope == MemoryScope.PROJECT else "default"

    @property
    def scheduler_key(self) -> str:
        return f"{self.scope.value}:{self.scope_id}"


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


class EvolutionCheckpointStore:
    """SQLite cursors shared by Dream and skill review."""

    _schema_lock = asyncio.Lock()

    @classmethod
    async def ensure_schema(cls) -> None:
        await Storage._ensure_init()
        async with cls._schema_lock:
            async with Storage.connect() as db:
                await db.executescript(_SCHEMA_DDL)
                await db.commit()

    @classmethod
    async def get(
        cls,
        pipeline: Pipeline,
        source_type: SourceType,
        source_key: str,
        *,
        scope: MemoryScope = MemoryScope.GLOBAL,
        scope_id: str = GLOBAL_SCOPE_ID,
    ) -> Optional[dict[str, Any]]:
        await cls.ensure_schema()
        async with Storage.connect() as db:
            cursor = await db.execute(
                """
                SELECT content_hash, line_count, last_message_id, source_mtime,
                       processed_at, updated_at
                FROM memory_evolution_checkpoints
                WHERE pipeline = ? AND scope = ? AND scope_id = ?
                    AND source_type = ? AND source_key = ?
                """,
                (
                    pipeline,
                    scope.value,
                    scope_id,
                    source_type,
                    source_key,
                ),
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
        row = await cls.get(
            pipeline,
            source.source_type,
            source.source_key,
            scope=source.scope,
            scope_id=source.scope_id,
        )
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
            INSERT INTO memory_evolution_checkpoints (
                pipeline, scope, scope_id, source_type, source_key, content_hash,
                line_count, last_message_id, source_mtime,
                processed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                pipeline, scope, scope_id, source_type, source_key
            ) DO UPDATE SET
                content_hash = excluded.content_hash,
                line_count = excluded.line_count,
                last_message_id = excluded.last_message_id,
                source_mtime = excluded.source_mtime,
                processed_at = excluded.processed_at,
                updated_at = excluded.updated_at
            """,
            (
                pipeline,
                source.scope.value,
                source.scope_id,
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
        await EvolutionCheckpointStore.ensure_schema()
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
        await EvolutionCheckpointStore.ensure_schema()
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
        await EvolutionCheckpointStore.ensure_schema()
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
        await EvolutionCheckpointStore.ensure_schema()
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
                await EvolutionCheckpointStore._upsert_in_transaction(
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
        await EvolutionCheckpointStore.ensure_schema()
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
    marker = "\n...[truncated for evolution context]...\n"
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
        raise ValueError("evolution response must be a JSON object")
    return value


async def _chat_json(
    *,
    provider_id: str,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any]:
    return _parse_json_object(
        await _chat_text(
            provider_id=provider_id,
            model_id=model_id,
            system=system,
            user=user,
            max_tokens=max_tokens,
        )
    )


async def _chat_text(
    *,
    provider_id: str,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    """Run one evolution model call and return its plain-text response."""
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
    if response.finish_reason in {"length", "max_tokens"}:
        raise ValueError("evolution response was truncated")
    return response.content


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
    scope: MemoryScope = MemoryScope.GLOBAL,
    scope_id: str = GLOBAL_SCOPE_ID,
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
        scope=scope,
        scope_id=scope_id,
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
    scope: MemoryScope = MemoryScope.GLOBAL,
    scope_id: str = GLOBAL_SCOPE_ID,
    allowed_session_ids: Optional[set[str]] = None,
    session_prefixes: Optional[dict[str, Optional[str]]] = None,
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
    if allowed_session_ids is None or session_prefixes is None:
        delta_content = "".join(consumed_lines)
    else:
        filtered_lines: list[str] = []
        current_session_id: Optional[str] = None
        for index, line in enumerate(lines[:consumed_count]):
            match = _DAILY_SESSION_HEADER_RE.match(line.strip())
            if match:
                current_session_id = session_prefixes.get(match.group("prefix"))
            if index >= start_line and current_session_id in allowed_session_ids:
                filtered_lines.append(line)
        delta_content = _truncate_middle(
            "".join(filtered_lines),
            max_chars,
        )
    snapshot = SourceSnapshot(
        source_type="daily",
        source_key=path.stem,
        content=delta_content,
        content_hash=_hash_text(cursor_content),
        line_count=consumed_count,
        scope=scope,
        scope_id=scope_id,
        source_mtime=path.stat().st_mtime,
    )
    return snapshot, consumed_count < current_count


async def list_dream_targets() -> list[DreamTarget]:
    """List deterministic Dream targets backed by non-deleted user Sessions."""
    from flocks.session.session import Session

    sessions = await Session.list_all_unfiltered()
    project_ids = {
        session.project_id for session in sessions if session.category == "user" and session.status != "deleted"
    }
    targets: list[DreamTarget] = []
    if "default" in project_ids:
        targets.append(DreamTarget.global_only())
    targets.extend(
        DreamTarget.project(project_id) for project_id in sorted(project_ids) if is_registered_project_id(project_id)
    )
    return targets


def _unique_session_prefixes(sessions: list[Any]) -> dict[str, Optional[str]]:
    """Map Daily's 16-character Session prefixes when they are unambiguous."""
    candidates: dict[str, list[str]] = {}
    for session in sessions:
        candidates.setdefault(session.id[:16], []).append(session.id)
    return {prefix: ids[0] if len(ids) == 1 else None for prefix, ids in candidates.items()}


async def _collect_dream_sources(
    config: MemoryConfig,
    target: DreamTarget,
    *,
    max_chars: Optional[int] = None,
) -> tuple[list[SourceSnapshot], bool, list[tuple[str, str]]]:
    """Collect one bounded bridge batch and its MemoryManager sync targets."""
    from flocks.session.session import Session

    evolution = config.evolution
    sessions = await Session.list_all_unfiltered()
    all_eligible_sessions = [
        session for session in sessions if session.category == "user" and session.status != "deleted"
    ]
    eligible_sessions = [session for session in all_eligible_sessions if session.project_id == target.project_id]
    eligible_session_ids = {session.id for session in eligible_sessions}
    session_prefixes = _unique_session_prefixes(all_eligible_sessions)
    if max_chars is None:
        total_source_budget = max(
            (evolution.max_input_chars * 2) // 3,
            2000,
        )
    else:
        total_source_budget = max(int(max_chars), 2)
    session_budget = total_source_budget // 2
    daily_budget = total_source_budget - session_budget
    sources: list[SourceSnapshot] = []
    sync_targets = [(session.project_id, session.directory) for session in eligible_sessions]
    backlog = False
    changed_sessions = 0

    for session in eligible_sessions:
        if evolution.catch_up_sessions > 0 and changed_sessions >= evolution.catch_up_sessions:
            backlog = True
            break
        if session_budget <= 0:
            backlog = True
            break
        checkpoint = await EvolutionCheckpointStore.get(
            "dream",
            "session",
            session.id,
            scope=target.scope,
            scope_id=target.scope_id,
        )
        snapshot, source_backlog = await _session_delta(
            session.id,
            checkpoint,
            max_messages=evolution.max_session_messages,
            max_chars=session_budget,
            scope=target.scope,
            scope_id=target.scope_id,
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
        evolution.dream.recent_daily_days,
    ):
        if daily_budget <= 0:
            backlog = True
            break
        checkpoint = await EvolutionCheckpointStore.get(
            "dream",
            "daily",
            path.stem,
            scope=target.scope,
            scope_id=target.scope_id,
        )
        snapshot, source_backlog = _daily_delta(
            path,
            checkpoint,
            max_chars=daily_budget,
            scope=target.scope,
            scope_id=target.scope_id,
            allowed_session_ids=eligible_session_ids,
            session_prefixes=session_prefixes,
        )
        if snapshot is None:
            continue
        sources.append(snapshot)
        daily_budget -= len(snapshot.content)
        backlog = backlog or source_backlog

    return sources, backlog, sync_targets


async def _sync_memory_indexes(
    config: MemoryConfig,
    sync_targets: list[tuple[str, str]],
    *,
    fallback_project_id: str,
) -> None:
    targets_by_project: dict[str, str] = {}
    for project_id, workspace in sync_targets:
        targets_by_project.setdefault(project_id, workspace)
    targets = list(targets_by_project.items())
    if not targets:
        targets = [(fallback_project_id, ".")]
    for project_id, workspace in targets:
        manager = MemoryManager.get_instance(
            project_id=project_id,
            workspace_dir=workspace,
            config=config,
        )
        await manager.sync(reason="dream")


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
    if completed_count >= config.evolution.skill.min_completed_tools:
        trigger_reasons.append("completed_tool_threshold")
    if recovered_after_error:
        trigger_reasons.append("failure_then_success")
    if _is_user_correction(user_text):
        trigger_reasons.append("user_correction")
    if not trigger_reasons:
        return None

    context_messages = messages[: assistant_index + 1][-config.evolution.max_session_messages :]
    context_blocks: list[str] = []
    for message in context_messages:
        role = _message_role(message)
        if role not in {"user", "assistant"}:
            continue
        text = _real_text(message)
        if text:
            context_blocks.append(f"{role}: {text}")
    context_budget = max(config.evolution.max_input_chars // 6, 1000)
    context = _truncate_tail("\n\n".join(context_blocks), context_budget)

    trace_budget = max(config.evolution.max_input_chars // 2, 1000)
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
