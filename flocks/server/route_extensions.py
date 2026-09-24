"""
Generic route registration for extensions.

An extension that adds HTTP endpoints registers its router here instead of the
application importing it by name.  That keeps the direction of the dependency
one-way: extensions know about Flocks, Flocks does not know about any
particular extension.

Registration is order-independent.  A router registered before the application
has finished building is queued and installed once it is; one registered
afterwards is installed immediately.  Extensions load at different points
depending on how they were installed, and none of them should have to know
which side of that line they landed on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from flocks.utils.log import Log

log = Log.create(service="server.routes")


@dataclass(frozen=True)
class RouteProvider:
    """One extension's routes and where they mount."""

    name: str
    router: Any
    prefix: str = ""
    tags: Sequence[str] = field(default_factory=tuple)


class _RouteExtensionRegistry:
    _providers: List[RouteProvider] = []
    _installed: set[str] = set()
    _app: Any = None

    @classmethod
    def register(
        cls,
        router: Any,
        *,
        name: str,
        prefix: str = "",
        tags: Optional[Sequence[str]] = None,
    ) -> None:
        if router is None:
            raise ValueError("router 不能为空")
        if not name:
            raise ValueError("route provider 必须提供 name")
        if any(provider.name == name for provider in cls._providers):
            raise ValueError(f"路由扩展已注册: {name}")

        provider = RouteProvider(name=name, router=router, prefix=prefix, tags=tuple(tags or ()))
        cls._providers.append(provider)
        log.info("server.routes.registered", {"name": name, "prefix": prefix})

        if cls._app is not None:
            cls._install(cls._app, provider)

    @classmethod
    def bind_app(cls, app: Any) -> None:
        """Attach the application and install everything registered so far."""

        cls._app = app
        for provider in cls._providers:
            cls._install(app, provider)

    @classmethod
    def _install(cls, app: Any, provider: RouteProvider) -> None:
        if provider.name in cls._installed:
            return
        try:
            app.include_router(
                provider.router,
                prefix=provider.prefix,
                tags=list(provider.tags) or None,
            )
        except Exception as exc:
            # One extension's bad router must not stop the application from
            # serving the rest of its routes.
            log.error(
                "server.routes.install_failed",
                {"name": provider.name, "error": str(exc)},
            )
            return
        cls._installed.add(provider.name)
        log.info("server.routes.installed", {"name": provider.name, "prefix": provider.prefix})

    @classmethod
    def registered_providers(cls) -> List[str]:
        return [provider.name for provider in cls._providers]

    @classmethod
    def installed_providers(cls) -> List[str]:
        return sorted(cls._installed)

    @classmethod
    def reset(cls) -> None:
        cls._providers = []
        cls._installed = set()
        cls._app = None


register_route_provider = _RouteExtensionRegistry.register
bind_route_app = _RouteExtensionRegistry.bind_app
registered_route_providers = _RouteExtensionRegistry.registered_providers
installed_route_providers = _RouteExtensionRegistry.installed_providers
reset_route_providers = _RouteExtensionRegistry.reset


def route_exists(app: Any, path: str, method: str) -> bool:
    """Whether the application already serves ``method path``.

    Lets an extension avoid re-registering something a higher-priority
    extension already provided.
    """

    target = method.upper()
    for route in getattr(app, "routes", ()):
        if getattr(route, "path", None) != path:
            continue
        if target in (getattr(route, "methods", None) or set()):
            return True
    return False


__all__ = [
    "RouteProvider",
    "bind_route_app",
    "installed_route_providers",
    "register_route_provider",
    "registered_route_providers",
    "reset_route_providers",
    "route_exists",
]
