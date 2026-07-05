"""Persisted installer language profile for CLI maintenance commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

INSTALL_PROFILE_FILE = "install_profile.json"
INSTALL_PROFILE_LANGUAGE_KEY = "Language"
DEFAULT_INSTALL_LANGUAGE = "en"
CN_INSTALL_LANGUAGE = "zh-CN"


def install_profile_path() -> Path:
    """Return the install profile path under the Flocks config directory."""
    config_dir = os.getenv("FLOCKS_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / INSTALL_PROFILE_FILE

    root = os.getenv("FLOCKS_ROOT")
    if root:
        return Path(root).expanduser() / "config" / INSTALL_PROFILE_FILE

    return Path.home() / ".flocks" / "config" / INSTALL_PROFILE_FILE


def normalize_install_language(value: str | None) -> str:
    """Normalize a persisted or environment installer language value."""
    language = (value or "").strip()
    if _is_cn_language(language):
        return CN_INSTALL_LANGUAGE
    if language:
        return language
    return DEFAULT_INSTALL_LANGUAGE


def read_install_language() -> str:
    """Read the persisted installer language, falling back to the environment."""
    path = install_profile_path()
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return normalize_install_language(_string_value(payload.get(INSTALL_PROFILE_LANGUAGE_KEY)))
    except (OSError, json.JSONDecodeError):
        pass

    return normalize_install_language(os.getenv("FLOCKS_INSTALL_LANGUAGE"))


def is_cn_install_language(language: str | None = None) -> bool:
    """Return whether *language* or the persisted profile selects China mirrors."""
    return _is_cn_language(language or read_install_language())


def cn_installer_environment() -> dict[str, str]:
    """Return environment variables equivalent to the zh source installer wrapper."""
    return {
        "FLOCKS_INSTALL_LANGUAGE": CN_INSTALL_LANGUAGE,
        "FLOCKS_INSTALL_REPO_URL": "https://gitee.com/flocks/flocks.git",
        "FLOCKS_RAW_INSTALL_SH_URL": "https://gitee.com/flocks/flocks/raw/main/install_zh.sh",
        "FLOCKS_RAW_INSTALL_PS1_URL": "https://gitee.com/flocks/flocks/raw/main/install_zh.ps1",
        "FLOCKS_UV_DEFAULT_INDEX": "https://mirrors.aliyun.com/pypi/simple",
        "FLOCKS_UV_INSTALL_SH_URL": "https://astral.org.cn/uv/install.sh",
        "FLOCKS_UV_INSTALL_SH_FALLBACK_URL": "https://uv.agentsmirror.com/install-cn.sh",
        "FLOCKS_UV_INSTALL_SH_SECONDARY_FALLBACK_URL": "https://astral.sh/uv/install.sh",
        "FLOCKS_UV_INSTALL_PS1_URL": "https://astral.org.cn/uv/install.ps1",
        "FLOCKS_UV_INSTALL_PS1_FALLBACK_URL": "https://uv.agentsmirror.com/install-cn.ps1",
        "FLOCKS_UV_INSTALL_PS1_SECONDARY_FALLBACK_URL": "https://astral.sh/uv/install.ps1",
        "FLOCKS_NPM_REGISTRY": "https://registry.npmmirror.com/",
        "FLOCKS_NVM_INSTALL_SCRIPT_URL": "https://gitee.com/mirrors/nvm/raw/v0.40.3/install.sh",
        "PUPPETEER_CHROME_DOWNLOAD_BASE_URL": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
        "FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL": "https://nodejs.org/zh-cn/download",
    }


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _is_cn_language(language: str | None) -> bool:
    normalized = (language or "").strip().lower().replace("_", "-")
    return normalized.startswith(("zh", "cn"))
