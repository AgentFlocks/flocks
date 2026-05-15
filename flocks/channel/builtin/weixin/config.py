"""
Constants, regex patterns, and dependency guards for the Weixin channel.

All public constants for the iLink Bot API live here so that other modules
in this package import a single source of truth.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# iLink Bot API constants
# ---------------------------------------------------------------------------
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_CONFIG = "ilink/bot/getconfig"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
QR_TIMEOUT_MS = 35_000

# ---------------------------------------------------------------------------
# Timeouts (milliseconds for API calls, seconds for media transfers)
# ---------------------------------------------------------------------------
LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000

MEDIA_DOWNLOAD_IMAGE_TIMEOUT_S = 30.0
MEDIA_DOWNLOAD_VIDEO_TIMEOUT_S = 120.0
MEDIA_DOWNLOAD_FILE_TIMEOUT_S = 60.0
MEDIA_DOWNLOAD_VOICE_TIMEOUT_S = 60.0
MEDIA_UPLOAD_TIMEOUT_S = 120.0
MEDIA_REMOTE_FETCH_TIMEOUT_S = 30.0

# ---------------------------------------------------------------------------
# Retry / backoff tuning
# ---------------------------------------------------------------------------
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY_SECONDS = 2.0
BACKOFF_DELAY_SECONDS = 30.0
SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2
MESSAGE_DEDUP_TTL_SECONDS = 300
MAX_MESSAGE_LENGTH = 2000

# ---------------------------------------------------------------------------
# iLink message / item type constants
# ---------------------------------------------------------------------------
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

# ---------------------------------------------------------------------------
# Markdown / format regex helpers (shared with format.py)
# ---------------------------------------------------------------------------
HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TABLE_RULE_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
FENCE_RE = re.compile(r"^```([^\n`]*)\s*$")

# ---------------------------------------------------------------------------
# Dependency guards (importable feature flags)
# ---------------------------------------------------------------------------
try:
    import aiohttp  # type: ignore[import-untyped]  # noqa: F401
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    from cryptography.hazmat.backends import default_backend  # noqa: F401
    from cryptography.hazmat.primitives.ciphers import (  # noqa: F401
        Cipher,
        algorithms,
        modes,
    )
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


def check_requirements() -> bool:
    """Return True when both runtime dependencies are installed."""
    return AIOHTTP_AVAILABLE and CRYPTO_AVAILABLE
