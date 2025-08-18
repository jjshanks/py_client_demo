"""
Integration tests for the ResilientClient against the FastAPI test server.

These tests validate that the client's resilience patterns work correctly
against real server endpoints including failure injection, idempotency,
and full end-to-end behavior.
"""

import asyncio
import uuid

import httpx
import pytest

from client.client import create_resilient_client
from client.config import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    RetryConfig,
)
from client.exceptions import (
    APIConnectionError,
    PoolTimeoutError,
    ServerError,
)


@pytest.fixture(scope="session")
def server_base_url():
    """Base URL for the test server. Override with environment variable if needed."""
    return "http://localhost:8000"


@pytest.fixture
def client_config(server_base_url):
    """Standard client configuration for integration tests."""
    return ClientConfig(
        base_url=server_base_url,
        retry=RetryConfig(
            max_attempts=3, min_wait_seconds=0.1, max_wait_seconds=2.0, jitter=True
        ),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=2.0),
        bulkhead=BulkheadConfig(max_concurrency=10, acquisition_timeout=5.0),
    )


@pytest.fixture(autouse=True)
def skip_if_no_server(server_base_url):
    """Auto-skip integration tests if server is not available."""

    async def check_server():
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{server_base_url}/health", timeout=2.0)
                return response.status_code == 200
        except Exception:
            return False

    # Run check synchronously
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        server_available = loop.run_until_complete(check_server())
    finally:
        loop.close()

    if not server_available:
        pytest.skip("Test server not available: All connection attempts failed")


@pytest.fixture
async def reset_server_state(server_base_url):
    """Reset server failure state before each test."""
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{server_base_url}/fail/reset")
        except Exception:
            # Server might not be running - skip reset
            pass
    yield
    # Reset after test as well
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{server_base_url}/fail/reset")
        except Exception:
            pass


class TestBasicClientFunctionality:
    """Test basic client functionality against live server."""

    async def test_successful_request(self, client_config, reset_server_state):
        """Test that basic requests work correctly."""
        async with create_resilient_client(client_config) as client:
            response = await client.get("/msg")
            assert response.status_code == 200

            data = response.json()
            assert "message_id" in data

            # Should be a valid UUID
            uuid.UUID(data["message_id"])

    async def test_idempotency_with_request_id(self, client_config, reset_server_state):
        """Test that idempotency works with X-Request-ID header."""
        request_id = str(uuid.uuid4())

        async with create_resilient_client(client_config) as client:
            # First request
            response1 = await client.get("/msg", request_id=request_id)
            data1 = response1.json()

            # Second request with same ID
            response2 = await client.get("/msg", request_id=request_id)
            data2 = response2.json()

            # Should return the same message_id
            assert data1["message_id"] == data2["message_id"]

    async def test_different_request_ids_get_different_responses(
        self, client_config, reset_server_state
    ):
        """Test that different request IDs get different UUIDs."""
        async with create_resilient_client(client_config) as client:
            response1 = await client.get("/msg", request_id=str(uuid.uuid4()))
            response2 = await client.get("/msg", request_id=str(uuid.uuid4()))

            data1 = response1.json()
            data2 = response2.json()

            # Should be different UUIDs
            assert data1["message_id"] != data2["message_id"]


class TestRetryBehavior:
    """Test retry behavior against server failure injection."""

    async def test_retry_on_server_failures(self, client_config, reset_server_state):
        """Test that client retries on server 500 errors."""
        # Configure server to fail 2 requests, then succeed
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{client_config.base_url}/fail/count/2")

        async with create_resilient_client(client_config) as client:
            # Should succeed after retries
            response = await client.get("/msg")
            assert response.status_code == 200

    async def test_retry_exhaustion_raises_exception(
        self, client_config, reset_server_state
    ):
        """Test that exhausted retries raise the final exception."""
        # Configure server to fail more times than we retry
        fail_count = client_config.retry.max_attempts + 2
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{client_config.base_url}/fail/count/{fail_count}")

        async with create_resilient_client(client_config) as client:
            # Should fail after all retries exhausted
            with pytest.raises(ServerError):
                await client.get("/msg")

    async def test_idempotent_retries_use_same_request_id(
        self, client_config, reset_server_state
    ):
        """Test that retries use the same request ID for idempotency."""
        request_id = str(uuid.uuid4())

        # Configure server to fail 1 request, then succeed
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{client_config.base_url}/fail/count/1")

        async with create_resilient_client(client_config) as client:
            response = await client.get("/msg", request_id=request_id)
            assert response.status_code == 200

            # Now request with same ID should return cached response
            response2 = await client.get("/msg", request_id=request_id)
            assert response.json()["message_id"] == response2.json()["message_id"]


class TestCircuitBreakerBehavior:
    """Test circuit breaker behavior with real server failures."""

    async def test_circuit_breaker_opens_on_sustained_failures(self, server_base_url):
        """Test that circuit breaker opens after sustained failures."""
        config = ClientConfig(
            base_url=server_base_url,
            retry=RetryConfig(max_attempts=1),  # No retries for this test
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=2, recovery_timeout=1.0
            ),
        )

        # Configure server to fail many requests
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{server_base_url}/fail/reset")
            await setup_client.post(f"{server_base_url}/fail/count/10")

        async with create_resilient_client(config) as client:
            # First failure
            with pytest.raises(ServerError):
                await client.get("/msg")

            # Second failure should open circuit
            with pytest.raises(ServerError):
                await client.get("/msg")

            # Reset server failures
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{server_base_url}/fail/reset")

            # Circuit should still be open, failing fast
            with pytest.raises(APIConnectionError, match="Circuit breaker is OPEN"):
                await client.get("/msg")

    async def test_circuit_breaker_recovery(self, server_base_url):
        """Test that circuit breaker recovers after timeout."""
        config = ClientConfig(
            base_url=server_base_url,
            retry=RetryConfig(max_attempts=1),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=2, recovery_timeout=0.5  # Short recovery time
            ),
        )

        async with create_resilient_client(config) as client:
            # Force circuit breaker to open
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{server_base_url}/fail/count/5")

            # Generate failures to open circuit
            for _ in range(2):
                with pytest.raises(ServerError):
                    await client.get("/msg")

            # Reset server to normal operation
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{server_base_url}/fail/reset")

            # Wait for recovery timeout
            await asyncio.sleep(0.6)

            # Should recover and work normally
            response = await client.get("/msg")
            assert response.status_code == 200


class TestBulkheadBehavior:
    """Test bulkhead (concurrency limiting) behavior."""

    async def test_concurrent_requests_within_limit(self, server_base_url):
        """Test that concurrent requests within limit work correctly."""
        config = ClientConfig(
            base_url=server_base_url,
            bulkhead=BulkheadConfig(max_concurrency=3, acquisition_timeout=2.0),
        )

        async with create_resilient_client(config) as client:
            # Make 3 concurrent requests (within limit)
            tasks = [
                client.get("/msg", params={"delay": 100})  # 100ms delay
                for _ in range(3)
            ]

            responses = await asyncio.gather(*tasks)
            assert len(responses) == 3
            assert all(r.status_code == 200 for r in responses)

    async def test_bulkhead_timeout_with_saturation(self, server_base_url):
        """Test that bulkhead timeout works when client is saturated."""
        config = ClientConfig(
            base_url=server_base_url,
            bulkhead=BulkheadConfig(
                max_concurrency=2, acquisition_timeout=0.2  # Very short timeout
            ),
            retry=RetryConfig(max_attempts=1),  # No retries
        )

        async with create_resilient_client(config) as client:
            # Start 2 long-running requests to saturate the bulkhead
            long_tasks = [
                asyncio.create_task(
                    client.get("/msg", params={"delay": 1000})
                )  # 1 second delay
                for _ in range(2)
            ]

            # Wait a bit for them to start
            await asyncio.sleep(0.05)

            # Third request should timeout on semaphore acquisition
            with pytest.raises(
                PoolTimeoutError, match="Failed to acquire connection slot"
            ):
                await client.get("/msg")

            # Clean up long running tasks
            for task in long_tasks:
                task.cancel()
            await asyncio.gather(*long_tasks, return_exceptions=True)


class TestEndToEndScenarios:
    """Test complete end-to-end scenarios with all patterns working together."""

    async def test_full_resilience_stack_recovery(
        self, client_config, reset_server_state
    ):
        """Test that client recovers from failures using full resilience stack."""
        async with create_resilient_client(client_config) as client:
            # Configure server for some failures
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{client_config.base_url}/fail/count/2")

            # Should succeed despite initial failures (via retries)
            response = await client.get("/msg")
            assert response.status_code == 200

            # Subsequent requests should work normally
            response2 = await client.get("/msg")
            assert response2.status_code == 200

    async def test_multiple_operations_maintain_state(
        self, client_config, reset_server_state
    ):
        """Test that client maintains internal state across multiple operations."""
        async with create_resilient_client(client_config) as client:
            # Make several successful requests
            responses = []
            for i in range(5):
                response = await client.get("/msg")
                assert response.status_code == 200
                responses.append(response.json()["message_id"])

            # All should be unique (no inappropriate caching)
            assert len(set(responses)) == 5

            # Circuit breaker should still be closed
            assert client._circuit_breaker.state.value == "closed"
            assert client._circuit_breaker.failure_count == 0

    async def test_mixed_success_and_failure_operations(
        self, client_config, reset_server_state
    ):
        """Test client behavior with mixed success/failure scenarios."""
        async with create_resilient_client(client_config) as client:
            # Configure server for limited failures
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{client_config.base_url}/fail/count/1")

            # First request should fail and retry to success
            response1 = await client.get("/msg")
            assert response1.status_code == 200

            # Second request should succeed immediately
            response2 = await client.get("/msg")
            assert response2.status_code == 200

            # Should have different message IDs
            assert response1.json()["message_id"] != response2.json()["message_id"]

    async def test_connection_pooling_reuse(self, client_config, reset_server_state):
        """Test that connection pooling is working (same client reused)."""
        async with create_resilient_client(client_config) as client:
            # Multiple requests should reuse the same underlying httpx client
            httpx_client_id = id(client._client)

            # Make multiple requests
            for _ in range(3):
                response = await client.get("/msg")
                assert response.status_code == 200

                # httpx client should be the same instance
                assert id(client._client) == httpx_client_id


@pytest.mark.skipif_no_server
class TestRealServerIntegration:
    """
    Tests that require a running server instance.

    These tests will be skipped if the server is not available.
    Run the server with: uv run python -m server.main serve --reload
    """

    async def test_server_availability(self, server_base_url):
        """Test that test server is available and responsive."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{server_base_url}/health")
                assert response.status_code == 200
            except Exception as e:
                pytest.skip(f"Test server not available: {e}")

    async def test_failure_injection_integration(
        self, client_config, reset_server_state
    ):
        """Test client resilience against server failure injection."""
        async with create_resilient_client(client_config) as client:
            # Test with count-based failures
            async with httpx.AsyncClient() as setup_client:
                # Configure 1 failure - client should retry and succeed
                await setup_client.post(f"{client_config.base_url}/fail/count/1")

                # Client should handle this gracefully
                response = await client.get("/msg")
                assert response.status_code == 200

                # Verify server is back to normal
                status_response = await setup_client.get(
                    f"{client_config.base_url}/fail/status"
                )
                status_data = status_response.json()
                assert not status_data["currently_failing"]

    async def test_duration_based_failure_recovery(
        self, client_config, reset_server_state
    ):
        """Test client behavior with duration-based server failures."""
        config = ClientConfig(
            base_url=client_config.base_url,
            retry=RetryConfig(
                max_attempts=5,  # More retries for duration test
                min_wait_seconds=0.2,
                max_wait_seconds=1.0,
            ),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=10,  # High threshold for duration test
                recovery_timeout=1.0,
            ),
        )

        async with create_resilient_client(config) as client:
            # Configure server to fail for 0.5 seconds
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(f"{config.base_url}/fail/duration/1")

                # Wait for duration to pass
                await asyncio.sleep(1.2)

                # Now requests should succeed
                response = await client.get("/msg")
                assert response.status_code == 200


class TestConcurrencyAndLoad:
    """Test client behavior under concurrent load."""

    async def test_high_concurrency_requests(self, client_config, reset_server_state):
        """Test client handles high concurrency correctly."""
        async with create_resilient_client(client_config) as client:
            # Make many concurrent requests
            num_requests = 20
            tasks = [client.get("/msg") for _ in range(num_requests)]

            responses = await asyncio.gather(*tasks)

            # All should succeed
            assert len(responses) == num_requests
            assert all(r.status_code == 200 for r in responses)

            # All should have unique message IDs
            message_ids = [r.json()["message_id"] for r in responses]
            assert len(set(message_ids)) == num_requests

    async def test_bulkhead_prevents_overload(self, server_base_url):
        """Test that bulkhead prevents client overload."""
        config = ClientConfig(
            base_url=server_base_url,
            bulkhead=BulkheadConfig(
                max_concurrency=3, acquisition_timeout=0.1  # Very short timeout
            ),
            retry=RetryConfig(max_attempts=1),  # No retries
        )

        async with create_resilient_client(config) as client:
            # Start 3 slow requests to fill bulkhead
            slow_tasks = [
                asyncio.create_task(client.get("/msg", params={"delay": 500}))
                for _ in range(3)
            ]

            # Wait for them to start
            await asyncio.sleep(0.05)

            # Additional requests should fail with PoolTimeoutError
            with pytest.raises(PoolTimeoutError):
                await client.get("/msg")

            # Clean up
            for task in slow_tasks:
                task.cancel()
            await asyncio.gather(*slow_tasks, return_exceptions=True)
