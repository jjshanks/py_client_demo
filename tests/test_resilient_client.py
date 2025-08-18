"""
Tests for the main ResilientClient class.

Validates the complete client implementation including lifecycle management,
exception mapping, idempotency, and integration of all resilience patterns.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from client.client import ResilientClient, create_resilient_client
from client.config import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    RetryConfig,
)
from client.exceptions import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InvalidRequestError,
    NotFoundError,
    PoolTimeoutError,
    ServerError,
)


class TestResilientClientLifecycle:
    """Test client lifecycle management."""

    @pytest.fixture
    def client_config(self):
        """Basic client configuration for testing."""
        return ClientConfig(
            base_url="http://localhost:8000",
            bulkhead=BulkheadConfig(max_concurrency=2, acquisition_timeout=1.0),
            retry=RetryConfig(
                max_attempts=2, min_wait_seconds=0.01, max_wait_seconds=0.1
            ),
        )

    async def test_client_initialization(self, client_config):
        """Test that client initializes properly."""
        client = ResilientClient(client_config)

        # Should not be initialized until first use
        assert client._client is None
        assert client._semaphore is None
        assert client._circuit_breaker is None

        await client._ensure_initialized()

        # Should be initialized after ensure_initialized
        assert client._client is not None
        assert client._semaphore is not None
        assert client._circuit_breaker is not None
        assert client._retry_policy is not None

        await client.close()

    async def test_async_context_manager(self, client_config):
        """Test client as async context manager."""
        async with ResilientClient(client_config) as client:
            assert client._client is not None
            assert not client._closed

        # Should be closed after context exit
        assert client._closed

    async def test_create_resilient_client_context_manager(self, client_config):
        """Test the create_resilient_client context manager."""
        async with create_resilient_client(client_config) as client:
            assert isinstance(client, ResilientClient)
            assert client._client is not None
            assert not client._closed

        # Should be closed after context exit
        assert client._closed

    async def test_double_close_is_safe(self, client_config):
        """Test that closing client multiple times is safe."""
        client = ResilientClient(client_config)
        await client._ensure_initialized()

        await client.close()
        assert client._closed

        # Second close should not raise error
        await client.close()


class TestExceptionMapping:
    """Test httpx exception mapping to custom hierarchy."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(base_url="http://localhost:8000")

    @pytest.fixture
    def client(self, client_config):
        return ResilientClient(client_config)

    def test_timeout_exception_mapping(self, client):
        """Test mapping of httpx timeout exceptions."""
        httpx_timeout = httpx.TimeoutException("Request timed out")
        mapped = client._map_httpx_exception(httpx_timeout)

        assert isinstance(mapped, APITimeoutError)
        assert "Request timed out" in str(mapped)

    def test_network_error_mapping(self, client):
        """Test mapping of network errors."""
        network_error = httpx.NetworkError("Network unreachable")
        mapped = client._map_httpx_exception(network_error)

        assert isinstance(mapped, APIConnectionError)
        assert "Network error" in str(mapped)

    def test_http_status_error_mapping(self, client):
        """Test mapping of HTTP status errors."""
        # Create mock response objects
        responses = {
            401: Mock(status_code=401),
            403: Mock(status_code=403),
            404: Mock(status_code=404),
            400: Mock(status_code=400),
            422: Mock(status_code=422),
            408: Mock(status_code=408),
            502: Mock(status_code=502),
            503: Mock(status_code=503),
            504: Mock(status_code=504),
            500: Mock(status_code=500),
            599: Mock(status_code=599),
        }

        # Test that HTTP status codes map to correct exception types

        for status_code, response in responses.items():
            status_error = httpx.HTTPStatusError(
                "HTTP error", request=Mock(), response=response
            )
            mapped = client._map_httpx_exception(status_error)

            # Check that we get the right exception type
            if status_code in [401, 403]:
                assert isinstance(mapped, AuthenticationError)
            elif status_code == 404:
                assert isinstance(mapped, NotFoundError)
            elif status_code in [400, 422]:
                assert isinstance(mapped, InvalidRequestError)
            elif status_code == 408:
                assert "ServerTimeoutError" in str(
                    type(mapped)
                )  # Will be implemented later
            elif status_code in [502, 503, 504]:
                assert "ServiceUnavailableError" in str(
                    type(mapped)
                )  # Will be implemented later
            elif 500 <= status_code < 600:
                assert isinstance(mapped, ServerError)


class TestIdempotencyAndHeaders:
    """Test idempotency key generation and header management."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(
            base_url="http://localhost:8000",
            bulkhead=BulkheadConfig(max_concurrency=1, acquisition_timeout=0.1),
        )

    async def test_automatic_request_id_generation(self, client_config):
        """Test that request IDs are automatically generated."""
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_request.return_value = mock_response

            async with ResilientClient(client_config) as client:
                await client.get("/test")

                # Check that X-Request-ID header was added
                call_args = mock_request.call_args
                headers = call_args[1]["headers"]
                assert "X-Request-ID" in headers

                # Should be a valid UUID
                request_id = headers["X-Request-ID"]
                uuid.UUID(request_id)  # Should not raise

    async def test_custom_request_id_preserved(self, client_config):
        """Test that custom request IDs are preserved."""
        custom_id = "custom-request-123"

        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_request.return_value = mock_response

            async with ResilientClient(client_config) as client:
                await client.post("/test", request_id=custom_id)

                call_args = mock_request.call_args
                headers = call_args[1]["headers"]
                assert headers["X-Request-ID"] == custom_id

    async def test_request_id_persists_across_retries(self, client_config):
        """Test that the same request ID is used across retries."""
        request_ids = []

        def capture_request_id(*args, **kwargs):
            headers = kwargs.get("headers", {})
            request_ids.append(headers.get("X-Request-ID"))
            raise httpx.TimeoutException("Timeout")

        with patch("httpx.AsyncClient.request", side_effect=capture_request_id):
            async with ResilientClient(client_config) as client:
                with pytest.raises(APITimeoutError):
                    await client.get("/test")

        # Should have made multiple attempts with same request ID
        assert len(request_ids) == client_config.retry.max_attempts
        assert all(rid == request_ids[0] for rid in request_ids)
        assert request_ids[0] is not None


class TestBulkheadPattern:
    """Test concurrency limiting (bulkhead pattern)."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(
            base_url="http://localhost:8000",
            bulkhead=BulkheadConfig(max_concurrency=2, acquisition_timeout=0.1),
            retry=RetryConfig(max_attempts=1),  # No retries for these tests
        )

    async def test_concurrent_requests_within_limit(self, client_config):
        """Test that concurrent requests within limit work correctly."""

        async def slow_response(*args, **kwargs):
            await asyncio.sleep(0.1)
            response = Mock()
            response.raise_for_status = Mock()
            return response

        with patch("httpx.AsyncClient.request", side_effect=slow_response):
            async with ResilientClient(client_config) as client:
                # Start 2 concurrent requests (within limit)
                tasks = [client.get("/test1"), client.get("/test2")]

                results = await asyncio.gather(*tasks)
                assert len(results) == 2

    async def test_semaphore_timeout_raises_pool_timeout_error(self, client_config):
        """Test that semaphore acquisition timeout raises PoolTimeoutError."""

        # Mock a very slow response to block semaphore slots
        async def blocking_response(*args, **kwargs):
            await asyncio.sleep(1.0)  # Block longer than acquisition timeout
            response = Mock()
            response.raise_for_status = Mock()
            return response

        with patch("httpx.AsyncClient.request", side_effect=blocking_response):
            async with ResilientClient(client_config) as client:
                # Start requests that fill up the semaphore
                task1 = asyncio.create_task(client.get("/blocking1"))
                task2 = asyncio.create_task(client.get("/blocking2"))

                # Wait a bit for them to acquire semaphore slots
                await asyncio.sleep(0.01)

                # Third request should timeout on semaphore acquisition
                with pytest.raises(
                    PoolTimeoutError, match="Failed to acquire connection slot"
                ):
                    await client.get("/should-timeout")

                # Clean up
                task1.cancel()
                task2.cancel()
                from contextlib import suppress

                with suppress(asyncio.CancelledError):
                    await asyncio.gather(task1, task2, return_exceptions=True)


class TestHTTPMethods:
    """Test all HTTP method implementations."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(base_url="http://localhost:8000")

    async def test_all_http_methods(self, client_config):
        """Test that all HTTP methods work correctly."""
        # Test that all HTTP methods work correctly

        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_request.return_value = mock_response

            async with ResilientClient(client_config) as client:
                # Test each HTTP method
                await client.get("/test")
                await client.post("/test", json={"data": "value"})
                await client.put("/test", json={"data": "updated"})
                await client.delete("/test")

                # Verify all methods were called correctly
                assert mock_request.call_count == 4

                calls = mock_request.call_args_list
                assert calls[0][0] == ("GET", "/test")
                assert calls[1][0] == ("POST", "/test")
                assert calls[2][0] == ("PUT", "/test")
                assert calls[3][0] == ("DELETE", "/test")

    async def test_request_parameters_passed_through(self, client_config):
        """Test that request parameters are properly passed to httpx."""
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_request.return_value = mock_response

            async with ResilientClient(client_config) as client:
                await client.post(
                    "/test",
                    json={"key": "value"},
                    params={"param": "test"},
                    headers={"Custom": "header"},
                )

                call_args = mock_request.call_args
                assert call_args[1]["json"] == {"key": "value"}
                assert call_args[1]["params"] == {"param": "test"}

                # Custom headers should be preserved along with X-Request-ID
                headers = call_args[1]["headers"]
                assert headers["Custom"] == "header"
                assert "X-Request-ID" in headers


class TestResilienceIntegration:
    """Test integration of all resilience patterns."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(
            base_url="http://localhost:8000",
            retry=RetryConfig(
                max_attempts=3, min_wait_seconds=0.01, max_wait_seconds=0.1
            ),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=5, recovery_timeout=0.1
            ),  # Higher threshold
            bulkhead=BulkheadConfig(max_concurrency=1, acquisition_timeout=0.1),
        )

    async def test_retry_circuit_breaker_integration(self, client_config):
        """Test that retries work with circuit breaker."""
        call_count = 0

        def failing_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("Timeout")

        with patch("httpx.AsyncClient.request", side_effect=failing_request):
            async with ResilientClient(client_config) as client:
                # First request should exhaust retries
                with pytest.raises(APITimeoutError):
                    await client.get("/test")

                # Should have tried max_attempts times
                assert call_count == client_config.retry.max_attempts

                # Circuit breaker failure count should be 1 (one logical operation)
                assert client._circuit_breaker.failure_count == 1

    async def test_bulkhead_with_retries(self, client_config):
        """Test that bulkhead semaphore is properly managed during retries."""
        call_count = 0

        async def failing_then_succeeding(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("First attempt fails")

            response = Mock()
            response.raise_for_status = Mock()
            return response

        with patch("httpx.AsyncClient.request", side_effect=failing_then_succeeding):
            async with ResilientClient(client_config) as client:
                result = await client.get("/test")
                assert result is not None

                # Semaphore should be available for next request
                assert (
                    client._semaphore._value == client_config.bulkhead.max_concurrency
                )

    @patch("client.client.logging.getLogger")
    async def test_logging_integration(self, mock_get_logger, client_config):
        """Test that logging works throughout the resilience stack."""
        mock_logger = Mock()
        mock_get_logger.return_value = mock_logger

        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_request.return_value = mock_response

            async with ResilientClient(client_config) as client:
                await client.get("/test")

                # Should have logged initialization
                info_calls = list(mock_logger.info.call_args_list)
                assert any("initialized" in str(call) for call in info_calls)


class TestErrorScenarios:
    """Test various error scenarios and edge cases."""

    @pytest.fixture
    def client_config(self):
        return ClientConfig(
            base_url="http://localhost:8000",
            retry=RetryConfig(max_attempts=2, min_wait_seconds=0.01),
            bulkhead=BulkheadConfig(acquisition_timeout=0.05),  # Very short for testing
        )

    async def test_semaphore_release_on_exception(self, client_config):
        """Test that semaphore is released even when exceptions occur."""

        def failing_request(*args, **kwargs):
            raise httpx.NetworkError("Network failure")

        with patch("httpx.AsyncClient.request", side_effect=failing_request):
            async with ResilientClient(client_config) as client:
                initial_semaphore_value = client._semaphore._value

                with pytest.raises(APIConnectionError):
                    await client.get("/test")

                # Semaphore should be released back to original value
                assert client._semaphore._value == initial_semaphore_value

    async def test_httpx_client_properly_configured(self, client_config):
        """Test that httpx client is configured with correct parameters."""
        async with ResilientClient(client_config) as client:
            httpx_client = client._client

            assert str(httpx_client.base_url) == client_config.base_url
            assert httpx_client.timeout.connect == client_config.timeout.connect
            assert httpx_client.timeout.read == client_config.timeout.read
            assert httpx_client.follow_redirects == client_config.follow_redirects

    async def test_custom_user_agent(self):
        """Test that custom user agent is properly set."""
        config = ClientConfig(
            base_url="http://localhost:8000", user_agent="TestClient/1.0"
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            client = ResilientClient(config)
            await client._ensure_initialized()

            # Check that httpx.AsyncClient was called with user agent headers
            call_kwargs = mock_client_class.call_args[1]
            assert call_kwargs["headers"]["User-Agent"] == "TestClient/1.0"
