"""Application-owned files associated with persisted sessions."""

from pathlib import Path
import shutil

from flocks.config.config import Config


def session_uploads_dir(session_id: str) -> Path:
    """Return the upload directory for one session, constrained to app data."""

    uploads_root = (Config.get_data_path() / "uploads").resolve()
    target = (uploads_root / session_id).resolve()
    if target == uploads_root or not target.is_relative_to(uploads_root):
        raise ValueError(f"Invalid session ID for upload path: {session_id}")
    return target


def remove_session_uploads(session_id: str) -> bool:
    """Remove one session's upload directory when it exists."""

    target = session_uploads_dir(session_id)
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
