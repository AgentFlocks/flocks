"""Read a deployment tool's last installation result without loading that tool.

The optional, versioned record contains display text only. Reading it never
activates an extension, switches storage, or executes a recovery command.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

from flocks.updater.handoff_progress import redact_credentials, summarize_error

MAX_RECORD_BYTES = 16_384
# A name that says what it holds, however it is written: PGPASSWORD=, "password": , api_key: , auth_token=.
_CREDENTIAL_NAMED = re.compile(  # secret-guard: allow - compiled redaction expression, not a credential
    r"""(?i)(["']?\b[a-z0-9_.-]*(?:password|passwd|pwd|token|api[_-]?key|secret|credential|authorization)[a-z0-9_.-]*["']?\s*[=:]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?:bearer|basic|token)\s+)?[^\s;&,}\]]+)"""
)
_CREDENTIAL_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{8,}")  # secret-guard: allow - compiled redaction expression


def scrub_credentials(value: str) -> str:
    """Mask credentials wherever an install's output has them: what is shown of it keeps its lines."""
    value = _CREDENTIAL_NAMED.sub(lambda match: match.group(1) + "***", value)
    value = _CREDENTIAL_SCHEME.sub(lambda match: match.group(1) + " ***", value)
    return redact_credentials(value)


def _display_text(value: str) -> str:
    return summarize_error(scrub_credentials(value))


def installation_result_path() -> Path:
    root = Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks").expanduser()
    return root / "run" / "installation-result.json"


def installation_result_id() -> str | None:
    """The id of the record there is, whatever it says (each one the installer writes has its own);
    None when there is none. Raises when there is one that cannot be read."""
    path = installation_result_path()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        record = json.loads(stream.read(MAX_RECORD_BYTES + 1))
    return str(record.get("id")) if isinstance(record, dict) else ""


def source_upgrade_notice_path() -> Path:
    return installation_result_path().with_name("source-upgrade-notice.json")


def write_source_upgrade_notice(
    *,
    target_version: str,
    current_version: str,
    source: str,
    service_running: bool,
    detail: str,
    backup_path: str,
) -> None:
    """How an in-place upgrade of the source ended, for whoever opens the page next.

    *source* says how the program was left: ``restored``, ``untouched`` or ``mixed``.
    Kept apart from the installer's record, which the enterprise upgrade tells its attempts apart by.
    """
    target, current = target_version.lstrip("v"), current_version.lstrip("v")
    retry_zh, retry_en = "把下面的原因处理掉之后可以重新升级。", "Resolve the failure below, then upgrade again."
    if source == "restored" and service_running:
        recovery = "restored"
        zh = {
            "title": f"升级到 v{target} 没有完成，已恢复到 v{current}",
            "message": "程序和界面已恢复到升级之前的版本，数据没有动，可以接着用。",
            "retry_hint": retry_zh,
        }
        en = {
            "title": f"The upgrade to v{target} did not complete; v{current} was restored",
            "message": "The program and the interface are back to the version before the upgrade and data was not touched. You can keep working.",
            "retry_hint": retry_en,
        }
    elif source == "untouched" and service_running:
        recovery = "unchanged"
        zh = {
            "title": f"升级到 v{target} 没有开始",
            "message": "程序和数据都没有动，可以接着用。",
            "retry_hint": retry_zh,
        }
        en = {
            "title": f"The upgrade to v{target} did not start",
            "message": "The program and data were not touched, and you can keep working.",
            "retry_hint": retry_en,
        }
    elif source in ("restored", "untouched"):
        recovery = "failed"
        zh = {
            "title": f"升级到 v{target} 没有完成，服务也没能重新启动",
            "message": f"程序是 v{current}，数据没有动，但是服务没能重新启动过。",
            "retry_hint": "如果这条提示是在手工启动服务之后看到的，可以把下面的原因处理掉再重新升级。",
        }
        en = {
            "title": f"The upgrade to v{target} did not complete and the service did not come back by itself",
            "message": f"The program is v{current} and data was not touched, but the service could not be restarted at the time.",
            "retry_hint": "If the service was started by hand since, resolve the failure below, then upgrade again.",
        }
    else:
        recovery = "failed"
        zh = {
            "title": f"升级到 v{target} 没有完成，也没能自动恢复",
            "message": f"程序目录可能是新旧混合的状态。升级前的程序备份在 {backup_path}，数据没有动。",
            "retry_hint": "把下面的原因处理掉之后重新运行 scripts/install.sh，或者把备份解回程序目录。",
        }
        en = {
            "title": f"The upgrade to v{target} did not complete and could not be undone",
            "message": f"The program directory may hold a mix of both versions. The program from before the upgrade is backed up at {backup_path}. Data was not touched.",
            "retry_hint": "Resolve the failure below, then run scripts/install.sh again or unpack the backup over the program directory.",
        }
    record = {
        "version": 1,
        "id": f"source-upgrade-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
        "phase": "failed",
        "recovery": recovery,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        # A mix of both versions reads as either, whichever way it is asked.
        "applies_to": [current, target] if source == "mixed" else [current],
        # What is shown of it anyway: tool output can be longer than the reader accepts a record to be.
        "detail": _display_text(detail),
        "messages": {"zh-CN": zh, "en-US": en},
    }
    path = source_upgrade_notice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    partial.replace(path)


def clear_source_upgrade_notice() -> None:
    source_upgrade_notice_path().unlink(missing_ok=True)


def _source_upgrade_notice_applies(record: dict) -> bool:
    """A notice is about the version it left behind; any later upgrade makes it history."""
    from flocks.updater.updater import get_current_version

    applies_to = record.get("applies_to")
    return isinstance(applies_to, list) and get_current_version() in applies_to


def _recorded_at(result: dict) -> datetime:
    try:
        recorded = datetime.fromisoformat(str(result.get("updated_at") or ""))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return recorded if recorded.tzinfo else recorded.replace(tzinfo=timezone.utc)


def read_installation_result(locale: str = "zh-CN") -> dict | None:
    """The latest failed attempt on record, an installer's or an in-place upgrade's."""
    results = [
        _read_result_record(installation_result_path(), locale),
        _read_result_record(source_upgrade_notice_path(), locale, applies=_source_upgrade_notice_applies),
    ]
    return max((result for result in results if result is not None), key=_recorded_at, default=None)


def _read_result_record(path: Path, locale: str, applies=None) -> dict | None:
    try:
        if path.is_symlink():
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
        return None
    if record.get("phase") != "failed" or not isinstance(record.get("id"), str):
        return None
    if applies is not None and not applies(record):
        return None
    language = "zh-CN" if locale.lower().startswith("zh") else "en-US"
    texts = record.get("messages")
    text = texts.get(language) if isinstance(texts, dict) else None
    if not isinstance(text, dict):
        return None
    if not all(isinstance(text.get(key), str) and text[key].strip() for key in ("title", "message")):
        return None
    recovery = record.get("recovery")
    if not isinstance(recovery, str):
        return None
    result = {
        "id": record["id"][:160],
        "phase": "failed",
        "recovery": recovery if recovery in {"restored", "unchanged", "retained", "failed"} else "failed",
        "updated_at": str(record.get("updated_at") or "")[:80],
        "title": _display_text(text["title"]),
        "message": _display_text(text["message"]),
        "retry_hint": _display_text(str(text.get("retry_hint") or "")),
        "detail": _display_text(str(record.get("detail") or "")),
    }
    return result
