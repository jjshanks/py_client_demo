"""
Tests for the client library exception hierarchy.

Validates that all custom exceptions are properly structured and provide
the correct inheritance relationships for retry logic and error handling.
"""

import httpx

from client.exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    InvalidRequestError,
    MyAPIError,
    NotFoundError,
    PoolTimeoutError,
    ServerError,
    ServerTimeoutError,
    ServiceUnavailableError,
)


class TestExceptionHierarchy:
    """Test the exception inheritance structure."""

    def test_base_exception(self):
        """Test base MyAPIError exception."""
        exc = MyAPIError("test message")
        assert str(exc) == "test message"
        assert exc.message == "test message"
        assert exc.response is None
        assert exc.request is None

        # Test with response and request
        response = httpx.Response(200)
        request = httpx.Request("GET", "http://example.com")
        exc = MyAPIError("test", response=response, request=request)
        assert exc.response is response
        assert exc.request is request

    def test_retryable_exceptions_inherit_from_connection_error(self):
        """Test that all retryable exceptions inherit from APIConnectionError."""
        retryable_exceptions = [
            APITimeoutError,
            ServerTimeoutError,
            ServiceUnavailableError,
            ServerError,
        ]

        for exc_class in retryable_exceptions:
            exc = exc_class("test")
            assert isinstance(exc, APIConnectionError)
            assert isinstance(exc, MyAPIError)

    def test_non_retryable_exceptions_inherit_from_status_error(self):
        """Test that non-retryable exceptions inherit from APIStatusError."""
        non_retryable_exceptions = [
            AuthenticationError,
            NotFoundError,
            InvalidRequestError,
        ]

        for exc_class in non_retryable_exceptions:
            exc = exc_class("test")
            assert isinstance(exc, APIStatusError)
            assert isinstance(exc, MyAPIError)
            assert not isinstance(exc, APIConnectionError)

    def test_pool_timeout_error_is_separate(self):
        """Test that PoolTimeoutError is a direct MyAPIError descendant."""
        exc = PoolTimeoutError("test")
        assert isinstance(exc, MyAPIError)
        assert not isinstance(exc, APIConnectionError)
        assert not isinstance(exc, APIStatusError)

    def test_api_connection_error_is_retryable_base(self):
        """Test that APIConnectionError is the base for retryable errors."""
        exc = APIConnectionError("test")
        assert isinstance(exc, MyAPIError)
        assert not isinstance(exc, APIStatusError)

    def test_api_status_error_is_non_retryable_base(self):
        """Test that APIStatusError is the base for non-retryable errors."""
        exc = APIStatusError("test")
        assert isinstance(exc, MyAPIError)
        assert not isinstance(exc, APIConnectionError)


class TestSpecificExceptions:
    """Test specific exception classes and their behavior."""

    def test_timeout_exceptions(self):
        """Test timeout-related exceptions."""
        client_timeout = APITimeoutError("Client timeout")
        server_timeout = ServerTimeoutError("Server timeout")

        assert isinstance(client_timeout, APIConnectionError)
        assert isinstance(server_timeout, APIConnectionError)
        assert str(client_timeout) == "Client timeout"
        assert str(server_timeout) == "Server timeout"

    def test_server_error_exceptions(self):
        """Test server error exceptions."""
        service_unavailable = ServiceUnavailableError("Service down")
        server_error = ServerError("Internal error")

        assert isinstance(service_unavailable, APIConnectionError)
        assert isinstance(server_error, APIConnectionError)

    def test_client_error_exceptions(self):
        """Test client error exceptions."""
        auth_error = AuthenticationError("Unauthorized")
        not_found = NotFoundError("Resource missing")
        invalid_request = InvalidRequestError("Bad data")

        assert isinstance(auth_error, APIStatusError)
        assert isinstance(not_found, APIStatusError)
        assert isinstance(invalid_request, APIStatusError)

        # Ensure they're not retryable
        assert not isinstance(auth_error, APIConnectionError)
        assert not isinstance(not_found, APIConnectionError)
        assert not isinstance(invalid_request, APIConnectionError)

    def test_pool_timeout_error(self):
        """Test pool timeout error behavior."""
        pool_error = PoolTimeoutError("Pool saturated")

        assert isinstance(pool_error, MyAPIError)
        assert not isinstance(pool_error, APIConnectionError)
        assert not isinstance(pool_error, APIStatusError)
        assert str(pool_error) == "Pool saturated"
