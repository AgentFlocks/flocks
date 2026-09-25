"""Process-shared voice settings; credentials never leave the server."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

RESOURCE_ID = "volc.seedasr.sauc.duration"
MODEL = "2.0"
KEY_ENV = "FLOCKS_DOUBAO_ASR_API_KEY"
MAX_HOTWORDS = 100
MAX_HOTWORD_CHARS = 64
MAX_CONFIG_BYTES = 65_536
_legacy_path: Callable[[], Path] | None = None
_legacy_key: Callable[[], str] | None = None


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    credential_source: str = "none"
    hotwords: tuple[str, ...] = ()
    capture_mode: str = "voice"

    def public(self, *, admin: bool = False) -> dict:
        configured = bool(self.api_key)
        result = {
            "enabled": self.enabled,
            "configured": configured,
            "available": self.enabled and configured,
            "capture_mode": self.capture_mode,
        }
        if admin:
            result.update(resource_id=RESOURCE_ID, model=MODEL,
                          credential_source=self.credential_source,  # secret-guard: allow
                          hotwords=list(self.hotwords))
        return result


def settings_path() -> Path:
    root = Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))).expanduser()
    return root / "voice-config.json"


def register_settings_fallback(
    path: Callable[[], Path], key: Callable[[], str],
) -> None:
    """Let an extension supply its former settings during a core migration.

    Registration happens at application startup. New settings always win;
    the source is never rewritten or deleted.
    """
    global _legacy_path, _legacy_key
    _legacy_path, _legacy_key = path, key


def _valid_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        return ""
    return value


def _hotwords(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_HOTWORDS:
        raise ValueError("热词最多支持 100 项")
    result: dict[str, None] = {}
    for word in value:
        if not isinstance(word, str) or not 1 <= len(word.strip()) <= MAX_HOTWORD_CHARS:
            raise ValueError("每项热词应为 1 至 64 个字符")
        if any(ord(char) < 32 or ord(char) == 127 for char in word):
            raise ValueError("热词不能包含控制字符")
        result[word.strip()] = None
    return tuple(result)


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _stored() -> dict:
    path = settings_path()
    # Existing installations keep working without moving or rewriting the old
    # credential file. Once core settings exist, even an empty/invalid file must
    # take precedence, so a removed key cannot reappear from legacy storage.
    if path.exists():
        return _read(path)
    return _read(_legacy_path()) if _legacy_path else {}


def _resolve(data: dict) -> VoiceSettings:
    environment = _valid_key(os.getenv(KEY_ENV, "")) or _valid_key(_legacy_key() if _legacy_key else "")
    stored = _valid_key(data.get("api_key"))
    key = environment or stored
    try:
        hotwords = _hotwords(data.get("hotwords", []))
    except ValueError:
        hotwords = ()
    return VoiceSettings(
        enabled=data.get("enabled") is True and bool(key),
        api_key=key,
        credential_source="environment" if environment else "stored" if stored else "none",  # secret-guard: allow
        hotwords=hotwords,
        capture_mode="raw" if data.get("capture_mode") == "raw" else "voice",
    )


def read_settings() -> VoiceSettings:
    return _resolve(_stored())


def update_settings(changes: dict) -> VoiceSettings:
    """Atomically update core settings, leaving any legacy file as a backup."""
    if set(changes) - {"enabled", "api_key", "hotwords", "capture_mode"}:
        raise ValueError("语音配置字段无效")
    path = settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=5, mode=0o600):
        previous = _stored()
        resolved = _resolve(previous)
        data = {"enabled": resolved.enabled,
                "api_key": _valid_key(previous.get("api_key")),
                "hotwords": list(resolved.hotwords), "capture_mode": resolved.capture_mode}
        deleting = False
        if "api_key" in changes:
            raw = changes["api_key"]
            if not isinstance(raw, str):
                raise ValueError("API Key 必须是文本")
            key = _valid_key(raw)
            if raw.strip() and not key:
                raise ValueError("API Key 格式无效")
            data["api_key"] = key
            deleting = not key
        if "enabled" in changes:
            if type(changes["enabled"]) is not bool:
                raise ValueError("语音开关必须是布尔值")
            data["enabled"] = changes["enabled"]
        if "hotwords" in changes:
            data["hotwords"] = list(_hotwords(changes["hotwords"]))
        if "capture_mode" in changes:
            if changes["capture_mode"] not in ("voice", "raw"):
                raise ValueError("录音模式无效")
            data["capture_mode"] = changes["capture_mode"]
        if deleting:
            data["enabled"] = False
        if data["enabled"] and not _resolve(data).api_key:
            raise ValueError("请先配置豆包 ASR API Key，再开启语音输入")
        descriptor, temporary = tempfile.mkstemp(prefix=".voice-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return _resolve(data)
