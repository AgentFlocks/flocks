"""
Tests for flocks/session/lifecycle/retry.py

Covers:
- SessionRetry.retryable(): error classification
- SessionRetry.delay(): exponential backoff and retry-after headers
- SessionRetry.sleep(): abort-aware sleep
"""

import asyncio
import pytest

from flocks.session.lifecycle.retry import (
    RETRY_BACKOFF_FACTOR,
    RETRY_INITIAL_DELAY,
    RETRY_MAX_DELAY_NO_HEADERS,
    SessionRetry,
)


# ---------------------------------------------------------------------------
# retryable()
# ---------------------------------------------------------------------------

class TestRetryable:
    def test_non_api_error_with_no_pattern_returns_none(self):
        error = {"name": "SomeOtherError", "data": {"message": "something went wrong"}}
        assert SessionRetry.retryable(error) is None

    def test_api_error_not_retryable_flag(self):
        error = {
            "name": "APIError",
            "data": {"isRetryable": False, "message": "Bad Request"},
        }
        assert SessionRetry.retryable(error) is None

    def test_api_error_retryable_overloaded(self):
        error = {
            "name": "APIError",
            "data": {"isRetryable": True, "message": "Overloaded"},
        }
        result = SessionRetry.retryable(error)
        assert result == "Provider is overloaded"

    def test_api_error_retryable_generic(self):
        error = {
            "name": "APIError",
            "data": {"isRetryable": True, "message": "Internal server error"},
        }
        result = SessionRetry.retryable(error)
        assert result == "Internal server error"

    def test_json_message_too_many_requests(self):
        import json
        msg = json.dumps({"type": "error", "error": {"type": "too_many_requests"}})
        error = {"name": "UnknownError", "data": {"message": msg}}
        result = SessionRetry.retryable(error)
        assert result == "Too Many Requests"

    def test_json_message_server_error(self):
        import json
        msg = json.dumps({"type": "error", "error": {"type": "server_error"}})
        error = {"name": "UnknownError", "data": {"message": msg}}
        result = SessionRetry.retryable(error)
        assert result == "Provider Server Error"

    def test_json_message_rate_limit_code(self):
        import json
        msg = json.dumps({"type": "error", "error": {"type": "other", "code": "rate_limit_exceeded"}})
        error = {"name": "UnknownError", "data": {"message": msg}}
        result = SessionRetry.retryable(error)
        assert result == "Rate Limited"

    def test_json_message_exhausted_code(self):
        import json
        msg = json.dumps({"code": "resource_exhausted"})
        error = {"name": "UnknownError", "data": {"message": msg}}
        result = SessionRetry.retryable(error)
        assert result == "Provider is overloaded"

    def test_string_message_rate_limit_pattern(self):
        error = {"name": "NetworkError", "data": {"message": "429 rate limit exceeded"}}
        result = SessionRetry.retryable(error)
        assert result is not None
        assert "Rate" in result or "rate" in result.lower()

    def test_string_message_overloaded_pattern(self):
        error = {"name": "NetworkError", "data": {"message": "Service is overloaded"}}
        result = SessionRetry.retryable(error)
        assert result is not None

    def test_string_message_timeout_pattern(self):
        error = {"name": "NetworkError", "data": {"message": "Request timed out"}}
        result = SessionRetry.retryable(error)
        assert result is not None

    def test_string_message_connection_error_pattern(self):
        error = {
            "name": "APIConnectionError",
            "data": {"message": "Connection error."},
        }
        result = SessionRetry.retryable(error)
        assert result is not None
        assert SessionRetry.is_connection_error(error) is True

    def test_empty_error_returns_none(self):
        assert SessionRetry.retryable({}) is None

    def test_missing_data_returns_none(self):
        error = {"name": "SomeError"}
        assert SessionRetry.retryable(error) is None


# ---------------------------------------------------------------------------
# delay()
# ---------------------------------------------------------------------------

class TestDelay:
    def test_attempt_1_returns_initial_delay(self):
        result = SessionRetry.delay(1)
        assert result == RETRY_INITIAL_DELAY

    def test_attempt_2_doubles(self):
        result = SessionRetry.delay(2)
        expected = RETRY_INITIAL_DELAY * RETRY_BACKOFF_FACTOR
        assert result == expected

    def test_attempt_3_quadruples(self):
        result = SessionRetry.delay(3)
        expected = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** 2)
        assert result == expected

    def test_caps_at_max_delay_no_headers(self):
        # Very large attempt number
        result = SessionRetry.delay(100)
        assert result <= RETRY_MAX_DELAY_NO_HEADERS

    def test_retry_after_ms_header(self):
        error = {
            "data": {
                "responseHeaders": {"retry-after-ms": "5000"}
            }
        }
        result = SessionRetry.delay(1, error)
        assert result == 5000

    def test_retry_after_seconds_header(self):
        error = {
            "data": {
                "responseHeaders": {"retry-after": "10"}
            }
        }
        result = SessionRetry.delay(1, error)
        assert result == 10000  # converted to ms

    def test_retry_after_ms_takes_priority_over_seconds(self):
        error = {
            "data": {
                "responseHeaders": {
                    "retry-after-ms": "3000",
                    "retry-after": "60",
                }
            }
        }
        result = SessionRetry.delay(1, error)
        assert result == 3000

    def test_invalid_retry_after_ms_falls_back_to_backoff(self):
        error = {
            "data": {
                "responseHeaders": {"retry-after-ms": "not_a_number"}
            }
        }
        result = SessionRetry.delay(1, error)
        # Falls back to backoff with headers (uncapped)
        assert result == RETRY_INITIAL_DELAY

    def test_empty_headers_uses_backoff(self):
        error = {"data": {"responseHeaders": {}}}
        result = SessionRetry.delay(1, error)
        assert result == RETRY_INITIAL_DELAY

    def test_no_error_uses_capped_backoff(self):
        result = SessionRetry.delay(1, None)
        assert result == RETRY_INITIAL_DELAY

    def test_zero_retry_after_not_used(self):
        error = {
            "data": {
                "responseHeaders": {"retry-after-ms": "0"}
            }
        }
        # 0 is not > 0, so falls back to backoff
        result = SessionRetry.delay(1, error)
        assert result == RETRY_INITIAL_DELAY


# ---------------------------------------------------------------------------
# sleep()
# ---------------------------------------------------------------------------

class TestSleep:
    @pytest.mark.asyncio
    async def test_sleep_completes_normally(self):
        abort = asyncio.Event()
        # 50ms sleep should complete quickly
        await SessionRetry.sleep(50, abort)

    @pytest.mark.asyncio
    async def test_sleep_aborted_raises_cancelled(self):
        abort = asyncio.Event()
        abort.set()  # Already aborted
        with pytest.raises(asyncio.CancelledError):
            await SessionRetry.sleep(5000, abort)

    @pytest.mark.asyncio
    async def test_sleep_aborted_mid_sleep(self):
        abort = asyncio.Event()

        async def set_abort_soon():
            await asyncio.sleep(0.05)
            abort.set()

        asyncio.create_task(set_abort_soon())
        with pytest.raises(asyncio.CancelledError):
            await SessionRetry.sleep(10000, abort)
