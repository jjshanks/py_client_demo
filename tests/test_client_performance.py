"""
Performance benchmarking tests for the ResilientClient.

These tests validate the performance benefits of connection pooling and
measure the overhead of resilience patterns under various load conditions.
"""

import asyncio
import statistics
import time

import httpx
import pytest

from client.client import ResilientClient, create_resilient_client
from client.config import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    RetryConfig,
)


@pytest.fixture(scope="session")
def server_base_url():
    """Base URL for performance tests."""
    return "http://localhost:8000"


@pytest.fixture
def performance_config(server_base_url):
    """Optimized configuration for performance testing."""
    return ClientConfig(
        base_url=server_base_url,
        retry=RetryConfig(max_attempts=1),  # No retries for pure performance
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=1000
        ),  # Very high threshold
        bulkhead=BulkheadConfig(max_concurrency=50, acquisition_timeout=10.0),
    )


class TestConnectionPoolingPerformance:
    """Test performance benefits of connection pooling."""

    async def test_connection_pooling_vs_new_client_per_request(
        self, performance_config
    ):
        """
        Compare performance of reused client vs new client per request.

        This test demonstrates the critical performance benefit of connection pooling.
        """
        num_requests = 20

        # Test 1: Reused client (connection pooling)
        pooled_times = []
        async with create_resilient_client(performance_config) as client:
            for _ in range(num_requests):
                start = time.perf_counter()
                response = await client.get("/msg")
                end = time.perf_counter()

                assert response.status_code == 200
                pooled_times.append(end - start)

        # Test 2: New client per request (no pooling)
        no_pool_times = []
        for _ in range(num_requests):
            start = time.perf_counter()

            # Create new client for each request (anti-pattern)
            async with httpx.AsyncClient(
                base_url=performance_config.base_url
            ) as client:
                response = await client.get("/msg")

            end = time.perf_counter()
            assert response.status_code == 200
            no_pool_times.append(end - start)

        # Analyze results
        pooled_avg = statistics.mean(pooled_times)
        no_pool_avg = statistics.mean(no_pool_times)

        # Connection pooling should be significantly faster
        performance_improvement = (no_pool_avg - pooled_avg) / no_pool_avg * 100

        print("\nConnection Pooling Performance Analysis:")
        print(f"  Pooled client avg:     {pooled_avg:.4f}s")
        print(f"  No-pool client avg:    {no_pool_avg:.4f}s")
        print(f"  Performance improvement: {performance_improvement:.1f}%")

        # Should see at least 20% improvement with connection pooling
        assert (
            performance_improvement > 20
        ), f"Expected >20% improvement, got {performance_improvement:.1f}%"

    async def test_concurrent_request_performance(self, performance_config):
        """Test performance under concurrent load."""
        concurrency_levels = [1, 5, 10, 20]
        results = {}

        for concurrency in concurrency_levels:
            times = []

            async with create_resilient_client(performance_config) as client:
                # Run multiple batches to get stable measurements
                for _ in range(3):
                    start = time.perf_counter()

                    # Make concurrent requests
                    tasks = [client.get("/msg") for _ in range(concurrency)]
                    responses = await asyncio.gather(*tasks)

                    end = time.perf_counter()

                    # Verify all succeeded
                    assert all(r.status_code == 200 for r in responses)

                    # Calculate requests per second
                    batch_time = end - start
                    rps = concurrency / batch_time
                    times.append(rps)

            results[concurrency] = statistics.mean(times)

        print("\nConcurrent Request Performance:")
        for concurrency, avg_rps in results.items():
            print(f"  {concurrency:2d} concurrent: {avg_rps:7.1f} req/s")

        # Higher concurrency should generally yield higher total throughput
        # (though this depends on server capacity and may plateau)
        # Allow some variance due to system load and overhead
        throughput_ratio = results[5] / results[1]
        assert (
            throughput_ratio > 0.8
        ), f"Significant throughput decrease with concurrency: {throughput_ratio:.2f}"


class TestResiliencePatternOverhead:
    """Test the performance overhead of resilience patterns."""

    async def test_resilience_pattern_overhead(self, server_base_url):
        """Compare performance with and without resilience patterns."""
        num_requests = 30

        # Test 1: Full resilience patterns enabled
        resilient_config = ClientConfig(
            base_url=server_base_url,
            retry=RetryConfig(max_attempts=3),
            circuit_breaker=CircuitBreakerConfig(failure_threshold=5),
            bulkhead=BulkheadConfig(max_concurrency=10),
        )

        resilient_times = []
        async with create_resilient_client(resilient_config) as client:
            for _ in range(num_requests):
                start = time.perf_counter()
                response = await client.get("/msg")
                end = time.perf_counter()

                assert response.status_code == 200
                resilient_times.append(end - start)

        # Test 2: Plain httpx client (no resilience)
        plain_times = []
        async with httpx.AsyncClient(base_url=server_base_url) as plain_client:
            for _ in range(num_requests):
                start = time.perf_counter()
                response = await plain_client.get("/msg")
                end = time.perf_counter()

                assert response.status_code == 200
                plain_times.append(end - start)

        # Analyze overhead
        resilient_avg = statistics.mean(resilient_times)
        plain_avg = statistics.mean(plain_times)
        overhead_percent = (resilient_avg - plain_avg) / plain_avg * 100

        print("\nResilience Pattern Overhead Analysis:")
        print(f"  Resilient client avg: {resilient_avg:.4f}s")
        print(f"  Plain client avg:     {plain_avg:.4f}s")
        print(f"  Overhead:             {overhead_percent:.1f}%")

        # Overhead should be reasonable (typically < 25% for successful requests)
        assert (
            overhead_percent < 25
        ), f"Resilience overhead too high: {overhead_percent:.1f}%"

    async def test_retry_performance_under_failures(self, server_base_url):
        """Test performance when retries are actually triggered."""
        config = ClientConfig(
            base_url=server_base_url,
            retry=RetryConfig(
                max_attempts=3,
                min_wait_seconds=0.05,  # Short waits for testing
                max_wait_seconds=0.2,
                jitter=False,  # Consistent timing
            ),
        )

        # Reset server state first
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{server_base_url}/fail/reset")

        async with create_resilient_client(config) as client:
            start = time.perf_counter()

            # Test individual requests to trigger retries
            # Configure server to fail a moderate number of requests
            async with httpx.AsyncClient() as setup_client:
                await setup_client.post(
                    f"{server_base_url}/fail/count/5"
                )  # Lower count

            # Make sequential requests to ensure some succeed and some retry
            responses = []
            for _ in range(10):
                try:
                    response = await client.get("/msg")
                    responses.append(response)
                except Exception:
                    # Some requests may still fail after retries, that's expected
                    pass

            end = time.perf_counter()

            # At least some should succeed (retry mechanism working)
            assert (
                len(responses) > 0
            ), "No requests succeeded - retry mechanism may not be working"

            # Calculate performance with retries
            total_time = end - start
            avg_time_per_request = total_time / len(responses)

            print("\nRetry Performance Analysis:")
            print(f"  Total time for {len(responses)} requests: {total_time:.2f}s")
            print(
                f"  Average time per request (with retries): "
                f"{avg_time_per_request:.3f}s"
            )

            # Should complete in reasonable time despite retries
            assert avg_time_per_request < 1.0, "Requests with retries taking too long"


class TestMemoryAndResourceUsage:
    """Test memory and resource efficiency."""

    async def test_client_resource_cleanup(self, performance_config):
        """Test that client properly cleans up resources."""
        clients_created = []

        # Create and close multiple clients
        for _ in range(5):
            client = ResilientClient(performance_config)
            await client._ensure_initialized()
            clients_created.append(client)
            await client.close()

        # All clients should be marked as closed
        assert all(client._closed for client in clients_created)

    async def test_concurrent_client_instances(self, performance_config):
        """Test multiple concurrent client instances."""
        num_clients = 5

        # Reset server state to ensure clean test environment
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post(f"{performance_config.base_url}/fail/reset")

        # Create multiple client instances
        clients = []
        for _ in range(num_clients):
            client = ResilientClient(performance_config)
            await client._ensure_initialized()
            clients.append(client)

        try:
            # Each should be able to make requests independently
            tasks = []
            for client in clients:
                tasks.append(client.get("/msg"))

            responses = await asyncio.gather(*tasks)
            assert len(responses) == num_clients
            assert all(r.status_code == 200 for r in responses)

            # All should have different message IDs
            message_ids = [r.json()["message_id"] for r in responses]
            assert len(set(message_ids)) == num_clients

        finally:
            # Clean up all clients
            for client in clients:
                await client.close()


@pytest.fixture(autouse=True)
def skip_if_no_server(server_base_url):
    """Auto-skip performance tests if server is not available."""

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
        pytest.skip("Test server not available for performance tests")


# Add marker for performance tests
pytestmark = pytest.mark.performance
