"""Service configuration model and serialization helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class ServiceConfigError(ValueError):
    """Raised when service configuration input is invalid."""


@dataclass(frozen=True)
class ServiceConfig:
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 5173
    no_browser: bool = False
    skip_frontend_build: bool = False

    @property
    def frontend_url(self) -> str:
        return f"http://{_format_host_for_url(loopback_host(self.frontend_host))}:{self.frontend_port}"


def loopback_host(host: str) -> str:
    """Return a local access host for wildcard bind addresses."""
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _format_host_for_url(host: str) -> str:
    """Wrap IPv6 literals in brackets before composing URLs."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def service_config_payload(config: ServiceConfig) -> dict[str, object]:
    """Serialize service config for the supervisor control API."""
    return {
        "backend_host": config.backend_host,
        "backend_port": config.backend_port,
        "frontend_host": config.frontend_host,
        "frontend_port": config.frontend_port,
        "no_browser": config.no_browser,
        "skip_frontend_build": config.skip_frontend_build,
    }


def service_config_from_payload(
    payload: dict[str, Any],
    default: ServiceConfig | None = None,
    *,
    no_browser: bool | None = None,
    skip_frontend_build: bool | None = None,
) -> ServiceConfig:
    """Deserialize service config from a control or upgrade payload."""
    base = default or ServiceConfig()
    resolved_skip_frontend_build = (
        _bool(payload.get("skip_frontend_build"), base.skip_frontend_build)
        if skip_frontend_build is None
        else skip_frontend_build
    )
    resolved_no_browser = _bool(payload.get("no_browser"), base.no_browser) if no_browser is None else no_browser
    return ServiceConfig(
        backend_host=_string(payload.get("backend_host"), base.backend_host),
        backend_port=_positive_int(payload.get("backend_port"), base.backend_port),
        frontend_host=_string(payload.get("frontend_host"), base.frontend_host),
        frontend_port=_positive_int(payload.get("frontend_port"), base.frontend_port),
        no_browser=resolved_no_browser,
        skip_frontend_build=resolved_skip_frontend_build,
    )


def service_config_from_status_payload(
    payload: dict[str, Any],
    *,
    default: ServiceConfig | None = None,
    no_browser: bool | None = None,
    skip_frontend_build: bool | None = None,
) -> ServiceConfig:
    """Extract service config from a supervisor status payload."""
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return service_config_from_payload(
        config,
        default=default,
        no_browser=no_browser,
        skip_frontend_build=skip_frontend_build,
    )


def restart_defaults_from_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return CLI default overrides from a supervisor status payload."""
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    defaults: dict[str, Any] = {}
    if isinstance(config.get("backend_host"), str):
        defaults["default_server_host"] = config["backend_host"]
    if _is_positive_int(config.get("backend_port")):
        defaults["default_server_port"] = config["backend_port"]
    if isinstance(config.get("frontend_host"), str):
        defaults["default_webui_host"] = config["frontend_host"]
    if _is_positive_int(config.get("frontend_port")):
        defaults["default_webui_port"] = config["frontend_port"]
    return defaults


def build_service_config(
    *,
    no_browser: bool = False,
    skip_webui_build: bool = False,
    server_host: str | None = None,
    server_port: int | None = None,
    webui_host: str | None = None,
    webui_port: int | None = None,
    default_server_host: str,
    default_server_port: int,
    default_webui_host: str = "127.0.0.1",
    default_webui_port: int = 5173,
) -> ServiceConfig:
    """Build service config from CLI values, environment, and defaults."""
    return ServiceConfig(
        backend_host=_resolve_host(
            cli_value=server_host,
            env_names=("FLOCKS_SERVER_HOST", "FLOCKS_BACKEND_HOST"),
            default=default_server_host,
        ),
        backend_port=_resolve_port(
            cli_value=server_port,
            env_names=("FLOCKS_SERVER_PORT", "FLOCKS_BACKEND_PORT"),
            default=default_server_port,
            label="server",
        ),
        frontend_host=_resolve_host(
            cli_value=webui_host,
            env_names=("FLOCKS_WEBUI_HOST", "FLOCKS_FRONTEND_HOST"),
            default=default_webui_host,
        ),
        frontend_port=_resolve_port(
            cli_value=webui_port,
            env_names=("FLOCKS_WEBUI_PORT", "FLOCKS_FRONTEND_PORT"),
            default=default_webui_port,
            label="webui",
        ),
        no_browser=no_browser,
        skip_frontend_build=skip_webui_build,
    )


def with_frontend_build(config: ServiceConfig, *, skip_frontend_build: bool) -> ServiceConfig:
    """Return config with only the WebUI build behavior changed."""
    return ServiceConfig(
        backend_host=config.backend_host,
        backend_port=config.backend_port,
        frontend_host=config.frontend_host,
        frontend_port=config.frontend_port,
        no_browser=config.no_browser,
        skip_frontend_build=skip_frontend_build,
    )


def _resolve_host(*, cli_value: str | None, env_names: tuple[str, ...], default: str) -> str:
    if cli_value is not None:
        return cli_value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return default


def _resolve_port(*, cli_value: int | None, env_names: tuple[str, ...], default: int, label: str) -> int:
    if cli_value is not None:
        return cli_value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if not env_value:
            continue
        try:
            return int(env_value)
        except ValueError as error:
            raise ServiceConfigError(f"{label} port from {env_name} must be an integer.") from error
    return default


def _string(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _positive_int(value: Any, fallback: int) -> int:
    return value if _is_positive_int(value) else fallback


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bool(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback
