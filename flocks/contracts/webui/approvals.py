"""An administrator's approval of a page's server code and the secrets it may read.

A page plugin's ``api/`` directory is Python that runs inside the server.  What
it may do is decided by an administrator, for the code as it was reviewed:

* the approval names the code by its digest (every file under ``api/``); any
  change to the code -- by an editor, an import, an agent writing files --
  leaves it unapproved until someone looks again;
* it lists, for each secret the page declares in ``routes.yaml``, which roles
  of caller it may be read for; a secret the page declares but nobody
  approved, or one approved for administrators only and asked for on a
  member's call, is refused;
* on a managed install the page's handlers run at all only once approved
  (``handlers_enabled``): code running in this process cannot be fenced off
  from it by an interface, so whether to run it is a person's decision.

Approvals live in the runtime database, next to what they govern.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional

ROLES = ("admin", "member")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webui_page_approvals (
    page_id          TEXT PRIMARY KEY,
    code_sha256      TEXT NOT NULL,
    secret_roles     TEXT NOT NULL DEFAULT '{}',
    handlers_enabled INTEGER NOT NULL DEFAULT 0,
    approved_by      TEXT NOT NULL,
    approved_at      TEXT NOT NULL
)
"""
_schema_ready: set[str] = set()

#: What counts as the page's server code: everything a handler can import or read as configuration.
_CODE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".txt"}


@dataclass(frozen=True)
class PageApproval:
    page_id: str
    code_sha256: str
    #: secret id -> the caller roles it may be read for
    secret_roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    handlers_enabled: bool = False
    approved_by: str = ""
    approved_at: str = ""

    def secrets_for(self, role: Optional[str]) -> frozenset[str]:
        return frozenset(secret for secret, roles in self.secret_roles.items() if role in roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageId": self.page_id,
            "codeSha256": self.code_sha256,
            "secrets": {secret: list(roles) for secret, roles in self.secret_roles.items()},
            "handlersEnabled": self.handlers_enabled,
            "approvedBy": self.approved_by,
            "approvedAt": self.approved_at,
        }


def api_code_snapshot(api_dir: Path) -> dict[str, bytes]:
    """Every code and configuration file under ``api/``, by relative path, read once.

    What an approval is checked against and what is executed are the same
    bytes: the page's modules are compiled from this snapshot, never from the
    files again nor from a compiled cache next to them.  Compiled caches and
    hidden files are not the reviewed source and are left out.
    """
    root = Path(api_dir)
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in _CODE_SUFFIXES
        and "__pycache__" not in path.relative_to(root).parts
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in files}


def snapshot_digest(snapshot: Mapping[str, bytes]) -> str:
    """The digest an approval names: paths and contents both count, in a fixed order."""
    digest = hashlib.sha256()
    for relative in sorted(snapshot):
        name = relative.encode("utf-8")
        content = snapshot[relative]
        digest.update(len(name).to_bytes(4, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def api_code_digest(api_dir: Path) -> str:
    """The digest of the code under ``api/`` as it is now (see api_code_snapshot)."""
    return snapshot_digest(api_code_snapshot(api_dir))


@asynccontextmanager
async def _approvals_db() -> AsyncIterator[Any]:
    """A connection to the runtime database with the approvals table in it; commits on success."""
    from flocks.storage.storage import Storage

    await Storage._ensure_init()
    db_path = Storage.get_db_path()
    async with Storage.connect(db_path) as db:
        key = str(db_path)
        if key not in _schema_ready:
            await db.execute(_SCHEMA)
            _schema_ready.add(key)
        yield db
        await db.commit()


def _decode_roles(raw: Optional[str]) -> dict[str, tuple[str, ...]]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    roles: dict[str, tuple[str, ...]] = {}
    for secret, granted in value.items():
        if isinstance(secret, str) and isinstance(granted, list):
            roles[secret] = tuple(role for role in granted if role in ROLES)
    return roles


async def get_approval(page_id: str) -> Optional[PageApproval]:
    async with _approvals_db() as db:
        cursor = await db.execute(
            "SELECT page_id, code_sha256, secret_roles, handlers_enabled, approved_by, approved_at "
            "FROM webui_page_approvals WHERE page_id = ?",
            (page_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return PageApproval(
        page_id=row[0],
        code_sha256=row[1],
        secret_roles=_decode_roles(row[2]),  # secret-guard: allow 误报：字段名，值是角色列表
        handlers_enabled=bool(row[3]),
        approved_by=row[4],
        approved_at=row[5],
    )


async def set_approval(
    page_id: str,
    *,
    code_sha256: str,
    secret_roles: Mapping[str, list[str] | tuple[str, ...]],
    handlers_enabled: bool,
    approved_by: str,
) -> PageApproval:
    cleaned = {secret: tuple(dict.fromkeys(role for role in roles if role in ROLES)) for secret, roles in secret_roles.items()}
    approved_at = datetime.now(UTC).isoformat()
    async with _approvals_db() as db:
        await db.execute(
            "INSERT INTO webui_page_approvals (page_id, code_sha256, secret_roles, handlers_enabled, approved_by, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (page_id) DO UPDATE SET code_sha256 = EXCLUDED.code_sha256, "
            "secret_roles = EXCLUDED.secret_roles, handlers_enabled = EXCLUDED.handlers_enabled, "  # secret-guard: allow 误报：列名
            "approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at",
            (
                page_id,
                code_sha256,
                json.dumps({secret: list(roles) for secret, roles in cleaned.items()}, ensure_ascii=False),
                1 if handlers_enabled else 0,
                approved_by,
                approved_at,
            ),
        )
    return PageApproval(page_id, code_sha256, cleaned, handlers_enabled, approved_by, approved_at)


async def revoke_approval(page_id: str) -> bool:
    async with _approvals_db() as db:
        cursor = await db.execute("DELETE FROM webui_page_approvals WHERE page_id = ?", (page_id,))
        removed = cursor.rowcount
    return bool(removed)


__all__ = ["PageApproval", "ROLES", "api_code_digest", "get_approval", "revoke_approval", "set_approval"]
