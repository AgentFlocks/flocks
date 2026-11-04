"""
Feishu message persistent deduplication.

Two-layer architecture:
- Memory layer (OrderedDict, LRU eviction): fast path, avoids I/O on every check
- File layer (JSON, TTL 24h): persists dedup state across restarts to prevent
  reprocessing messages after WebSocket reconnects

File path: {data_dir}/feishu/dedup/{account_id}.json
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from flocks.utils.log import Log

log = Log.create(service="channel.feishu.dedup")

_DEFAULT_TTL_SECONDS = 86400        # 24h
_SYNTHETIC_TTL_SECONDS = 600        # 10 minutes
_REPLAY_TTL_SECONDS = 300           # 5 minutes
_DEFAULT_MEMORY_MAX = 1_000
_DEFAULT_FILE_MAX = 10_000
_FLUSH_INTERVAL_SECONDS = 300       # 5-minute periodic flush


def _default_data_dir() -> Path:
    """Return the default data directory (FLOCKS_DATA_DIR env var takes priority)."""
    from flocks.config.config import _get_data_dir

    # Honors FLOCKS_DATA_DIR / XDG_DATA_HOME / FLOCKS_ROOT like every other data path.
    return _get_data_dir()


def _safe_account_id(account_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", account_id) or "default"


class FeishuDedup:
    """Feishu message two-layer persistent deduplicator.

    Usage::

        dedup = FeishuDedup(account_id="default")
        await dedup.warmup()                       # pre-warm from disk on startup

        if await dedup.is_duplicate(message_id):
            return                                 # skip duplicate message

        # ... process message ...

        # Flush periodically in background, or call manually in finally block
        await dedup.flush()
    """

    def __init__(
        self,
        account_id: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        memory_max: int = _DEFAULT_MEMORY_MAX,
        file_max: int = _DEFAULT_FILE_MAX,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._account_id = account_id
        self._ttl = ttl_seconds
        self._memory_max = memory_max
        self._file_max = file_max
        self._data_dir = data_dir or _default_data_dir()
        self._file_path = (
            self._data_dir
            / "feishu"
            / "dedup"
            / f"{_safe_account_id(account_id)}.json"
        )

        # Memory layer: message_id -> timestamp_ms (float)
        self._memory: OrderedDict[str, float] = OrderedDict()
        # Dirty flag: skip unnecessary file writes when nothing has changed
        self._dirty = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def warmup(self) -> int:
        """Load dedup records from disk into memory.

        Returns the number of valid records loaded (expired entries are skipped).
        Should be called once before each account's WebSocket starts.
        """
        async with self._lock:
            return self._load_from_file()

    async def is_duplicate(self, message_id: str) -> bool:
        """Check whether message_id is a duplicate.

        - Not a duplicate: record and return False
        - Duplicate: return True

        Note: empty message_id skips dedup and always returns False.
        """
        if not message_id:
            return False
        async with self._lock:
            return self._check_and_record(message_id)

    async def flush(self) -> None:
        """Write memory increments to file (call periodically or before process exit)."""
        async with self._lock:
            if self._dirty:
                self._write_to_file()
                self._dirty = False

    async def start_background_flush(self) -> asyncio.Task:
        """Start a background periodic flush task; returns the Task for external cancellation."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
                try:
                    await self.flush()
                except Exception as e:
                    log.warning("feishu.dedup.flush_error", {
                        "account_id": self._account_id, "error": str(e),
                    })

        return asyncio.create_task(_loop(), name=f"feishu-dedup-flush-{self._account_id}")

    # ------------------------------------------------------------------
    # Internal implementation (all called under _lock)
    # ------------------------------------------------------------------

    def _now_ms(self) -> float:
        return time.time() * 1000

    def _ttl_for_message_id(self, message_id: str) -> int:
        if message_id.startswith("replay:"):
            return _REPLAY_TTL_SECONDS
        if message_id.startswith("synthetic:") or message_id.startswith("card-action:"):
            return _SYNTHETIC_TTL_SECONDS
        return self._ttl

    def _is_expired(self, message_id: str, ts_ms: float) -> bool:
        return (self._now_ms() - ts_ms) > self._ttl_for_message_id(message_id) * 1000

    def _check_and_record(self, message_id: str) -> bool:
        """Memory-layer dedup check. True = duplicate."""
        now = self._now_ms()

        # Evict expired entries first (keep memory clean)
        self._evict_expired(now)

        if message_id in self._memory:
            # Update access order (move_to_end keeps most-recently-used at the tail)
            self._memory.move_to_end(message_id)
            return True

        # Record
        self._memory[message_id] = now
        self._dirty = True

        # Capacity eviction: remove earliest inserted entry (approximate LRU)
        while len(self._memory) > self._memory_max:
            self._memory.popitem(last=False)

        return False

    def _evict_expired(self, now: float) -> None:
        to_delete = [
            k for k, ts in self._memory.items()
            if (now - ts) > self._ttl_for_message_id(k) * 1000
        ]
        for k in to_delete:
            del self._memory[k]

    def _load_from_file(self) -> int:
        """Read file and pre-warm memory layer; returns loaded count."""
        if not self._file_path.exists():
            # Clean up any leftover temp file from an interrupted write
            tmp_path = self._file_path.with_suffix(".tmp")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return 0

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            data: dict = json.loads(raw)
        except Exception as e:
            log.warning("feishu.dedup.load_error", {
                "account_id": self._account_id,
                "path": str(self._file_path),
                "error": str(e),
            })
            return 0

        now = self._now_ms()
        loaded = 0
        for msg_id, ts_ms in data.items():
            if isinstance(ts_ms, (int, float)) and not self._is_expired(msg_id, float(ts_ms)):
                if msg_id not in self._memory:
                    self._memory[msg_id] = float(ts_ms)
                    loaded += 1

        log.info("feishu.dedup.warmup", {
            "account_id": self._account_id, "loaded": loaded,
        })
        return loaded

    def _write_to_file(self) -> None:
        """Serialize memory records to file (atomic write: write to temp file then rename)."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

            now = self._now_ms()
            # Keep only non-expired entries; sort by time and take the newest file_max entries
            valid: list[tuple[str, float]] = [
                (k, ts) for k, ts in self._memory.items()
                if not self._is_expired(k, ts)
            ]
            valid.sort(key=lambda x: x[1])
            if len(valid) > self._file_max:
                valid = valid[-self._file_max:]

            data = {k: ts for k, ts in valid}
            content = json.dumps(data, separators=(",", ":"))

            # Atomic write: write to temp file then rename to avoid corruption on crash
            tmp_path = self._file_path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self._file_path)

        except Exception as e:
            log.warning("feishu.dedup.write_error", {
                "account_id": self._account_id,
                "path": str(self._file_path),
                "error": str(e),
            })


# ------------------------------------------------------------------
# Global registry: one FeishuDedup instance per account_id
# ------------------------------------------------------------------

_registry: dict[str, FeishuDedup] = {}
_registry_lock = asyncio.Lock()


async def get_dedup(
    account_id: str,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    data_dir: Optional[Path] = None,
) -> FeishuDedup:
    """Return (or lazily create) the FeishuDedup instance for the given account."""
    if account_id in _registry:
        return _registry[account_id]
    async with _registry_lock:
        if account_id not in _registry:
            _registry[account_id] = FeishuDedup(
                account_id=account_id,
                ttl_seconds=ttl_seconds,
                data_dir=data_dir,
            )
        return _registry[account_id]
