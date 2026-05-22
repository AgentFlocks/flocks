"""OSS console login orchestration for local nodes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from flocks import __version__
from flocks.storage.storage import Storage


def _shared_console_session_path() -> Path:
    raw = os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))
    return Path(raw).expanduser() / "run" / "console-session.json"


def _write_shared_console_session(session: dict[str, Any]) -> None:
    path = _shared_console_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "console_session_token": session.get("console_session_token"),
        "fingerprint": session.get("fingerprint"),
        "install_id": session.get("install_id"),
        "passport_uid": session.get("passport_uid"),
        "expires_at": session.get("expires_at"),
        "updated_at": session.get("updated_at") or _now_iso(),
        "console_base_url": ConsoleLoginService.console_base_url(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _delete_shared_console_session() -> None:
    path = _shared_console_session_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


class ConsoleLoginService:
    @classmethod
    async def _get_install_id(cls) -> str:
        key = "console:install_id"
        existing = await Storage.get(key)
        if existing:
            return str(existing)
        install_id = str(uuid4())
        await Storage.set(key, install_id, "string")
        return install_id

    @classmethod
    async def get_fingerprint(cls) -> str:
        install_id = await cls._get_install_id()
        raw = f"{platform.node()}|{platform.machine()}|{platform.system()}|{install_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def console_base_url() -> str:
        raw = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").strip().rstrip("/")
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")):
            return raw
        return f"https://{raw}"

    @classmethod
    async def start_console_login(cls, return_to: str) -> dict[str, Any]:
        console_base = cls.console_base_url()
        if not console_base:
            raise ValueError("未配置 FLOCKS_CONSOLE_BASE_URL，无法发起云账号登录")
        console_login_id = str(uuid4())
        state = secrets.token_urlsafe(24)
        payload = {
            "console_login_id": console_login_id,
            "state": state,
            "fingerprint": await cls.get_fingerprint(),
            "install_id": await cls._get_install_id(),
            "return_to": return_to,
            "created_at": _now_iso(),
            "status": "pending",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{console_base}/v1/flocks/console-logins", json=payload)
            resp.raise_for_status()
            data = resp.json()
            console_login_id = data.get("console_login_id", console_login_id)
            passport_login_url = data.get("passport_login_url")
            if not passport_login_url:
                raise ValueError("console 未返回 passport_login_url")
            payload.update({"console_login_id": console_login_id, "status": "pending_remote"})
        await Storage.set(f"console:login:{console_login_id}", payload, "json")
        return {"console_login_id": console_login_id, "passport_login_url": passport_login_url}

    @classmethod
    async def finish_console_login(
        cls,
        console_login_id: str,
        state: str | None = None,
        passport_uid: str | None = None,
    ) -> dict[str, Any]:
        console_base = cls.console_base_url()
        if not console_base:
            raise ValueError("未配置 FLOCKS_CONSOLE_BASE_URL，无法完成云账号登录")
        pending = await Storage.get(f"console:login:{console_login_id}")
        if not isinstance(pending, dict):
            raise ValueError("console_login_id 不存在或已过期")
        expected_state = str(pending.get("state") or "")
        if expected_state and state != expected_state:
            raise ValueError("console login state 校验失败")

        fingerprint = await cls.get_fingerprint()
        install_id = await cls._get_install_id()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{console_base}/v1/flocks/console-logins/{console_login_id}/exchange",
                json={
                    "fingerprint": fingerprint,
                    "install_id": install_id,
                    "state": state,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("console_session_token")
            if not token:
                raise ValueError("console 未返回 console_session_token")
            console_passport_uid = data.get("passport_uid")
            user_email = data.get("user_email")
            user_display = data.get("user_display")
            expires_at = data.get("expires_at")

        console_session = {
            "console_login_id": console_login_id,
            "console_session_token": token,
            "fingerprint": fingerprint,
            "install_id": install_id,
            "passport_uid": console_passport_uid or passport_uid,
            "user_email": user_email,
            "user_display": user_display,
            "expires_at": expires_at,
            "updated_at": _now_iso(),
        }
        await Storage.set("console:session", console_session, "json")
        await Storage.set(f"console:login:{console_login_id}", {**pending, "status": "exchanged"}, "json")
        _write_shared_console_session(console_session)
        return console_session

    @classmethod
    async def get_console_session(cls) -> dict[str, Any] | None:
        raw = await Storage.get("console:session")
        if not isinstance(raw, dict):
            return None
        expires_at = str(raw.get("expires_at") or "").strip()
        if expires_at:
            try:
                if _parse_iso(expires_at) <= datetime.now(UTC):
                    await Storage.delete("console:session")
                    _delete_shared_console_session()
                    return None
            except ValueError:
                await Storage.delete("console:session")
                _delete_shared_console_session()
                return None
        return raw

    @classmethod
    async def refresh_console_session(cls) -> dict[str, Any]:
        session = await cls._require_session()
        console_base = cls.console_base_url()
        if not console_base:
            return {"ok": True, "mode": "mock", "session": session}
        token = str(session["console_session_token"])
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{console_base}/v1/console-sessions/refresh",
                headers={"Authorization": f"Bearer {token}"},
                json={"console_session_token": token},
            )
            if resp.status_code in {400, 401, 403, 404}:
                await Storage.delete("console:session")
                _delete_shared_console_session()
                raise ValueError("console 会话已失效，请重新登录")
            resp.raise_for_status()
            data = resp.json()
        now = _now_iso()
        refreshed_session = {
            **session,
            "console_session_token": data.get("console_session_token") or session.get("console_session_token"),
            "passport_uid": data.get("passport_uid") or session.get("passport_uid"),
            "user_email": data.get("user_email", session.get("user_email")),
            "user_display": data.get("user_display", session.get("user_display")),
            "expires_at": data.get("expires_at"),
            "refreshed_at": now,
            "updated_at": now,
        }
        await Storage.set("console:session", refreshed_session, "json")
        _write_shared_console_session(refreshed_session)
        return refreshed_session

    @classmethod
    async def logout_console_session(cls) -> None:
        session = await cls.get_console_session()
        console_base = cls.console_base_url()
        if console_base and session:
            token = str(session.get("console_session_token") or "").strip()
            if token:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            f"{console_base}/v1/console-sessions/revoke",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"console_session_token": token},
                        )
                except Exception:
                    pass
        await Storage.delete("console:session")
        _delete_shared_console_session()

    @classmethod
    async def require_console_session(cls) -> dict[str, Any]:
        session = await cls._require_session()
        account_name = session.get("user_display") or session.get("user_email") or session.get("passport_uid")
        if not account_name:
            raise ValueError("云账号未登录")
        return session

    @classmethod
    async def _require_session(cls) -> dict[str, Any]:
        original = await cls.get_console_session()
        if not isinstance(original, dict):
            raise ValueError("云账号未登录")
        session = dict(original)
        token = str(session.get("console_session_token") or "").strip()
        fingerprint = str(session.get("fingerprint") or "").strip()
        install_id = str(session.get("install_id") or "").strip()
        if not token or not fingerprint or not install_id:
            raise ValueError("console 会话无效，请重新登录")
        if token.startswith("mock-console-session-") and cls.console_base_url():
            raise ValueError("console 登录未完成远端 exchange，请重新登录")
        return session

    @staticmethod
    def _edition() -> str:
        raw = (os.getenv("FLOCKS_EDITION") or "oss").strip().lower()
        return "flockspro" if raw == "flockspro" else "oss"

    @staticmethod
    def _runtime_version() -> str:
        try:
            from flocks.updater.updater import get_current_version

            version = str(get_current_version() or "").strip()
            if version:
                return version.lstrip("v")
        except Exception:
            pass
        return str(__version__).lstrip("v")

    @classmethod
    async def send_heartbeat(cls) -> dict[str, Any]:
        session = await cls._require_session()
        console_base = cls.console_base_url()
        payload = {
            "fingerprint": session["fingerprint"],
            "install_id": session["install_id"],
            "console_login_id": session.get("console_login_id"),
            "sent_at": _now_iso(),
            "status": "ok",
        }
        if not console_base:
            return {"ok": True, "mode": "mock", "node": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{console_base}/v1/heartbeats",
                json=payload,
                headers={"Authorization": f"Bearer {session['console_session_token']}"},
            )
            if resp.status_code in {401, 403}:
                raise ValueError("console 会话已失效，请重新登录")
            resp.raise_for_status()
            return resp.json()

    @classmethod
    async def sync_node_profile(cls, *, force: bool = False, source: str = "scheduled") -> dict[str, Any]:
        _ = force
        session = await cls._require_session()
        console_base = cls.console_base_url()
        payload = {
            "fingerprint": session["fingerprint"],
            "install_id": session["install_id"],
            "edition": cls._edition(),
            "version": cls._runtime_version(),
            "source": source,
            "sent_at": _now_iso(),
        }
        if not console_base:
            return {"ok": True, "mode": "mock", "node": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{console_base}/v1/nodes/sync",
                json=payload,
                headers={"Authorization": f"Bearer {session['console_session_token']}"},
            )
            if resp.status_code in {401, 403}:
                raise ValueError("console 会话已失效，请重新登录")
            resp.raise_for_status()
            return resp.json()
