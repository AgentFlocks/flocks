"""
Session binding service.

Maps platform conversations to Flocks Sessions.

Binding key logic:
- DM:                (channel_id, account_id, sender_id, NULL)
- Group:             (channel_id, account_id, chat_id, NULL)
- Thread:            (channel_id, account_id, chat_id, thread_id)
- group_sender:      (channel_id, account_id, chat_id:sender_id, NULL)  — 群内每人独立 session
- group_topic:       (channel_id, account_id, chat_id, root_id)         — 按话题隔离（等效 Thread）

session_scope 参数由渠道层（如 feishu dispatcher）在调用前通过 ``scope_override`` 传入。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Literal, Optional

import aiosqlite

from flocks.channel.base import ChatType, InboundMessage
from flocks.utils.id import Identifier
from flocks.utils.log import Log

log = Log.create(service="channel.binding")

# Supported group session scope values (mirrors FeishuGroupConfig.group_session_scope)
GroupSessionScope = Literal["group", "group_sender", "group_topic", "group_topic_sender"]


@dataclass
class SessionBinding:
    channel_id: str
    account_id: str
    chat_id: str
    chat_type: ChatType
    thread_id: Optional[str]
    session_id: str
    agent_id: Optional[str]
    created_at: float
    last_message_at: float


_DDL = """
CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'direct',
    thread_id TEXT,
    session_id TEXT NOT NULL,
    agent_id TEXT,
    created_at REAL NOT NULL,
    last_message_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_bindings_unique
    ON channel_bindings(channel_id, account_id, chat_id, COALESCE(thread_id, ''));

CREATE INDEX IF NOT EXISTS idx_channel_bindings_session
    ON channel_bindings(session_id);
"""

_init_lock = asyncio.Lock()

# Persistent connection shared by all SessionBindingService instances.
_db_conn: Optional[aiosqlite.Connection] = None
_db_ready = False

# Register channel_bindings DDL with Storage so the tables are created
# during Storage.init() as well (idempotent CREATE IF NOT EXISTS).
try:
    from flocks.storage.storage import Storage
    Storage.register_ddl(_DDL)
except Exception:
    pass


async def _get_db() -> aiosqlite.Connection:
    """Return (and lazily create) the shared persistent database connection.

    Uses ``Storage.get_db_path()`` to ensure the same database file is
    shared with the rest of the Flocks storage subsystem.
    """
    global _db_conn, _db_ready
    if _db_conn is not None and _db_ready:
        return _db_conn

    async with _init_lock:
        if _db_conn is not None and _db_ready:
            return _db_conn

        from flocks.storage.storage import Storage
        db_path = Storage.get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _db_conn = await aiosqlite.connect(str(db_path))
        _db_conn.row_factory = aiosqlite.Row

        await _db_conn.execute("PRAGMA journal_mode=WAL")
        await _db_conn.execute("PRAGMA busy_timeout=5000")
        await _db_conn.executescript(_DDL)
        _db_ready = True
        return _db_conn


async def close_binding_db() -> None:
    """Close the persistent connection (call during shutdown)."""
    global _db_conn, _db_ready
    if _db_conn is not None:
        try:
            await _db_conn.close()
        except Exception:
            pass
        _db_conn = None
        _db_ready = False


class SessionBindingService:
    """Manages the platform-conversation ↔ Flocks-session mapping."""

    async def resolve_or_create(
        self,
        msg: InboundMessage,
        default_agent: Optional[str] = None,
        scope_override: Optional[GroupSessionScope] = None,
        directory: Optional[str] = None,
    ) -> SessionBinding:
        """Find or create a binding for *msg*.

        Args:
            msg: The inbound message to bind.
            default_agent: Agent ID to use when creating a new session.
            scope_override: Override the session isolation scope for group messages.
                - ``None`` / ``"group"`` (default): one session per group chat.
                - ``"group_sender"``: one session per (group, sender) pair.
                - ``"group_topic"``: one session per topic thread in the group
                  (falls back to per-group if msg.thread_id is absent).
            directory: Working directory used when a new session has to be
                created. When ``None``, falls back to the current Project
                Instance directory and finally ``os.getcwd()``. Aligning this
                with the WebUI session creation path is what keeps ``<env>``,
                ``AGENTS.md`` and sandbox prompts consistent across entry
                points.
        """
        chat_id, thread_id = _resolve_session_key(msg, scope_override)

        existing = await self._find_binding(
            msg.channel_id, msg.account_id, chat_id, thread_id,
        )
        if existing:
            # Verify the bound session still exists (user may have deleted it via WebUI)
            from flocks.session.session import Session as _Session
            still_alive = await _Session.get_by_id(existing.session_id)
            if still_alive:
                await self._touch(existing.session_id)
                return existing
            # Session was deleted — remove stale binding and fall through to create a new one
            log.info("channel.binding.stale", {
                "channel": msg.channel_id,
                "chat_id": chat_id,
                "old_session_id": existing.session_id,
            })
            await self.unbind(existing.session_id)

        session_id = await self._create_session(
            msg, default_agent=default_agent, directory=directory,
        )
        now = time.time()
        binding = SessionBinding(
            channel_id=msg.channel_id,
            account_id=msg.account_id,
            chat_id=chat_id,
            chat_type=msg.chat_type,
            thread_id=thread_id,
            session_id=session_id,
            agent_id=default_agent,
            created_at=now,
            last_message_at=now,
        )
        await self._insert(binding)
        log.info("channel.binding.created", {
            "channel": msg.channel_id,
            "chat_id": chat_id,
            "session_id": session_id,
            "scope": scope_override or "group",
        })
        return binding

    async def get_binding(
        self,
        channel_id: str,
        chat_id: str,
        thread_id: Optional[str] = None,
        account_id: str = "default",
    ) -> Optional[SessionBinding]:
        return await self._find_binding(channel_id, account_id, chat_id, thread_id)

    async def unbind(self, session_id: str) -> None:
        db = await _get_db()
        await db.execute(
            "DELETE FROM channel_bindings WHERE session_id = ?",
            (session_id,),
        )
        await db.commit()

    async def rebind(
        self,
        msg: InboundMessage,
        session_id: str,
        *,
        agent_id: Optional[str] = None,
        scope_override: Optional[GroupSessionScope] = None,
    ) -> SessionBinding:
        """Replace the conversation's binding with an existing session."""
        chat_id, thread_id = _resolve_session_key(msg, scope_override)
        existing = await self._find_binding(
            msg.channel_id,
            msg.account_id,
            chat_id,
            thread_id,
        )
        now = time.time()
        binding = SessionBinding(
            channel_id=msg.channel_id,
            account_id=msg.account_id,
            chat_id=chat_id,
            chat_type=msg.chat_type,
            thread_id=thread_id,
            session_id=session_id,
            agent_id=agent_id,
            created_at=existing.created_at if existing else now,
            last_message_at=now,
        )
        await self._insert(binding)
        return binding

    async def get_bindings_by_session(self, session_id: str) -> list[SessionBinding]:
        """Return all channel bindings for the given session_id."""
        db = await _get_db()
        cursor = await db.execute(
            "SELECT * FROM channel_bindings WHERE session_id = ? ORDER BY last_message_at DESC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_binding(r) for r in rows]

    async def list_bindings(
        self,
        channel_id: Optional[str] = None,
    ) -> list[SessionBinding]:
        db = await _get_db()
        sql = "SELECT * FROM channel_bindings"
        params: tuple = ()
        if channel_id:
            sql += " WHERE channel_id = ?"
            params = (channel_id,)
        sql += " ORDER BY last_message_at DESC"

        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_binding(r) for r in rows]

    # --- internal helpers ---

    async def _find_binding(
        self,
        channel_id: str,
        account_id: str,
        chat_id: str,
        thread_id: Optional[str],
    ) -> Optional[SessionBinding]:
        db = await _get_db()
        if thread_id is None:
            cursor = await db.execute(
                "SELECT * FROM channel_bindings "
                "WHERE channel_id = ? AND account_id = ? AND chat_id = ? "
                "AND COALESCE(thread_id, '') = ''",
                (channel_id, account_id, chat_id),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM channel_bindings "
                "WHERE channel_id = ? AND account_id = ? AND chat_id = ? "
                "AND thread_id = ?",
                (channel_id, account_id, chat_id, thread_id),
            )
        row = await cursor.fetchone()
        if row:
            return self._row_to_binding(row)
        return None

    async def _insert(self, b: SessionBinding) -> None:
        binding_id = Identifier.ascending("chbind")
        db = await _get_db()
        await db.execute(
            "INSERT OR REPLACE INTO channel_bindings "
            "(id, channel_id, account_id, chat_id, chat_type, thread_id, "
            " session_id, agent_id, created_at, last_message_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                binding_id,
                b.channel_id, b.account_id, b.chat_id,
                b.chat_type.value, b.thread_id,
                b.session_id, b.agent_id,
                b.created_at, b.last_message_at,
            ),
        )
        await db.commit()

    async def _touch(self, session_id: str) -> None:
        """Update last_message_at for the binding."""
        db = await _get_db()
        await db.execute(
            "UPDATE channel_bindings SET last_message_at = ? WHERE session_id = ?",
            (time.time(), session_id),
        )
        await db.commit()

    @staticmethod
    def _row_to_binding(row) -> SessionBinding:
        return SessionBinding(
            channel_id=row["channel_id"],
            account_id=row["account_id"],
            chat_id=row["chat_id"],
            chat_type=ChatType(row["chat_type"]),
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            created_at=row["created_at"],
            last_message_at=row["last_message_at"],
        )

    @staticmethod
    async def _create_session(
        msg: InboundMessage,
        default_agent: Optional[str] = None,
        directory: Optional[str] = None,
    ) -> str:
        """Create a new Flocks Session and return its ID.

        ``directory`` follows the same priority as the WebUI ``Session.create``
        route: explicit caller value → ``Instance.get_directory()`` → server
        ``os.getcwd()``. Keeping this aligned ensures channel-originated
        sessions inject the same ``<env>`` block, AGENTS.md custom rules and
        sandbox configuration as WebUI sessions.
        """
        from flocks.session.session import Session

        title = _build_title(msg)
        session = await Session.create(
            project_id="channel",
            directory=_resolve_session_directory(directory),
            title=title,
            agent=default_agent,
        )
        return session.id


def _resolve_session_directory(explicit: Optional[str]) -> str:
    """Resolve the working directory for a channel-created session.

    Priority:
        1. Explicit value passed by the dispatcher (typically from
           ``ChannelConfig.workspace_dir``).
        2. The active project Instance directory (same source the WebUI
           ``Session.create`` route uses).
        3. The server process ``os.getcwd()`` as a last resort.

    Keeping this aligned with the WebUI route is what unifies the
    ``<env>`` block, the AGENTS.md / CLAUDE.md / CONTEXT.md custom prompt
    injection and the sandbox prompt across both entry points.
    """
    if explicit:
        return explicit
    try:
        from flocks.project.instance import Instance
        instance_dir = Instance.get_directory()
        if instance_dir:
            return instance_dir
    except Exception:
        pass
    return os.getcwd()


def _build_title(msg: InboundMessage) -> str:
    prefix = msg.channel_id.capitalize()
    if msg.chat_type == ChatType.DIRECT:
        who = msg.sender_name or msg.sender_id
        return f"[{prefix}] DM — {who}"
    return f"[{prefix}] {msg.chat_id}"


def _resolve_session_key(
    msg: InboundMessage,
    scope_override: Optional[GroupSessionScope] = None,
) -> tuple[str, Optional[str]]:
    """Compute the (chat_id, thread_id) binding key for *msg* given *scope_override*.

    Scope semantics:
    - DM: always (sender_id, None) regardless of scope.
    - ``"group"`` (default): (chat_id, None)
    - ``"group_sender"``: (chat_id + ":" + sender_id, None)
      Each person in a group gets their own isolated session.
    - ``"group_topic"``: (chat_id, thread_id or None)
      Separate sessions per Feishu topic thread; falls back to per-group
      when there is no root_id (i.e. the message is not inside a topic).
    - ``"group_topic_sender"``: (chat_id + ":topic:" + thread_id, sender_id key)
      Each person in each topic thread gets their own session.
      Falls back to group_sender when there is no thread_id.
    """
    if msg.chat_type == ChatType.DIRECT:
        return msg.sender_id, None

    scope = scope_override or "group"

    if scope == "group_sender":
        return f"{msg.chat_id}:{msg.sender_id}", None

    if scope == "group_topic":
        # Use thread_id (Feishu root_id) as the isolation key when present
        return msg.chat_id, msg.thread_id or None

    if scope == "group_topic_sender":
        # Per-sender isolation within each topic thread
        if msg.thread_id:
            # Use composite chat_id so each topic+sender pair is unique
            return f"{msg.chat_id}:topic:{msg.thread_id}:{msg.sender_id}", None
        # Fallback: no thread — behave like group_sender
        return f"{msg.chat_id}:{msg.sender_id}", None

    # Default: one session per group chat
    return msg.chat_id, None
