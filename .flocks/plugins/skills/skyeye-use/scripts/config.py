"""SkyEye skill-local CLI configuration."""

import os
from pathlib import Path

BASE_URL = os.getenv("SKYEYE_BASE_URL", "")
AUTH_STATE_FILE = Path(
    os.getenv(
        "SKYEYE_AUTH_STATE",
        Path.home() / ".flocks" / "browser" / "skyeye" / "auth-state.json",
    )
)
TOKEN = os.getenv("SKYEYE_CSRF_TOKEN", "")

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": "Skyeye-Skill-CLI/1.0",
}

TIMEOUT = 30
SSL_VERIFY = os.getenv("SKYEYE_SSL_VERIFY", "false").lower() in ("1", "true", "yes")
