# Building Your First Resilient Client: Part 2

This is the continuation of [Part 1](BUILDING_YOUR_FIRST_RESILIENT_CLIENT.md). If you haven't completed Part 1, please do that first.

In this part, we'll add the remaining resilience patterns and create a complete, production-ready client.

## What's Covered in Part 2

5. [Step 5: Implementing Circuit Breaker](#step-5-implementing-circuit-breaker)
6. [Step 6: Adding Bulkhead Pattern](#step-6-adding-bulkhead-pattern)
7. [Step 7: Creating Configuration System](#step-7-creating-configuration-system)
8. [Step 8: Testing Your Resilient Client](#step-8-testing-your-resilient-client)
9. [Step 9: Production Deployment](#step-9-production-deployment)

---

## Step 5: Implementing Circuit Breaker

The circuit breaker pattern prevents wasted retries when a service is known to be failing.

### Create Circuit Breaker Implementation

```python
# circuit_breaker.py
import asyncio
import time
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Any
from exceptions import ConnectionError

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"           # Failing fast
    HALF_OPEN = "half_open" # Testing recovery

@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5      # Failures before opening
    recovery_timeout: float = 30.0  # Seconds before testing recovery

class CircuitBreaker:
    """Circuit breaker implementation for preventing cascading failures."""

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.lock = asyncio.Lock()  # Thread safety
        self.logger = logging.getLogger(f"{__name__}.CircuitBreaker")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function call protected by the circuit breaker."""

        # Check circuit state (thread-safe)
        async with self.lock:
            if self.state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if (self.last_failure_time and
                    time.time() - self.last_failure_time < self.config.recovery_timeout):
                    # Still in timeout period - fail fast
                    raise ConnectionError("Circuit breaker is OPEN - failing fast")
                else:
                    # Recovery timeout elapsed - try half-open
                    self.state = CircuitState.HALF_OPEN
                    self.logger.info("Circuit breaker moving to HALF_OPEN for recovery test")

        try:
            # Execute the protected function
            result = await func(*args, **kwargs)

            # Success! Update circuit state
            async with self.lock:
                if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.last_failure_time = None
                    self.logger.info("Circuit breaker CLOSED - service recovered")

            return result

        except ConnectionError as e:
            # Connection error - record failure
            async with self.lock:
                self.failure_count += 1
                self.last_failure_time = time.time()

                # Check if we should open the circuit
                if self.failure_count >= self.config.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        self.state = CircuitState.OPEN
                        self.logger.warning(
                            f"Circuit breaker OPENED after {self.failure_count} failures"
                        )

            # Re-raise the connection error
            raise

        except Exception:
            # Non-connection errors don't affect circuit breaker
            raise

# Test circuit breaker behavior
async def test_circuit_breaker():
    """Test circuit breaker state transitions."""

    circuit_config = CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=2.0  # Short for testing
    )

    circuit_breaker = CircuitBreaker(circuit_config)

    async def failing_function():
        """A function that always fails with connection error."""
        raise ConnectionError("Simulated network failure")

    async def working_function():
        """A function that always succeeds."""
        return "Success!"

    print("🧪 Testing Circuit Breaker State Transitions\n")

    # Test 1: Normal failures (circuit should stay CLOSED)
    print("1. Testing normal failures (circuit stays CLOSED):")
    for i in range(2):
        try:
            await circuit_breaker.call(failing_function)
        except ConnectionError:
            print(f"   Failure {i+1}: Circuit state = {circuit_breaker.state.value}")

    # Test 2: Threshold breach (circuit should OPEN)
    print("\n2. Testing threshold breach (circuit should OPEN):")
    try:
        await circuit_breaker.call(failing_function)
    except ConnectionError:
        print(f"   Failure 3: Circuit state = {circuit_breaker.state.value}")

    # Test 3: Fail fast while OPEN
    print("\n3. Testing fail-fast behavior while OPEN:")
    try:
        await circuit_breaker.call(failing_function)
    except ConnectionError as e:
        print(f"   Fast failure: {e}")

    # Test 4: Recovery timeout and HALF_OPEN
    print("\n4. Testing recovery (waiting for timeout):")
    await asyncio.sleep(2.1)  # Wait for recovery timeout

    print("   After timeout, next call should move to HALF_OPEN")
    try:
        result = await circuit_breaker.call(working_function)
        print(f"   Recovery success: {result}")
        print(f"   Circuit state: {circuit_breaker.state.value}")
    except Exception as e:
        print(f"   Recovery failed: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(test_circuit_breaker())
```

### Integrate Circuit Breaker with Client

```python
# circuit_breaker_client.py
import asyncio
import httpx
import time
import logging
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from exceptions import *

@dataclass
class RetryConfig:
    max_attempts: int = 3
    min_wait: float = 1.0
    max_wait: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True

@dataclass
class TimeoutConfig:
    connect: float = 10.0
    read: float = 30.0
    write: float = 15.0
    pool: float = 5.0

class CircuitBreakerClient:
    """HTTP client with circuit breaker protection."""

    def __init__(self, base_url: str, timeout_config: TimeoutConfig = None,
                 retry_config: RetryConfig = None, circuit_config: CircuitBreakerConfig = None):
        self.base_url = base_url
        self.timeout_config = timeout_config or TimeoutConfig()
        self.retry_config = retry_config or RetryConfig()
        self.circuit_config = circuit_config or CircuitBreakerConfig()

        # Initialize components
        httpx_timeout = httpx.Timeout(
            connect=self.timeout_config.connect,
            read=self.timeout_config.read,
            write=self.timeout_config.write,
            pool=self.timeout_config.pool
        )

        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx_timeout)
        self.circuit_breaker = CircuitBreaker(self.circuit_config)
        self.logger = logging.getLogger(__name__)

    def _map_exception(self, exc: Exception, response=None) -> APIError:
        """Convert httpx exceptions to our semantic exceptions."""
        if isinstance(exc, httpx.TimeoutException):
            return TimeoutError("Request timed out", response=response)

        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            return ConnectionError(f"Network error: {exc}", response=response)

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code

            if status in (401, 403):
                return AuthenticationError(f"Auth failed: {status}", response=exc.response)
            elif status == 404:
                return NotFoundError("Resource not found", response=exc.response)
            elif 400 <= status < 500:
                return ClientError(f"Client error: {status}", response=exc.response)
            elif 500 <= status < 600:
                return ServerError(f"Server error: {status}", response=exc.response)

        return APIError(f"Unknown error: {exc}", response=response)

    def _create_retry_decorator(self, operation_name: str):
        """Create a retry decorator for a specific operation."""

        def log_retry_attempt(retry_state):
            if retry_state.outcome and retry_state.outcome.failed:
                exception = retry_state.outcome.exception()
                attempt = retry_state.attempt_number
                wait_time = retry_state.next_action.sleep if retry_state.next_action else 0

                self.logger.warning(
                    f"{operation_name} attempt {attempt} failed: {type(exception).__name__}: {exception}. "
                    f"Retrying in {wait_time:.2f}s"
                )

        return retry(
            retry=retry_if_exception_type(ConnectionError),
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_config.multiplier,
                min=self.retry_config.min_wait,
                max=self.retry_config.max_wait
            ),
            before_sleep=log_retry_attempt,
            reraise=True
        )

    async def get(self, path: str):
        """Make a GET request with circuit breaker and retry protection."""

        async def make_request_with_retries():
            """HTTP request with retry logic - protected by circuit breaker."""

            @self._create_retry_decorator(f"GET {path}")
            async def _make_request():
                start_time = time.time()

                try:
                    self.logger.debug(f"GET {path}")
                    response = await self.client.get(path)
                    response.raise_for_status()

                    elapsed = time.time() - start_time
                    self.logger.debug(f"GET {path} completed in {elapsed:.3f}s")
                    return response

                except Exception as e:
                    elapsed = time.time() - start_time
                    self.logger.debug(f"GET {path} failed after {elapsed:.3f}s: {e}")

                    response_attr = getattr(e, 'response', None)
                    raise self._map_exception(e, response_attr) from e

            return await _make_request()

        # Execute with circuit breaker protection
        return await self.circuit_breaker.call(make_request_with_retries)

    async def close(self):
        await self.client.aclose()

# Test circuit breaker integration
async def test_circuit_integration():
    """Test circuit breaker integration with HTTP client."""

    # Configure for quick testing
    circuit_config = CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout=3.0
    )

    client = CircuitBreakerClient(
        "https://httpbin.org",
        circuit_config=circuit_config
    )

    print("🧪 Testing Circuit Breaker Integration\n")

    # Test 1: Successful requests
    print("1. Testing successful requests:")
    try:
        response = await client.get("/json")
        print(f"   ✅ Success: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # Test 2: Trigger circuit opening
    print("\n2. Triggering circuit breaker (making failing requests):")
    for i in range(3):
        try:
            response = await client.get("/status/500")
        except Exception as e:
            print(f"   Attempt {i+1}: {type(e).__name__}")

    # Test 3: Circuit should be open now
    print("\n3. Testing fail-fast (circuit should be OPEN):")
    try:
        response = await client.get("/json")  # Would normally succeed
        print(f"   Unexpected success: {response.status_code}")
    except ConnectionError as e:
        if "Circuit breaker is OPEN" in str(e):
            print(f"   ✅ Fast failure: {e}")
        else:
            print(f"   Unexpected error: {e}")

    # Test 4: Wait for recovery
    print("\n4. Waiting for recovery timeout...")
    await asyncio.sleep(3.5)

    print("5. Testing recovery:")
    try:
        response = await client.get("/json")
        print(f"   ✅ Recovery successful: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Recovery failed: {e}")

    await client.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(test_circuit_integration())
```

---

## Step 6: Adding Bulkhead Pattern

The bulkhead pattern prevents resource exhaustion by limiting concurrent operations.

### Implement Bulkhead Pattern

```python
# resilient_client.py
import asyncio
import httpx
import time
import logging
import uuid
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from exceptions import *

class PoolTimeoutError(APIError):
    """Failed to acquire resource from bulkhead pool."""
    pass

@dataclass
class BulkheadConfig:
    """Configuration for bulkhead (concurrency limiting) pattern."""
    max_concurrency: int = 50
    acquisition_timeout: float = 30.0

@dataclass
class RetryConfig:
    max_attempts: int = 3
    min_wait: float = 1.0
    max_wait: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True

@dataclass
class TimeoutConfig:
    connect: float = 10.0
    read: float = 30.0
    write: float = 15.0
    pool: float = 5.0

class ResilientClient:
    """Fully resilient HTTP client with all patterns implemented."""

    def __init__(self, base_url: str, timeout_config: TimeoutConfig = None,
                 retry_config: RetryConfig = None, circuit_config: CircuitBreakerConfig = None,
                 bulkhead_config: BulkheadConfig = None):
        self.base_url = base_url
        self.timeout_config = timeout_config or TimeoutConfig()
        self.retry_config = retry_config or RetryConfig()
        self.circuit_config = circuit_config or CircuitBreakerConfig()
        self.bulkhead_config = bulkhead_config or BulkheadConfig()

        # Initialize HTTP client
        httpx_timeout = httpx.Timeout(
            connect=self.timeout_config.connect,
            read=self.timeout_config.read,
            write=self.timeout_config.write,
            pool=self.timeout_config.pool
        )

        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx_timeout)

        # Initialize resilience components
        self.circuit_breaker = CircuitBreaker(self.circuit_config)
        self.semaphore = asyncio.Semaphore(self.bulkhead_config.max_concurrency)

        self.logger = logging.getLogger(__name__)

    def _map_exception(self, exc: Exception, response=None) -> APIError:
        """Convert httpx exceptions to our semantic exceptions."""
        if isinstance(exc, httpx.TimeoutException):
            return TimeoutError("Request timed out", response=response)

        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            return ConnectionError(f"Network error: {exc}", response=response)

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code

            if status in (401, 403):
                return AuthenticationError(f"Auth failed: {status}", response=exc.response)
            elif status == 404:
                return NotFoundError("Resource not found", response=exc.response)
            elif 400 <= status < 500:
                return ClientError(f"Client error: {status}", response=exc.response)
            elif 500 <= status < 600:
                return ServerError(f"Server error: {status}", response=exc.response)

        return APIError(f"Unknown error: {exc}", response=response)

    async def _acquire_semaphore(self, request_id: str):
        """Acquire semaphore slot with timeout (bulkhead pattern)."""
        self.logger.debug(f"[{request_id}] Waiting for semaphore slot")

        try:
            await asyncio.wait_for(
                self.semaphore.acquire(),
                timeout=self.bulkhead_config.acquisition_timeout
            )

            # Log successful acquisition
            available_slots = self.semaphore._value
            self.logger.debug(
                f"[{request_id}] Acquired semaphore slot. {available_slots} remaining"
            )
        except asyncio.TimeoutError as e:
            raise PoolTimeoutError(
                f"Failed to acquire semaphore slot within "
                f"{self.bulkhead_config.acquisition_timeout}s - client saturated"
            ) from e

    def _create_retry_decorator(self, operation_name: str, request_id: str):
        """Create a retry decorator for a specific operation."""

        def log_retry_attempt(retry_state):
            if retry_state.outcome and retry_state.outcome.failed:
                exception = retry_state.outcome.exception()
                attempt = retry_state.attempt_number
                wait_time = retry_state.next_action.sleep if retry_state.next_action else 0

                self.logger.warning(
                    f"[{request_id}] {operation_name} attempt {attempt} failed: "
                    f"{type(exception).__name__}: {exception}. Retrying in {wait_time:.2f}s"
                )

        return retry(
            retry=retry_if_exception_type(ConnectionError),
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_config.multiplier,
                min=self.retry_config.min_wait,
                max=self.retry_config.max_wait
            ),
            before_sleep=log_retry_attempt,
            reraise=True
        )

    async def get(self, path: str, request_id: str = None):
        """Make a GET request with full resilience protection."""

        # Generate request ID for tracing
        if request_id is None:
            request_id = str(uuid.uuid4())[:8]

        # Step 1: Acquire semaphore (bulkhead protection)
        await self._acquire_semaphore(request_id)

        try:
            # Step 2: Execute with circuit breaker protection
            async def make_request_with_retries():
                """HTTP request with retry logic - protected by circuit breaker."""

                @self._create_retry_decorator(f"GET {path}", request_id)
                async def _make_request():
                    start_time = time.time()

                    try:
                        self.logger.debug(f"[{request_id}] GET {path}")
                        response = await self.client.get(path)
                        response.raise_for_status()

                        elapsed = time.time() - start_time
                        self.logger.debug(f"[{request_id}] GET {path} completed in {elapsed:.3f}s")
                        return response

                    except Exception as e:
                        elapsed = time.time() - start_time
                        self.logger.debug(f"[{request_id}] GET {path} failed after {elapsed:.3f}s: {e}")

                        response_attr = getattr(e, 'response', None)
                        raise self._map_exception(e, response_attr) from e

                return await _make_request()

            # Execute with circuit breaker protection
            return await self.circuit_breaker.call(make_request_with_retries)

        finally:
            # Step 3: Always release semaphore
            self.semaphore.release()
            self.logger.debug(f"[{request_id}] Released semaphore slot")

    async def close(self):
        """Clean up resources."""
        await self.client.aclose()

# Test complete resilient client
async def test_resilient_client():
    """Test the complete resilient client with all patterns."""

    # Configure for testing
    bulkhead_config = BulkheadConfig(
        max_concurrency=3,  # Small pool for testing
        acquisition_timeout=2.0
    )

    circuit_config = CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout=3.0
    )

    client = ResilientClient(
        "https://httpbin.org",
        bulkhead_config=bulkhead_config,
        circuit_config=circuit_config
    )

    print("🧪 Testing Complete Resilient Client\n")

    # Test 1: Normal operation
    print("1. Testing normal operation:")
    try:
        response = await client.get("/json")
        print(f"   ✅ Success: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # Test 2: Concurrent requests (bulkhead test)
    print("\n2. Testing concurrent requests (bulkhead):")

    async def make_slow_request(delay: int):
        """Make a request that takes a specific amount of time."""
        try:
            response = await client.get(f"/delay/{delay}")
            return f"Completed delay {delay}s"
        except Exception as e:
            return f"Failed delay {delay}s: {type(e).__name__}"

    # Launch 5 concurrent requests (more than bulkhead limit of 3)
    start_time = time.time()
    tasks = [make_slow_request(2) for _ in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.time() - start_time

    print(f"   Concurrent requests completed in {elapsed:.2f}s:")
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"   Request {i+1}: {type(result).__name__}: {result}")
        else:
            print(f"   Request {i+1}: {result}")

    # Test 3: Circuit breaker with retries
    print("\n3. Testing circuit breaker integration:")
    for i in range(3):
        try:
            response = await client.get("/status/500")
            print(f"   Attempt {i+1}: Unexpected success")
        except Exception as e:
            print(f"   Attempt {i+1}: {type(e).__name__}")

    await client.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    asyncio.run(test_resilient_client())
```

### What You Learned

- How to implement the bulkhead pattern with semaphores
- Why resource isolation is critical in distributed systems
- How to coordinate multiple resilience patterns
- The importance of proper resource cleanup (semaphore release)

---

## Step 7: Creating Configuration System

Let's create a clean configuration system that makes the client easy to use.

### Final Configuration Classes

```python
# config.py
from dataclasses import dataclass
from typing import Optional
import httpx

@dataclass
class TimeoutConfig:
    """HTTP timeout configuration for different phases."""
    connect: float = 10.0    # Connection establishment timeout
    read: float = 30.0       # Response reading timeout
    write: float = 15.0      # Request writing timeout
    pool: float = 5.0        # Connection pool timeout

    def to_httpx_timeout(self) -> httpx.Timeout:
        """Convert to httpx.Timeout object."""
        return httpx.Timeout(
            connect=self.connect,
            read=self.read,
            write=self.write,
            pool=self.pool
        )

@dataclass
class RetryConfig:
    """Retry behavior configuration."""
    max_attempts: int = 3           # Maximum retry attempts
    min_wait: float = 1.0          # Minimum wait between retries
    max_wait: float = 60.0         # Maximum wait between retries
    multiplier: float = 2.0        # Exponential backoff multiplier
    jitter: bool = True            # Add random jitter to prevent thundering herd

@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5      # Failures before opening circuit
    recovery_timeout: float = 30.0 # Seconds before testing recovery

@dataclass
class BulkheadConfig:
    """Bulkhead (concurrency limiting) configuration."""
    max_concurrency: int = 50       # Maximum concurrent requests
    acquisition_timeout: float = 30.0  # Timeout for acquiring semaphore slot

@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"             # Log level
    include_request_id: bool = True # Include request ID in logs
    logger_name: str = "resilient_client"

@dataclass
class ClientConfig:
    """Complete configuration for the resilient HTTP client."""
    base_url: str                                          # Base URL for requests (required)
    timeout: TimeoutConfig = None                          # Timeout configuration
    retry: RetryConfig = None                              # Retry configuration
    circuit_breaker: CircuitBreakerConfig = None          # Circuit breaker configuration
    bulkhead: BulkheadConfig = None                        # Bulkhead configuration
    logging: LoggingConfig = None                          # Logging configuration

    # Optional httpx client configuration
    follow_redirects: bool = True                          # Follow HTTP redirects
    verify_ssl: bool = True                                # Verify SSL certificates
    user_agent: Optional[str] = None                       # Custom User-Agent header

    def __post_init__(self):
        """Initialize default configurations if not provided."""
        if self.timeout is None:
            self.timeout = TimeoutConfig()
        if self.retry is None:
            self.retry = RetryConfig()
        if self.circuit_breaker is None:
            self.circuit_breaker = CircuitBreakerConfig()
        if self.bulkhead is None:
            self.bulkhead = BulkheadConfig()
        if self.logging is None:
            self.logging = LoggingConfig()

# Predefined configurations for common scenarios
def get_high_latency_config(base_url: str) -> ClientConfig:
    """Configuration optimized for high-latency networks (satellite, international)."""
    return ClientConfig(
        base_url=base_url,
        timeout=TimeoutConfig(
            connect=30.0,
            read=120.0,
            write=60.0,
            pool=15.0
        ),
        retry=RetryConfig(
            max_attempts=5,
            min_wait=2.0,
            max_wait=300.0,
            multiplier=2.0,
            jitter=True
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout=120.0
        ),
        bulkhead=BulkheadConfig(
            max_concurrency=20,
            acquisition_timeout=60.0
        )
    )

def get_fast_microservice_config(base_url: str) -> ClientConfig:
    """Configuration optimized for fast internal microservices."""
    return ClientConfig(
        base_url=base_url,
        timeout=TimeoutConfig(
            connect=2.0,
            read=5.0,
            write=5.0,
            pool=1.0
        ),
        retry=RetryConfig(
            max_attempts=2,
            min_wait=0.5,
            max_wait=10.0,
            multiplier=2.0,
            jitter=True
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=10.0
        ),
        bulkhead=BulkheadConfig(
            max_concurrency=100,
            acquisition_timeout=5.0
        )
    )

def get_batch_processing_config(base_url: str) -> ClientConfig:
    """Configuration optimized for batch processing (can wait longer)."""
    return ClientConfig(
        base_url=base_url,
        timeout=TimeoutConfig(
            connect=10.0,
            read=300.0,  # 5 minutes for large data processing
            write=120.0,
            pool=30.0
        ),
        retry=RetryConfig(
            max_attempts=10,
            min_wait=5.0,
            max_wait=600.0,  # Up to 10 minutes
            multiplier=1.5,   # Gentler backoff
            jitter=True
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=15,
            recovery_timeout=180.0  # 3 minutes
        ),
        bulkhead=BulkheadConfig(
            max_concurrency=5,  # Lower concurrency for batch
            acquisition_timeout=120.0
        )
    )
```

### Complete Production Client

```python
# production_client.py
import asyncio
import httpx
import time
import logging
import uuid
from typing import Optional, Any, Dict
from contextlib import asynccontextmanager
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import ClientConfig
from circuit_breaker import CircuitBreaker
from exceptions import *

class ResilientHTTPClient:
    """Production-ready resilient HTTP client with all patterns implemented."""

    def __init__(self, config: ClientConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._closed = False

        # Setup logging
        self.logger = logging.getLogger(config.logging.logger_name)
        self.logger.setLevel(getattr(logging, config.logging.level.upper()))

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_initialized()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_initialized(self):
        """Initialize the client if not already initialized."""
        if self._client is None:
            # Create httpx client
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout.to_httpx_timeout(),
                follow_redirects=self.config.follow_redirects,
                verify=self.config.verify_ssl,
                headers={"User-Agent": self.config.user_agent} if self.config.user_agent else None
            )

            # Initialize resilience components
            self._circuit_breaker = CircuitBreaker(self.config.circuit_breaker)
            self._semaphore = asyncio.Semaphore(self.config.bulkhead.max_concurrency)

            self.logger.info(f"ResilientHTTPClient initialized with base_url={self.config.base_url}")

    def _map_exception(self, exc: Exception, response=None) -> APIError:
        """Convert httpx exceptions to our semantic exceptions."""
        if isinstance(exc, httpx.TimeoutException):
            return TimeoutError("Request timed out", response=response)

        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            return ConnectionError(f"Network error: {exc}", response=response)

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code

            if status in (401, 403):
                return AuthenticationError(f"Auth failed: {status}", response=exc.response)
            elif status == 404:
                return NotFoundError("Resource not found", response=exc.response)
            elif 400 <= status < 500:
                return ClientError(f"Client error: {status}", response=exc.response)
            elif 500 <= status < 600:
                return ServerError(f"Server error: {status}", response=exc.response)

        return APIError(f"Unknown error: {exc}", response=response)

    async def _acquire_semaphore(self, request_id: str):
        """Acquire semaphore slot with timeout."""
        if self.config.logging.include_request_id:
            self.logger.debug(f"[{request_id}] Waiting for semaphore slot")

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.config.bulkhead.acquisition_timeout
            )

            if self.config.logging.include_request_id:
                available = self._semaphore._value
                self.logger.debug(f"[{request_id}] Acquired slot. {available} remaining")

        except asyncio.TimeoutError as e:
            raise PoolTimeoutError(
                f"Failed to acquire semaphore slot within "
                f"{self.config.bulkhead.acquisition_timeout}s - client saturated"
            ) from e

    def _create_retry_decorator(self, operation: str, request_id: str):
        """Create a retry decorator."""

        def log_retry_attempt(retry_state):
            if retry_state.outcome and retry_state.outcome.failed:
                exception = retry_state.outcome.exception()
                attempt = retry_state.attempt_number
                wait_time = retry_state.next_action.sleep if retry_state.next_action else 0

                if self.config.logging.include_request_id:
                    self.logger.warning(
                        f"[{request_id}] {operation} attempt {attempt} failed: "
                        f"{type(exception).__name__}: {exception}. Retrying in {wait_time:.2f}s"
                    )

        return retry(
            retry=retry_if_exception_type(ConnectionError),
            stop=stop_after_attempt(self.config.retry.max_attempts),
            wait=wait_exponential(
                multiplier=self.config.retry.multiplier,
                min=self.config.retry.min_wait,
                max=self.config.retry.max_wait
            ),
            before_sleep=log_retry_attempt,
            reraise=True
        )

    async def request(self, method: str, path: str, request_id: str = None, **kwargs) -> httpx.Response:
        """Make an HTTP request with full resilience protection."""
        await self._ensure_initialized()

        if request_id is None:
            request_id = str(uuid.uuid4())[:8]

        # Add request ID to headers for server-side correlation
        headers = kwargs.get("headers", {})
        headers["X-Request-ID"] = request_id
        kwargs["headers"] = headers

        # Bulkhead: Acquire semaphore
        await self._acquire_semaphore(request_id)

        try:
            # Circuit breaker + Retry logic
            async def make_request_with_retries():
                @self._create_retry_decorator(f"{method} {path}", request_id)
                async def _make_request():
                    start_time = time.time()

                    try:
                        if self.config.logging.include_request_id:
                            self.logger.debug(f"[{request_id}] {method} {path}")

                        response = await self._client.request(method, path, **kwargs)
                        response.raise_for_status()

                        elapsed = time.time() - start_time
                        if self.config.logging.include_request_id:
                            self.logger.debug(f"[{request_id}] {method} {path} completed in {elapsed:.3f}s")

                        return response

                    except Exception as e:
                        elapsed = time.time() - start_time
                        if self.config.logging.include_request_id:
                            self.logger.debug(f"[{request_id}] {method} {path} failed after {elapsed:.3f}s: {e}")

                        response_attr = getattr(e, 'response', None)
                        raise self._map_exception(e, response_attr) from e

                return await _make_request()

            return await self._circuit_breaker.call(make_request_with_retries)

        finally:
            # Always release semaphore
            self._semaphore.release()

    async def get(self, path: str, request_id: str = None, **kwargs) -> httpx.Response:
        """Make a GET request."""
        return await self.request("GET", path, request_id, **kwargs)

    async def post(self, path: str, request_id: str = None, **kwargs) -> httpx.Response:
        """Make a POST request."""
        return await self.request("POST", path, request_id, **kwargs)

    async def put(self, path: str, request_id: str = None, **kwargs) -> httpx.Response:
        """Make a PUT request."""
        return await self.request("PUT", path, request_id, **kwargs)

    async def delete(self, path: str, request_id: str = None, **kwargs) -> httpx.Response:
        """Make a DELETE request."""
        return await self.request("DELETE", path, request_id, **kwargs)

    async def close(self):
        """Clean up resources."""
        if self._client and not self._closed:
            await self._client.aclose()
            self._closed = True
            self.logger.info("ResilientHTTPClient closed")

@asynccontextmanager
async def create_resilient_client(config: ClientConfig):
    """Context manager for creating and managing a resilient client."""
    client = ResilientHTTPClient(config)
    try:
        yield client
    finally:
        await client.close()

# Example usage
async def main():
    """Example of using the production resilient client."""
    from config import get_fast_microservice_config

    config = get_fast_microservice_config("https://httpbin.org")

    async with create_resilient_client(config) as client:
        try:
            # Make some requests
            response = await client.get("/json")
            print(f"✅ Success: {response.status_code}")
            data = response.json()
            print(f"Data keys: {list(data.keys())}")

            # Test POST
            response = await client.post("/post", json={"test": "data"})
            print(f"✅ POST Success: {response.status_code}")

        except APIError as e:
            print(f"❌ API Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
```

This completes Part 2 of the tutorial. In the next sections, we'll cover testing strategies and production deployment considerations.
