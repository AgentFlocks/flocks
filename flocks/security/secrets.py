"""
Simple secret management using flat KV JSON file

Stores all secrets in ~/.flocks/config/.secret.json as a flat {secret_id: secret_value} dict.
For MVP/development use only. For production, consider encryption.

Naming conventions for secret IDs:
    - LLM provider keys:  "{provider_id}_llm_key"  e.g. "anthropic_llm_key"
    - API service keys:   "{service_id}_api_key"   e.g. "virustotal_api_key"
    - MCP server keys:    "{server_name}_mcp_key"  e.g. "threatbook_mcp_key"

Usage:
    secrets = SecretManager()
    secrets.set("anthropic_llm_key", "sk-xxx")
    api_key = secrets.get("anthropic_llm_key")
"""

import contextvars
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

from filelock import FileLock

from flocks.config.config import Config
from flocks.utils.log import Log

log = Log.create(service="security.secrets")


def secret_file_lock(secret_file: Path) -> FileLock:
    """The lock every writer of ``secret_file`` takes, across threads and processes."""
    return FileLock(str(secret_file.with_suffix(".lock")), timeout=5, mode=0o600)


def replace_secret_file(secret_file: Path, text: str) -> None:
    """Replace the file atomically, so no reader ever sees a partial write.

    A partial read parses as nothing; a writer that then saved would drop
    every other secret. mkstemp creates the replacement with mode 600.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=".secret-", suffix=".json", dir=secret_file.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, secret_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_secret_file(secret_file: Path, text: str) -> bool:
    """Create ``secret_file`` with ``text`` unless another writer already did; True if created."""
    secret_file = Path(os.path.realpath(secret_file))  # secret-guard: allow (a path, not a secret)
    if secret_file.exists():
        return False
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    with secret_file_lock(secret_file):
        if secret_file.exists():
            return False
        replace_secret_file(secret_file, text)
        return True


# A key that is not in the file, as opposed to one holding any value.
_ABSENT = object()


class SecretChanges:
    """The secrets one operation wrote, so a failed operation takes back exactly those.

    Restoring a snapshot of the whole file instead would also undo what another
    request or the CLI wrote in the meantime.
    """

    def __init__(self, parent: Optional["SecretChanges"] = None) -> None:
        self._parent = parent
        # (secret file, secret id) -> (value before the first write, value the operation left)
        self._writes: Dict[tuple[Path, str], tuple[object, object]] = {}

    def record(self, secret_file: Path, secret_id: str, before: object, after: object) -> None:
        """Called under the file's write lock, so records follow the order of the writes."""
        key = (secret_file, secret_id)
        recorded = self._writes.get(key)
        # Taking these writes back returns to what was there before the first of
        # them -- or, if someone else wrote the key between two of them, to that.
        baseline = recorded[0] if recorded is not None and recorded[1] == before else before
        self._writes[key] = (baseline, after)
        if self._parent is not None:
            # An enclosing operation that fails later takes these writes back as well.
            self._parent.record(secret_file, secret_id, before, after)

    def revert(self) -> list[str]:
        """Give each written key its earlier value; return the keys left as they are
        because someone else wrote them since."""
        by_file: Dict[Path, Dict[str, tuple[object, object]]] = {}
        for (secret_file, secret_id), change in self._writes.items():
            by_file.setdefault(secret_file, {})[secret_id] = change
        kept: list[str] = []
        for secret_file, changes in by_file.items():
            # An enclosing operation hears of each restore under the same lock,
            # before any later write: its own records stay in order.
            notify = self._parent.record if self._parent is not None else None
            left = SecretManager(secret_file)._restore(changes, notify)
            # Forgotten once restored: a revert that fails can be tried again.
            for secret_id in changes:
                del self._writes[(secret_file, secret_id)]
            kept.extend(left)
        return kept


_recording: contextvars.ContextVar[Optional[SecretChanges]] = contextvars.ContextVar(
    "flocks_secret_changes", default=None,
)


@contextmanager
def recording_secret_changes() -> Iterator[SecretChanges]:
    """Record the secrets written in this context, including by tasks and
    ``asyncio.to_thread`` calls started from it (they copy the context)."""
    changes = SecretChanges(_recording.get())
    previous = _recording.set(changes)
    try:
        yield changes
    finally:
        _recording.reset(previous)


def get_mcp_secret_id(server_name: str) -> str:
    """Return the canonical MCP secret ID without duplicating an `_mcp` suffix."""
    return f"{server_name}_key" if server_name.endswith("_mcp") else f"{server_name}_mcp_key"


class SecretManager:
    """
    Flat KV secret manager using plain JSON file

    Stores secrets in ~/.flocks/config/.secret.json with 600 permissions.
    Format: {"secret_id": "secret_value", ...}

    Naming conventions:
        LLM provider keys:  "{provider_id}_llm_key"  e.g. "anthropic_llm_key"
        API service keys:   "{service_id}_api_key"   e.g. "virustotal_api_key"
        MCP server keys:    "{server_name}_mcp_key"  e.g. "threatbook_mcp_key"

    Usage:
        secrets = SecretManager()
        secrets.set("anthropic_llm_key", "sk-xxx")
        api_key = secrets.get("anthropic_llm_key")
        secrets.delete("anthropic_llm_key")
    """

    def __init__(self, secret_file: Optional[Path] = None):
        """
        Initialize secret manager.

        Args:
            secret_file: Path to secret file. Defaults to ~/.flocks/config/.secret.json
                         (or the directory specified via FLOCKS_CONFIG_DIR).
        """
        if secret_file is None:
            secret_file = Config.get_secret_file()

        # Writes replace the file; through a symlink they must replace its target.
        self.secret_file = Path(os.path.realpath(secret_file))  # secret-guard: allow (a path, not a secret)
        self._ensure_secure()

    def _lock(self) -> FileLock:
        """One writer at a time, across threads and processes (server, CLI)."""
        return secret_file_lock(self.secret_file)

    def _record(self, secret_id: str, before: object, after: object) -> None:
        changes = _recording.get()
        if changes is not None:
            changes.record(self.secret_file, secret_id, before, after)

    def _ensure_secure(self) -> None:
        """Ensure secret file exists with secure permissions."""
        # Another process may create (and fill) it meanwhile: checked again under the lock.
        if initialize_secret_file(self.secret_file, "{}"):
            log.info("secrets.created", {"path": str(self.secret_file)})

        # Set secure permissions (600: owner read/write only)
        try:
            current_mode = self.secret_file.stat().st_mode & 0o777
            if current_mode != 0o600:
                self.secret_file.chmod(0o600)
                log.info("secrets.permissions_fixed", {
                    "path": str(self.secret_file),
                    "old_mode": oct(current_mode),
                    "new_mode": "0o600",
                })
        except Exception as e:
            log.warning("secrets.chmod_failed", {"error": str(e)})

    def _load(self) -> Dict[str, str]:
        """Load secrets from file."""
        try:
            if self.secret_file.exists():
                content = self.secret_file.read_text()
                if not content.strip():
                    return {}
                return json.loads(content)
            return {}
        except json.JSONDecodeError as e:
            log.error("secrets.load_failed", {
                "path": str(self.secret_file),
                "error": str(e),
            })
            return {}
        except Exception as e:
            log.error("secrets.load_error", {"error": str(e)})
            return {}

    def _save(self, data: Dict[str, str]) -> None:
        """Save secrets; callers hold the lock."""
        try:
            replace_secret_file(self.secret_file, json.dumps(data, indent=2))
            log.debug("secrets.saved", {"path": str(self.secret_file)})
        except Exception as e:
            log.error("secrets.save_failed", {"error": str(e)})
            raise

    def get(self, secret_id: str) -> Optional[str]:
        """
        Get secret value by ID.

        Args:
            secret_id: Secret identifier (e.g., "anthropic_llm_key")

        Returns:
            Secret value or None if not found
        """
        # Per-device credential context takes priority over global secrets.
        try:
            from flocks.tool.credential_context import get_secret_override
            override = get_secret_override(secret_id)
            if override is not None:
                return override
        except Exception:
            pass

        data = self._load()
        value = data.get(secret_id)
        if value:
            log.debug("secrets.get", {"secret_id": secret_id})
        return value

    def set(self, secret_id: str, value: str) -> None:
        """
        Set secret value.

        Args:
            secret_id: Secret identifier (e.g., "anthropic_llm_key")
            value: Secret value
        """
        # Read-modify-write of the whole file: unlocked, two writers keep only one change.
        with self._lock():
            data = self._load()
            before = data.get(secret_id, _ABSENT)
            data[secret_id] = value
            self._save(data)
            self._record(secret_id, before, value)
        log.info("secrets.set", {"secret_id": secret_id})

    def _restore(
        self,
        changes: Dict[str, tuple[object, object]],
        notify: Optional[Callable[[Path, str, object, object], None]] = None,
    ) -> list[str]:
        """Put back the value each key had before ``changes`` (see SecretChanges.revert).

        A key that no longer holds what was written keeps its current value; the
        keys kept are returned. ``notify(file, key, value replaced, value put
        back)`` hears of each restore while the lock is still held.
        """
        restored: Dict[str, tuple[object, object]] = {}
        kept: list[str] = []
        with self._lock():
            data = self._load()
            for secret_id, (before, after) in changes.items():
                current = data.get(secret_id, _ABSENT)
                if current != after:
                    kept.append(secret_id)
                    continue
                if before is _ABSENT:
                    data.pop(secret_id, None)
                else:
                    data[secret_id] = before
                restored[secret_id] = (current, before)
            if restored:
                self._save(data)
                if notify is not None:
                    for secret_id, (previous, value) in restored.items():
                        notify(self.secret_file, secret_id, previous, value)
        log.info("secrets.reverted", {"secret_ids": sorted(restored), "kept": kept})
        return kept

    def delete(self, secret_id: str) -> bool:
        """
        Delete a secret.

        Args:
            secret_id: Secret identifier

        Returns:
            True if deleted, False if not found
        """
        with self._lock():
            data = self._load()
            if secret_id not in data:
                return False
            before = data.pop(secret_id)
            self._save(data)
            self._record(secret_id, before, _ABSENT)
        log.info("secrets.deleted", {"secret_id": secret_id})
        return True

    def list(self) -> list[str]:
        """
        List all secret IDs.

        Returns:
            List of secret IDs
        """
        data = self._load()
        return list(data.keys())

    def has(self, secret_id: str) -> bool:
        """
        Check if secret exists.

        Args:
            secret_id: Secret identifier

        Returns:
            True if secret exists
        """
        data = self._load()
        return secret_id in data

    @staticmethod
    def mask(value: str, show_chars: int = 4) -> str:
        """
        Mask secret for display (e.g., sk-***abc123).

        Args:
            value: Secret value to mask
            show_chars: Number of chars to show at end

        Returns:
            Masked string
        """
        if not value:
            return ""

        if len(value) <= show_chars + 3:
            return "***"

        prefix = value[:3] if value.startswith(("sk-", "pk-")) else value[:2]
        suffix = value[-show_chars:]
        return f"{prefix}***{suffix}"


# Singleton instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get singleton secret manager instance."""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
    return _secret_manager
