"""Shared persistence, source collection, and trigger helpers for evolution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Optional

from flocks.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.memory.paths import (
    GLOBAL_SCOPE_ID,
    is_registered_project_id,
)
from flocks.memory.types import MemoryScope
from flocks.session.message import (
    Message,
    TextPart,
    ToolPart,
)
from flocks.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="memory.evolution")

_DREAM_MAX_SESSION_MESSAGES = 100
_DREAM_MAX_INPUT_CHARS = 60_000
_DREAM_CATCH_UP_SESSIONS = 20
Pipeline = Literal["dream"]
SourceType = Literal["session", "daily"]
_DREAM_LOCK = asyncio.Lock()
_TOOL_PAYLOAD_MIN_CHARS = 256

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

DROP INDEX IF EXISTS idx_memory_skill_proposals_status;
DROP TABLE IF EXISTS memory_skill_proposals;
DROP TABLE IF EXISTS memory_skill_evolution_state;
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
    memory_changed: bool = False
    skill_changed: bool = False
    changed_memory_files: tuple[str, ...] = ()
    changed_skills: tuple[str, ...] = ()


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


class EvolutionCheckpointStore:
    """SQLite source cursors for incremental Dream processing."""

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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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


def _tool_evidence(message: Any, *, per_tool_chars: int) -> list[str]:
    """Serialize bounded, redacted tool evidence for Skill decisions."""
    blocks: list[str] = []
    for part in message.parts:
        if not isinstance(part, ToolPart) or not _is_real_tool_part(part):
            continue
        state = part.state
        payload = {
            "tool": part.tool,
            "status": state.status,
            "input": _redact_sensitive(getattr(state, "input", None)),
            "output": _redact_sensitive(getattr(state, "output", None)),
            "error": _redact_sensitive(getattr(state, "error", None)),
        }
        blocks.append(
            _truncate_middle(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    default=str,
                ),
                per_tool_chars,
            )
        )
    return blocks


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
    per_tool_chars = max(
        max_chars // max(max_messages * 2, 1),
        _TOOL_PAYLOAD_MIN_CHARS,
    )
    for message in pending:
        if len(consumed) >= max_messages:
            break
        role = _message_role(message)
        text = _real_text(message) if role in {"user", "assistant"} else ""
        parts = [f"{role}: {text}"] if text else []
        if role == "assistant":
            parts.extend(
                f"tool: {tool_text}"
                for tool_text in _tool_evidence(
                    message,
                    per_tool_chars=per_tool_chars,
                )
            )
        block = "\n".join(parts)
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

    sessions = await Session.list_all_unfiltered()
    all_eligible_sessions = [
        session for session in sessions if session.category == "user" and session.status != "deleted"
    ]
    eligible_sessions = [session for session in all_eligible_sessions if session.project_id == target.project_id]
    eligible_session_ids = {session.id for session in eligible_sessions}
    session_prefixes = _unique_session_prefixes(all_eligible_sessions)
    if max_chars is None:
        total_source_budget = max(
            (_DREAM_MAX_INPUT_CHARS * 2) // 3,
            2000,
        )
    else:
        total_source_budget = max(int(max_chars), 2)
    remaining_budget = total_source_budget
    sources: list[SourceSnapshot] = []
    sync_targets = [(session.project_id, session.directory) for session in eligible_sessions]
    backlog = False
    changed_sessions = 0
    included_session_ids: set[str] = set()

    for session in eligible_sessions:
        if changed_sessions >= _DREAM_CATCH_UP_SESSIONS:
            backlog = True
            break
        if remaining_budget <= 0:
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
            max_messages=_DREAM_MAX_SESSION_MESSAGES,
            max_chars=remaining_budget,
            scope=target.scope,
            scope_id=target.scope_id,
        )
        if snapshot is None:
            continue
        sources.append(snapshot)
        changed_sessions += 1
        if snapshot.content.strip():
            included_session_ids.add(session.id)
        remaining_budget -= len(snapshot.content)
        backlog = backlog or source_backlog

    memory_root = Config.get_data_path() / "memory"
    for path in _recent_daily_paths(
        memory_root,
        config.dream.recent_daily_days,
    ):
        if remaining_budget <= 0:
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
            max_chars=remaining_budget,
            scope=target.scope,
            scope_id=target.scope_id,
            allowed_session_ids=eligible_session_ids - included_session_ids,
            session_prefixes=session_prefixes,
        )
        if snapshot is None:
            continue
        sources.append(snapshot)
        remaining_budget -= len(snapshot.content)
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


def _is_real_tool_part(part: ToolPart) -> bool:
    metadata = part.metadata or {}
    return not bool(metadata.get("ignored") or metadata.get("synthetic"))
