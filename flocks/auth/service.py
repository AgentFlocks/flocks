"""
Local account/authentication service.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from pydantic import BaseModel, Field

from flocks.auth.context import AuthUser
from flocks.storage.storage import Storage
from flocks.utils.id import Identifier
from flocks.utils.log import Log

log = Log.create(service="auth.service")


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


class AuditRecord(BaseModel):
    id: str
    operator_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    action: str
    result: str
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CloudBinding(BaseModel):
    provider: str
    account_id: str
    account_name: Optional[str] = None
    token_masked: Optional[str] = None
    mcp_quota: Optional[str] = None
    api_quota: Optional[str] = None
    balance: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    bound_by: str
    bound_at: str
    updated_at: str


class AuthService:
    """Account, auth session, audit and cloud binding service."""

    _initialized: bool = False
    _session_ttl_days: int = 7
    _temp_password_ttl_hours: int = 24

    @classmethod
    async def init(cls) -> None:
        if cls._initialized:
            return
        await Storage.init()
        db_path = Storage.get_db_path()
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

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    operator_user_id TEXT,
                    target_user_id TEXT,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    ip TEXT,
                    user_agent TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);

                CREATE TABLE IF NOT EXISTS cloud_binding (
                    singleton_key TEXT PRIMARY KEY CHECK (singleton_key = 'instance'),
                    provider TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    account_name TEXT,
                    token_masked TEXT,
                    mcp_quota TEXT,
                    api_quota TEXT,
                    balance TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    bound_by TEXT NOT NULL,
                    bound_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await db.commit()

        cls._initialized = True
        log.info("auth.initialized")

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
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(1) FROM users") as cursor:
                row = await cursor.fetchone()
                return bool(row and row[0] > 0)

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
        await cls.record_audit(
            action="auth.bootstrap_admin",
            result="success",
            operator_user_id=user.id,
            target_user_id=user.id,
            metadata={"username": username},
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
        return await cls.get_user_by_id(user_id)  # type: ignore[return-value]

    @classmethod
    async def create_user(cls, username: str, password: str, role: str = "member") -> LocalUser:
        return await cls._create_user_internal(username=username, password=password, role=role)

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
    async def update_user_status(cls, user_id: str, status: str) -> LocalUser:
        if status not in {"active", "disabled"}:
            raise ValueError("无效账号状态")
        await cls.init()
        user = await cls.get_user_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")

        if status == "disabled" and user.role == "admin" and user.status == "active":
            remaining_admins = await cls.count_active_admins(exclude_user_id=user_id)
            if remaining_admins == 0:
                raise ValueError("不能禁用最后一个管理员账号")

        now = _iso_now()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, user_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("用户不存在")
        updated_user = await cls.get_user_by_id(user_id)
        if not updated_user:
            raise ValueError("用户不存在")
        return updated_user

    @classmethod
    async def count_active_admins(cls, exclude_user_id: Optional[str] = None) -> int:
        await cls.init()
        db_path = Storage.get_db_path()
        query = "SELECT COUNT(1) FROM users WHERE role = 'admin' AND status = 'active'"
        params: tuple[Any, ...] = ()
        if exclude_user_id:
            query += " AND id != ?"
            params = (exclude_user_id,)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return int(row[0] if row else 0)

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
        *,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[LocalUser, str]:
        user_with_hash = await cls.get_user_by_username(username)
        if not user_with_hash:
            await cls.record_audit(
                action="auth.login",
                result="failed",
                metadata={"reason": "user_not_found", "username": username},
                ip=ip,
                user_agent=user_agent,
            )
            raise ValueError("用户名或密码错误")

        user, password_hash, temp_expires_at = user_with_hash
        if user.status != "active":
            await cls.record_audit(
                action="auth.login",
                result="failed",
                operator_user_id=user.id,
                target_user_id=user.id,
                metadata={"reason": "user_disabled"},
                ip=ip,
                user_agent=user_agent,
            )
            raise ValueError("账号已被禁用")

        valid = cls._verify_password(password, password_hash)
        if not valid:
            await cls.record_audit(
                action="auth.login",
                result="failed",
                operator_user_id=user.id,
                target_user_id=user.id,
                metadata={"reason": "password_mismatch"},
                ip=ip,
                user_agent=user_agent,
            )
            raise ValueError("用户名或密码错误")

        if temp_expires_at:
            expiry = _parse_iso(temp_expires_at)
            if _utc_now() > expiry:
                await cls.record_audit(
                    action="auth.login",
                    result="failed",
                    operator_user_id=user.id,
                    target_user_id=user.id,
                    metadata={"reason": "temp_password_expired"},
                    ip=ip,
                    user_agent=user_agent,
                )
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

        await cls.record_audit(
            action="auth.login",
            result="success",
            operator_user_id=user.id,
            target_user_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        return updated_user, session_id

    @classmethod
    async def change_password(
        cls,
        user: AuthUser,
        *,
        current_password: str,
        new_password: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        existing = await cls.get_user_by_username(user.username)
        if not existing:
            raise ValueError("用户不存在")
        _, password_hash, _ = existing
        if not cls._verify_password(current_password, password_hash):
            await cls.record_audit(
                action="auth.change_password",
                result="failed",
                operator_user_id=user.id,
                target_user_id=user.id,
                metadata={"reason": "current_password_invalid"},
                ip=ip,
                user_agent=user_agent,
            )
            raise ValueError("当前密码错误")
        await cls.set_password(
            operator_user=user,
            target_user_id=user.id,
            new_password=new_password,
            must_reset_password=False,
            temp_password_expires_at=None,
            action="auth.change_password",
            ip=ip,
            user_agent=user_agent,
        )

    @classmethod
    async def set_password(
        cls,
        *,
        operator_user: AuthUser,
        target_user_id: str,
        new_password: str,
        must_reset_password: bool,
        temp_password_expires_at: Optional[str] = None,
        action: str = "admin.reset_password",
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
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
        await cls.record_audit(
            action=action,
            result="success",
            operator_user_id=operator_user.id,
            target_user_id=target_user_id,
            ip=ip,
            user_agent=user_agent,
            metadata={"must_reset_password": must_reset_password},
        )

    @classmethod
    async def generate_admin_temp_password(
        cls,
        operator: Optional[AuthUser] = None,
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
        if operator is None:
            operator = AuthUser(
                id="system",
                username="system",
                role="admin",
                status="active",
            )
        await cls.set_password(
            operator_user=operator,
            target_user_id=user.id,
            new_password=temp_password,
            must_reset_password=True,
            temp_password_expires_at=expires,
            action="admin.generate_one_time_password",
        )
        return temp_password

    @classmethod
    async def record_audit(
        cls,
        *,
        action: str,
        result: str,
        operator_user_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await cls.init()
        db_path = Storage.get_db_path()
        payload = metadata or {}
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO audit_logs(
                    id, operator_user_id, target_user_id, action, result, ip, user_agent, metadata, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    Identifier.ascending("audit"),
                    operator_user_id,
                    target_user_id,
                    action,
                    result,
                    ip,
                    user_agent,
                    json.dumps(payload, ensure_ascii=True),
                    _iso_now(),
                ),
            )
            await db.commit()

    @classmethod
    async def list_audits(cls, *, limit: int = 200, offset: int = 0) -> List[AuditRecord]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, operator_user_id, target_user_id, action, result, ip, user_agent, metadata, created_at
                FROM audit_logs
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        records: List[AuditRecord] = []
        for row in rows:
            records.append(
                AuditRecord(
                    id=row[0],
                    operator_user_id=row[1],
                    target_user_id=row[2],
                    action=row[3],
                    result=row[4],
                    ip=row[5],
                    user_agent=row[6],
                    metadata=json.loads(row[7] or "{}"),
                    created_at=row[8],
                )
            )
        return records

    @classmethod
    async def get_cloud_binding(cls) -> Optional[CloudBinding]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT provider, account_id, account_name, token_masked, mcp_quota, api_quota, balance,
                       metadata, bound_by, bound_at, updated_at
                FROM cloud_binding WHERE singleton_key = 'instance'
                """
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return CloudBinding(
            provider=row[0],
            account_id=row[1],
            account_name=row[2],
            token_masked=row[3],
            mcp_quota=row[4],
            api_quota=row[5],
            balance=row[6],
            metadata=json.loads(row[7] or "{}"),
            bound_by=row[8],
            bound_at=row[9],
            updated_at=row[10],
        )

    @classmethod
    async def bind_cloud_account(
        cls,
        *,
        operator: AuthUser,
        provider: str,
        account_id: str,
        account_name: Optional[str] = None,
        token: Optional[str] = None,
        mcp_quota: Optional[str] = None,
        api_quota: Optional[str] = None,
        balance: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CloudBinding:
        await cls.init()
        if not provider.strip() or not account_id.strip():
            raise ValueError("云账号信息不完整")
        now = _iso_now()
        token_masked = None
        if token:
            token_masked = (token[:3] + "***" + token[-3:]) if len(token) >= 8 else "***"
        db_path = Storage.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO cloud_binding(
                    singleton_key, provider, account_id, account_name, token_masked,
                    mcp_quota, api_quota, balance, metadata, bound_by, bound_at, updated_at
                )
                VALUES('instance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_key) DO UPDATE SET
                    provider=excluded.provider,
                    account_id=excluded.account_id,
                    account_name=excluded.account_name,
                    token_masked=excluded.token_masked,
                    mcp_quota=excluded.mcp_quota,
                    api_quota=excluded.api_quota,
                    balance=excluded.balance,
                    metadata=excluded.metadata,
                    bound_by=excluded.bound_by,
                    updated_at=excluded.updated_at
                """,
                (
                    provider.strip(),
                    account_id.strip(),
                    (account_name or "").strip() or None,
                    token_masked,
                    mcp_quota,
                    api_quota,
                    balance,
                    json.dumps(metadata or {}, ensure_ascii=True),
                    operator.id,
                    now,
                    now,
                ),
            )
            await db.commit()
        await cls.record_audit(
            action="cloud.bind",
            result="success",
            operator_user_id=operator.id,
            metadata={
                "provider": provider,
                "account_id": account_id,
                "rebind": True,
            },
        )
        binding = await cls.get_cloud_binding()
        if not binding:
            raise ValueError("云账号绑定失败")
        return binding

    @classmethod
    async def migrate_legacy_sessions_to_admin(cls, admin_user_id: str) -> None:
        """Set owner on legacy sessions without owner_user_id."""
        marker_key = "auth:migration:legacy_session_owner_to_admin"
        marker = await Storage.get(marker_key, dict)
        if marker and marker.get("done"):
            return
        try:
            from flocks.session.session import Session

            sessions = await Session.list_all()
            migrated = 0
            for session in sessions:
                if session.owner_user_id:
                    continue
                await Session.update(
                    project_id=session.project_id,
                    session_id=session.id,
                    owner_user_id=admin_user_id,
                    visibility="private",
                )
                migrated += 1
            await Storage.set(
                marker_key,
                {"done": True, "migrated": migrated, "updated_at": _iso_now()},
                "json",
            )
            await cls.record_audit(
                action="auth.migrate_legacy_sessions",
                result="success",
                operator_user_id=admin_user_id,
                metadata={"migrated": migrated},
            )
        except Exception as exc:
            log.warn("auth.migrate_legacy_sessions.failed", {"error": str(exc)})
            raise
