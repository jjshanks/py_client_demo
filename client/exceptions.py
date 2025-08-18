"""
Custom exception hierarchy for the resilient HTTP client library.

This module defines a comprehensive exception hierarchy that provides clear
error contracts to users while maintaining semantic meaning for retry logic
and circuit breaker behavior.
"""

from typing import Any, Optional


class MyAPIError(Exception):
    """Base exception for all library errors."""

    def __init__(
        self,
        message: str = "",
        response: Optional[Any] = None,
        request: Optional[Any] = None,
    ):
        self.message = message
        self.response = response
        self.request = request
        super().__init__(message)


class APIConnectionError(MyAPIError):
    """
    Base for all retryable errors.

    These errors indicate connectivity issues, timeouts, or server-side
    problems that may be resolved by retrying the request.
    """

    pass


class APITimeoutError(APIConnectionError):
    """A client-side timeout occurred."""

    pass


class ServerTimeoutError(APIConnectionError):
    """The server returned a 408 timeout."""

    pass


class ServiceUnavailableError(APIConnectionError):
    """The server is temporarily unavailable (502, 503, 504)."""

    pass


class ServerError(APIConnectionError):
    """A generic 5xx server error occurred."""

    pass


class APIStatusError(MyAPIError):
    """Base for non-retryable API errors."""

    pass


class AuthenticationError(APIStatusError):
    """The request was not authorized (401, 403)."""

    pass


class NotFoundError(APIStatusError):
    """The requested resource was not found (404)."""

    pass


class InvalidRequestError(APIStatusError):
    """The request was malformed (400, 422)."""

    pass


class PoolTimeoutError(MyAPIError):
    """Failed to acquire a connection from the bulkhead."""

    pass
