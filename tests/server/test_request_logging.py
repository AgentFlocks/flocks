"""Request logging noise filters."""

import pytest

from flocks.server import app as server_app
from flocks.server.route_extensions import register_polling_path, reset_route_paths


@pytest.fixture(autouse=True)
def _clean_route_paths():
    reset_route_paths()
    yield
    reset_route_paths()


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/api/health",
        "/api/event",
        "/api/session/status",
        "/api/session/ses_123/status",
        "/session/ses_123/status",
        "/api/session/ses_123/message",
        "/api/question/session/ses_123/pending",
    ],
)
def test_successful_polling_requests_are_not_logged(path: str) -> None:
    assert not server_app._should_log_request(path, 200)


@pytest.mark.parametrize(
    "path",
    [
        "/api/provider",
        "/api/tools",
        "/api/session",
    ],
)
def test_regular_successful_requests_are_logged(path: str) -> None:
    assert server_app._should_log_request(path, 200)


def test_noisy_request_errors_are_still_logged() -> None:
    assert server_app._should_log_request("/api/session/ses_123/message", 500)


def test_a_probe_nothing_registered_is_logged_like_any_request() -> None:
    assert server_app._should_log_request("/ready", 200)
    assert server_app._should_log_request("/api/ready", 200)


def test_a_registered_polling_path_is_quiet_only_while_it_succeeds() -> None:
    register_polling_path("/ready")

    assert not server_app._should_log_request("/ready", 200)
    assert server_app._should_log_request("/ready", 503)
    # The path itself, not the paths under it.
    assert server_app._should_log_request("/ready/details", 200)
