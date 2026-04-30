"""Request logging noise filters."""

import pytest

from flocks.server import app as server_app


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/api/health",
        "/api/event",
        "/api/session/status",
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
