"""
Tests for the /ready probe a load balancer routes on.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.plugin.readiness import (
    ENV_REQUIRED_PLUGINS,
    register_readiness_probe,
    reset_readiness_cache,
    reset_readiness_probes,
)
from flocks.server.routes.health import router


@pytest.fixture(autouse=True)
def _clean_readiness_state():
    """Each test sees a fresh cache and no probes from its neighbours."""
    reset_readiness_probes()
    reset_readiness_cache()
    yield
    reset_readiness_probes()
    reset_readiness_cache()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.critical_plugin_entrypoint_failures = ()
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def client_with_failed_plugin() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.critical_plugin_entrypoint_failures = ("broken_pkg",)
    return TestClient(app, raise_server_exceptions=True)


def test_ready_returns_200_when_nothing_is_required(client, monkeypatch):
    """A stock install declares nothing, so it is ready."""
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_ready_returns_503_when_a_required_plugin_is_missing(client, monkeypatch):
    """An instance missing a mandatory extension is taken out of rotation.

    Answering 200 here would route traffic to an instance applying different
    rules from its peers, which is worse than routing to one fewer instance.
    """
    monkeypatch.setenv(ENV_REQUIRED_PLUGINS, "not_installed_pkg")

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_public_readiness_body_carries_only_the_flag(client, monkeypatch):
    """The unauthenticated probe must not enumerate what is installed.

    This endpoint has to be reachable without a login so a proxy can poll it.
    Returning the readiness report there handed any anonymous caller the list
    of installed extensions, the names a deployment declared mandatory, and the
    text of load errors -- enough to identify which add-ons a site runs. A load
    balancer needs the status code and nothing else.
    """
    monkeypatch.setenv(ENV_REQUIRED_PLUGINS, "not_installed_pkg")

    body = client.get("/ready").json()

    assert set(body) == {"ready"}
    serialised = repr(body)
    for leaked in ("installed_plugins", "not_installed_pkg", "failed_entrypoints"):
        assert leaked not in serialised


def test_details_endpoint_requires_a_user(client, monkeypatch):
    """The inventory is available, but only to someone signed in."""
    monkeypatch.setenv(ENV_REQUIRED_PLUGINS, "not_installed_pkg")

    response = client.get("/ready/details")

    assert response.status_code == 401


def test_details_endpoint_reports_the_inventory_for_a_user(client, monkeypatch):
    """A signed-in operator diagnosing a 503 gets the full report."""
    monkeypatch.setenv(ENV_REQUIRED_PLUGINS, "not_installed_pkg")

    import flocks.server.auth as auth

    monkeypatch.setattr(auth, "get_optional_user", lambda request: object())

    response = client.get("/ready/details")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["extensions"]["missing_plugins"] == ["not_installed_pkg"]


def test_health_stays_up_even_when_not_ready(client, monkeypatch):
    """/health answers "the process is running"; /ready answers "it can serve".

    Keeping them separate lets an operator tell a crashed instance from a
    running one that is missing an extension.
    """
    monkeypatch.setenv(ENV_REQUIRED_PLUGINS, "not_installed_pkg")

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503


def test_ready_reports_a_plugin_that_loaded_and_failed(client_with_failed_plugin, monkeypatch):
    """An instance whose critical plugin failed must not keep taking traffic.

    The failure is recorded at startup and cannot be rediscovered from inside
    the request, so the endpoint has to read it off application state. Missing
    that wiring made the endpoint answer "ready" for an instance that had
    logged a critical plugin failure seconds earlier -- the process was up,
    but not in a state to serve.
    """
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)

    response = client_with_failed_plugin.get("/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_ready_handles_an_app_without_the_state_attribute(client):
    """A bare app must not 500 the probe just because state was never set."""
    del client.app.state.critical_plugin_entrypoint_failures

    assert client.get("/ready").status_code == 200


def test_a_failing_dependency_probe_takes_the_instance_out_of_rotation(client, monkeypatch):
    """Readiness covers what the instance depends on, not only its wiring.

    An extension registered and holding a dead database connection is wired
    correctly and cannot serve; the deployment's load balancer has to see that.
    """
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)

    async def database_down():
        return False, "connection refused"

    register_readiness_probe("postgres", database_down)

    assert client.get("/ready").status_code == 503


def test_a_probe_that_hangs_counts_as_not_ready(client, monkeypatch):
    """"Cannot tell" and "not ready" have the same answer for a proxy."""
    import asyncio

    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)
    monkeypatch.setattr("flocks.plugin.readiness._PROBE_TIMEOUT_S", 0.05)

    async def never_answers():
        await asyncio.sleep(30)
        return True, "ok"

    register_readiness_probe("core", never_answers)

    response = client.get("/ready")

    assert response.status_code == 503
    reset_readiness_cache()


def test_a_passing_probe_keeps_the_instance_in_rotation(client, monkeypatch):
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)

    async def healthy():
        return True, "ok"

    register_readiness_probe("core", healthy)

    assert client.get("/ready").status_code == 200


def test_the_answer_is_cached_between_probes(client, monkeypatch):
    """A proxy polls every couple of seconds per node; recomputing each time
    is a steady cost for an answer that changes far more slowly."""
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)

    calls = {"n": 0}

    async def counting():
        calls["n"] += 1
        return True, "ok"

    register_readiness_probe("core", counting)

    for _ in range(5):
        assert client.get("/ready").status_code == 200

    assert calls["n"] == 1



def test_unprefixed_health_and_ready_aliases_are_mounted(monkeypatch):
    """``/health`` and ``/ready`` are public in server/auth.py; they must
    resolve to the same handlers as their ``/api`` counterparts."""
    from flocks.server.routes.health import public_router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(public_router)
    app.state.critical_plugin_entrypoint_failures = ()
    client = TestClient(app, raise_server_exceptions=True)

    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "healthy"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json() == {"ready": True}
    assert client.get("/api/health").status_code == 200
    # Only the two probe paths get the alias; /ping stays under /api.
    assert client.get("/ping").status_code == 404


def test_health_answers_head_requests():
    """Load-balancer probes commonly use HEAD; it must not be a 405."""
    from flocks.server.routes.health import public_router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(public_router)
    client = TestClient(app, raise_server_exceptions=True)

    for path in ("/api/health", "/health"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b"", path
        assert client.get(path).json()["status"] == "healthy"


@pytest.fixture
def client_with_both_routers() -> TestClient:
    from flocks.server.routes.health import public_router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(public_router)
    app.state.critical_plugin_entrypoint_failures = ()
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.parametrize("path", ["/ready", "/api/ready"])
def test_an_upgrade_learns_whether_the_release_it_started_answers(client_with_both_routers, monkeypatch, path):
    """A release upgrade asks for its release; a different one on the port answers "other" with 503."""
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)
    monkeypatch.setenv("FLOCKS_RELEASE_TAG", "3f2a9c41d7e0b6a58c1f2e3d4b5a6978")
    monkeypatch.setenv("FLOCKS_RELEASE_STARTED_AT", "1792000000500")
    client = client_with_both_routers

    same = client.get(path, params={"release": "3f2a9c41d7e0b6a58c1f2e3d4b5a6978", "started_after": 1792000000000})
    assert (same.status_code, same.json()) == (200, {"ready": True, "release": "match"})

    other = client.get(path, params={"release": "0" * 32})
    assert (other.status_code, other.json()) == (503, {"ready": False, "release": "other"})

    earlier = client.get(path, params={"release": "3f2a9c41d7e0b6a58c1f2e3d4b5a6978", "started_after": 1792000009000})
    assert (earlier.status_code, earlier.json()["release"]) == (503, "other"), "started before the restart"

    assert set(client.get(path).json()) == {"ready"}, "without the question the body stays just the flag"


def test_a_process_started_from_no_release_ignores_the_question(client, monkeypatch):
    monkeypatch.delenv(ENV_REQUIRED_PLUGINS, raising=False)
    monkeypatch.delenv("FLOCKS_RELEASE_TAG", raising=False)

    response = client.get("/ready", params={"release": "3f2a9c41d7e0b6a58c1f2e3d4b5a6978"})

    assert (response.status_code, response.json()) == (200, {"ready": True})
    assert client.get("/ready", params={"started_after": -1}).status_code == 422
