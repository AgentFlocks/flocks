"""
MCP Utility Functions

Provides utility functions for URL building, environment variable resolution, etc.
"""

import os
import re
import hashlib
from typing import Optional, Dict, Any
from urllib.parse import urlencode, urlparse, parse_qs, parse_qsl, urlunparse

_SENSITIVE_QUERY_PARAMS = frozenset({
    "apikey", "api_key", "key", "token", "access_token",
    "auth", "auth_key", "secret", "password",
})

_SENSITIVE_HEADER_NAMES = frozenset({
    "api-key",
    "apikey",
    "authorization",
    "token",
    "access-token",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
})

_AUTH_ERROR_KEYWORDS = (
    "401",
    "403",
    "api key",
    "apikey",
    "auth",
    "authentication",
    "authorization",
    "credential",
    "forbidden",
    "secret not found",
    "token",
    "unauthorized",
)

REMOTE_MCP_TYPES = frozenset({"remote", "sse"})
LOCAL_MCP_TYPES = frozenset({"local", "stdio"})


def resolve_url_template(url: str) -> str:
    """
    Resolve ``{secret:KEY}`` placeholders embedded anywhere in a URL string.

    This allows storing URLs like::

        https://mcp.example.com/mcp?apikey={secret:myserver_mcp_key}

    in ``flocks.json`` while keeping the actual secret value in
    ``~/.flocks/config/.secret.json``.

    Args:
        url: URL string possibly containing ``{secret:KEY}`` patterns.

    Returns:
        URL with all ``{secret:KEY}`` patterns replaced by their resolved values.

    Raises:
        ValueError: If a referenced secret key does not exist.
    """
    if '{secret:' not in url:
        return url

    import re
    from flocks.security.secrets import get_secret_manager

    secrets = get_secret_manager()

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        value = secrets.get(key)
        if value is None:
            raise ValueError(f"Secret not found: {key}")
        return value

    return re.sub(r'\{secret:([^}]+)\}', _replace, url)


def normalize_mcp_config_aliases(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize transport aliases to canonical backend config values."""
    normalized = dict(config)
    server_type = str(normalized.get("type", "")).strip().lower()
    if server_type == "sse":
        normalized["type"] = "remote"
        normalized.setdefault("transport", "sse")
    elif server_type == "stdio":
        normalized["type"] = "local"

    transport = str(normalized.get("transport", "")).strip().lower()
    if normalized.get("type") in REMOTE_MCP_TYPES:
        if transport in ("", "auto"):
            normalized["transport"] = "auto"
        elif transport in ("streamablehttp", "streamable_http", "http"):
            normalized["transport"] = "http"
        elif transport == "sse":
            normalized["transport"] = "sse"
    return normalized


def normalize_mcp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize MCP config into the backend's canonical representation."""
    normalized = normalize_mcp_config_aliases(config)
    if normalized.get("type") not in LOCAL_MCP_TYPES:
        return normalized

    command_value = normalized.get("command")
    if isinstance(command_value, list):
        command_parts = [str(item).strip() for item in command_value if str(item).strip()]
    elif isinstance(command_value, str):
        command_parts = [command_value.strip()] if command_value.strip() else []
    elif command_value is None:
        command_parts = []
    else:
        command_text = str(command_value).strip()
        command_parts = [command_text] if command_text else []

    raw_args = normalized.get("args")
    if isinstance(raw_args, list):
        arg_parts = [str(item).strip() for item in raw_args if str(item).strip()]
    elif isinstance(raw_args, str):
        arg_parts = [line.strip() for line in raw_args.splitlines() if line.strip()]
    else:
        arg_parts = []

    normalized["command"] = [*command_parts, *arg_parts]
    normalized.pop("args", None)
    return normalized


def build_mcp_url(base_url: str, auth_config: Optional[Dict[str, Any]] = None) -> str:
    """
    Build MCP URL with support for multiple authentication methods.

    ``{secret:KEY}`` placeholders embedded in *base_url* (e.g.
    ``?apikey={secret:myserver_mcp_key}``) are resolved before any further
    processing.

    Args:
        base_url: Base URL (may contain ``{secret:KEY}`` placeholders).
        auth_config: Authentication configuration.

    Returns:
        Complete MCP URL with credentials injected.

    Examples:
        >>> build_mcp_url("https://mcp.example.com/mcp", {
        ...     "type": "apikey",
        ...     "location": "query",
        ...     "param_name": "apikey",
        ...     "value": "abc123"
        ... })
        'https://mcp.example.com/mcp?apikey=abc123'
    """
    # Resolve any {secret:KEY} patterns embedded in the URL first
    base_url = resolve_url_template(base_url)

    if not auth_config:
        return base_url
    
    location = auth_config.get("location", "header")
    
    # Query parameter authentication
    if location == "query":
        param_name = auth_config.get("param_name", "apikey")
        param_value = resolve_env_var(auth_config.get("value", ""))
        
        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)
        query_params[param_name] = [param_value]
        
        new_query = urlencode(query_params, doseq=True)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
    
    # Header or Body authentication handled during connection
    return base_url


def build_mcp_headers(
    base_headers: Optional[Dict[str, str]] = None,
    auth_config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """
    Build resolved HTTP headers for a remote MCP connection.

    Args:
        base_headers: Static headers from MCP config.
        auth_config: Optional auth config. When ``location == "header"``,
            this is injected into the returned headers.

    Returns:
        Resolved header dict, or None when no headers are needed.
    """
    headers: Dict[str, str] = {}

    for key, value in (base_headers or {}).items():
        if value is None:
            continue
        headers[str(key)] = resolve_env_var(str(value))

    if auth_config and auth_config.get("location", "header") == "header":
        param_name = str(auth_config.get("param_name", "Authorization"))
        param_value = resolve_env_var(str(auth_config.get("value", "")))
        if (
            str(auth_config.get("scheme", "")).strip().lower() == "bearer"
            and param_value
            and not param_value.lower().startswith("bearer ")
        ):
            param_value = f"Bearer {param_value}"
        if param_value:
            headers.setdefault(param_name, param_value)

    return headers or None


def config_has_pending_credentials(config: Dict[str, Any]) -> bool:
    """Return True when the config intentionally leaves credential slots blank."""
    auth_config = config.get("auth")
    if isinstance(auth_config, dict):
        auth_type = str(auth_config.get("type", "")).strip().lower()
        auth_value = auth_config.get("value")
        if auth_type and auth_type != "none":
            if not isinstance(auth_value, str) or not auth_value.strip():
                return True

    headers = config.get("headers")
    if isinstance(headers, dict):
        for header_name, header_value in headers.items():
            if (
                str(header_name).strip().lower() in _SENSITIVE_HEADER_NAMES
                and not str(header_value or "").strip()
            ):
                return True

    url = str(config.get("url", "") or "")
    if not url:
        return False

    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if key.lower() in _SENSITIVE_QUERY_PARAMS and not value.strip():
            return True

    return False


def is_auth_related_error(error_detail: Optional[str]) -> bool:
    """Best-effort detection for missing or invalid MCP credentials."""
    if not error_detail:
        return False
    lowered = error_detail.lower()
    return any(keyword in lowered for keyword in _AUTH_ERROR_KEYWORDS)


def should_allow_unconnected_add(
    config: Dict[str, Any], error_detail: Optional[str]
) -> bool:
    """Allow add to succeed when only credentials are missing for a remote server."""
    if config.get("type") not in REMOTE_MCP_TYPES:
        return False
    return config_has_pending_credentials(config) or is_auth_related_error(error_detail)


def should_skip_connect_on_add(config: Dict[str, Any]) -> bool:
    """Skip eager connect when credential fields are intentionally left blank."""
    if config.get("type") not in REMOTE_MCP_TYPES:
        return False
    return config_has_pending_credentials(config)


def get_connect_block_reason(config: Dict[str, Any]) -> Optional[str]:
    """Return a user-facing reason why connect should not be attempted yet."""
    if config.get("type") not in REMOTE_MCP_TYPES:
        return None
    if config_has_pending_credentials(config):
        return (
            "MCP server credentials are not configured yet. "
            "Please save the required API key or auth header first."
        )
    return None


def extract_api_key_from_mcp_url(server_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Move sensitive query credentials from remote MCP URLs into SecretManager."""
    url = str(config.get("url", "") or "")
    if not url or config.get("type") not in REMOTE_MCP_TYPES or "?" not in url:
        return dict(config)

    from urllib.parse import unquote

    base, _, raw_query = url.partition("?")
    fragment = ""
    if "#" in raw_query:
        raw_query, _, fragment = raw_query.partition("#")

    parts = raw_query.split("&")
    new_parts: list[str] = []
    extracted = False

    for part in parts:
        if "=" not in part:
            new_parts.append(part)
            continue
        key_encoded, _, value_encoded = part.partition("=")
        key = unquote(key_encoded)
        value = unquote(value_encoded)
        if key.lower() in _SENSITIVE_QUERY_PARAMS and not value.startswith("{secret:"):
            secret_key = f"{server_name}_mcp_key"
            from flocks.security import get_secret_manager
            get_secret_manager().set(secret_key, value)
            new_parts.append(f"{key_encoded}={{secret:{secret_key}}}")
            extracted = True
        else:
            new_parts.append(part)

    if not extracted:
        return dict(config)

    new_url = base + "?" + "&".join(new_parts)
    if fragment:
        new_url += "#" + fragment

    return {**config, "url": new_url}


def extract_auth_value_from_mcp_config(server_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Move plain-text ``auth.value`` into SecretManager and keep a secret reference."""
    auth_config = config.get("auth")
    if not isinstance(auth_config, dict):
        return dict(config)

    auth_value = auth_config.get("value")
    if not isinstance(auth_value, str):
        return dict(config)

    auth_value = auth_value.strip()
    if not auth_value or auth_value.startswith("{secret:") or auth_value.startswith("${"):
        return dict(config)

    updated_auth = dict(auth_config)
    scheme = str(updated_auth.get("scheme", "")).strip().lower()
    if (
        not scheme
        and str(updated_auth.get("location", "")).strip().lower() == "header"
        and str(updated_auth.get("param_name", "")).strip().lower() == "authorization"
        and auth_value.lower().startswith("bearer ")
    ):
        scheme = "bearer"
    if scheme == "bearer":
        updated_auth["scheme"] = "bearer"
        if auth_value.lower().startswith("bearer "):
            auth_value = auth_value[7:].strip()
    elif "scheme" in updated_auth and not scheme:
        updated_auth.pop("scheme", None)

    secret_key = str(auth_config.get("secret_id") or f"{server_name}_mcp_key")
    from flocks.security import get_secret_manager

    get_secret_manager().set(secret_key, auth_value)

    updated_auth["value"] = f"{{secret:{secret_key}}}"
    updated_auth.pop("secret_id", None)

    updated_config = dict(config)
    updated_config["auth"] = updated_auth
    return updated_config


def resolve_env_var(value: str) -> str:
    """
    Resolve environment variable or secret placeholder.

    Supported formats:
        - ``${VAR_NAME}``      — read from OS environment variable
        - ``{secret:KEY}``     — read from SecretManager (~/.flocks/config/.secret.json)

    Args:
        value: Value or placeholder string.

    Returns:
        Resolved value.

    Raises:
        ValueError: If the referenced variable / secret is not found.

    Examples:
        >>> os.environ['TEST_KEY'] = 'secret'
        >>> resolve_env_var('${TEST_KEY}')
        'secret'
        >>> resolve_env_var('plain_value')
        'plain_value'
    """
    if not value:
        return value

    # {secret:KEY} — read from SecretManager
    if value.startswith("{secret:") and value.endswith("}"):
        secret_key = value[8:-1]
        from flocks.security.secrets import get_secret_manager
        secret_value = get_secret_manager().get(secret_key)
        if secret_value is None:
            raise ValueError(f"Secret not found: {secret_key}")
        return secret_value

    # ${VAR_NAME} — read from OS environment variable
    if value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        env_value = os.getenv(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable not found: {var_name}")
        return env_value

    return value


def sanitize_name(name: str) -> str:
    """
    Sanitize name to ensure it conforms to identifier rules
    
    Args:
        name: Original name
        
    Returns:
        Sanitized name (lowercase, containing only letters, digits, underscores, hyphens)
        
    Examples:
        >>> sanitize_name('ThreatBook API')
        'threatbook_api'
        >>> sanitize_name('my-tool@v1.0')
        'my-tool_v1_0'
    """
    # Keep only letters, digits, underscores, hyphens
    name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    
    # Ensure it doesn't start with a digit
    if name and name[0].isdigit():
        name = f"_{name}"
    
    return name.lower()


def generate_tool_name(server_name: str, tool_name: str) -> str:
    """
    Generate Flocks tool name
    
    Format: {server}_{tool}
    
    Args:
        server_name: MCP server name
        tool_name: MCP tool name
        
    Returns:
        Flocks tool name
        
    Examples:
        >>> generate_tool_name('ThreatBook', 'ip_query')
        'threatbook_ip_query'
    """
    return f"{sanitize_name(server_name)}_{sanitize_name(tool_name)}"


def calculate_schema_hash(schema: Dict[str, Any]) -> str:
    """
    Calculate schema hash value for detecting changes
    
    Args:
        schema: JSON Schema
        
    Returns:
        SHA256 hash value
    """
    import json
    schema_str = json.dumps(schema, sort_keys=True)
    return hashlib.sha256(schema_str.encode()).hexdigest()


def check_name_conflict(tool_name: str) -> bool:
    """
    Check if tool name conflicts
    
    Args:
        tool_name: Tool name
        
    Returns:
        True if conflict exists
    """
    from flocks.tool import ToolRegistry
    return tool_name in ToolRegistry._tools


def resolve_conflict(tool_name: str, attempt: int = 0) -> str:
    """
    Resolve name conflict by adding numeric suffix
    
    Args:
        tool_name: Tool name
        attempt: Attempt count
        
    Returns:
        Conflict-free tool name
        
    Examples:
        >>> resolve_conflict('my_tool')  # Assuming it exists
        'my_tool_1'
    """
    if not check_name_conflict(tool_name):
        return tool_name
    
    new_name = f"{tool_name}_{attempt + 1}"
    return resolve_conflict(new_name, attempt + 1)


__all__ = [
    'REMOTE_MCP_TYPES',
    'LOCAL_MCP_TYPES',
    'build_mcp_url',
    'build_mcp_headers',
    'config_has_pending_credentials',
    'extract_api_key_from_mcp_url',
    'extract_auth_value_from_mcp_config',
    'get_connect_block_reason',
    'is_auth_related_error',
    'normalize_mcp_config',
    'normalize_mcp_config_aliases',
    'resolve_url_template',
    'resolve_env_var',
    'sanitize_name',
    'should_allow_unconnected_add',
    'should_skip_connect_on_add',
    'generate_tool_name',
    'calculate_schema_hash',
    'check_name_conflict',
    'resolve_conflict',
]
