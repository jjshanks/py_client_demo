# Building Your First Resilient Client: A Step-by-Step Tutorial

This tutorial guides junior engineers through building their first resilient HTTP client from scratch. You'll learn by doing - starting with a naive implementation and progressively adding resilience patterns.

## Prerequisites

- Basic Python async/await knowledge
- Understanding of HTTP requests
- Familiarity with exception handling
- Read [Resilience Patterns 101](RESILIENCE_PATTERNS_101.md) first

## What You'll Build

By the end of this tutorial, you'll have built a production-ready HTTP client that can:
- Handle network failures gracefully
- Retry transient errors automatically
- Protect against cascading failures
- Limit resource consumption
- Provide observability and debugging

## Tutorial Structure

1. [Step 1: The Naive Client (What NOT to do)](#step-1-the-naive-client)
2. [Step 2: Adding Basic Error Handling](#step-2-adding-basic-error-handling)
3. [Step 3: Implementing Timeouts](#step-3-implementing-timeouts)
4. [Step 4: Adding Retry Logic](#step-4-adding-retry-logic)
5. [Step 5: Implementing Circuit Breaker](#step-5-implementing-circuit-breaker)
6. [Step 6: Adding Bulkhead Pattern](#step-6-adding-bulkhead-pattern)
7. [Step 7: Creating Configuration System](#step-7-creating-configuration-system)
8. [Step 8: Testing Your Resilient Client](#step-8-testing-your-resilient-client)

---

## Step 1: The Naive Client (What NOT to do)

Let's start by building a simple HTTP client to understand the problems we need to solve.

### Create the Basic Structure

```python
# naive_client.py
import asyncio
import httpx

class NaiveClient:
    """A simple HTTP client - demonstrates what NOT to do in production."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url)

    async def get(self, path: str):
        """Make a GET request - no resilience patterns."""
        response = await self.client.get(path)
        response.raise_for_status()
        return response

    async def close(self):
        """Clean up the HTTP client."""
        await self.client.aclose()

# Test the naive client
async def test_naive_client():
    client = NaiveClient("https://httpbin.org")

    try:
        # This works when everything is perfect
        response = await client.get("/json")
        print(f"Success: {response.status_code}")
        print(f"Data: {response.json()}")
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        await client.close()

# Run the test
if __name__ == "__main__":
    asyncio.run(test_naive_client())
```

### Problems with the Naive Client

Run this code and try these scenarios to see the problems:

1. **No timeouts**: Try connecting to a slow/dead service
2. **No retries**: Network hiccups cause permanent failures
3. **No error distinction**: Can't tell client errors from server errors
4. **No rate limiting**: Can overwhelm services
5. **Poor observability**: No logging or metrics

Let's fix these problems step by step.

---

## Step 2: Adding Basic Error Handling

First, let's create a proper exception hierarchy to distinguish different types of errors.

### Create Exception Classes

```python
# exceptions.py
class APIError(Exception):
    """Base exception for all API errors."""

    def __init__(self, message: str, response=None, request=None):
        self.message = message
        self.response = response
        self.request = request
        super().__init__(message)

class ConnectionError(APIError):
    """Retryable connection/infrastructure errors."""
    pass

class TimeoutError(ConnectionError):
    """Request timed out."""
    pass

class ServerError(ConnectionError):
    """Server error (5xx status codes)."""
    pass

class ClientError(APIError):
    """Non-retryable client errors (4xx status codes)."""
    pass

class AuthenticationError(ClientError):
    """Authentication failed (401, 403)."""
    pass

class NotFoundError(ClientError):
    """Resource not found (404)."""
    pass
```

### Update Client with Error Mapping

```python
# improved_client.py
import asyncio
import httpx
import logging
from exceptions import *

class ImprovedClient:
    """HTTP client with proper error handling."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url)
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

    async def get(self, path: str):
        """Make a GET request with proper error handling."""
        try:
            response = await self.client.get(path)
            response.raise_for_status()
            return response
        except Exception as e:
            # Map to our exception hierarchy
            response_attr = getattr(e, 'response', None)
            raise self._map_exception(e, response_attr) from e

    async def close(self):
        await self.client.aclose()

# Test error handling
async def test_error_handling():
    client = ImprovedClient("https://httpbin.org")

    test_cases = [
        ("/json", "Should succeed"),
        ("/status/404", "Should raise NotFoundError"),
        ("/status/500", "Should raise ServerError"),
        ("/status/401", "Should raise AuthenticationError"),
    ]

    for path, description in test_cases:
        try:
            response = await client.get(path)
            print(f"✅ {description}: Success {response.status_code}")
        except NotFoundError as e:
            print(f"🔍 {description}: {type(e).__name__}")
        except ServerError as e:
            print(f"💥 {description}: {type(e).__name__}")
        except AuthenticationError as e:
            print(f"🔐 {description}: {type(e).__name__}")
        except Exception as e:
            print(f"❌ {description}: Unexpected {type(e).__name__}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(test_error_handling())
```

### What You Learned

- How to create semantic exception hierarchies
- Why different error types need different handling
- How to map low-level HTTP exceptions to business logic exceptions

---

## Step 3: Implementing Timeouts

Now let's add timeout protection to prevent hanging requests.

### Add Timeout Configuration

```python
# timeout_client.py
import asyncio
import httpx
import time
from dataclasses import dataclass
from exceptions import *

@dataclass
class TimeoutConfig:
    """Configuration for different types of timeouts."""
    connect: float = 10.0    # Connection establishment timeout
    read: float = 30.0       # Response reading timeout
    write: float = 15.0      # Request writing timeout
    pool: float = 5.0        # Connection pool timeout

class TimeoutClient:
    """HTTP client with comprehensive timeout protection."""

    def __init__(self, base_url: str, timeout_config: TimeoutConfig = None):
        self.base_url = base_url
        self.timeout_config = timeout_config or TimeoutConfig()

        # Create httpx timeout object
        httpx_timeout = httpx.Timeout(
            connect=self.timeout_config.connect,
            read=self.timeout_config.read,
            write=self.timeout_config.write,
            pool=self.timeout_config.pool
        )

        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx_timeout
        )
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

    async def get(self, path: str):
        """Make a GET request with timeout protection."""
        start_time = time.time()

        try:
            self.logger.debug(f"GET {path} (timeout: {self.timeout_config.read}s)")
            response = await self.client.get(path)
            response.raise_for_status()

            elapsed = time.time() - start_time
            self.logger.debug(f"GET {path} completed in {elapsed:.3f}s")
            return response

        except Exception as e:
            elapsed = time.time() - start_time
            self.logger.warning(f"GET {path} failed after {elapsed:.3f}s: {e}")

            response_attr = getattr(e, 'response', None)
            raise self._map_exception(e, response_attr) from e

    async def close(self):
        await self.client.aclose()

# Test timeout behavior
async def test_timeouts():
    # Configure aggressive timeouts for testing
    fast_timeout = TimeoutConfig(
        connect=2.0,
        read=3.0,
        write=2.0,
        pool=1.0
    )

    client = TimeoutClient("https://httpbin.org", fast_timeout)

    test_cases = [
        ("/json", "Fast response - should succeed"),
        ("/delay/5", "Slow response - should timeout"),
    ]

    for path, description in test_cases:
        try:
            start = time.time()
            response = await client.get(path)
            elapsed = time.time() - start
            print(f"✅ {description}: Success in {elapsed:.2f}s")
        except TimeoutError as e:
            elapsed = time.time() - start
            print(f"⏰ {description}: Timeout after {elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - start
            print(f"❌ {description}: {type(e).__name__} after {elapsed:.2f}s")

    await client.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(test_timeouts())
```

### What You Learned

- How to configure different types of timeouts
- Why timeouts are essential for preventing hanging requests
- How to add observability with timing logs
- The difference between connect, read, write, and pool timeouts

---

## Step 4: Adding Retry Logic

Now let's add automatic retry capability with exponential backoff.

### Install Required Dependencies

```bash
pip install tenacity  # For retry logic
```

### Implement Retry Pattern

```python
# retry_client.py
import asyncio
import httpx
import time
import random
import logging
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from exceptions import *

@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
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

class RetryClient:
    """HTTP client with automatic retry logic."""

    def __init__(self, base_url: str, timeout_config: TimeoutConfig = None, retry_config: RetryConfig = None):
        self.base_url = base_url
        self.timeout_config = timeout_config or TimeoutConfig()
        self.retry_config = retry_config or RetryConfig()

        httpx_timeout = httpx.Timeout(
            connect=self.timeout_config.connect,
            read=self.timeout_config.read,
            write=self.timeout_config.write,
            pool=self.timeout_config.pool
        )

        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx_timeout
        )
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
            """Log retry attempts for observability."""
            if retry_state.outcome and retry_state.outcome.failed:
                exception = retry_state.outcome.exception()
                attempt = retry_state.attempt_number
                wait_time = retry_state.next_action.sleep if retry_state.next_action else 0

                self.logger.warning(
                    f"{operation_name} attempt {attempt} failed: {type(exception).__name__}: {exception}. "
                    f"Retrying in {wait_time:.2f}s"
                )

        # Configure wait strategy
        if self.retry_config.jitter:
            wait_strategy = wait_exponential(
                multiplier=self.retry_config.multiplier,
                min=self.retry_config.min_wait,
                max=self.retry_config.max_wait
            )
        else:
            wait_strategy = wait_exponential(
                multiplier=self.retry_config.multiplier,
                min=self.retry_config.min_wait,
                max=self.retry_config.max_wait
            )

        return retry(
            # Only retry connection errors (not client errors like 404)
            retry=retry_if_exception_type(ConnectionError),
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait_strategy,
            before_sleep=log_retry_attempt,
            reraise=True
        )

    async def get(self, path: str):
        """Make a GET request with retry logic."""

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

    async def close(self):
        await self.client.aclose()

# Test retry behavior
async def test_retries():
    # Configure for quick testing
    retry_config = RetryConfig(
        max_attempts=3,
        min_wait=0.5,
        max_wait=2.0,
        jitter=True
    )

    timeout_config = TimeoutConfig(read=2.0)  # Short timeout to trigger retries

    client = RetryClient("https://httpbin.org", timeout_config, retry_config)

    test_cases = [
        ("/json", "Should succeed on first try"),
        ("/status/500", "Should retry and eventually fail"),
        ("/delay/1", "Should succeed after timeout and retry"),
        ("/status/404", "Should fail immediately (no retry for 404)"),
    ]

    for path, description in test_cases:
        print(f"\n🧪 Testing: {description}")
        try:
            start = time.time()
            response = await client.get(path)
            elapsed = time.time() - start
            print(f"✅ Success in {elapsed:.2f}s")
        except ConnectionError as e:
            elapsed = time.time() - start
            print(f"🔄 Connection error after {elapsed:.2f}s (retries exhausted)")
        except ClientError as e:
            elapsed = time.time() - start
            print(f"🚫 Client error after {elapsed:.2f}s (no retries)")
        except Exception as e:
            elapsed = time.time() - start
            print(f"❌ Unexpected error after {elapsed:.2f}s: {type(e).__name__}")

    await client.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    asyncio.run(test_retries())
```

### What You Learned

- How to implement exponential backoff with jitter
- Why only certain types of errors should trigger retries
- The importance of logging retry attempts for observability
- How to use the tenacity library for robust retry logic

This tutorial continues in the next part where we'll add circuit breaker and bulkhead patterns.
