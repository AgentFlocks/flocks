"""
This process's identity.

What a process holds -- a session's execution, say -- is recorded under the id
of the process launch that holds it, so a later launch never takes it for its
own.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

_process_owner: Optional[tuple[int, str]] = None


def process_owner_id() -> str:
    """This process launch's id; a forked child gets its own."""
    global _process_owner
    pid = os.getpid()
    if _process_owner is None or _process_owner[0] != pid:
        _process_owner = (pid, f"{pid}:{uuid.uuid4().hex}")
    return _process_owner[1]


__all__ = ["process_owner_id"]
