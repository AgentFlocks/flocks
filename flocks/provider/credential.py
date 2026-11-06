"""
Credential utilities — thin helpers around SecretManager + ConfigWriter.

Credentials (API keys) are stored in .secret.json via SecretManager.
Provider config (base_url etc.) is stored in flocks.json via ConfigWriter.
SQLite is NOT used for credential storage.
"""

import os
from typing import List, Optional

from flocks.config.config_writer import ConfigWriter
from flocks.security.secrets import SecretManager
from flocks.utils.log import Log

log = Log.create(service="credential")

#: Refusing a key that is being saved.
UNSENDABLE_API_KEY_MESSAGE = (
    "API Key 只能包含英文字母、数字和英文符号，不能有中文、全角符号或换行，请检查后重新填写。"
)
#: Explaining a request that could not be sent, whatever key got stored earlier.
UNSENDABLE_HEADER_MESSAGE = (
    "请求发不出去：API Key（或自定义请求头）里有中文、全角符号等非 ASCII 字符。请重新填写后再试。"
)


def api_key_is_sendable(api_key: str) -> bool:
    """Whether a key can travel in an HTTP header, which httpx encodes as ASCII."""
    return api_key.isascii() and api_key.isprintable()


def is_unsendable_header_error(error: BaseException) -> bool:
    """Whether the HTTP client refused a request because a header is not ASCII.

    Only the client's own encoding counts: any other ``UnicodeEncodeError`` in a
    model step (a hook, a log stream) says nothing about the credentials.
    """
    if not isinstance(error, UnicodeEncodeError) or error.encoding != "ascii":
        return False
    frame = error.__traceback__
    while frame is not None:
        module = frame.tb_frame.f_globals.get("__name__", "")
        if module.partition(".")[0] in {"httpx", "httpcore", "h11"}:
            return True
        frame = frame.tb_next
    return False


def has_credential(provider_id: str) -> bool:
    """Check if a provider has an API key configured in .secret.json.

    Checks _llm_key first (current convention), then falls back to legacy
    _api_key for backward compatibility.
    """
    try:
        from flocks.security import get_secret_manager
        secrets = get_secret_manager()
        return secrets.has(f"{provider_id}_llm_key") or secrets.has(f"{provider_id}_api_key")
    except Exception:
        return False


def get_api_key(provider_id: str) -> Optional[str]:
    """Get the API key for a provider from .secret.json.

    Tries _llm_key first (current convention), then falls back to legacy _api_key.
    """
    try:
        from flocks.security import get_secret_manager
        secrets = get_secret_manager()
        return secrets.get(f"{provider_id}_llm_key") or secrets.get(f"{provider_id}_api_key")
    except Exception:
        return None


def get_base_url(provider_id: str) -> Optional[str]:
    """Get the base URL for a provider from flocks.json."""
    raw = ConfigWriter.get_provider_raw(provider_id)
    if not raw:
        return None
    options = raw.get("options", {})
    return options.get("baseURL") or options.get("base_url")


def get_masked_key(provider_id: str) -> Optional[str]:
    """Get the masked API key for display."""
    api_key = get_api_key(provider_id)
    if not api_key:
        return None
    return SecretManager.mask(api_key)


def list_configured_providers() -> List[str]:
    """List all provider IDs that have credentials configured.

    Recognises both _llm_key (current convention) and legacy _api_key suffixes.
    """
    try:
        from flocks.security import get_secret_manager
        secrets = get_secret_manager()
        configured = set()
        for sid in secrets.list():
            if sid.endswith("_llm_key"):
                configured.add(sid[: -len("_llm_key")])
            elif sid.endswith("_api_key"):
                configured.add(sid[: -len("_api_key")])
        return list(configured)
    except Exception:
        return []


# ==================== Migration ====================


def migrate_env_credentials() -> int:
    """Migrate credentials from environment variables to .secret.json.

    Scans well-known env vars (OPENAI_API_KEY, etc.) and saves them
    to .secret.json if not already present. This is a one-time convenience
    so that users with env-based setups can start using the WebUI immediately.

    Returns:
        Number of credentials migrated.
    """
    from flocks.security import get_secret_manager
    secrets = get_secret_manager()

    env_mapping = {
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "GOOGLE_API_KEY": "google",
        "MISTRAL_API_KEY": "mistral",
        "GROQ_API_KEY": "groq",
        "COHERE_API_KEY": "cohere",
        "TOGETHER_API_KEY": "together",
        "XAI_API_KEY": "xai",
        "DEEPSEEK_API_KEY": "deepseek",
        "DEEPINFRA_API_KEY": "deepinfra",
        "PERPLEXITY_API_KEY": "perplexity",
        "OPENROUTER_API_KEY": "openrouter",
        "SILICONFLOW_API_KEY": "siliconflow",
    }

    migrated = 0
    for env_var, provider_id in env_mapping.items():
        api_key = os.environ.get(env_var)
        if not api_key:
            continue

        secret_id = f"{provider_id}_llm_key"
        # Skip if already stored under either naming convention
        if secrets.has(secret_id) or secrets.has(f"{provider_id}_api_key"):
            continue

        try:
            secrets.set(secret_id, api_key)
            migrated += 1
            log.info("credential.env_migrated", {
                "provider_id": provider_id,
                "env_var": env_var,
            })
        except Exception as e:
            log.warning("credential.env_migration_failed", {
                "provider_id": provider_id,
                "error": str(e),
            })

    if migrated > 0:
        log.info("credential.env_migration_completed", {"migrated": migrated})

    return migrated
