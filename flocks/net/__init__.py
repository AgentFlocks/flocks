"""
Outbound network policy shared by every client in the process.

One place decides whether a request goes direct or through a proxy
(``flocks.json`` → ``network.proxy``, or the usual ``HTTP_PROXY`` /
``HTTPS_PROXY`` / ``ALL_PROXY`` / ``NO_PROXY`` variables), and the helpers here
hand that decision to each HTTP stack the code base uses:

- :func:`get_proxy_settings` / :func:`proxy_url_for` — the decision itself,
  including the bypass list (intranet ranges, ``*.corp.example``, ``host:port``,
  CIDR blocks) so internal targets never leave through the proxy.
- :func:`apply_process_environment` — writes the decision into the process
  environment at startup so libraries that follow ``trust_env`` (openai,
  anthropic, google-genai, litellm, requests, urllib, mcp, ...) and child
  processes (git, uv, npm, docker, MCP stdio servers) pick it up without code.
- :mod:`flocks.net.http` — httpx clients whose transport routes per request.
- :mod:`flocks.net.aio` — aiohttp sessions with HTTP / SOCKS connectors.
- :func:`requests_proxies`, :func:`chromium_proxy_args`, :func:`child_env` —
  the same decision for requests, headless Chrome and subprocess environments.
"""

from flocks.net.proxy import (
    DEFAULT_BYPASS_RULES,
    PROXY_ENV_NAMES,
    ProxySettings,
    apply_process_environment,
    chromium_proxy_args,
    child_env,
    container_env,
    describe_for_log,
    get_proxy_settings,
    invalidate_proxy_settings,
    is_bypassed,
    load_proxy_settings,
    proxy_url_for,
    requests_proxies,
)

__all__ = [
    "DEFAULT_BYPASS_RULES",
    "PROXY_ENV_NAMES",
    "ProxySettings",
    "apply_process_environment",
    "chromium_proxy_args",
    "child_env",
    "container_env",
    "describe_for_log",
    "get_proxy_settings",
    "invalidate_proxy_settings",
    "is_bypassed",
    "load_proxy_settings",
    "proxy_url_for",
    "requests_proxies",
]
