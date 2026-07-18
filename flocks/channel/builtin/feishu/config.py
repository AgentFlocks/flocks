"""
Feishu-specific configuration constants and helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from typing import Any, Optional

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover - defensive import for runtime environments
    BaseModel = object  # type: ignore[misc,assignment]

# Domain endpoints
_FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
_LARK_API_BASE = "https://open.larksuite.com/open-apis"

# Defaults (domestic Feishu)
FEISHU_API_BASE = _FEISHU_API_BASE
FEISHU_TOKEN_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
FEISHU_SEND_URL = f"{FEISHU_API_BASE}/im/v1/messages"
_DEFAULT_WEBHOOK_MAX_SKEW_SECONDS = 300

_GROUP_KEY_ALIASES = {
    "require_mention": "requireMention",
    "allow_from": "allowFrom",
    "system_prompt": "systemPrompt",
    "default_agent": "defaultAgent",
    "group_session_scope": "groupSessionScope",
    "mention_context_messages": "mentionContextMessages",
}


def resolve_api_base(config: dict) -> str:
    """Return the correct API base URL based on the ``domain`` config field.

    - ``"feishu"`` (default) → ``https://open.feishu.cn/open-apis``
    - ``"lark"``             → ``https://open.larksuite.com/open-apis``
    - Any URL starting with ``https://`` → used as-is (custom domain)
    """
    domain = config.get("domain", "feishu")
    if domain == "lark":
        return _LARK_API_BASE
    if isinstance(domain, str) and domain.startswith("https://"):
        return domain.rstrip("/")
    return _FEISHU_API_BASE


def resolve_token_url(config: dict) -> str:
    return f"{resolve_api_base(config)}/auth/v3/tenant_access_token/internal"


def normalize_webhook_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Return a lowercase string-keyed headers mapping."""
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
    }


def verify_webhook_timestamp(
    headers: Mapping[str, Any],
    max_skew_seconds: int = _DEFAULT_WEBHOOK_MAX_SKEW_SECONDS,
) -> bool:
    """Check whether the webhook request timestamp is recent enough."""
    normalized = normalize_webhook_headers(headers)
    timestamp_raw = normalized.get("x-lark-request-timestamp", "").strip()
    if not timestamp_raw:
        return True
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False
    return abs(time.time() - timestamp) <= max_skew_seconds


def verify_webhook_signature(
    body: bytes,
    headers: dict,
    encrypt_key: str,
) -> bool:
    """Verify the ``X-Lark-Signature`` header from a Feishu Webhook request.

    The signature is ``sha256(timestamp + nonce + encrypt_key + body)``.
    Returns True if valid, or if no encrypt_key is configured (skip check).
    """
    if not encrypt_key:
        return True
    normalized = normalize_webhook_headers(headers)
    timestamp = normalized.get("x-lark-request-timestamp", "")
    nonce = normalized.get("x-lark-request-nonce", "")
    signature = normalized.get("x-lark-signature", "")
    if not signature:
        return False
    payload = f"{timestamp}{nonce}{encrypt_key}".encode() + body
    expected = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_verification_token(data: dict, token: str) -> bool:
    """Check the ``verification_token`` field inside the event body."""
    if not token:
        return True
    return data.get("token", "") == token


def list_account_configs(
    config: dict,
    *,
    webhook_only: bool = False,
    require_credentials: bool = False,
) -> list[dict]:
    """Return merged per-account configs, including ``_account_id`` metadata."""
    accounts_cfg: dict = config.get("accounts", {}) or {}
    default_account = str(config.get("defaultAccount", "")).strip()
    top_level_has_credentials = bool(config.get("appId")) and bool(config.get("appSecret"))
    top_level_has_webhook_surface = (
        bool(config.get("verificationToken"))
        or bool(config.get("encryptKey"))
        or config.get("connectionMode", "websocket") == "webhook"
    )

    def _should_include(merged: dict) -> bool:
        if webhook_only and merged.get("connectionMode", "websocket") != "webhook":
            return False
        if require_credentials and (not merged.get("appId") or not merged.get("appSecret")):
            return False
        return True

    if not accounts_cfg:
        merged = {**config, "_account_id": config.get("_account_id", "default")}
        return [merged] if _should_include(merged) else []

    result: list[dict] = []
    if "default" not in accounts_cfg and (
        top_level_has_credentials
        or (webhook_only and top_level_has_webhook_surface)
        or (not webhook_only and not require_credentials and top_level_has_webhook_surface)
    ):
        merged = {
            **config,
            "_account_id": config.get("_account_id", "default"),
            "_default_account": default_account,
            "_has_own_encryptKey": False,
            "_has_own_verificationToken": False,
        }
        if _should_include(merged):
            result.append(merged)

    for acc_id, acc_overrides in accounts_cfg.items():
        acc_overrides = acc_overrides or {}
        if not acc_overrides.get("enabled", True):
            continue
        merged = {
            **config,
            **acc_overrides,
            "_account_id": acc_id,
            "_default_account": default_account,
            "_has_own_encryptKey": "encryptKey" in acc_overrides,
            "_has_own_verificationToken": "verificationToken" in acc_overrides,
        }
        merged.pop("accounts", None)
        if _should_include(merged):
            result.append(merged)

    if result:
        return result

    merged = {
        **config,
        "_account_id": "default",
        "_default_account": default_account,
        "_has_own_encryptKey": False,
        "_has_own_verificationToken": False,
    }
    return [merged] if _should_include(merged) else []


def resolve_webhook_account_config(
    config: dict,
    *,
    body: bytes,
    headers: Mapping[str, Any],
    data: dict,
) -> Optional[dict]:
    """Resolve the best matching webhook account config for the incoming request."""
    candidates = list_account_configs(
        config,
        webhook_only=True,
        require_credentials=False,
    )
    if not candidates:
        return None

    default_account = str(config.get("defaultAccount", "")).strip() or "default"
    matches: list[tuple[tuple[int, int, int], dict]] = []

    for candidate in candidates:
        has_encrypt_key = bool(candidate.get("encryptKey"))
        has_verification_token = bool(candidate.get("verificationToken"))
        if not has_encrypt_key and not has_verification_token:
            continue

        if has_encrypt_key and not verify_webhook_signature(body, headers, candidate["encryptKey"]):
            continue
        if has_verification_token and not verify_verification_token(
            data,
            str(candidate.get("verificationToken", "")),
        ):
            continue

        specificity = (
            (2 if bool(candidate.get("_has_own_encryptKey")) else 0)
            + (2 if bool(candidate.get("_has_own_verificationToken")) else 0)
            + (1 if has_encrypt_key else 0)
            + (1 if has_verification_token else 0)
        )
        default_bonus = 1 if candidate.get("_account_id") == default_account else 0
        named_bonus = 1 if candidate.get("_account_id") != "default" else 0
        matches.append(((specificity, default_bonus, named_bonus), candidate))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1:
        top_score = matches[0][0]
        top_matches = [candidate for score, candidate in matches if score == top_score]
        top_ids = {candidate.get("_account_id", "default") for candidate in top_matches}
        if len(top_ids) > 1:
            return None
    return matches[0][1]


def build_webhook_replay_key(headers: Mapping[str, Any], data: dict) -> Optional[str]:
    """Build a stable dedup key for webhook replay protection."""
    normalized = normalize_webhook_headers(headers)
    event_id = ((data.get("header") or {}).get("event_id") or "").strip()
    if event_id:
        return f"replay:event:{event_id}"
    request_id = (normalized.get("x-lark-request-id", "") or "").strip()
    if request_id:
        return f"replay:req:{request_id}"
    nonce = (normalized.get("x-lark-request-nonce", "") or "").strip()
    timestamp = (normalized.get("x-lark-request-timestamp", "") or "").strip()
    if nonce and timestamp:
        return f"replay:nonce:{timestamp}:{nonce}"
    return None


def normalize_group_entry(entry: Any) -> dict[str, Any]:
    """Convert a Feishu group config entry to an alias-keyed plain dict."""
    if entry is None:
        return {}

    if isinstance(entry, BaseModel):
        raw = entry.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(entry, Mapping):
        raw = dict(entry)
    else:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        normalized[_GROUP_KEY_ALIASES.get(str(key), str(key))] = value
    return normalized


def merge_group_overrides(groups: Any, chat_id: str) -> dict[str, Any]:
    """Merge ``groups.*`` and ``groups.<chat_id>`` using alias-keyed fields."""
    if not isinstance(groups, Mapping):
        return {}
    wildcard = normalize_group_entry(groups.get("*"))
    specific = normalize_group_entry(groups.get(chat_id))
    return {**wildcard, **specific}


def resolve_receive_id_type(to: str) -> str:
    """
    Infer the ``receive_id_type`` query parameter for the Feishu send API.

    Conventions:
    - ``oc_`` prefix → chat_id (group chat)
    - ``ou_`` prefix → open_id (user)
    - ``on_`` prefix → union_id (cross-app user id)
    - ``chat:`` prefix → chat_id
    - ``user:`` prefix → open_id
    - fallback → open_id
    """
    if to.startswith("oc_"):
        return "chat_id"
    if to.startswith("ou_"):
        return "open_id"
    if to.startswith("on_"):
        return "union_id"
    if to.startswith("chat:"):
        return "chat_id"
    if to.startswith("user:"):
        return "open_id"
    return "open_id"


def strip_target_prefix(to: str) -> str:
    """Remove ``chat:`` / ``user:`` prefixes."""
    for prefix in ("chat:", "user:"):
        if to.startswith(prefix):
            return to[len(prefix):]
    return to
