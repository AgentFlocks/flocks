"""SkyEye Sensor skill-local CLI configuration."""

import os
from pathlib import Path

BASE_URL = os.getenv("SKYEYE_SENSOR_BASE_URL", "")
AUTH_STATE_FILE = Path(
    os.getenv(
        "SKYEYE_SENSOR_AUTH_STATE",
        Path.home() / ".flocks" / "browser" / "skyeye-sensor" / "auth-state.json",
    )
)
COOKIE_FILE = Path(
    os.getenv(
        "SKYEYE_SENSOR_COOKIE_FILE",
        Path.home() / ".flocks" / "browser" / "skyeye-sensor" / "cookie.json",
    )
)
CSRF_TOKEN = os.getenv("SKYEYE_SENSOR_CSRF_TOKEN", "")

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Skyeye-Sensor-Skill-CLI/1.0",
    "X-Requested-With": "XMLHttpRequest",
}

TIMEOUT = 30
SSL_VERIFY = os.getenv("SKYEYE_SENSOR_SSL_VERIFY", "false").lower() in ("1", "true", "yes")
