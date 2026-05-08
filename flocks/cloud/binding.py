"""
OSS cloud account binding orchestration (node-side).
"""

from __future__ import annotations

import hashlib
import os
import platform
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

import httpx

from flocks import __version__
from flocks.config.config import Config
from flocks.storage.storage import Storage


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CloudBindingService:
    @classmethod
    async def _get_install_id(cls) -> str:
        key = "cloud:install_id"
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

    @classmethod
    async def _portal_base_url(cls) -> str:
        config_portal_base = None
        try:
            cfg = await Config.get()
            config_portal_base = getattr(cfg, "portal_base_url", None)
        except Exception:
            config_portal_base = None
        raw = (
            config_portal_base
            or os.getenv("FLOCKS_PORTAL_BASE_URL")
            or "http://127.0.0.1:3000"
        ).strip()
        if not raw:
            return "http://127.0.0.1:3000"
        if raw.startswith(("http://", "https://")):
            return raw.rstrip("/")
        return f"http://{raw.rstrip('/')}"

    @staticmethod
    def _activation_base_url() -> str:
        # If the env var exists (even empty), honor it strictly so tests can force mock mode.
        if "FLOCKS_ACT_BASE_URL" in os.environ:
            return os.getenv("FLOCKS_ACT_BASE_URL", "").strip().rstrip("/")
        return "http://127.0.0.1:18000"

    @classmethod
    def _ensure_return_to(cls, login_url: str, return_to: str) -> str:
        parsed = urlparse(login_url)
        query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if not query_pairs.get("return_to"):
            query_pairs["return_to"] = return_to
        if not query_pairs.get("return-to"):
            query_pairs["return-to"] = return_to
        return urlunparse(parsed._replace(query=urlencode(query_pairs)))

    @classmethod
    def _rewrite_login_url_base_if_local(cls, login_url: str, portal_base: str) -> str:
        """
        When local portal is configured, force login redirect to that base.
        This keeps query/path while avoiding accidental jump to production domain.
        """
        parsed_login = urlparse(login_url)
        parsed_portal = urlparse(portal_base)
        if not parsed_portal.netloc:
            return login_url
        if parsed_portal.hostname not in {"127.0.0.1", "localhost"}:
            return login_url
        return urlunparse(
            parsed_login._replace(
                scheme=parsed_portal.scheme or "http",
                netloc=parsed_portal.netloc,
            )
        )

    @classmethod
    async def init_binding(cls, return_to: str) -> dict[str, Any]:
        act_base = cls._activation_base_url()
        portal_base = await cls._portal_base_url()
        binding_id = str(uuid4())
        payload = {
            "binding_id": binding_id,
            "fingerprint": await cls.get_fingerprint(),
            "install_id": await cls._get_install_id(),
            "return_to": return_to,
            "created_at": _now_iso(),
            "status": "pending",
        }

        if act_base:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{act_base}/v1/bindings/init", json=payload)
                resp.raise_for_status()
                data = resp.json()
                binding_id = data.get("binding_id", binding_id)
                portal_login_url = data.get("portal_login_url") or data.get("login_url")
                payload.update({"binding_id": binding_id, "status": "pending_remote"})
        else:
            portal_login_url = f"{portal_base}/auth/login?{urlencode({'binding_id': binding_id})}"

        portal_login_url = cls._rewrite_login_url_base_if_local(str(portal_login_url), portal_base)
        portal_login_url = cls._ensure_return_to(str(portal_login_url), return_to)
        await Storage.set(f"cloud:binding:{binding_id}", payload, "json")
        return {"binding_id": binding_id, "portal_login_url": portal_login_url}

    @classmethod
    async def exchange_binding(
        cls,
        binding_id: str,
        passport_uid: str | None = None,
    ) -> dict[str, Any]:
        act_base = cls._activation_base_url()
        binding = await Storage.get(f"cloud:binding:{binding_id}")
        if not binding:
            raise ValueError("binding_id 不存在或已过期")

        fingerprint = await cls.get_fingerprint()
        install_id = await cls._get_install_id()
        if act_base:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{act_base}/v1/bindings/{binding_id}/exchange",
                    json={
                        "fingerprint": fingerprint,
                        "install_id": install_id,
                    },
                )
                if resp.status_code == 404:
                    # Compatibility: older ACT uses /v1/bindings/exchange
                    resp = await client.post(
                        f"{act_base}/v1/bindings/exchange",
                        json={
                            "binding_id": binding_id,
                            "fingerprint": fingerprint,
                            "install_id": install_id,
                        },
                    )
                resp.raise_for_status()
                data = resp.json()
                token = data.get("cloud_session_token")
                if not token:
                    raise ValueError("云端未返回 cloud_session_token")
                cloud_passport_uid = data.get("passport_uid")
                user_email = data.get("user_email")
                user_display = data.get("user_display")
        else:
            token = f"mock-cloud-session-{binding_id}"
            cloud_passport_uid = passport_uid or binding_id
            user_email = None
            user_display = passport_uid or "local-cloud-user"

        cloud_session = {
            "binding_id": binding_id,
            "cloud_session_token": token,
            "fingerprint": fingerprint,
            "install_id": install_id,
            "passport_uid": cloud_passport_uid or passport_uid,
            "user_email": user_email,
            "user_display": user_display,
            "updated_at": _now_iso(),
        }
        await Storage.set("cloud:session", cloud_session, "json")
        await Storage.set(f"cloud:binding:{binding_id}", {**binding, "status": "exchanged"}, "json")
        return cloud_session

    @classmethod
    async def get_bound_session(cls) -> dict[str, Any] | None:
        raw = await Storage.get("cloud:session")
        if not isinstance(raw, dict):
            return None
        return raw

    @classmethod
    async def clear_bound_session(cls) -> None:
        await Storage.delete("cloud:session")

    @classmethod
    async def require_bound_session(cls) -> dict[str, Any]:
        session = await cls._require_session()
        account_name = session.get("user_display") or session.get("user_email") or session.get("passport_uid")
        if not account_name:
            raise ValueError("云账号未绑定")
        return session

    @classmethod
    async def _require_session(cls) -> dict[str, Any]:
        original = await cls.get_bound_session()
        if not isinstance(original, dict):
            raise ValueError("云账号未绑定")
        session = dict(original)
        token = str(session.get("cloud_session_token") or "").strip()
        fingerprint = str(session.get("fingerprint") or "").strip()
        install_id = str(session.get("install_id") or "").strip()

        # Upgrade legacy/mock local session to real cloud session when ACT is reachable.
        if (
            token.startswith("mock-cloud-session-")
            and cls._activation_base_url()
            and session.get("binding_id")
        ):
            try:
                refreshed = await cls.exchange_binding(
                    binding_id=str(session["binding_id"]),
                    passport_uid=session.get("passport_uid"),
                )
                token = str(refreshed.get("cloud_session_token") or "").strip()
                fingerprint = str(refreshed.get("fingerprint") or "").strip()
                install_id = str(refreshed.get("install_id") or "").strip()
                session = refreshed
            except Exception:
                # Keep existing behavior and surface a clear error below.
                pass

        if not token or not fingerprint or not install_id:
            raise ValueError("云绑定会话无效，请重新绑定")
        if token.startswith("mock-cloud-session-") and cls._activation_base_url():
            raise ValueError("云绑定会话未完成云端 exchange，请重新绑定")
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
        act_base = cls._activation_base_url()
        payload = {
            "fingerprint": session["fingerprint"],
            "install_id": session["install_id"],
            "binding_id": session.get("binding_id"),
            "sent_at": _now_iso(),
            "status": "ok",
        }
        if not act_base:
            return {"ok": True, "mode": "mock", "node": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{act_base}/v1/heartbeats",
                json=payload,
                headers={"Authorization": f"Bearer {session['cloud_session_token']}"},
            )
            if resp.status_code in {401, 403}:
                raise ValueError("云绑定会话已失效，请重新绑定")
            resp.raise_for_status()
            return resp.json()

    @classmethod
    async def sync_node_profile(cls, *, force: bool = False, source: str = "scheduled") -> dict[str, Any]:
        _ = force
        session = await cls._require_session()
        act_base = cls._activation_base_url()
        payload = {
            "fingerprint": session["fingerprint"],
            "install_id": session["install_id"],
            "edition": cls._edition(),
            "version": cls._runtime_version(),
            "source": source,
            "sent_at": _now_iso(),
        }
        if not act_base:
            return {"ok": True, "mode": "mock", "node": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{act_base}/v1/nodes/sync",
                json=payload,
                headers={"Authorization": f"Bearer {session['cloud_session_token']}"},
            )
            if resp.status_code in {401, 403}:
                raise ValueError("云绑定会话已失效，请重新绑定")
            resp.raise_for_status()
            return resp.json()
