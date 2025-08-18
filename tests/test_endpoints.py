"""Integration tests for all API endpoints."""

import asyncio

# Import the app but override config for testing
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

os.environ.update({
    "SERVER_MAX_CONCURRENCY": "10",
    "SERVER_REQUEST_TIMEOUT": "5",
    "SERVER_CACHE_MAX_SIZE": "100",
    "SERVER_CACHE_TTL_SECONDS": "10",
    "SERVER_LOG_LEVEL": "DEBUG",
    "SERVER_LOG_FORMAT": "console"
})

from server.main import app


@pytest.fixture
def client():
    """Create a test client."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def async_client():
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test basic health check functionality."""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0


class TestFailureEndpoints:
    """Test failure injection endpoints."""

    def test_set_failure_count(self, client):
        """Test setting failure count."""
        # Reset first
        client.post("/fail/reset")

        # Set failure count
        response = client.post("/fail/count/3")
        assert response.status_code == 200

        data = response.json()
        assert "message" in data
        assert data["fail_requests_count"] == 3

    def test_set_failure_duration(self, client):
        """Test setting failure duration."""
        # Reset first
        client.post("/fail/reset")

        # Set failure duration
        response = client.post("/fail/duration/5")
        assert response.status_code == 200

        data = response.json()
        assert "message" in data
        assert data["duration_seconds"] == 5

    def test_reset_failures(self, client):
        """Test resetting all failure modes."""
        # Set some failures
        client.post("/fail/count/5")
        client.post("/fail/duration/10")

        # Reset
        response = client.post("/fail/reset")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "normal_operation"

    def test_failure_status(self, client):
        """Test getting failure status."""
        # Reset first
        client.post("/fail/reset")

        response = client.get("/fail/status")
        assert response.status_code == 200

        data = response.json()
        assert "failure_status" in data
        assert "currently_failing" in data
        assert not data["currently_failing"]

    def test_invalid_failure_parameters(self, client):
        """Test validation of failure parameters."""
        # Negative count should be rejected
        response = client.post("/fail/count/-1")
        assert response.status_code == 422

        # Excessive duration should be rejected
        response = client.post("/fail/duration/10000")
        assert response.status_code == 422


class TestMsgEndpoint:
    """Test the core /msg endpoint."""

    def test_basic_message_generation(self, client):
        """Test basic message generation without parameters."""
        # Reset failures first
        client.post("/fail/reset")

        response = client.get("/msg")
        assert response.status_code == 200

        data = response.json()
        assert "message_id" in data

        # Verify it's a valid UUID
        message_id = data["message_id"]
        uuid.UUID(message_id)  # Will raise ValueError if invalid

    def test_unique_message_ids(self, client):
        """Test that each request generates a unique message ID."""
        # Reset failures first
        client.post("/fail/reset")

        # Make multiple requests
        responses = [client.get("/msg") for _ in range(5)]

        # All should succeed
        for response in responses:
            assert response.status_code == 200

        # All message IDs should be unique
        message_ids = [r.json()["message_id"] for r in responses]
        assert len(set(message_ids)) == len(message_ids)

    def test_delay_parameter(self, client):
        """Test delay parameter functionality."""
        # Reset failures first
        client.post("/fail/reset")

        # Request with 200ms delay
        start_time = time.time()
        response = client.get("/msg?delay=200")
        end_time = time.time()

        assert response.status_code == 200

        # Should have taken at least 200ms
        duration_ms = (end_time - start_time) * 1000
        assert duration_ms >= 180  # Allow some tolerance for timing

    def test_idempotency_with_request_id(self, client):
        """Test idempotency using X-Request-ID header."""
        # Reset failures first
        client.post("/fail/reset")

        request_id = "test-request-123"
        headers = {"X-Request-ID": request_id}

        # First request
        response1 = client.get("/msg", headers=headers)
        assert response1.status_code == 200
        message_id_1 = response1.json()["message_id"]

        # Second request with same ID should return same message
        response2 = client.get("/msg", headers=headers)
        assert response2.status_code == 200
        message_id_2 = response2.json()["message_id"]

        assert message_id_1 == message_id_2

    def test_different_request_ids_generate_different_messages(self, client):
        """Test that different request IDs generate different messages."""
        # Reset failures first
        client.post("/fail/reset")

        # Two different request IDs
        response1 = client.get("/msg", headers={"X-Request-ID": "id-1"})
        response2 = client.get("/msg", headers={"X-Request-ID": "id-2"})

        assert response1.status_code == 200
        assert response2.status_code == 200

        message_id_1 = response1.json()["message_id"]
        message_id_2 = response2.json()["message_id"]

        assert message_id_1 != message_id_2


class TestFailureInjection:
    """Test failure injection behavior in /msg endpoint."""

    def test_count_based_failure_injection(self, client):
        """Test count-based failure injection."""
        # Configure 3 failures
        client.post("/fail/count/3")

        # First 3 requests should fail
        for i in range(3):
            response = client.get("/msg")
            assert response.status_code == 500
            data = response.json()
            assert data["detail"] == "Induced server failure"

        # Fourth request should succeed
        response = client.get("/msg")
        assert response.status_code == 200
        assert "message_id" in response.json()

    def test_duration_based_failure_injection(self, client):
        """Test duration-based failure injection."""
        # Configure 1 second of failures
        client.post("/fail/duration/1")

        # Immediate request should fail
        response = client.get("/msg")
        assert response.status_code == 500

        # Wait for duration to expire
        time.sleep(1.1)

        # Request should now succeed
        response = client.get("/msg")
        assert response.status_code == 200
        assert "message_id" in response.json()

    def test_failure_precedence_over_idempotency(self, client):
        """Test that failure injection takes precedence over idempotency."""
        # First establish a cached response
        client.post("/fail/reset")
        request_id = "test-precedence-123"
        headers = {"X-Request-ID": request_id}

        response = client.get("/msg", headers=headers)
        assert response.status_code == 200
        cached_message_id = response.json()["message_id"]

        # Configure failure
        client.post("/fail/count/1")

        # Request with cached ID should fail (failure takes precedence)
        response = client.get("/msg", headers=headers)
        assert response.status_code == 500

        # Next request should return cached response
        response = client.get("/msg", headers=headers)
        assert response.status_code == 200
        assert response.json()["message_id"] == cached_message_id


class TestSpecificationFlow:
    """Test the exact flow described in section 6 of the specification."""

    @pytest.mark.asyncio
    async def test_complete_specification_flow(self, async_client):
        """Test the complete example flow from the specification."""

        # Step 2: Wait for healthy (already done by test setup)
        response = await async_client.get("/health")
        assert response.status_code == 200

        # Step 3: Configure failure (3 failures)
        response = await async_client.post("/fail/count/3")
        assert response.status_code == 200

        # Step 4: Validate failure (3 requests should fail)
        for i in range(3):
            response = await async_client.get("/msg")
            assert response.status_code == 500

        # Step 5: Validate recovery (4th request should succeed)
        response = await async_client.get("/msg")
        assert response.status_code == 200
        uuid_after_recovery = response.json()["message_id"]

        # Step 6: Validate idempotency
        headers = {"X-Request-ID": "test-123"}

        # First request with request ID
        response = await async_client.get("/msg", headers=headers)
        assert response.status_code == 200
        uuid_a = response.json()["message_id"]

        # Second request with same ID should return same UUID
        response = await async_client.get("/msg", headers=headers)
        assert response.status_code == 200
        uuid_a_repeat = response.json()["message_id"]
        assert uuid_a == uuid_a_repeat

        # Step 7: Validate failure precedence
        # Configure 1 failure
        response = await async_client.post("/fail/count/1")
        assert response.status_code == 200

        # Request with cached ID should fail (failure precedence)
        response = await async_client.get("/msg", headers=headers)
        assert response.status_code == 500

        # Next request should return cached response
        response = await async_client.get("/msg", headers=headers)
        assert response.status_code == 200
        assert response.json()["message_id"] == uuid_a


class TestConcurrencyLimiting:
    """Test concurrency limiting behavior."""

    @pytest.mark.asyncio
    async def test_concurrency_limiting_with_delays(self, async_client):
        """Test that concurrency is properly limited with delayed requests."""
        # Reset failures
        await async_client.post("/fail/reset")

        # Start multiple concurrent requests with delays
        # With max_concurrency=10 in test config, all should eventually complete
        tasks = []
        for i in range(15):  # More than max concurrency
            task = async_client.get("/msg?delay=100")  # 100ms delay each
            tasks.append(task)

        start_time = time.time()
        responses = await asyncio.gather(*tasks)
        end_time = time.time()

        # All should succeed
        for response in responses:
            assert response.status_code == 200

        # Should take longer than a single request due to concurrency limiting
        duration = end_time - start_time
        assert duration > 0.1  # At least one delay period


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_invalid_delay_parameter(self, client):
        """Test validation of delay parameter."""
        client.post("/fail/reset")

        # Negative delay should be rejected
        response = client.get("/msg?delay=-100")
        assert response.status_code == 422

        # Excessively large delay should be rejected
        response = client.get("/msg?delay=99999999")
        assert response.status_code == 422

    def test_correlation_id_headers(self, client):
        """Test that correlation IDs are added to responses."""
        client.post("/fail/reset")

        response = client.get("/msg")
        assert response.status_code == 200

        # Should have correlation ID header from middleware
        assert "X-Correlation-ID" in response.headers

        # Correlation ID should be a valid UUID
        correlation_id = response.headers["X-Correlation-ID"]
        uuid.UUID(correlation_id)  # Will raise if invalid
