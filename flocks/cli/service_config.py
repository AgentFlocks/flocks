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
    backend_port: int = 5173
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 5173
    legacy_backend_host: str | None = "127.0.0.1"
    legacy_backend_port: int | None = 8000
    server_port_migration_hint: bool = False
    no_browser: bool = False
    skip_frontend_build: bool = False

    @property
    def backend_url(self) -> str:
        return f"http://{_format_host_for_url(loopback_host(self.backend_host))}:{self.backend_port}"

    @property
    def frontend_url(self) -> str:
        return self.backend_url

    @property
    def legacy_cleanup_config(self) -> "ServiceConfig":
        return ServiceConfig(
            backend_host=self.legacy_backend_host or self.backend_host,
            backend_port=self.legacy_backend_port or self.backend_port,
            frontend_host=self.frontend_host,
            frontend_port=self.frontend_port,
            no_browser=self.no_browser,
            server_port_migration_hint=self.server_port_migration_hint,
            skip_frontend_build=self.skip_frontend_build,
        )


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
        "legacy_backend_host": config.legacy_backend_host,
        "legacy_backend_port": config.legacy_backend_port,
        "server_port_migration_hint": config.server_port_migration_hint,
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
        legacy_backend_host=_optional_string(payload.get("legacy_backend_host"), base.legacy_backend_host),
        legacy_backend_port=_optional_positive_int(payload.get("legacy_backend_port"), base.legacy_backend_port),
        server_port_migration_hint=_bool(payload.get("server_port_migration_hint"), base.server_port_migration_hint),
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
    public_host: str | None = None,
    public_port: int | None = None,
    server_host: str | None = None,
    server_port: int | None = None,
    webui_host: str | None = None,
    webui_port: int | None = None,
    default_server_host: str,
    default_server_port: int,
    default_webui_host: str = "127.0.0.1",
    default_webui_port: int = 5173,
) -> ServiceConfig:
    """Build service config from CLI values, environment, and defaults.

    Static WebUI mode uses the old WebUI endpoint as the public FastAPI
    listener so remote deployments keep their existing browser URL.
    """
    explicit_public_host = _first_host(public_host, ("FLOCKS_HOST", "FLOCKS_PUBLIC_HOST"))
    explicit_public_port = _first_port(public_port, ("FLOCKS_PORT", "FLOCKS_PUBLIC_PORT"), "public")
    explicit_webui_host = _first_host(webui_host, ("FLOCKS_WEBUI_HOST", "FLOCKS_FRONTEND_HOST"))
    explicit_webui_port = _first_port(webui_port, ("FLOCKS_WEBUI_PORT", "FLOCKS_FRONTEND_PORT"), "webui")
    explicit_server_host = _first_host(server_host, ("FLOCKS_SERVER_HOST", "FLOCKS_BACKEND_HOST"))
    explicit_server_port = _first_port(server_port, ("FLOCKS_SERVER_PORT", "FLOCKS_BACKEND_PORT"), "server")

    resolved_public_host = explicit_public_host or explicit_webui_host or explicit_server_host or default_webui_host
    resolved_public_port = explicit_public_port or explicit_webui_port or explicit_server_port or default_webui_port
    legacy_host = explicit_server_host or default_server_host
    legacy_port = explicit_server_port or default_server_port
    show_server_port_hint = (
        explicit_server_port is not None
        and (explicit_public_port is not None or explicit_webui_port is not None)
        and explicit_server_port != resolved_public_port
    )

    return ServiceConfig(
        backend_host=resolved_public_host,
        backend_port=resolved_public_port,
        frontend_host=resolved_public_host,
        frontend_port=resolved_public_port,
        legacy_backend_host=legacy_host,
        legacy_backend_port=legacy_port,
        server_port_migration_hint=show_server_port_hint,
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
        legacy_backend_host=config.legacy_backend_host,
        legacy_backend_port=config.legacy_backend_port,
        server_port_migration_hint=config.server_port_migration_hint,
        no_browser=config.no_browser,
        skip_frontend_build=skip_frontend_build,
    )


def _first_host(cli_value: str | None, env_names: tuple[str, ...]) -> str | None:
    if cli_value is not None:
        return cli_value
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return None


def _first_port(cli_value: int | None, env_names: tuple[str, ...], label: str) -> int | None:
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
    return None


def _string(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _optional_string(value: Any, fallback: str | None) -> str | None:
    return value if isinstance(value, str) and value else fallback


def _positive_int(value: Any, fallback: int) -> int:
    return value if _is_positive_int(value) else fallback


def _optional_positive_int(value: Any, fallback: int | None) -> int | None:
    return value if _is_positive_int(value) else fallback


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bool(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback
