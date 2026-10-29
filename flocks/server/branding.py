"""
Where the product name shown in the interface comes from.

The open product has one name and one default.  A deployment that ships its own
branding registers a provider here; without one, the default stands.  This is
deliberately a registration point rather than a lookup: asking "is a particular
package installed, and if so use its name" would put that package's name into
open source, which is exactly what the extension mechanism exists to avoid.
"""

from __future__ import annotations

from typing import Any, List, Optional

from flocks.utils.log import Log

log = Log.create(service="server.branding")

DEFAULT_DISPLAY_NAME = "Flocks"

_providers: List[Any] = []


def register_branding_provider(provider: Any) -> None:
    """Register a source for the product display name.

    The provider exposes ``display_name() -> str | None``.  Returning ``None``
    means "no opinion", which keeps whatever the previous provider or the
    default supplied.
    """

    if provider is None:
        raise ValueError("branding provider 不能为空")
    if not callable(getattr(provider, "display_name", None)):
        raise ValueError("branding provider 必须实现 display_name()")
    _providers.append(provider)
    log.info("server.branding.registered", {"provider": type(provider).__name__})


def reset_branding_providers() -> None:
    """Drop every registered provider.  For tests."""

    _providers.clear()


def default_display_name() -> str:
    """The product name to show when the deployment has not configured one.

    Consulted on the request path, so it stays a plain call into whatever is
    registered -- no import probing, no filesystem, no network.  A provider
    that raises is ignored rather than turned into a failed page load: a
    branding lookup is not worth an error page.
    """

    for provider in reversed(_providers):
        try:
            name: Optional[str] = provider.display_name()
        except Exception as exc:
            log.warn("server.branding.provider_failed", {"error": str(exc)})
            continue
        if name:
            return str(name)
    return DEFAULT_DISPLAY_NAME


__all__ = [
    "DEFAULT_DISPLAY_NAME",
    "default_display_name",
    "register_branding_provider",
    "reset_branding_providers",
]
