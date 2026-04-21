"""
DingTalk-specific configuration constants and helpers.

Outbound is supported only via the **enterprise app robot OAPI** (sometimes
called the *stream/app push* path):

- Required: ``appKey`` + ``appSecret``.
- ``clientId`` / ``clientSecret`` are accepted as aliases for ``appKey`` /
  ``appSecret`` (the DingTalk Stream / DWClient docs use the former names but
  refer to the same credential pair).
- ``robotCode`` defaults to ``appKey`` (DingTalk's standard "enterprise
  internal app robot" setup uses the same string for both).  Override only
  when one app hosts multiple robots and the console issues a distinct code.
- Sends via the OAPI domain ``https://api.dingtalk.com``
  (``/v1.0/robot/oToMessages/batchSend`` for 1:1, ``/v1.0/robot/groupMessages/send``
  for groups).

Custom group robot incoming webhooks are intentionally **not** supported.
"""

from __future__ import annotations

from typing import Optional

# OAPI base used by the enterprise app robot APIs (access_token, batchSend, …).
DINGTALK_API_BASE = "https://api.dingtalk.com"
DINGTALK_TOKEN_URL = f"{DINGTALK_API_BASE}/v1.0/oauth2/accessToken"


def strip_target_prefix(to: str) -> str:
    """Remove ``user:`` / ``chat:`` prefixes from a DingTalk target."""
    if not to:
        return ""
    for prefix in ("user:", "chat:"):
        if to.startswith(prefix):
            return to[len(prefix):]
    return to


def resolve_target_kind(to: str) -> str:
    """Infer whether *to* points at a user or a group conversation.

    Conventions:
    - ``user:<staffId>`` → user (1:1 message via ``robot/oToMessages/batchSend``)
    - ``chat:<openConversationId>`` → group (``robot/groupMessages/send``)
    - ``cid`` prefix or strings starting with ``cid`` — DingTalk group convention
    - otherwise default to ``user``
    """
    if not to:
        return "user"
    if to.startswith("chat:"):
        return "group"
    if to.startswith("user:"):
        return "user"
    bare = to.lstrip()
    if bare.startswith("cid"):
        return "group"
    return "user"


def _merged_app_key(source: dict) -> str:
    """Return the effective ``appKey`` for *source*.

    DingTalk Stream config historically uses ``clientId`` while the OAPI v1.0
    docs use ``appKey`` — they are the same credential.  Accepting both means
    the project-local Node.js connector's existing ``flocks.json`` works
    without changes.
    """
    return str(source.get("appKey") or source.get("clientId") or "")


def _merged_app_secret(source: dict) -> str:
    """Return the effective ``appSecret`` for *source*.  See :func:`_merged_app_key`."""
    return str(source.get("appSecret") or source.get("clientSecret") or "")


def list_account_configs(
    config: dict,
    *,
    require_credentials: bool = False,
) -> list[dict]:
    """Return merged per-account configs, including ``_account_id`` metadata.

    Mirrors :func:`flocks.channel.builtin.feishu.config.list_account_configs`
    but filters by DingTalk OAPI credentials (``appKey``+``appSecret``).
    ``clientId`` / ``clientSecret`` are accepted as aliases.
    """
    accounts_cfg: dict = config.get("accounts", {}) or {}

    def _has_credentials(merged: dict) -> bool:
        return bool(_merged_app_key(merged)) and bool(_merged_app_secret(merged))

    def _should_include(merged: dict) -> bool:
        if require_credentials and not _has_credentials(merged):
            return False
        return True

    if not accounts_cfg:
        merged = {**config, "_account_id": config.get("_account_id", "default")}
        return [merged] if _should_include(merged) else []

    result: list[dict] = []
    top_level_has_credentials = _has_credentials(config)
    if "default" not in accounts_cfg and top_level_has_credentials:
        merged = {**config, "_account_id": "default"}
        if _should_include(merged):
            result.append(merged)

    for acc_id, acc_overrides in accounts_cfg.items():
        acc_overrides = acc_overrides or {}
        if not acc_overrides.get("enabled", True):
            continue
        merged = {**config, **acc_overrides, "_account_id": acc_id}
        merged.pop("accounts", None)
        if _should_include(merged):
            result.append(merged)

    if result:
        return result

    merged = {**config, "_account_id": "default"}
    return [merged] if _should_include(merged) else []


def resolve_account_credentials(
    config: dict,
    account_id: Optional[str],
) -> tuple[str, str, str]:
    """Return ``(appKey, appSecret, robotCode)`` for the given account.

    Falls back to top-level config when the named account omits a field.
    Accepts ``clientId`` / ``clientSecret`` as aliases for ``appKey`` /
    ``appSecret`` so that DingTalk Stream-style configs work unchanged.

    ``robotCode`` defaults to the resolved ``appKey`` — for an enterprise
    internal app robot DingTalk uses the same string for both.  Set
    ``robotCode`` explicitly only when one app hosts multiple robots.
    """
    if account_id and account_id != "default":
        accounts = config.get("accounts", {}) or {}
        acc = accounts.get(account_id, {}) or {}
        app_key = _merged_app_key(acc) or _merged_app_key(config)
        app_secret = _merged_app_secret(acc) or _merged_app_secret(config)
        robot_code = acc.get("robotCode") or config.get("robotCode") or app_key
        return app_key, app_secret, robot_code

    app_key = _merged_app_key(config)
    return (
        app_key,
        _merged_app_secret(config),
        config.get("robotCode") or app_key,
    )


def resolve_account_config(config: dict, account_id: Optional[str]) -> dict:
    """Merge top-level config with the named account's overrides."""
    if not account_id or account_id == "default":
        return config
    accounts = config.get("accounts", {}) or {}
    acc = accounts.get(account_id, {}) or {}
    if not acc:
        return config
    merged = {**config, **acc}
    merged.pop("accounts", None)
    return merged
