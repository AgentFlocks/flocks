"""
Tests for generic route registration.

Extensions contribute routes by registering them; the application never imports
an extension by name.
"""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from flocks.server.route_extensions import (
    backend_paths,
    bind_route_app,
    installed_route_providers,
    polling_paths,
    register_backend_path,
    register_polling_path,
    register_route_provider,
    registered_route_providers,
    reset_route_paths,
    reset_route_providers,
    route_exists,
)

@pytest.fixture(autouse=True)
def _clean_registry():
    reset_route_providers()
    reset_route_paths()
    yield
    reset_route_providers()
    reset_route_paths()


def _probe_router(path: str = "/probe", payload: str = "ok") -> APIRouter:
    router = APIRouter()

    @router.get(path)
    async def _probe() -> dict:
        return {"value": payload}

    return router


# ---------------------------------------------------------------------------
# E1-E2: registration order does not matter
# ---------------------------------------------------------------------------

def test_router_registered_before_the_app_is_installed_on_bind():
    """E1: an extension loading early still gets its routes mounted."""
    register_route_provider(_probe_router(), name="early", prefix="/ext")

    app = FastAPI()
    bind_route_app(app)

    assert installed_route_providers() == ["early"]
    assert TestClient(app).get("/ext/probe").json() == {"value": "ok"}


def test_router_registered_after_the_app_is_installed_immediately():
    """E2: an extension loading late does not have to wait for a rebind."""
    app = FastAPI()
    bind_route_app(app)

    register_route_provider(_probe_router(payload="late"), name="late", prefix="/ext")

    assert TestClient(app).get("/ext/probe").json() == {"value": "late"}


# ---------------------------------------------------------------------------
# E3-E5: registry behaviour
# ---------------------------------------------------------------------------

def test_duplicate_provider_name_is_rejected():
    """E3: two providers sharing a name make mounting ambiguous."""
    register_route_provider(_probe_router(), name="dup")

    with pytest.raises(ValueError, match="已注册"):
        register_route_provider(_probe_router(), name="dup")

    assert registered_route_providers() == ["dup"]


def test_one_bad_router_does_not_block_the_others():
    """E4: a broken extension costs its own routes, not everyone else's."""
    app = FastAPI()

    class _Broken:
        routes = []

    register_route_provider(_Broken(), name="broken")
    register_route_provider(_probe_router(), name="good", prefix="/ext")
    bind_route_app(app)

    assert "good" in installed_route_providers()
    assert TestClient(app).get("/ext/probe").status_code == 200


def test_route_exists_detects_a_mounted_path():
    """E5: an extension can check before re-registering a path."""
    app = FastAPI()
    register_route_provider(_probe_router(), name="probe", prefix="/ext")
    bind_route_app(app)

    assert route_exists(app, "/ext/probe", "GET") is True
    assert route_exists(app, "/ext/probe", "POST") is False
    assert route_exists(app, "/ext/absent", "GET") is False


# ---------------------------------------------------------------------------
# E6: the generic license route replaces the product-named one
# ---------------------------------------------------------------------------

def test_generic_license_route_reports_the_registered_checker(monkeypatch):
    """E6: one endpoint shape, whichever checker is installed."""
    from flocks.server.routes.license import router as license_router

    app = FastAPI()
    app.include_router(license_router, prefix="/api/license")
    monkeypatch.setattr("flocks.server.routes.license.require_user", lambda request: None)

    body = TestClient(app).get("/api/license/status").json()

    assert body["activated"] is True
    assert body["active"] is True


# ---------------------------------------------------------------------------
# How the server treats an extension's paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("/ready", "/ready"),
    ("/ready/", "/ready"),
    (" /api/ready ", "/api/ready"),
])
def test_registered_paths_are_normalized(raw: str, expected: str) -> None:
    register_backend_path(raw)
    register_polling_path(raw)

    assert backend_paths() == {expected}
    assert polling_paths() == {expected}


@pytest.mark.parametrize("raw", ["", "ready", "/", "//"])
def test_a_registration_that_names_no_path_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        register_backend_path(raw)
    with pytest.raises(ValueError):
        register_polling_path(raw)

    assert backend_paths() == frozenset()
    assert polling_paths() == frozenset()


def test_the_extension_facade_registers_paths() -> None:
    from flocks import extensions

    extensions.register_backend_path("/ready")
    extensions.register_polling_path("/api/ready")

    assert backend_paths() == {"/ready"}
    assert polling_paths() == {"/api/ready"}
