"""
Tests for retry policies and logic.

Validates that retry behavior works correctly with exponential backoff,
jitter, and proper exception-based triggering.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from client.config import RetryConfig
from client.exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    NotFoundError,
    ServerError,
)
from client.retry_policies import RetryPolicy, create_retry_decorator


class TestRetryDecorator:
    """Test the tenacity retry decorator creation."""

    @pytest.fixture
    def retry_config(self):
        """Retry configuration for testing."""
        return RetryConfig(
            max_attempts=3,
            min_wait_seconds=0.1,  # Fast for testing
            max_wait_seconds=1.0,
            multiplier=2.0,
            jitter=False  # Deterministic for testing
        )

    async def test_successful_operation_no_retry(self, retry_config):
        """Test that successful operations don't trigger retries."""
        call_count = 0

        async def successful_operation():
            nonlocal call_count
            call_count += 1
            return "success"

        retry_decorator = create_retry_decorator(retry_config)
        wrapped_operation = retry_decorator(successful_operation)

        result = await wrapped_operation()
        assert result == "success"
        assert call_count == 1  # Called only once

    async def test_connection_errors_trigger_retries(self, retry_config):
        """Test that connection errors trigger retries."""
        call_count = 0

        async def failing_then_succeeding():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ServerError("Server temporarily down")
            return "recovered"

        retry_decorator = create_retry_decorator(retry_config)
        wrapped_operation = retry_decorator(failing_then_succeeding)

        result = await wrapped_operation()
        assert result == "recovered"
        assert call_count == 3  # Failed twice, succeeded on third

    async def test_non_connection_errors_no_retry(self, retry_config):
        """Test that non-connection errors don't trigger retries."""
        call_count = 0

        async def client_error_operation():
            nonlocal call_count
            call_count += 1
            raise NotFoundError("Resource not found")

        retry_decorator = create_retry_decorator(retry_config)
        wrapped_operation = retry_decorator(client_error_operation)

        with pytest.raises(NotFoundError):
            await wrapped_operation()

        assert call_count == 1  # Should not retry 4xx errors

    async def test_max_attempts_exhausted(self, retry_config):
        """Test behavior when all retry attempts are exhausted."""
        call_count = 0

        async def always_failing():
            nonlocal call_count
            call_count += 1
            raise APITimeoutError("Always times out")

        retry_decorator = create_retry_decorator(retry_config)
        wrapped_operation = retry_decorator(always_failing)

        with pytest.raises(APITimeoutError):
            await wrapped_operation()

        # Should try max_attempts times
        assert call_count == retry_config.max_attempts

    @patch('client.retry_policies.logging.getLogger')
    async def test_retry_logging(self, mock_get_logger, retry_config):
        """Test that retry attempts are logged properly."""
        mock_logger = AsyncMock()
        mock_get_logger.return_value = mock_logger

        call_count = 0
        request_id = "test-request-123"

        async def failing_operation():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise ServerError("Server error")
            return "success"

        retry_decorator = create_retry_decorator(retry_config, request_id)
        wrapped_operation = retry_decorator(failing_operation)

        result = await wrapped_operation()
        assert result == "success"

        # Should have logged the retry attempt
        mock_logger.debug.assert_called()
        debug_call = mock_logger.debug.call_args[0][0]
        assert request_id in debug_call
        assert "ServerError" in debug_call
        assert "Retrying in" in debug_call


class TestRetryPolicy:
    """Test the RetryPolicy class."""

    @pytest.fixture
    def retry_config(self):
        """Retry configuration for testing."""
        return RetryConfig(
            max_attempts=2,
            min_wait_seconds=0.01,
            max_wait_seconds=0.1,
            jitter=False
        )

    @pytest.fixture
    def retry_policy(self, retry_config):
        """RetryPolicy instance for testing."""
        return RetryPolicy(retry_config)

    async def test_wrap_operation(self, retry_policy):
        """Test wrapping an operation with retry policy."""
        call_count = 0

        async def test_operation():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise APIConnectionError("First attempt fails")
            return "success on retry"

        wrapped = retry_policy.wrap_operation(test_operation, "test-id")
        result = await wrapped()

        assert result == "success on retry"
        assert call_count == 2

    async def test_wrap_operation_with_auto_request_id(self, retry_policy):
        """Test wrapping operation without providing request ID."""
        async def test_operation():
            return "success"

        # Should work without explicit request ID
        wrapped = retry_policy.wrap_operation(test_operation)
        result = await wrapped()
        assert result == "success"

    async def test_different_connection_error_types_all_retry(self, retry_policy):
        """Test that all APIConnectionError subclasses trigger retries."""
        connection_errors = [
            APITimeoutError("Timeout"),
            ServerError("Server error"),
            APIConnectionError("Generic connection error"),
        ]

        for error_class in connection_errors:
            call_count = 0

            async def failing_operation():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise error_class
                return "recovered"

            wrapped = retry_policy.wrap_operation(failing_operation)
            result = await wrapped()
            assert result == "recovered"
            assert call_count == 2  # Failed once, succeeded on retry

    async def test_status_errors_dont_retry(self, retry_policy):
        """Test that status errors (4xx) don't trigger retries."""
        status_errors = [
            NotFoundError("Not found"),
            APIStatusError("Generic status error"),
        ]

        for error_class in status_errors:
            call_count = 0

            async def failing_operation():
                nonlocal call_count
                call_count += 1
                raise error_class

            wrapped = retry_policy.wrap_operation(failing_operation)

            with pytest.raises(type(error_class)):
                await wrapped()

            assert call_count == 1  # Should not retry


class TestRetryDecoratorEdgeCases:
    """Test edge cases and advanced retry scenarios."""

    async def test_retry_with_jitter_enabled(self):
        """Test retry behavior with jitter enabled."""
        config = RetryConfig(
            max_attempts=3,
            min_wait_seconds=0.01,
            max_wait_seconds=0.1,
            jitter=True
        )

        call_count = 0
        wait_times = []

        async def failing_operation():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise APIConnectionError("Connection failed")
            return "success"

        retry_decorator = create_retry_decorator(config)
        wrapped_operation = retry_decorator(failing_operation)

        start_time = asyncio.get_event_loop().time()
        result = await wrapped_operation()
        end_time = asyncio.get_event_loop().time()

        assert result == "success"
        assert call_count == 3

        # With jitter, total time should be within reasonable bounds
        total_time = end_time - start_time
        assert 0.01 <= total_time <= 1.0  # Generous bounds for jitter

    async def test_retry_preserves_original_exception_details(self):
        """Test that retry preserves original exception information."""
        config = RetryConfig(max_attempts=2, min_wait_seconds=0.01, max_wait_seconds=0.1)

        original_response = "mock_response_object"

        async def failing_operation():
            raise ServerError("Detailed error message", response=original_response)

        retry_decorator = create_retry_decorator(config)
        wrapped_operation = retry_decorator(failing_operation)

        with pytest.raises(ServerError) as exc_info:
            await wrapped_operation()

        # Original exception details should be preserved
        assert str(exc_info.value) == "Detailed error message"
        assert exc_info.value.response == original_response
