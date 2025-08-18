"""
Tests for the circuit breaker implementation.

Validates that the circuit breaker correctly transitions between states
and only trips on APIConnectionError exceptions while ignoring client errors.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from client.circuit_breaker import CircuitBreaker, CircuitState
from client.config import CircuitBreakerConfig
from client.exceptions import (
    APIConnectionError,
    NotFoundError,
    ServerError,
)


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    @pytest.fixture
    def config(self):
        """Circuit breaker configuration for testing."""
        return CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=1.0,  # Short timeout for testing
            expected_exception="APIConnectionError",
        )

    @pytest.fixture
    def circuit_breaker(self, config):
        """Circuit breaker instance for testing."""
        return CircuitBreaker(config)

    async def test_circuit_starts_closed(self, circuit_breaker):
        """Test that circuit breaker starts in closed state."""
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0
        assert circuit_breaker.last_failure_time is None

    async def test_successful_calls_keep_circuit_closed(self, circuit_breaker):
        """Test that successful calls maintain closed state."""

        async def successful_operation():
            return "success"

        result = await circuit_breaker.call(successful_operation)
        assert result == "success"
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0

    async def test_connection_errors_increment_failure_count(self, circuit_breaker):
        """Test that connection errors increment the failure count."""

        async def failing_operation():
            raise APIConnectionError("Connection failed")

        # First failure
        with pytest.raises(APIConnectionError):
            await circuit_breaker.call(failing_operation)

        assert circuit_breaker.failure_count == 1
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.last_failure_time is not None

    async def test_circuit_opens_after_threshold_failures(self, circuit_breaker):
        """Test that circuit opens after reaching failure threshold."""

        async def failing_operation():
            raise ServerError("Server error")

        # Generate failures up to threshold
        for i in range(circuit_breaker.config.failure_threshold):
            with pytest.raises(ServerError):
                await circuit_breaker.call(failing_operation)

            if i < circuit_breaker.config.failure_threshold - 1:
                assert circuit_breaker.state == CircuitState.CLOSED
            else:
                assert circuit_breaker.state == CircuitState.OPEN

    async def test_open_circuit_fails_fast(self, circuit_breaker):
        """Test that open circuit fails fast without calling function."""
        # Force circuit to open state
        circuit_breaker.state = CircuitState.OPEN
        circuit_breaker.failure_count = circuit_breaker.config.failure_threshold
        circuit_breaker.last_failure_time = time.time()

        mock_operation = AsyncMock(return_value="should not be called")

        with pytest.raises(APIConnectionError, match="Circuit breaker is OPEN"):
            await circuit_breaker.call(mock_operation)

        # Verify function was not called
        mock_operation.assert_not_called()

    async def test_circuit_moves_to_half_open_after_timeout(self, circuit_breaker):
        """Test that circuit moves to half-open after recovery timeout."""
        # Force circuit to open state with old timestamp
        circuit_breaker.state = CircuitState.OPEN
        circuit_breaker.failure_count = circuit_breaker.config.failure_threshold
        circuit_breaker.last_failure_time = (
            time.time() - circuit_breaker.config.recovery_timeout - 1
        )

        async def test_operation():
            return "recovery test"

        result = await circuit_breaker.call(test_operation)
        assert result == "recovery test"
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0

    async def test_half_open_success_closes_circuit(self, circuit_breaker):
        """Test that successful call in half-open state closes circuit."""
        # Set circuit to half-open
        circuit_breaker.state = CircuitState.HALF_OPEN
        circuit_breaker.failure_count = 2

        async def recovery_operation():
            return "recovered"

        result = await circuit_breaker.call(recovery_operation)
        assert result == "recovered"
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0
        assert circuit_breaker.last_failure_time is None

    async def test_half_open_failure_reopens_circuit(self, circuit_breaker):
        """Test that failure in half-open state reopens circuit."""
        # Set circuit to half-open with threshold-1 failures
        circuit_breaker.state = CircuitState.HALF_OPEN
        circuit_breaker.failure_count = circuit_breaker.config.failure_threshold - 1

        async def failing_operation():
            raise APIConnectionError("Still failing")

        with pytest.raises(APIConnectionError):
            await circuit_breaker.call(failing_operation)

        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.failure_count == circuit_breaker.config.failure_threshold

    async def test_non_connection_errors_dont_affect_circuit(self, circuit_breaker):
        """Test that non-connection errors (like 4xx) don't affect circuit state."""

        async def client_error_operation():
            raise NotFoundError("Resource not found")

        # Multiple 4xx errors should not affect circuit breaker
        for _ in range(10):  # More than failure threshold
            with pytest.raises(NotFoundError):
                await circuit_breaker.call(client_error_operation)

        # Circuit should remain closed
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.failure_count == 0

    async def test_mixed_errors_only_count_connection_errors(self, circuit_breaker):
        """Test that only connection errors count toward failure threshold."""

        async def connection_error():
            raise APIConnectionError("Connection failed")

        async def client_error():
            raise NotFoundError("Not found")

        # Mix of connection and client errors
        with pytest.raises(APIConnectionError):
            await circuit_breaker.call(connection_error)

        with pytest.raises(NotFoundError):
            await circuit_breaker.call(client_error)

        with pytest.raises(APIConnectionError):
            await circuit_breaker.call(connection_error)

        # Only connection errors should count
        assert circuit_breaker.failure_count == 2
        assert circuit_breaker.state == CircuitState.CLOSED

    async def test_circuit_breaker_logging(self, circuit_breaker):
        """Test that circuit breaker logs state changes."""
        # Use a mock logger directly on the circuit breaker instance
        from unittest.mock import Mock

        mock_logger = Mock()
        circuit_breaker.logger = mock_logger

        # Force circuit to open
        async def failing_operation():
            raise ServerError("Server down")

        for _ in range(circuit_breaker.config.failure_threshold):
            with pytest.raises(ServerError):
                await circuit_breaker.call(failing_operation)

        # Should have logged warning about opening
        mock_logger.warning.assert_called()
        warning_call = mock_logger.warning.call_args[0][0]
        assert "Circuit breaker OPENED" in warning_call

    async def test_concurrent_access_thread_safety(self, circuit_breaker):
        """Test that circuit breaker is thread-safe with concurrent access."""
        failure_count = 0

        async def sometimes_failing_operation():
            nonlocal failure_count
            failure_count += 1
            if failure_count <= 2:
                raise APIConnectionError("Intermittent failure")
            return "success"

        # Run multiple concurrent operations
        tasks = [circuit_breaker.call(sometimes_failing_operation) for _ in range(5)]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Should have some failures and some successes
        connection_errors = [r for r in results if isinstance(r, APIConnectionError)]
        successes = [r for r in results if r == "success"]

        assert len(connection_errors) > 0
        assert len(successes) > 0
