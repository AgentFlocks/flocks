"""
Local account/authentication service.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiosqlite
from pydantic import BaseModel, Field

from flocks.auth.context import AuthUser
from flocks.storage.storage import Storage
from flocks.utils.id import Identifier
from flocks.utils.log import Log

log = Log.create(service="auth.service")


# Hours that an admin-issued one-time / reset password remains valid.
# Centralize here so CLI, HTTP routes and the service itself stay in sync.
TEMP_PASSWORD_TTL_HOURS: int = 24
# Days that a browser login session cookie stays valid.
SESSION_TTL_DAYS: int = 7


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class LocalUser(BaseModel):
    id: str
    username: str
    role: str
    status: str
    must_reset_password: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None

    def to_auth_user(self) -> AuthUser:
        return AuthUser(
            id=self.id,
            username=self.username,
            role=self.role,
            status=self.status,
            must_reset_password=self.must_reset_password,
        )


class AuthService:
    """Single-admin account and session service."""

    _initialized: bool = False
    _initialized_db_path: Optional[str] = None
    _session_ttl_days: int = SESSION_TTL_DAYS
    _temp_password_ttl_hours: int = TEMP_PASSWORD_TTL_HOURS
    # Once the system has any user, it can't transition back to the
    # "no users" state (there is no full-wipe flow). Cache the True result
    # so the hot path in apply_auth_for_request avoids hitting SQLite.
    _has_users_cached: bool = False

    @classmethod
    async def init(cls) -> None:
        await Storage.init()
        db_path = Storage.get_db_path()
        if cls._initialized and cls._initialized_db_path == str(db_path) and db_path.exists():
            return
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    status TEXT NOT NULL DEFAULT 'active',
                    must_reset_password INTEGER NOT NULL DEFAULT 0,
                    temp_password_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at);

                """
            )
            await cls._drop_legacy_tables(db)
            await db.commit()

        cls._initialized = True
        cls._initialized_db_path = str(db_path)
        log.info("auth.initialized")

    # Patterns matching tables from the removed cloud-account subsystem;
    # any table matching these patterns is dropped on first init so new
    # installs and upgrades converge on the same schema without having to
    # enumerate every historical table name.
    _LEGACY_TABLE_PATTERNS: Tuple[str, ...] = ("cloud\\_%",)

    @classmethod
    async def _drop_legacy_tables(cls, db: aiosqlite.Connection) -> None:
        for pattern in cls._LEGACY_TABLE_PATTERNS:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE ? ESCAPE '\\'",
                (pattern,),
            ) as cursor:
                rows = await cursor.fetchall()
            for (table_name,) in rows:
                await db.execute(f"DROP TABLE IF EXISTS {table_name}")
                log.info("auth.legacy_table.dropped", {"table": table_name})

    @classmethod
    def _hash_password(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        return "scrypt$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")

    @classmethod
    def _verify_password(cls, password: str, password_hash: str) -> bool:
        try:
            scheme, salt_b64, digest_b64 = password_hash.split("$", 2)
            if scheme != "scrypt":
                return False
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(digest_b64.encode("ascii"))
            actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    @classmethod
    async def has_users(cls) -> bool:
        if cls._has_users_cached:
            return True
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(1) FROM users") as cursor:
                row = await cursor.fetchone()
                result = bool(row and row[0] > 0)
        if result:
            cls._has_users_cached = True
        return result

    @classmethod
    async def get_bootstrap_status(cls) -> Dict[str, bool]:
        has_users = await cls.has_users()
        return {"bootstrapped": has_users}

    @classmethod
    async def bootstrap_admin(cls, username: str, password: str) -> LocalUser:
        await cls.init()
        if await cls.has_users():
            raise ValueError("账号体系已初始化")
        user = await cls._create_user_internal(
            username=username,
            password=password,
            role="admin",
            must_reset_password=False,
        )
        await cls.migrate_legacy_sessions_to_admin(user.id)
        return user

    @classmethod
    async def _create_user_internal(
        cls,
        username: str,
        password: str,
        role: str = "member",
        must_reset_password: bool = False,
        temp_expires_at: Optional[str] = None,
    ) -> LocalUser:
        await cls.init()
        if role not in {"admin", "member"}:
            raise ValueError("无效角色")
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("用户名不能为空")
        if len(password) < 8:
            raise ValueError("密码长度至少 8 位")

        user_id = Identifier.ascending("user")
        now = _iso_now()
        password_hash = cls._hash_password(password)
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, role, status, must_reset_password,
                    temp_password_expires_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_username,
                    password_hash,
                    role,
                    1 if must_reset_password else 0,
                    temp_expires_at,
                    now,
                    now,
                ),
            )
            await db.commit()
        cls._has_users_cached = True
        return await cls.get_user_by_id(user_id)  # type: ignore[return-value]

    @classmethod
    async def get_user_by_id(cls, user_id: str) -> Optional[LocalUser]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password,
                       created_at, updated_at, last_login_at
                FROM users WHERE id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return LocalUser(
            id=row[0],
            username=row[1],
            role=row[2],
            status=row[3],
            must_reset_password=bool(row[4]),
            created_at=row[5],
            updated_at=row[6],
            last_login_at=row[7],
        )

    @classmethod
    async def get_user_by_username(cls, username: str) -> Optional[Tuple[LocalUser, str, Optional[str]]]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password, created_at, updated_at, last_login_at,
                       password_hash, temp_password_expires_at
                FROM users WHERE username = ?
                """,
                (username.strip(),),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        user = LocalUser(
            id=row[0],
            username=row[1],
            role=row[2],
            status=row[3],
            must_reset_password=bool(row[4]),
            created_at=row[5],
            updated_at=row[6],
            last_login_at=row[7],
        )
        return user, row[8], row[9]

    @classmethod
    async def list_users(cls) -> List[LocalUser]:
        await cls.init()
        db_path = Storage.get_db_path()
        users: List[LocalUser] = []
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password, created_at, updated_at, last_login_at
                FROM users
                ORDER BY created_at ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            users.append(
                LocalUser(
                    id=row[0],
                    username=row[1],
                    role=row[2],
                    status=row[3],
                    must_reset_password=bool(row[4]),
                    created_at=row[5],
                    updated_at=row[6],
                    last_login_at=row[7],
                )
            )
        return users

    @classmethod
    async def _create_session(cls, user_id: str) -> str:
        await cls.init()
        session_id = secrets.token_urlsafe(32)
        now = _iso_now()
        expires_at = (_utc_now() + timedelta(days=cls._session_ttl_days)).isoformat()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO user_sessions(session_id, user_id, expires_at, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (session_id, user_id, expires_at, now, now),
            )
            await db.commit()
        return session_id

    @classmethod
    async def get_user_by_session_id(cls, session_id: str) -> Optional[LocalUser]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT u.id, u.username, u.role, u.status, u.must_reset_password, u.created_at, u.updated_at, u.last_login_at,
                       s.expires_at
                FROM user_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        expires_at = _parse_iso(row[8])
        if _utc_now() >= expires_at:
            await cls.revoke_session(session_id)
            return None
        user = LocalUser(
            id=row[0],
            username=row[1],
            role=row[2],
            status=row[3],
            must_reset_password=bool(row[4]),
            created_at=row[5],
            updated_at=row[6],
            last_login_at=row[7],
        )
        if user.status != "active":
            return None
        return user

    @classmethod
    async def revoke_session(cls, session_id: str) -> None:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))
            await db.commit()

    @classmethod
    async def login(
        cls,
        username: str,
        password: str,
    ) -> Tuple[LocalUser, str]:
        user_with_hash = await cls.get_user_by_username(username)
        if not user_with_hash:
            raise ValueError("用户名或密码错误")

        user, password_hash, temp_expires_at = user_with_hash
        if user.status != "active":
            raise ValueError("账号已被禁用")

        valid = cls._verify_password(password, password_hash)
        if not valid:
            raise ValueError("用户名或密码错误")

        if temp_expires_at:
            expiry = _parse_iso(temp_expires_at)
            if _utc_now() > expiry:
                raise ValueError("一次性密码已过期，请联系管理员重置")

        session_id = await cls._create_session(user.id)
        now = _iso_now()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, user.id))
            await db.commit()

        updated_user = await cls.get_user_by_id(user.id)
        if not updated_user:
            raise ValueError("登录失败")

        return updated_user, session_id

    @classmethod
    async def change_password(
        cls,
        user: AuthUser,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        existing = await cls.get_user_by_username(user.username)
        if not existing:
            raise ValueError("用户不存在")
        _, password_hash, _ = existing
        if not cls._verify_password(current_password, password_hash):
            raise ValueError("当前密码错误")
        await cls.set_password(
            target_user_id=user.id,
            new_password=new_password,
            must_reset_password=False,
            temp_password_expires_at=None,
        )

    @classmethod
    async def set_password(
        cls,
        *,
        target_user_id: str,
        new_password: str,
        must_reset_password: bool,
        temp_password_expires_at: Optional[str] = None,
    ) -> None:
        if len(new_password) < 8:
            raise ValueError("密码长度至少 8 位")
        await cls.init()
        now = _iso_now()
        pwd_hash = cls._hash_password(new_password)
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                """
                UPDATE users
                SET password_hash = ?, must_reset_password = ?, temp_password_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    pwd_hash,
                    1 if must_reset_password else 0,
                    temp_password_expires_at,
                    now,
                    target_user_id,
                ),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("用户不存在")
            # Security hardening: revoke all active sessions after password change/reset.
            await db.execute("DELETE FROM user_sessions WHERE user_id = ?", (target_user_id,))
            await db.commit()

    @classmethod
    async def generate_admin_temp_password(
        cls,
        *,
        username: str = "admin",
    ) -> str:
        user_info = await cls.get_user_by_username(username)
        if not user_info:
            raise ValueError("管理员账号不存在")
        user, _, _ = user_info
        if user.role != "admin":
            raise ValueError("目标账号不是管理员")
        temp_password = secrets.token_urlsafe(12)
        expires = (_utc_now() + timedelta(hours=cls._temp_password_ttl_hours)).isoformat()
        await cls.set_password(
            target_user_id=user.id,
            new_password=temp_password,
            must_reset_password=True,
            temp_password_expires_at=expires,
        )
        return temp_password

    @classmethod
    async def migrate_legacy_sessions_to_admin(cls, admin_user_id: str) -> None:
        """Set owner on legacy sessions without owner_user_id."""
        marker_key = "auth:migration:legacy_session_owner_to_admin"
        marker = await Storage.get(marker_key, dict)
        if marker and marker.get("done"):
            return
        try:
            from flocks.session.session import Session

            admin_user = await cls.get_user_by_id(admin_user_id)
            admin_username = admin_user.username if admin_user else None
            sessions = await Session.list_all()
            migrated = 0
            for session in sessions:
                if session.owner_user_id:
                    continue
                await Session.update(
                    project_id=session.project_id,
                    session_id=session.id,
                    owner_user_id=admin_user_id,
                    owner_username=admin_username,
                )
                migrated += 1
            await Storage.set(
                marker_key,
                {"done": True, "migrated": migrated, "updated_at": _iso_now()},
                "json",
            )
        except Exception as exc:
            log.warn("auth.migrate_legacy_sessions.failed", {"error": str(exc)})
            raise
