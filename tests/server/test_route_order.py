"""Guard against static API paths hidden behind earlier dynamic routes.

Starlette matches routes in registration order, so a ``GET /tools`` declared
after ``GET /{name}`` is never reached: "tools" is taken as a name and the
request 404s (FL-BUG-005 hit MCP, session and workflow routes this way).
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute

_PARAM = re.compile(r"\{[^}]+\}")
_SAMPLE = "zz9param"


def _shadowed_routes(routes: list[APIRoute]) -> list[str]:
    shadowed = []
    for index, route in enumerate(routes):
        sample = _PARAM.sub(_SAMPLE, route.path)
        segments = sample.strip("/").split("/")
        for earlier in routes[:index]:
            methods = (route.methods or set()) & (earlier.methods or set())
            if not methods or not earlier.path_regex.match(sample):
                continue
            earlier_segments = earlier.path.strip("/").split("/")
            # Only a parameter swallowing a literal segment hides the route;
            # two routes with the same shape are a separate (duplicate) issue.
            swallows_literal = any(":path}" in segment for segment in earlier_segments) or any(
                segment != _SAMPLE and _PARAM.fullmatch(other)
                for segment, other in zip(segments, earlier_segments)
            )
            if swallows_literal:
                shadowed.append(f"{','.join(sorted(methods))} {route.path} <- {earlier.path}")
            break
    return shadowed


def test_detector_flags_a_static_route_declared_after_a_dynamic_one():
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/items/{name}")
    async def item(name: str):
        return name

    @app.get("/items/stats")
    async def stats():
        return {}

    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert _shadowed_routes(routes) == ["GET /items/stats <- /items/{name}"]


def test_no_static_route_is_shadowed_by_an_earlier_dynamic_route():
    from flocks.server.app import app

    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert _shadowed_routes(routes) == []
