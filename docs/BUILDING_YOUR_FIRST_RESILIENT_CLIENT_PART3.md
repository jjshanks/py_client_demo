# Building Your First Resilient Client: Part 3 - Testing & Production

This is the final part of the tutorial series. Make sure you've completed [Part 1](BUILDING_YOUR_FIRST_RESILIENT_CLIENT.md) and [Part 2](BUILDING_YOUR_FIRST_RESILIENT_CLIENT_PART2.md) first.

In this part, we'll cover:
- Testing strategies for resilient systems
- Production deployment considerations
- Monitoring and observability
- Common pitfalls and how to avoid them

## Step 8: Testing Your Resilient Client

Testing resilient systems requires different strategies than testing simple applications. We need to test both normal operation and failure scenarios.

### Unit Testing Individual Patterns

```python
# test_patterns.py
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, Mock
from circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from exceptions import ConnectionError, ClientError

class TestCircuitBreaker:
    """Test circuit breaker behavior in isolation."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker for testing."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=1.0  # Short timeout for testing
        )
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_circuit_stays_closed_on_success(self, circuit_breaker):
        """Test that circuit stays closed for successful operations."""

        async def successful_operation():
            return "success"

        # Multiple successful calls should keep circuit closed
        for _ in range(5):
            result = await circuit_breaker.call(successful_operation)
            assert result == "success"
            assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold_failures(self, circuit_breaker):
        """Test that circuit opens after reaching failure threshold."""

        async def failing_operation():
            raise ConnectionError("Simulated failure")

        # First 2 failures should keep circuit closed
        for i in range(2):
            with pytest.raises(ConnectionError):
                await circuit_breaker.call(failing_operation)
            assert circuit_breaker.state == CircuitState.CLOSED

        # 3rd failure should open the circuit
        with pytest.raises(ConnectionError):
            await circuit_breaker.call(failing_operation)
        assert circuit_breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_circuit_fails_fast_when_open(self, circuit_breaker):
        """Test that circuit fails fast when open."""

        async def operation():
            return "should not be called"

        # Force circuit to open
        circuit_breaker.state = CircuitState.OPEN
        circuit_breaker.failure_count = 3
        circuit_breaker.last_failure_time = time.time()

        # Should fail fast without calling the operation
        with pytest.raises(ConnectionError, match="Circuit breaker is OPEN"):
            await circuit_breaker.call(operation)

    @pytest.mark.asyncio
    async def test_circuit_recovery_flow(self, circuit_breaker):
        """Test circuit recovery from open to closed."""

        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("Still failing")
            return "recovered"

        # Trip the circuit
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await circuit_breaker.call(operation)

        assert circuit_breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Next call should try half-open and succeed
        result = await circuit_breaker.call(lambda: "recovered")
        assert result == "recovered"
        assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_client_errors_dont_affect_circuit(self, circuit_breaker):
        """Test that client errors (4xx) don't affect circuit breaker."""

        async def client_error_operation():
            raise ClientError("Bad request")

        # Multiple client errors shouldn't open circuit
        for _ in range(5):
            with pytest.raises(ClientError):
                await circuit_breaker.call(client_error_operation)
            assert circuit_breaker.state == CircuitState.CLOSED
            assert circuit_breaker.failure_count == 0

class TestBulkheadPattern:
    """Test bulkhead (concurrency limiting) behavior."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Test that semaphore properly limits concurrent operations."""

        semaphore = asyncio.Semaphore(2)  # Only allow 2 concurrent operations
        active_operations = 0
        max_concurrent = 0

        async def slow_operation(duration: float):
            nonlocal active_operations, max_concurrent

            await semaphore.acquire()
            try:
                active_operations += 1
                max_concurrent = max(max_concurrent, active_operations)
                await asyncio.sleep(duration)
                return f"completed after {duration}s"
            finally:
                active_operations -= 1
                semaphore.release()

        # Start 5 operations concurrently
        tasks = [slow_operation(0.1) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # All should complete
        assert len(results) == 5
        # But no more than 2 should have been active at once
        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_semaphore_timeout(self):
        """Test that semaphore acquisition can timeout."""

        semaphore = asyncio.Semaphore(1)

        async def blocking_operation():
            await semaphore.acquire()
            # Never release - simulate hung operation
            await asyncio.sleep(10)

        async def timing_out_operation():
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
                semaphore.release()
                return "acquired"
            except asyncio.TimeoutError:
                raise Exception("Semaphore acquisition timed out")

        # Start blocking operation
        blocking_task = asyncio.create_task(blocking_operation())
        await asyncio.sleep(0.05)  # Let it acquire the semaphore

        # This should timeout
        with pytest.raises(Exception, match="Semaphore acquisition timed out"):
            await timing_out_operation()

        # Cleanup
        blocking_task.cancel()
        try:
            await blocking_task
        except asyncio.CancelledError:
            pass

# Run the tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

### Integration Testing with Mock Server

```python
# test_integration.py
import pytest
import asyncio
import aiohttp
from aiohttp import web
import time
from production_client import ResilientHTTPClient
from config import ClientConfig, TimeoutConfig, RetryConfig, CircuitBreakerConfig, BulkheadConfig
from exceptions import *

class MockServer:
    """Mock HTTP server for testing resilient client behavior."""

    def __init__(self):
        self.app = web.Application()
        self.request_count = 0
        self.failure_mode = None
        self.setup_routes()

    def setup_routes(self):
        """Setup mock server routes."""
        self.app.router.add_get("/health", self.health_handler)
        self.app.router.add_get("/success", self.success_handler)
        self.app.router.add_get("/failure", self.failure_handler)
        self.app.router.add_get("/slow", self.slow_handler)
        self.app.router.add_get("/intermittent", self.intermittent_handler)

    async def health_handler(self, request):
        """Always returns healthy status."""
        return web.json_response({"status": "healthy"})

    async def success_handler(self, request):
        """Always succeeds."""
        self.request_count += 1
        return web.json_response({
            "message": "success",
            "request_count": self.request_count,
            "request_id": request.headers.get("X-Request-ID")
        })

    async def failure_handler(self, request):
        """Always returns server error."""
        self.request_count += 1
        return web.Response(status=500, text="Internal Server Error")

    async def slow_handler(self, request):
        """Slow response for timeout testing."""
        delay = float(request.query.get("delay", "2.0"))
        await asyncio.sleep(delay)
        return web.json_response({"message": f"slow response after {delay}s"})

    async def intermittent_handler(self, request):
        """Fails first N requests, then succeeds."""
        self.request_count += 1
        fail_count = int(request.query.get("fail_count", "3"))

        if self.request_count <= fail_count:
            return web.Response(status=503, text="Service Unavailable")
        else:
            return web.json_response({
                "message": "success after failures",
                "request_count": self.request_count
            })

    def reset(self):
        """Reset server state."""
        self.request_count = 0
        self.failure_mode = None

@pytest.fixture
async def mock_server():
    """Create and start mock server for testing."""
    server = MockServer()

    # Start the server
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8888)
    await site.start()

    yield server

    # Cleanup
    await runner.cleanup()

@pytest.fixture
def client_config():
    """Create test client configuration."""
    return ClientConfig(
        base_url="http://localhost:8888",
        timeout=TimeoutConfig(
            connect=1.0,
            read=2.0,
            write=1.0,
            pool=0.5
        ),
        retry=RetryConfig(
            max_attempts=3,
            min_wait=0.1,
            max_wait=1.0,
            multiplier=2.0,
            jitter=False  # Disable jitter for predictable testing
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=1.0
        ),
        bulkhead=BulkheadConfig(
            max_concurrency=3,
            acquisition_timeout=1.0
        )
    )

class TestResilientClientIntegration:
    """Integration tests for the complete resilient client."""

    @pytest.mark.asyncio
    async def test_successful_requests(self, mock_server, client_config):
        """Test normal successful operation."""
        client = ResilientHTTPClient(client_config)

        try:
            response = await client.get("/success")
            assert response.status_code == 200

            data = response.json()
            assert data["message"] == "success"
            assert "request_id" in data

        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_behavior(self, mock_server, client_config):
        """Test that retries work for intermittent failures."""
        client = ResilientHTTPClient(client_config)
        mock_server.reset()

        try:
            # This should fail twice, then succeed on retry
            response = await client.get("/intermittent?fail_count=2")
            assert response.status_code == 200

            data = response.json()
            assert data["message"] == "success after failures"
            # Should have made 3 attempts (2 failures + 1 success)
            assert data["request_count"] == 3

        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_timeout_behavior(self, mock_server, client_config):
        """Test timeout handling."""
        client = ResilientHTTPClient(client_config)

        try:
            start_time = time.time()

            with pytest.raises(TimeoutError):
                # Request that takes 5s, but timeout is 2s
                await client.get("/slow?delay=5.0")

            elapsed = time.time() - start_time
            # Should timeout around 2 seconds (read timeout)
            assert 1.5 < elapsed < 3.0

        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration(self, mock_server, client_config):
        """Test circuit breaker with real HTTP requests."""
        client = ResilientHTTPClient(client_config)
        mock_server.reset()

        try:
            # Make enough failing requests to trip circuit breaker
            for i in range(2):
                with pytest.raises(ServerError):
                    await client.get("/failure")

            # Circuit should be open now - next request should fail fast
            start_time = time.time()
            with pytest.raises(ConnectionError, match="Circuit breaker is OPEN"):
                await client.get("/success")  # Would normally succeed
            elapsed = time.time() - start_time

            # Should fail very quickly (no network request)
            assert elapsed < 0.1

            # Wait for recovery timeout
            await asyncio.sleep(1.1)

            # Should work again now
            response = await client.get("/success")
            assert response.status_code == 200

        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_bulkhead_concurrency_limiting(self, mock_server, client_config):
        """Test that bulkhead limits concurrent requests."""
        client = ResilientHTTPClient(client_config)
        mock_server.reset()

        try:
            # Start 5 slow requests (more than bulkhead limit of 3)
            start_time = time.time()

            tasks = [
                client.get("/slow?delay=0.5")
                for _ in range(5)
            ]

            responses = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.time() - start_time

            # Some should succeed, some might fail with PoolTimeoutError
            successful_responses = [r for r in responses if hasattr(r, 'status_code')]
            timeout_errors = [r for r in responses if isinstance(r, PoolTimeoutError)]

            assert len(successful_responses) > 0
            assert len(successful_responses) + len(timeout_errors) == 5

            # Should take longer than 0.5s due to concurrency limiting
            assert elapsed > 0.5

        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_request_id_correlation(self, mock_server, client_config):
        """Test that request IDs are properly added and can be used for correlation."""
        client = ResilientHTTPClient(client_config)

        try:
            # Test with explicit request ID
            response = await client.get("/success", request_id="test-123")
            data = response.json()
            assert data["request_id"] == "test-123"

            # Test with auto-generated request ID
            response = await client.get("/success")
            data = response.json()
            assert data["request_id"] is not None
            assert len(data["request_id"]) == 8  # UUID prefix

        finally:
            await client.close()

# Chaos testing - simulating real-world failure scenarios
class TestChaosScenarios:
    """Test client behavior under chaotic conditions."""

    @pytest.mark.asyncio
    async def test_mixed_failure_scenarios(self, mock_server, client_config):
        """Test client behavior with mixed success/failure patterns."""
        client = ResilientHTTPClient(client_config)

        try:
            results = []

            # Mix of different request types
            test_scenarios = [
                ("/success", "should succeed"),
                ("/failure", "should fail"),
                ("/slow?delay=0.1", "should succeed fast"),
                ("/intermittent?fail_count=1", "should succeed after retry"),
                ("/failure", "should fail again"),
            ]

            for path, description in test_scenarios:
                try:
                    response = await client.get(path)
                    results.append(f"✅ {description}: Success {response.status_code}")
                except Exception as e:
                    results.append(f"❌ {description}: {type(e).__name__}")

            # Should have mix of successes and failures
            successes = [r for r in results if "✅" in r]
            failures = [r for r in results if "❌" in r]

            assert len(successes) > 0, "Should have some successes"
            assert len(failures) > 0, "Should have some failures"

            print("\nChaos test results:")
            for result in results:
                print(f"  {result}")

        finally:
            await client.close()

# Run integration tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
```

### Load Testing

```python
# test_load.py
import asyncio
import time
import statistics
from production_client import ResilientHTTPClient
from config import get_fast_microservice_config

async def load_test_client():
    """Load test the resilient client to measure performance characteristics."""

    config = get_fast_microservice_config("https://httpbin.org")

    # Override for load testing
    config.bulkhead.max_concurrency = 100
    config.logging.level = "WARNING"  # Reduce log noise

    async with ResilientHTTPClient(config) as client:
        print("🚀 Starting load test...")

        # Test parameters
        total_requests = 1000
        concurrent_requests = 50

        # Metrics collection
        successful_requests = 0
        failed_requests = 0
        response_times = []
        start_time = time.time()

        async def make_request(request_id: int):
            """Make a single request and record metrics."""
            nonlocal successful_requests, failed_requests

            request_start = time.time()
            try:
                response = await client.get(f"/json?request_id={request_id}")
                response_time = time.time() - request_start
                response_times.append(response_time)
                successful_requests += 1
                return "success"
            except Exception as e:
                response_time = time.time() - request_start
                response_times.append(response_time)
                failed_requests += 1
                return f"failed: {type(e).__name__}"

        # Execute load test in batches
        print(f"Executing {total_requests} requests with {concurrent_requests} concurrent...")

        for batch_start in range(0, total_requests, concurrent_requests):
            batch_end = min(batch_start + concurrent_requests, total_requests)
            batch_size = batch_end - batch_start

            # Create batch of requests
            tasks = [
                make_request(i)
                for i in range(batch_start, batch_end)
            ]

            # Execute batch
            await asyncio.gather(*tasks, return_exceptions=True)

            # Progress update
            completed = batch_end
            print(f"  Completed: {completed}/{total_requests} ({completed/total_requests*100:.1f}%)")

        # Calculate final metrics
        total_time = time.time() - start_time
        requests_per_second = total_requests / total_time

        print(f"\n📊 Load Test Results:")
        print(f"  Total requests: {total_requests}")
        print(f"  Successful: {successful_requests}")
        print(f"  Failed: {failed_requests}")
        print(f"  Success rate: {successful_requests/total_requests*100:.1f}%")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Requests/second: {requests_per_second:.1f}")

        if response_times:
            print(f"\n⏱️  Response Time Statistics:")
            print(f"  Mean: {statistics.mean(response_times)*1000:.1f}ms")
            print(f"  Median: {statistics.median(response_times)*1000:.1f}ms")
            print(f"  P95: {sorted(response_times)[int(0.95*len(response_times))]*1000:.1f}ms")
            print(f"  P99: {sorted(response_times)[int(0.99*len(response_times))]*1000:.1f}ms")
            print(f"  Min: {min(response_times)*1000:.1f}ms")
            print(f"  Max: {max(response_times)*1000:.1f}ms")

if __name__ == "__main__":
    asyncio.run(load_test_client())
```

---

## Step 9: Production Deployment

### Production Configuration

```python
# production_config.py
import os
from config import ClientConfig, TimeoutConfig, RetryConfig, CircuitBreakerConfig, BulkheadConfig, LoggingConfig

def get_production_config(service_name: str) -> ClientConfig:
    """Get production configuration from environment variables."""

    base_url = os.getenv(f"{service_name.upper()}_BASE_URL")
    if not base_url:
        raise ValueError(f"Environment variable {service_name.upper()}_BASE_URL is required")

    return ClientConfig(
        base_url=base_url,
        timeout=TimeoutConfig(
            connect=float(os.getenv(f"{service_name.upper()}_CONNECT_TIMEOUT", "10.0")),
            read=float(os.getenv(f"{service_name.upper()}_READ_TIMEOUT", "30.0")),
            write=float(os.getenv(f"{service_name.upper()}_WRITE_TIMEOUT", "30.0")),
            pool=float(os.getenv(f"{service_name.upper()}_POOL_TIMEOUT", "5.0"))
        ),
        retry=RetryConfig(
            max_attempts=int(os.getenv(f"{service_name.upper()}_MAX_RETRIES", "3")),
            min_wait=float(os.getenv(f"{service_name.upper()}_MIN_RETRY_WAIT", "1.0")),
            max_wait=float(os.getenv(f"{service_name.upper()}_MAX_RETRY_WAIT", "60.0")),
            multiplier=float(os.getenv(f"{service_name.upper()}_RETRY_MULTIPLIER", "2.0")),
            jitter=os.getenv(f"{service_name.upper()}_RETRY_JITTER", "true").lower() == "true"
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=int(os.getenv(f"{service_name.upper()}_CIRCUIT_THRESHOLD", "5")),
            recovery_timeout=float(os.getenv(f"{service_name.upper()}_CIRCUIT_RECOVERY", "30.0"))
        ),
        bulkhead=BulkheadConfig(
            max_concurrency=int(os.getenv(f"{service_name.upper()}_MAX_CONCURRENCY", "50")),
            acquisition_timeout=float(os.getenv(f"{service_name.upper()}_POOL_TIMEOUT", "30.0"))
        ),
        logging=LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            include_request_id=os.getenv("INCLUDE_REQUEST_ID", "true").lower() == "true",
            logger_name=f"resilient_client.{service_name}"
        ),
        verify_ssl=os.getenv("VERIFY_SSL", "true").lower() == "true",
        user_agent=os.getenv("USER_AGENT", f"resilient-client/{service_name}")
    )

# Example environment configuration
def create_env_file():
    """Create example .env file for production deployment."""
    env_content = """
# Payment Service Configuration
PAYMENT_BASE_URL=https://payment-service.company.com
PAYMENT_CONNECT_TIMEOUT=5.0
PAYMENT_READ_TIMEOUT=15.0
PAYMENT_MAX_RETRIES=3
PAYMENT_CIRCUIT_THRESHOLD=5
PAYMENT_MAX_CONCURRENCY=20

# User Service Configuration
USER_BASE_URL=https://user-service.company.com
USER_CONNECT_TIMEOUT=3.0
USER_READ_TIMEOUT=10.0
USER_MAX_RETRIES=2
USER_CIRCUIT_THRESHOLD=3
USER_MAX_CONCURRENCY=50

# Analytics Service Configuration (less critical)
ANALYTICS_BASE_URL=https://analytics.company.com
ANALYTICS_CONNECT_TIMEOUT=2.0
ANALYTICS_READ_TIMEOUT=5.0
ANALYTICS_MAX_RETRIES=1
ANALYTICS_CIRCUIT_THRESHOLD=2
ANALYTICS_MAX_CONCURRENCY=10

# Global Configuration
LOG_LEVEL=INFO
INCLUDE_REQUEST_ID=true
VERIFY_SSL=true
USER_AGENT=my-application/1.0
"""

    with open(".env.example", "w") as f:
        f.write(env_content.strip())

    print("Created .env.example file with production configuration examples")

if __name__ == "__main__":
    create_env_file()
```

### Service Integration Example

```python
# service_integration.py
"""Example of integrating resilient clients into a FastAPI application."""

from fastapi import FastAPI, HTTPException, Depends
from contextlib import asynccontextmanager
import logging
from production_client import ResilientHTTPClient
from production_config import get_production_config
from exceptions import *

# Global client instances
clients = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""

    # Initialize clients on startup
    logging.info("Initializing resilient HTTP clients...")

    try:
        clients["payment"] = ResilientHTTPClient(get_production_config("payment"))
        clients["user"] = ResilientHTTPClient(get_production_config("user"))
        clients["analytics"] = ResilientHTTPClient(get_production_config("analytics"))

        logging.info("All HTTP clients initialized successfully")
        yield

    finally:
        # Clean up clients on shutdown
        logging.info("Shutting down HTTP clients...")
        for name, client in clients.items():
            try:
                await client.close()
                logging.info(f"Closed {name} client")
            except Exception as e:
                logging.error(f"Error closing {name} client: {e}")

# Create FastAPI app with lifespan management
app = FastAPI(
    title="E-commerce API",
    description="Example API using resilient HTTP clients",
    lifespan=lifespan
)

# Dependency to get clients
def get_payment_client() -> ResilientHTTPClient:
    return clients["payment"]

def get_user_client() -> ResilientHTTPClient:
    return clients["user"]

def get_analytics_client() -> ResilientHTTPClient:
    return clients["analytics"]

@app.get("/users/{user_id}")
async def get_user(user_id: str, user_client: ResilientHTTPClient = Depends(get_user_client)):
    """Get user information with resilient client."""

    try:
        response = await user_client.get(f"/users/{user_id}")
        return response.json()

    except NotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="Authentication failed")
    except ConnectionError as e:
        # Log the error but don't expose internal details
        logging.error(f"User service connection error: {e}")
        raise HTTPException(status_code=503, detail="User service temporarily unavailable")
    except Exception as e:
        logging.error(f"Unexpected error getting user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/orders")
async def create_order(
    order_data: dict,
    payment_client: ResilientHTTPClient = Depends(get_payment_client),
    user_client: ResilientHTTPClient = Depends(get_user_client),
    analytics_client: ResilientHTTPClient = Depends(get_analytics_client)
):
    """Create order with multiple service calls."""

    user_id = order_data.get("user_id")
    request_id = order_data.get("request_id", "order-" + str(time.time()))

    try:
        # Step 1: Validate user (critical)
        try:
            user_response = await user_client.get(f"/users/{user_id}", request_id=request_id)
            user_data = user_response.json()
        except Exception as e:
            logging.error(f"User validation failed for order {request_id}: {e}")
            raise HTTPException(status_code=400, detail="Invalid user")

        # Step 2: Process payment (critical)
        payment_data = {
            "user_id": user_id,
            "amount": order_data["amount"],
            "request_id": request_id
        }

        try:
            payment_response = await payment_client.post(
                "/payments",
                json=payment_data,
                request_id=request_id
            )
            payment_result = payment_response.json()
        except Exception as e:
            logging.error(f"Payment processing failed for order {request_id}: {e}")
            raise HTTPException(status_code=402, detail="Payment processing failed")

        # Step 3: Track analytics (non-critical - graceful degradation)
        try:
            analytics_data = {
                "event": "order_created",
                "user_id": user_id,
                "order_value": order_data["amount"],
                "request_id": request_id
            }

            await analytics_client.post(
                "/events",
                json=analytics_data,
                request_id=request_id
            )
            analytics_tracked = True

        except Exception as e:
            # Non-critical failure - log but don't fail the order
            logging.warning(f"Analytics tracking failed for order {request_id}: {e}")
            analytics_tracked = False

        # Return successful order
        return {
            "order_id": f"order_{request_id}",
            "user_id": user_id,
            "payment_id": payment_result["payment_id"],
            "status": "created",
            "analytics_tracked": analytics_tracked
        }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Catch-all for unexpected errors
        logging.error(f"Unexpected error creating order {request_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    """Health check endpoint that tests all client connections."""

    health_status = {
        "status": "healthy",
        "timestamp": time.time(),
        "services": {}
    }

    # Test each client
    for service_name, client in clients.items():
        try:
            # Quick health check request
            response = await client.get("/health", request_id=f"health-{int(time.time())}")
            health_status["services"][service_name] = {
                "status": "healthy",
                "response_time_ms": response.elapsed.total_seconds() * 1000,
                "circuit_state": client._circuit_breaker.state.value if client._circuit_breaker else "unknown"
            }
        except Exception as e:
            health_status["services"][service_name] = {
                "status": "unhealthy",
                "error": str(e),
                "circuit_state": client._circuit_breaker.state.value if client._circuit_breaker else "unknown"
            }
            health_status["status"] = "degraded"

    return health_status

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Monitoring and Alerting

```python
# monitoring.py
"""Monitoring and metrics collection for resilient clients."""

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List
from collections import defaultdict, deque

@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    timestamp: float
    method: str
    path: str
    status_code: int
    response_time_ms: float
    exception_type: str = None
    circuit_state: str = None
    retry_count: int = 0

@dataclass
class ServiceMetrics:
    """Aggregated metrics for a service."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    p95_response_time_ms: float = 0.0
    circuit_open_count: int = 0
    retry_count: int = 0
    recent_requests: deque = field(default_factory=lambda: deque(maxlen=1000))

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100.0

    @property
    def error_rate(self) -> float:
        """Calculate error rate percentage."""
        return 100.0 - self.success_rate

class MetricsCollector:
    """Collects and aggregates metrics from resilient clients."""

    def __init__(self):
        self.service_metrics: Dict[str, ServiceMetrics] = defaultdict(ServiceMetrics)
        self.logger = logging.getLogger("metrics")

    def record_request(self, service_name: str, metrics: RequestMetrics):
        """Record metrics for a single request."""
        service = self.service_metrics[service_name]

        # Update counters
        service.total_requests += 1
        if 200 <= metrics.status_code < 400:
            service.successful_requests += 1
        else:
            service.failed_requests += 1

        # Track retries
        service.retry_count += metrics.retry_count

        # Track circuit state changes
        if metrics.circuit_state == "OPEN":
            service.circuit_open_count += 1

        # Store recent request for percentile calculations
        service.recent_requests.append(metrics)

        # Update response time statistics
        if service.recent_requests:
            response_times = [r.response_time_ms for r in service.recent_requests]
            service.avg_response_time_ms = sum(response_times) / len(response_times)
            service.p95_response_time_ms = sorted(response_times)[int(0.95 * len(response_times))]

    def get_service_metrics(self, service_name: str) -> ServiceMetrics:
        """Get current metrics for a service."""
        return self.service_metrics[service_name]

    def get_all_metrics(self) -> Dict[str, ServiceMetrics]:
        """Get metrics for all services."""
        return dict(self.service_metrics)

    def export_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []

        for service_name, metrics in self.service_metrics.items():
            # Request counters
            lines.append(f'http_requests_total{{service="{service_name}",status="success"}} {metrics.successful_requests}')
            lines.append(f'http_requests_total{{service="{service_name}",status="error"}} {metrics.failed_requests}')

            # Response times
            lines.append(f'http_request_duration_ms{{service="{service_name}",quantile="0.50"}} {metrics.avg_response_time_ms}')
            lines.append(f'http_request_duration_ms{{service="{service_name}",quantile="0.95"}} {metrics.p95_response_time_ms}')

            # Circuit breaker
            lines.append(f'circuit_breaker_open_total{{service="{service_name}"}} {metrics.circuit_open_count}')

            # Retries
            lines.append(f'http_retries_total{{service="{service_name}"}} {metrics.retry_count}')

        return '\n'.join(lines)

    def check_alerts(self) -> List[str]:
        """Check for alert conditions and return alert messages."""
        alerts = []

        for service_name, metrics in self.service_metrics.items():
            # High error rate alert
            if metrics.error_rate > 10.0 and metrics.total_requests > 10:
                alerts.append(
                    f"HIGH_ERROR_RATE: {service_name} error rate is {metrics.error_rate:.1f}% "
                    f"({metrics.failed_requests}/{metrics.total_requests} requests)"
                )

            # High response time alert
            if metrics.p95_response_time_ms > 5000:  # 5 seconds
                alerts.append(
                    f"HIGH_LATENCY: {service_name} P95 response time is {metrics.p95_response_time_ms:.0f}ms"
                )

            # Circuit breaker alert
            if metrics.circuit_open_count > 0:
                alerts.append(
                    f"CIRCUIT_OPEN: {service_name} circuit breaker has opened {metrics.circuit_open_count} times"
                )

        return alerts

# Global metrics collector
metrics_collector = MetricsCollector()

# Enhanced client wrapper with metrics
class MonitoredResilientClient:
    """Wrapper around ResilientHTTPClient that collects metrics."""

    def __init__(self, service_name: str, client: ResilientHTTPClient):
        self.service_name = service_name
        self.client = client
        self.metrics_collector = metrics_collector

    async def request(self, method: str, path: str, **kwargs):
        """Make request with metrics collection."""
        start_time = time.time()
        exception_type = None
        retry_count = 0

        try:
            response = await self.client.request(method, path, **kwargs)
            status_code = response.status_code
            return response

        except Exception as e:
            exception_type = type(e).__name__
            status_code = getattr(e, 'response', {}).get('status_code', 0) if hasattr(e, 'response') else 0
            raise

        finally:
            # Record metrics
            response_time_ms = (time.time() - start_time) * 1000
            circuit_state = self.client._circuit_breaker.state.value if self.client._circuit_breaker else None

            metrics = RequestMetrics(
                timestamp=time.time(),
                method=method,
                path=path,
                status_code=status_code,
                response_time_ms=response_time_ms,
                exception_type=exception_type,
                circuit_state=circuit_state,
                retry_count=retry_count
            )

            self.metrics_collector.record_request(self.service_name, metrics)

    async def get(self, path: str, **kwargs):
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs):
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs):
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs):
        return await self.request("DELETE", path, **kwargs)

    async def close(self):
        await self.client.close()

# Monitoring dashboard
async def print_metrics_dashboard():
    """Print a simple metrics dashboard."""
    while True:
        await asyncio.sleep(10)  # Update every 10 seconds

        print("\n" + "="*80)
        print("🔍 RESILIENT CLIENT METRICS DASHBOARD")
        print("="*80)

        all_metrics = metrics_collector.get_all_metrics()

        if not all_metrics:
            print("No metrics available yet...")
            continue

        for service_name, metrics in all_metrics.items():
            print(f"\n📊 {service_name.upper()} SERVICE:")
            print(f"  Requests: {metrics.total_requests} (✅ {metrics.successful_requests}, ❌ {metrics.failed_requests})")
            print(f"  Success Rate: {metrics.success_rate:.1f}%")
            print(f"  Avg Response Time: {metrics.avg_response_time_ms:.0f}ms")
            print(f"  P95 Response Time: {metrics.p95_response_time_ms:.0f}ms")
            print(f"  Circuit Opens: {metrics.circuit_open_count}")
            print(f"  Total Retries: {metrics.retry_count}")

        # Check for alerts
        alerts = metrics_collector.check_alerts()
        if alerts:
            print(f"\n🚨 ALERTS:")
            for alert in alerts:
                print(f"  {alert}")
        else:
            print(f"\n✅ All services healthy")

if __name__ == "__main__":
    # Example usage
    async def demo_monitoring():
        from production_client import ResilientHTTPClient
        from config import get_fast_microservice_config

        # Create monitored client
        config = get_fast_microservice_config("https://httpbin.org")
        base_client = ResilientHTTPClient(config)
        monitored_client = MonitoredResilientClient("demo_service", base_client)

        # Start metrics dashboard
        dashboard_task = asyncio.create_task(print_metrics_dashboard())

        try:
            # Make some requests to generate metrics
            for i in range(50):
                try:
                    if i % 10 == 0:
                        # Occasional failure for demo
                        await monitored_client.get("/status/500")
                    else:
                        await monitored_client.get("/json")

                    await asyncio.sleep(0.1)

                except Exception:
                    pass  # Expected for demo

            # Let dashboard run for a bit
            await asyncio.sleep(30)

        finally:
            dashboard_task.cancel()
            await monitored_client.close()

    asyncio.run(demo_monitoring())
```

## Conclusion

Congratulations! You've built a complete, production-ready resilient HTTP client from scratch. Here's what you've accomplished:

### What You Built

✅ **Timeout Pattern** - Prevents hanging requests
✅ **Retry Pattern** - Handles transient failures automatically
✅ **Circuit Breaker Pattern** - Protects against cascading failures
✅ **Bulkhead Pattern** - Limits resource consumption
✅ **Exception Hierarchy** - Provides semantic error handling
✅ **Configuration System** - Makes the client adaptable to different environments
✅ **Testing Strategy** - Ensures reliability through comprehensive testing
✅ **Production Integration** - Ready for real-world deployment
✅ **Monitoring & Metrics** - Provides observability and alerting

### Key Lessons Learned

1. **Resilience is about graceful failure** - Not preventing failures, but handling them well
2. **Patterns work together** - Each pattern addresses specific failure modes
3. **Configuration matters** - Different environments need different settings
4. **Testing is crucial** - You must test failure scenarios, not just happy paths
5. **Observability is essential** - You need metrics to tune and debug your system

### Next Steps

1. **Use the client** in your real applications
2. **Monitor its behavior** in production
3. **Tune configuration** based on real traffic patterns
4. **Study advanced patterns** like saga pattern, CQRS, and event sourcing
5. **Contribute back** to the open source community

### Resources for Further Learning

- **Books**: "Release It!" by Michael Nygard, "Building Microservices" by Sam Newman
- **Patterns**: Martin Fowler's patterns catalog at martinfowler.com
- **Tools**: Hystrix (Java), Polly (.NET), resilience4j (Java)
- **Monitoring**: Prometheus, Grafana, OpenTelemetry
- **Chaos Engineering**: Chaos Monkey, Gremlin, Litmus

Remember: Building resilient systems is an ongoing journey, not a destination. Keep learning, keep improving, and always be prepared for the unexpected! 🚀
