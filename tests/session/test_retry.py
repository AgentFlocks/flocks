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

    def test_stream_tool_arguments_truncated_error_is_not_retryable(self):
        # The runner feeds a cut-off tool call back to the model as a tool
        # error; replaying the same request must not happen.
        error = {
            "name": "StreamToolArgumentsTruncatedError",
            "data": {"message": "tool arguments were truncated"},
        }
        result = SessionRetry.retryable(error)
        assert result is None

    def test_rejected_raised_output_limit_is_retried(self):
        """The retry is rebuilt with the configured output limit, which the
        provider accepted before the runner raised it."""
        error = {
            "name": "OutputLimitRejectedError",
            "data": {"message": "The provider rejected max_tokens=16384", "statusCode": 400},
        }
        assert SessionRetry.retryable(error) is not None

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

    def test_string_message_incomplete_chunked_read_is_connection_error(self):
        error = {
            "name": "RemoteProtocolError",
            "data": {
                "message": (
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )
            },
        }
        result = SessionRetry.retryable(error)
        assert result == "Connection or Server Error"
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
    def test_five_retry_schedule(self):
        assert [SessionRetry.delay(attempt) for attempt in range(1, 6)] == [
            2_000,
            4_000,
            8_000,
            16_000,
            30_000,
        ]

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


# ---------------------------------------------------------------------------
# jittered_delay() / retry_after_ms() / is_rate_limited()
# ---------------------------------------------------------------------------

class TestJitteredDelay:
    def test_plain_backoff_stays_within_half_to_full_base(self):
        for attempt, base in zip(range(1, 6), [2000, 4000, 8000, 16000, 30_000]):
            samples = {SessionRetry.jittered_delay(attempt) for _ in range(200)}
            assert all(base // 2 <= sample <= base for sample in samples), (attempt, samples)
            # Actually spreads: more than one distinct value out of 200 draws.
            assert len(samples) > 1

    def test_never_exceeds_explicit_cap(self):
        samples = [SessionRetry.jittered_delay(9, max_delay_ms=5000) for _ in range(100)]
        assert all(2500 <= sample <= 5000 for sample in samples)

    def test_retry_after_is_a_lower_bound_plus_up_to_25_percent(self):
        error = {"data": {"responseHeaders": {"retry-after": "3"}}}
        samples = [SessionRetry.jittered_delay(1, error) for _ in range(100)]
        assert all(3000 <= sample <= 3750 for sample in samples)

    def test_retry_after_ms_header_wins_and_header_case_is_ignored(self):
        error = {"data": {"responseHeaders": {"Retry-After-Ms": "500", "retry-after": "9"}}}
        assert SessionRetry.retry_after_ms(error) == 500
        samples = [SessionRetry.jittered_delay(1, error) for _ in range(50)]
        assert all(500 <= sample <= 625 for sample in samples)

    def test_retry_after_ms_returns_none_without_usable_headers(self):
        assert SessionRetry.retry_after_ms(None) is None
        assert SessionRetry.retry_after_ms({}) is None
        assert SessionRetry.retry_after_ms({"data": {"responseHeaders": {"retry-after": "soon"}}}) is None
        assert SessionRetry.retry_after_ms({"data": {"responseHeaders": {"retry-after": "0"}}}) is None


class TestIsRateLimited:
    def test_status_code_429(self):
        assert SessionRetry.is_rate_limited({"data": {"statusCode": 429, "message": "x"}}) is True
        assert SessionRetry.is_rate_limited({"data": {"statusCode": "429"}}) is True

    def test_message_patterns(self):
        assert SessionRetry.is_rate_limited({"data": {"message": "Too Many Requests"}}) is True
        assert SessionRetry.is_rate_limited({"data": {"message": "rate_limit_exceeded"}}) is True
        assert SessionRetry.is_rate_limited({"message": "HTTP 429"}) is True

    def test_status_code_wins_over_message_text(self):
        # Provider errors carry request ids and quota hints; a 401 whose body
        # mentions 429 must not cool down the whole provider.
        assert SessionRetry.is_rate_limited({
            "data": {"statusCode": 401, "message": "Invalid API key; request_id=req_429abc"}
        }) is False
        assert SessionRetry.is_rate_limited({
            "data": {"statusCode": 500, "message": "rate limit"}
        }) is False
        assert SessionRetry.is_rate_limited({
            "data": {"statusCode": 429, "message": "slow down"}
        }) is True
        # Unparseable status falls back to the message.
        assert SessionRetry.is_rate_limited({
            "data": {"statusCode": "n/a", "message": "Too Many Requests"}
        }) is True

    def test_other_errors_are_not_rate_limits(self):
        assert SessionRetry.is_rate_limited({"data": {"statusCode": 500, "message": "boom"}}) is False
        assert SessionRetry.is_rate_limited({"data": {"message": "connection reset"}}) is False
        assert SessionRetry.is_rate_limited({"data": {"message": None}}) is False
