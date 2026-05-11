"""
Disk-backed state stores for the Weixin channel:

- ``ContextTokenStore`` — per-account, per-peer ``context_token`` cache
  required to maintain conversation continuity with the iLink server.
- ``MessageDedup`` — in-memory dedup with TTL-based pruning.
- ``sync_buf`` helpers — long-poll cursor persistence.

State files default to ``~/.flocks/workspace/channels/weixin/`` but the channel
can override the root via the ``dataDir`` config key (useful for multi-profile
setups). When ``dataDir`` is set it is used as-is (no ``weixin/`` sub-dir is
appended).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from flocks.utils.log import Log

from .config import MESSAGE_DEDUP_TTL_SECONDS

log = Log.create(service="channel.weixin.store")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def state_dir(data_dir: Optional[str] = None) -> Path:
    if data_dir:
        return Path(data_dir)
    return Path.home() / ".flocks" / "workspace" / "channels" / "weixin"


def ensure_state_dir(data_dir: Optional[str] = None) -> Path:
    path = state_dir(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Sync-buf cursor (long-poll position)
# ---------------------------------------------------------------------------

def load_sync_buf(account_id: str, data_dir: Optional[str] = None) -> str:
    path = state_dir(data_dir) / f"{account_id}.sync.json"
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("get_updates_buf", "")
    except Exception:
        return ""


def save_sync_buf(account_id: str, sync_buf: str, data_dir: Optional[str] = None) -> None:
    try:
        path = ensure_state_dir(data_dir) / f"{account_id}.sync.json"
        path.write_text(json.dumps({"get_updates_buf": sync_buf}), encoding="utf-8")
    except Exception as exc:
        log.warning("weixin.sync_buf.save_error", {"error": str(exc)})


# ---------------------------------------------------------------------------
# Per-peer context token cache
# ---------------------------------------------------------------------------

class ContextTokenStore:
    """Disk-backed ``context_token`` cache keyed by ``(account_id, user_id)``."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._root = state_dir(data_dir)
        self._cache: dict[str, str] = {}

    def _path(self, account_id: str) -> Path:
        return self._root / f"{account_id}.context-tokens.json"

    @staticmethod
    def _key(account_id: str, user_id: str) -> str:
        return f"{account_id}:{user_id}"

    def restore(self, account_id: str) -> None:
        path = self._path(account_id)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        for user_id, token in data.items():
            if isinstance(token, str) and token:
                self._cache[self._key(account_id, user_id)] = token

    def get(self, account_id: str, user_id: str) -> Optional[str]:
        return self._cache.get(self._key(account_id, user_id))

    def set(self, account_id: str, user_id: str, token: str) -> None:
        self._cache[self._key(account_id, user_id)] = token
        self._persist(account_id)

    def clear(self, account_id: str, user_id: str) -> None:
        """Drop a stale token (called on session-expired errors)."""
        if self._cache.pop(self._key(account_id, user_id), None) is not None:
            self._persist(account_id)

    def _persist(self, account_id: str) -> None:
        prefix = f"{account_id}:"
        payload = {
            key[len(prefix):]: value
            for key, value in self._cache.items()
            if key.startswith(prefix)
        }
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            self._path(account_id).write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            log.warning("weixin.context_token.persist_error", {"error": str(exc)})


# ---------------------------------------------------------------------------
# In-memory dedup with TTL pruning
# ---------------------------------------------------------------------------

class MessageDedup:
    """Track recent message ids / content hashes to drop redelivered messages."""

    def __init__(self, ttl_seconds: float = MESSAGE_DEDUP_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self._ttl
        seen_at = self._seen.get(key)
        if seen_at is not None and seen_at > cutoff:
            return True
        # Prune stale entries lazily every ~100 inserts to bound memory growth.
        if len(self._seen) >= 100 and len(self._seen) % 100 == 0:
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        self._seen[key] = now
        return False
