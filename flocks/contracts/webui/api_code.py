"""A page's server code, read once, and the policy an extension may set over it.

A page plugin's ``api/`` directory is Python that runs inside the server.  The
runtime reads it into one snapshot, names that snapshot by its digest, and
compiles exactly those bytes -- never the files again, nor a compiled cache
next to them -- so whatever was decided about a digest holds for the code
that runs.

With no policy registered, every page's handlers run and each call reads the
secrets its ``routes.yaml`` declares.  An extension narrows that with a
:class:`PageCodePolicy`: it may refuse to run a page's code, and it picks
which of the declared secrets a call reads.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

#: What counts as the page's server code: everything a handler can import or read as configuration.
_CODE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".txt"}


def api_code_snapshot(api_dir: Path) -> dict[str, bytes]:
    """Every code and configuration file under ``api/``, by relative path, read once.

    What a policy is asked about and what is executed are the same bytes: the
    page's modules are compiled from this snapshot.  Compiled caches and
    hidden files are not source and are left out.
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
    """The digest of a snapshot: paths and contents both count, in a fixed order."""
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


class PageCodePolicy(Protocol):
    async def run_refusal(self, page_id: str, code_sha256: str) -> Optional[str]:
        """Why this code may not run now, or None when it may.

        Asked before any of it is imported (module-level code runs on import);
        the message is the 403 the caller gets.
        """

    async def readable_secrets(
        self, page_id: str, code_sha256: str, declared: frozenset[str], user: Any,
    ) -> frozenset[str]:
        """Which of the *declared* secrets this call may read; raising refuses the call."""


_policy: Optional[PageCodePolicy] = None
_policy_lock = threading.Lock()


def register_page_code_policy(policy: PageCodePolicy) -> None:
    """Set the policy over page code; registering again replaces it."""
    global _policy
    with _policy_lock:
        _policy = policy


def unregister_page_code_policy(policy: PageCodePolicy) -> None:
    global _policy
    with _policy_lock:
        if _policy is policy:
            _policy = None


def page_code_policy() -> Optional[PageCodePolicy]:
    with _policy_lock:
        return _policy


__all__ = [
    "PageCodePolicy",
    "api_code_digest",
    "api_code_snapshot",
    "page_code_policy",
    "register_page_code_policy",
    "snapshot_digest",
    "unregister_page_code_policy",
]
