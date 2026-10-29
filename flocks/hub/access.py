"""What an extension may decide about hub content and the pages it ships.

A registered policy may refuse to install a hub plugin, add what it knows
about each scene suite to the suite listing, refuse to serve a WebUI page,
leave pages out of the page listing and lock workspaces.  With no policy
registered everything installs, opens and is listed as it is.

A policy implements any of these (all optional):

* ``async install_refusal(plugin_type, plugin_id) -> str | None``: why the
  plugin may not be installed or updated now;
* ``async decorate_scene_suites(suites) -> list``: the suite listing as the
  policy sees it -- copies with ``locked``, ``lockReason`` or
  ``annotations`` filled in;
* ``async page_refusal(page_id, store) -> PageRefusal | None``: why a page,
  its assets, data contracts and server code may not be served now (*store*
  is the page store the routes serve from);
* ``async hidden_pages(pages, store) -> set[str]``: page ids to leave out of a listing;
* ``async locked_workspaces(workspaces) -> dict[str, str]``: workspace ids to
  show locked (and leave out of the navigation), each with a reason of the
  policy's own for its part of the page;
* ``contents_changed()``: hub content was installed, updated, removed or
  imported, for policies that keep caches.

Several policies may be registered: the first refusal wins, listings are
narrowed by all of them, and suite decorations are applied in turn.
"""

from __future__ import annotations

import threading
from typing import Any, NamedTuple, Optional

from flocks.utils.log import Log

log = Log.create(service="hub.access")


class PageRefusal(NamedTuple):
    status_code: int
    detail: str


_POLICIES: list[Any] = []
_LOCK = threading.Lock()


def register_policy(policy: Any) -> None:
    with _LOCK:
        if policy not in _POLICIES:
            _POLICIES.append(policy)


def unregister_policy(policy: Any) -> None:
    with _LOCK:
        if policy in _POLICIES:
            _POLICIES.remove(policy)


def registered_policies() -> tuple[Any, ...]:
    with _LOCK:
        return tuple(_POLICIES)


def _methods(name: str) -> list[Any]:
    return [method for policy in registered_policies() if callable(method := getattr(policy, name, None))]


async def install_refusal(plugin_type: str, plugin_id: str) -> Optional[str]:
    """Why a policy refuses to install or update this plugin now; None when none does."""
    for method in _methods("install_refusal"):
        refusal = await method(plugin_type, plugin_id)
        if refusal:
            return str(refusal)
    return None


async def decorate_scene_suites(suites: list[Any]) -> list[Any]:
    for method in _methods("decorate_scene_suites"):
        suites = list(await method(suites))
    return suites


async def page_refusal(page_id: str, store: Any) -> Optional[PageRefusal]:
    """Why a policy refuses to serve this page now; None when none does."""
    for method in _methods("page_refusal"):
        refusal = await method(page_id, store)
        if refusal is not None:
            return PageRefusal(int(refusal[0]), str(refusal[1]))
    return None


async def hidden_pages(pages: list[Any], store: Any) -> set[str]:
    hidden: set[str] = set()
    for method in _methods("hidden_pages"):
        hidden |= set(await method(pages, store))
    return hidden


async def locked_workspaces(workspaces: list[Any]) -> dict[str, str]:
    locked: dict[str, str] = {}
    for method in _methods("locked_workspaces"):
        for workspace_id, reason in dict(await method(workspaces)).items():
            locked.setdefault(workspace_id, str(reason))
    return locked


def contents_changed() -> None:
    """Tell the policies hub content changed; one that fails is logged, not raised."""
    for method in _methods("contents_changed"):
        try:
            method()
        except Exception as exc:
            log.warning("hub.access.contents_changed_failed", {"error": str(exc)})


__all__ = [
    "PageRefusal",
    "contents_changed",
    "decorate_scene_suites",
    "hidden_pages",
    "install_refusal",
    "locked_workspaces",
    "page_refusal",
    "register_policy",
    "registered_policies",
    "unregister_policy",
]
