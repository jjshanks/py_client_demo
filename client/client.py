"""
Resilient HTTP client implementation.

This module contains the main ResilientClient class that implements all
resilience patterns including connection pooling, retries, circuit breaker,
bulkhead, and comprehensive error handling.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from tenacity import RetryError

from .circuit_breaker import CircuitBreaker
from .config import ClientConfig
from .exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    InvalidRequestError,
    MyAPIError,
    NotFoundError,
    PoolTimeoutError,
    ServerError,
    ServerTimeoutError,
    ServiceUnavailableError,
)
from .retry_policies import RetryPolicy


class ResilientClient:
    """
    A resilient asynchronous HTTP client implementing modern resilience patterns.

    This client combines multiple resilience patterns to create a production-ready
    HTTP client that can handle various failure scenarios gracefully:

    **Resilience Patterns Implemented:**

    1. **Connection Pooling**: Uses a long-lived httpx.AsyncClient to maintain
       persistent connections, reducing overhead and improving performance.

    2. **Circuit Breaker**: Monitors connection failures and switches to "fail-fast"
       mode when a service becomes unhealthy, preventing cascading failures.
       - CLOSED: Normal operation
       - OPEN: Failing fast after threshold failures
       - HALF_OPEN: Testing if service has recovered

    3. **Retry with Exponential Backoff**: Automatically retries failed requests
       with increasing delays and optional jitter to prevent thundering herd effects.

    4. **Bulkhead Pattern**: Limits concurrent requests using semaphores to prevent
       resource exhaustion and protect upstream services.

    5. **Idempotency**: Automatically generates X-Request-ID headers to enable
       safe retries and duplicate request detection.

    **Usage Examples:**

    Basic usage with default configuration:
    ```python
    from client import ClientConfig, ResilientClient

    config = ClientConfig(base_url="https://api.example.com")

    async with ResilientClient(config) as client:
        response = await client.get("/users/123")
        print(response.status_code)
    ```

    Advanced configuration for high-latency environments:
    ```python
    from client import ClientConfig, TimeoutConfig, RetryConfig

    config = ClientConfig(
        base_url="https://api.example.com",
        timeout=TimeoutConfig(
            connect=10.0,    # Allow longer connection time
            read=60.0        # Allow longer response time
        ),
        retry=RetryConfig(
            max_attempts=5,  # More retries for unreliable networks
            max_wait_seconds=120.0  # Longer backoff for slow services
        )
    )
    ```

    **Pattern Interaction Flow:**

    1. Request initiated → Bulkhead pattern acquires semaphore slot
    2. Circuit breaker checks if service is healthy (CLOSED state)
    3. Retry wrapper executes the HTTP request with exponential backoff
    4. Each retry failure is reported to circuit breaker
    5. Success resets circuit breaker state and releases semaphore

    **Exception Hierarchy:**

    - APIConnectionError: Retryable errors (network, timeouts, 5xx)
    - APIStatusError: Non-retryable errors (4xx client errors)
    - PoolTimeoutError: Bulkhead pattern timeout (too many concurrent requests)

    **Best Practices:**

    - Use as an async context manager to ensure proper cleanup
    - Configure timeouts appropriate for your service's SLA
    - Set circuit breaker thresholds based on acceptable failure rates
    - Monitor logs for pattern behavior insights
    - Use request_id parameter for request tracing and debugging

    **Threading and Async Safety:**

    This client is designed for asyncio environments and maintains thread-safe
    state using asyncio.Lock for circuit breaker operations and asyncio.Semaphore
    for concurrency limiting.

    Attributes:
        config: Client configuration containing all resilience pattern settings
        logger: Structured logger for observability and debugging
    """

    def __init__(self, config: ClientConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._retry_policy: Optional[RetryPolicy] = None
        self._closed = False

        # Setup logging
        self.logger = logging.getLogger(config.logging.logger_name)
        self.logger.setLevel(getattr(logging, config.logging.level.upper()))

    async def __aenter__(self) -> "ResilientClient":
        """Async context manager entry."""
        await self._ensure_initialized()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_initialized(self) -> None:
        """Initialize the client if not already initialized."""
        # LAZY INITIALIZATION PATTERN: Only initialize when first needed
        # This allows the client to be constructed with configuration but delays
        # expensive resource allocation until actual use. This is important for
        # applications that might construct clients but not always use them.
        if self._client is None:
            # CONNECTION POOLING: Create long-lived httpx client
            # Using a single AsyncClient instance enables connection pooling,
            # which dramatically improves performance by reusing TCP connections.
            # Without pooling, each request would require TCP handshake overhead.
            #
            # EDUCATIONAL: Connection pooling is one of the most important
            # optimizations for HTTP clients. It reduces latency and server load.
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,  # Base URL for relative requests
                timeout=self.config.timeout.to_httpx_timeout(),  # Multi-level timeouts
                follow_redirects=self.config.follow_redirects,  # HTTP redirect handling
                verify=self.config.verify_ssl,  # SSL certificate verification
                headers=(
                    {"User-Agent": self.config.user_agent}
                    if self.config.user_agent
                    else None
                ),  # Default headers for all requests
            )

            # RESILIENCE COMPONENT INITIALIZATION: Set up all patterns
            # Each pattern is initialized with its specific configuration.
            # The order doesn't matter here since they're just being created,
            # not yet orchestrated together.

            # BULKHEAD: Semaphore for concurrency limiting
            # This semaphore acts as a "connection pool" limiting how many
            # concurrent requests can be in flight. This protects both the
            # client and the target service from overload scenarios.
            self._semaphore = asyncio.Semaphore(self.config.bulkhead.max_concurrency)

            # CIRCUIT BREAKER: Failure detection and fast-fail protection
            # The circuit breaker monitors for connection failures and switches
            # to fail-fast mode when a service becomes unhealthy.
            self._circuit_breaker = CircuitBreaker(self.config.circuit_breaker)

            # RETRY POLICY: Exponential backoff retry with jitter
            # The retry policy handles transient failures by automatically
            # retrying requests with increasing delays between attempts.
            self._retry_policy = RetryPolicy(self.config.retry)

            # OBSERVABILITY: Log successful initialization
            # This log message confirms the client is ready and shows key config
            self.logger.info(
                f"ResilientClient initialized with base_url={self.config.base_url}"
            )

    async def close(self) -> None:
        """Close the HTTP client and clean up resources."""
        if self._client and not self._closed:
            await self._client.aclose()
            self._closed = True
            self.logger.info("ResilientClient closed")

    def _map_httpx_exception(
        self, exc: Exception, response: Optional[httpx.Response] = None
    ) -> MyAPIError:
        """
        Map httpx exceptions to our custom exception hierarchy.

        Args:
            exc: The original httpx exception
            response: Optional HTTP response object

        Returns:
            Mapped exception from our hierarchy
        """
        if isinstance(exc, httpx.TimeoutException):
            return APITimeoutError("Request timed out", response=response)

        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            return APIConnectionError(f"Network error: {exc}", response=response)

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code

            # Map status codes to specific exceptions according to CLIENT_GUIDE.md
            if status_code in (401, 403):
                return AuthenticationError(
                    f"Authentication failed: {status_code}", response=exc.response
                )
            elif status_code == 404:
                return NotFoundError("Resource not found", response=exc.response)
            elif status_code in (400, 422):
                return InvalidRequestError(
                    f"Invalid request: {status_code}", response=exc.response
                )
            elif status_code == 408:
                return ServerTimeoutError("Server timeout", response=exc.response)
            elif status_code in (502, 503, 504):
                return ServiceUnavailableError(
                    f"Service unavailable: {status_code}", response=exc.response
                )
            elif 500 <= status_code < 600:
                return ServerError(
                    f"Server error: {status_code}", response=exc.response
                )
            else:
                # Other status codes default to generic status error
                return APIStatusError(
                    f"HTTP error: {status_code}", response=exc.response
                )

        # Fallback for other httpx errors
        return MyAPIError(f"HTTP client error: {exc}", response=response)

    async def _acquire_semaphore_with_timeout(self, request_id: str) -> None:
        """
        Acquire semaphore slot with timeout (bulkhead pattern).

        Args:
            request_id: Request ID for logging correlation

        Raises:
            PoolTimeoutError: If semaphore cannot be acquired within timeout
        """
        # BULKHEAD PATTERN: Log when operation is waiting for resources
        # This helps identify when the client is becoming saturated and
        # operations are queuing up waiting for available concurrency slots
        self.logger.debug(
            f"Operation [{request_id}] is waiting for a free connection slot"
        )

        try:
            # SAFETY CHECK: Ensure semaphore was initialized during client setup
            if self._semaphore is None:
                raise RuntimeError("Client not initialized")

            # RESOURCE ACQUISITION: Wait for semaphore slot with timeout protection
            # The timeout prevents requests from waiting indefinitely when the client
            # is saturated. This is a "bulkhead" - we limit concurrent operations
            # to protect both the client and the target service from overload.
            #
            # EDUCATIONAL: Without this timeout, a slow service could cause
            # requests to queue up indefinitely, leading to memory issues and
            # poor user experience. The timeout provides fast failure.
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.config.bulkhead.acquisition_timeout,
            )

            # OBSERVABILITY: Log successful acquisition with resource visibility
            # The remaining slots count helps operators understand current load
            # and can be used for metrics/alerts about client saturation
            remaining_slots = self._semaphore._value
            self.logger.debug(
                f"Operation [{request_id}] acquired a connection slot. "
                f"{remaining_slots} slots remaining"
            )

        except asyncio.TimeoutError as e:
            # BULKHEAD TIMEOUT: Convert asyncio timeout to semantic exception
            # PoolTimeoutError clearly indicates this is a client-side resource
            # limitation, not a remote service issue. This helps with debugging
            # and allows different error handling strategies.
            #
            # EDUCATIONAL: This type of error suggests the client is overloaded.
            # Common solutions: reduce request rate, increase concurrency limit,
            # or scale to multiple client instances.
            raise PoolTimeoutError(
                f"Failed to acquire connection slot within "
                f"{self.config.bulkhead.acquisition_timeout}s - client is saturated"
            ) from e

    async def _make_request_with_resilience(
        self, method: str, url: str, request_id: str, **kwargs: Any
    ) -> httpx.Response:
        """
        Make an HTTP request with full resilience pattern implementation.

        This method orchestrates all resilience patterns in the correct order to
        create a robust HTTP request pipeline. Understanding this flow is crucial
        for junior engineers to see how patterns work together.

        **Resilience Pattern Execution Order:**

        1. **Idempotency Setup**: Add X-Request-ID header for duplicate detection
        2. **Bulkhead Pattern**: Acquire semaphore slot (with timeout protection)
        3. **Circuit Breaker Check**: Verify if service is healthy enough to try
        4. **Retry Loop**: Execute request with exponential backoff retry logic
        5. **Exception Mapping**: Convert httpx exceptions to semantic API exceptions
        6. **State Updates**: Update circuit breaker based on success/failure
        7. **Resource Cleanup**: Always release semaphore slot

        **Why This Order Matters:**

        - Bulkhead comes first to prevent resource exhaustion
        - Circuit breaker prevents wasted retries when service is known to be down
        - Retry loop is inside circuit breaker so the entire retry sequence counts
          as one operation for circuit breaker state management
        - Exception mapping provides consistent error contracts to calling code
        - Cleanup happens in finally block to ensure resources are always released

        **Educational Example - What Happens During Failures:**

        ```
        Request 1: Network timeout
        → Bulkhead: Acquired slot ✓
        → Circuit: CLOSED state, proceed ✓
        → Retry: Attempt 1 fails, wait 1s, retry
        → Retry: Attempt 2 fails, wait 2s, retry
        → Retry: Attempt 3 fails, raise exception
        → Circuit: Increment failure count (1/5)
        → Cleanup: Release semaphore slot

        Request 6: After 5 failures...
        → Circuit: Failure threshold reached, circuit OPENS
        → Future requests: Fail fast without hitting network
        ```

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: Request URL (relative to base_url or absolute)
            request_id: Unique request ID for idempotency and correlation tracking
            **kwargs: Additional arguments passed to httpx (headers, json, etc.)

        Returns:
            HTTP response object on success

        Raises:
            APIConnectionError: Retryable errors (network, timeout, 5xx server errors)
            APIStatusError: Non-retryable errors (4xx client errors)
            PoolTimeoutError: Bulkhead pattern timeout (too many concurrent requests)
            RetryError: All retry attempts exhausted

        **Performance Considerations:**

        - Connection pooling reduces TCP handshake overhead
        - Semaphore prevents overwhelming target service
        - Circuit breaker reduces latency during outages
        - Jitter in retry timing prevents thundering herd effects
        """
        # INITIALIZATION: Ensure all components are ready
        # This lazy initialization pattern allows configuration after construction
        # but ensures everything is properly set up before first use
        await self._ensure_initialized()

        # IDEMPOTENCY: Add unique request ID for duplicate detection
        # The X-Request-ID header allows the server to detect and handle duplicate
        # requests safely. This is crucial for retry safety - if a retry succeeds
        # but the response is lost, the duplicate won't cause side effects.
        headers = kwargs.get("headers", {})
        headers["X-Request-ID"] = request_id
        kwargs["headers"] = headers

        # BULKHEAD PATTERN: Acquire semaphore slot for concurrency limiting
        # This prevents the client from overwhelming the target service or itself.
        # The semaphore acts as a "connection pool" limiting concurrent operations.
        await self._acquire_semaphore_with_timeout(request_id)

        try:
            # PATTERN ORCHESTRATION: Nest patterns in the correct order
            # The nesting order is critical for proper resilience behavior:
            # 1. Bulkhead (outermost) - protects client resources
            # 2. Circuit breaker - prevents retries when service is known bad
            # 3. Retry loop - handles transient failures
            # 4. HTTP request - the actual network operation

            # Define the HTTP operation with retry logic inside circuit breaker
            async def http_operation_with_retries() -> httpx.Response:
                # RETRY WRAPPER: This function will be called by the retry policy
                # Multiple times with exponential backoff between attempts

                # Define the actual HTTP operation
                async def single_http_request() -> httpx.Response:
                    try:
                        # NETWORK OPERATION: Make the actual HTTP request
                        # This is where the network I/O happens. All the patterns above
                        # are protecting and orchestrating this core operation.
                        if self._client is None:
                            raise RuntimeError("Client not initialized")
                        response = await self._client.request(method, url, **kwargs)

                        # HTTP STATUS CHECK: Raise exception for 4xx/5xx status codes
                        # This converts HTTP error statuses into Python exceptions
                        # so retry and circuit breaker logic can handle them uniformly
                        response.raise_for_status()

                        return response

                    except Exception as e:
                        # EXCEPTION MAPPING: Convert httpx exceptions to semantic types
                        # This is crucial for the retry and circuit breaker logic.
                        # Different exception types trigger different behaviors:
                        # - APIConnectionError → retryable, affects circuit breaker
                        # - APIStatusError → not retryable, doesn't affect circuit
                        response_attr = getattr(e, "response", None)
                        raise self._map_httpx_exception(
                            e,
                            response_attr
                            if isinstance(response_attr, httpx.Response)
                            else None,
                        ) from e

                # RETRY POLICY APPLICATION: Wrap the HTTP operation with retry logic
                # The retry policy uses tenacity to implement exponential backoff
                # with jitter. It will only retry APIConnectionError exceptions.
                if self._retry_policy is None:
                    raise RuntimeError("Client not initialized")
                retry_wrapped = self._retry_policy.wrap_operation(
                    single_http_request, request_id
                )

                # RETRY EXECUTION: Execute with automatic retries
                # This returns the result of the first successful attempt,
                # or raises RetryError if all attempts fail
                return await retry_wrapped()

            # CIRCUIT BREAKER APPLICATION: Protect the entire retry sequence
            # This ensures the entire retry sequence counts as one operation
            # for circuit breaker state management. If the circuit is OPEN,
            # we skip all retries and fail immediately.
            if self._circuit_breaker is None:
                raise RuntimeError("Client not initialized")
            return await self._circuit_breaker.call(http_operation_with_retries)

        except RetryError as e:
            # RETRY EXHAUSTION: Handle case where all retry attempts failed
            # Extract the actual exception from the last retry attempt.
            # RetryError is a wrapper - we want to surface the underlying cause.
            last_exc = e.last_attempt.exception()
            if last_exc is not None:
                raise last_exc from e
            raise MyAPIError("Retry failed with no exception") from e

        finally:
            # RESOURCE CLEANUP: Always release the semaphore slot
            # This happens regardless of success or failure to prevent resource leaks.
            # The semaphore slot must be released so other requests can proceed.
            # Using finally ensures this cleanup happens even if exceptions occur.
            if self._semaphore is not None:
                self._semaphore.release()

    async def get(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a GET request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for GET operation"
                )

        return await self._make_request_with_resilience(
            "GET", url, request_id, **kwargs
        )

    async def post(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a POST request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for POST operation"
                )

        return await self._make_request_with_resilience(
            "POST", url, request_id, **kwargs
        )

    async def put(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a PUT request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for PUT operation"
                )

        return await self._make_request_with_resilience(
            "PUT", url, request_id, **kwargs
        )

    async def delete(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a DELETE request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for DELETE operation"
                )

        return await self._make_request_with_resilience(
            "DELETE", url, request_id, **kwargs
        )


@asynccontextmanager
async def create_resilient_client(
    config: ClientConfig,
) -> AsyncGenerator[ResilientClient, None]:
    """
    Async context manager for creating and managing a resilient client.

    Args:
        config: Client configuration

    Yields:
        Configured ResilientClient instance
    """
    client = ResilientClient(config)
    try:
        await client._ensure_initialized()
        yield client
    finally:
        await client.close()
