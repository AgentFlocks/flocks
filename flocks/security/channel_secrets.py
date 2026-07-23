"""Channel secret extraction utilities.

Sensitive channel fields (API keys, tokens, passwords) should be stored
in .secret.json rather than flocks.json. This module handles extracting
those fields to .secret.json and replacing them with {secret:...} refs.

Naming convention for secret IDs: channel_{channel_id}_{field_name}
  e.g. channel_telegram_botToken, channel_feishu_appSecret
"""

from typing import Any, Dict, Set

from flocks.utils.log import Log

log = Log.create(service="security.channel_secrets")

# Field names considered sensitive — stored in .secret.json, not flocks.json
SENSITIVE_FIELD_NAMES: Set[str] = {
    "appSecret",
    "botToken",
    "appToken",
    "slackAppToken",
    "slackBotToken",
    "secret",
    "clientSecret",
    "apiKey",
    "token",
    "accessToken",
    "refreshToken",
    "webhookSecret",
    "verificationToken",
    "encryptKey",
    "password",
}


def _is_ref(value: str) -> bool:
    """Return True if value is already a {secret:...} or {env:...} placeholder."""
    return value.startswith("{secret:") or value.startswith("{env:")


def make_secret_id(channel_id: str, field_name: str) -> str:
    """Generate the canonical secret ID for a channel field."""
    return f"channel_{channel_id}_{field_name}"


def extract_channel_secrets(channels_config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract sensitive fields from channel configs into .secret.json.

    For each channel, plaintext values of sensitive fields are written to
    .secret.json and replaced with ``{secret:channel_{id}_{field}}``
    references in the returned dict.  Values that are already references
    (``{secret:...}`` / ``{env:...}``) or empty strings are left as-is.

    Args:
        channels_config: The ``channels`` section of the flocks config dict.

    Returns:
        A copy of *channels_config* with sensitive field values replaced by
        ``{secret:...}`` references.
    """
    from flocks.security.secrets import get_secret_manager

    secret_manager = get_secret_manager()
    result: Dict[str, Any] = {}

    for channel_id, channel_cfg in channels_config.items():
        if not isinstance(channel_cfg, dict):
            result[channel_id] = channel_cfg
            continue

        modified_cfg: Dict[str, Any] = {}
        for field, value in channel_cfg.items():
            if (
                field in SENSITIVE_FIELD_NAMES
                and isinstance(value, str)
                and value
                and not _is_ref(value)
            ):
                secret_id = make_secret_id(channel_id, field)
                secret_manager.set(secret_id, value)
                modified_cfg[field] = f"{{secret:{secret_id}}}"
                log.info(
                    "channel_secrets.extracted",
                    {"channel": channel_id, "field": field, "secret_id": secret_id},
                )
            else:
                modified_cfg[field] = value

        result[channel_id] = modified_cfg

    return result
