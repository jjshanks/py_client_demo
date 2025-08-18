#!/usr/bin/env python3
"""
Demonstration of the Resilient HTTP Client Library.

This script shows how to use the client library with all its resilience patterns
against the FastAPI test server. Run the server first with:

    uv run python -m server.main serve --reload

Then run this demo:

    python examples/client_demo.py
"""

import asyncio
import logging
import sys
import time

import httpx

from client import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    LoggingConfig,
    ResilientClient,
    RetryConfig,
)
from client.exceptions import APIConnectionError, PoolTimeoutError, ServerError

# Setup logging to see client behavior
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)


async def test_basic_functionality():
    """Test basic client functionality."""
    print("\n=== Testing Basic Functionality ===")

    config = ClientConfig(
        base_url="http://localhost:8000", logging=LoggingConfig(level="INFO")
    )

    async with ResilientClient(config) as client:
        # Basic request
        response = await client.get("/msg")
        print(f"✅ Basic request: {response.json()['message_id']}")

        # Idempotency test
        request_id = "demo-idempotent-123"
        resp1 = await client.get("/msg", request_id=request_id)
        resp2 = await client.get("/msg", request_id=request_id)

        if resp1.json()["message_id"] == resp2.json()["message_id"]:
            print("✅ Idempotency working: Same request ID returns same response")
        else:
            print("❌ Idempotency failed")


async def test_retry_behavior():
    """Test retry behavior with server failure injection."""
    print("\n=== Testing Retry Behavior ===")

    config = ClientConfig(
        base_url="http://localhost:8000",
        retry=RetryConfig(max_attempts=4, min_wait_seconds=0.2, max_wait_seconds=1.0),
        logging=LoggingConfig(level="DEBUG"),  # See retry logs
    )

    # Configure server to fail 2 requests
    async with httpx.AsyncClient() as setup_client:
        await setup_client.post("http://localhost:8000/fail/reset")
        await setup_client.post("http://localhost:8000/fail/count/2")

    async with ResilientClient(config) as client:
        try:
            response = await client.get("/msg")
            print(f"✅ Retry succeeded: {response.json()['message_id']}")
        except APIConnectionError as e:
            print(f"❌ Retry failed: {e}")


async def test_circuit_breaker():
    """Test circuit breaker behavior."""
    print("\n=== Testing Circuit Breaker ===")

    config = ClientConfig(
        base_url="http://localhost:8000",
        retry=RetryConfig(max_attempts=1),  # No retries for this demo
        circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=2.0),
        logging=LoggingConfig(level="INFO"),
    )

    # Configure sustained failures
    async with httpx.AsyncClient() as setup_client:
        await setup_client.post("http://localhost:8000/fail/reset")
        await setup_client.post("http://localhost:8000/fail/count/10")

    async with ResilientClient(config) as client:
        # Generate failures to trip circuit breaker
        failure_count = 0
        for _ in range(5):
            try:
                await client.get("/msg")
            except ServerError:
                failure_count += 1
                print(f"💥 Failure {failure_count}: Server error")
            except APIConnectionError as e:
                if "Circuit breaker is OPEN" in str(e):
                    print("🔌 Circuit breaker opened - failing fast!")
                    break
                else:
                    print(f"❌ Unexpected connection error: {e}")

        # Reset server and test recovery
        print("⏳ Waiting for circuit breaker recovery...")
        await asyncio.sleep(2.5)

        async with httpx.AsyncClient() as setup_client:
            await setup_client.post("http://localhost:8000/fail/reset")

        try:
            response = await client.get("/msg")
            print(f"✅ Circuit breaker recovered: {response.json()['message_id']}")
        except Exception as e:
            print(f"❌ Recovery failed: {e}")


async def test_bulkhead_pattern():
    """Test bulkhead (concurrency limiting) pattern."""
    print("\n=== Testing Bulkhead Pattern ===")

    config = ClientConfig(
        base_url="http://localhost:8000",
        bulkhead=BulkheadConfig(max_concurrency=3, acquisition_timeout=1.0),
        logging=LoggingConfig(level="DEBUG"),
    )

    async with ResilientClient(config) as client:
        # Start some slow requests to fill bulkhead
        slow_tasks = [
            asyncio.create_task(
                client.get("/msg", params={"delay": 2000})
            )  # 2 second delay
            for _ in range(3)
        ]

        # Wait for them to start
        await asyncio.sleep(0.1)

        # Try additional request - should hit bulkhead timeout
        try:
            await client.get("/msg")
            print("❌ Bulkhead should have prevented this request")
        except PoolTimeoutError:
            print("✅ Bulkhead working: Connection slot acquisition timed out")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")

        # Clean up
        for task in slow_tasks:
            task.cancel()
        await asyncio.gather(*slow_tasks, return_exceptions=True)


async def test_performance_comparison():
    """Compare performance of resilient client vs plain httpx."""
    print("\n=== Performance Comparison ===")

    num_requests = 10

    # Test 1: Resilient client with connection pooling
    config = ClientConfig(base_url="http://localhost:8000")

    start = time.perf_counter()
    async with ResilientClient(config) as client:
        tasks = [client.get("/msg") for _ in range(num_requests)]
        await asyncio.gather(*tasks)
    end = time.perf_counter()

    resilient_time = end - start
    print(f"Resilient client ({num_requests} requests): {resilient_time:.3f}s")

    # Test 2: Plain httpx with connection pooling
    start = time.perf_counter()
    async with httpx.AsyncClient(base_url="http://localhost:8000") as plain_client:
        tasks = [plain_client.get("/msg") for _ in range(num_requests)]
        await asyncio.gather(*tasks)
    end = time.perf_counter()

    plain_time = end - start
    overhead = (resilient_time - plain_time) / plain_time * 100

    print(f"Plain httpx client ({num_requests} requests): {plain_time:.3f}s")
    print(f"Resilience overhead: {overhead:.1f}%")


async def demonstrate_exception_handling():
    """Demonstrate the exception hierarchy."""
    print("\n=== Exception Handling Demo ===")

    config = ClientConfig(
        base_url="http://localhost:8000",
        retry=RetryConfig(max_attempts=2),
        logging=LoggingConfig(level="INFO"),
    )

    async with ResilientClient(config) as client:
        # Test different error scenarios
        scenarios = [
            ("Server error (will retry)", lambda: setup_server_failure(5)),
            ("Too many failures (exhaust retries)", lambda: setup_server_failure(10)),
        ]

        for description, setup_func in scenarios:
            print(f"\n{description}:")
            try:
                await setup_func()
                response = await client.get("/msg")
                print(f"✅ Succeeded: {response.json()['message_id']}")
            except ServerError as e:
                print(f"🔄 Server error (retried): {e}")
            except APIConnectionError as e:
                print(f"💥 Connection error: {e}")
            except Exception as e:
                print(f"❌ Unexpected error: {e}")

            # Reset for next test
            await reset_server()


async def setup_server_failure(count):
    """Helper to setup server failure."""
    async with httpx.AsyncClient() as client:
        await client.post("http://localhost:8000/fail/reset")
        await client.post(f"http://localhost:8000/fail/count/{count}")


async def reset_server():
    """Helper to reset server state."""
    async with httpx.AsyncClient() as client:
        await client.post("http://localhost:8000/fail/reset")


async def check_server_health():
    """Check if the test server is running."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health", timeout=2.0)
            return response.status_code == 200
    except Exception:
        return False


async def main():
    """Run all demonstrations."""
    print("🚀 Resilient HTTP Client Library Demo")
    print("=====================================")

    # Check if server is running
    if not await check_server_health():
        print("❌ Test server is not running!")
        print("\nPlease start the server first:")
        print("    uv run python -m server.main serve --reload")
        print("\nThen run this demo again.")
        return

    print("✅ Test server is running")

    # Reset server state
    await reset_server()

    # Run demonstrations
    await test_basic_functionality()
    await test_retry_behavior()
    await test_circuit_breaker()
    await test_bulkhead_pattern()
    await demonstrate_exception_handling()

    # Performance comparison
    await test_performance_comparison()

    print("\n🎉 Demo completed!")
    print("\nFor more examples, see docs/CLIENT_USAGE.md")


if __name__ == "__main__":
    asyncio.run(main())
