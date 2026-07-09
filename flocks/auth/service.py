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
from typing import Dict, Iterable, List, Optional, Tuple

import aiosqlite
from pydantic import BaseModel, Field

from flocks.auth.context import AuthUser
from flocks.extensions import ensure_callable_methods
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


def _clean_scope_values(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    return tuple(cleaned)


def _decode_scope_values(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    return _clean_scope_values(values)


def _encode_scope_values(values: Iterable[str]) -> str:
    return json.dumps(list(_clean_scope_values(values)), ensure_ascii=False)


class LocalUser(BaseModel):
    id: str
    username: str
    role: str
    status: str
    must_reset_password: bool
    tenant_ids: tuple[str, ...] = Field(default_factory=tuple)
    department: str = ""
    asset_groups: tuple[str, ...] = Field(default_factory=tuple)
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
            tenant_ids=self.tenant_ids,
            department=self.department,
            asset_groups=self.asset_groups,
        )


class ApiKeyRecord(BaseModel):
    id: str
    name: str
    secret_hash: str
    subject_type: str
    role: str
    tenant_id: str
    department: str
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    permission_mode: str
    expires_at: Optional[str] = None
    disabled: bool = False
    created_by: str = ""
    created_at: str
    last_used_at: Optional[str] = None

    def to_auth_user(self) -> AuthUser:
        username = self.name.strip() or f"api-key:{self.id}"
        return AuthUser(
            id=f"api-key:{self.id}",
            username=username,
            role=self.role or "member",
            status="disabled" if self.disabled else "active",
            must_reset_password=False,
            tenant_ids=((self.tenant_id,) if self.tenant_id else ()),
            department=self.department,
            asset_groups=tuple(self.scopes),
        )


class LocalAuthBackend:
    """Default local account/session backend."""

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
        # Switching to a new DB path (common in tests) must clear cached state.
        cls._has_users_cached = False
        async with Storage.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    status TEXT NOT NULL DEFAULT 'active',
                    must_reset_password INTEGER NOT NULL DEFAULT 0,
                    tenant_ids TEXT NOT NULL DEFAULT '[]',
                    department TEXT NOT NULL DEFAULT '',
                    asset_groups TEXT NOT NULL DEFAULT '[]',
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

                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    secret_hash TEXT NOT NULL,
                    subject_type TEXT NOT NULL DEFAULT 'api_key',
                    role TEXT NOT NULL DEFAULT 'member',
                    tenant_id TEXT NOT NULL DEFAULT '',
                    department TEXT NOT NULL DEFAULT '',
                    scopes TEXT NOT NULL DEFAULT '[]',
                    permission_mode TEXT NOT NULL DEFAULT 'readonly',
                    expires_at TEXT,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_api_keys_secret_hash ON api_keys(secret_hash);
                CREATE INDEX IF NOT EXISTS idx_api_keys_disabled ON api_keys(disabled);

                """
            )
            await cls._ensure_user_scope_columns(db)
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
    async def _ensure_user_scope_columns(cls, db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "tenant_ids" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN tenant_ids TEXT NOT NULL DEFAULT '[]'")
        if "department" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN department TEXT NOT NULL DEFAULT ''")
        if "asset_groups" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN asset_groups TEXT NOT NULL DEFAULT '[]'")

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
    def _hash_api_key_secret(cls, secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @classmethod
    def _row_to_api_key(cls, row: aiosqlite.Row) -> ApiKeyRecord:
        return ApiKeyRecord(
            id=row[0],
            name=row[1],
            secret_hash=row[2],
            subject_type=row[3],
            role=row[4],
            tenant_id=row[5] or "",
            department=row[6] or "",
            scopes=_decode_scope_values(row[7]),
            permission_mode=row[8] or "readonly",
            expires_at=row[9],
            disabled=bool(row[10]),
            created_by=row[11] or "",
            created_at=row[12],
            last_used_at=row[13],
        )

    @classmethod
    async def has_users(cls) -> bool:
        if cls._has_users_cached:
            return True
        await cls.init()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
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
        tenant_ids: Iterable[str] = (),
        department: str = "",
        asset_groups: Iterable[str] = (),
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
        async with Storage.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, role, status, must_reset_password,
                    tenant_ids, department, asset_groups, temp_password_expires_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_username,
                    password_hash,
                    role,
                    1 if must_reset_password else 0,
                    _encode_scope_values(tenant_ids),
                    department.strip(),
                    _encode_scope_values(asset_groups),
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
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password, tenant_ids, department, asset_groups,
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
            tenant_ids=_decode_scope_values(row[5]),
            department=row[6] or "",
            asset_groups=_decode_scope_values(row[7]),
            created_at=row[8],
            updated_at=row[9],
            last_login_at=row[10],
        )

    @classmethod
    async def get_user_by_username(cls, username: str) -> Optional[Tuple[LocalUser, str, Optional[str]]]:
        await cls.init()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password, tenant_ids, department, asset_groups,
                       created_at, updated_at, last_login_at,
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
            tenant_ids=_decode_scope_values(row[5]),
            department=row[6] or "",
            asset_groups=_decode_scope_values(row[7]),
            created_at=row[8],
            updated_at=row[9],
            last_login_at=row[10],
        )
        return user, row[11], row[12]

    @classmethod
    async def list_users(cls) -> List[LocalUser]:
        await cls.init()
        db_path = Storage.get_db_path()
        users: List[LocalUser] = []
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, username, role, status, must_reset_password, tenant_ids, department, asset_groups,
                       created_at, updated_at, last_login_at
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
                    tenant_ids=_decode_scope_values(row[5]),
                    department=row[6] or "",
                    asset_groups=_decode_scope_values(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                    last_login_at=row[10],
                )
            )
        return users

    @classmethod
    async def create_api_key(
        cls,
        *,
        name: str,
        role: str = "member",
        tenant_id: str = "",
        department: str = "",
        scopes: Iterable[str] = (),
        permission_mode: str = "readonly",
        expires_at: Optional[str] = None,
        created_by: str = "",
        secret: Optional[str] = None,
    ) -> tuple[ApiKeyRecord, str]:
        await cls.init()
        if role not in {"admin", "member"}:
            raise ValueError("无效角色")
        secret_value = (secret or secrets.token_urlsafe(32)).strip()
        if len(secret_value) < 16:
            raise ValueError("API key 长度至少 16 字符")
        key_id = f"ak_{secrets.token_hex(8)}"
        now = _iso_now()
        db_path = Storage.get_db_path()
        secret_hash = cls._hash_api_key_secret(secret_value)
        async with Storage.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO api_keys (
                    id, name, secret_hash, subject_type, role, tenant_id, department,
                    scopes, permission_mode, expires_at, disabled, created_by, created_at, last_used_at
                )
                VALUES (?, ?, ?, 'api_key', ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
                """,
                (
                    key_id,
                    name.strip(),
                    secret_hash,
                    role,
                    tenant_id.strip(),
                    department.strip(),
                    _encode_scope_values(scopes),
                    permission_mode.strip() or "readonly",
                    expires_at,
                    created_by.strip(),
                    now,
                ),
            )
            await db.commit()
            async with db.execute(
                """
                SELECT id, name, secret_hash, subject_type, role, tenant_id, department,
                       scopes, permission_mode, expires_at, disabled, created_by, created_at, last_used_at
                FROM api_keys
                WHERE id = ?
                """,
                (key_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise ValueError("API key 创建失败")
        return cls._row_to_api_key(row), secret_value

    @classmethod
    async def list_api_keys(cls) -> List[ApiKeyRecord]:
        await cls.init()
        db_path = Storage.get_db_path()
        keys: list[ApiKeyRecord] = []
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, name, secret_hash, subject_type, role, tenant_id, department,
                       scopes, permission_mode, expires_at, disabled, created_by, created_at, last_used_at
                FROM api_keys
                ORDER BY created_at DESC
                """
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            keys.append(cls._row_to_api_key(row))
        return keys

    @classmethod
    async def revoke_api_key(cls, key_id: str) -> None:
        await cls.init()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
            cursor = await db.execute(
                "UPDATE api_keys SET disabled = 1 WHERE id = ?",
                (key_id,),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("API key 不存在")

    @classmethod
    async def touch_api_key(cls, key_id: str) -> None:
        await cls.init()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (_iso_now(), key_id),
            )
            await db.commit()

    @classmethod
    async def verify_api_key(cls, secret: str) -> Optional[ApiKeyRecord]:
        await cls.init()
        candidate = secret.strip()
        if not candidate:
            return None
        candidate_hash = cls._hash_api_key_secret(candidate)
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT id, name, secret_hash, subject_type, role, tenant_id, department,
                       scopes, permission_mode, expires_at, disabled, created_by, created_at, last_used_at
                FROM api_keys
                WHERE disabled = 0
                """
            ) as cursor:
                rows = await cursor.fetchall()
        now = _utc_now()
        for row in rows:
            api_key = cls._row_to_api_key(row)
            if not hmac.compare_digest(api_key.secret_hash, candidate_hash):
                continue
            if api_key.expires_at:
                try:
                    if now >= _parse_iso(api_key.expires_at):
                        return None
                except Exception:
                    return None
            await cls.touch_api_key(api_key.id)
            return api_key
        return None

    @classmethod
    async def _create_session(cls, user_id: str) -> str:
        await cls.init()
        session_id = secrets.token_urlsafe(32)
        now = _iso_now()
        expires_at = (_utc_now() + timedelta(days=cls._session_ttl_days)).isoformat()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
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
        async with Storage.connect(db_path) as db:
            async with db.execute(
                """
                SELECT u.id, u.username, u.role, u.status, u.must_reset_password,
                       u.tenant_ids, u.department, u.asset_groups, u.created_at, u.updated_at, u.last_login_at,
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
        expires_at = _parse_iso(row[11])
        if _utc_now() >= expires_at:
            await cls.revoke_session(session_id)
            return None
        user = LocalUser(
            id=row[0],
            username=row[1],
            role=row[2],
            status=row[3],
            must_reset_password=bool(row[4]),
            tenant_ids=_decode_scope_values(row[5]),
            department=row[6] or "",
            asset_groups=_decode_scope_values(row[7]),
            created_at=row[8],
            updated_at=row[9],
            last_login_at=row[10],
        )
        if user.status != "active":
            return None
        return user

    @classmethod
    async def revoke_session(cls, session_id: str) -> None:
        await cls.init()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
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
        async with Storage.connect(db_path) as db:
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
        async with Storage.connect(db_path) as db:
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
    async def set_user_contract_scope(
        cls,
        *,
        target_user_id: str,
        tenant_ids: Iterable[str],
        asset_groups: Iterable[str],
    ) -> LocalUser:
        await cls.init()
        now = _iso_now()
        db_path = Storage.get_db_path()
        async with Storage.connect(db_path) as db:
            cursor = await db.execute(
                """
                UPDATE users
                SET tenant_ids = ?, asset_groups = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _encode_scope_values(tenant_ids),
                    _encode_scope_values(asset_groups),
                    now,
                    target_user_id,
                ),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("用户不存在")
        user = await cls.get_user_by_id(target_user_id)
        if not user:
            raise ValueError("用户不存在")
        return user

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
    async def reassign_orphan_sessions(
        cls,
        admin_user_id: str,
        *,
        dry_run: bool = False,
    ) -> Dict[str, int]:
        """Backfill owner on every session that still lacks one.

        Unlike :meth:`migrate_legacy_sessions_to_admin`, this is **not**
        guarded by the one-shot startup marker — it can be re-run anytime
        operators discover orphan sessions accumulated by CLI / background
        / inbound-channel workers (which run without an auth context and
        therefore leave ``owner_user_id`` empty).

        Each session is rewritten independently: a single failure (IO
        error, concurrent delete, …) does not abort the whole pass. The
        returned summary always carries ``scanned`` / ``orphaned`` /
        ``reassigned`` / ``failed`` counts, so the operator can decide
        whether to re-run.  ``reassigned`` and ``failed`` are always 0
        when ``dry_run=True``.
        """
        from flocks.session.session import Session

        admin_user = await cls.get_user_by_id(admin_user_id)
        if not admin_user:
            raise ValueError("目标管理员账号不存在")
        if admin_user.role != "admin":
            raise ValueError("目标账号不是管理员，拒绝转移所有权")

        sessions = await Session.list_all()
        orphans = [s for s in sessions if not s.owner_user_id]
        reassigned = 0
        failed = 0
        if not dry_run:
            for session in orphans:
                try:
                    await Session.update(
                        project_id=session.project_id,
                        session_id=session.id,
                        owner_user_id=admin_user_id,
                        owner_username=admin_user.username,
                    )
                    reassigned += 1
                except Exception as exc:
                    failed += 1
                    log.warn(
                        "auth.reassign_orphan_sessions.update_failed",
                        {"session_id": session.id, "error": str(exc)},
                    )
        return {
            "scanned": len(sessions),
            "orphaned": len(orphans),
            "reassigned": reassigned,
            "failed": failed,
        }

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


class _AuthServiceFacadeMeta(type):
    """Delegate unknown class attributes to the configured backend."""

    _MIRRORED_STATE_ATTRS = ("_initialized", "_initialized_db_path", "_has_users_cached")

    def __getattr__(cls, name: str):
        backend = cls.get_backend()
        return getattr(backend, name)

    def __setattr__(cls, name: str, value):
        super().__setattr__(name, value)
        if name in cls._MIRRORED_STATE_ATTRS and hasattr(cls, "_backend"):
            backend = cls.get_backend()
            if hasattr(backend, name):
                setattr(backend, name, value)


class AuthService(metaclass=_AuthServiceFacadeMeta):
    """
    Authentication facade.

    The OSS default backend is ``LocalAuthBackend``. Flocks Pro packages can
    swap in a compatible backend via ``register_backend``.
    """

    _backend = LocalAuthBackend
    _initialized = LocalAuthBackend._initialized
    _initialized_db_path = LocalAuthBackend._initialized_db_path
    _has_users_cached = LocalAuthBackend._has_users_cached

    @classmethod
    def register_backend(cls, backend) -> None:
        if backend is None:
            raise ValueError("backend 不能为空")
        ensure_callable_methods(
            backend,
            (
                "init",
                "has_users",
                "get_bootstrap_status",
                "bootstrap_admin",
                "get_user_by_id",
                "get_user_by_username",
                "list_users",
                "get_user_by_session_id",
                "revoke_session",
                "login",
                "change_password",
                "set_password",
                "generate_admin_temp_password",
                "reassign_orphan_sessions",
                "migrate_legacy_sessions_to_admin",
            ),
            label="auth backend",
        )
        cls._backend = backend
        for attr in _AuthServiceFacadeMeta._MIRRORED_STATE_ATTRS:
            if hasattr(backend, attr):
                setattr(backend, attr, getattr(cls, attr))
        log.info("auth.backend.registered", {"backend": getattr(backend, "__name__", str(backend))})

    @classmethod
    def reset_backend(cls) -> None:
        cls._backend = LocalAuthBackend
        for attr in _AuthServiceFacadeMeta._MIRRORED_STATE_ATTRS:
            setattr(LocalAuthBackend, attr, getattr(cls, attr))
        log.info("auth.backend.reset", {"backend": "LocalAuthBackend"})

    @classmethod
    def get_backend(cls):
        return cls._backend
