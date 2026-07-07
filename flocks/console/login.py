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


def _flocks_root() -> Path:
    return Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))).expanduser()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_pro_bundle_marker() -> dict[str, Any]:
    return _read_json_file(_flocks_root() / "run" / "pro-bundle-installed.json")


def _local_pro_license_path() -> Path:
    return _flocks_root() / "flockspro" / "license.json"


def _read_local_pro_license_state() -> dict[str, Any]:
    return _read_json_file(_local_pro_license_path())


def _read_local_pro_license_id() -> str:
    state = _read_local_pro_license_state()
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    return str(state.get("license_id") or payload.get("license_id") or "").strip()


def _read_local_pro_license_status() -> str:
    state = _read_local_pro_license_state()
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    return str(
        state.get("license_status")
        or state.get("status")
        or payload.get("license_status")
        or payload.get("status")
        or ""
    ).strip()


def _pending_pro_bundle_install_receipt_path() -> Path:
    return _flocks_root() / "run" / "pro-bundle-install-receipt-pending.json"


def _sync_local_pro_license_from_heartbeat_response(data: dict[str, Any]) -> None:
    license_path = _local_pro_license_path()
    state = _read_json_file(license_path)
    now_ts = int(datetime.now(UTC).timestamp())
    if state:
        changed = False
        patch_token = data.get("license_patch") or data.get("latest_patch")
        if isinstance(patch_token, str) and patch_token:
            patches = state.get("patches") if isinstance(state.get("patches"), list) else []
            if patch_token not in patches:
                state["patches"] = [*patches, patch_token]
                changed = True
        if state.get("last_sync_at") != now_ts:
            state["last_sync_at"] = now_ts
            state["last_heartbeat_ok_at"] = now_ts
            changed = True
        if changed:
            license_path.parent.mkdir(parents=True, exist_ok=True)
            license_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    revoked_license_ids = data.get("revoked_license_ids")
    if isinstance(revoked_license_ids, list):
        revocation_path = _flocks_root() / "flockspro" / "revocation.json"
        revocation_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"revoked_license_ids": sorted({str(item) for item in revoked_license_ids})}
        revocation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def read_shared_console_session() -> dict[str, Any] | None:
    path = _shared_console_session_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = str(payload.get("console_session_token") or "").strip()
    fingerprint = str(payload.get("fingerprint") or "").strip()
    install_id = str(payload.get("install_id") or "").strip()
    if not token or not fingerprint or not install_id:
        return None
    expires_at = str(payload.get("expires_at") or "").strip()
    if expires_at:
        try:
            if _parse_iso(expires_at) <= datetime.now(UTC):
                return None
        except ValueError:
            return None
    return payload


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
    def runtime_version_payload(cls, *, pro_component_version: str | None = None) -> dict[str, str]:
        marker = _read_pro_bundle_marker()
        core_version = str(
            marker.get("core_version")
            or cls._runtime_version()
        ).strip()
        bundle_version = str(
            marker.get("bundle_version")
            or ""
        ).strip()
        pro_component_version = str(marker.get("flockspro_component_version") or pro_component_version or "").strip()
        has_pro_bundle = bool(bundle_version or pro_component_version)
        edition = "flockspro" if has_pro_bundle else "oss"
        payload = {
            "edition": edition,
        }
        if core_version:
            payload["core_version"] = core_version
        if edition == "flockspro" and bundle_version:
            payload["bundle_version"] = bundle_version
        if edition == "flockspro" and pro_component_version:
            payload["flockspro_component_version"] = pro_component_version
        return {key: value for key, value in payload.items() if value}

    @classmethod
    def heartbeat_payload(
        cls,
        session: dict[str, Any],
        *,
        status: str = "ok",
        license_id: str | None = None,
        pro_component_version: str | None = None,
    ) -> dict[str, Any]:
        version_payload = cls.runtime_version_payload(pro_component_version=pro_component_version)
        return {
            "fingerprint": session.get("fingerprint"),
            "install_id": session.get("install_id"),
            "console_login_id": session.get("console_login_id"),
            "sent_at": _now_iso(),
            "status": status,
            "license_id": license_id or None,
            **version_payload,
        }

    @classmethod
    async def send_heartbeat_for_session(
        cls,
        *,
        session: dict[str, Any],
        status: str = "ok",
        license_id: str | None = None,
        heartbeat_url: str | None = None,
        report_install_receipt: bool = False,
        pro_component_version: str | None = None,
    ) -> dict[str, Any]:
        console_base = cls.console_base_url()
        payload = cls.heartbeat_payload(
            session,
            status=status,
            license_id=license_id,
            pro_component_version=pro_component_version,
        )
        target_url = heartbeat_url or (f"{console_base}/v1/heartbeats" if console_base else "")
        if not target_url:
            return {"ok": True, "mode": "mock", "node": payload}
        token = str(session.get("console_session_token") or "").strip()
        if not token:
            raise ValueError("console_session_token 缺失，无法发送心跳")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                target_url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code in {401, 403}:
                raise ValueError("console 会话已失效，请重新登录")
            resp.raise_for_status()
            data = resp.json()
            _sync_local_pro_license_from_heartbeat_response(data)
            if report_install_receipt:
                await cls._report_pending_pro_bundle_install_receipt(client=client, session=session)
            return data

    @classmethod
    async def send_heartbeat(cls) -> dict[str, Any]:
        session = await cls._require_session()
        return await cls.send_heartbeat_for_session(
            session=session,
            status=_read_local_pro_license_status() or "ok",
            license_id=_read_local_pro_license_id() or None,
            report_install_receipt=True,
        )

    @classmethod
    async def report_pending_pro_bundle_install_receipt(cls) -> bool:
        try:
            session = await cls._require_session()
        except Exception:
            session = read_shared_console_session()
        if not session:
            return False
        async with httpx.AsyncClient(timeout=10) as client:
            return await cls._report_pending_pro_bundle_install_receipt(client=client, session=session)

    @classmethod
    def _console_base_url_for_session(cls, session: dict[str, Any]) -> str:
        console_base = cls.console_base_url() or str(session.get("console_base_url") or "").strip().rstrip("/")
        if console_base:
            return console_base
        shared_session = read_shared_console_session()
        return str((shared_session or {}).get("console_base_url") or "").strip().rstrip("/")

    @classmethod
    async def _report_pending_pro_bundle_install_receipt(
        cls,
        *,
        client: httpx.AsyncClient,
        session: dict[str, Any],
    ) -> bool:
        console_base = cls._console_base_url_for_session(session)
        token = str(session.get("console_session_token") or "").strip()
        if not console_base or not token:
            return False
        path = _pending_pro_bundle_install_receipt_path()
        payload = _read_json_file(path)
        if not payload:
            return False
        payload = {
            **payload,
            "fingerprint": session.get("fingerprint"),
            "install_id": session.get("install_id"),
            "license_id": payload.get("license_id") or _read_local_pro_license_id() or None,
        }
        try:
            resp = await client.post(
                f"{console_base}/v1/pro-bundles/installations",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code in {200, 201, 202}:
                path.unlink(missing_ok=True)
                return True
        except Exception:
            return False
        return False

    @classmethod
    async def sync_node_profile(cls, *, force: bool = False, source: str = "scheduled") -> dict[str, Any]:
        _ = force
        session = await cls._require_session()
        console_base = cls.console_base_url()
        version_payload = cls.runtime_version_payload()
        payload = {
            "fingerprint": session["fingerprint"],
            "install_id": session["install_id"],
            **version_payload,
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
