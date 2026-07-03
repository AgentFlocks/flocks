"""
Session Retry module

Handles retry logic for LLM API failures with exponential backoff.
Based on Flocks' ported src/session/retry.ts
"""

import asyncio
from typing import Optional, Dict, Any

from flocks.utils.log import Log


log = Log.create(service="session.retry")


# Constants matching Flocks
RETRY_INITIAL_DELAY = 2000  # 2 seconds in milliseconds
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY_NO_HEADERS = 30_000  # 30 seconds
RETRY_MAX_DELAY = 2_147_483_647  # max 32-bit signed integer
CONNECTION_ERROR_DISPLAY_MESSAGE = (
    "Model is unavailable. Please check the provider connection and model configuration."
)
CONNECTION_ERROR_PATTERNS = [
    "connection error",
    "connection reset",
    "connection refused",
    "could not connect",
    "failed to connect",
    "api connection",
    "remote protocol error",
    "peer closed",
    "incomplete chunked read",
    "model unavailable",
    "model is unavailable",
    "model not available",
]


class SessionRetry:
    """
    Session Retry namespace
    
    Handles retry logic for API failures with exponential backoff.
    Matches Flocks SessionRetry namespace.
    """
    
    @staticmethod
    async def sleep(ms: int, abort_event: asyncio.Event) -> None:
        """
        Sleep for ms milliseconds, can be aborted
        
        Args:
            ms: Milliseconds to sleep
            abort_event: Event to check for abort
            
        Raises:
            asyncio.CancelledError: If aborted
        """
        delay_seconds = min(ms, RETRY_MAX_DELAY) / 1000.0
        
        try:
            await asyncio.wait_for(
                abort_event.wait(),
                timeout=delay_seconds
            )
            # If we got here, abort was triggered
            raise asyncio.CancelledError("Aborted")
        except asyncio.TimeoutError:
            # Normal completion - timeout elapsed
            pass
    
    @staticmethod
    def delay(attempt: int, error: Optional[Dict[str, Any]] = None) -> int:
        """
        Calculate retry delay in milliseconds
        
        Supports retry-after headers and exponential backoff.
        
        Args:
            attempt: Current retry attempt number (1-indexed)
            error: Error object with optional responseHeaders
            
        Returns:
            Delay in milliseconds
        """
        if error and isinstance(error, dict):
            data = error.get("data", {})
            headers = data.get("responseHeaders", {})
            
            if headers:
                # Check retry-after-ms header
                retry_after_ms = headers.get("retry-after-ms")
                if retry_after_ms:
                    try:
                        parsed_ms = float(retry_after_ms)
                        if 0 < parsed_ms < float('inf'):
                            return int(parsed_ms)
                    except (ValueError, TypeError):
                        pass
                
                # Check retry-after header (in seconds)
                retry_after = headers.get("retry-after")
                if retry_after:
                    try:
                        # Try parsing as seconds
                        parsed_seconds = float(retry_after)
                        if 0 < parsed_seconds < float('inf'):
                            return int(parsed_seconds * 1000)
                    except (ValueError, TypeError):
                        pass
                    
                    # Try parsing as HTTP date (not implemented for simplicity)
                    # In production, use email.utils.parsedate_to_datetime
                
                # Has headers but no valid retry-after, use exponential backoff
                return int(RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)))
        
        # No headers or no error, use capped exponential backoff
        delay_ms = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
        return int(min(delay_ms, RETRY_MAX_DELAY_NO_HEADERS))
    
    @staticmethod
    def retryable(error: Dict[str, Any]) -> Optional[str]:
        """
        Check if an error is retryable
        
        Args:
            error: Error object from MessageV2.fromError
            
        Returns:
            Error message string if retryable, None if not retryable
        """
        error_name = error.get("name", "")
        error_data = error.get("data", {})
        
        # Check if it's an APIError with isRetryable flag
        if error_name == "APIError":
            if not error_data.get("isRetryable", False):
                return None
            
            message = error_data.get("message", "")
            if "Overloaded" in message:
                return "Provider is overloaded"
            return message
        
        # Check error message for retryable patterns
        message = error_data.get("message", "")
        if isinstance(message, str):
            try:
                # Try parsing as JSON for structured errors
                import json
                json_data = json.loads(message)
                
                # Anthropic too_many_requests
                if json_data.get("type") == "error":
                    error_type = json_data.get("error", {}).get("type", "")
                    error_code = json_data.get("error", {}).get("code", "")
                    
                    if error_type == "too_many_requests":
                        return "Too Many Requests"
                    
                    if error_type == "server_error":
                        return "Provider Server Error"
                    
                    if "rate_limit" in error_code:
                        return "Rate Limited"
                
                # Check for exhausted/unavailable codes
                code = json_data.get("code", "")
                if "exhausted" in code or "unavailable" in code:
                    return "Provider is overloaded"
                
                # Check for no_kv_space or other server errors
                error_msg = json_data.get("error", {}).get("message", "")
                if "no_kv_space" in error_msg:
                    return "Provider Server Error"
                
                # Generic error object present
                if json_data.get("error"):
                    return "Provider Server Error"
                    
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
        
        # Check common error patterns in message string
        if isinstance(message, str):
            message_lower = message.lower()
            rate_limit_patterns = [
                "rate limit", "too many requests", "429",
                "overloaded",
            ]
            transient_patterns = [
                "unavailable", "503", "502",
                "timeout", "timed out",
                *CONNECTION_ERROR_PATTERNS,
                "null message", "returned choice with null",
                "empty streaming response", "empty choices",
            ]
            if any(p in message_lower for p in rate_limit_patterns):
                return "Rate Limited"
            if any(p in message_lower for p in transient_patterns):
                return "Connection or Server Error"
        
        return None

    @staticmethod
    def is_connection_error(error: Dict[str, Any]) -> bool:
        """Return True for provider/model connection failures."""
        data = error.get("data", {})
        message = data.get("message") or error.get("message", "")
        if not isinstance(message, str):
            return False
        message_lower = message.lower()
        return any(pattern in message_lower for pattern in CONNECTION_ERROR_PATTERNS)
